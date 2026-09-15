import threading
from collections import deque
from robot_ai.utils import get_logger

class PercentileTracker:
    def __init__(self, maxlen=100):
        self.values = deque(maxlen=maxlen)

    def add(self, value: float):
        self.values.append(value)

    def percentile(self, p: float) -> float:
        if not self.values:
            return 0.0
        sorted_vals = sorted(self.values)
        k = (len(sorted_vals) - 1) * (p / 100.0)
        f = int(k)
        c = min(f + 1, len(sorted_vals) - 1)
        if f == c:
            return sorted_vals[f]
        return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)

    def max(self) -> float:
        if not self.values:
            return 0.0
        return max(self.values)

class MetricsCollector:
    def __init__(self, node):
        self.node = node
        self._lock = threading.Lock()
        self._metrics = {
            "requests_total": 0, "requests_success": 0, "requests_failed": 0,
            "llm_calls": 0, "llm_errors": 0, "skill_calls": 0, "skill_errors": 0,
            "llm_latency_p50": 0.0, "llm_latency_p95": 0.0,
            "llm_latency_p99": 0.0, "llm_latency_max": 0.0,
        }
        self._latency_tracker = PercentileTracker(maxlen=100)
        self._connectivity_state = "ONLINE"
        self._llm_errors_consecutive = 0
        self._logger = get_logger("metrics")

    def inc_requests_total(self):
        with self._lock:
            self._metrics["requests_total"] += 1

    def inc_requests_success(self):
        with self._lock:
            self._metrics["requests_success"] += 1

    def inc_requests_failed(self):
        with self._lock:
            self._metrics["requests_failed"] += 1

    def record_llm_latency(self, seconds: float):
        with self._lock:
            self._metrics["llm_calls"] += 1
            self._latency_tracker.add(seconds)
            self._update_connectivity_from_latency()
            self._llm_errors_consecutive = 0

    def record_llm_error(self, error_type="unexpected"):
        with self._lock:
            self._metrics["llm_errors"] += 1
            self._llm_errors_consecutive += 1
            if self._llm_errors_consecutive >= 3:
                self._connectivity_state = "OFFLINE"
        self._logger.debug(f"Recorded LLM error of type: {error_type}")

    def _update_connectivity_from_latency(self):
        if len(self._latency_tracker.values) < 3:
            return
        p95 = self._latency_tracker.percentile(95)
        online_thr = 12.0
        degraded_thr = 18.0
        if self._connectivity_state == "ONLINE" and p95 > degraded_thr:
            self._connectivity_state = "DEGRADED"
        elif self._connectivity_state == "DEGRADED" and p95 < online_thr:
            self._connectivity_state = "ONLINE"

    def get_metrics_dict(self):
        with self._lock:
            m = self._metrics.copy()
            m["llm_latency_p50"] = self._latency_tracker.percentile(50)
            m["llm_latency_p95"] = self._latency_tracker.percentile(95)
            m["llm_latency_p99"] = self._latency_tracker.percentile(99)
            m["llm_latency_max"] = self._latency_tracker.max()
            m["connectivity_state"] = self._connectivity_state
            return m

    @property
    def connectivity_state(self):
        with self._lock:
            return self._connectivity_state
