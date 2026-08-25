#!/usr/bin/env python3
"""
Unit Test - Negative Obstacle Detection (FM-NAV-009)
===================================================
Tests that semantic_costmap_injector correctly processes depth images,
detects floor drop-offs (> 15cm), and registers negative obstacles.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath('.'))

# Mock ROS 2 modules if running outside ROS 2 environment
if 'rclpy' not in sys.modules:
    class DummyNode:
        def __init__(self, *args, **kwargs):
            pass

    sys.modules['rclpy'] = MagicMock()
    sys.modules['rclpy.node'] = MagicMock(Node=DummyNode)
    sys.modules['rclpy.qos'] = MagicMock()
    sys.modules['std_msgs'] = MagicMock()
    sys.modules['std_msgs.msg'] = MagicMock()
    sys.modules['sensor_msgs'] = MagicMock()
    sys.modules['sensor_msgs.msg'] = MagicMock()
    sys.modules['geometry_msgs'] = MagicMock()
    sys.modules['geometry_msgs.msg'] = MagicMock()
    sys.modules['sensor_msgs_py'] = MagicMock()
    sys.modules['sensor_msgs_py.point_cloud2'] = MagicMock()
    sys.modules['geometry_msgs.msg'] = MagicMock()
    sys.modules['visualization_msgs.msg'] = MagicMock()
    sys.modules['tf2_ros'] = MagicMock()
    sys.modules['tf2_geometry_msgs'] = MagicMock()

    # Mock custom package messages
    mock_msg = MagicMock()
    sys.modules['robopy_controller.msg'] = mock_msg

import numpy as np


class TestNegativeObstacleAlgorithm(unittest.TestCase):
    def test_depth_array_decoding_and_drop_detection(self):
        """Test array parsing and drop detection logic"""
        height, width = 400, 640
        depth_data = np.ones((height, width), dtype=np.float32) * 1.0
        # Insert drop-off hole (> 15cm drop) in center-lower region
        depth_data[300:399, 280:360] = 3.5

        # Verify array shapes and drop-off thresholding
        drop_mask = depth_data > 1.15
        self.assertTrue(np.any(drop_mask), "Drop-off mask failed to detect drop area")

        drop_rows, drop_cols = np.where(drop_mask)
        self.assertGreater(len(drop_rows), 0, "No drop-off coordinates identified")
        self.assertTrue(np.min(drop_rows) >= 300, "Drop-off row range mismatch")
        self.assertTrue(np.max(drop_cols) <= 360, "Drop-off col range mismatch")

    def test_mock_depth_callback(self):
        """Test callback algorithm logic with mock injector instance"""
        from robopy_controller.nodes.semantic_costmap_injector import SemanticCostmapInjector
        
        injector = object.__new__(SemanticCostmapInjector)
        injector.enable_negative_obstacles = True
        injector.max_floor_dist = 2.5
        injector.min_drop_height = 0.15
        injector.grid_res = 0.05
        injector.costmap_frame = 'map'
        injector.active_obstacles = {}
        injector.lock = MagicMock()
        injector.get_logger = MagicMock()

        # Mock TF buffer
        injector.tf_buffer = MagicMock()
        injector.tf_buffer.can_transform.return_value = True
        injector.depth_lock = MagicMock()
        injector.max_obstacles = 500
        injector._depth_frame_counter = 5  # Set to 5 so 5+1=6 executes without throttle skip

        mock_tf = MagicMock()
        mock_tf.transform.rotation.x = 0.0
        mock_tf.transform.rotation.y = 0.0
        mock_tf.transform.rotation.z = 0.0
        mock_tf.transform.rotation.w = 1.0
        mock_tf.transform.translation.x = 0.0
        mock_tf.transform.translation.y = 0.0
        mock_tf.transform.translation.z = -0.20  # floor drop offset
        injector.tf_buffer.lookup_transform.return_value = mock_tf

        # Create mock image msg
        height, width = 400, 640
        depth_data = np.ones((height, width), dtype=np.float32) * 1.0
        depth_data[300:399, 280:360] = 3.5

        msg = MagicMock()
        msg.height = height
        msg.width = width
        msg.encoding = '32FC1'
        msg.data = depth_data.tobytes()
        msg.header.frame_id = 'camera_optical_frame'

        injector.depth_callback(msg)

        # Verify obstacles registered
        self.assertGreater(len(injector.active_obstacles), 0, "No negative obstacles registered by depth_callback")
        has_neg = any(obs.get('label') == 'negative_obstacle' for obs in injector.active_obstacles.values())
        self.assertTrue(has_neg, "Negative obstacle label missing in active_obstacles dictionary")


if __name__ == '__main__':
    unittest.main()
