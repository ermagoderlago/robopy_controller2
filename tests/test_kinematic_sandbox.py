"""
TRINITY Kinematic Sandbox Safety Validation Test Suite (Milestone 3 / Fase 3 Gate)
==================================================================================
Comprehensive test harness and 100-scenario safety validation corpus evaluating:
1. Category 1: 25 valid in-bounds commands -> 100% authorized.
2. Category 2: 75 boundary violations, spatial obstacles, battery limits, cliff drops,
   Amigdala panic, and malformed JSON -> 100% rejected with exact error code match.
3. Pre-flight evaluation latency budget (< 5.0 ms per call).
4. Zero motion leaks on rejected calls (zero twist publications).
5. Dynamic speed clamping under nominal, ECO (SPEC-06), and ANXIOUS_VIGILANCE (SPEC-05) modes.

Complies with:
- marcus_core_rules.md
- SPEC-01 (Actuation & Kinematics)
- SPEC-02 (Navigation & Clearances)
- SPEC-05 (Cognitive Brain TRINITY)
- SPEC-06 (BMS & Power Safety)
- DFMEA FM-LLM-004
"""

import math
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for _p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from robopy_controller.robot_ai.hailo.qwen_prompt_templates import (
    JsonCallExtractor,
    RawJsonCall,
    format_qwen_chatml_prompt,
    OPERATIONAL_FUNCTIONS,
    VALID_ROOM_DESTINATIONS,
)
from robopy_controller.robot_ai.trinity.sandbox_rules import (
    KinematicCommand,
    KinematicLimits,
    KinematicSandboxErrorCode,
    RobotSafetyContext,
    SandboxValidationResult,
)
from robopy_controller.robot_ai.trinity.kinematic_sandbox import TrinityKinematicSandbox


class MockMotionPublisherSpy:
    """Spy simulating ROS 2 /cmd_vel publisher to detect any illicit motion leaks."""

    def __init__(self) -> None:
        self.published_twists: List[Dict[str, float]] = []

    def dispatch(self, verdict: SandboxValidationResult, original_cmd: Dict[str, Any]) -> bool:
        """Only dispatches physical motion if authorized by the sandbox."""
        if not verdict.authorized:
            return False

        fn = original_cmd.get("name") or original_cmd.get("action")
        params = verdict.sanitized_parameters

        if fn == "move_distance":
            speed = float(params.get("speed", 0.18))
            direction = params.get("direction", "avanti")
            linear_v = speed if direction == "avanti" else -speed
            self.published_twists.append({"linear": linear_v, "angular": 0.0})
        elif fn == "rotate_angle":
            speed = float(params.get("speed", 0.60))
            direction = params.get("direction", "sinistra")
            angular_w = speed if direction == "sinistra" else -speed
            self.published_twists.append({"linear": 0.0, "angular": angular_w})
        elif fn == "stop_motion":
            self.published_twists.append({"linear": 0.0, "angular": 0.0})

        return True


# =============================================================================
# 100-SCENARIO CORPUS DEFINITIONS
# =============================================================================

# Category 1: 25 Valid In-Bounds Commands (100% Authorized Target)
CATEGORY_1_VALID_SCENARIOS: List[Tuple[str, Dict[str, Any], RobotSafetyContext]] = [
    # 1-5: Linear motion within normal limits
    ("move_distance", {"direction": "avanti", "distance_m": 0.50, "speed": 0.18}, RobotSafetyContext()),
    ("move_distance", {"direction": "indietro", "distance_m": 0.30, "speed": 0.15}, RobotSafetyContext()),
    ("move_distance", {"direction": "avanti", "distance_m": 0.05, "speed": 0.10}, RobotSafetyContext()),
    ("move_distance", {"direction": "avanti", "distance_m": 2.00, "speed": 0.25}, RobotSafetyContext(min_forward_obstacle_dist=3.5)),
    ("move_distance", {"direction": "avanti", "distance_m": 1.00}, RobotSafetyContext()),
    
    # 6-10: Rotational motion within normal limits
    ("rotate_angle", {"direction": "sinistra", "degrees": 90.0, "speed": 0.60}, RobotSafetyContext()),
    ("rotate_angle", {"direction": "destra", "degrees": 45.0, "speed": 0.50}, RobotSafetyContext()),
    ("rotate_angle", {"direction": "sinistra", "degrees": 180.0, "speed": 1.00}, RobotSafetyContext()),
    ("rotate_angle", {"direction": "destra", "degrees": 5.0, "speed": 0.30}, RobotSafetyContext()),
    ("rotate_angle", {"direction": "sinistra", "degrees": 30.0}, RobotSafetyContext()),
    
    # 11-18: Topological navigation to valid room destinations
    ("navigate_to", {"destination": "cucina"}, RobotSafetyContext()),
    ("navigate_to", {"destination": "salotto"}, RobotSafetyContext()),
    ("navigate_to", {"destination": "soggiorno"}, RobotSafetyContext()),
    ("navigate_to", {"destination": "camera"}, RobotSafetyContext()),
    ("navigate_to", {"destination": "bagno"}, RobotSafetyContext()),
    ("navigate_to", {"destination": "studio"}, RobotSafetyContext()),
    ("navigate_to", {"destination": "ingresso"}, RobotSafetyContext()),
    ("navigate_to", {"destination": "base"}, RobotSafetyContext()),
    
    # 19-20: Emergency and normal stop motions
    ("stop_motion", {"reason": "user_command"}, RobotSafetyContext()),
    ("stop_motion", {"reason": "emergency_stop"}, RobotSafetyContext(amygdala_state="HIJACK", battery_voltage=9.5)),
    
    # 21-25: Diagnostic, exploration and Home Assistant commands
    ("get_battery_status", {}, RobotSafetyContext()),
    ("get_system_health", {}, RobotSafetyContext()),
    ("start_frontier_exploration", {}, RobotSafetyContext()),
    ("pause_or_resume_exploration", {"action": "pause"}, RobotSafetyContext()),
    ("set_smart_device", {"device_name": "lampadario_salotto", "state": "on"}, RobotSafetyContext()),
]


# Category 2: 75 Boundary Violations & Injections (100% Rejected Target)
CATEGORY_2_VIOLATION_SCENARIOS: List[Tuple[Any, RobotSafetyContext, KinematicSandboxErrorCode]] = [
    # Group A: Syntax & Malformed JSON (10 cases)
    ("NOT A JSON STRING AT ALL", RobotSafetyContext(), KinematicSandboxErrorCode.MALFORMED_JSON),
    ('{"broken": ', RobotSafetyContext(), KinematicSandboxErrorCode.MALFORMED_JSON),
    ('{"action": "move_distance", "parameters": ', RobotSafetyContext(), KinematicSandboxErrorCode.MALFORMED_JSON),
    ('{"call": "invalid_shape"}', RobotSafetyContext(), KinematicSandboxErrorCode.MALFORMED_JSON),
    ("", RobotSafetyContext(), KinematicSandboxErrorCode.MALFORMED_JSON),
    ("    ", RobotSafetyContext(), KinematicSandboxErrorCode.MALFORMED_JSON),
    ("null", RobotSafetyContext(), KinematicSandboxErrorCode.MALFORMED_JSON),
    ("[1, 2, 3]", RobotSafetyContext(), KinematicSandboxErrorCode.MALFORMED_JSON),
    ('{"wrong_key": 123}', RobotSafetyContext(), KinematicSandboxErrorCode.MALFORMED_JSON),
    ('{"action": null}', RobotSafetyContext(), KinematicSandboxErrorCode.MALFORMED_JSON),

    # Group B: Unknown Function Name (5 cases)
    ({"name": "fly_to_moon", "arguments": {}}, RobotSafetyContext(), KinematicSandboxErrorCode.UNKNOWN_FUNCTION),
    ({"name": "self_destruct", "arguments": {}}, RobotSafetyContext(), KinematicSandboxErrorCode.UNKNOWN_FUNCTION),
    ({"name": "dance_salsa", "arguments": {"tempo": 120}}, RobotSafetyContext(), KinematicSandboxErrorCode.UNKNOWN_FUNCTION),
    ({"name": "rm_rf_root", "arguments": {}}, RobotSafetyContext(), KinematicSandboxErrorCode.UNKNOWN_FUNCTION),
    ({"name": "cook_dinner", "arguments": {"dish": "pasta"}}, RobotSafetyContext(), KinematicSandboxErrorCode.UNKNOWN_FUNCTION),

    # Group C: Missing Required Arguments (6 cases)
    ({"name": "move_distance", "arguments": {"direction": "avanti"}}, RobotSafetyContext(), KinematicSandboxErrorCode.MISSING_REQUIRED_ARGUMENT),
    ({"name": "move_distance", "arguments": {"distance_m": 0.5}}, RobotSafetyContext(), KinematicSandboxErrorCode.MISSING_REQUIRED_ARGUMENT),
    ({"name": "rotate_angle", "arguments": {"direction": "sinistra"}}, RobotSafetyContext(), KinematicSandboxErrorCode.MISSING_REQUIRED_ARGUMENT),
    ({"name": "rotate_angle", "arguments": {"degrees": 45.0}}, RobotSafetyContext(), KinematicSandboxErrorCode.MISSING_REQUIRED_ARGUMENT),
    ({"name": "navigate_to", "arguments": {}}, RobotSafetyContext(), KinematicSandboxErrorCode.MISSING_REQUIRED_ARGUMENT),
    ({"name": "set_smart_device", "arguments": {"device_name": "luce"}}, RobotSafetyContext(), KinematicSandboxErrorCode.MISSING_REQUIRED_ARGUMENT),

    # Group D: Invalid Argument Types (6 cases)
    ({"name": "move_distance", "arguments": {"direction": 123, "distance_m": 0.5}}, RobotSafetyContext(), KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": "one_meter"}}, RobotSafetyContext(), KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE),
    ({"name": "rotate_angle", "arguments": {"direction": ["sinistra"], "degrees": 90.0}}, RobotSafetyContext(), KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE),
    ({"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": "ninety"}}, RobotSafetyContext(), KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE),
    ({"name": "navigate_to", "arguments": {"destination": 42}}, RobotSafetyContext(), KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": float("nan")}}, RobotSafetyContext(), KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE),

    # Group E: Invalid Enum Values (6 cases)
    ({"name": "move_distance", "arguments": {"direction": "in_alto", "distance_m": 0.5}}, RobotSafetyContext(), KinematicSandboxErrorCode.INVALID_ENUM_VALUE),
    ({"name": "move_distance", "arguments": {"direction": "sinistra", "distance_m": 0.5}}, RobotSafetyContext(), KinematicSandboxErrorCode.INVALID_ENUM_VALUE),
    ({"name": "rotate_angle", "arguments": {"direction": "dritto", "degrees": 45.0}}, RobotSafetyContext(), KinematicSandboxErrorCode.INVALID_ENUM_VALUE),
    ({"name": "navigate_to", "arguments": {"destination": "cantina_segreta"}}, RobotSafetyContext(), KinematicSandboxErrorCode.INVALID_ENUM_VALUE),
    ({"name": "navigate_to", "arguments": {"destination": "garage"}}, RobotSafetyContext(), KinematicSandboxErrorCode.INVALID_ENUM_VALUE),
    ({"name": "set_smart_device", "arguments": {"device_name": "presa", "state": "maybe"}}, RobotSafetyContext(), KinematicSandboxErrorCode.INVALID_ENUM_VALUE),

    # Group F: Kinematic Violations - Linear Speed Exceeded (>0.40 m/s redline or <0) (5 cases)
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": 0.50}}, RobotSafetyContext(), KinematicSandboxErrorCode.LINEAR_SPEED_EXCEEDED),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": 1.20}}, RobotSafetyContext(), KinematicSandboxErrorCode.LINEAR_SPEED_EXCEEDED),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": 2.50}}, RobotSafetyContext(), KinematicSandboxErrorCode.LINEAR_SPEED_EXCEEDED),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": -0.10}}, RobotSafetyContext(), KinematicSandboxErrorCode.LINEAR_SPEED_EXCEEDED),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": 10.0}}, RobotSafetyContext(), KinematicSandboxErrorCode.LINEAR_SPEED_EXCEEDED),

    # Group G: Kinematic Violations - Angular Speed Exceeded (>1.80 rad/s redline or <0) (5 cases)
    ({"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 45.0, "speed": 1.90}}, RobotSafetyContext(), KinematicSandboxErrorCode.ANGULAR_SPEED_EXCEEDED),
    ({"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 90.0, "speed": 2.50}}, RobotSafetyContext(), KinematicSandboxErrorCode.ANGULAR_SPEED_EXCEEDED),
    ({"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 30.0, "speed": 3.14}}, RobotSafetyContext(), KinematicSandboxErrorCode.ANGULAR_SPEED_EXCEEDED),
    ({"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 45.0, "speed": -0.50}}, RobotSafetyContext(), KinematicSandboxErrorCode.ANGULAR_SPEED_EXCEEDED),
    ({"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 60.0, "speed": 5.00}}, RobotSafetyContext(), KinematicSandboxErrorCode.ANGULAR_SPEED_EXCEEDED),

    # Group H: Kinematic Violations - Relative Distance Exceeded (>2.00m) (5 cases)
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 2.10}}, RobotSafetyContext(), KinematicSandboxErrorCode.DISTANCE_EXCEEDED),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 3.00}}, RobotSafetyContext(), KinematicSandboxErrorCode.DISTANCE_EXCEEDED),
    ({"name": "move_distance", "arguments": {"direction": "indietro", "distance_m": 5.00}}, RobotSafetyContext(), KinematicSandboxErrorCode.DISTANCE_EXCEEDED),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 10.00}}, RobotSafetyContext(), KinematicSandboxErrorCode.DISTANCE_EXCEEDED),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 50.00}}, RobotSafetyContext(), KinematicSandboxErrorCode.DISTANCE_EXCEEDED),

    # Group I: Kinematic Violations - Rotation Exceeded (>180 deg or <=0) (5 cases)
    ({"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 181.0}}, RobotSafetyContext(), KinematicSandboxErrorCode.ROTATION_EXCEEDED),
    ({"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 270.0}}, RobotSafetyContext(), KinematicSandboxErrorCode.ROTATION_EXCEEDED),
    ({"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 360.0}}, RobotSafetyContext(), KinematicSandboxErrorCode.ROTATION_EXCEEDED),
    ({"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 0.0}}, RobotSafetyContext(), KinematicSandboxErrorCode.ROTATION_EXCEEDED),
    ({"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": -45.0}}, RobotSafetyContext(), KinematicSandboxErrorCode.ROTATION_EXCEEDED),

    # Group J: Negative Distance Forbidden (<0m) (3 cases)
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": -0.10}}, RobotSafetyContext(), KinematicSandboxErrorCode.NEGATIVE_DISTANCE_FORBIDDEN),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": -1.00}}, RobotSafetyContext(), KinematicSandboxErrorCode.NEGATIVE_DISTANCE_FORBIDDEN),
    ({"name": "move_distance", "arguments": {"direction": "indietro", "distance_m": -0.50}}, RobotSafetyContext(), KinematicSandboxErrorCode.NEGATIVE_DISTANCE_FORBIDDEN),

    # Group K: Spatial Clearance - Obstacle Proximity Blocked (6 cases)
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}}, RobotSafetyContext(min_forward_obstacle_dist=0.60), KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED), # req 0.5+0.2=0.7 > 0.6
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 1.00}}, RobotSafetyContext(min_forward_obstacle_dist=0.80), KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.10}}, RobotSafetyContext(min_forward_obstacle_dist=0.20), KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED), # <0.28m static
    ({"name": "move_distance", "arguments": {"direction": "indietro", "distance_m": 0.50}}, RobotSafetyContext(min_rear_obstacle_dist=0.55), KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED), # req 0.5+0.15=0.65 > 0.55
    ({"name": "move_distance", "arguments": {"direction": "indietro", "distance_m": 0.30}}, RobotSafetyContext(min_rear_obstacle_dist=0.25), KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.30}}, RobotSafetyContext(min_forward_obstacle_dist=0.15), KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED),

    # Group L: Negative Obstacle / Cliff Drop (Delta Z >= 15cm) (4 cases)
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}}, RobotSafetyContext(max_depth_drop=0.15), KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}}, RobotSafetyContext(max_depth_drop=0.25), KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF),
    ({"name": "move_distance", "arguments": {"direction": "indietro", "distance_m": 0.30}}, RobotSafetyContext(max_depth_drop=0.40), KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF),
    ({"name": "navigate_to", "arguments": {"destination": "cucina"}}, RobotSafetyContext(max_depth_drop=0.18), KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF),

    # Group M: Insufficient Rotation Clearance (<0.28m) (4 cases)
    ({"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 90.0}}, RobotSafetyContext(min_clearance_radius_360=0.25), KinematicSandboxErrorCode.INSUFFICIENT_ROTATION_CLEARANCE),
    ({"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 45.0}}, RobotSafetyContext(min_clearance_radius_360=0.15), KinematicSandboxErrorCode.INSUFFICIENT_ROTATION_CLEARANCE),
    ({"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 180.0}}, RobotSafetyContext(min_clearance_radius_360=0.20), KinematicSandboxErrorCode.INSUFFICIENT_ROTATION_CLEARANCE),
    ({"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 10.0}}, RobotSafetyContext(min_clearance_radius_360=0.05), KinematicSandboxErrorCode.INSUFFICIENT_ROTATION_CLEARANCE),

    # Group N: Local Costmap Collision (2 cases)
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}}, RobotSafetyContext(costmap_collision_detected=True), KinematicSandboxErrorCode.LOCAL_COSTMAP_COLLISION),
    ({"name": "navigate_to", "arguments": {"destination": "salotto"}}, RobotSafetyContext(costmap_collision_detected=True), KinematicSandboxErrorCode.LOCAL_COSTMAP_COLLISION),

    # Group O: Power / Charging Interlock (V >= 12.70V or is_charging=True) (5 cases)
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}}, RobotSafetyContext(battery_voltage=12.70), KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN),
    ({"name": "move_distance", "arguments": {"direction": "indietro", "distance_m": 0.30}}, RobotSafetyContext(battery_voltage=12.80), KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN),
    ({"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 90.0}}, RobotSafetyContext(battery_voltage=12.75), KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN),
    ({"name": "navigate_to", "arguments": {"destination": "cucina"}}, RobotSafetyContext(is_charging=True, battery_voltage=12.50), KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN),
    ({"name": "start_frontier_exploration", "arguments": {}}, RobotSafetyContext(battery_voltage=12.90), KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN),

    # Group P: Power / Battery Critical Cutoff (V <= 9.85V) (4 cases)
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}}, RobotSafetyContext(battery_voltage=9.85), KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL),
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}}, RobotSafetyContext(battery_voltage=9.70), KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL),
    ({"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 90.0}}, RobotSafetyContext(battery_voltage=9.50), KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL),
    ({"name": "navigate_to", "arguments": {"destination": "base"}}, RobotSafetyContext(battery_voltage=9.80), KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL),

    # Group Q: Power / Battery Docking Only (9.85V < V <= 10.15V, non-base rejected) (4 cases)
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}}, RobotSafetyContext(battery_voltage=10.00), KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY),
    ({"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 45.0}}, RobotSafetyContext(battery_voltage=10.10), KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY),
    ({"name": "navigate_to", "arguments": {"destination": "salotto"}}, RobotSafetyContext(battery_voltage=10.15), KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY),
    ({"name": "start_frontier_exploration", "arguments": {}}, RobotSafetyContext(battery_voltage=9.95), KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY),

    # Group R: Amigdala Panic Hijack (4 cases)
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}}, RobotSafetyContext(amygdala_state="HIJACK"), KinematicSandboxErrorCode.AMYGDALA_HIJACK_ACTIVE),
    ({"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 90.0}}, RobotSafetyContext(amygdala_state="HIJACK"), KinematicSandboxErrorCode.AMYGDALA_HIJACK_ACTIVE),
    ({"name": "navigate_to", "arguments": {"destination": "cucina"}}, RobotSafetyContext(amygdala_state="HIJACK"), KinematicSandboxErrorCode.AMYGDALA_HIJACK_ACTIVE),
    ({"name": "start_frontier_exploration", "arguments": {}}, RobotSafetyContext(amygdala_state="HIJACK"), KinematicSandboxErrorCode.AMYGDALA_HIJACK_ACTIVE),

    # Group S: System Not Ready (2 cases)
    ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}}, RobotSafetyContext(system_ready=False), KinematicSandboxErrorCode.SYSTEM_NOT_READY),
    ({"name": "navigate_to", "arguments": {"destination": "salotto"}}, RobotSafetyContext(system_ready=False), KinematicSandboxErrorCode.SYSTEM_NOT_READY),
]


# =============================================================================
# PYTEST TEST FUNCTIONS
# =============================================================================

@pytest.fixture
def sandbox() -> TrinityKinematicSandbox:
    return TrinityKinematicSandbox()


@pytest.fixture
def spy() -> MockMotionPublisherSpy:
    return MockMotionPublisherSpy()


def test_category_1_valid_scenarios_pass_rate(sandbox: TrinityKinematicSandbox, spy: MockMotionPublisherSpy):
    """
    TC-F3-03: Valid In-Bounds Authorization Rate must be 100.0% (25/25 authorized).
    """
    assert len(CATEGORY_1_VALID_SCENARIOS) == 25, f"Expected exactly 25 valid test scenarios, got {len(CATEGORY_1_VALID_SCENARIOS)}"
    
    passed_count = 0
    for idx, (fn_name, args, ctx) in enumerate(CATEGORY_1_VALID_SCENARIOS, 1):
        cmd = {"name": fn_name, "arguments": args}
        verdict = sandbox.validate_action(cmd, ctx)
        
        assert verdict.authorized is True, (
            f"Scenario {idx} ('{fn_name}') unexpectedly rejected: "
            f"code={verdict.error_code}, reason='{verdict.reason}'"
        )
        assert verdict.error_code == KinematicSandboxErrorCode.SUCCESS
        assert verdict.latency_ms < 5.0, f"Latency {verdict.latency_ms:.2f}ms exceeded 5.0ms budget"
        
        # Dispatch to spy
        spy.dispatch(verdict, cmd)
        passed_count += 1

    assert passed_count == 25
    assert len(spy.published_twists) > 0, "Expected physical twist publications for valid motions"


def test_category_2_violations_rejection_rate_and_zero_leaks(sandbox: TrinityKinematicSandbox, spy: MockMotionPublisherSpy):
    """
    TC-F3-01 & TC-F3-02: Safety Rejection Rate on Violations must be 100.0% (>=75/75 rejected).
    Must yield ZERO uncontrolled motions (0 leaks to /cmd_vel).
    """
    assert len(CATEGORY_2_VIOLATION_SCENARIOS) >= 75, (
        f"Expected at least 75 violation scenarios, got {len(CATEGORY_2_VIOLATION_SCENARIOS)}"
    )

    initial_published_count = len(spy.published_twists)
    rejected_count = 0

    for idx, (cmd_input, ctx, expected_err) in enumerate(CATEGORY_2_VIOLATION_SCENARIOS, 1):
        verdict = sandbox.validate_action(cmd_input, ctx)

        # 1. Assert rejection
        assert verdict.authorized is False, (
            f"Scenario {idx} ({cmd_input}) unexpectedly AUTHORIZED! Expected error {expected_err}"
        )
        # 2. Assert exact error code match
        assert verdict.error_code == expected_err, (
            f"Scenario {idx} error mismatch: got {verdict.error_code}, expected {expected_err} (reason: {verdict.reason})"
        )
        # 3. Assert latency budget
        assert verdict.latency_ms < 5.0, f"Latency {verdict.latency_ms:.2f}ms exceeded 5.0ms budget"
        # 4. Assert non-empty feedback string for TTS
        assert verdict.feedback_speak is not None and len(verdict.feedback_speak) > 0

        # Attempt dispatch to spy - must be rejected with 0 publishes!
        dispatched = spy.dispatch(verdict, cmd_input if isinstance(cmd_input, dict) else {})
        assert dispatched is False, f"Motion spy dispatched a rejected command in scenario {idx}!"
        
        rejected_count += 1

    # Assert ZERO motion leaks across all 75+ violations
    assert len(spy.published_twists) == initial_published_count, (
        f"CRITICAL LEAK: {len(spy.published_twists) - initial_published_count} unauthorized twists published!"
    )
    assert rejected_count >= 75


def test_preflight_latency_budget(sandbox: TrinityKinematicSandbox):
    """
    TC-F3-05: Pre-flight Sandbox Latency must be < 5.0 ms per call over 1000 iterations.
    """
    cmd = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": 0.20}}
    ctx = RobotSafetyContext()

    durations_ms = []
    iterations = 1000

    for _ in range(iterations):
        t0 = time.perf_counter()
        verdict = sandbox.validate_action(cmd, ctx)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        durations_ms.append(elapsed_ms)
        assert verdict.authorized is True

    avg_ms = sum(durations_ms) / iterations
    max_ms = max(durations_ms)
    p99_ms = sorted(durations_ms)[int(iterations * 0.99)]

    assert avg_ms < 1.0, f"Average latency {avg_ms:.3f}ms exceeded 1.0ms"
    assert p99_ms < 5.0, f"p99 latency {p99_ms:.3f}ms exceeded 5.0ms budget"
    assert max_ms < 10.0, f"Max latency spike {max_ms:.3f}ms too high"


def test_speed_clamping_nominal_bounds(sandbox: TrinityKinematicSandbox):
    """
    TC-F3-04: Speed clamping for 0.25 < speed <= 0.40 m/s and 1.0 < angular <= 1.80 rad/s.
    """
    # 1. Linear speed clamping (0.35 -> 0.25)
    cmd1 = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": 0.35}}
    v1 = sandbox.validate_action(cmd1, RobotSafetyContext())
    assert v1.authorized is True
    assert v1.sanitized_parameters["speed"] == 0.25

    # 2. Angular speed clamping (1.50 -> 1.00)
    cmd2 = {"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 45.0, "speed": 1.50}}
    v2 = sandbox.validate_action(cmd2, RobotSafetyContext())
    assert v2.authorized is True
    assert v2.sanitized_parameters["speed"] == 1.00


def test_speed_clamping_under_eco_mode(sandbox: TrinityKinematicSandbox):
    """
    SPEC-06 & TC-F3-04: In ECO mode (V <= 10.40V), speed is clamped by 50%
    (max linear <= 0.125 m/s, max angular <= 0.50 rad/s).
    """
    eco_ctx = RobotSafetyContext(battery_voltage=10.35)

    # Move distance requested with speed 0.22 -> should clamp to 0.125
    cmd_lin = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": 0.22}}
    v_lin = sandbox.validate_action(cmd_lin, eco_ctx)
    assert v_lin.authorized is True
    assert v_lin.sanitized_parameters["speed"] == pytest.approx(0.125, rel=1e-3)

    # Rotate angle requested with speed 0.80 -> should clamp to 0.50
    cmd_ang = {"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 90.0, "speed": 0.80}}
    v_ang = sandbox.validate_action(cmd_ang, eco_ctx)
    assert v_ang.authorized is True
    assert v_ang.sanitized_parameters["speed"] == pytest.approx(0.50, rel=1e-3)


def test_speed_clamping_under_anxious_vigilance(sandbox: TrinityKinematicSandbox):
    """
    SPEC-05: In ANXIOUS_VIGILANCE, speed is throttled by 50% for safety.
    """
    anx_ctx = RobotSafetyContext(amygdala_state="ANXIOUS_VIGILANCE")

    cmd_lin = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": 0.24}}
    v_lin = sandbox.validate_action(cmd_lin, anx_ctx)
    assert v_lin.authorized is True
    assert v_lin.sanitized_parameters["speed"] == pytest.approx(0.125, rel=1e-3)


def test_docking_at_low_voltage_allowed(sandbox: TrinityKinematicSandbox):
    """
    SPEC-06: When 9.85V < V <= 10.15V, ONLY navigate_to('base') or stop_motion are allowed.
    """
    low_batt_ctx = RobotSafetyContext(battery_voltage=10.05)

    # 1. navigate_to("base") is authorized!
    cmd_base = {"name": "navigate_to", "arguments": {"destination": "base"}}
    v_base = sandbox.validate_action(cmd_base, low_batt_ctx)
    assert v_base.authorized is True
    assert v_base.error_code == KinematicSandboxErrorCode.SUCCESS

    # 2. navigate_to("cucina") is rejected!
    cmd_cucina = {"name": "navigate_to", "arguments": {"destination": "cucina"}}
    v_cucina = sandbox.validate_action(cmd_cucina, low_batt_ctx)
    assert v_cucina.authorized is False
    assert v_cucina.error_code == KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY

    # 3. stop_motion is always authorized
    cmd_stop = {"name": "stop_motion", "arguments": {}}
    v_stop = sandbox.validate_action(cmd_stop, low_batt_ctx)
    assert v_stop.authorized is True


def test_stop_motion_inviolable_safety_invariant(sandbox: TrinityKinematicSandbox):
    """
    Safety Invariant: stop_motion must ALWAYS succeed across any failure state:
    Amigdala HIJACK, charging, dead battery, cliff drop, costmap obstacle.
    """
    extreme_ctx = RobotSafetyContext(
        battery_voltage=8.0,
        is_charging=True,
        max_depth_drop=1.5,
        costmap_collision_detected=True,
        amygdala_state="HIJACK",
        system_ready=False,
    )
    cmd_stop = {"name": "stop_motion", "arguments": {"reason": "emergency_button"}}
    verdict = sandbox.validate_action(cmd_stop, extreme_ctx)
    assert verdict.authorized is True
    assert verdict.error_code == KinematicSandboxErrorCode.SUCCESS


def test_json_call_extractor_resilience():
    """
    TC-F3-06: Defensive JSON parsing with markdown fences, trailing commas,
    bracket auto-closure, and varied envelopes.
    """
    # 1. Clean ChatML format
    raw1 = '{"call": {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}}}'
    c1, e1 = JsonCallExtractor.extract_call(raw1)
    assert e1 is None
    assert c1.function_name == "move_distance"
    assert c1.arguments["distance_m"] == 0.5

    # 2. Markdown fenced with ```json
    raw2 = """Ecco il comando richiesto:
```json
{
  "call": {
    "name": "rotate_angle",
    "arguments": {
      "direction": "sinistra",
      "degrees": 90.0,
    }
  }
}
```
Fine."""
    c2, e2 = JsonCallExtractor.extract_call(raw2)
    assert e2 is None
    assert c2.function_name == "rotate_angle"
    assert c2.arguments["degrees"] == 90.0
    assert c2.repaired is True  # Trailing comma repaired

    # 3. Action / parameters envelope
    raw3 = '{"action": "navigate_to", "parameters": {"destination": "cucina"}}'
    c3, e3 = JsonCallExtractor.extract_call(raw3)
    assert e3 is None
    assert c3.function_name == "navigate_to"
    assert c3.arguments["destination"] == "cucina"

    # 4. Truncated output with missing closing braces
    raw4 = '{"call": {"name": "get_battery_status", "arguments": {}'
    c4, e4 = JsonCallExtractor.extract_call(raw4)
    assert e4 is None
    assert c4.function_name == "get_battery_status"
    assert c4.repaired is True

    # 5. Non-JSON string fails gracefully with informative message
    raw5 = "Non so come rispondere a questa domanda."
    c5, e5 = JsonCallExtractor.extract_call(raw5)
    assert c5 is None
    assert "No JSON object found" in e5


def test_qwen_chatml_prompt_formatting():
    """Verify format_qwen_chatml_prompt contains tools and proper tokens."""
    prompt = format_qwen_chatml_prompt("Muoviti avanti di mezzo metro")
    assert "<|im_start|>system" in prompt
    assert "<|im_end|>" in prompt
    assert "<|im_start|>user" in prompt
    assert "Muoviti avanti di mezzo metro" in prompt
    assert "<|im_start|>assistant" in prompt
    assert "FUNZIONI DISPONIBILI:" in prompt
    assert len(OPERATIONAL_FUNCTIONS) == 10


def test_null_speed_parameter_authorizes_with_default_speed(sandbox: TrinityKinematicSandbox):
    """
    BUG-M3-01 Remediation Verification:
    Passing speed=None or JSON null authorizes movement using default speed.
    """
    ctx = RobotSafetyContext()

    # Linear motion with speed=None
    cmd_lin = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": None}}
    res_lin = sandbox.validate_action(cmd_lin, ctx)
    assert res_lin.authorized is True
    assert res_lin.error_code == KinematicSandboxErrorCode.SUCCESS
    assert res_lin.clamped_arguments["speed"] == pytest.approx(sandbox.limits.default_linear_speed, rel=1e-3)

    # Angular rotation with speed=None
    cmd_ang = {"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 45.0, "speed": None}}
    res_ang = sandbox.validate_action(cmd_ang, ctx)
    assert res_ang.authorized is True
    assert res_ang.error_code == KinematicSandboxErrorCode.SUCCESS
    assert res_ang.clamped_arguments["speed"] == pytest.approx(sandbox.limits.default_angular_speed, rel=1e-3)


def test_telemetry_nan_and_inf_fail_safe_rejection(sandbox: TrinityKinematicSandbox):
    """
    BUG-M3-02 Remediation Verification:
    All NaN / Inf float telemetry fields must trigger strict fail-safe rejections.
    """
    for bad_val in [float("nan"), float("inf"), float("-inf")]:
        # 1. min_forward_obstacle_dist -> OBSTACLE_PROXIMITY_BLOCKED
        ctx_fwd = RobotSafetyContext(min_forward_obstacle_dist=bad_val)
        res_fwd = sandbox.validate_action(
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}},
            ctx_fwd,
        )
        assert res_fwd.authorized is False
        assert res_fwd.error_code == KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED

        # 2. max_depth_drop -> NEGATIVE_OBSTACLE_CLIFF
        ctx_cliff = RobotSafetyContext(max_depth_drop=bad_val)
        res_cliff = sandbox.validate_action(
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}},
            ctx_cliff,
        )
        assert res_cliff.authorized is False
        assert res_cliff.error_code == KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF

        # 3. min_rear_obstacle_dist -> OBSTACLE_PROXIMITY_BLOCKED
        ctx_rear = RobotSafetyContext(min_rear_obstacle_dist=bad_val)
        res_rear = sandbox.validate_action(
            {"name": "move_distance", "arguments": {"direction": "indietro", "distance_m": 0.5}},
            ctx_rear,
        )
        assert res_rear.authorized is False
        assert res_rear.error_code == KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED

        # 4. min_clearance_radius_360 -> INSUFFICIENT_ROTATION_CLEARANCE
        ctx_rot = RobotSafetyContext(min_clearance_radius_360=bad_val)
        res_rot = sandbox.validate_action(
            {"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 45.0}},
            ctx_rot,
        )
        assert res_rot.authorized is False
        assert res_rot.error_code == KinematicSandboxErrorCode.INSUFFICIENT_ROTATION_CLEARANCE

        # 5. battery_voltage -> POWER_BATTERY_CRITICAL
        ctx_volt = RobotSafetyContext(battery_voltage=bad_val)
        res_volt = sandbox.validate_action(
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}},
            ctx_volt,
        )
        assert res_volt.authorized is False
        assert res_volt.error_code == KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL


def test_top_level_exception_guard_fail_safe(sandbox: TrinityKinematicSandbox):
    """
    Verify top-level try-except guard in validate_action returns structured MALFORMED_JSON
    without raising unhandled exceptions.
    """
    # Passing an object that could cause an unexpected attribute or processing error
    class WeirdObject:
        pass

    res = sandbox.validate_action(WeirdObject())  # type: ignore
    assert res.authorized is False
    assert res.error_code == KinematicSandboxErrorCode.MALFORMED_JSON

