"""
Adversarial Empirical Stress Suite for HailoQwenClient (Milestone 1 Iteration 2 Gate)
====================================================================================
Challenger: challenger_m1_1_iter2
Target: robopy_controller/robot_ai/hailo/hailo_qwen_client.py

Verifies:
1. Telemetry isolation on empty / whitespace prompts under sequential & high-concurrency access.
2. Fallback latency: stream-level TTFT abort and absence of latency doubling.
3. Transient hardware errors: self-healing circuit breaker, no permanent mock latching.
4. Mid-stream hardware errors: clean exception raise without duplicate mock tokens.
5. High concurrency burst (50 requests mock & 50 requests HTTP).
6. Malformed and extreme payload robustness.
7. Sustained memory stability (100 requests, RSS drift < 5.0 MB).
"""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import psutil
import pytest

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from robopy_controller.robot_ai.hailo.hailo_qwen_client import (
    HailoQwenClient,
    HailoQwenConfig,
    InferenceMode,
    InferenceTelemetry,
    TokenChunk,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("challenger.m1.iter2")


# ============================================================================
# HELPER: Configurable Asynchronous Mock Ollama HTTP Server
# ============================================================================

class ConfigurableMockOllamaServer:
    """Mock server simulating Ollama REST API with controllable fault injection."""

    def __init__(self, host: str = "127.0.0.1", port: int = 11488):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.endpoint = f"http://{self.host}:{self.port}"
        self.behavior: str = "normal"
        self.request_count: int = 0
        self.fail_countdown: int = 0
        self.disconnect_countdown: int = 0
        self.midstream_token_count: int = 2

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
        logger.info("ConfigurableMockOllamaServer started at %s", self.endpoint)

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
            logger.info("ConfigurableMockOllamaServer stopped")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.request_count += 1
        try:
            request_line = await reader.readline()
            content_length = 0
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                decoded = line.decode("utf-8", errors="ignore")
                if ":" in decoded:
                    k, v = decoded.split(":", 1)
                    if k.strip().lower() == "content-length":
                        content_length = int(v.strip())

            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)

            path = request_line.decode("utf-8", errors="ignore").split(" ")[1] if b" " in request_line else "/"

            if path == "/api/tags":
                body_str = json.dumps({"models": [{"name": "qwen2.5-instruct:1.5b"}]})
                resp = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body_str)}\r\n"
                    f"Connection: close\r\n\r\n"
                    f"{body_str}"
                )
                writer.write(resp.encode("utf-8"))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            # Check transient failure countdowns
            current_behavior = self.behavior
            if self.fail_countdown > 0:
                self.fail_countdown -= 1
                current_behavior = "http_500"
            elif self.disconnect_countdown > 0:
                self.disconnect_countdown -= 1
                current_behavior = "disconnect_midstream"

            if current_behavior == "tarpit":
                await asyncio.sleep(10.0)
                writer.close()
                await writer.wait_closed()
                return

            elif current_behavior == "http_500":
                body_err = json.dumps({"error": "Simulated transient NPU failure"})
                resp = (
                    f"HTTP/1.1 500 Internal Server Error\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body_err)}\r\n"
                    f"Connection: close\r\n\r\n"
                    f"{body_err}"
                )
                writer.write(resp.encode("utf-8"))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            elif current_behavior == "disconnect_midstream":
                header = (
                    "HTTP/1.1 200 OK\r\n"
                    "Transfer-Encoding: chunked\r\n"
                    "Content-Type: application/x-ndjson\r\n\r\n"
                )
                writer.write(header.encode("utf-8"))
                await writer.drain()

                # Emit requested number of tokens before abruptly breaking
                for i in range(self.midstream_token_count):
                    tok_data = json.dumps({"response": f"token_{i}", "done": False}) + "\n"
                    chunk = f"{len(tok_data):X}\r\n{tok_data}\r\n"
                    writer.write(chunk.encode("utf-8"))
                    await writer.drain()
                    await asyncio.sleep(0.02)

                # Abrupt socket termination mid-stream
                writer.close()
                await writer.wait_closed()
                return

            elif current_behavior == "prefill_delay":
                # Delay first token by 350ms to simulate slow prefill
                header = (
                    "HTTP/1.1 200 OK\r\n"
                    "Transfer-Encoding: chunked\r\n"
                    "Content-Type: application/x-ndjson\r\n\r\n"
                )
                writer.write(header.encode("utf-8"))
                await writer.drain()
                await asyncio.sleep(0.35)

                tok_data = json.dumps({"response": "ok_token", "done": True}) + "\n"
                chunk = f"{len(tok_data):X}\r\n{tok_data}\r\n0\r\n\r\n"
                writer.write(chunk.encode("utf-8"))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            else:
                # Normal NDJSON streaming
                header = (
                    "HTTP/1.1 200 OK\r\n"
                    "Transfer-Encoding: chunked\r\n"
                    "Content-Type: application/x-ndjson\r\n\r\n"
                )
                writer.write(header.encode("utf-8"))
                await writer.drain()

                tokens = ["{\"action\":", " \"move_distance\",", " \"parameters\":", " {\"distance_m\": 0.5}}"]
                for i, tok in enumerate(tokens):
                    is_done = (i == len(tokens) - 1)
                    line = json.dumps({"response": tok, "done": is_done}) + "\n"
                    chunk = f"{len(line):X}\r\n{line}\r\n"
                    writer.write(chunk.encode("utf-8"))
                    await writer.drain()
                    await asyncio.sleep(0.01)

                writer.write(b"0\r\n\r\n")
                await writer.drain()
                writer.close()
                await writer.wait_closed()

        except Exception as exc:
            logger.debug("Mock server handler exception: %s", exc)
            try:
                writer.close()
            except Exception:
                pass


# ============================================================================
# FOCUS AREA 1: EMPTY & WHITESPACE PROMPTS TELEMETRY ISOLATION
# ============================================================================

@pytest.mark.asyncio
async def test_empty_prompt_telemetry_isolation_sequential():
    """
    Stress 1.1: Verify empty / whitespace prompts do not leak telemetry from prior calls.
    Tests sequential transitions: valid -> empty -> whitespace -> valid -> empty.
    """
    client = HailoQwenClient(HailoQwenConfig(enable_mock_fallback=True, mock_ttft_ms=10.0, mock_inter_token_ms=2.0))
    client.mode = InferenceMode.MOCK_ENGINE

    # Step 1: Prime with a valid command
    t1_text, t1_telem = await client.generate("avanti veloce")
    assert len(t1_text) > 0
    assert t1_telem.prompt == "avanti veloce"
    assert t1_telem.token_count > 0
    assert client.get_last_telemetry().prompt == "avanti veloce"

    # Step 2: Query empty prompt
    t2_text, t2_telem = await client.generate("")
    assert t2_text == ""
    assert t2_telem.prompt == "", f"Telemetry prompt leaked: expected '', got {t2_telem.prompt!r}"
    assert t2_telem.response == "", f"Telemetry response leaked: got {t2_telem.response!r}"
    assert t2_telem.token_count == 0
    assert t2_telem.ttft_ms == 0.0
    assert t2_telem.total_time_ms == 0.0
    assert client.get_last_telemetry().prompt == ""

    # Step 3: Query whitespace prompt
    t3_text, t3_telem = await client.generate("   \n\t  \r  ")
    assert t3_text == ""
    assert t3_telem.prompt == ""
    assert t3_telem.response == ""
    assert t3_telem.token_count == 0
    assert client.get_last_telemetry().prompt == ""

    # Step 4: Stream empty prompt
    chunks: List[TokenChunk] = []
    async for chunk in client.generate_stream(""):
        chunks.append(chunk)
    assert len(chunks) == 0
    stream_telem = client.get_last_telemetry()
    assert stream_telem is not None
    assert stream_telem.prompt == ""
    assert stream_telem.token_count == 0
    assert stream_telem.is_streaming is True

    # Step 5: Valid command again to verify clean resume
    t5_text, t5_telem = await client.generate("fermati subito")
    assert len(t5_text) > 0
    assert t5_telem.prompt == "fermati subito"
    assert client.get_last_telemetry().prompt == "fermati subito"

    await client.close()
    logger.info("PASS: Empty prompt telemetry isolation sequential test.")


@pytest.mark.asyncio
async def test_empty_prompt_telemetry_isolation_concurrent():
    """
    Stress 1.2: 40 concurrent mixed requests (alternating valid commands and empty/whitespace prompts).
    Verifies that concurrent tasks do NOT leak prompt/response telemetry across tasks.
    """
    client = HailoQwenClient(HailoQwenConfig(enable_mock_fallback=True, mock_ttft_ms=20.0, mock_inter_token_ms=2.0))
    client.mode = InferenceMode.MOCK_ENGINE

    prompts = [
        "" if (i % 3 == 0) else ("  \n\t  " if (i % 3 == 1) else f"command_task_{i}_ruota")
        for i in range(40)
    ]

    tasks = [client.generate(p) for p in prompts]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, res in enumerate(results):
        assert not isinstance(res, Exception), f"Task {i} failed: {res}"
        text, telem = res
        p = prompts[i]
        clean_p = p.strip() if p else ""
        if not clean_p:
            assert text == "", f"Task {i} (prompt={p!r}) returned non-empty text: {text!r}"
            assert telem.prompt == "", f"Task {i} leaked prompt: {telem.prompt!r}"
            assert telem.response == "", f"Task {i} leaked response: {telem.response!r}"
            assert telem.token_count == 0
        else:
            assert len(text) > 0
            assert telem.prompt == clean_p, f"Task {i} prompt mismatch: expected {clean_p!r}, got {telem.prompt!r}"

    await client.close()
    logger.info("PASS: Concurrent empty prompt telemetry isolation test (40 tasks).")


# ============================================================================
# FOCUS AREA 2: FALLBACK LATENCY & TTFT STREAM-LEVEL ABORT
# ============================================================================

@pytest.mark.asyncio
async def test_fallback_stream_abort_prevents_latency_doubling():
    """
    Stress 2.1: Primary model (1.5B) prefill exceeds TTFT budget.
    Verifies that stream-level abort stops 1.5B BEFORE token generation and cleanly
    falls back to 0.5B without running the full 1.5B generation (no latency doubling).
    """
    # Configure 1.5B with mock_ttft_ms = 300ms, but budget is 150ms.
    # 0.5B fallback runs at min(300, 160ms) = 160ms.
    config = HailoQwenConfig(
        enable_mock_fallback=True,
        ttft_budget_ms=150.0,
        mock_ttft_ms=300.0,
        mock_inter_token_ms=10.0,
    )
    client = HailoQwenClient(config)
    client.mode = InferenceMode.MOCK_ENGINE

    t0 = time.perf_counter()
    text, telem = await client.generate_with_fallback("vai dritto")
    elapsed = time.perf_counter() - t0

    history = client.get_telemetry_history()

    logger.info(
        "Fallback Stream Abort: Elapsed=%.3fs, History entries=%d, Returned model=%s",
        elapsed, len(history), telem.model
    )

    # 1. Returned model must be fallback 0.5B
    assert "0.5b" in telem.model
    assert len(text) > 0

    # 2. 1.5B must NOT have generated full response (stream aborted before yielding)
    # The history should contain only the completed 0.5B generation (1 entry),
    # or at most 0.5B without any completed 1.5B generation!
    completed_1_5b = [h for h in history if "1.5b" in h.model]
    assert len(completed_1_5b) == 0, f"1.5B was completed instead of aborted: {completed_1_5b}"

    # 3. Elapsed time check:
    # 150ms (timeout) + ~160ms (0.5B TTFT) + tokens (~60ms) ~ 0.37s - 0.45s.
    # In Iteration 1 without stream abort, it took 0.61s - 0.70s!
    assert elapsed < 0.55, f"Latency doubling observed! Elapsed was {elapsed:.3f}s (expected < 0.55s)"

    await client.close()
    logger.info("PASS: Fallback stream abort prevented latency doubling.")


@pytest.mark.asyncio
async def test_fallback_grace_return_when_tokens_arrive_in_budget():
    """
    Stress 2.2: When primary 1.5B yields token within budget, full response is returned
    and NEVER discarded, even if timer jitter pushes measured TTFT slightly past budget.
    """
    config = HailoQwenConfig(
        enable_mock_fallback=True,
        ttft_budget_ms=250.0,
        mock_ttft_ms=240.0,  # Within budget
        mock_inter_token_ms=5.0,
    )
    client = HailoQwenClient(config)
    client.mode = InferenceMode.MOCK_ENGINE

    t0 = time.perf_counter()
    text, telem = await client.generate_with_fallback("gira a sinistra")
    elapsed = time.perf_counter() - t0

    history = client.get_telemetry_history()
    assert len(history) == 1, f"Expected 1 history entry, got {len(history)}"
    assert "1.5b" in telem.model, f"Expected primary 1.5B, got {telem.model}"
    assert len(text) > 0
    assert elapsed < 0.45

    await client.close()
    logger.info("PASS: Fallback grace return preserved primary model response.")


# ============================================================================
# FOCUS AREA 3: TRANSIENT ERRORS & NO PERMANENT MOCK LATCHING
# ============================================================================

@pytest.mark.asyncio
async def test_transient_error_does_not_latch_mock_permanently():
    """
    Stress 3.1: Verify self-healing circuit breaker on transient hardware errors.
    Sequence:
    1. Healthy call -> HARDWARE_OLLAMA succeeds.
    2. Transient HTTP 500 error -> Served by mock fallback for this invocation, BUT client.mode remains HARDWARE_OLLAMA.
    3. Server recovers -> Subsequent call immediately targets HARDWARE_OLLAMA and succeeds on hardware (is_mock=False).
    4. Two consecutive failures -> Circuit opens; third request served by mock without hitting network.
    5. Cooldown expires -> Circuit half-opens; server is back, call recovers to hardware!
    """
    server = ConfigurableMockOllamaServer(port=11487)
    await server.start()

    config = HailoQwenConfig(
        endpoint=server.endpoint,
        enable_mock_fallback=True,
        max_consecutive_failures=2,
        reconnect_cooldown_sec=0.2,  # Short cooldown for test speed
        mock_ttft_ms=10.0,
        mock_inter_token_ms=2.0,
    )
    client = HailoQwenClient(config)
    # Probe and establish hardware mode
    mode = await client.check_connection()
    assert mode == InferenceMode.HARDWARE_OLLAMA
    assert client.mode == InferenceMode.HARDWARE_OLLAMA

    # Query 1: Healthy hardware call
    text1, telem1 = await client.generate("avanti")
    assert telem1.is_mock is False
    assert client.mode == InferenceMode.HARDWARE_OLLAMA
    assert client._consecutive_failures == 0

    # Query 2: Single transient 500 error
    server.fail_countdown = 1  # Fails once then recovers
    text2, telem2 = await client.generate("indietro")
    # Must be served via mock fallback
    assert telem2.is_mock is True
    # CRITICAL: Client mode MUST NOT be permanently latched to MOCK_ENGINE!
    assert client.mode == InferenceMode.HARDWARE_OLLAMA, (
        f"Defect FND-04 resurrected: client.mode was mutated to {client.mode}"
    )
    assert client._consecutive_failures == 1

    # Query 3: Server is healthy again. Client MUST automatically retry hardware!
    text3, telem3 = await client.generate("destra")
    assert client.mode == InferenceMode.HARDWARE_OLLAMA
    assert telem3.is_mock is False, "Client failed to automatically self-heal back to hardware mode!"
    assert client._consecutive_failures == 0

    # Query 4 & 5: Trigger 2 consecutive failures to trip circuit
    server.fail_countdown = 2
    text4, telem4 = await client.generate("sinistra")
    text5, telem5 = await client.generate("stop")
    assert client._consecutive_failures == 2
    assert client.is_circuit_open() is True
    assert client.mode == InferenceMode.HARDWARE_OLLAMA

    # Query 6: While circuit is open, request served by mock
    text6, telem6 = await client.generate("stato")
    assert telem6.is_mock is True

    # Wait for cooldown to expire
    await asyncio.sleep(0.25)
    assert client.is_circuit_open() is False

    # Query 7: Half-open recovery test (server normal)
    text7, telem7 = await client.generate("avanti")
    assert telem7.is_mock is False
    assert client._consecutive_failures == 0

    await client.close()
    await server.stop()
    logger.info("PASS: Transient error self-healing circuit breaker verified without mock latching.")


# ============================================================================
# FOCUS AREA 4: MID-STREAM ERRORS CLEAN ABORT & ZERO DUPLICATE TOKENS
# ============================================================================

@pytest.mark.asyncio
async def test_mid_stream_exception_prevents_duplicate_tokens():
    """
    Stress 4.1: Hardware connection severed mid-stream after tokens have been yielded.
    Verifies that client aborts immediately with clean RuntimeError rather than
    restarting from token 0 with mock generator (which caused duplicate tokens & corrupt JSON).
    """
    server = ConfigurableMockOllamaServer(port=11486)
    server.behavior = "disconnect_midstream"
    server.midstream_token_count = 3
    await server.start()

    config = HailoQwenConfig(
        endpoint=server.endpoint,
        enable_mock_fallback=True,
        timeout_sec=2.0,
    )
    client = HailoQwenClient(config)
    client.mode = InferenceMode.HARDWARE_OLLAMA

    yielded_chunks: List[TokenChunk] = []

    # Direct generate_stream test
    with pytest.raises(RuntimeError) as exc_info:
        async for chunk in client.generate_stream("esplora stanza"):
            yielded_chunks.append(chunk)

    err_msg = str(exc_info.value)
    logger.info("Caught expected mid-stream error: %s", err_msg)

    # 1. Error message indicates mid-generation interruption after 3 tokens
    assert "interrupted mid-generation after 3 tokens" in err_msg

    # 2. Exactly 3 chunks yielded before failure
    assert len(yielded_chunks) == 3

    # 3. Only ONE chunk had is_first=True
    first_chunks = [c for c in yielded_chunks if c.is_first]
    assert len(first_chunks) == 1, f"Multiple is_first tokens detected: {first_chunks}"

    # 4. No duplicate mock tokens yielded!
    tokens_text = [c.token for c in yielded_chunks]
    assert tokens_text == ["token_0", "token_1", "token_2"]

    # 5. Telemetry captured partial state
    telem = client.get_last_telemetry()
    assert telem is not None
    assert telem.token_count == 3
    assert telem.response == "token_0token_1token_2"
    assert telem.is_mock is False

    await client.close()
    await server.stop()
    logger.info("PASS: Mid-stream error cleanly raised without token duplication.")


@pytest.mark.asyncio
async def test_mid_stream_error_in_generate_with_fallback_recovery():
    """
    Stress 4.2: Primary 1.5B fails mid-stream, fallback 0.5B completes successfully on hardware.
    Verifies that generate_with_fallback catches the mid-stream failure on primary, aborts it,
    and fallback model executes and completes cleanly.
    """
    server = ConfigurableMockOllamaServer(port=11485)
    server.disconnect_countdown = 1  # Only the first request (1.5B) disconnects midstream; next (0.5B) succeeds
    server.midstream_token_count = 2
    await server.start()

    config = HailoQwenConfig(
        endpoint=server.endpoint,
        enable_mock_fallback=True,
        timeout_sec=2.0,
    )
    client = HailoQwenClient(config)
    client.mode = InferenceMode.HARDWARE_OLLAMA

    text, telem = await client.generate_with_fallback("naviga in cucina")

    # Fallback model should have cleanly generated valid response
    assert len(text) > 0
    assert "navigate_to" in text or "move_distance" in text or len(text) > 5
    assert "0.5b" in telem.model
    assert telem.is_mock is False

    await client.close()
    await server.stop()
    logger.info("PASS: Primary mid-stream failure recovered smoothly via fallback model on hardware.")


@pytest.mark.asyncio
async def test_mid_stream_error_in_generate_with_fallback_unrecoverable():
    """
    Stress 4.3: BOTH primary and fallback models fail mid-stream.
    Verifies clean RuntimeError propagation without duplicate mock tokens or state corruption.
    """
    server = ConfigurableMockOllamaServer(port=11483)
    server.behavior = "disconnect_midstream"  # All requests disconnect midstream
    server.midstream_token_count = 2
    await server.start()

    config = HailoQwenConfig(
        endpoint=server.endpoint,
        enable_mock_fallback=True,
        timeout_sec=2.0,
    )
    client = HailoQwenClient(config)
    client.mode = InferenceMode.HARDWARE_OLLAMA

    with pytest.raises(RuntimeError) as exc_info:
        await client.generate_with_fallback("naviga in cucina")

    assert "interrupted mid-generation after 2 tokens" in str(exc_info.value)
    await client.close()
    await server.stop()
    logger.info("PASS: Unrecoverable mid-stream failure cleanly raised RuntimeError.")


# ============================================================================
# EXTENDED CONCURRENCY, PAYLOAD & MEMORY STRESS
# ============================================================================

@pytest.mark.asyncio
async def test_burst_50_concurrent_requests_mock():
    """Stress 5.1: 50 concurrent requests in mock mode."""
    config = HailoQwenConfig(
        enable_mock_fallback=True,
        mock_ttft_ms=25.0,
        mock_inter_token_ms=2.0,
        max_history_records=100,
    )
    client = HailoQwenClient(config)
    client.mode = InferenceMode.MOCK_ENGINE

    prompts = [f"task_{i}_avanti" for i in range(50)]
    t0 = time.perf_counter()
    tasks = [client.generate(p) for p in prompts]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    t_elapsed = time.perf_counter() - t0

    for i, res in enumerate(results):
        assert not isinstance(res, Exception), f"Task {i} failed: {res}"
        text, telem = res
        assert len(text) > 0

    assert len(client.get_telemetry_history()) == 50
    assert t_elapsed < 2.0
    await client.close()
    logger.info("PASS: 50 concurrent mock requests in %.2fs.", t_elapsed)


@pytest.mark.asyncio
async def test_burst_50_concurrent_requests_http():
    """Stress 5.2: 50 concurrent requests over HTTP connection pool."""
    server = ConfigurableMockOllamaServer(port=11484)
    await server.start()

    config = HailoQwenConfig(
        endpoint=server.endpoint,
        enable_mock_fallback=False,
        timeout_sec=5.0,
    )
    client = HailoQwenClient(config)
    client.mode = InferenceMode.HARDWARE_OLLAMA

    prompts = [f"http_task_{i}" for i in range(50)]
    t0 = time.perf_counter()
    tasks = [client.generate(p) for p in prompts]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    t_elapsed = time.perf_counter() - t0

    await client.close()
    await server.stop()

    failed = [r for r in results if isinstance(r, Exception)]
    assert len(failed) == 0, f"HTTP burst failures: {failed}"
    logger.info("PASS: 50 concurrent HTTP requests in %.2fs.", t_elapsed)


@pytest.mark.asyncio
async def test_extreme_and_malformed_payloads():
    """Stress 5.3: Massive 50k chars, emojis, null bytes, SQL/JSON injection."""
    client = HailoQwenClient(HailoQwenConfig(enable_mock_fallback=True, mock_ttft_ms=5.0, mock_inter_token_ms=1.0))
    client.mode = InferenceMode.MOCK_ENGINE

    payloads = [
        "🤖🔥🏎️🚨 멈춰! 停止! تَوَقَّفْ",
        "avanti \x00 null \x1b[31m ansi \r\n newline",
        '{"action": "move_distance", "parameters": {unclosed',
        "'" * 1000 + '"' * 1000,
        "%s%d%n%x" * 200,
        "<script>alert('xss')</script>",
        "SELECT * FROM rooms WHERE 1=1; DROP TABLE telemetry;",
        "vai avanti " * 5000,  # ~55,000 characters
    ]

    for p in payloads:
        text, telem = await client.generate(p)
        assert isinstance(text, str)
        assert len(text) > 0

    await client.close()
    logger.info("PASS: Extreme and malformed payloads processed safely.")


@pytest.mark.asyncio
async def test_memory_stability_100_cycles():
    """Stress 5.4: 100 consecutive generation cycles with RSS memory guard (< 5.0 MB)."""
    client = HailoQwenClient(HailoQwenConfig(enable_mock_fallback=True, mock_ttft_ms=2.0, mock_inter_token_ms=0.5))
    client.mode = InferenceMode.MOCK_ENGINE

    proc = psutil.Process(os.getpid())
    import gc
    gc.collect()
    rss_start = proc.memory_info().rss / (1024.0 * 1024.0)

    for i in range(100):
        text, telem = await client.generate(f"cycle_{i}_ruota")
        assert len(text) > 0

    gc.collect()
    rss_end = proc.memory_info().rss / (1024.0 * 1024.0)
    rss_delta = max(0.0, rss_end - rss_start)

    assert rss_delta < 5.0, f"Memory leak detected: RSS grew by {rss_delta:.3f} MB"
    assert len(client.get_telemetry_history()) == 100
    await client.close()
    logger.info("PASS: Memory stability 100 cycles (RSS delta = %.3f MB).", rss_delta)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
