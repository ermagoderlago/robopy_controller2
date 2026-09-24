#!/usr/bin/env python3
"""
Unit Test - Motor Stiction Compensation & Nav2 Motion Fluidity
=============================================================
Validates:
1. Motor driver speed_to_duty mapping and minimum stiction breakout floor.
2. Enforcement of post-trim and post-voltage-scale duty floors (Left >= 0.12, Right >= 0.14).
3. Stiction Breakout Kick activation on standstill-to-motion transitions.
4. Nav2 MPPI continuous curvature and velocity parameters in nav2_params_jazzy.yaml.
"""

import os
import sys
import math
import time
import yaml
import unittest

sys.path.insert(0, os.path.abspath('.'))


class MockWaveshareMotorDriver:
    """Minimal simulation mock of WaveshareMotorDriver logic for unit testing."""
    def __init__(self):
        self.max_linear_speed = 0.40
        self.open_loop_min_duty = 0.12
        self.open_loop_max_duty = 0.28
        self.open_loop_spin_min_duty = 0.18
        self.linear_min_duty_left = 0.12
        self.linear_min_duty_right = 0.14
        self.left_motor_trim = 0.73
        self.left_motor_trim_rev = 0.65
        self.right_motor_trim_rev = 1.25
        self.stiction_kick_duty = 0.18
        self.stiction_kick_duration = 0.12
        self.enable_voltage_feedforward = True
        self.feedforward_nominal_voltage = 11.10
        self.feedforward_min_scale = 0.70
        self.feedforward_max_scale = 1.40
        self.filtered_battery_voltage = 12.60
        self.is_charging_detected = False
        self.enable_esp32_pid = False
        self.is_system_shutdown = False
        self.was_stopped = True
        self.stiction_kick_start_time = 0.0
        self.wheel_separation = 0.285

    def speed_to_duty(self, speed_mps):
        if abs(speed_mps) < 0.003:
            return 0.0
        sign = 1.0 if speed_mps > 0 else -1.0
        clamped_mps = min(abs(speed_mps), self.max_linear_speed)
        ratio = min(clamped_mps / self.max_linear_speed, 1.0)
        duty = self.open_loop_min_duty + (self.open_loop_max_duty - self.open_loop_min_duty) * ratio
        return sign * min(duty, 1.0)

    def compute_duties(self, left_mps, right_mps, cmd_vx=0.0, cmd_wz=0.0, current_time=None):
        if current_time is None:
            current_time = time.time()

        if self.is_system_shutdown or (abs(left_mps) < 0.001 and abs(right_mps) < 0.001):
            self.was_stopped = True
            self.stiction_kick_start_time = 0.0
            return 0.0, 0.0

        if self.was_stopped:
            self.was_stopped = False
            self.stiction_kick_start_time = current_time

        is_kicking = (current_time - self.stiction_kick_start_time) < self.stiction_kick_duration
        is_in_place_spin = (abs(cmd_vx) < 0.02 and abs(cmd_wz) >= 0.05)

        if is_in_place_spin:
            spin_duty = self.open_loop_spin_min_duty + (self.open_loop_max_duty - self.open_loop_spin_min_duty) * min(abs(cmd_wz) / 1.0, 1.0)
            target_duty_left = math.copysign(spin_duty, left_mps)
            target_duty_right = math.copysign(spin_duty, right_mps)
        else:
            target_duty_left = self.speed_to_duty(left_mps)
            target_duty_right = self.speed_to_duty(right_mps)

        # 1. Trims and minimum floors
        if not is_in_place_spin:
            if target_duty_left > 0:
                target_duty_left *= self.left_motor_trim
            else:
                target_duty_left *= self.left_motor_trim_rev

            if target_duty_right < 0:
                target_duty_right *= self.right_motor_trim_rev

            floor_l = self.linear_min_duty_left
            floor_r = self.linear_min_duty_right
            if is_kicking:
                floor_l = max(floor_l, self.stiction_kick_duty * self.left_motor_trim)
                floor_r = max(floor_r, self.stiction_kick_duty)

            if abs(left_mps) >= 0.003 and abs(target_duty_left) < floor_l:
                target_duty_left = math.copysign(floor_l, target_duty_left) if target_duty_left != 0.0 else math.copysign(floor_l, left_mps)
            if abs(right_mps) >= 0.003 and abs(target_duty_right) < floor_r:
                target_duty_right = math.copysign(floor_r, target_duty_right) if target_duty_right != 0.0 else math.copysign(floor_r, right_mps)
        else:
            if abs(target_duty_left) < 0.22:
                target_duty_left = math.copysign(0.22, target_duty_left)
            if abs(target_duty_right) < 0.22:
                target_duty_right = math.copysign(0.22, target_duty_right)

        # Voltage scaling
        v_eff = max(min(self.filtered_battery_voltage, 12.80), 9.00)
        scale = max(min(self.feedforward_nominal_voltage / v_eff, self.feedforward_max_scale), self.feedforward_min_scale)
        duty_l = target_duty_left * scale
        duty_r = target_duty_right * scale

        # Post-scale stiction safety floor
        if not is_in_place_spin:
            if abs(left_mps) >= 0.003 and abs(duty_l) < self.linear_min_duty_left:
                duty_l = math.copysign(self.linear_min_duty_left, duty_l)
            if abs(right_mps) >= 0.003 and abs(duty_r) < self.linear_min_duty_right:
                duty_r = math.copysign(self.linear_min_duty_right, duty_r)
        else:
            if abs(left_mps) >= 0.003 and abs(duty_l) < 0.16:
                duty_l = math.copysign(0.16, duty_l)
            if abs(right_mps) >= 0.003 and abs(duty_r) < 0.18:
                duty_r = math.copysign(0.18, duty_r)

        return duty_l, duty_r


class TestMotorStictionCompensation(unittest.TestCase):
    """Test suite for motor driver stiction and duty floor logic"""

    def setUp(self):
        self.driver = MockWaveshareMotorDriver()

    def test_speed_to_duty_zero_deadband(self):
        """Zero velocity must produce strictly zero duty"""
        self.assertEqual(self.driver.speed_to_duty(0.0), 0.0)
        self.assertEqual(self.driver.speed_to_duty(0.001), 0.0)

    def test_low_speed_linear_duty_floor_post_trim_and_scale(self):
        """Commanding very low speeds (0.02 - 0.08 m/s) must NEVER produce duty below breakout floor"""
        speeds_to_test = [0.02, 0.04, 0.06, 0.08, 0.12]
        # Simulate after stiction kick has expired (t > 0.20s)
        test_time = 100.0
        self.driver.was_stopped = False
        self.driver.stiction_kick_start_time = 90.0  # Expired kick

        for v in speeds_to_test:
            dL, dR = self.driver.compute_duties(v, v, cmd_vx=v, cmd_wz=0.0, current_time=test_time)
            # Left motor must be >= 0.12
            self.assertGreaterEqual(
                abs(dL), 0.12,
                f"Left duty {dL:.4f} is below stiction threshold 0.12 at speed {v} m/s"
            )
            # Right motor must be >= 0.14
            self.assertGreaterEqual(
                abs(dR), 0.14,
                f"Right duty {dR:.4f} is below stiction threshold 0.14 at speed {v} m/s"
            )

    def test_stiction_breakout_kick_on_start(self):
        """Starting from stop must trigger the stiction kick boost during the first 120ms"""
        t0 = 100.0
        self.driver.was_stopped = True  # Robot is stopped
        
        # Immediate start at low speed 0.04 m/s at t = 100.01s (within kick duration)
        dL_kick, dR_kick = self.driver.compute_duties(0.04, 0.04, cmd_vx=0.04, cmd_wz=0.0, current_time=t0 + 0.01)
        expected_scaled_kick = 0.18 * (11.10 / 12.60)
        self.assertGreaterEqual(
            abs(dR_kick), expected_scaled_kick - 1e-3,
            f"Stiction kick not active on right wheel: dR={dR_kick:.4f}"
        )
        self.assertGreaterEqual(
            abs(dL_kick), expected_scaled_kick * 0.73 - 1e-3,
            f"Stiction kick not active on left wheel: dL={dL_kick:.4f}"
        )

        # After kick expires (t = 100.20s > 120ms): duty relaxes to steady-state floor
        dL_steady, dR_steady = self.driver.compute_duties(0.04, 0.04, cmd_vx=0.04, cmd_wz=0.0, current_time=t0 + 0.25)
        self.assertGreaterEqual(abs(dL_steady), 0.12)
        self.assertGreaterEqual(abs(dR_steady), 0.14)
        self.assertLessEqual(abs(dR_steady), abs(dR_kick))

    def test_stop_command_resets_duties(self):
        """Stopping command must return strictly 0.0 duty and reset was_stopped state"""
        self.driver.was_stopped = False
        dL, dR = self.driver.compute_duties(0.0, 0.0, cmd_vx=0.0, cmd_wz=0.0, current_time=200.0)
        self.assertEqual(dL, 0.0)
        self.assertEqual(dR, 0.0)
        self.assertTrue(self.driver.was_stopped)


class TestNav2JazzyMotionParameters(unittest.TestCase):
    """Test suite for nav2_params_jazzy.yaml continuous motion parameters"""

    def setUp(self):
        self.yaml_path = os.path.join('robopy_controller', 'config', 'nav2_params_jazzy.yaml')
        self.assertTrue(os.path.exists(self.yaml_path), f"File {self.yaml_path} not found")
        with open(self.yaml_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

    def test_controller_velocity_thresholds(self):
        """Velocity threshold must not allow micro-crawls below 0.04 m/s"""
        params = self.config['controller_server']['ros__parameters']
        self.assertGreaterEqual(params['min_x_velocity_threshold'], 0.04)

    def test_mppi_continuous_curvature_kinematics(self):
        """MPPI must enforce minimum turning radius to eliminate robotic stop-and-spin in place"""
        mppi = self.config['controller_server']['ros__parameters']['FollowPath']
        self.assertIn('kinematics', mppi)
        radius = mppi['kinematics']['base_min_turning_radius']
        self.assertGreaterEqual(radius, 0.15, "base_min_turning_radius must be >= 0.15m for smooth arc curves")
        self.assertLessEqual(radius, 0.30, "base_min_turning_radius must be <= 0.30m to stay maneuverable")

    def test_mppi_speed_limits_and_sampling(self):
        """MPPI velocity limits must adhere to SPEC-01 Red Zone limits and have proper exploration variance"""
        mppi = self.config['controller_server']['ros__parameters']['FollowPath']
        self.assertLessEqual(mppi['vx_max'], 0.40, "SPEC-01 RED ZONE: max linear speed must be <= 0.40 m/s")
        self.assertLessEqual(mppi['wz_max'], 1.80, "SPEC-01 RED ZONE: max angular speed must be <= 1.80 rad/s")
        self.assertGreaterEqual(mppi['vx_std'], 0.06, "vx_std must be >= 0.06 for effective forward exploration")

    def test_mppi_critics_balance(self):
        """Critics must balance path following against forward progress to eliminate stops"""
        mppi = self.config['controller_server']['ros__parameters']['FollowPath']
        align_weight = mppi['PathAlignCritic']['cost_weight']
        follow_weight = mppi['PathFollowCritic']['cost_weight']
        self.assertLessEqual(align_weight, 10.0, "PathAlignCritic weight must be <= 10.0 to prevent freeze on angular deviation")
        self.assertGreaterEqual(follow_weight, 8.0, "PathFollowCritic weight must be >= 8.0 to prioritize forward progress")


if __name__ == '__main__':
    unittest.main()
