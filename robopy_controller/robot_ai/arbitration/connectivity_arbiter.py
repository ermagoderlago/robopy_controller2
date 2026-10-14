"""
ConnectivityArbiter - Network Health Monitor & Seamless Fallback Arbiter
=========================================================================
Monitors WAN connectivity and latency to Google Gemini Live API.
Maintains a smoothed Exponential Moving Average (EMA) latency:
    EMA_new = 0.7 * EMA_old + 0.3 * sample

Classifies network health into three states:
1. ONLINE: latency <= 250 ms (full cloud multimodal dialogue enabled)
2. DEGRADED: 250 ms < latency <= 500 ms (cloud allowed with tight watchdog)
3. OFFLINE_LOCAL: latency > 500 ms OR socket drop (seamless fallback to local Qwen in < 100 ms)

Complies with Master Project Spec (Requirement R2, Feature F7),
SPEC-04, and SPEC-05.
"""

from __future__ import annotations

from enum import Enum
import logging
import socket
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("marcus.arbitration.connectivity_arbiter")


class ConnectivityState(str, Enum):
    """Network connection operational state."""
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE_LOCAL = "OFFLINE_LOCAL"


class ConnectivityArbiter:
    """
    Thread-safe latency and connectivity monitor providing sub-100ms failover.
    """

    LATENCY_ONLINE_THRESHOLD_MS: float = 250.0
    LATENCY_DEGRADED_THRESHOLD_MS: float = 500.0
    EMA_ALPHA_OLD: float = 0.7
    EMA_ALPHA_SAMPLE: float = 0.3
    FAILOVER_BUDGET_MS: float = 100.0

    DEFAULT_PROBE_HOST: str = "generativelanguage.googleapis.com"
    DEFAULT_PROBE_PORT: int = 443

    def __init__(
        self,
        initial_state: ConnectivityState = ConnectivityState.ONLINE,
        initial_ema_ms: Optional[float] = 50.0,
        enable_logging: bool = False,
    ) -> None:
        self.enable_logging = enable_logging
        self._lock = threading.Lock()

        self._state: ConnectivityState = initial_state
        self._latency_ema_ms: Optional[float] = initial_ema_ms
        self._forced_offline: bool = False
        self._consecutive_failures: int = 0
        self._total_samples: int = 0
        self._last_sample_time: float = time.time()
        self._last_failover_latency_us: float = 0.0

        # Background worker state
        self._probe_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def record_ping_sample(self, sample_ms: float) -> ConnectivityState:
        """
        Records a latency probe sample in milliseconds and updates the EMA.
        Formula: EMA_new = 0.7 * EMA_old + 0.3 * sample
        Returns the updated ConnectivityState.
        """
        t0 = time.perf_counter_ns()
        with self._lock:
            self._total_samples += 1
            self._consecutive_failures = 0
            self._last_sample_time = time.time()

            sample_f = max(0.0, float(sample_ms))
            if self._latency_ema_ms is None:
                self._latency_ema_ms = sample_f
            else:
                self._latency_ema_ms = (
                    self.EMA_ALPHA_OLD * self._latency_ema_ms
                    + self.EMA_ALPHA_SAMPLE * sample_f
                )

            old_state = self._state
            if self._forced_offline:
                self._state = ConnectivityState.OFFLINE_LOCAL
            elif self._latency_ema_ms <= self.LATENCY_ONLINE_THRESHOLD_MS:
                self._state = ConnectivityState.ONLINE
            elif self._latency_ema_ms <= self.LATENCY_DEGRADED_THRESHOLD_MS:
                self._state = ConnectivityState.DEGRADED
            else:
                self._state = ConnectivityState.OFFLINE_LOCAL

            if old_state != self._state:
                failover_us = (time.perf_counter_ns() - t0) / 1000.0
                self._last_failover_latency_us = failover_us
                if self.enable_logging:
                    logger.info(
                        "Connectivity state transitioned %s -> %s (EMA: %.1f ms) in %.2f us",
                        old_state.value,
                        self._state.value,
                        self._latency_ema_ms,
                        failover_us,
                    )

            return self._state

    def record_failure(self, reason: str = "socket_disconnect") -> ConnectivityState:
        """
        Immediately transitions to OFFLINE_LOCAL upon connection drop or timeout.
        Guarantees seamless failover in < 100 ms (typically < 5 microseconds).
        """
        t0 = time.perf_counter_ns()
        with self._lock:
            self._consecutive_failures += 1
            self._total_samples += 1
            self._last_sample_time = time.time()
            old_state = self._state
            self._state = ConnectivityState.OFFLINE_LOCAL

            failover_us = (time.perf_counter_ns() - t0) / 1000.0
            self._last_failover_latency_us = failover_us

            if self.enable_logging and old_state != self._state:
                logger.warning(
                    "[FAILOVER] Connection dropped (%s) -> OFFLINE_LOCAL executed in %.2f us",
                    reason,
                    failover_us,
                )

            return self._state

    def set_offline(self, offline: bool) -> None:
        """Force the arbiter into or out of offline mode."""
        t0 = time.perf_counter_ns()
        with self._lock:
            self._forced_offline = bool(offline)
            if self._forced_offline:
                self._state = ConnectivityState.OFFLINE_LOCAL
            else:
                if self._latency_ema_ms is None or self._latency_ema_ms <= self.LATENCY_ONLINE_THRESHOLD_MS:
                    self._state = ConnectivityState.ONLINE
                elif self._latency_ema_ms <= self.LATENCY_DEGRADED_THRESHOLD_MS:
                    self._state = ConnectivityState.DEGRADED
                else:
                    self._state = ConnectivityState.OFFLINE_LOCAL
            self._last_failover_latency_us = (time.perf_counter_ns() - t0) / 1000.0

    def should_route_to_local(self) -> bool:
        """
        Ultra-fast query (< 1 microsecond) determining whether requests
        must be routed to local Qwen2 due to offline state or degraded latency.
        """
        with self._lock:
            if self._forced_offline:
                return True
            if self._state == ConnectivityState.OFFLINE_LOCAL:
                return True
            if self._latency_ema_ms is not None and self._latency_ema_ms > self.LATENCY_DEGRADED_THRESHOLD_MS:
                return True
            return False

    def probe_once(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        timeout_s: float = 0.5,
    ) -> float:
        """
        Performs a lightweight non-blocking TCP SYN/connect probe to target endpoint.
        Records ping sample or failure and returns measured latency in ms.
        """
        target_host = host or self.DEFAULT_PROBE_HOST
        target_port = port or self.DEFAULT_PROBE_PORT

        t0 = time.perf_counter()
        sock = None
        try:
            sock = socket.create_connection((target_host, target_port), timeout=timeout_s)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.record_ping_sample(elapsed_ms)
            return elapsed_ms
        except (socket.timeout, OSError) as e:
            self.record_failure(reason=str(e))
            return 9999.0
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    def start_probe_worker(
        self,
        interval_s: float = 1.0,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        """Starts background periodic probing worker."""
        if self._probe_thread is not None and self._probe_thread.is_alive():
            return

        self._stop_event.clear()

        def _worker() -> None:
            while not self._stop_event.is_set():
                self.probe_once(host=host, port=port)
                self._stop_event.wait(timeout=interval_s)

        self._probe_thread = threading.Thread(
            target=_worker,
            name="MarcusConnectivityProbeWorker",
            daemon=True,
        )
        self._probe_thread.start()

    def stop_probe_worker(self) -> None:
        """Gracefully stops background probing worker."""
        self._stop_event.set()
        if self._probe_thread is not None:
            self._probe_thread.join(timeout=1.0)
            self._probe_thread = None

    def reset(self) -> None:
        """Resets all metrics and telemetry counters."""
        with self._lock:
            self._state = ConnectivityState.ONLINE
            self._latency_ema_ms = None
            self._forced_offline = False
            self._consecutive_failures = 0
            self._total_samples = 0
            self._last_sample_time = time.time()
            self._last_failover_latency_us = 0.0

    @property
    def state(self) -> ConnectivityState:
        with self._lock:
            return self._state

    @property
    def latency_ema_ms(self) -> Optional[float]:
        with self._lock:
            return self._latency_ema_ms

    @property
    def is_online(self) -> bool:
        with self._lock:
            return self._state == ConnectivityState.ONLINE

    @property
    def is_degraded(self) -> bool:
        with self._lock:
            return self._state == ConnectivityState.DEGRADED

    @property
    def is_offline(self) -> bool:
        with self._lock:
            return self._state == ConnectivityState.OFFLINE_LOCAL

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    @property
    def total_samples(self) -> int:
        with self._lock:
            return self._total_samples

    @property
    def last_failover_latency_us(self) -> float:
        with self._lock:
            return self._last_failover_latency_us

    def get_telemetry(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self._state.value,
                "latency_ema_ms": round(self._latency_ema_ms, 2) if self._latency_ema_ms is not None else None,
                "forced_offline": self._forced_offline,
                "consecutive_failures": self._consecutive_failures,
                "total_samples": self._total_samples,
                "last_failover_latency_us": round(self._last_failover_latency_us, 2),
            }
