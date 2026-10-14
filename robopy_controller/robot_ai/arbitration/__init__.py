"""
Marcus Hybrid AI Arbitration Package
====================================
Subsystem for fast-path physical safety overrides, cognitive intent classification,
and WAN connectivity arbitration.
"""

from robopy_controller.robot_ai.arbitration.fast_path_stop import (
    FastPathStopHandler,
    FastPathStopResult,
    STOP_REGEX,
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
from robopy_controller.robot_ai.arbitration.hybrid_router import (
    HybridRouter,
)

__all__ = [
    "FastPathStopHandler",
    "FastPathStopResult",
    "STOP_REGEX",
    "CognitiveIntentClassifier",
    "RouteTarget",
    "RoutingDecision",
    "ConnectivityArbiter",
    "ConnectivityState",
    "HybridRouter",
]
