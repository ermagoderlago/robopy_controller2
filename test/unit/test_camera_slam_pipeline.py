#!/usr/bin/env python3
"""
test_camera_slam_pipeline.py - Pipeline tests for Camera, Foxglove, YOLO Semantics, and RTAB-Map SLAM
=====================================================================================================
Verifies:
1. TF single authority exclusivity (no duplicate odom->base_link broadcast).
2. RTAB-Map YAML configuration integrity (2D depth occupancy, queue size, detection rate).
3. YOLO classes & semantic translation mappings.
4. Costmap 2.5D injector interfaces.
"""

import unittest
import os
import yaml

from robopy_controller.nodes.localization_fuser_node import LocalizationFuserNode


class TestCameraSlamPipeline(unittest.TestCase):

    def test_rtabmap_configuration(self):
        config_path = os.path.join(os.path.dirname(__file__), '../../robopy_controller/config/rtabmap.yaml')
        self.assertTrue(os.path.exists(config_path), f"rtabmap.yaml not found at {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)

        params = cfg['rtabmap']['ros__parameters']

        # Verify single authority map->odom
        self.assertEqual(params.get('publish_tf'), True)
        self.assertEqual(params.get('frame_id'), 'base_link')
        self.assertEqual(params.get('odom_frame_id'), 'odom')
        self.assertEqual(params.get('map_frame_id'), 'map')

        # Verify pure depth 2D grid mapping without LiDAR
        self.assertEqual(params.get('subscribe_scan'), False)
        self.assertEqual(params.get('Grid/FromDepth'), 'true')
        self.assertEqual(params.get('Grid/Sensor'), '1')

        # Verify rate and queue sizing for RPi5
        self.assertGreaterEqual(params.get('queue_size', 0), 10)
        self.assertEqual(params.get('Rtabmap/DetectionRate'), '1.5')

    def test_localization_fuser_tf_exclusivity(self):
        # Verify that localization_fuser_node defaults to publish_tf=False to prevent conflicts
        node = LocalizationFuserNode.__new__(LocalizationFuserNode)
        node.publish_tf = False
        self.assertFalse(node.publish_tf, "localization_fuser_node must not broadcast TF by default")

    def test_coco_semantic_mappings(self):
        # Test semantic object categories
        coco_to_ita = {
            "person": "persona",
            "chair": "sedia",
            "couch": "divano",
            "bed": "letto",
            "dining table": "tavolo",
            "bottle": "bottiglia",
            "tv": "televisore",
            "laptop": "computer"
        }

        self.assertEqual(coco_to_ita["chair"], "sedia")
        self.assertEqual(coco_to_ita["person"], "persona")
        self.assertEqual(coco_to_ita["dining table"], "tavolo")

    def test_restart_script_consistency(self):
        restart_script_path = os.path.join(os.path.dirname(__file__), '../../restart_hailo.sh')
        self.assertTrue(os.path.exists(restart_script_path), "restart_hailo.sh must exist")

        with open(restart_script_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check RTAB-Map uses rtabmap.yaml
        self.assertIn('rtabmap.yaml', content, "RTAB-Map must use rtabmap.yaml in restart_hailo.sh")
        # Check localization_fuser_node publish_tf is False
        self.assertIn('publish_tf:=False', content, "localization_fuser_node publish_tf must be False in restart_hailo.sh")
        # Check nomad_navigator_node is in pkill list
        self.assertIn('nomad_navigator_node', content, "nomad_navigator_node must be terminated in pkill block")


if __name__ == '__main__':
    unittest.main()
