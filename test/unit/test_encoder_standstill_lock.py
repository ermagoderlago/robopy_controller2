#!/usr/bin/env python3
"""
Unit Test - Encoder Standstill Zero-Velocity Lock & Outlier Filter (FM-MOT-006)
==============================================================================
Validates:
1. Standstill Zero-Velocity Lock: when motors_stopped is True, massive encoder tick bursts
   (such as 300+ ticks from Hall transition jitter) produce delta_s=0.0 and v_robot=0.0.
2. Position stability: x and y do not accumulate phantom drift when stopped.
3. Outlier rejection: tick deltas exceeding physical speed limits (0.45 m/s) are discarded.
4. Kinematic asymmetry suppression: single wheel bursts without corresponding IMU yaw rate are rejected.
"""

import sys
import os
import time
import math
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
            'invert_right_encoder': True,
            'encoder_dead_zone': 2,
            'publish_tf': False,
            'odom_topic': '/odom',
            'use_cmd_vel_odometry': False,
            'use_encoder_for_linear': True,
            'use_imu_for_rotation': True,
            'invert_imu_yaw': True,
            'standstill_encoder_deadband': 8,
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
        logger = MagicMock()
        logger.info = MagicMock()
        logger.warn = MagicMock()
        logger.error = MagicMock()
        return logger
    def get_clock(self):
        clock = MagicMock()
        clock.now().nanoseconds = int(time.time() * 1e9)
        clock.now().to_msg = MagicMock()
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

from robopy_controller.nodes.waveshare_motor_driver import WaveshareMotorDriver

class TestEncoderStandstillLock(unittest.TestCase):

    def setUp(self):
        with patch('threading.Thread'), patch('serial.Serial'):
            self.driver = WaveshareMotorDriver()
            self.driver.serial_conn = MagicMock()
            self.driver.serial_conn.is_open = True
            self.driver.logger = MagicMock()
            self.driver.get_logger = MagicMock(return_value=self.driver.logger)

    def test_01_standstill_lock_rejects_hall_jitter_bursts(self):
        """When motors_stopped is True, a burst of 300 ticks must result in delta_s=0 and v_robot=0."""
        self.driver.motors_stopped = True
        
        # Initial baseline
        self.driver.process_encoder_feedback(1000, 1000)
        initial_x = self.driver.x
        initial_y = self.driver.y
        
        # Hall jitter burst: left wheel static, right wheel jumps by 308 ticks (FM-MOT-006)
        self.driver.process_encoder_feedback(1000, 1308)
        
        self.assertEqual(self.driver.v_robot, 0.0, "v_robot must be strictly 0.0 at standstill")
        self.assertEqual(self.driver.x, initial_x, "Robot x must not drift at standstill")
        self.assertEqual(self.driver.y, initial_y, "Robot y must not drift at standstill")

    def test_02_outlier_rejection_during_commanded_motion(self):
        """When moving, ticks exceeding physical max velocity (0.45 m/s ceiling) must be discarded."""
        self.driver.motors_stopped = False
        self.driver.cmd_linear_x = 0.20
        
        # Initial baseline
        self.driver.process_encoder_feedback(2000, 2000)
        
        # Glitch: an impossible 500-tick jump in 50ms (~3.2 m/s)
        self.driver.process_encoder_feedback(2000, 2500)
        
        # Right wheel should be rejected (warning logged)
        self.driver.logger.warn.assert_called()

    def test_03_asymmetric_chatter_suppression(self):
        """Single wheel chatter with near-zero IMU rotation must be suppressed."""
        self.driver.motors_stopped = False
        self.driver.cmd_linear_x = 0.0
        self.driver.oak_yaw_rate = 0.0  # IMU reports 0 rotation
        
        # Initial baseline
        self.driver.process_encoder_feedback(5000, 5000)
        
        # Left 0, Right 50 ticks (would imply ~50 deg/s rotation, but IMU says 0.0)
        self.driver.process_encoder_feedback(5000, 5050)
        
        self.driver.logger.warn.assert_called()

    def test_04_pure_theoretical_cmd_vel_odometry(self):
        """When use_cmd_vel_odometry is True, odometry strictly follows cmd_vel, immune to camera vibration."""
        self.driver.use_cmd_vel_odometry = True
        self.driver.use_imu_for_rotation = False
        self.driver.motors_stopped = False
        self.driver.cmd_linear_x = 0.25
        self.driver.cmd_angular_z = 0.00
        
        # Simulate severe camera mast vibration on OAK-D Lite gyro (e.g. 0.20 rad/s)
        self.driver.oak_yaw_rate = 0.20
        
        # Initial baseline
        self.driver.process_encoder_feedback(100, 100)
        initial_theta = self.driver.theta
        
        # Simulate ticks and multiple feedback cycles
        self.driver.process_encoder_feedback(150, 150)
        
        # In theoretical odometry, w_robot must strictly follow cmd_angular_z (0.00)
        self.assertEqual(self.driver.w_robot, 0.00, "w_robot must strictly be 0.00 when driving straight")
        self.assertEqual(self.driver.v_robot, 0.25, "v_robot must strictly match commanded linear speed")
        self.assertEqual(self.driver.theta, initial_theta, "theta must not be corrupted by camera mast vibration")

if __name__ == '__main__':
    unittest.main()
