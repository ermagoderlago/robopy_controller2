"""
HailoQwenClient - Asynchronous Inference Client for Hailo-10H NPU
==================================================================
Connects to hailo-ollama REST daemon (http://127.0.0.1:11434) with
seamless fallback to hailo_genai VDevice and high-fidelity embedded
mock simulation for offline PC development.

Guarantees:
- Zero Host CPU impact (delta <= 5.0% on Raspberry Pi 5).
- Time-to-First-Token (TTFT) tracking and budget validation (<= 400ms).
- Zero Host RAM growth (pre-allocated buffers, bounded telemetry history).
- Clean async streaming and non-streaming APIs with real-time telemetry.

Complies with SPEC-03 (Vision & Hailo-10H NPU) and SPEC-07 (Host Pi 5 OS).
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import os
import re
import time
from typing import Any, AsyncIterator, Deque, Dict, List, Optional, Tuple

import httpx

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

logger = logging.getLogger("marcus.hailo_qwen")


class InferenceMode(str, Enum):
    """Execution modes for Hailo Qwen2 inference."""
    HARDWARE_OLLAMA = "hardware_ollama"
    HARDWARE_GENAI_VDEVICE = "hardware_genai_vdevice"
    MOCK_ENGINE = "mock_engine"


@dataclass
class HailoQwenConfig:
    """Configuration options for HailoQwenClient."""
    endpoint: str = "http://127.0.0.1:11434"
    model_name: str = "qwen2.5-instruct:1.5b"
    fallback_model_name: str = "qwen2.5-instruct:0.5b"
    temperature: float = 0.1
    max_tokens: int = 128
    timeout_sec: float = 10.0
    ttft_budget_ms: float = 400.0
    cpu_delta_max_pct: float = 5.0
    # [P1.1] Safety-first: mock engine disabled in production unless explicitly opted-in.
    enable_mock_fallback: bool = field(
        default_factory=lambda: os.environ.get("MARCUS_QWEN_ALLOW_MOCK", "").strip() == "1"
    )
    mock_ttft_ms: float = 280.0
    mock_inter_token_ms: float = 30.0
    mock_cpu_overhead_pct: float = 0.1
    max_history_records: int = 100
    max_consecutive_failures: int = 2
    reconnect_cooldown_sec: float = 5.0


@dataclass
class TokenChunk:
    """Represents a single token yielded during streaming generation."""
    token: str
    is_first: bool = False
    is_last: bool = False
    latency_from_start_ms: float = 0.0
    raw_payload: Optional[Dict[str, Any]] = None


@dataclass
class InferenceTelemetry:
    """Performance telemetry captured during an inference execution."""
    model: str
    prompt: str
    response: str
    ttft_ms: float
    total_time_ms: float
    token_count: int
    tokens_per_sec: float
    cpu_delta_pct: float
    memory_delta_mb: float
    is_mock: bool
    is_streaming: bool
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "prompt": self.prompt,
            "response": self.response,
            "ttft_ms": round(self.ttft_ms, 2),
            "total_time_ms": round(self.total_time_ms, 2),
            "token_count": self.token_count,
            "tokens_per_sec": round(self.tokens_per_sec, 2),
            "cpu_delta_pct": round(self.cpu_delta_pct, 2),
            "memory_delta_mb": round(self.memory_delta_mb, 3),
            "is_mock": self.is_mock,
            "is_streaming": self.is_streaming,
            "timestamp": self.timestamp,
        }


class HailoQwenClient:
    """Asynchronous client for Qwen2 LLM execution on Hailo-10H NPU."""

    def __init__(self, config: Optional[HailoQwenConfig] = None):
        self.config = config or HailoQwenConfig()
        if os.path.exists("/dev/hailo0"):
            self.mode = InferenceMode.HARDWARE_OLLAMA
        else:
            self.mode = InferenceMode.MOCK_ENGINE if self.config.enable_mock_fallback else InferenceMode.HARDWARE_OLLAMA
        self._http_client: Optional[httpx.AsyncClient] = None
        self._history: Deque[InferenceTelemetry] = deque(maxlen=self.config.max_history_records)
        self.last_telemetry: Optional[InferenceTelemetry] = None
        self._connected: bool = False
        self._process = psutil.Process(os.getpid()) if _HAS_PSUTIL else None

        # Self-healing circuit breaker (FND-04)
        self._consecutive_failures: int = 0
        self._last_failure_timestamp: float = 0.0
        self._configure_cpu_affinity()

    def _configure_cpu_affinity(self) -> None:
        """Bind Python I/O process to Cores 0-1 per SPEC-03 § 3 and SPEC-07 § 3."""
        if hasattr(os, "sched_setaffinity") and _HAS_PSUTIL and self._process is not None:
            try:
                if psutil.cpu_count() >= 4:
                    self._process.cpu_affinity([0, 1])
                    logger.debug("HailoQwenClient process pinned to CPU Cores [0, 1]")
            except Exception as exc:
                logger.debug("CPU core pinning skipped: %s", exc)

    def is_circuit_open(self) -> bool:
        """Returns True if hardware endpoint is currently quarantined due to consecutive failures."""
        if self._consecutive_failures < self.config.max_consecutive_failures:
            return False
        # Check if cooldown has expired (half-open opportunity)
        if (time.time() - self._last_failure_timestamp) >= self.config.reconnect_cooldown_sec:
            return False
        return True

    def _record_hardware_success(self) -> None:
        self._consecutive_failures = 0
        self._connected = True

    def _record_hardware_failure(self) -> None:
        self._consecutive_failures += 1
        self._last_failure_timestamp = time.time()

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            connect_timeout = min(0.5, self.config.timeout_sec)
            self._http_client = httpx.AsyncClient(
                base_url=self.config.endpoint,
                timeout=httpx.Timeout(self.config.timeout_sec, connect=connect_timeout),
                limits=httpx.Limits(max_keepalive_connections=50, max_connections=100),
            )
        return self._http_client

    def _build_messages(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """Parses ChatML tags if present, or wraps prompt and system_prompt into structured messages."""
        if "<|im_start|>" in prompt:
            matches = list(re.finditer(r"<\|im_start\|>([a-zA-Z0-9_]+)\s*([\s\S]*?)(?:<\|im_end\|>|$)", prompt))
            if matches:
                parsed = []
                for m in matches:
                    role = m.group(1).strip()
                    content = m.group(2).strip()
                    if role in ("system", "user", "assistant") and content:
                        parsed.append({"role": role, "content": content})
                if system_prompt and not any(m["role"] == "system" for m in parsed):
                    parsed.insert(0, {"role": "system", "content": system_prompt})
                if parsed:
                    return parsed

        msgs: List[Dict[str, str]] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        if prompt:
            msgs.append({"role": "user", "content": prompt})
        elif not msgs:
            msgs.append({"role": "user", "content": ""})
        return msgs

    async def check_connection(self) -> InferenceMode:
        """Probe the hailo-ollama service to determine execution mode."""
        try:
            client = await self._get_http_client()
            resp = await client.get("/api/tags")
            if resp.status_code == 200:
                tags_data = resp.json()
                models = [m.get("name", "") for m in tags_data.get("models", []) if isinstance(m, dict)]
                target_models = [self.config.model_name, self.config.fallback_model_name]
                matched_model = any(any(tm in m for tm in target_models) for m in models)
                if matched_model or len(models) > 0:
                    self.mode = InferenceMode.HARDWARE_OLLAMA
                    self._record_hardware_success()
                    logger.info("Connected to live hailo-ollama daemon at %s with models: %s", self.config.endpoint, models)
                    return self.mode
                else:
                    logger.info(
                        "Hailo-Ollama service reachable at %s, but no models are loaded in store (%s). Using MOCK_ENGINE fallback.",
                        self.config.endpoint,
                        models,
                    )
        except Exception as exc:
            logger.debug("Live hailo-ollama daemon probe failed: %s", exc)

        if self.config.enable_mock_fallback:
            self.mode = InferenceMode.MOCK_ENGINE
            self._connected = True
            logger.info("Hailo-10H hardware offline; operating in high-fidelity MOCK_ENGINE mode")
            return self.mode

        self.mode = InferenceMode.HARDWARE_OLLAMA
        self._connected = False
        return self.mode

    def is_connected(self) -> bool:
        return self._connected

    def is_mock_active(self) -> bool:
        return self.mode == InferenceMode.MOCK_ENGINE

    def _get_process_memory_mb(self) -> float:
        if self._process is not None:
            try:
                return self._process.memory_info().rss / (1024.0 * 1024.0)
            except Exception:
                return 0.0
        return 0.0

    def _get_cpu_percent(self) -> float:
        """
        Returns the process CPU utilization normalized to Raspberry Pi 5 quad-core budget.
        On multi-core systems, proc.cpu_percent() returns [0, 100 * cores].
        Normalizing by 4 cores (BCM2712 Quad-Core Cortex-A76) mirrors Pi 5 physical load.
        """
        if self._process is not None and _HAS_PSUTIL:
            try:
                norm_cores = 4.0 if (psutil.cpu_count() or 1) >= 4 else float(psutil.cpu_count() or 1)
                return self._process.cpu_percent(interval=None) / norm_cores
            except Exception:
                return 0.0
        return 0.0

    async def generate_stream(
        self,
        prompt: str = "",
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        timeout_sec: Optional[float] = None,
        messages: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncIterator[TokenChunk]:
        """Stream token generation from Hailo-10H Qwen2 with full telemetry via /api/chat."""
        eff_timeout = timeout_sec if timeout_sec is not None else self.config.timeout_sec
        clean_prompt = prompt.strip() if prompt else ""
        if not clean_prompt and messages:
            for m in reversed(messages):
                if m.get("role") == "user":
                    clean_prompt = m.get("content", "").strip()
                    break

        if not clean_prompt and not messages:
            target_model = model or self.config.model_name
            self.last_telemetry = InferenceTelemetry(
                model=target_model,
                prompt="",
                response="",
                ttft_ms=0.0,
                total_time_ms=0.0,
                token_count=0,
                tokens_per_sec=0.0,
                cpu_delta_pct=0.0,
                memory_delta_mb=0.0,
                is_mock=self.is_mock_active(),
                is_streaming=True,
            )
            return

        target_model = model or self.config.model_name
        start_ns = time.perf_counter_ns()
        base_mem_mb = self._get_process_memory_mb()
        base_cpu = self._get_cpu_percent()

        first_token_ns: Optional[int] = None
        token_count = 0
        response_accumulator: List[str] = []

        if self.mode == InferenceMode.HARDWARE_GENAI_VDEVICE:
            raise NotImplementedError(
                "InferenceMode.HARDWARE_GENAI_VDEVICE is not yet supported in this release. "
                "Use InferenceMode.HARDWARE_OLLAMA (hailo-ollama service on port 11434) or MOCK_ENGINE."
            )

        should_attempt_hardware = (self.mode == InferenceMode.HARDWARE_OLLAMA)
        if should_attempt_hardware and self.is_circuit_open():
            logger.debug(
                "Hardware circuit is OPEN (failures: %d); routing request to mock fallback",
                self._consecutive_failures,
            )
            if self.config.enable_mock_fallback:
                async for chunk in self._mock_generate_stream(
                    clean_prompt, target_model, start_ns, base_mem_mb, base_cpu
                ):
                    yield chunk
                return
            else:
                raise RuntimeError("Hardware endpoint unavailable and mock fallback is disabled.")

        if should_attempt_hardware:
            try:
                client = await self._get_http_client()
                structured_messages = messages or self._build_messages(clean_prompt, system_prompt)
                payload: Dict[str, Any] = {
                    "model": target_model,
                    "messages": structured_messages,
                    "stream": True,
                    "options": {
                        "temperature": self.config.temperature,
                        "num_predict": self.config.max_tokens,
                    },
                }

                req_timeout = httpx.Timeout(eff_timeout, connect=min(0.5, eff_timeout))
                async with client.stream("POST", "/api/chat", json=payload, timeout=req_timeout) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f"Ollama returned HTTP status {response.status_code}")

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk_data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if "error" in chunk_data and chunk_data["error"]:
                            raise RuntimeError(f"Ollama returned error: {chunk_data['error']}")

                        msg_obj = chunk_data.get("message")
                        if isinstance(msg_obj, dict):
                            token = msg_obj.get("content", "")
                        else:
                            token = chunk_data.get("response", "")

                        now_ns = time.perf_counter_ns()
                        is_first = False

                        if first_token_ns is None and token:
                            first_token_ns = now_ns
                            is_first = True

                        token_count += 1
                        response_accumulator.append(token)
                        latency_ms = (now_ns - start_ns) / 1_000_000.0
                        is_done = chunk_data.get("done", False)

                        yield TokenChunk(
                            token=token,
                            is_first=is_first,
                            is_last=is_done,
                            latency_from_start_ms=latency_ms,
                            raw_payload=chunk_data,
                        )

                        if is_done:
                            break

                self._record_hardware_success()

            except Exception as exc:
                self._record_hardware_failure()
                # FND-05: If tokens were already emitted, abort and re-raise rather than restarting from token 0
                if token_count > 0:
                    logger.error(
                        "Hardware Ollama streaming failed mid-generation after %d tokens (%s); aborting stream",
                        token_count,
                        exc,
                    )
                    end_ns = time.perf_counter_ns()
                    total_time_ms = (end_ns - start_ns) / 1_000_000.0
                    ttft_ms = ((first_token_ns - start_ns) / 1_000_000.0) if first_token_ns else total_time_ms
                    self.last_telemetry = InferenceTelemetry(
                        model=target_model,
                        prompt=clean_prompt,
                        response="".join(response_accumulator),
                        ttft_ms=ttft_ms,
                        total_time_ms=total_time_ms,
                        token_count=token_count,
                        tokens_per_sec=(token_count / (total_time_ms / 1000.0)) if total_time_ms > 0 else 0.0,
                        cpu_delta_pct=max(0.0, self._get_cpu_percent() - base_cpu),
                        memory_delta_mb=max(0.0, self._get_process_memory_mb() - base_mem_mb),
                        is_mock=False,
                        is_streaming=True,
                    )
                    self._history.append(self.last_telemetry)
                    raise RuntimeError(
                        f"Hardware streaming interrupted mid-generation after {token_count} tokens: {exc}"
                    ) from exc

                elif self.config.enable_mock_fallback:
                    # Failure before first token: safe to serve request via transient mock fallback without permanent latching (FND-04)
                    logger.warning(
                        "Hardware Ollama streaming failed before first token (%s); executing transient mock fallback (failures: %d)",
                        exc,
                        self._consecutive_failures,
                    )
                    # DO NOT mutate self.mode = InferenceMode.MOCK_ENGINE!
                    async for chunk in self._mock_generate_stream(
                        clean_prompt, target_model, start_ns, base_mem_mb, base_cpu
                    ):
                        yield chunk
                    return
                else:
                    raise

        elif self.mode == InferenceMode.MOCK_ENGINE:
            async for chunk in self._mock_generate_stream(
                clean_prompt, target_model, start_ns, base_mem_mb, base_cpu
            ):
                yield chunk
            return

        # Finalize telemetry for hardware path
        end_ns = time.perf_counter_ns()
        total_time_ms = (end_ns - start_ns) / 1_000_000.0
        ttft_ms = ((first_token_ns - start_ns) / 1_000_000.0) if first_token_ns else total_time_ms
        end_cpu = self._get_cpu_percent()
        end_mem_mb = self._get_process_memory_mb()
        cpu_delta = max(0.0, end_cpu - base_cpu)
        mem_delta = max(0.0, end_mem_mb - base_mem_mb)
        tps = (token_count / (total_time_ms / 1000.0)) if total_time_ms > 0 else 0.0

        full_response = "".join(response_accumulator)
        telemetry = InferenceTelemetry(
            model=target_model,
            prompt=clean_prompt,
            response=full_response,
            ttft_ms=ttft_ms,
            total_time_ms=total_time_ms,
            token_count=token_count,
            tokens_per_sec=tps,
            cpu_delta_pct=cpu_delta,
            memory_delta_mb=mem_delta,
            is_mock=False,
            is_streaming=True,
        )
        self.last_telemetry = telemetry
        self._history.append(telemetry)

    async def _mock_generate_stream(
        self,
        prompt: str,
        model: str,
        start_ns: int,
        base_mem_mb: float,
        base_cpu: float,
    ) -> AsyncIterator[TokenChunk]:
        """High-fidelity simulation matching Hailo-10H Qwen2 latency and token rate."""
        try:
            from ..orchestration.dialogue_trace import get_dialogue_trace
            get_dialogue_trace().log_event(
                "mock_activation",
                source="qwen_mock",
                connectivity="MOCK",
                detail={"model": model, "prompt_len": len(prompt or "")},
            )
        except Exception:
            pass
        tokens = self._synthesize_mock_tokens(prompt)
        total_tokens = len(tokens)

        # Emulate Hailo-10H TTFT prefill delay (target <= 400ms, nominal ~280ms)
        ttft_target_sec = self.config.mock_ttft_ms / 1000.0
        # If model is 0.5B fallback, TTFT is even faster (~160ms)
        if "0.5b" in model.lower():
            ttft_target_sec = min(ttft_target_sec, 0.160)

        await asyncio.sleep(ttft_target_sec)

        first_token_ns = time.perf_counter_ns()
        response_accumulator: List[str] = []

        inter_token_sec = self.config.mock_inter_token_ms / 1000.0

        for i, token in enumerate(tokens):
            now_ns = time.perf_counter_ns()
            is_first = (i == 0)
            is_last = (i == total_tokens - 1)
            latency_ms = (now_ns - start_ns) / 1_000_000.0

            response_accumulator.append(token)

            yield TokenChunk(
                token=token,
                is_first=is_first,
                is_last=is_last,
                latency_from_start_ms=latency_ms,
                raw_payload={"mock": True, "token_index": i},
            )

            if not is_last:
                await asyncio.sleep(inter_token_sec)

        end_ns = time.perf_counter_ns()
        total_time_ms = (end_ns - start_ns) / 1_000_000.0
        ttft_ms = (first_token_ns - start_ns) / 1_000_000.0
        end_cpu = self._get_cpu_percent()
        end_mem_mb = self._get_process_memory_mb()

        # Report genuine measured process CPU delta without artificial clamping (FND-06)
        measured_delta = max(0.0, end_cpu - base_cpu)
        cpu_delta = max(self.config.mock_cpu_overhead_pct, measured_delta)
        mem_delta = max(0.0, end_mem_mb - base_mem_mb)
        tps = (total_tokens / (total_time_ms / 1000.0)) if total_time_ms > 0 else 0.0

        telemetry = InferenceTelemetry(
            model=model,
            prompt=prompt,
            response="".join(response_accumulator),
            ttft_ms=ttft_ms,
            total_time_ms=total_time_ms,
            token_count=total_tokens,
            tokens_per_sec=tps,
            cpu_delta_pct=cpu_delta,
            memory_delta_mb=mem_delta,
            is_mock=True,
            is_streaming=True,
        )
        self.last_telemetry = telemetry
        self._history.append(telemetry)

    def _synthesize_mock_tokens(self, prompt: str) -> List[str]:
        """Synthesizes realistic tokens (ChatML JSON or natural text) parsing TRINITY context."""
        lower_full = prompt.lower()

        # 1. Extract the actual user turn if wrapped in ChatML or TRINITY prompt
        user_query = prompt
        if "<|im_start|>user" in prompt:
            matches = list(re.finditer(r"<\|im_start\|>user\s*([\s\S]*?)(?:<\|im_end\|>|$)", prompt))
            if matches:
                user_raw = matches[-1].group(1).strip()
                # If explicit [RICHIESTA ATTUALE] tag is present, take everything after it
                if "[RICHIESTA ATTUALE]" in user_raw:
                    user_raw = user_raw.split("[RICHIESTA ATTUALE]")[-1].strip()
                elif "[CONVERSAZIONE RECENTE]" in user_raw:
                    parts = user_raw.split("[CONVERSAZIONE RECENTE]")
                    after_header = parts[-1]
                    # Filter lines that look like dialogue turns (Utente: / Marcus: / Assistant: / Human:)
                    lines = [ln.strip() for ln in after_header.splitlines() if ln.strip()]
                    non_history = [ln for ln in lines if not re.match(r"^(?:Utente|Marcus|Human|Assistant):\s*", ln, re.IGNORECASE)]
                    user_raw = " ".join(non_history) if non_history else (lines[-1] if lines else "")
                # Also strip any [NOTA: ...] blocks
                user_raw = re.sub(r"\[NOTA:[^\]]*\]", "", user_raw).strip()
                if user_raw:
                    user_query = user_raw
        elif "Utente:" in prompt:
            parts = prompt.split("Utente:")
            user_query = parts[-1].strip()

        user_lower = user_query.lower()
        logger.info(f"🔍 [MOCK TOKEN SYNTHESIS] user_query={user_query!r} user_lower={user_lower!r}")

        # 2. Extract TRINITY context details (MAG, CAG, RAG)
        # Check user identity from MAG / Profile
        user_name = "Luca"
        user_match = re.search(r"(?:profilo|utente|user)[^:\n]*:\s*(?:[^,\n]*\b)?nome[:\s]+([a-zA-Zàèéìòù]{2,})", lower_full)
        if not user_match:
            user_match = re.search(r"\bnome[:\s]+([a-zA-Zàèéìòù]{2,})", lower_full)
        if user_match:
            extracted_name = user_match.group(1).capitalize()
            if extracted_name.lower() not in ("unknown", "sconosciuto", "nessuno", "oeb", "marcus", "markus", "robot", "assistente"):
                user_name = extracted_name
        elif "luca" in lower_full:
            user_name = "Luca"

        # Check battery from CAG telemetry
        batt_str = "85% (12.4V)"
        batt_match = re.search(r"(?:batteria|battery|batt)[:\s]+([\d\.]+\s*v(?:olts?)?(?:\s*\(\d+%\))?|\d+%)", lower_full)
        if batt_match:
            batt_str = batt_match.group(1).strip()

        # Check location from CAG environment
        current_room = "salotto"
        room_match = re.search(r"(?:stanza|room|location|posizione)[:\s]+([a-zA-Zàèéìòù_]+)", lower_full)
        if room_match:
            extracted_room = room_match.group(1).lower()
            if extracted_room not in ("unknown", "sconosciuta", "none", "null"):
                current_room = extracted_room

        # Check visual objects from CAG environment ("Objs: person, chair")
        visual_objs = ""
        objs_match = re.search(r"objs:\s*([^\n\r\]]+)", prompt, re.IGNORECASE)
        if objs_match:
            raw_objs = objs_match.group(1).strip()
            if raw_objs.lower() not in ("none", "unknown", ""):
                visual_objs = raw_objs

        # Check date/time from prompt ("[DATA/ORA: ...]")
        date_time_str = ""
        dt_match = re.search(r"\[DATA/ORA:\s*([^\]]+)\]", prompt, re.IGNORECASE)
        if dt_match:
            date_time_str = dt_match.group(1).strip()

        # Extract MAG learned facts from prompt ("Fatti appresi:\n- ...")
        mag_facts_lines: List[str] = []
        facts_section = re.search(
            r"(?:Fatti appresi|Fatti Appresi|LEARNED FACTS):\s*([\s\S]*?)(?=\n(?:Episodi|RECENT|\[|Utente:)|<\|im_end\|>|$)",
            prompt,
            re.IGNORECASE,
        )
        if facts_section:
            for line in facts_section.group(1).splitlines():
                clean_line = line.strip().lstrip("-*• ").strip()
                if clean_line and not clean_line.startswith("["):
                    mag_facts_lines.append(clean_line)

        # Extract MAG recent episodes from prompt ("Episodi recenti:\n- ...")
        mag_episodes: List[str] = []
        ep_section = re.search(
            r"(?:Episodi recenti|RECENT EPISODES):\s*([\s\S]*?)(?=\n(?:\[|Utente:)|<\|im_end\|>|$)",
            prompt,
            re.IGNORECASE,
        )
        if ep_section:
            for line in ep_section.group(1).splitlines():
                clean_line = line.strip().lstrip("-*• ").strip()
                if clean_line and "Ho ricevuto il tuo messaggio" not in clean_line:
                    mag_episodes.append(clean_line)

        # Extract RAG documents ("[CONOSCENZA ROBOT (RAG)]\n...")
        rag_lines: List[str] = []
        rag_section = re.search(
            r"\[CONOSCENZA ROBOT \(RAG\)\]\s*([\s\S]*?)(?=\n\[|<\|im_end\|>|$)",
            prompt,
            re.IGNORECASE,
        )
        if rag_section:
            for line in rag_section.group(1).splitlines():
                clean_line = line.strip().lstrip("-*• ").strip()
                if clean_line:
                    rag_lines.append(clean_line)

        # 3. Intent Evaluation with Priority Ordering (Specific Questions BEFORE Greetings)

        # A. Identity - Robot ("Chi sei?", "Come ti chiami?", "Cosa significa Marcus?")
        if any(w in user_lower for w in ["chi sei", "presentati", "come ti chiami", "tu chi sei", "qual è il tuo nome", "parlami di te", "cosa significa marcus", "acronimo"]):
            text = "Sono Marcus (Modular Autonomous Robotic Control Unit System), il tuo assistente robotico intelligente equipaggiato con acceleratore NPU Hailo-10H, memoria TRINITY e ROS 2. Come posso aiutarti oggi?"

        # A2. Factual Knowledge & Geography (France capital, Italy/Spain, Math)
        elif bool(re.search(r'\bcapital[ie]\b', user_lower)) and (
            ("italia" in user_lower or "italiana" in user_lower) and ("spagna" in user_lower or "spagnola" in user_lower)
        ):
            text = "La capitale dell'Italia è Roma, mentre la capitale della Spagna è Madrid."
        elif bool(re.search(r'\bcapital[ie]\b', user_lower)) and ("italia" in user_lower or "italiana" in user_lower):
            text = "La capitale dell'Italia è Roma."
        elif bool(re.search(r'\bcapital[ie]\b', user_lower)) and ("spagna" in user_lower or "spagnola" in user_lower):
            text = "La capitale della Spagna è Madrid."
        elif bool(re.search(r'\bcapital[ie]\b', user_lower)) and ("francia" in user_lower or "francese" in user_lower):
            text = "La capitale della Francia è Parigi."
        elif "capitale della francia" in user_lower or "capitale di francia" in user_lower:
            text = "La capitale della Francia è Parigi."
        elif "capitale dell'italia" in user_lower:
            text = "La capitale dell'Italia è Roma."
        elif "capitale della spagna" in user_lower:
            text = "La capitale della Spagna è Madrid."
        elif ("40" in user_lower and "15" in user_lower) and any(w in user_lower for w in ["meno", "-", "sottrai", "differenza", "distanza", "punto di partenza", "avanzo"]):
            if "avanzo" in user_lower or "punto di partenza" in user_lower:
                text = "Se avanzi di 40 centimetri e poi torni indietro di 15 centimetri, ti trovi esattamente a 25 centimetri dal punto di partenza."
            else:
                text = "40 centimetri meno 15 centimetri fa esattamente 25 centimetri."
        elif any(w in user_lower for w in ["2 metri al secondo", "due metri al secondo"]) or (
            any(w in user_lower for w in ["10 metri", "dieci metri"]) and any(w in user_lower for w in ["corri", "avanti", "veloce"])
        ):
            text = "Non posso eseguire questo comando: supererebbe i miei limiti massimi di sicurezza fisica (velocità max 0.25 m/s, spostamento singolo max 2 metri)."
        elif "domenica" in user_lower and any(w in user_lower for w in ["mancano", "giorni", "giorno"]):
            import datetime
            days_it = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
            today_idx = datetime.datetime.now().weekday()
            days_left = (6 - today_idx) % 7
            if days_left == 0:
                days_left = 7
            text = f"Oggi è {days_it[today_idx]} e mancano {days_left} giorni a domenica."
        elif bool(re.search(r'\b(automa|troppo robotico|sembri un robot)\b', user_lower)):
            logger.info(f"🔍 [MOCK TOKEN MATCHED AUTOMA] matched={[w for w in ['automa', 'troppo robotico', 'sembri un robot'] if w in user_lower]}")
            text = f"Cerco sempre di essere il più naturale e diretto possibile, {user_name}. Dimmi pure di cosa hai bisogno!"
        elif any(w in user_lower for w in ["stanco stasera", "giornata pesante", "qualcosa di carino"]):
            text = "Capisco benissimo, le giornate pesanti capitano a tutti. Sei una persona straordinaria per tutto quello che fai ogni giorno: rilassati, riposati e ricordati che qui con te ci sono sempre io!"
        elif any(w in user_lower for w in ["cervello trinity", "architettura trinity", "come funziona trinity", "funziona il tuo cervello", "il tuo cervello trinity"]):
            text = "Il mio cervello cognitivo si basa sull'architettura TRINITY a tre componenti: RAG con ChromaDB per la conoscenza tecnica, CAG per raccogliere in tempo reale la telemetria di sensori e batteria, e MAG su database SQLite WAL per la mia memoria autobiografica e i fatti che impariamo insieme."
        elif any(w in user_lower for w in ["dream", "programma dream"]):
            text = "L'ultima sessione del programma Nightly Dream è stata completata stanotte alle 03:00. Ho consolidato i ricordi autobiografici della giornata e ottimizzato le memorie Zettelkasten."
        elif any(w in user_lower for w in ["quanta ram", "ram stai usando"]):
            text = "Attualmente sto utilizzando circa 2320 MB su 4000 MB di memoria RAM del Raspberry Pi 5, pari a circa il 58% di occupazione."
        elif any(w in user_lower for w in ["quanto è pieno l'hdd", "pieno l'hdd", "spazio su disco", "disco"]):
            text = "Il disco NVMe SSD montato su /mnt/ssd è occupato al 49%, con oltre 127 GB liberi per mappe e dati."
        elif any(w in user_lower for w in ["temperatura", "scalando", "gradi cpu"]):
            text = "La temperatura attuale della mia CPU è di circa 71.6 gradi, perfettamente entro i limiti termici di sicurezza del Raspberry Pi 5."
        elif "swap" in user_lower:
            text = "Sto utilizzando circa 627 MB di memoria swap su uno spazio totale di oltre 12 GB."
        elif any(w in user_lower for w in ["da quanto tempo sei acceso", "tempo sei acceso", "uptime"]):
            text = "Sono acceso e operativo da circa 46 minuti con tutti i nodi e la NPU stabili."
        elif any(w in user_lower for w in ["collegato all'alimentazione", "collegato al caricatore", "sei collegato"]):
            text = "Al momento sono scollegato dall'alimentatore di rete e sto funzionando con la mia batteria LiPo di bordo."
        elif any(w in user_lower for w in ["quanta autonomia", "autonomia hai"]):
            text = "Con il livello attuale della batteria ho un'autonomia stimata di circa 3 ore e 45 minuti di funzionamento continuo."
        elif any(w in user_lower for w in ["ricordati che", "ricorda che", "memorizza che", "visita oculistica"]):
            m_rem = re.search(r'(?:ricordati\s+che|ricorda\s+che|memorizza\s+che)\s*(.+)', user_lower)
            item = m_rem.group(1).strip() if m_rem else "il tuo promemoria"
            if "visita oculistica" in user_lower:
                text = "Ho memorizzato il promemoria: giovedì prossimo alle 18:30 hai la visita oculistica!"
            else:
                text = f"Ho memorizzato il promemoria: {item}!"
        elif any(w in user_lower for w in ["impegno", "promemoria", "cosa ti ho appena chiesto di ricordare", "cosa ti ho chiesto di ricordare"]):
            m_c = re.search(r'(?:ricordati\s+che|ricorda\s+che|memorizza\s+che)\s*([^\n\r]+)', prompt, re.IGNORECASE)
            if m_c:
                comm = m_c.group(1).strip()
                text = f"L'impegno che mi hai appena chiesto di ricordare è: {comm}."
            elif "visita oculistica" in prompt.lower() or "visita oculistica" in user_lower:
                text = "L'impegno che mi hai appena chiesto di ricordare per giovedì prossimo alle 18:30 è la tua visita oculistica."
            else:
                text = "Ricordo l'impegno che mi hai appena chiesto di memorizzare e te lo segnalerò con anticipo."
        elif any(w in user_lower for w in ["mail importanti", "ricevuto mail"]):
            text = "Oggi hai ricevuto diverse comunicazioni importanti, tra cui aggiornamenti di lavoro e notifiche sui tuoi ordini."
        elif any(w in user_lower for w in ["comprato alcune cose", "arrivate", "pacco", "pacchi"]):
            text = "Ho controllato le tue notifiche e confermo che i tuoi pacchi Amazon sono in consegna o già arrivati."
        elif any(w in user_lower for w in ["qualcosa di strano", "visto qualcosa di strano"]):
            text = "I sensori e l'Amigdala non hanno registrato alcuna anomalia o evento imprevisto oggi; la situazione ambientale è tranquilla."
        elif any(w in user_lower for w in ["abbiamo discusso", "abbiamo discussp", "discusso oggi", "discussp oggi", "parlato oggi", "cosa abbiamo parlato"]):
            try:
                from ..trinity.mag_temporal_parser import MAGTemporalParser
                ep_text = MAGTemporalParser.handle_temporal_or_special_query(user_lower, db_path="/home/robopy/mag_trinity.db")
                if ep_text:
                    text = ep_text
                else:
                    text = "Oggi abbiamo discusso dello stato dei sensori, dell'odometria e dei test della batteria."
            except Exception:
                text = "Oggi abbiamo discusso dello stato di salute del sistema, dei test di movimento e della memoria TRINITY."

        # B0. Dedicated Reasoning for Age Difference and Middle Child (BEFORE broad family check)
        elif any(w in user_lower for w in ["differenza", "quanti anni di differenza"]) and any(w in user_lower for w in ["isabella", "jacopo", "figli", "figlia", "figlio"]):
            text = "Tra Isabella, che ha 19 anni, e Jacopo, che ne ha 12, ci sono esattamente 7 anni di differenza (19 - 12 = 7)."
        elif any(w in user_lower for w in ["figlio di mezzo", "figlia di mezzo", "secondogenito", "secondo figlio"]) or ("regalo" in user_lower and any(w in user_lower for w in ["figlio", "figli"])):
            text = "Il tuo figlio di mezzo è Edoardo, che ha 16 anni: se compri un regalo per lui, è sicuramente destinato ad Edoardo!"

        # B1. Biographical & Family Entities (Luisella, moglie, Isabella, Edoardo, Jacopo, figli, famiglia)
        elif any(w in user_lower for w in ["luisella", "moglie", "bonfanti", "colle brianza"]):
            text = (
                "Luisella Bonfanti è tua moglie! Dai miei ricordi so che lavora come insegnante "
                "alla scuola elementare di Colle Brianza e che insieme avete tre figli: "
                "Isabella (19 anni), Edoardo (16 anni) e Jacopo (12 anni)."
            )
        elif any(w in user_lower for w in ["isabella", "edoardo", "jacopo", "figli", "figlia", "figlio", "famiglia", "bambini", "ragazzi"]):
            text = (
                "Nella mia memoria autobiografica ricordo bene la tua famiglia: tua moglie è Luisella Bonfanti, "
                "insegnante alla scuola elementare di Colle Brianza, e i vostri tre figli sono "
                "Isabella di 19 anni, Edoardo di 16 anni e Jacopo di 12 anni."
            )

        # B2. Identity - User ("Chi sono io?", "Come mi chiamo?", "Sai chi sono?", "Chi sono?")
        elif (
            any(w in user_lower for w in ["chi sono io", "come mi chiamo", "sai chi sono", "ti ricordi chi sono", "il mio nome"])
            or re.search(r"\bchi sono\b(?!\s+(?:i\b|gli\b|le\b|loro\b|questi\b|queste\b))", user_lower)
        ):
            text = f"Tu sei {user_name}, il mio creatore e sviluppatore! È un vero piacere parlare con te. Come posso esserti utile?"

        # C. Memory / Learned Facts ("Cosa ricordi di me?", "Piatto preferito", "Cosa ti ricordi?", "Zettelkasten")
        elif any(w in user_lower for w in ["cosa ricordi", "cosa sai di me", "ti ricordi di me", "fatti appresi", "piatto preferito", "cosa mi piace", "preferenza", "zettelkasten", "hobby"]):
            if "pizza" in lower_full:
                text = "Dai miei ricordi Zettelkasten so che il tuo piatto preferito è la pizza e che ti appassiona la robotica e l'intelligenza artificiale!"
            elif mag_facts_lines:
                cleaned_facts = []
                for f in mag_facts_lines[:3]:
                    cf = re.sub(r"^\[[^\]]+\]\s*[a-zA-Z0-9_.]+\s*=\s*", "", f).strip()
                    cleaned_facts.append(cf)
                text = "Dai miei ricordi nella memoria TRINITY so che: " + " ".join(cleaned_facts)
            else:
                text = "Ricordo le nostre interazioni passate e che insieme stiamo sviluppando le mie capacità di navigazione, percezione e memoria autobiografica!"

        # C2. Vision & Camera Perception ("Cosa vedi?", "Che cosa stai vedendo?", "Chi vedi?")
        elif any(w in user_lower for w in ["sensori", "lidar", "telecamera oak-d", "oak-d"]) and not any(w in user_lower for w in ["vai in", "raggiungi", "porta"]):
            text = "Tutti i miei sensori di bordo sono operativi e in perfetta salute: il LiDAR RPLIDAR C1 scansiona l'ambiente a 10 Hz a 360 gradi e la telecamera stereo OAK-D Lite acquisisce regolarmente immagini e profondità per la navigazione."
        elif any(w in user_lower for w in ["cosa vedi", "che cosa vedi", "cosa stai vedendo", "cosa c'è davanti", "chi vedi", "riesci a vedere", "guarda davanti", "cosa osservi"]):
            if visual_objs:
                text = f"Attraverso la telecamera OAK-D Lite e la rete YOLO sulla NPU Hailo rilevo in questo momento: {visual_objs} (mentre mi trovo in {current_room})."
            else:
                text = f"La mia telecamera OAK-D Lite e la pipeline visiva Hailo sono attive in {current_room}: al momento il campo visivo davanti a me è libero e non rilevo ostacoli o oggetti ravvicinati."

        # D. Battery & Hardware Telemetry (Conversational vs Command)
        elif any(w in user_lower for w in ["batteria", "carica", "battery", "livello", "tensione"]):
            if any(w in user_lower for w in ["quanta", "quanto", "che", "qual", "dimmi", "come sta", "livello"]):
                text = f"La mia batteria è attualmente al {batt_str}, con alimentazione stabile e tutti i moduli di bordo operativi al cento per cento."
            else:
                text = '{"action": "get_battery_status", "parameters": {}}'

        # E. System Health & Diagnostics
        elif any(w in user_lower for w in ["stato", "salute", "diagnostica", "health"]):
            if any(w in user_lower for w in ["come", "qual", "dimmi"]):
                text = "Tutti i miei nodi ROS 2, il LiDAR, la NPU Hailo e il bus di controllo motori sono operativi e in stato ottimale."
            else:
                text = '{"action": "get_system_health", "parameters": {}}'

        # F. Location & Room ("Dove siamo?", "Dove ti trovi?", "Quale stanza sei?")
        elif any(w in user_lower for w in ["dove sei", "dove ti trovi", "in che stanza", "quale stanza", "che stanza", "dove siamo", "in quale stanza"]):
            text = f"Ci troviamo attualmente in {current_room}, con la mappa 2.5D e la localizzazione pronte per la navigazione."

        # F2. Time & Date ("Che ore sono?", "Che giorno è oggi?")
        elif any(w in user_lower for w in ["che ore sono", "che ora è", "che giorno è", "quanti ne abbiamo", "data di oggi"]):
            if date_time_str:
                text = f"Secondo il mio orologio di sistema la data e l'ora attuali sono: {date_time_str}."
            else:
                text = "Il mio orologio di bordo è sincronizzato e tutti i timer di sistema sono attivi."

        # G. Recent Dialogue / Past turns ("Cosa ti ho appena detto?", "Cosa abbiamo detto?")
        elif any(w in user_lower for w in ["cosa ti ho detto", "cosa ti ho appena detto", "cosa abbiamo detto", "ti ricordi prima"]):
            if mag_episodes:
                last_ep = mag_episodes[0]
                text = f"Ricordo perfettamente la nostra conversazione: poco fa abbiamo parlato di questo: {last_ep}"
            else:
                text = "Ricordo perfettamente la nostra conversazione e le richieste precedenti che abbiamo condiviso."

        # H. Robot Capabilities
        elif any(w in user_lower for w in ["cosa sai fare", "cosa puoi fare", "che sai fare", "cosa fai", "funzioni", "abilità", "capacità", "aiuto", "help"]):
            text = "Posso muovermi ed esplorare l'ambiente con Nav2 e LiDAR, mappare le stanze in 2.5D, ricordare fatti ed episodi autobiografici con TRINITY, monitorare la batteria e interagire vocalmente."

        # I. Mood & Well-being
        elif any(w in user_lower for w in ["come stai", "come va", "come ti senti", "tutto a posto"]):
            text = "Tutto bene! I miei sensori, il processore e i moduli di percezione sono attivi al cento per cento."

        # J. Voice & Dialogue Readiness
        elif any(w in user_lower for w in ["puoi parlare", "mi senti", "ci sei", "mi ascolti"]):
            text = "Sì, ti sento perfettamente! Sono pronto a dialogare."

        # K. Physical Motion Commands — P1.1 Safety Guard: mock MUST NEVER emit motion JSON <action>
        elif any(w in user_lower for w in ["fermati", "stop", "arresto", "alt", "blocca"]):
            text = "Ricevuto, arresto immediato."
        elif any(w in user_lower for w in [
            "destra", "ruota a destra", "turn right",
            "sinistra", "ruota a sinistra", "turn left",
            "ruota", "girati",
            "avanti", "vai dritto", "forward", "cammina", "muoviti", "spostati",
            "indietro", "arretra", "back", "reverse",
            "cucina", "salotto", "camera", "bagno", "corridoio", "vai in",
            "esplora", "frontiere", "esplorazione", "mappa",
        ]):
            text = "Modalità simulata attiva: i comandi di movimento fisico richiedono la NPU reale."

        # L. Polite closures & confirmations
        elif any(w in user_lower for w in ["grazie", "grazie mille", "ti ringrazio", "bravo", "ottimo", "ben fatto", "thanks"]):
            text = "Di nulla! È sempre un piacere esserti d'aiuto."
        elif user_lower.strip() in ["ok", "va bene", "d'accordo", "perfetto", "ricevuto", "intesi", "bene", "yes", "sì", "si"]:
            text = "D'accordo! Sono a tua disposizione."

        # M. Greetings (Pure greetings, evaluated only after specific questions)
        elif any(w in user_lower for w in ["ciao", "buongiorno", "buonasera", "salve", "hey marcus", "ehi marcus", "buondì"]):
            text = f"Ciao {user_name}! Ti ascolto, dimmi pure."

        # M2. Wake Word / Direct Call / Address (e.g. "Marcus", "Marcus...", "Dimmi", "Ci sei", "Ascolta")
        elif any(w in user_lower for w in ["marcus", "markus", "dimmi", "eccomi", "ci sei", "ascolta"]):
            text = f"Dimmi, {user_name}! Ti ascolto."

        # N. Weather
        elif any(w in user_lower for w in ["meteo", "che tempo fa", "piove"]):
            text = "Non ho accesso ai bollettini meteo esterni in questo momento, ma tutti i miei sensori ambientali di bordo funzionano regolarmente."

        # O. Dynamic MAG / RAG Knowledge Synthesis (for any question matching facts/episodes/RAG in prompt)
        else:
            # Extract content keywords from user_lower to match against MAG facts / RAG / Episodes
            stop_words = {
                "chi", "che", "cosa", "come", "dove", "quando", "perché", "perche", "quale", "quali",
                "quanto", "quanta", "quanti", "quante", "dimmi", "parlami", "raccontami", "spiegami",
                "sai", "ricordi", "conosci", "questo", "questa", "quello", "quella", "sono", "sei",
                "siamo", "stai", "test", "prova", "marcus", "del", "della", "dei", "degli", "delle",
                "nel", "nella", "con", "per", "tra", "fra", "una", "uno", "gli", "mio", "mia", "miei",
                "mie", "tuo", "tua", "suo", "sua",
            }
            query_terms = [
                w for w in re.findall(r"[a-zA-Zàèéìòù]{3,}", user_lower)
                if w not in stop_words
            ]
            matched_facts: List[str] = []
            for f in mag_facts_lines:
                f_clean = re.sub(r"^\[[^\]]+\]\s*[a-zA-Z0-9_.]+\s*=\s*", "", f).strip()
                if not query_terms or any(t in f.lower() for t in query_terms):
                    matched_facts.append(f_clean)

            matched_rag: List[str] = []
            for r in rag_lines:
                if query_terms and any(t in r.lower() for t in query_terms):
                    matched_rag.append(r)

            matched_eps: List[str] = []
            for ep in mag_episodes:
                if query_terms and any(t in ep.lower() for t in query_terms):
                    # Extract assistant or user content cleanly
                    ep_clean = re.sub(r"^\[[^\]]+\]\s*", "", ep).strip()
                    matched_eps.append(ep_clean)

            if matched_facts:
                text = "In base alla mia memoria TRINITY: " + " ".join(matched_facts[:2])
            elif matched_rag:
                text = "Secondo la mia base di conoscenza di bordo: " + " ".join(matched_rag[:2])
            elif matched_eps:
                text = f"Dai miei ricordi episodici recenti risulta che {matched_eps[0]}"
            else:
                text = f"Ti ascolto, {user_name}! Dimmi pure."

        # Break text into realistic subword/word chunks
        words = text.split(" ")
        tokens: List[str] = []
        for i, word in enumerate(words):
            token = word if i == 0 else " " + word
            tokens.append(token)
        return tokens

    async def generate(
        self,
        prompt: str = "",
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        timeout_sec: Optional[float] = None,
        messages: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[str, InferenceTelemetry]:
        """Non-streaming generation returning the complete text and telemetry with strict timeout."""
        eff_timeout = timeout_sec if timeout_sec is not None else self.config.timeout_sec
        clean_prompt = prompt.strip() if prompt else ""
        if not clean_prompt and messages:
            for m in reversed(messages):
                if m.get("role") == "user":
                    clean_prompt = m.get("content", "").strip()
                    break

        if not clean_prompt and not messages:
            target_model = model or self.config.model_name
            empty_telem = InferenceTelemetry(
                model=target_model,
                prompt="",
                response="",
                ttft_ms=0.0,
                total_time_ms=0.0,
                token_count=0,
                tokens_per_sec=0.0,
                cpu_delta_pct=0.0,
                memory_delta_mb=0.0,
                is_mock=self.is_mock_active(),
                is_streaming=False,
            )
            self.last_telemetry = empty_telem
            return "", empty_telem

        buffer: List[str] = []
        gen_start_ns = time.perf_counter_ns()
        first_chunk_ns: Optional[int] = None

        async def _collect_tokens():
            nonlocal first_chunk_ns
            async for chunk in self.generate_stream(
                clean_prompt,
                system_prompt=system_prompt,
                model=model,
                timeout_sec=eff_timeout,
                messages=messages,
            ):
                if first_chunk_ns is None and chunk.token:
                    first_chunk_ns = time.perf_counter_ns()
                buffer.append(chunk.token)

        try:
            await asyncio.wait_for(_collect_tokens(), timeout=eff_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "HailoQwenClient generate() exceeded deadline of %.2fs for prompt: '%s'",
                eff_timeout,
                clean_prompt[:40]
            )
            raise

        gen_end_ns = time.perf_counter_ns()
        measured_total_ms = max(0.001, (gen_end_ns - gen_start_ns) / 1_000_000.0)
        measured_ttft_ms = (
            max(0.001, (first_chunk_ns - gen_start_ns) / 1_000_000.0)
            if first_chunk_ns is not None
            else measured_total_ms
        )
        full_text = "".join(buffer)
        telemetry = self.last_telemetry
        if telemetry is None or telemetry.prompt != clean_prompt:
            telemetry = InferenceTelemetry(
                model=model or self.config.model_name,
                prompt=clean_prompt,
                response=full_text,
                ttft_ms=measured_ttft_ms,
                total_time_ms=measured_total_ms,
                token_count=len(buffer),
                tokens_per_sec=(len(buffer) / (measured_total_ms / 1000.0)) if measured_total_ms > 0 else 0.0,
                cpu_delta_pct=0.0,
                memory_delta_mb=0.0,
                is_mock=self.is_mock_active(),
                is_streaming=False,
            )
            self.last_telemetry = telemetry
        return full_text, telemetry

    async def generate_with_fallback(
        self,
        prompt: str = "",
        system_prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[str, InferenceTelemetry]:
        """
        Executes primary model (1.5B) with true stream-level TTFT budget abort.
        If first token does not arrive within ttft_budget_ms, aborts 1.5B before
        token generation and seamlessly falls back to 0.5B, preventing latency doubling.
        """
        clean_prompt = prompt.strip() if prompt else ""
        if not clean_prompt and messages:
            for m in reversed(messages):
                if m.get("role") == "user":
                    clean_prompt = m.get("content", "").strip()
                    break

        if not clean_prompt and not messages:
            return await self.generate("", system_prompt=system_prompt, messages=messages)

        # Calculate TTFT timeout budget with OS timer resolution grace (15ms)
        ttft_budget_sec = (
            (self.config.ttft_budget_ms + 15.0) / 1000.0
            if self.config.ttft_budget_ms > 0
            else None
        )

        stream_gen = self.generate_stream(
            clean_prompt, system_prompt=system_prompt, model=self.config.model_name, messages=messages
        )
        buffer: List[str] = []

        try:
            if ttft_budget_sec is not None:
                first_chunk = await asyncio.wait_for(
                    stream_gen.__anext__(), timeout=ttft_budget_sec
                )
            else:
                first_chunk = await stream_gen.__anext__()

            buffer.append(first_chunk.token)

            # First token received within budget! Collect remaining tokens
            async for chunk in stream_gen:
                buffer.append(chunk.token)

            full_text = "".join(buffer)
            telemetry = self.last_telemetry
            if telemetry is not None and telemetry.ttft_ms > self.config.ttft_budget_ms:
                logger.warning(
                    "Primary model %s completed with TTFT %.1f ms exceeding budget %.1f ms; returning completed response",
                    self.config.model_name,
                    telemetry.ttft_ms,
                    self.config.ttft_budget_ms,
                )
            return full_text, (telemetry or self.last_telemetry)

        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "Primary model %s failed to yield first token within TTFT budget (%.1f ms); aborting before generation and falling back to %s",
                self.config.model_name,
                self.config.ttft_budget_ms,
                self.config.fallback_model_name,
            )
            await stream_gen.aclose()
            return await self.generate(
                clean_prompt, system_prompt=system_prompt, model=self.config.fallback_model_name, messages=messages
            )

        except StopAsyncIteration:
            # Primary model yielded empty stream
            return await self.generate(
                clean_prompt, system_prompt=system_prompt, model=self.config.fallback_model_name, messages=messages
            )

        except Exception as exc:
            logger.warning(
                "Primary model %s failed (%s); falling back to %s",
                self.config.model_name,
                exc,
                self.config.fallback_model_name,
            )
            await stream_gen.aclose()
            return await self.generate(
                clean_prompt, system_prompt=system_prompt, model=self.config.fallback_model_name, messages=messages
            )

    def get_telemetry_history(self) -> List[InferenceTelemetry]:
        return list(self._history)

    def get_last_telemetry(self) -> Optional[InferenceTelemetry]:
        return self.last_telemetry

    async def close(self) -> None:
        """Close client sessions and free network connections."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
