"""
Unit test suite for FM-NAV-018 (360° LiDAR coverage and collision mitigation).
Validates configuration of RPLIDAR C1, Nav2 costmaps, TF2 transforms,
RTAB-Map scan subscription, and DFMEA database closure.
"""

import os
import unittest
import yaml


class TestFMNav018LidarCoverage(unittest.TestCase):
    """Test suite validating complete 360-degree LiDAR mitigation for FM-NAV-018"""

    def setUp(self):
        self.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

    def test_nav2_local_costmap_scan_source(self):
        """Verify that local_costmap uses /scan with marking and clearing enabled"""
        params_file = os.path.join(self.root_dir, 'robopy_controller', 'config', 'nav2_params_jazzy.yaml')
        self.assertTrue(os.path.exists(params_file), f"nav2_params_jazzy.yaml not found at {params_file}")

        with open(params_file, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)

        local_params = cfg['local_costmap']['local_costmap']['ros__parameters']
        self.assertIn('obstacle_layer', local_params['plugins'])
        
        obs_layer = local_params['obstacle_layer']
        self.assertTrue(obs_layer.get('enabled'), "Local costmap obstacle_layer is not enabled")
        self.assertIn('scan', obs_layer.get('observation_sources', ''), "Local obstacle_layer does not observe 'scan'")
        
        scan_cfg = obs_layer.get('scan', {})
        self.assertEqual(scan_cfg.get('topic'), '/scan', "Local costmap scan topic must be /scan")
        self.assertTrue(scan_cfg.get('marking'), "Local costmap scan marking must be true")
        self.assertTrue(scan_cfg.get('clearing'), "Local costmap scan clearing must be true")
        self.assertEqual(scan_cfg.get('data_type'), 'LaserScan', "Local costmap scan data_type must be LaserScan")

    def test_nav2_global_costmap_scan_source(self):
        """Verify that global_costmap uses /scan for long-range planning"""
        params_file = os.path.join(self.root_dir, 'robopy_controller', 'config', 'nav2_params_jazzy.yaml')
        with open(params_file, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)

        global_params = cfg['global_costmap']['global_costmap']['ros__parameters']
        self.assertIn('obstacle_layer', global_params['plugins'])
        
        obs_layer = global_params['obstacle_layer']
        self.assertTrue(obs_layer.get('enabled'), "Global costmap obstacle_layer is not enabled")
        self.assertIn('scan', obs_layer.get('observation_sources', ''), "Global obstacle_layer does not observe 'scan'")
        
        scan_cfg = obs_layer.get('scan', {})
        self.assertEqual(scan_cfg.get('topic'), '/scan', "Global costmap scan topic must be /scan")
        self.assertTrue(scan_cfg.get('marking'), "Global costmap scan marking must be true")
        self.assertTrue(scan_cfg.get('clearing'), "Global costmap scan clearing must be true")

    def test_restart_hailo_sllidar_and_tf(self):
        """Verify restart_hailo.sh launches sllidar_node and calibrates TF2 base_link -> laser"""
        script_file = os.path.join(self.root_dir, 'restart_hailo.sh')
        self.assertTrue(os.path.exists(script_file), f"restart_hailo.sh not found at {script_file}")

        with open(script_file, 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn('sllidar_ros2 sllidar_node', content, "sllidar_node invocation missing in restart_hailo.sh")
        self.assertIn('/dev/rplidar', content, "Serial port /dev/rplidar missing in restart_hailo.sh")
        self.assertIn('460800', content, "Baudrate 460800 missing in restart_hailo.sh")
        self.assertIn('--child-frame-id laser', content, "TF child frame 'laser' missing in restart_hailo.sh")
        self.assertIn('--yaw 3.14159265', content, "TF yaw rotation 180° missing in restart_hailo.sh")

    def test_rtabmap_lidar_subscription(self):
        """Verify that RTAB-Map SLAM subscribes to 360° laser scan for 2D ICP"""
        rtab_file = os.path.join(self.root_dir, 'robopy_controller', 'config', 'rtabmap.yaml')
        self.assertTrue(os.path.exists(rtab_file), f"rtabmap.yaml not found at {rtab_file}")

        with open(rtab_file, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)

        params = cfg['rtabmap']['ros__parameters']
        self.assertTrue(params.get('subscribe_scan'), "RTAB-Map subscribe_scan must be true")
        self.assertIn(str(params.get('Reg/Strategy')), ['1', '2'], "RTAB-Map Reg/Strategy must be 1 (ICP) or 2 (Visual+ICP)")
        self.assertEqual(str(params.get('RGBD/ProximityAngle')), '360', "RTAB-Map proximity angle must be 360 degrees")

    def test_dfmea_status_closed_and_low_residual_rpn(self):
        """Verify FM-NAV-018 in dfmea.yaml is CLOSED with RPN <= 9"""
        dfmea_file = os.path.join(self.root_dir, 'fmea', 'dfmea.yaml')
        self.assertTrue(os.path.exists(dfmea_file), f"dfmea.yaml not found at {dfmea_file}")

        with open(dfmea_file, 'r', encoding='utf-8') as f:
            fms = yaml.safe_load(f)

        fm18 = next((x for x in fms if x.get('id') == 'FM-NAV-018'), None)
        self.assertIsNotNone(fm18, "FM-NAV-018 not found in dfmea.yaml")
        self.assertEqual(fm18.get('mitigation_status'), 'CLOSED', "FM-NAV-018 status must be CLOSED")
        
        residual = fm18.get('residual_scoring', {})
        self.assertLessEqual(residual.get('rpn', 999), 9, f"FM-NAV-018 residual RPN must be <= 9, got {residual.get('rpn')}")
        self.assertIn('ECO-2026-09-04-001', fm18.get('eco_ref', ''), "FM-NAV-018 eco_ref must reference ECO-2026-09-04-001")

    def test_improvement_index_and_file_exist(self):
        """Verify FM-NAV-018 is registered in IMPROVEMENT_INDEX.yaml and file exists"""
        index_file = os.path.join(self.root_dir, 'fmea', 'IMPROVEMENT_INDEX.yaml')
        self.assertTrue(os.path.exists(index_file), f"IMPROVEMENT_INDEX.yaml not found at {index_file}")

        with open(index_file, 'r', encoding='utf-8') as f:
            index = yaml.safe_load(f)

        improvements = index.get('improvements', {})
        self.assertIn('FM-NAV-018', improvements, "FM-NAV-018 missing from IMPROVEMENT_INDEX.yaml")
        
        info = improvements['FM-NAV-018']
        self.assertEqual(info.get('status'), 'COMPLETED', "FM-NAV-018 status in index must be COMPLETED")
        
        doc_rel_path = info.get('file', '')
        doc_full_path = os.path.join(self.root_dir, doc_rel_path)
        self.assertTrue(os.path.exists(doc_full_path), f"Improvement file {doc_full_path} not found")


if __name__ == '__main__':
    unittest.main()
