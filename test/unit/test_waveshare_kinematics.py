#!/usr/bin/env python3
"""
Unit Test - Waveshare Differential Drive Kinematics & Encoder Odometry Verification
===================================================================================
Validates:
1. cmd_vel forward (v > 0, w = 0):
   - Left wheel target > 0 -> serial channel L receives negative duty (hardware mirrored).
   - Right wheel target > 0 -> serial channel R receives positive duty.
2. cmd_vel turn left (w > 0, CCW):
   - Right wheel forward (+), Left wheel backward (-).
   - Encoder feedback: odl < 0 (left forward) -> inverted to +, odr > 0 -> right forward.
   - delta_theta > 0 (strictly CCW, conforming to REP-103).
3. cmd_vel turn right (w < 0, CW):
   - Right wheel backward (-), Left wheel forward (+).
   - delta_theta < 0 (strictly CW, conforming to REP-103).
4. Standstill jitter suppression:
   - Jitter <= encoder_dead_zone produces zero delta.
5. Watchdog stall reset:
   - Watchdog stop resets cmd_active and prevents latched stall state.
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
            'invert_left_encoder': True,
            'invert_right_encoder': True,
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
        """Moving straight forward must drive Left wheel (negative L) and Right wheel (positive R)."""
        msg = MagicMock()
        msg.linear.x = 0.20
        msg.angular.z = 0.0
        
        self.driver.cmd_vel_callback(msg)
        self.assertGreater(len(self.sent_commands), 0)
        last_cmd = self.sent_commands[-1]
        
        # Channel L = -duty_left < 0 (drives left motor forward)
        # Channel R = +duty_right > 0 (drives right motor forward)
        self.assertLess(last_cmd['L'], 0.0, "Channel L must be negative to drive left wheel forward")
        self.assertGreater(last_cmd['R'], 0.0, "Channel R must be positive to drive right wheel forward")

    def test_02_turn_left_command_and_encoder_polarity(self):
        """Turn left (CCW, w > 0) must command Right wheel forward and Left wheel backward, and delta_theta > 0."""
        msg = MagicMock()
        msg.linear.x = 0.0
        msg.angular.z = 0.50 # CCW
        
        self.driver.cmd_vel_callback(msg)
        self.assertGreater(len(self.sent_commands), 0)
        last_cmd = self.sent_commands[-1]
        
        # When turning left (CCW):
        # Left wheel moves backward -> duty_left < 0 -> channel L = -duty_left > 0
        # Right wheel moves forward -> duty_right > 0 -> channel R = +duty_right > 0
        self.assertGreater(last_cmd['L'], 0.0, "Channel L must be positive to drive left wheel backward")
        self.assertGreater(last_cmd['R'], 0.0, "Channel R must be positive to drive right wheel forward")
        
        # Now simulate encoder feedback for turn left:
        # Physical encoders count negative when moving forward, positive when moving backward.
        # Turn left: Left wheel backward (odl increases), Right wheel forward (odr decreases).
        self.driver.process_encoder_feedback(1000, 1000) # Baseline
        self.driver.process_encoder_feedback(1050, 950)  # odl: +50 (backward), odr: -50 (forward)
        
        # With invert_left_encoder=True: delta_ticks_left = -50 (backward)
        # With invert_right_encoder=True: delta_ticks_right = +50 (forward)
        # delta_theta = (delta_s_right - delta_s_left) / W = (+s - (-s)) / W > 0
        self.assertGreater(self.driver.theta, 0.0, "Integrated yaw must be positive (CCW) for turn left")

    def test_03_turn_right_command_and_encoder_polarity(self):
        """Turn right (CW, w < 0) must command Right wheel backward and Left wheel forward, and delta_theta < 0."""
        msg = MagicMock()
        msg.linear.x = 0.0
        msg.angular.z = -0.50 # CW
        
        self.driver.cmd_vel_callback(msg)
        self.assertGreater(len(self.sent_commands), 0)
        last_cmd = self.sent_commands[-1]
        
        # When turning right (CW):
        # Left wheel moves forward -> duty_left > 0 -> channel L = -duty_left < 0
        # Right wheel moves backward -> duty_right < 0 -> channel R = +duty_right < 0
        self.assertLess(last_cmd['L'], 0.0, "Channel L must be negative to drive left wheel forward")
        self.assertLess(last_cmd['R'], 0.0, "Channel R must be negative to drive right wheel backward")
        
        # Simulate encoder feedback for turn right:
        # Physical encoders count negative when moving forward, positive when moving backward.
        # Turn right: Left wheel forward (odl decreases), Right wheel backward (odr increases).
        self.driver.process_encoder_feedback(1000, 1000) # Baseline
        self.driver.process_encoder_feedback(950, 1050)  # odl: -50 (forward), odr: +50 (backward)
        
        # With invert_left_encoder=True: delta_ticks_left = +50 (forward)
        # With invert_right_encoder=True: delta_ticks_right = -50 (backward)
        # delta_theta = (delta_s_right - delta_s_left) / W = (-s - (+s)) / W < 0
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
        self.driver.last_cmd_vel_time = time.time() - 1.0 # Expired
        
        self.driver.watchdog_callback()
        self.assertTrue(self.driver.motors_stopped)
        self.assertFalse(self.driver.is_stalled, "is_stalled must be cleared when motors are stopped")

if __name__ == '__main__':
    unittest.main()
