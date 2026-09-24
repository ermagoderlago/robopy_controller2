#!/usr/bin/env python3
"""
test_nomad_motion_smoothness.py - Unit tests for NOMAD Natural Motion & Smoothness
==================================================================================
Validates:
1. Anti-stiction linear velocity floor (min_linear_speed >= 0.05 m/s).
2. Continuous curvature enforcement (turning radius R >= 0.18 m when moving forward).
3. Acceleration slew-rate limiting (smooth transitions, no abrupt 4 Hz jerk).
4. Angular speed containment (softened to <= 0.70 rad/s).
5. Slew rate memory reset on stop.
"""

import math
import sys
import os
import unittest
from unittest.mock import MagicMock

# Setup lightweight ROS 2 mocks before importing ROS modules
class FakeNode:
    def __init__(self, *args, **kwargs):
        pass

for mod in [
    'rclpy', 'rclpy.node', 'rclpy.qos',
    'sensor_msgs', 'sensor_msgs.msg',
    'nav_msgs', 'nav_msgs.msg',
    'geometry_msgs', 'geometry_msgs.msg',
    'std_msgs', 'std_msgs.msg',
    'cv_bridge'
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

sys.modules['rclpy.node'].Node = FakeNode

import numpy as np

# Create Twist mock with float properties
class FakeTwist:
    class Linear:
        def __init__(self):
            self.x = 0.0
            self.y = 0.0
            self.z = 0.0
    class Angular:
        def __init__(self):
            self.x = 0.0
            self.y = 0.0
            self.z = 0.0
    def __init__(self):
        self.linear = self.Linear()
        self.angular = self.Angular()

sys.modules['geometry_msgs.msg'].Twist = FakeTwist

from robopy_controller.nodes.nomad_reactive_pipeline_node import PurePursuitController
from robopy_controller.nodes.nomad_navigator_node import NomadNavigatorNode


class TestNomadMotionSmoothness(unittest.TestCase):

    def setUp(self):
        self.controller = PurePursuitController(
            lookahead_index=2,
            max_linear_speed=0.22,
            max_angular_speed=0.70,
            k_angular=1.40,
            min_linear_speed=0.05,
            max_linear_accel=0.35,
            max_angular_accel=1.20,
            min_turning_radius=0.18
        )

    def test_anti_stiction_velocity_floor(self):
        """Verify commanded linear velocity never drops below 0.05 m/s when moving."""
        # Sharp 80 degree turn waypoints: cos(80 deg) is approx 0.17
        turn_waypoints = np.array([
            [0.10, 0.50],
            [0.20, 1.00],
            [0.30, 1.50],
            [0.40, 2.00]
        ], dtype=np.float32)

        # First call with no previous history
        cmd = self.controller.compute_cmd_vel(turn_waypoints, current_time=0.0)
        self.assertGreaterEqual(cmd.linear.x, 0.05, "Linear speed must not fall below anti-stiction floor")

        # Test with lower speed override (e.g. 0.06 m/s)
        cmd_override = self.controller.compute_cmd_vel(turn_waypoints, speed_limit_override=0.06, current_time=0.0)
        self.assertGreaterEqual(cmd_override.linear.x, 0.05)

    def test_continuous_curvature_constraint(self):
        """Verify turning radius R = v / |w| >= 0.18m (Marcus chassis radius) during forward progress."""
        # Extreme lateral target at 90 degrees
        sharp_waypoints = np.array([
            [0.05, 0.50],
            [0.10, 1.00],
            [0.15, 1.50]
        ], dtype=np.float32)

        cmd = self.controller.compute_cmd_vel(sharp_waypoints, current_time=0.0)
        v = cmd.linear.x
        w = abs(cmd.angular.z)

        if v > 0.02 and w > 1e-3:
            radius = v / w
            self.assertGreaterEqual(radius, 0.179, f"Turning radius {radius:.3f}m must be >= 0.18m (continuous curvature)")

    def test_angular_velocity_clamping(self):
        """Verify angular velocity never exceeds the softened natural limit (0.70 rad/s)."""
        sharp_waypoints = np.array([
            [0.01, 1.00],
            [0.02, 2.00],
            [0.03, 3.00]
        ], dtype=np.float32)

        cmd = self.controller.compute_cmd_vel(sharp_waypoints, current_time=0.0)
        self.assertLessEqual(abs(cmd.angular.z), 0.701, "Angular velocity must be <= 0.70 rad/s")

    def test_acceleration_slew_rate_limiting(self):
        """Verify step changes in trajectory are ramped smoothly across 250ms (4 Hz) cycles."""
        straight_waypoints = np.array([
            [0.25, 0.0],
            [0.50, 0.0],
            [0.75, 0.0],
            [1.00, 0.0]
        ], dtype=np.float32)

        sharp_left_waypoints = np.array([
            [0.10, 0.50],
            [0.20, 1.00],
            [0.30, 1.50],
            [0.40, 2.00]
        ], dtype=np.float32)

        # Cycle 0 (t = 0.0): moving straight
        cmd_0 = self.controller.compute_cmd_vel(straight_waypoints, current_time=0.0)
        self.assertAlmostEqual(cmd_0.angular.z, 0.0, places=2)

        # Cycle 1 (t = 0.25s, 4 Hz): sudden sharp turn
        dt = 0.25
        cmd_1 = self.controller.compute_cmd_vel(sharp_left_waypoints, current_time=dt)

        # Max allowed change in angular velocity: max_angular_accel * dt = 1.20 * 0.25 = 0.30 rad/s
        dw = abs(cmd_1.angular.z - cmd_0.angular.z)
        self.assertLessEqual(dw, 0.30 + 1e-3, f"Angular velocity change {dw:.3f} rad/s exceeded slew limit of 0.30 rad/s")

    def test_reset_clears_slew_memory(self):
        """Verify reset() zeroes out velocity memory so subsequent motion starts cleanly."""
        waypoints = np.array([[0.25, 0.0], [0.50, 0.0], [0.75, 0.0]], dtype=np.float32)
        self.controller.compute_cmd_vel(waypoints, current_time=0.0)
        self.assertGreater(self.controller.last_v, 0.0)

        self.controller.reset()
        self.assertEqual(self.controller.last_v, 0.0)
        self.assertEqual(self.controller.last_w, 0.0)
        self.assertIsNone(self.controller.last_time)

    def test_empty_waypoints_returns_zero_and_resets(self):
        """Verify passing empty waypoints zeroes output and resets controller."""
        cmd = self.controller.compute_cmd_vel(np.empty((0, 2)))
        self.assertEqual(cmd.linear.x, 0.0)
        self.assertEqual(cmd.angular.z, 0.0)
        self.assertEqual(self.controller.last_v, 0.0)

    def test_nomad_navigator_pure_pursuit_smoothness(self):
        """Verify NomadNavigatorNode._compute_pure_pursuit_cmd enforces minimum speed and curvature."""
        class MockNavigatorHarness:
            def __init__(self):
                self.lookahead_index = 2
                self.max_linear_speed = 0.18
                self.min_linear_speed = 0.05
                self.max_angular_speed = 0.45
                self.max_linear_accel = 0.35
                self.max_angular_accel = 1.20
                self.min_turning_radius = 0.18
                self._last_cmd_v = 0.0
                self._last_cmd_w = 0.0
                self._last_cmd_time = None
                self.compute_cmd = NomadNavigatorNode._compute_pure_pursuit_cmd

        harness = MockNavigatorHarness()
        waypoints = [(0.25, 0.50), (0.50, 1.00), (0.75, 1.50)]

        cmd = harness.compute_cmd(harness, waypoints, current_time=0.0)
        self.assertGreaterEqual(cmd.linear.x, 0.05, "Navigator linear speed must respect anti-stiction floor")
        self.assertLessEqual(abs(cmd.angular.z), 0.451, "Navigator angular speed must not exceed max_angular_speed")

        if cmd.linear.x > 0.02 and abs(cmd.angular.z) > 1e-3:
            radius = cmd.linear.x / abs(cmd.angular.z)
            self.assertGreaterEqual(radius, 0.179, f"Navigator turning radius {radius:.3f}m must be >= 0.18m")


if __name__ == '__main__':
    unittest.main()
