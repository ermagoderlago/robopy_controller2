"""
TRINITY Cognitive Brain & Safety Engine Package
===============================================
Triadic cognitive architecture (RAG, CAG, MAG) alongside synchronous
pre-flight Kinematic Sandbox safety gating.
"""

from __future__ import annotations

import os
import sys

# Ensure robopy_controller is in sys.path so 'robot_ai' subpackages resolve cleanly
_PKG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

try:
    from .trinity_engine import TrinityEngine
except ImportError:
    TrinityEngine = None  # type: ignore

try:
    from .intent_router import IntentRouter, IntentCategory
except ImportError:
    IntentRouter = None  # type: ignore
    IntentCategory = None  # type: ignore

try:
    from .metaprompt_fusion import MetapromptFusion
except ImportError:
    MetapromptFusion = None  # type: ignore

from .kinematic_sandbox import TrinityKinematicSandbox
from .sandbox_rules import (
    KinematicLimits,
    RobotSafetyContext,
    KinematicSandboxErrorCode,
    SandboxValidationResult,
    SandboxVerdict,
    KinematicCommand,
)

__all__ = [
    "TrinityEngine",
    "IntentRouter",
    "IntentCategory",
    "MetapromptFusion",
    "TrinityKinematicSandbox",
    "KinematicLimits",
    "RobotSafetyContext",
    "KinematicSandboxErrorCode",
    "SandboxValidationResult",
    "SandboxVerdict",
    "KinematicCommand",
]
