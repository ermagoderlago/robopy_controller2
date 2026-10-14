"""
HybridRouter - Master Hybrid Arbitration Engine
================================================
Coordinates FastPathStopHandler, CognitiveIntentClassifier, and ConnectivityArbiter
to provide a unified, resilient routing entry point:
    route(text: str, context: Optional[dict] = None) -> RoutingDecision

Execution Protocol:
1. Fast-Path Stop Interception (< 10 ms bypass, no LLM):
   Immediately arrests motor motion and interrupts conversation on emergency phrases.
2. Cognitive Intent Classification (< 1.5 ms):
   Determines whether prompt is Operational (Local Qwen2) or Conversational (Gemini Live).
3. Connectivity & Latency Arbiter Check (< 100 ms):
   Fails over to Local Qwen2 if WAN latency > 500 ms or connection drops.

Complies with Master Project Spec (Milestone 2, Fase 2 Test Gate),
marcus_core_rules.md §6.1, SPEC-01, SPEC-04, and SPEC-05.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Any, Dict, Optional

from robopy_controller.robot_ai.arbitration.fast_path_stop import (
    FastPathStopHandler,
    FastPathStopResult,
)
from robopy_controller.robot_ai.arbitration.intent_classifier import (
    CognitiveIntentClassifier,
    RouteTarget,
    RoutingDecision,
)
from robopy_controller.robot_ai.arbitration.connectivity_arbiter import (
    ConnectivityArbiter,
    ConnectivityState,
)

logger = logging.getLogger("marcus.arbitration.hybrid_router")


class HybridRouter:
    """
    Unified hybrid router coordinating fast-path safety stop,
    cognitive intent classification, and WAN connectivity failover.
    """

    def __init__(
        self,
        fast_path_stop: Optional[FastPathStopHandler] = None,
        intent_classifier: Optional[CognitiveIntentClassifier] = None,
        connectivity_arbiter: Optional[ConnectivityArbiter] = None,
        node: Optional[Any] = None,
        enable_logging: bool = False,
    ) -> None:
        self.fast_path_stop = fast_path_stop or FastPathStopHandler(node=node, enable_logging=enable_logging)
        if node is not None and fast_path_stop is not None and fast_path_stop.node is None:
            self.fast_path_stop.attach_node(node)

        self.intent_classifier = intent_classifier or CognitiveIntentClassifier(enable_logging=enable_logging)
        self.connectivity_arbiter = connectivity_arbiter or ConnectivityArbiter(enable_logging=enable_logging)
        self.enable_logging = enable_logging

        # Router telemetry
        self.total_routed: int = 0
        self.fast_path_stop_count: int = 0
        self.local_qwen_count: int = 0
        self.gemini_live_count: int = 0
        self.failover_count: int = 0
        self.total_latency_us: float = 0.0

    def route(
        self,
        text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> RoutingDecision:
        """
        Processes user speech or text and returns the definitive RoutingDecision.
        """
        t0 = time.perf_counter_ns()
        self.total_routed += 1
        raw_text = (text or "").strip()

        # 1. Tier 0: Fast-Path Emergency Stop Check (< 10 ms hardware bypass)
        stop_result = self.fast_path_stop.handle(raw_text)
        if stop_result.triggered:
            elapsed_us = (time.perf_counter_ns() - t0) / 1000.0
            self.fast_path_stop_count += 1
            self.total_latency_us += elapsed_us
            return RoutingDecision(
                destination=RouteTarget.FAST_PATH_STOP,
                confidence=1.0,
                matched_category="emergency_stop",
                reason="fast_path_stop_triggered",
                latency_us=elapsed_us,
                raw_text=raw_text,
            )

        # 2. Tier 1: Cognitive Intent Classification (< 1.5 ms)
        decision = self.intent_classifier.classify(raw_text)

        # 3. Tier 2: Connectivity & Latency Failover Gate
        if decision.destination == RouteTarget.GEMINI_LIVE:
            if self.connectivity_arbiter.should_route_to_local():
                decision.destination = RouteTarget.LOCAL_QWEN
                decision.failover_applied = True
                ema = self.connectivity_arbiter.latency_ema_ms or 0.0
                if ema > self.connectivity_arbiter.LATENCY_DEGRADED_THRESHOLD_MS:
                    decision.reason = "fallback_high_latency"
                else:
                    decision.reason = "fallback_offline"
                self.failover_count += 1
                self.local_qwen_count += 1
            else:
                self.gemini_live_count += 1
        else:
            self.local_qwen_count += 1

        elapsed_us = (time.perf_counter_ns() - t0) / 1000.0
        decision.latency_us = elapsed_us
        self.total_latency_us += elapsed_us

        return decision

    def get_telemetry(self) -> Dict[str, Any]:
        """Returns consolidated router telemetry."""
        avg_us = (self.total_latency_us / self.total_routed) if self.total_routed > 0 else 0.0
        return {
            "total_routed": self.total_routed,
            "fast_path_stop_count": self.fast_path_stop_count,
            "local_qwen_count": self.local_qwen_count,
            "gemini_live_count": self.gemini_live_count,
            "failover_count": self.failover_count,
            "average_latency_us": round(avg_us, 2),
            "connectivity": self.connectivity_arbiter.get_telemetry(),
        }

    def reset_stats(self) -> None:
        """Resets router telemetry."""
        self.total_routed = 0
        self.fast_path_stop_count = 0
        self.local_qwen_count = 0
        self.gemini_live_count = 0
        self.failover_count = 0
        self.total_latency_us = 0.0
