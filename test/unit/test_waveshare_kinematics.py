#!/usr/bin/env python3
"""
Unit Test - Waveshare Differential Drive Kinematics & Encoder Odometry Verification
===================================================================================
Validates the CHANNEL SWAP architecture (§24 actuation_motor_driver.md):
  - Serial channel 'L' on ESP32 drives the physical RIGHT wheel motor.
  - Serial channel 'R' on ESP32 drives the physical LEFT wheel motor.
  - odl encoder corresponds to physical RIGHT wheel.
  - odr encoder corresponds to physical LEFT wheel.
  - invert_left_encoder = False, invert_right_encoder = False.

Tests:
1. cmd_vel forward (v > 0, w = 0): L and R same sign (both positive = both forward).
2. cmd_vel turn left (w > 0, CCW): delta_theta > 0 per REP-103.
3. cmd_vel turn right (w < 0, CW): delta_theta < 0 per REP-103.
4. Standstill jitter suppression.
5. Watchdog stall reset.
"""

import sys
import os
import time
import math
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('robopy_controller'))

# Mock ROS 2 environment for isolated testing
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
            'invert_left_encoder': False,   # §24: no inversion, channel swap handles polarity
            'invert_right_encoder': False,  # §24: no inversion, channel swap handles polarity
            'encoder_dead_zone': 2,
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

with patch('threading.Thread.start'):
    from robopy_controller.nodes.waveshare_motor_driver import WaveshareMotorDriver

class TestWaveshareKinematics(unittest.TestCase):
    def setUp(self):
        with patch('threading.Thread.start'):
            self.driver = WaveshareMotorDriver()
        self.driver.serial_conn = MagicMock()
        self.driver.serial_conn.is_open = True
        self.sent_commands = []

        def mock_write(b):
            line = b.decode('utf-8').strip()
            for part in line.split('\n'):
                part = part.strip()
                if part and part.startswith('{') and '"L"' in part:
                    try:
                        self.sent_commands.append(json.loads(part))
                    except Exception:
                        pass
        self.driver.serial_conn.write = mock_write

        # Reset odometry state
        self.driver.x = 0.0
        self.driver.y = 0.0
        self.driver.theta = 0.0
        self.driver.prev_left_ticks = None
        self.driver.prev_right_ticks = None

    def test_01_straight_forward_command(self):
        """Moving straight forward: with channel swap L=duty_right, R=duty_left.
        For v>0, w=0: duty_left = duty_right > 0 -> L > 0, R > 0 (same sign = both wheels forward)."""
        msg = MagicMock()
        msg.linear.x = 0.20
        msg.angular.z = 0.0

        self.driver.cmd_vel_callback(msg)
        self.assertGreater(len(self.sent_commands), 0)
        last_cmd = self.sent_commands[-1]

        # Both channels must be positive (same direction = straight forward)
        self.assertGreater(last_cmd['L'], 0.0, "Channel L (phys RIGHT motor) must be positive for forward")
        self.assertGreater(last_cmd['R'], 0.0, "Channel R (phys LEFT motor) must be positive for forward")
        # Equal magnitude for straight motion (no angular component)
        self.assertAlmostEqual(abs(last_cmd['L']), abs(last_cmd['R']), places=2,
            msg="Both channels must be equal magnitude for straight forward motion")

    def test_02_turn_left_command_and_encoder_polarity(self):
        """Turn left (CCW, w > 0): delta_theta > 0 per REP-103.
        Kinematics: v_L < 0 (left back), v_R > 0 (right fwd).
        Channel swap: L = duty_right > 0, R = duty_left < 0."""
        msg = MagicMock()
        msg.linear.x = 0.0
        msg.angular.z = 0.50  # CCW

        self.driver.cmd_vel_callback(msg)
        self.assertGreater(len(self.sent_commands), 0)
        last_cmd = self.sent_commands[-1]

        # L = duty_right > 0 (right wheel forward), R = duty_left < 0 (left wheel backward)
        self.assertGreater(last_cmd['L'], 0.0, "Channel L (phys right) must be positive to spin right wheel fwd (turn left)")
        self.assertLess(last_cmd['R'], 0.0, "Channel R (phys left) must be negative to spin left wheel bkd (turn left)")

        # Encoder simulation after swap:
        # Turn left: right wheel fwd -> odl increases; left wheel back -> odr decreases
        # In process_encoder_feedback: delta_ticks_right = odl_delta, delta_ticks_left = odr_delta
        # delta_s_right > 0, delta_s_left < 0 -> delta_theta = (d_right - d_left)/W > 0
        self.driver.process_encoder_feedback(1000, 1000)  # baseline
        self.driver.process_encoder_feedback(1050, 950)   # odl+50 (right fwd), odr-50 (left back)

        self.assertGreater(self.driver.theta, 0.0, "Integrated yaw must be positive (CCW) for turn left")

    def test_03_turn_right_command_and_encoder_polarity(self):
        """Turn right (CW, w < 0): delta_theta < 0 per REP-103.
        Kinematics: v_L > 0 (left fwd), v_R < 0 (right back).
        Channel swap: L = duty_right < 0, R = duty_left > 0."""
        msg = MagicMock()
        msg.linear.x = 0.0
        msg.angular.z = -0.50  # CW

        self.driver.cmd_vel_callback(msg)
        self.assertGreater(len(self.sent_commands), 0)
        last_cmd = self.sent_commands[-1]

        # L = duty_right < 0 (right wheel backward), R = duty_left > 0 (left wheel forward)
        self.assertLess(last_cmd['L'], 0.0, "Channel L (phys right) must be negative to spin right wheel bkd (turn right)")
        self.assertGreater(last_cmd['R'], 0.0, "Channel R (phys left) must be positive to spin left wheel fwd (turn right)")

        # Encoder simulation after swap:
        # Turn right: right wheel back -> odl decreases; left wheel fwd -> odr increases
        # delta_ticks_right = odl_delta < 0, delta_ticks_left = odr_delta > 0
        # delta_theta = (d_right - d_left)/W = (neg - pos)/W < 0
        self.driver.process_encoder_feedback(1000, 1000)  # baseline
        self.driver.process_encoder_feedback(950, 1050)   # odl-50 (right back), odr+50 (left fwd)

        self.assertLess(self.driver.theta, 0.0, "Integrated yaw must be negative (CW) for turn right")

    def test_04_standstill_jitter_suppression(self):
        """Electrical jitter <= encoder_dead_zone must not drift pose at standstill."""
        self.driver.process_encoder_feedback(1000, 1000)
        self.driver.motors_stopped = True

        # Jitter of 1-2 ticks
        self.driver.process_encoder_feedback(1002, 1001)
        self.assertEqual(self.driver.theta, 0.0, "Standstill jitter must not drift theta")
        self.assertEqual(self.driver.x, 0.0, "Standstill jitter must not drift position")

    def test_05_watchdog_stall_cleared_on_stop(self):
        """Watchdog stop must clear latched motor stall state."""
        self.driver.cmd_linear_x = 0.20
        self.driver.v_robot = 0.0
        self.driver.motors_stopped = False
        self.driver.last_cmd_vel_time = time.time() - 1.0  # Expired watchdog

        self.driver.watchdog_callback()
        self.assertTrue(self.driver.motors_stopped)
        self.assertFalse(self.driver.is_stalled, "is_stalled must be cleared when motors are stopped")

if __name__ == '__main__':
    unittest.main()

