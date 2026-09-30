#!/usr/bin/env python3
"""
StallSlipDetector - Differential Drive Wheel Stall & Wheel Slip Detector
=======================================================================
Relocated from nomad_reactive_pipeline_node to robopy_controller.robot_corpo.stall_detector.

Detects:
1. Wheel Stall: Commanded linear speed > stall_vel_cmd_thresh (default 0.09 m/s),
   but measured wheel speed < stall_wheel_vel_thresh (default 0.02 m/s) for > stall_duration_sec.
   Immunity: Automatically inhibited during in-place pivot turns (|cmd_w| > 0.15 rad/s with low linear speed).
2. Wheel Slip / Pinned: Commanded speed > stall_vel_cmd_thresh, wheel speed > slip_wheel_vel_thresh,
   but VIO speed < slip_vio_vel_thresh for > slip_duration_sec.
"""

from typing import Tuple


class StallSlipDetector:
    """
    Detects:
    1. Wheel Stall: Commanded linear speed > 0.08 m/s, but wheel speed < 0.02 m/s for > 0.75s.
       Immunity: Ignored during in-place pivot turns (|cmd_w| > 0.15 rad/s with low linear speed).
    2. Wheel Slip / Pinned: Commanded speed > 0.08 m/s, wheel speed > 0.05 m/s, but VIO speed < 0.015 m/s for > 0.60s.
    """
    def __init__(
        self,
        stall_vel_cmd_thresh: float = 0.09,
        stall_wheel_vel_thresh: float = 0.02,
        stall_duration_sec: float = 2.00,
        slip_wheel_vel_thresh: float = 0.05,
        slip_vio_vel_thresh: float = 0.015,
        slip_duration_sec: float = 0.80,
        enable_vio_slip: bool = False
    ):
        self.stall_vel_cmd_thresh = float(stall_vel_cmd_thresh)
        self.stall_wheel_vel_thresh = float(stall_wheel_vel_thresh)
        self.stall_duration_sec = float(stall_duration_sec)
        self.slip_wheel_vel_thresh = float(slip_wheel_vel_thresh)
        self.slip_vio_vel_thresh = float(slip_vio_vel_thresh)
        self.slip_duration_sec = float(slip_duration_sec)
        self.enable_vio_slip = bool(enable_vio_slip)

        self.stall_start_time = None
        self.slip_start_time = None

    def reset(self) -> None:
        """Resets timers."""
        self.stall_start_time = None
        self.slip_start_time = None

    def evaluate(
        self,
        cmd_v: float,
        cmd_w: float,
        wheel_v: float,
        wheel_w: float,
        vio_v: float,
        now_mono: float
    ) -> Tuple[bool, str]:
        """
        Evaluates stall and slip conditions with differential drive pivot turn immunity.
        Returns (is_triggered, trigger_reason).
        """
        abs_cmd = abs(float(cmd_v))
        abs_cmd_w = abs(float(cmd_w))
        abs_wheel = abs(float(wheel_v))
        abs_wheel_w = abs(float(wheel_w))
        abs_vio = abs(float(vio_v))

        # Pivot Turn Immunity: In differential drive kinematics, pure rotations on the spot
        # (|cmd_w| > 0.15 rad/s or |wheel_w| > 0.10 rad/s with low commanded linear speed)
        # naturally produce linear wheel velocity near 0.0 m/s ((v_r + v_l) / 2 = 0).
        # This is expected and must NEVER be flagged as a wheel stall against a wall.
        if (abs_cmd_w > 0.15 and abs_cmd < 0.06) or (abs_wheel_w > 0.10 and abs_cmd < 0.06):
            self.stall_start_time = None
            self.slip_start_time = None
            return False, "NONE"

        # 1. Stall Check (motors blocked against rigid wall/door during forward/backward translation)
        if abs_cmd > self.stall_vel_cmd_thresh and abs_wheel < self.stall_wheel_vel_thresh:
            if self.stall_start_time is None:
                self.stall_start_time = now_mono
            elif (now_mono - self.stall_start_time) >= self.stall_duration_sec:
                return True, "WHEEL_STALL_PINNED"
        else:
            self.stall_start_time = None

        # 2. Slip / Pinned Check (wheels spinning against wall, VIO shows robot is stationary)
        # Only evaluate if VIO slip detection is explicitly enabled and VIO data stream is active
        if self.enable_vio_slip and abs_cmd > self.stall_vel_cmd_thresh and abs_wheel > self.slip_wheel_vel_thresh and abs_vio < self.slip_vio_vel_thresh:
            if self.slip_start_time is None:
                self.slip_start_time = now_mono
            elif (now_mono - self.slip_start_time) >= self.slip_duration_sec:
                return True, "WHEEL_SLIP_ON_WALL"
        else:
            self.slip_start_time = None

        return False, "NONE"
