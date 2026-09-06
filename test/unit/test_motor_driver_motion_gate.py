#!/usr/bin/env python3
"""
Unit Test - Waveshare Motor Driver Motion Gating & Hardware Safety
==================================================================
Validates:
1. When motion_gate is True, cmd_vel commands are routed to motor kinematics and serial.
2. When motion_gate is False, cmd_vel commands immediately command hardware 0.0 PWM,
   preventing physical wheel movement while sensors spin up.
3. When motion_gate transitions False -> True, recent cached cmd_vel (< 500ms)
   is immediately dispatched to wheels.
4. When motion_gate transitions True -> False during movement, motors are halted immediately.
"""

import sys
import os
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('robopy_controller'))

# Ensure ROS 2 & serial modules are mocked if running outside ROS 2 environment
class DummyNode:
    def __init__(self, node_name, *args, **kwargs):
        self.node_name = node_name
    def declare_parameter(self, name, default):
        pass
    def get_parameter(self, name):
        m = MagicMock()
        defaults = {
            'serial_port': '/dev/null',
            'baud_rate': 115200,
            'wheel_radius': 0.0335,
            'wheel_separation': 0.285,
            'rotational_wheel_separation': 0.285,
            'ticks_per_rev': 657,
            'invert_left_motor': False,
            'invert_right_motor': False,
            'invert_left_encoder': False,
            'invert_right_encoder': False,
            'encoder_dead_zone': 0,
            'publish_tf': False,
            'odom_topic': '/odom_wheel',
            'feedforward_nominal_voltage': 11.10,
            'feedforward_min_scale': 0.70,
            'feedforward_max_scale': 1.40,
            'enable_voltage_feedforward': False,
            'max_linear_speed': 0.40,
            'motor_min_duty_cycle': 0.18,
            'raw_battery_topic': '/battery/raw',
            'esp32_adc_scale_factor': 2880.95,
        }
        m.value = defaults.get(name, 0.0)
        return m
    def create_publisher(self, *args, **kwargs):
        return MagicMock()
    def create_subscription(self, *args, **kwargs):
        return MagicMock()
    def create_timer(self, *args, **kwargs):
        return MagicMock()
    def get_logger(self):
        return MagicMock()
    def get_clock(self):
        clock = MagicMock()
        clock.now().nanoseconds = int(time.time() * 1e9)
        return clock
    def add_on_set_parameters_callback(self, *args, **kwargs):
        pass

if 'rclpy' not in sys.modules:
    sys.modules['rclpy'] = MagicMock()
    sys.modules['rclpy.node'] = MagicMock(Node=DummyNode)
if 'geometry_msgs' not in sys.modules:
    sys.modules['geometry_msgs'] = MagicMock()
    sys.modules['geometry_msgs.msg'] = MagicMock()
if 'nav_msgs' not in sys.modules:
    sys.modules['nav_msgs'] = MagicMock()
    sys.modules['nav_msgs.msg'] = MagicMock()
if 'tf2_ros' not in sys.modules or not hasattr(sys.modules['tf2_ros'], 'TransformBroadcaster'):
    tf2_mock = MagicMock()
    tf2_mock.TransformBroadcaster = MagicMock
    sys.modules['tf2_ros'] = tf2_mock
if 'sensor_msgs' not in sys.modules:
    sys.modules['sensor_msgs'] = MagicMock()
    sys.modules['sensor_msgs.msg'] = MagicMock()
if 'std_msgs' not in sys.modules:
    sys.modules['std_msgs'] = MagicMock()
    sys.modules['std_msgs.msg'] = MagicMock()
if 'diagnostic_msgs' not in sys.modules:
    sys.modules['diagnostic_msgs'] = MagicMock()
    sys.modules['diagnostic_msgs.msg'] = MagicMock()
if 'serial' not in sys.modules:
    sys.modules['serial'] = MagicMock()
    sys.modules['diagnostic_msgs.msg'] = MagicMock()
    sys.modules['serial'] = MagicMock()

# Patch serial thread loop before importing node to avoid opening threads
with patch('threading.Thread.start'):
    from robopy_controller.nodes.waveshare_motor_driver import WaveshareMotorDriver


class TestMotorDriverMotionGate(unittest.TestCase):
    def setUp(self):
        with patch('threading.Thread.start'):
            self.node = WaveshareMotorDriver()
        self.node.wheel_separation = 0.285
        self.node.wheel_radius = 0.0335
        self.node.max_linear_speed = 0.40
        self.node.motor_min_duty_cycle = 0.18
        self.node.send_speeds = MagicMock()

    def test_01_motion_gate_initial_state(self):
        """Motion gate must initialize to True (open) by default."""
        self.assertTrue(self.node.motion_gate)
        self.assertIsNone(self.node.cached_cmd_vel)

    def test_02_cmd_vel_normal_when_gate_open(self):
        """When motion_gate is True, cmd_vel executes kinematics and sends non-zero speeds."""
        twist = MagicMock()
        twist.linear.x = 0.20
        twist.angular.z = 0.0
        
        self.node.cmd_vel_callback(twist)
        
        self.node.send_speeds.assert_called()
        args = self.node.send_speeds.call_args[0]
        # v_L and v_R should be ~0.20
        self.assertAlmostEqual(args[0], 0.20, delta=0.01)
        self.assertAlmostEqual(args[1], 0.20, delta=0.01)

    def test_03_cmd_vel_locked_when_gate_closed(self):
        """When motion_gate is False, cmd_vel must send 0.0 speeds and cache the command."""
        self.node.motion_gate = False
        
        twist = MagicMock()
        twist.linear.x = 0.25
        twist.angular.z = 0.5
        
        self.node.cmd_vel_callback(twist)
        
        self.node.send_speeds.assert_called_with(0.0, 0.0)
        self.assertEqual(self.node.cached_cmd_vel, twist)
        self.assertTrue(self.node.motors_stopped)

    def test_04_gate_opening_dispatches_cached_command(self):
        """When motion gate opens, recent cached cmd_vel (< 500ms) is immediately dispatched."""
        self.node.motion_gate = False
        
        twist = MagicMock()
        twist.linear.x = 0.15
        twist.angular.z = 0.0
        
        self.node.cmd_vel_callback(twist)
        self.assertEqual(self.node.cached_cmd_vel, twist)
        self.node.send_speeds.reset_mock()
        
        # Simulate motion gate opening from sensor_standby_manager
        gate_msg = MagicMock()
        gate_msg.data = True
        self.node.motion_gate_callback(gate_msg)
        
        self.assertTrue(self.node.motion_gate)
        self.node.send_speeds.assert_called()
        args = self.node.send_speeds.call_args[0]
        self.assertAlmostEqual(args[0], 0.15, delta=0.01)

    def test_05_gate_closing_halts_motors_immediately(self):
        """When motion gate closes while robot is moving, motors are halted immediately."""
        self.node.motion_gate = True
        
        gate_msg = MagicMock()
        gate_msg.data = False
        self.node.motion_gate_callback(gate_msg)
        
        self.assertFalse(self.node.motion_gate)
        self.node.send_speeds.assert_called_with(0.0, 0.0)
        self.assertTrue(self.node.motors_stopped)


if __name__ == '__main__':
    unittest.main()
