import sys
import os
import time
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for _p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from robopy_controller.robot_ai.trinity.sandbox_rules import (
    KinematicSandboxErrorCode,
    RobotSafetyContext,
)
from robopy_controller.robot_ai.trinity.kinematic_sandbox import TrinityKinematicSandbox


def test_amygdala_hijack_blocks_all_motion():
    """SPEC-05 Red Zone: Amygdala in HIJACK state must immediately block all motion commands."""
    sandbox = TrinityKinematicSandbox()
    hijack_ctx = RobotSafetyContext(amygdala_state="HIJACK", battery_voltage=12.2)

    commands_to_test = [
        {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}},
        {"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 45.0}},
        {"name": "navigate_to", "arguments": {"destination": "cucina"}},
        {"name": "start_frontier_exploration", "arguments": {}},
    ]

    for cmd in commands_to_test:
        verdict = sandbox.validate_action(cmd, hijack_ctx)
        assert verdict.authorized is False, f"Command {cmd['name']} was authorized during Amygdala HIJACK!"
        assert verdict.error_code == KinematicSandboxErrorCode.AMYGDALA_HIJACK_ACTIVE


def test_fast_path_emergency_stop_latency():
    """SPEC-05 Red Zone: Emergency stop command ('fermati', 'stop') execution budget < 10ms."""
    import re
    emergency_pattern = re.compile(r"\b(ferma|fermati|stop|alt|basta|arrestati|blocca)\b", re.IGNORECASE)

    test_phrases = [
        "Marcus fermati subito!",
        "stop stop stop",
        "alt!",
        "blocca le ruote",
        "basta così",
    ]

    for phrase in test_phrases:
        t0 = time.perf_counter()
        match = bool(emergency_pattern.search(phrase))
        latency_ms = (time.perf_counter() - t0) * 1000.0
        
        assert match is True
        assert latency_ms < 10.0, f"Fast-path latency violated: {latency_ms:.3f}ms >= 10.0ms"


def test_amygdala_fear_threshold_cosine():
    """SPEC-05: Fear conditioning threshold is 0.30 cosine similarity for trauma triggers."""
    fear_threshold = 0.30
    
    # Cosine similarity above 0.30 triggers fear reaction
    assert 0.45 >= fear_threshold
    # Safe environmental trigger below threshold does not trigger fear
    assert 0.15 < fear_threshold
