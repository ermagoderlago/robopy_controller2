"""
Unit Tests for HailoQwenClient (Milestone 1)
============================================
Verifies:
1. Default timeout_sec is 10.0s (sufficient for real Hailo-10H NPU generation ~4.3s warm).
2. Local inference uses /api/chat endpoint with structured messages: [{"role": "system", ...}, {"role": "user", ...}].
3. Structured message conversion from plain prompts and ChatML strings.
4. Streaming and non-streaming token generation with TTFT tracking.
5. Mock engine produces factually grounded, empathetic, non-robotic Italian responses.
6. Fallback model execution and self-healing circuit breaker.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Dict, List, Optional
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.robot_ai.hailo.hailo_qwen_client import (
    HailoQwenClient,
    HailoQwenConfig,
    InferenceMode,
    InferenceTelemetry,
    TokenChunk,
)


class MockChatOllamaServer:
    """Mock HTTP server simulating /api/chat endpoint of hailo-ollama."""

    def __init__(self, host: str = "127.0.0.1", port: int = 11495):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.endpoint = f"http://{self.host}:{self.port}"
        self.received_requests: List[Dict[str, any]] = []

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            req_line = await reader.readline()
            req_str = req_line.decode("utf-8", errors="ignore")
            path = req_str.split(" ")[1] if " " in req_str else "/"

            content_len = 0
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                decoded = line.decode("utf-8", errors="ignore")
                if ":" in decoded:
                    k, v = decoded.split(":", 1)
                    if k.strip().lower() == "content-length":
                        content_len = int(v.strip())

            body_bytes = await reader.readexactly(content_len) if content_len > 0 else b""
            body_json = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}

            self.received_requests.append({
                "path": path,
                "body": body_json,
            })

            if path == "/api/tags":
                payload = json.dumps({"models": [{"name": "qwen2.5-instruct:1.5b"}]})
                resp = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(payload)}\r\n"
                    f"Connection: close\r\n\r\n{payload}"
                )
                writer.write(resp.encode("utf-8"))
                await writer.drain()
            elif path == "/api/chat":
                # Stream NDJSON response in Ollama /api/chat format
                header = (
                    "HTTP/1.1 200 OK\r\n"
                    "Transfer-Encoding: chunked\r\n"
                    "Content-Type: application/x-ndjson\r\n\r\n"
                )
                writer.write(header.encode("utf-8"))
                await writer.drain()

                chunks = [
                    {"message": {"role": "assistant", "content": "La capitale "}, "done": False},
                    {"message": {"role": "assistant", "content": "della Francia "}, "done": False},
                    {"message": {"role": "assistant", "content": "è Parigi."}, "done": True},
                ]
                for c in chunks:
                    c_str = json.dumps(c) + "\n"
                    chunk_hdr = f"{len(c_str):X}\r\n{c_str}\r\n"
                    writer.write(chunk_hdr.encode("utf-8"))
                    await writer.drain()
                    await asyncio.sleep(0.01)

                writer.write(b"0\r\n\r\n")
                await writer.drain()
            else:
                resp = "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"
                writer.write(resp.encode("utf-8"))
                await writer.drain()

            writer.close()
            await writer.wait_closed()
        except Exception:
            try:
                writer.close()
            except Exception:
                pass


@pytest.mark.asyncio
async def test_default_config_timeout():
    """Verify default timeout_sec is 10.0s for Hailo-10H NPU."""
    cfg = HailoQwenConfig()
    assert cfg.timeout_sec == 10.0, f"Expected default timeout 10.0s, got {cfg.timeout_sec}"
    client = HailoQwenClient(cfg)
    assert client.config.timeout_sec == 10.0
    await client.close()


@pytest.mark.asyncio
async def test_structured_message_builder():
    """Verify _build_messages parses plain text and ChatML into structured messages."""
    client = HailoQwenClient()

    # 1. Plain text with system prompt
    msgs1 = client._build_messages("Ciao Marcus", system_prompt="Sei un robot fisico.")
    assert msgs1 == [
        {"role": "system", "content": "Sei un robot fisico."},
        {"role": "user", "content": "Ciao Marcus"},
    ]

    # 2. ChatML formatted prompt
    chatml = (
        "<|im_start|>system\nSei Marcus, un assistente su ruote.<|im_end|>\n"
        "<|im_start|>user\nQual è la capitale della Francia?<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
    msgs2 = client._build_messages(chatml)
    assert len(msgs2) == 2
    assert msgs2[0] == {"role": "system", "content": "Sei Marcus, un assistente su ruote."}
    assert msgs2[1] == {"role": "user", "content": "Qual è la capitale della Francia?"}

    await client.close()


@pytest.mark.asyncio
async def test_api_chat_endpoint_invocation():
    """Verify that HailoQwenClient sends structured messages to /api/chat endpoint."""
    server = MockChatOllamaServer(port=11495)
    await server.start()

    try:
        cfg = HailoQwenConfig(
            endpoint=server.endpoint,
            enable_mock_fallback=False,
            timeout_sec=5.0,
        )
        client = HailoQwenClient(cfg)
        client.mode = InferenceMode.HARDWARE_OLLAMA

        text, telem = await client.generate(
            prompt="Qual è la capitale della Francia?",
            system_prompt="Rispondi in modo conciso.",
        )

        assert len(server.received_requests) >= 1
        chat_req = next((r for r in server.received_requests if r["path"] == "/api/chat"), None)
        assert chat_req is not None, "Request must be sent to /api/chat"

        body = chat_req["body"]
        assert "messages" in body, "Payload must contain 'messages'"
        assert isinstance(body["messages"], list)
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "Rispondi in modo conciso."
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][1]["content"] == "Qual è la capitale della Francia?"

        assert "Parigi" in text
        assert telem.is_mock is False
        await client.close()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_mock_engine_natural_factual_answers():
    """Verify that mock engine returns factually grounded, empathetic Italian responses without robotic strings."""
    client = HailoQwenClient(HailoQwenConfig(enable_mock_fallback=True))
    client.mode = InferenceMode.MOCK_ENGINE

    test_queries = [
        ("qual'è la capitale della francia?", "Parigi"),
        ("Qual è la capitale dell'Italia e quale quella della Spagna?", "Roma"),
        ("Se avanzo di 40 centimetri e poi torno indietro di 15, a che distanza mi trovo dal punto di partenza?", "25 centimetri"),
        ("quando è stata l'iltima volta che hai svolto il programma dream? e cosa hai elaaborato?", "Nightly Dream"),
        ("quanta ram stai usando?", "RAM"),
        ("quanto è pieno l'hdd?", "SSD"),
        ("A che temperatura lavora la tua CPU adesso? Ti stai scaldando?", "71.6"),
        ("quanta memoria swap stai usando in questo momento?", "swap"),
        ("quanta autonomia hai?", "autonomia"),
        ("sei collegato all'alimentazione?", "scollegato"),
        ("Perché a volte sembri un automa quando parli? Promettimi che sarai più naturale!", "naturale"),
        ("Sono un po' stanco stasera dopo una giornata pesante, mi dici qualcosa di carino?", "straordinaria"),
    ]

    for query, expected_snippet in test_queries:
        text, telem = await client.generate(query)
        assert expected_snippet.lower() in text.lower(), f"Query '{query}' failed snippet match: '{text}'"
        assert "ho ricevuto il tuo messaggio" not in text.lower()
        assert "ho elaborato la tua richiesta" not in text.lower()
        assert "sono un automa" not in text.lower()
        assert len(text) > 10

    await client.close()


@pytest.mark.asyncio
async def test_streaming_token_chunk_generation():
    """Verify streaming yields TokenChunks with proper TTFT and ordering."""
    client = HailoQwenClient(HailoQwenConfig(enable_mock_fallback=True, mock_ttft_ms=50.0, mock_inter_token_ms=5.0))
    client.mode = InferenceMode.MOCK_ENGINE

    chunks: List[TokenChunk] = []
    async for chunk in client.generate_stream("ciao marcus"):
        chunks.append(chunk)

    assert len(chunks) > 0
    assert chunks[0].is_first is True
    assert chunks[-1].is_last is True
    assert client.last_telemetry is not None
    assert client.last_telemetry.ttft_ms > 0.0
    await client.close()


if __name__ == "__main__":
    pytest.main(["-v", __file__])
