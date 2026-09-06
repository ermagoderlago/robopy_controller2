#!/usr/bin/env python3
"""
Unit Test - Sensor Standby Manager (Smart Power-Save & Motion Gating)
=====================================================================
Validates:
1. Initial node state: ACTIVE, Motion Gate OPEN (True), Power State ACTIVE.
2. Idle Stillness Detection: Inactivity > 120s triggers STANDBY state,
   calling /stop_motor and /rtabmap/pause, and locking Motion Gate (False).
3. IMU Disturbance Wake-up: External bump/lift/push (|norm(a) - g| > 0.35 or norm(w) > 0.15)
   triggers WAKING_UP state, calling /start_motor and /rtabmap/resume.
4. Command Velocity Wake-up: Incoming /cmd_vel triggers WAKING_UP state.
5. Motion Gating during Spin-up: Motion gate remains LOCKED (False) until LiDAR scans arrive.
6. Spin-up Scan Confirmation: Receiving min_scans_to_wake valid scans returns state to ACTIVE
   and OPENS the Motion Gate (True).
7. Spin-up Timeout Safeguard: If LiDAR fails to provide scans within scan_timeout_sec,
   the node safely falls back to ACTIVE to prevent deadlock.
"""

import sys
import os
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('robopy_controller'))

# Ensure ROS 2 modules are mocked if running outside ROS 2 environment
if 'rclpy' not in sys.modules or 'tf2_ros' not in sys.modules:
    class DummyNode:
        def __init__(self, node_name, *args, **kwargs):
            self.node_name = node_name
        def declare_parameter(self, name, default):
            pass
        def get_parameter(self, name):
            m = MagicMock()
            defaults = {
                'idle_timeout_sec': 120.0,
                'imu_accel_threshold': 0.35,
                'imu_gyro_threshold': 0.15,
                'nominal_gravity': 9.81,
                'min_scans_to_wake': 2,
                'scan_timeout_sec': 3.5,
                'check_frequency_hz': 10.0,
            }
            m.value = defaults.get(name, MagicMock())
            return m
        def create_publisher(self, *args, **kwargs):
            return MagicMock()
        def create_subscription(self, *args, **kwargs):
            return MagicMock()
        def create_timer(self, *args, **kwargs):
            return MagicMock()
        def create_client(self, *args, **kwargs):
            cli = MagicMock()
            cli.service_is_ready.return_value = True
            cli.call_async.return_value = MagicMock()
            return cli
        def create_service(self, *args, **kwargs):
            return MagicMock()
        def add_on_set_parameters_callback(self, *args, **kwargs):
            pass
        def get_clock(self):
            clock = MagicMock()
            clock.now().nanoseconds = int(time.time() * 1e9)
            return clock
        def get_logger(self):
            return MagicMock()

    if 'rclpy' not in sys.modules:
        sys.modules['rclpy'] = MagicMock()
        sys.modules['rclpy.node'] = MagicMock(Node=DummyNode)
        sys.modules['rclpy.callback_groups'] = MagicMock()
    if 'sensor_msgs' not in sys.modules:
        sys.modules['sensor_msgs'] = MagicMock()
        sys.modules['sensor_msgs.msg'] = MagicMock()
    if 'geometry_msgs' not in sys.modules:
        sys.modules['geometry_msgs'] = MagicMock()
        sys.modules['geometry_msgs.msg'] = MagicMock()
    if 'nav_msgs' not in sys.modules:
        sys.modules['nav_msgs'] = MagicMock()
        sys.modules['nav_msgs.msg'] = MagicMock()
    if 'std_msgs' not in sys.modules:
        sys.modules['std_msgs'] = MagicMock()
        sys.modules['std_msgs.msg'] = MagicMock()
    if 'std_srvs' not in sys.modules:
        sys.modules['std_srvs'] = MagicMock()
        sys.modules['std_srvs.srv'] = MagicMock()
    if 'tf2_ros' not in sys.modules:
        tf2_mock = MagicMock()
        tf2_mock.TransformBroadcaster = MagicMock
        sys.modules['tf2_ros'] = tf2_mock

from robopy_controller.nodes.sensor_standby_manager import SensorStandbyManager


class TestSensorStandbyManager(unittest.TestCase):
    def setUp(self):
        self.node = SensorStandbyManager()
        self.node.idle_timeout_sec = 120.0
        self.node.imu_accel_threshold = 0.35
        self.node.imu_gyro_threshold = 0.15
        self.node.nominal_gravity = 9.81
        self.node.min_scans_to_wake = 2
        self.node.scan_timeout_sec = 3.5

    def test_01_initial_state(self):
        """Node must boot in ACTIVE state with Motion Gate OPEN (True)."""
        self.assertEqual(self.node.state, 'ACTIVE')
        self.assertTrue(self.node.last_gate_published)

    def test_02_idle_timeout_triggers_standby(self):
        """After 120s of inactivity, supervisor must transition to STANDBY and lock Motion Gate."""
        now = time.time()
        # Simulate 121 seconds of inactivity
        self.node.last_activity_time = now - 121.0
        
        self.node.supervisor_step()
        
        self.assertEqual(self.node.state, 'STANDBY')
        self.assertFalse(self.node.last_gate_published)
        self.node.stop_motor_cli.call_async.assert_called()
        self.node.pause_rtabmap_cli.call_async.assert_called()

    def test_03_imu_disturbance_wakes_from_standby(self):
        """External acceleration (bump/lift/push) must awaken sensors from STANDBY."""
        self.node.state = 'STANDBY'
        self.node.last_gate_published = False
        
        # Create mock IMU message with linear acceleration departure: norm ~ 10.5 (> 9.81 + 0.35)
        imu_msg = MagicMock()
        imu_msg.linear_acceleration.x = 0.0
        imu_msg.linear_acceleration.y = 0.0
        imu_msg.linear_acceleration.z = 10.50
        imu_msg.angular_velocity.x = 0.0
        imu_msg.angular_velocity.y = 0.0
        imu_msg.angular_velocity.z = 0.0
        
        self.node.imu_callback(imu_msg)
        
        self.assertEqual(self.node.state, 'WAKING_UP')
        self.assertFalse(self.node.last_gate_published)  # Motion gate must stay locked during waking
        self.node.start_motor_cli.call_async.assert_called()
        self.node.resume_rtabmap_cli.call_async.assert_called()

    def test_04_cmd_vel_wakes_from_standby(self):
        """Movement command on /cmd_vel must awaken sensors from STANDBY."""
        self.node.state = 'STANDBY'
        self.node.last_gate_published = False
        
        twist_msg = MagicMock()
        twist_msg.linear.x = 0.15
        twist_msg.angular.z = 0.0
        
        self.node.cmd_vel_callback(twist_msg)
        
        self.assertEqual(self.node.state, 'WAKING_UP')
        self.assertFalse(self.node.last_gate_published)
        self.node.start_motor_cli.call_async.assert_called()
        self.node.resume_rtabmap_cli.call_async.assert_called()

    def test_05_scan_confirmation_unlocks_motion_gate(self):
        """Once min_scans_to_wake LaserScans arrive, state returns to ACTIVE and Motion Gate OPENS."""
        self.node.state = 'WAKING_UP'
        self.node.wake_start_time = time.time()
        self.node.wake_scans_count = 0
        self.node.last_gate_published = False
        
        scan_msg = MagicMock()
        scan_msg.ranges = [1.0, 1.2, 1.5]
        
        # First scan
        self.node.scan_callback(scan_msg)
        self.assertEqual(self.node.state, 'WAKING_UP')
        self.assertEqual(self.node.wake_scans_count, 1)
        self.assertFalse(self.node.last_gate_published)
        
        # Second scan (meets min_scans_to_wake = 2)
        self.node.scan_callback(scan_msg)
        self.assertEqual(self.node.state, 'ACTIVE')
        self.assertTrue(self.node.last_gate_published)

    def test_06_spin_up_timeout_safeguard(self):
        """If LiDAR scans do not arrive within scan_timeout_sec, fallback unlocks Motion Gate."""
        self.node.state = 'WAKING_UP'
        self.node.wake_start_time = time.time() - 4.0  # Expired > 3.5s
        self.node.last_gate_published = False
        
        self.node.supervisor_step()
        
        self.assertEqual(self.node.state, 'ACTIVE')
        self.assertTrue(self.node.last_gate_published)

    def test_07_manual_wake_service(self):
        """Manual trigger service wakes up sensors on demand."""
        self.node.state = 'STANDBY'
        self.node.last_gate_published = False
        
        req = MagicMock()
        res = MagicMock()
        self.node.manual_wake_trigger_callback(req, res)
        
        self.assertEqual(self.node.state, 'WAKING_UP')
        self.assertTrue(res.success)


if __name__ == '__main__':
    unittest.main()
