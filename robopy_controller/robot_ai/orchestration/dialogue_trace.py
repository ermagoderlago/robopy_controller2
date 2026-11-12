"""Structured JSONL dialogue trace logging and turn telemetry counters (Phase 0).

Thread-safe, lightweight observability for VUI, Gemini Live, Hailo Qwen, TRINITY,
and TTS floor arbitration running in-process inside ``robot_ai_node``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Deque, Dict, List, Optional
import uuid


_DEFAULT_TRACE_RELPATH = os.path.join("logs", "dialogue_trace.jsonl")
_MAX_TRACE_BYTES = 5 * 1024 * 1024  # 5 MB safety cap on Pi 5 SD card
_MAX_DETAIL_STR_LEN = 320


def _sanitize_value(value: Any) -> Any:
    """Truncates long strings and converts non-JSON primitives cleanly."""
    if isinstance(value, str):
        if len(value) > _MAX_DETAIL_STR_LEN:
            return value[: _MAX_DETAIL_STR_LEN - 3] + "..."
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _sanitize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(v) for v in value[:12]]
    return str(value)[:_MAX_DETAIL_STR_LEN]


@dataclass
class DialogueTelemetryCounters:
    """Per-session and per-run observability counters for Marcus dialogue turns."""

    turns_total: int = 0
    double_response_count: int = 0
    double_response_blocked: int = 0
    out_of_window_turns: int = 0
    mock_activations: int = 0
    trinity_timeouts: int = 0
    proactive_suppressed: int = 0
    proactive_spoken: int = 0
    barge_in_events: int = 0
    backchannels_emitted: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def inc(self, name: str, delta: int = 1) -> int:
        """Atomically increments a named counter and returns its new value."""
        with self._lock:
            if not hasattr(self, name) or name.startswith("_"):
                raise ValueError(f"Unknown telemetry counter: {name}")
            new_val = int(getattr(self, name)) + int(delta)
            setattr(self, name, new_val)
            return new_val

    def get(self, name: str) -> int:
        """Returns the current value of a named counter."""
        with self._lock:
            if not hasattr(self, name) or name.startswith("_"):
                raise ValueError(f"Unknown telemetry counter: {name}")
            return int(getattr(self, name))

    def snapshot(self) -> Dict[str, int]:
        """Returns a thread-safe dictionary copy of all counters."""
        with self._lock:
            return {
                "turns_total": self.turns_total,
                "double_response_count": self.double_response_count,
                "double_response_blocked": self.double_response_blocked,
                "out_of_window_turns": self.out_of_window_turns,
                "mock_activations": self.mock_activations,
                "trinity_timeouts": self.trinity_timeouts,
                "proactive_suppressed": self.proactive_suppressed,
                "proactive_spoken": self.proactive_spoken,
                "barge_in_events": self.barge_in_events,
                "backchannels_emitted": self.backchannels_emitted,
            }

    def reset(self) -> None:
        """Resets all telemetry counters to zero."""
        with self._lock:
            self.turns_total = 0
            self.double_response_count = 0
            self.double_response_blocked = 0
            self.out_of_window_turns = 0
            self.mock_activations = 0
            self.trinity_timeouts = 0
            self.proactive_suppressed = 0
            self.proactive_spoken = 0
            self.barge_in_events = 0
            self.backchannels_emitted = 0


_COUNTER_EVENT_MAP: Dict[str, str] = {
    "turn_start": "turns_total",
    "double_response": "double_response_count",
    "floor_denied": "double_response_blocked",
    "out_of_window_drop": "out_of_window_turns",
    "mock_activation": "mock_activations",
    "trinity_timeout": "trinity_timeouts",
    "proactive_suppressed": "proactive_suppressed",
    "proactive_spoken": "proactive_spoken",
    "barge_in": "barge_in_events",
    "backchannel": "backchannels_emitted",
}


class DialogueTraceLogger:
    """Thread-safe JSONL event logger and counter hub correlated by ``turn_id``."""

    def __init__(
        self,
        log_path: Optional[str] = None,
        max_bytes: int = _MAX_TRACE_BYTES,
        ring_capacity: int = 250,
        write_to_disk: bool = True,
    ):
        resolved = log_path or os.environ.get("MARCUS_DIALOGUE_TRACE_PATH") or _DEFAULT_TRACE_RELPATH
        self.log_path = Path(resolved)
        self.max_bytes = max_bytes
        self.write_to_disk = write_to_disk
        self.counters = DialogueTelemetryCounters()
        self._lock = threading.Lock()
        self._seq = 0
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=ring_capacity)

    def new_turn_id(self, prefix: str = "turn") -> str:
        """Generates a monotonic + short-random correlated identifier for one turn."""
        with self._lock:
            self._seq += 1
            seq = self._seq
        short_hex = uuid.uuid4().hex[:6]
        return f"{prefix}-{seq:05d}-{short_hex}"

    def log_event(
        self,
        event: str,
        *,
        turn_id: Optional[str] = None,
        source: str = "system",
        floor: str = "IDLE",
        connectivity: str = "UNKNOWN",
        latency_ms: Optional[float] = None,
        detail: Optional[Dict[str, Any]] = None,
        count_metric: bool = True,
    ) -> Dict[str, Any]:
        """Records a structured dialogue trace event in memory and appends to JSONL."""
        record: Dict[str, Any] = {
            "ts_wall": round(time.time(), 4),
            "ts_mono": round(time.monotonic(), 4),
            "turn_id": turn_id or "no-turn",
            "event": str(event),
            "source": str(source),
            "floor": str(floor),
            "connectivity": str(connectivity),
        }
        if latency_ms is not None:
            record["latency_ms"] = round(float(latency_ms), 2)
        if detail:
            record["detail"] = _sanitize_value(detail)

        if count_metric and event in _COUNTER_EVENT_MAP:
            self.counters.inc(_COUNTER_EVENT_MAP[event], 1)

        with self._lock:
            self._recent.append(record)
            if self.write_to_disk:
                self._append_jsonl_locked(record)

        return record

    def _append_jsonl_locked(self, record: Dict[str, Any]) -> None:
        """Appends one JSON line to disk (caller holds ``self._lock``), rotating on size."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.max_bytes > 0:
                if self.log_path.stat().st_size >= self.max_bytes:
                    rotated = self.log_path.with_suffix(".jsonl.1")
                    if rotated.exists():
                        rotated.unlink()
                    self.log_path.rename(rotated)
            line = json.dumps(record, ensure_ascii=False)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            # Never crash runtime dialogue paths on transient filesystem errors.
            pass

    def recent_events(self, limit: int = 50, turn_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns recent trace records, optionally filtered by ``turn_id``."""
        with self._lock:
            items = list(self._recent)
        if turn_id is not None:
            items = [item for item in items if item.get("turn_id") == turn_id]
        return items[-max(1, int(limit)):]


_GLOBAL_TRACE_LOCK = threading.Lock()
_GLOBAL_TRACE_INSTANCE: Optional[DialogueTraceLogger] = None


def get_dialogue_trace(log_path: Optional[str] = None) -> DialogueTraceLogger:
    """Returns the shared in-process ``DialogueTraceLogger`` singleton."""
    global _GLOBAL_TRACE_INSTANCE
    with _GLOBAL_TRACE_LOCK:
        if _GLOBAL_TRACE_INSTANCE is None:
            _GLOBAL_TRACE_INSTANCE = DialogueTraceLogger(log_path=log_path)
        return _GLOBAL_TRACE_INSTANCE


def reset_dialogue_trace(log_path: Optional[str] = None, write_to_disk: bool = True) -> DialogueTraceLogger:
    """Re-initializes the shared trace singleton (primarily for test isolation)."""
    global _GLOBAL_TRACE_INSTANCE
    with _GLOBAL_TRACE_LOCK:
        _GLOBAL_TRACE_INSTANCE = DialogueTraceLogger(
            log_path=log_path,
            write_to_disk=write_to_disk,
        )
        return _GLOBAL_TRACE_INSTANCE
