"""
Global Pytest Configuration and Test Fixtures for Marcus AI.
Provides clean fallback mocks for ROS 2 modules (rclpy, geometry_msgs, nav2_msgs)
when running test suites in non-ROS host environments (e.g., Windows x86_64).
"""

import os
import sys
import types
from unittest.mock import MagicMock

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for _p in (_PROJECT_ROOT, _ROBOPY_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 1. Mock rclpy if not installed
if "rclpy" not in sys.modules:
    mock_rclpy = types.ModuleType("rclpy")
    mock_rclpy.__path__ = []
    mock_rclpy.logging = MagicMock()
    mock_rclpy.action = MagicMock()

    mock_rclpy_node = types.ModuleType("rclpy.node")

    class _MockROSNode:
        def __init__(self, *args, **kwargs):
            self._logger = MagicMock()

        def get_logger(self):
            return self._logger

        def create_publisher(self, *args, **kwargs):
            return MagicMock()

        def create_subscription(self, *args, **kwargs):
            return MagicMock()

        def create_client(self, *args, **kwargs):
            return MagicMock()

        def create_service(self, *args, **kwargs):
            return MagicMock()

        def create_timer(self, *args, **kwargs):
            return MagicMock()

        def declare_parameter(self, *args, **kwargs):
            return MagicMock()

        def get_parameter(self, *args, **kwargs):
            return MagicMock()

    mock_rclpy_node.Node = _MockROSNode
    mock_rclpy.node = mock_rclpy_node

    mock_rclpy_qos = types.ModuleType("rclpy.qos")
    mock_rclpy_qos.QoSProfile = MagicMock
    mock_rclpy_qos.ReliabilityPolicy = MagicMock()
    mock_rclpy_qos.HistoryPolicy = MagicMock()
    mock_rclpy_qos.DurabilityPolicy = MagicMock()
    mock_rclpy.qos = mock_rclpy_qos

    mock_rclpy.init = MagicMock()
    mock_rclpy.shutdown = MagicMock()
    mock_rclpy.ok = MagicMock(return_value=True)
    mock_rclpy.spin = MagicMock()
    mock_rclpy.spin_once = MagicMock()
    mock_rclpy.callback_groups = MagicMock()
    mock_rclpy.executors = MagicMock()
    mock_rclpy.time = MagicMock()
    mock_rclpy.duration = MagicMock()
    sys.modules["rclpy"] = mock_rclpy
    sys.modules["rclpy.action"] = mock_rclpy.action
    sys.modules["rclpy.node"] = mock_rclpy_node
    sys.modules["rclpy.qos"] = mock_rclpy_qos
    sys.modules["rclpy.logging"] = mock_rclpy.logging
    sys.modules["rclpy.callback_groups"] = mock_rclpy.callback_groups
    sys.modules["rclpy.executors"] = mock_rclpy.executors
    sys.modules["rclpy.time"] = mock_rclpy.time
    sys.modules["rclpy.duration"] = mock_rclpy.duration

if "std_srvs" not in sys.modules:
    mock_std_srvs = types.ModuleType("std_srvs")
    mock_std_srvs_srv = types.ModuleType("std_srvs.srv")

    class _MockSetBool:
        class Request:
            def __init__(self, data: bool = False):
                self.data = data

        class Response:
            def __init__(self, success: bool = True, message: str = ""):
                self.success = success
                self.message = message

    mock_std_srvs_srv.SetBool = _MockSetBool
    mock_std_srvs_srv.Empty = MagicMock
    mock_std_srvs_srv.Trigger = MagicMock
    mock_std_srvs.srv = mock_std_srvs_srv
    sys.modules["std_srvs"] = mock_std_srvs
    sys.modules["std_srvs.srv"] = mock_std_srvs_srv

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
    mock_geo_msg.TwistStamped = MagicMock
    mock_geo_msg.PoseStamped = MockPoseStamped
    mock_geo_msg.PoseWithCovarianceStamped = MagicMock
    mock_geo_msg.PointStamped = MagicMock
    mock_geo_msg.Point = MagicMock
    mock_geo_msg.Quaternion = MagicMock
    mock_geo_msg.TransformStamped = MagicMock
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

# 4. Mock robopy_controller.srv / msg and standard ROS 2 interface packages if not installed
if "robopy_controller.srv" not in sys.modules:
    mock_srv = types.ModuleType("robopy_controller.srv")
    mock_srv.AskVisualQuestion = MagicMock
    sys.modules["robopy_controller.srv"] = mock_srv

if "robopy_controller.msg" not in sys.modules:
    mock_msg = types.ModuleType("robopy_controller.msg")
    mock_msg.AudioData = MagicMock
    sys.modules["robopy_controller.msg"] = mock_msg

if "std_msgs" not in sys.modules:
    _std_m = types.ModuleType("std_msgs")
    _std_m.__path__ = []
    _std_msg = types.ModuleType("std_msgs.msg")

    class _MockString:
        def __init__(self, data: str = ""):
            self.data = data

    class _MockBool:
        def __init__(self, data: bool = False):
            self.data = data

    class _MockFloat32:
        def __init__(self, data: float = 0.0):
            self.data = data

    class _MockInt32:
        def __init__(self, data: int = 0):
            self.data = data

    class _MockFloat32MultiArray:
        def __init__(self, data=None):
            self.data = list(data) if data is not None else []

    _std_msg.String = _MockString
    _std_msg.Bool = _MockBool
    _std_msg.Float32 = _MockFloat32
    _std_msg.Int32 = _MockInt32
    _std_msg.Float32MultiArray = _MockFloat32MultiArray
    _std_msg.Header = MagicMock
    _std_m.msg = _std_msg
    sys.modules["std_msgs"] = _std_m
    sys.modules["std_msgs.msg"] = _std_msg

for _pkg, _sub in (
    ("sensor_msgs", "msg"),
    ("vision_msgs", "msg"),
    ("diagnostic_msgs", "msg"),
    ("visualization_msgs", "msg"),
    ("rcl_interfaces", "msg"),
    ("example_interfaces", "srv"),
):
    if _pkg not in sys.modules:
        _m = types.ModuleType(_pkg)
        _m.__path__ = []
        _sm = MagicMock()
        setattr(_m, _sub, _sm)
        sys.modules[_pkg] = _m
        sys.modules[f"{_pkg}.{_sub}"] = _sm
