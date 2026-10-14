"""
TRINITY Kinematic Sandbox Rules, Limits & Data Models
=====================================================
Defines safety contracts, kinematic thresholds (SPEC-01, SPEC-02, SPEC-06, SPEC-05),
error code taxonomy, and telemetry evaluation context for Marcus Hybrid AI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class KinematicSandboxErrorCode(str, Enum):
    """
    Error Code Taxonomy for Pre-flight Kinematic Sandbox.
    Categorized into 5 validation tiers.
    """
    # Tier 0 & 1: Syntax & Schema
    SUCCESS = "SUCCESS"
    MALFORMED_JSON = "MALFORMED_JSON"
    UNKNOWN_FUNCTION = "UNKNOWN_FUNCTION"
    MISSING_REQUIRED_ARGUMENT = "MISSING_REQUIRED_ARGUMENT"
    INVALID_ARGUMENT_TYPE = "INVALID_ARGUMENT_TYPE"
    INVALID_ENUM_VALUE = "INVALID_ENUM_VALUE"

    # Tier 2: Kinematic Bounds (SPEC-01)
    LINEAR_SPEED_EXCEEDED = "LINEAR_SPEED_EXCEEDED"
    ANGULAR_SPEED_EXCEEDED = "ANGULAR_SPEED_EXCEEDED"
    DISTANCE_EXCEEDED = "DISTANCE_EXCEEDED"
    ROTATION_EXCEEDED = "ROTATION_EXCEEDED"
    NEGATIVE_DISTANCE_FORBIDDEN = "NEGATIVE_DISTANCE_FORBIDDEN"
    DURATION_EXCEEDED = "DURATION_EXCEEDED"

    # Tier 3: Spatial & Obstacle Clearance (SPEC-02)
    OBSTACLE_PROXIMITY_BLOCKED = "OBSTACLE_PROXIMITY_BLOCKED"
    NEGATIVE_OBSTACLE_CLIFF = "NEGATIVE_OBSTACLE_CLIFF"
    INSUFFICIENT_ROTATION_CLEARANCE = "INSUFFICIENT_ROTATION_CLEARANCE"
    LOCAL_COSTMAP_COLLISION = "LOCAL_COSTMAP_COLLISION"

    # Tier 4: Battery & Power Interlocks (SPEC-06)
    POWER_CHARGING_MOTION_FORBIDDEN = "POWER_CHARGING_MOTION_FORBIDDEN"
    POWER_BATTERY_CRITICAL = "POWER_BATTERY_CRITICAL"
    POWER_BATTERY_DOCKING_ONLY = "POWER_BATTERY_DOCKING_ONLY"

    # Tier 5: Cognitive Interlocks (SPEC-05)
    AMYGDALA_HIJACK_ACTIVE = "AMYGDALA_HIJACK_ACTIVE"
    SYSTEM_NOT_READY = "SYSTEM_NOT_READY"


@dataclass(frozen=True)
class KinematicLimits:
    """
    Inviolable and operational kinematic boundaries per SPEC-01, SPEC-02, SPEC-06.
    """
    # Linear speed (m/s)
    max_linear_speed: float = 0.25          # Operational cap (SPEC-01)
    redline_linear_speed: float = 0.40      # Absolute hard cap (SPEC-01 Red Zone)
    min_linear_speed: float = 0.00
    default_linear_speed: float = 0.18

    # Angular speed (rad/s)
    max_angular_speed: float = 1.00         # Operational cap (SPEC-01)
    redline_angular_speed: float = 1.80     # Absolute hard cap (SPEC-01 Red Zone)
    min_angular_speed: float = 0.00
    default_angular_speed: float = 0.60

    # Motion scope limits
    max_relative_distance: float = 2.00     # Single relative step cap (m)
    min_relative_distance: float = 0.05     # Minimum meaningful step (m)
    max_relative_angle_deg: float = 180.0   # Single relative rotation cap (deg)
    min_relative_angle_deg: float = 1.0     # Minimum meaningful rotation (deg)

    # Spatial clearances (m)
    forward_obstacle_buffer: float = 0.20   # d_scan >= d_req + 0.20m (SPEC-02)
    min_forward_clearance: float = 0.28     # Minimum static forward clearance
    rear_obstacle_buffer: float = 0.15      # d_rear >= d_req + 0.15m (SPEC-02)
    min_rotation_clearance_radius: float = 0.28  # In-place 360° spin clearance (SPEC-02)
    cliff_depth_drop_max: float = 0.15      # Negative obstacle cliff drop (Delta Z < 15cm)

    # Battery thresholds (V) (SPEC-06 & FM-NAV-035)
    charging_voltage_threshold: float = 12.70   # Charging / AC power lock
    shutdown_voltage_threshold: float = 9.85    # Emergency cutoff
    docking_voltage_threshold: float = 10.15    # Docking reserve only
    eco_voltage_threshold: float = 10.40        # ECO mode throttle
    eco_speed_scale: float = 0.50               # 50% velocity scaling in ECO mode

    # Cognitive interlocks (SPEC-05)
    anxious_speed_scale: float = 0.50           # 50% velocity scaling under ANXIOUS_VIGILANCE


@dataclass
class RobotSafetyContext:
    """
    Real-time telemetry snapshot provided to the Kinematic Sandbox before pre-flight evaluation.
    """
    battery_voltage: float = 12.00
    is_charging: bool = False
    min_forward_obstacle_dist: float = 3.00
    min_rear_obstacle_dist: float = 3.00
    min_clearance_radius_360: float = 1.00
    max_depth_drop: float = 0.00
    amygdala_state: str = "CALM"  # "CALM" | "ANXIOUS_VIGILANCE" | "HIJACK"
    system_ready: bool = True
    costmap_collision_detected: bool = False
    extra_telemetry: Dict[str, Any] = field(default_factory=dict)

    def effective_is_charging(self, charging_thresh: float = 12.70) -> bool:
        """Determines if the robot is effectively in charging mode."""
        return self.is_charging or (self.battery_voltage >= charging_thresh)


@dataclass
class KinematicCommand:
    """A parsed operational command ready for pre-flight evaluation."""
    function_name: str
    arguments: Dict[str, Any]
    raw_payload: str = ""
    timestamp: float = 0.0


@dataclass
class SandboxValidationResult:
    """
    Immutable authorization verdict returned by TrinityKinematicSandbox.
    """
    authorized: bool
    error_code: KinematicSandboxErrorCode
    reason: str
    clamped_arguments: Optional[Dict[str, Any]] = None
    telemetry_snapshot: Dict[str, Any] = field(default_factory=dict)
    feedback_speak: Optional[str] = None
    latency_ms: float = 0.0

    @property
    def approved(self) -> bool:
        """Alias for compatibility with PROJECT.md SandboxVerdict."""
        return self.authorized

    @property
    def sanitized_parameters(self) -> Dict[str, Any]:
        """Returns clamped/sanitized parameters or empty dict."""
        return self.clamped_arguments if self.clamped_arguments is not None else {}


# Cross-compatibility alias
SandboxVerdict = SandboxValidationResult
