#!/usr/bin/env python3
"""
Unit Test - Battery Monitoring & INA219 Telemetry (SPEC-06 & FM-SYS-003/004)
=============================================================================
Validates:
1. waveshare_motor_driver: converts INA219 millivolts (e.g. 11400mV -> 11.40V, 12800mV -> 12.80V).
2. waveshare_motor_driver: parses INA219 current (e.g. 1500mA -> 1.5A) and sets bat_msg.current.
3. waveshare_motor_driver: detects external power charging (V >= 12.70V -> CHARGING, V < 12.70V -> DISCHARGING).
4. battery_manager_node: normalizes millivolt samples into FIFO buffer.
5. battery_manager_node: transitions to CHARGING (12.80V) with 100% SoC and propagates current.
"""

import sys
import os
import math
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
            'charging_threshold_voltage': 12.70,
            'esp32_adc_scale_factor': 2880.95,
            'enable_esp32_pid': False,
            'esp32_pid_kp': 3.20,
            'esp32_pid_ki': 0.22,
            'esp32_pid_kd': 0.04,
            'battery_state_topic': '/battery_state',
            'foxglove_pct_topic': '/foxglove/battery_pct',
            'foxglove_status_topic': '/foxglove/power_status',
            'docking_trigger_topic': '/robot/docking/trigger',
            'undock_trigger_topic': '/robot/docking/undock_trigger',
            'shutdown_topic': '/robot/system/shutdown',
            'speed_limit_topic': '/speed_limit',
            'legacy_voltage_topic': '/motor/battery_voltage',
            'charging_threshold_voltage': 12.70,
            'charging_bus_voltage': 12.80,
            'full_voltage': 12.60,
            'nominal_voltage': 11.10,
            'eco_voltage': 10.20,
            'docking_voltage': 9.90,
            'shutdown_voltage': 9.00,
            'filter_window_size': 20,
            'sample_rate_hz': 5.0,
            'persistence_sec': 3.0,
            'speed_limit_eco_pct': 50.0,
            'auto_poweroff': False,
            'total_capacity_ah': 6.80,
            'battery_chemistry': 'NCR18650B_3S2P',
            'internal_resistance_ohm': 0.085,
            'base_quiescent_current_a': 1.20,
            'use_ocv_table': True,
            'charger_current_a': 1.50,
            'full_charge_soc_thresh': 0.98,
            'post_dock_verify_sec': 5.0,
            'post_dock_min_voltage': 12.45,
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
        m.now().to_msg().sec = 1
        m.now().to_msg().nanosec = 0
        return m

for mod in [
    'rclpy', 'rclpy.node', 'rclpy.callback_groups', 'rclpy.action', 'rclpy.executors',
    'rclpy.time', 'rclpy.duration', 'rclpy.qos',
    'geometry_msgs', 'geometry_msgs.msg',
    'nav_msgs', 'nav_msgs.msg',
    'sensor_msgs', 'sensor_msgs.msg',
    'diagnostic_msgs', 'diagnostic_msgs.msg',
    'std_msgs', 'std_msgs.msg',
    'tf2_ros', 'tf_transformations',
    'nav2_msgs', 'nav2_msgs.msg'
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

sys.modules['rclpy.node'].Node = DummyNode
sys.modules['sensor_msgs'].msg = sys.modules['sensor_msgs.msg']
sys.modules['diagnostic_msgs'].msg = sys.modules['diagnostic_msgs.msg']
sys.modules['geometry_msgs'].msg = sys.modules['geometry_msgs.msg']
sys.modules['nav_msgs'].msg = sys.modules['nav_msgs.msg']
sys.modules['std_msgs'].msg = sys.modules['std_msgs.msg']


# BatteryState mock definition
class MockBatteryState:
    POWER_SUPPLY_STATUS_UNKNOWN = 0
    POWER_SUPPLY_STATUS_CHARGING = 1
    POWER_SUPPLY_STATUS_DISCHARGING = 2
    POWER_SUPPLY_STATUS_NOT_CHARGING = 3
    POWER_SUPPLY_STATUS_FULL = 4
    POWER_SUPPLY_TECHNOLOGY_LIPO = 3
    POWER_SUPPLY_TECHNOLOGY_LION = 2
    def __init__(self):
        self.header = MagicMock()
        self.voltage = 0.0
        self.current = float('nan')
        self.percentage = 0.0
        self.charge = 0.0
        self.capacity = 0.0
        self.design_capacity = 0.0
        self.power_supply_status = 2
        self.power_supply_technology = 3
        self.present = True


sys.modules['sensor_msgs.msg'].BatteryState = MockBatteryState
sys.modules['sensor_msgs'].msg.BatteryState = MockBatteryState

class MockKeyValue:
    def __init__(self, key="", value=""):
        self.key = key
        self.value = value

class MockDiagnosticStatus:
    OK = 0
    WARN = 1
    ERROR = 2
    def __init__(self):
        self.name = ""
        self.level = 0
        self.message = ""
        self.hardware_id = ""
        self.values = []
class MockDiagnosticArray:
    def __init__(self):
        self.header = MagicMock()
        self.status = []

sys.modules['diagnostic_msgs.msg'].KeyValue = MockKeyValue
sys.modules['diagnostic_msgs.msg'].DiagnosticStatus = MockDiagnosticStatus
sys.modules['diagnostic_msgs.msg'].DiagnosticArray = MockDiagnosticArray
sys.modules['diagnostic_msgs'].msg.KeyValue = MockKeyValue
sys.modules['diagnostic_msgs'].msg.DiagnosticStatus = MockDiagnosticStatus
sys.modules['diagnostic_msgs'].msg.DiagnosticArray = MockDiagnosticArray


from robopy_controller.nodes.waveshare_motor_driver import WaveshareMotorDriver
from robopy_controller.nodes.battery_manager_node import BatteryManagerNode


class TestBatteryMonitoring(unittest.TestCase):
    def setUp(self):
        import robopy_controller.nodes.waveshare_motor_driver as wmd
        import robopy_controller.nodes.battery_manager_node as bmn
        wmd.BatteryState.POWER_SUPPLY_STATUS_CHARGING = 1
        wmd.BatteryState.POWER_SUPPLY_STATUS_DISCHARGING = 2
        wmd.BatteryState.POWER_SUPPLY_STATUS_FULL = 4
        bmn.BatteryState.POWER_SUPPLY_STATUS_CHARGING = 1
        bmn.BatteryState.POWER_SUPPLY_STATUS_DISCHARGING = 2
        bmn.BatteryState.POWER_SUPPLY_STATUS_FULL = 4

        with patch('serial.Serial') as mock_serial:
            self.mock_serial_instance = MagicMock()
            self.mock_serial_instance.is_open = True
            mock_serial.return_value = self.mock_serial_instance
            self.driver = WaveshareMotorDriver()
            self.driver.raw_battery_pub = MagicMock()
            self.driver.raw_battery_float_pub = MagicMock()
            self.driver.diag_pub = MagicMock()

        self.bms = BatteryManagerNode()
        self.bms.pub_battery_state = MagicMock()
        self.bms.pub_foxglove_pct = MagicMock()
        self.bms.pub_foxglove_status = MagicMock()
        self.bms.pub_docking_trigger = MagicMock()
        self.bms.pub_undock_trigger = MagicMock()


    def test_waveshare_driver_discharging_telemetry(self):
        """Test that INA219 11400mV is parsed as 11.40V and marked DISCHARGING."""
        self.driver.process_battery_feedback(11400, 1250)
        self.assertAlmostEqual(self.driver.latest_voltage, 11.40, places=2)
        
        self.driver.raw_battery_pub.publish.assert_called()
        bat_msg = self.driver.raw_battery_pub.publish.call_args[0][0]
        self.assertAlmostEqual(bat_msg.voltage, 11.40, places=2)
        self.assertAlmostEqual(bat_msg.current, 1.25, places=2)
        self.assertEqual(bat_msg.power_supply_status, MockBatteryState.POWER_SUPPLY_STATUS_DISCHARGING)

    def test_waveshare_driver_charging_telemetry(self):
        """Test that external power 12800mV is parsed as 12.80V and marked CHARGING."""
        self.driver.process_battery_feedback(12800, 2100)
        self.assertAlmostEqual(self.driver.latest_voltage, 12.80, places=2)
        
        self.driver.raw_battery_pub.publish.assert_called()
        bat_msg = self.driver.raw_battery_pub.publish.call_args[0][0]
        self.assertAlmostEqual(bat_msg.voltage, 12.80, places=2)
        self.assertAlmostEqual(bat_msg.current, 2.10, places=2)
        self.assertEqual(bat_msg.power_supply_status, MockBatteryState.POWER_SUPPLY_STATUS_CHARGING)

    def test_battery_manager_sample_normalization(self):
        """Test that BatteryManager normalizes INA219 millivolts to Volts."""
        self.bms._insert_raw_sample(11100.0, 1.5)
        self.assertAlmostEqual(self.bms.latest_raw_voltage, 11.10, places=2)
        self.assertAlmostEqual(self.bms.latest_current, 1.5, places=2)

    def test_battery_manager_charging_in_progress(self):
        """Test that BatteryManager estimates CC-CV charge progress and sets CHARGING status."""
        self.bms.last_discharging_soc = 0.50
        for _ in range(20):
            self.bms._insert_raw_sample(12800.0, 2.0)
            
        self.bms._control_and_publish_loop()
        self.assertTrue(self.bms.is_charging)
        self.assertIn("IN CARICA", self.bms.current_state_str)
        self.assertFalse(self.bms.undock_triggered)
        
        self.bms.pub_battery_state.publish.assert_called()
        pub_bat = self.bms.pub_battery_state.publish.call_args[0][0]
        self.assertAlmostEqual(pub_bat.voltage, 12.80, places=2)
        self.assertEqual(pub_bat.power_supply_status, MockBatteryState.POWER_SUPPLY_STATUS_CHARGING)
        self.assertAlmostEqual(pub_bat.current, 2.0, places=2)

    def test_battery_manager_charging_full_and_undock_trigger(self):
        """Test that when battery reaches >=98% during charging, undock trigger fires."""
        for _ in range(20):
            self.bms._insert_raw_sample(12800.0, 2.0)
        self.bms.is_charging = True
        self.bms.estimated_charging_soc = 0.99
            
        self.bms._control_and_publish_loop()
        self.assertTrue(self.bms.is_charging)
        self.assertEqual(self.bms.current_state_str, "CARICA COMPLETA (100%)")
        self.assertTrue(self.bms.undock_triggered)
        
        self.bms.pub_battery_state.publish.assert_called()
        pub_bat = self.bms.pub_battery_state.publish.call_args[0][0]
        self.assertEqual(pub_bat.power_supply_status, MockBatteryState.POWER_SUPPLY_STATUS_FULL)
        self.assertAlmostEqual(pub_bat.percentage, 0.99, places=2)

        self.bms.pub_undock_trigger.publish.assert_called()
        undock_msg = self.bms.pub_undock_trigger.publish.call_args[0][0]
        self.assertTrue(undock_msg.data)

    def test_battery_manager_post_dock_verification_success(self):
        """Test that disconnecting from dock initiates verification window, validating 12.55V as 100%."""
        import time
        self.bms.is_charging = True
        
        # Robot disconnects: voltage drops to 12.55V (fully charged NCR18650B resting voltage)
        for _ in range(20):
            self.bms._insert_raw_sample(12550.0, 0.0)
            
        # Tick 1: verification window starts
        self.bms._control_and_publish_loop()
        self.assertFalse(self.bms.is_charging)
        self.assertIn("VERIFICA CARICA", self.bms.current_state_str)
        
        # Advance clock past 5 seconds verification window (using time.monotonic())
        self.bms.post_dock_verify_start = time.monotonic() - 6.0
        self.bms._control_and_publish_loop()
        
        self.assertTrue(self.bms.post_dock_verification_success)
        self.assertEqual(self.bms.current_state_str, "CARICA VERIFICATA OK (100%)")

    def test_battery_manager_post_dock_verification_incomplete(self):
        """Test that disconnecting with lower resting voltage (11.50V) flags incomplete charge."""
        import time
        self.bms.is_charging = True
        
        # Robot disconnects prematurely: resting voltage only 11.50V (< 12.45V threshold)
        for _ in range(20):
            self.bms._insert_raw_sample(11500.0, 0.0)
            
        # Advance clock past verification window (using time.monotonic())
        self.bms._control_and_publish_loop()
        self.bms.post_dock_verify_start = time.monotonic() - 6.0
        self.bms._control_and_publish_loop()
        
        self.assertFalse(self.bms.post_dock_verification_success)
        self.assertEqual(self.bms.current_state_str, "BATTERIA OK")
        # SoC based on 11.50V table lookup (~60%)
        self.assertAlmostEqual(self.bms.latest_raw_voltage, 11.50, places=2)

    def test_battery_manager_discharging_state_transition(self):
        """Test that BatteryManager computes correct SoC and marks DISCHARGING when on battery."""
        for _ in range(20):
            self.bms._insert_raw_sample(11100.0, 1.2)
            
        self.bms._control_and_publish_loop()
        self.assertFalse(self.bms.is_charging)
        self.assertEqual(self.bms.current_state_str, "BATTERIA OK")
        
        self.bms.pub_battery_state.publish.assert_called()
        pub_bat = self.bms.pub_battery_state.publish.call_args[0][0]
        self.assertAlmostEqual(pub_bat.voltage, 11.10, places=2)
        # Panasonic NCR18650B 3S OCV with IR compensation (~52.6% SoC, between 0.45 and 0.55)
        self.assertGreater(pub_bat.percentage, 0.45)
        self.assertLess(pub_bat.percentage, 0.55)
        self.assertEqual(pub_bat.power_supply_status, MockBatteryState.POWER_SUPPLY_STATUS_DISCHARGING)
        self.assertAlmostEqual(pub_bat.capacity, 6.80, places=2)
        self.assertAlmostEqual(pub_bat.design_capacity, 6.80, places=2)
        self.assertAlmostEqual(pub_bat.charge, pub_bat.percentage * 6.80, places=2)
        self.assertEqual(pub_bat.power_supply_technology, MockBatteryState.POWER_SUPPLY_TECHNOLOGY_LION)



if __name__ == '__main__':
    unittest.main()
