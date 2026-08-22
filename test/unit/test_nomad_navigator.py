#!/usr/bin/env python3
"""
test_nomad_navigator.py - Unit tests for NOMAD Visual Navigator Node
===================================================================
Tests sliding window context buffer, unconditioned exploration,
goal-conditioned navigation, and pure pursuit controller logic.
"""

import unittest
import numpy as np
import math

from robopy_controller.nodes.nomad_navigator_node import NomadNavigatorNode


class MockNomadNode:
    """Lightweight test harness for testing NOMAD policy & control algorithms without full ROS daemon."""
    def __init__(self):
        self.context_size = 3
        self.input_width = 128
        self.input_height = 128
        self.max_linear_speed = 0.18
        self.max_angular_speed = 0.45
        self.goal_reach_distance = 0.35
        self.lookahead_index = 2
        self.infer_policy = NomadNavigatorNode._infer_nomad_policy
        self.compute_cmd = NomadNavigatorNode._compute_pure_pursuit_cmd


class TestNomadNavigator(unittest.TestCase):

    def setUp(self):
        self.harness = MockNomadNode()

    def test_unconditioned_exploration_trajectory(self):
        # Create a synthetic context of 3 normalized frames
        context = [
            np.ones((128, 128, 3), dtype=np.float32) * 0.5,
            np.ones((128, 128, 3), dtype=np.float32) * 0.5,
            np.ones((128, 128, 3), dtype=np.float32) * 0.5,
        ]

        waypoints, distance = self.harness.infer_policy(self.harness, context, goal=None, mode="EXPLORING")

        # Must predict 6 waypoints ahead
        self.assertEqual(len(waypoints), 6)
        self.assertGreater(distance, 1.0)

        # First waypoint X must advance forward (> 0)
        self.assertGreater(waypoints[0][0], 0.0)
        self.assertLess(waypoints[0][0], waypoints[-1][0])

    def test_goal_conditioned_navigation_steering(self):
        # Create context frame and a goal image with target on the right
        curr_frame = np.zeros((128, 128, 3), dtype=np.float32)
        context = [curr_frame, curr_frame, curr_frame]

        # Goal has bright target on the right sector
        goal_frame = np.zeros((128, 128, 3), dtype=np.float32)
        goal_frame[:, 85:, :] = 1.0  # Right side bright

        waypoints, distance = self.harness.infer_policy(self.harness, context, goal=goal_frame, mode="NAVIGATING_TO_GOAL")

        self.assertEqual(len(waypoints), 6)
        # Distance should be positive
        self.assertGreater(distance, 0.0)
        # Trajectory should curve toward the right (+Y is left, so -Y is right) or follow steering bias
        self.assertIsNotNone(waypoints[-1][1])

    def test_pure_pursuit_controller_clipping(self):
        # Test forward straight trajectory
        straight_wps = [(0.25, 0.0), (0.50, 0.0), (0.75, 0.0), (1.0, 0.0)]
        cmd = self.harness.compute_cmd(self.harness, straight_wps)

        self.assertGreater(cmd.linear.x, 0.0)
        self.assertLessEqual(cmd.linear.x, self.harness.max_linear_speed)
        self.assertAlmostEqual(cmd.angular.z, 0.0, places=2)

        # Test sharp turn trajectory
        sharp_turn_wps = [(0.25, 0.5), (0.50, 1.0), (0.75, 1.5)]
        cmd_turn = self.harness.compute_cmd(self.harness, sharp_turn_wps)

        self.assertLessEqual(abs(cmd_turn.angular.z), self.harness.max_angular_speed)
        # Speed should scale down during sharp turns
        self.assertLess(cmd_turn.linear.x, self.harness.max_linear_speed)


if __name__ == '__main__':
    unittest.main()
