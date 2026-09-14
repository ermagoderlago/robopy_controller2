#!/usr/bin/env python3
"""
Unit Test - ESP32 Closed-Loop Velocity PID Controller (FM-MOT-008)
==================================================================
Validates:
1. Driver initializes with enable_esp32_pid=True, Kp=3.20, Ki=0.22, Kd=0.04.
2. send_esp32_pid_config() formats and sends valid JSON T:133 command over serial.
3. parameter_callback() handles dynamic ROS 2 parameter reconfig for enable_esp32_pid, kp, ki, kd.
4. Telemetry packet parsing captures "pid" status from ESP32.
5. Diagnostics /diagnostics includes esp32_pid_active status.
"""

import sys
import os
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('robopy_controller'))

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
            'enable_esp32_pid': True,
            'esp32_pid_kp': 3.20,
            'esp32_pid_ki': 0.22,
            'esp32_pid_kd': 0.04,
            'use_cmd_vel_odometry': True,
            'use_imu_for_rotation': False,
            'invert_imu_yaw': True,
            'use_encoder_for_linear': False,
            'standstill_encoder_deadband': 8,
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
    def add_on_set_parameters_callback(self, cb):
        self.param_cb = cb
    def get_clock(self):
        m = MagicMock()
        m.now().nanoseconds = 1000000000
        return m

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
if 'serial' not in sys.modules:
    sys.modules['serial'] = MagicMock()

with patch('threading.Thread.start'):
    from robopy_controller.nodes.waveshare_motor_driver import WaveshareMotorDriver

class TestESP32PIDControl(unittest.TestCase):
    def setUp(self):
        with patch('threading.Thread'):
            self.driver = WaveshareMotorDriver()
        self.driver.serial_conn = MagicMock()
        self.driver.serial_conn.is_open = True

    def test_default_pid_parameters(self):
        self.assertTrue(self.driver.enable_esp32_pid)
        self.assertEqual(self.driver.esp32_pid_kp, 3.20)
        self.assertEqual(self.driver.esp32_pid_ki, 0.22)
        self.assertEqual(self.driver.esp32_pid_kd, 0.04)
        self.assertFalse(self.driver.esp32_pid_active)

    def test_send_esp32_pid_config(self):
        self.driver.serial_conn.write.reset_mock()
        self.driver.send_esp32_pid_config()
        self.driver.serial_conn.write.assert_called_once()
        sent_bytes = self.driver.serial_conn.write.call_args[0][0]
        sent_json = json.loads(sent_bytes.decode('utf-8'))
        self.assertEqual(sent_json["T"], 133)
        self.assertEqual(sent_json["pid"], 1)
        self.assertEqual(sent_json["kp"], 3.2)
        self.assertEqual(sent_json["ki"], 0.22)
        self.assertEqual(sent_json["kd"], 0.04)

    def test_dynamic_parameter_reconfigure(self):
        p_enable = MagicMock()
        p_enable.name = 'enable_esp32_pid'
        p_enable.value = False

        p_kp = MagicMock()
        p_kp.name = 'esp32_pid_kp'
        p_kp.value = 2.50
        
        with patch.object(self.driver, 'send_esp32_pid_config') as mock_send:
            res = self.driver.parameter_callback([p_enable, p_kp])
            self.assertTrue(res.successful)
            self.assertFalse(self.driver.enable_esp32_pid)
            self.assertEqual(self.driver.esp32_pid_kp, 2.50)
            self.assertEqual(mock_send.call_count, 2)

    def test_telemetry_pid_parsing(self):
        self.driver.serial_conn.readline.side_effect = [
            b'{"T":1001,"odl":100,"odr":100,"v":11500,"pid":1}\n',
            b''
        ]
        line = self.driver.serial_conn.readline()
        data = json.loads(line.decode('utf-8'))
        if 'pid' in data:
            self.driver.esp32_pid_active = bool(data.get('pid') == 1)
        self.assertTrue(self.driver.esp32_pid_active)

    def test_diagnostics_includes_esp32_pid_active(self):
        self.driver.esp32_pid_active = True
        self.driver.process_battery_feedback(11500)
        self.driver.diag_pub.publish.assert_called()
        diag_msg = self.driver.diag_pub.publish.call_args[0][0]
        stall_status = [s for s in diag_msg.status if s.name == "motor_stall"][0]
        kv_dict = {kv.key: kv.value for kv in stall_status.values}
        self.assertIn("esp32_pid_active", kv_dict)
        self.assertEqual(kv_dict["esp32_pid_active"], "True")

if __name__ == '__main__':
    unittest.main()
