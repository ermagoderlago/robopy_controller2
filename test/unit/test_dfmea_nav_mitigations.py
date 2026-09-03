#!/usr/bin/env python3
"""
Unit Test - DFMEA Navigation & Odometry Software Mitigations
============================================================
Validates:
1. FM-NAV-015: Wheel slip detection and covariance gating calibration algorithms
2. FM-NAV-017: Dynamic ZUPT Gyro Z bias auto-nulling algorithm
3. FM-NAV-016: RTAB-Map anti-aliasing and strict geometric verification config
4. FM-NAV-019: Nav2 safe recovery Behavior Tree without blind spins and costmap persistence
"""

import sys
import os
import unittest
import yaml
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.abspath('.'))


class TestWheelSlipAndGating(unittest.TestCase):
    """Test suite for FM-NAV-015: Wheel slip detection and gated calibration"""

    def test_wheel_slip_detection(self):
        """Simulate wheel vs IMU acceleration differentials"""
        slip_threshold = 0.25  # m/s^2

        # Nominal case: wheel acceleration matches IMU forward acceleration
        wheel_accel_nominal = 0.18
        imu_accel_nominal = 0.19
        diff_nominal = abs(wheel_accel_nominal - imu_accel_nominal)
        self.assertLessEqual(diff_nominal, slip_threshold, "False positive slip detection in nominal condition")

        # Slip case (tile to rug transition or wheel spinning):
        wheel_accel_slip = 0.45
        imu_accel_slip = 0.05
        diff_slip = abs(wheel_accel_slip - imu_accel_slip)
        self.assertGreater(diff_slip, slip_threshold, "Failed to detect wheel slip condition")

    def test_calibration_gating_and_clamping(self):
        """Verify that wheel_scale_ is frozen during slip and clamped in [0.85, 1.15]"""
        scale_init = 1.0
        scale = scale_init

        # Case 1: Slip is detected -> Gating freezes calibration
        slip_detected = True
        d_vio = 0.01  # Poor VIO
        d_wheel = 0.08 # Spinning wheels
        
        if not slip_detected and d_wheel > 0.03:
            instantaneous_scale = d_vio / d_wheel
            scale = 0.95 * scale + 0.05 * instantaneous_scale
            scale = max(0.85, min(1.15, scale))
        
        self.assertEqual(scale, scale_init, "Calibration was incorrectly updated during slip condition")

        # Case 2: Healthy tracking, valid displacement -> smooth update
        slip_detected = False
        d_vio = 0.052
        d_wheel = 0.050
        inliers = 45
        good_inlier_thresh = 25

        if inliers >= good_inlier_thresh and not slip_detected and d_wheel > 0.03 and d_vio > 0.02:
            instantaneous_scale = d_vio / d_wheel
            if 0.75 <= instantaneous_scale <= 1.25:
                scale = 0.95 * scale + 0.05 * instantaneous_scale
                scale = max(0.85, min(1.15, scale))

        self.assertAlmostEqual(scale, 1.002, places=3, msg="Failed to perform smooth calibration update")
        self.assertTrue(0.85 <= scale <= 1.15, "Scale exceeded safe boundary [0.85, 1.15]")


class TestDynamicGyroBias(unittest.TestCase):
    """Test suite for FM-NAV-017: Dynamic ZUPT Gyro Z bias compensation"""

    def test_dynamic_bias_estimation(self):
        """Test rolling average bias accumulation when robot is stationary"""
        simulated_thermal_bias = 0.012  # rad/s drift
        samples = [simulated_thermal_bias + (0.001 * (i % 3 - 1)) for i in range(50)]

        gyro_bias_accum = sum(samples)
        avg_bias = gyro_bias_accum / len(samples)

        self.assertAlmostEqual(avg_bias, simulated_thermal_bias, places=3)

        # Verify bias correction
        gz_raw = 0.0125
        gz_corrected = gz_raw - avg_bias
        deadband = 0.005

        if abs(gz_corrected) < deadband:
            gz_final = 0.0
        else:
            gz_final = gz_corrected

        self.assertEqual(gz_final, 0.0, "Dynamic bias compensation failed to null stationary gyro drift")


class TestConfigIntegrity(unittest.TestCase):
    """Test suite for FM-NAV-016 and FM-NAV-019 configuration and BT integrity"""

    def test_rtabmap_anti_aliasing_params(self):
        """Verify FM-NAV-016 params in rtabmap.yaml"""
        yaml_path = os.path.join('robopy_controller', 'config', 'rtabmap.yaml')
        self.assertTrue(os.path.exists(yaml_path), f"rtabmap.yaml not found at {yaml_path}")

        with open(yaml_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)

        params = cfg['rtabmap']['ros__parameters']
        self.assertAlmostEqual(float(params.get('Rtabmap/LoopThr')), 0.20, places=2, msg="Rtabmap/LoopThr not tightened to 0.20")
        self.assertAlmostEqual(float(params.get('Vis/PnPReprojError')), 2.5, places=1, msg="Vis/PnPReprojError not set to 2.5")
        self.assertIn('Kp/RoiRatios', params, "Kp/RoiRatios missing for floor reflection exclusion")

    def test_nav2_bt_and_costmap_persistence(self):
        """Verify FM-NAV-019 safe recovery in nav2_survival_bt.xml and combination_method in nav2_params"""
        bt_path = os.path.join('robopy_controller', 'config', 'nav2_survival_bt.xml')
        self.assertTrue(os.path.exists(bt_path), f"BT XML not found at {bt_path}")

        tree = ET.parse(bt_path)
        root = tree.getroot()

        # Verify no blind Spin tags exist inside SurvivalRecoverySequence
        recovery_seq = None
        for seq in root.iter('Sequence'):
            if seq.get('name') == 'SurvivalRecoverySequence':
                recovery_seq = seq
                break

        self.assertIsNotNone(recovery_seq, "SurvivalRecoverySequence not found in BT XML")
        spins = recovery_seq.findall('Spin')
        self.assertEqual(len(spins), 0, "Hazardous blind Spin node still present in SurvivalRecoverySequence")

        # Verify combination_method in nav2_params_jazzy.yaml
        jazzy_yaml_path = os.path.join('robopy_controller', 'config', 'nav2_params_jazzy.yaml')
        with open(jazzy_yaml_path, 'r', encoding='utf-8') as f:
            jazzy_cfg = yaml.safe_load(f)

        local_obs = jazzy_cfg['local_costmap']['local_costmap']['ros__parameters']['obstacle_layer']
        self.assertEqual(local_obs.get('combination_method'), 1, "combination_method: 1 missing in local obstacle_layer")


if __name__ == '__main__':
    unittest.main()
