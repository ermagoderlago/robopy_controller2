#!/usr/bin/env python3
"""
Unit Test - AMCL 2D Localization & Map Server Integration (Opzione A - FM-NAV-030)
==================================================================================
Validates:
1. Configuration integrity of map_server and amcl in nav2_params_jazzy.yaml.
2. Launch arguments and conditional lifecycle nodes in custom_nav2_launch.py.
3. Realistic dynamic covariances in waveshare_motor_driver.py.
4. Mutually exclusive TF authority between AMCL and RTAB-Map in restart_hailo.sh.
5. Availability of scripts/save_map.sh tooling.
"""

import sys
import os
import unittest
import yaml

sys.path.insert(0, os.path.abspath('.'))


class TestNav2AmclConfig(unittest.TestCase):
    """Test suite for AMCL & Map Server configuration in nav2_params_jazzy.yaml"""

    def setUp(self):
        self.yaml_path = os.path.join('robopy_controller', 'config', 'nav2_params_jazzy.yaml')
        self.assertTrue(os.path.exists(self.yaml_path), f"File {self.yaml_path} not found")
        with open(self.yaml_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

    def test_map_server_params(self):
        """Verify map_server parameters are correctly configured"""
        self.assertIn('map_server', self.config, "Missing map_server block in nav2_params_jazzy.yaml")
        params = self.config['map_server']['ros__parameters']
        self.assertIn('yaml_filename', params)
        self.assertIn('topic_name', params)
        self.assertEqual(params['topic_name'], 'map')
        self.assertEqual(params['frame_id'], 'map')

    def test_amcl_params(self):
        """Verify amcl parameters for RPLIDAR C1 360° ToF on Raspberry Pi 5"""
        self.assertIn('amcl', self.config, "Missing amcl block in nav2_params_jazzy.yaml")
        params = self.config['amcl']['ros__parameters']
        
        # Topic and Frame IDs
        self.assertEqual(params['scan_topic'], '/scan')
        self.assertEqual(params['global_frame_id'], 'map')
        self.assertEqual(params['odom_frame_id'], 'odom')
        self.assertEqual(params['base_frame_id'], 'base_link')
        self.assertTrue(params['tf_broadcast'], "AMCL must broadcast TF map->odom")
        
        # Motion Model
        self.assertEqual(params['robot_model_type'], 'nav2_amcl::DifferentialMotionModel')
        
        # Sensor Model
        self.assertEqual(params['laser_model_type'], 'likelihood_field')
        self.assertLessEqual(params['laser_min_range'], 0.10)
        self.assertGreaterEqual(params['laser_max_range'], 8.0)
        
        # Particle filter sizing for RPi 5 4GB RAM
        self.assertGreaterEqual(params['min_particles'], 200)
        self.assertLessEqual(params['max_particles'], 4000)
        
        # Dynamic update thresholds
        self.assertLessEqual(params['update_min_d'], 0.10, "Update linear delta should be <= 10cm")
        self.assertLessEqual(params['update_min_a'], 0.10, "Update angular delta should be <= 0.10 rad")


class TestCustomNav2Launch(unittest.TestCase):
    """Test suite for custom_nav2_launch.py integration"""

    def setUp(self):
        self.launch_path = os.path.join('launch', 'custom_nav2_launch.py')
        self.assertTrue(os.path.exists(self.launch_path), f"File {self.launch_path} not found")
        with open(self.launch_path, 'r', encoding='utf-8') as f:
            self.content = f.read()

    def test_launch_arguments(self):
        """Verify enable_amcl and map arguments are declared"""
        self.assertIn("enable_amcl = LaunchConfiguration('enable_amcl')", self.content)
        self.assertIn("map_yaml_file = LaunchConfiguration('map')", self.content)
        self.assertIn("DeclareLaunchArgument(\n        'enable_amcl'", self.content)
        self.assertIn("DeclareLaunchArgument(\n        'map'", self.content)

    def test_conditional_localization_nodes(self):
        """Verify map_server, amcl and lifecycle_manager_localization are present"""
        self.assertIn("executable='map_server'", self.content)
        self.assertIn("executable='amcl'", self.content)
        self.assertIn("lifecycle_manager_localization", self.content)
        self.assertIn("condition=IfCondition(enable_amcl)", self.content)


class TestMotorDriverCovariance(unittest.TestCase):
    """Test suite for realistic differential drive odometry covariances"""

    def setUp(self):
        self.driver_path = os.path.join('robopy_controller', 'nodes', 'waveshare_motor_driver.py')
        self.assertTrue(os.path.exists(self.driver_path), f"File {self.driver_path} not found")
        with open(self.driver_path, 'r', encoding='utf-8') as f:
            self.content = f.read()

    def test_realistic_yaw_covariance(self):
        """Verify yaw covariance is not rigidly set to 1e-5 during motion"""
        self.assertNotIn("pose_cov = 1e-5", self.content, "1e-5 is overly rigid for differential drive motion")
        self.assertIn("pose_cov_yaw = 0.02", self.content, "Realistic yaw covariance (~8 deg) must be used during motion")
        self.assertIn("pose_cov_xy = 1e-4", self.content)


class TestRestartHailoScript(unittest.TestCase):
    """Test suite for restart_hailo.sh launch coordination"""

    def setUp(self):
        self.script_path = 'restart_hailo.sh'
        self.assertTrue(os.path.exists(self.script_path), f"File {self.script_path} not found")
        with open(self.script_path, 'r', encoding='utf-8') as f:
            self.content = f.read()

    def test_amcl_flag_and_rtabmap_tf_isolation(self):
        """Verify --amcl flag disables RTAB-Map publish_tf to prevent REP-105 conflict"""
        self.assertIn("--amcl)", self.content)
        self.assertIn("USE_AMCL=\"true\"", self.content)
        self.assertIn("-p publish_tf:=false -p Mem/IncrementalMemory:=false", self.content)
        self.assertIn("enable_amcl:=$USE_AMCL", self.content)


class TestSaveMapScript(unittest.TestCase):
    """Test suite for save_map.sh tooling"""

    def test_save_map_script_exists(self):
        script_path = os.path.join('scripts', 'save_map.sh')
        self.assertTrue(os.path.exists(script_path), f"Tooling {script_path} must exist")
        with open(script_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn("map_saver_cli", content)
        self.assertIn("map_subscribe_transient_local:=true", content)


if __name__ == '__main__':
    unittest.main()
