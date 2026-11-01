"""
Global Pytest Configuration and Test Fixtures for Marcus AI.
Provides clean fallback mocks for ROS 2 modules (rclpy, geometry_msgs, nav2_msgs)
when running test suites in non-ROS host environments (e.g., Windows x86_64).
"""

import sys
import types
from unittest.mock import MagicMock

# 1. Mock rclpy if not installed
if "rclpy" not in sys.modules:
    mock_rclpy = types.ModuleType("rclpy")
    mock_rclpy.logging = MagicMock()
    mock_rclpy.action = MagicMock()
    mock_rclpy.node = MagicMock()
    mock_rclpy.init = MagicMock()
    mock_rclpy.shutdown = MagicMock()
    mock_rclpy.ok = MagicMock(return_value=True)
    mock_rclpy.spin = MagicMock()
    mock_rclpy.spin_once = MagicMock()
    sys.modules["rclpy"] = mock_rclpy
    sys.modules["rclpy.action"] = mock_rclpy.action
    sys.modules["rclpy.node"] = mock_rclpy.node
    sys.modules["rclpy.logging"] = mock_rclpy.logging

# 2. Mock geometry_msgs if not installed
if "geometry_msgs" not in sys.modules:
    mock_geo = types.ModuleType("geometry_msgs")
    mock_geo_msg = types.ModuleType("geometry_msgs.msg")

    class MockVector3:
        def __init__(self, x=0.0, y=0.0, z=0.0):
            self.x = float(x)
            self.y = float(y)
            self.z = float(z)

    class MockTwist:
        def __init__(self, *args, **kwargs):
            self.linear = MockVector3()
            self.angular = MockVector3()

    class MockPoseStamped:
        def __init__(self, *args, **kwargs):
            self.header = MagicMock()
            self.pose = MagicMock()

    mock_geo_msg.Twist = MockTwist
    mock_geo_msg.PoseStamped = MockPoseStamped
    mock_geo_msg.Point = MagicMock
    mock_geo_msg.Quaternion = MagicMock
    mock_geo.msg = mock_geo_msg
    sys.modules["geometry_msgs"] = mock_geo
    sys.modules["geometry_msgs.msg"] = mock_geo_msg

# 3. Mock nav2_msgs if not installed
if "nav2_msgs" not in sys.modules:
    mock_nav2 = types.ModuleType("nav2_msgs")
    mock_nav2_action = types.ModuleType("nav2_msgs.action")
    mock_nav2_action.NavigateToPose = MagicMock
    mock_nav2.action = mock_nav2_action
    sys.modules["nav2_msgs"] = mock_nav2
    sys.modules["nav2_msgs.action"] = mock_nav2_action

# 4. Mock robopy_controller.srv if not installed
if "robopy_controller.srv" not in sys.modules:
    mock_srv = types.ModuleType("robopy_controller.srv")
    mock_srv.AskVisualQuestion = MagicMock
    sys.modules["robopy_controller.srv"] = mock_srv
