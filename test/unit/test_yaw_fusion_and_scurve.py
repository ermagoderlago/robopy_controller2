#!/usr/bin/env python3
"""
Unit Test - Complementary Chassis Yaw Fusion & S-Curve Jerk Limiter
===================================================================
Validates:
1. S-Curve Jerk Limiter:
   - Continuous 2nd-order acceleration ramping with finite jerk (max_duty_jerk=25.0 duty/s^2).
   - Duty acceleration bounded by max_duty_accel (5.0 duty/s).
   - Zero-command hard clamp for instantaneous safety stop.
2. Stationary Chassis Gyro Auto-Bias Tracking:
   - Continuous EMA bias estimation (alpha=0.05) when motors_stopped is True.
   - Bias subtraction during motion.
3. Complementary Yaw Fusion:
   - Blending wheel differential delta_theta_wheel with chassis gyro rate (alpha=0.88).
   - Graceful fallback to pure wheel odometry if IMU telemetry is stale (>0.25s).
4. Dynamic Parameter Reconfiguration:
   - On-line update of enable_chassis_yaw_fusion, yaw_fusion_alpha, max_duty_accel, max_duty_jerk.
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

# Ensure ROS 2 & serial modules are mocked if running outside ROS 2 environment
class DummyNode:
    def __init__(self, node_name, *args, **kwargs):
        self.node_name = node_name
        self._declared_params = {}
    def declare_parameter(self, name, default=None):
        self._declared_params[name] = default
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
            'encoder_dead_zone': 2,
            'publish_tf': False,
            'odom_topic': '/odom_wheel',
            'use_cmd_vel_odometry': False,
            'use_encoder_for_linear': True,
            'use_imu_for_rotation': False,
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
            'enable_esp32_pid': True,
            'esp32_pid_kp': 3.20,
            'esp32_pid_ki': 0.22,
            'esp32_pid_kd': 0.04,
            'enable_chassis_yaw_fusion': True,
            'yaw_fusion_alpha': 0.88,
            'max_duty_accel': 5.0,
            'max_duty_jerk': 25.0,
        }
        val = self._declared_params.get(name, defaults.get(name, 0.0))
        m.value = val
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
if 'tf2_ros' not in sys.modules:
    tf2_mock = MagicMock()
    tf2_mock.TransformBroadcaster = MagicMock
    sys.modules['tf2_ros'] = tf2_mock
if 'sensor_msgs' not in sys.modules:
    sys.modules['sensor_msgs'] = MagicMock()
    sys.modules['sensor_msgs.msg'] = MagicMock()
if 'std_msgs' not in sys.modules:
    sys.modules['std_msgs'] = MagicMock()
    sys.modules['std_msgs.msg'] = MagicMock()
class DummyKeyValue:
    def __init__(self, key="", value=""):
        self.key = key
        self.value = value

class DummyDiagnosticStatus:
    OK = 0
    WARN = 1
    ERROR = 2
    def __init__(self, name="", level=0, message="", hardware_id=""):
        self.name = name
        self.level = level
        self.message = message
        self.hardware_id = hardware_id
        self.values = []

class DummyDiagnosticArray:
    def __init__(self):
        self.header = MagicMock()
        self.status = []

if 'diagnostic_msgs' not in sys.modules:
    sys.modules['diagnostic_msgs'] = MagicMock()
diag_msg_mock = MagicMock()
diag_msg_mock.KeyValue = DummyKeyValue
diag_msg_mock.DiagnosticStatus = DummyDiagnosticStatus
diag_msg_mock.DiagnosticArray = DummyDiagnosticArray
sys.modules['diagnostic_msgs.msg'] = diag_msg_mock

from robopy_controller.nodes.waveshare_motor_driver import WaveshareMotorDriver

class TestYawFusionAndSCurve(unittest.TestCase):

    def setUp(self):
        with patch('threading.Thread'), patch('serial.Serial'):
            self.driver = WaveshareMotorDriver()
            self.driver.serial_conn = MagicMock()
            self.driver.serial_conn.is_open = True
            self.driver.logger = MagicMock()
            self.driver.get_logger = MagicMock(return_value=self.driver.logger)
            self.driver.sent_cmds = []

            def mock_write(b):
                line = b.decode('utf-8').strip()
                for part in line.split('\n'):
                    part = part.strip()
                    if part and part.startswith('{'):
                        try:
                            self.driver.sent_cmds.append(json.loads(part))
                        except Exception:
                            pass
            self.driver.serial_conn.write = mock_write

    def test_01_scurve_ramping_continuity_and_finite_jerk(self):
        """Verify that step command produces smooth C^1 acceleration ramp with bounded jerk."""
        # Initial state: at rest
        self.driver.current_duty_left = 0.0
        self.driver.current_duty_right = 0.0
        self.driver.duty_accel_left = 0.0
        self.driver.duty_accel_right = 0.0
        self.driver.last_duty_update_time = time.time() - 0.02

        dt = 0.02
        target_v = 0.30 # target linear velocity
        duty_history = []
        accel_history = []

        # Run 15 simulation steps (0.30s total)
        current_t = time.time()
        for i in range(15):
            current_t += dt
            with patch('time.time', return_value=current_t):
                self.driver.send_speeds(target_v, target_v)
                duty_history.append(self.driver.current_duty_left)
                accel_history.append(self.driver.duty_accel_left)

        # 1. Acceleration must start near 0 and grow smoothly
        self.assertGreater(accel_history[0], 0.0)
        self.assertLessEqual(accel_history[0], self.driver.max_duty_accel)

        # 2. Jerk (rate of change of accel) must never exceed max_duty_jerk * 1.05 (allowing numerical delta)
        for k in range(1, len(accel_history)):
            jerk = abs(accel_history[k] - accel_history[k-1]) / dt
            self.assertLessEqual(jerk, self.driver.max_duty_jerk + 0.1,
                f"Jerk spike at step {k}: {jerk} > {self.driver.max_duty_jerk}")

        # 3. Duty must monotonically increase towards target duty
        target_duty = self.driver.speed_to_duty(target_v)
        self.assertGreater(duty_history[-1], duty_history[0], "Duty must ramp upwards")
        self.assertLessEqual(duty_history[-1], target_duty + 0.001)

    def test_02_scurve_immediate_stop_clamp(self):
        """Verify that commanding zero speed immediately halts duty and cancels acceleration."""
        self.driver.current_duty_left = 0.60
        self.driver.current_duty_right = 0.60
        self.driver.duty_accel_left = 3.5
        self.driver.duty_accel_right = 3.5

        self.driver.send_speeds(0.0, 0.0)

        self.assertEqual(self.driver.current_duty_left, 0.0, "Left duty must clamp to 0 immediately on stop")
        self.assertEqual(self.driver.current_duty_right, 0.0, "Right duty must clamp to 0 immediately on stop")
        self.assertEqual(self.driver.duty_accel_left, 0.0, "Left accel must reset on stop")
        self.assertEqual(self.driver.duty_accel_right, 0.0, "Right accel must reset on stop")

    def test_03_stationary_gyro_auto_bias_tracking(self):
        """When stopped, stationary gyro readings update chassis_yaw_bias via EMA."""
        self.driver.motors_stopped = True
        self.driver.chassis_yaw_bias = 0.0

        # Simulate 20 IMU packets with stationary bias of 1.15 deg/s (~0.02 rad/s)
        bias_rad = 0.02
        bias_deg = math.degrees(bias_rad)

        imu_data = {
            "T": 1001,
            "ax": 0.0, "ay": 0.0, "az": 9.81,
            "gx": 0.0, "gy": bias_deg, "gz": 0.0,
            "roll": 0.0, "pitch": 0.0, "yaw": 0.0
        }

        for _ in range(20):
            self.driver.process_imu_feedback(roll=0.0, pitch=0.0, yaw=0.0, ax=0.0, ay=0.0, az=9.81, gx=0.0, gy=bias_deg, gz=0.0)

        # Bias should converge towards bias_rad (0.02 rad/s)
        self.assertGreater(self.driver.chassis_yaw_bias, 0.010,
            f"Expected chassis_yaw_bias to track bias_rad, got {self.driver.chassis_yaw_bias}")
        self.assertLessEqual(self.driver.chassis_yaw_bias, 0.022)

        # Now simulate motion: yaw rate must subtract the estimated bias
        self.driver.motors_stopped = False
        # Robot is rotating CCW at 10 deg/s + bias
        rot_deg = 10.0 + bias_deg
        self.driver.process_imu_feedback(roll=0.0, pitch=0.0, yaw=0.0, ax=0.0, ay=0.0, az=9.81, gx=0.0, gy=rot_deg, gz=0.0)

        expected_rate = math.radians(10.0)
        self.assertAlmostEqual(self.driver.chassis_yaw_rate, expected_rate, delta=0.01)

    def test_04_complementary_yaw_fusion(self):
        """Verify blending of wheel differential rotation and chassis gyro rotation."""
        self.driver.motors_stopped = False
        self.driver.enable_chassis_yaw_fusion = True
        self.driver.yaw_fusion_alpha = 0.80
        self.driver.theta = 0.0
        self.driver.last_odom_time = time.time()

        # Provide a fresh chassis gyro rate: 0.50 rad/s CCW
        now = time.time()
        self.driver.chassis_yaw_rate = 0.50
        self.driver.last_chassis_imu_time = now

        # Initialize encoder baseline
        self.driver.process_encoder_feedback(1000, 1000)

        # Advance time by 0.10s
        t_next = now + 0.10
        with patch.object(self.driver, 'get_clock') as mock_clock:
            mock_clock.return_value.now.return_value.nanoseconds = int(t_next * 1e9)
            
            # Encoder ticks: left -20, right +20 (pure CCW rotation)
            self.driver.process_encoder_feedback(980, 1020)

            # Check that theta is positive (CCW turn) and scaled properly
            self.assertGreater(self.driver.theta, 0.040)
            self.assertLess(self.driver.theta, 0.060)

    def test_05_graceful_fallback_on_stale_imu(self):
        """When chassis IMU is stale (>0.25s), fusion must fall back 100% to wheel odometry."""
        self.driver.motors_stopped = False
        self.driver.enable_chassis_yaw_fusion = True
        self.driver.yaw_fusion_alpha = 0.88
        self.driver.theta = 0.0

        # IMU packet received 2.0 seconds ago (stale!)
        self.driver.chassis_yaw_rate = 9.99  # bogus stale rate
        self.driver.last_chassis_imu_time = time.time() - 2.0

        # Baseline
        self.driver.process_encoder_feedback(2000, 2000)

        # Move straight forward: left +50, right +50 (delta_theta_wheel = 0.0)
        self.driver.process_encoder_feedback(2050, 2050)

        # Theta must remain 0.0 (not corrupted by the 9.99 rad/s stale gyro)
        self.assertAlmostEqual(self.driver.theta, 0.0, places=4,
            msg="Stale IMU must not corrupt heading; must fall back to wheel odometry")

    def test_06_dynamic_parameter_reconfiguration(self):
        """Verify on-line updating of parameters via parameter_callback."""
        class ParamMock:
            def __init__(self, name, value):
                self.name = name
                self.value = value

        params = [
            ParamMock('enable_chassis_yaw_fusion', False),
            ParamMock('yaw_fusion_alpha', 0.55),
            ParamMock('max_duty_accel', 7.5),
            ParamMock('max_duty_jerk', 35.0),
        ]

        res = self.driver.parameter_callback(params)
        self.assertTrue(res.successful)
        self.assertFalse(self.driver.enable_chassis_yaw_fusion)
        self.assertEqual(self.driver.yaw_fusion_alpha, 0.55)
        self.assertEqual(self.driver.max_duty_accel, 7.5)
        self.assertEqual(self.driver.max_duty_jerk, 35.0)

if __name__ == '__main__':
    unittest.main()
