#!/usr/bin/env python3
"""
Unit Test Suite for MPPI Telemetry & Offline Autotuner (FM-NAV-008)
===================================================================
Tests telemetry calculations, angular jitter, cross-track error computation,
and offline optimization logic.
"""

import os
import sys
import json
import unittest
import tempfile
import numpy as np

# Add project root to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from robopy_controller.nodes.mppi_offline_autotuner import MPPIOfflineAutotuner


class TestMPPIAutotuner(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_filepath = os.path.join(self.temp_dir.name, 'test_telemetry.jsonl')
        self.config_filepath = os.path.join(self.temp_dir.name, 'test_nav2_params.yaml')

        # Dummy YAML configuration
        dummy_yaml = {
            'controller_server': {
                'ros__parameters': {
                    'FollowPath': {
                        'PathAlign': {'cost_weight': 10.0},
                        'Obstacle': {'cost_weight': 15.0}
                    }
                }
            },
            'local_costmap': {
                'local_costmap': {
                    'ros__parameters': {
                        'inflation_layer': {
                            'inflation_radius': 0.45,
                            'cost_scaling_factor': 3.0
                        }
                    }
                }
            }
        }

        import yaml
        with open(self.config_filepath, 'w') as f:
            yaml.dump(dummy_yaml, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cost_calculation_empty_telemetry(self):
        autotuner = MPPIOfflineAutotuner(self.log_filepath, self.config_filepath)
        score = autotuner.compute_cost_score([])
        self.assertEqual(score, 0.0)

    def test_cost_calculation_with_synthetic_data(self):
        synthetic_data = [
            {'cross_track_error': 0.05, 'angular_jitter': 0.02, 'stop_and_go_count': 1},
            {'cross_track_error': 0.08, 'angular_jitter': 0.04, 'stop_and_go_count': 1},
            {'cross_track_error': 0.03, 'angular_jitter': 0.01, 'stop_and_go_count': 2}
        ]

        with open(self.log_filepath, 'w') as f:
            for p in synthetic_data:
                f.write(json.dumps(p) + '\n')

        autotuner = MPPIOfflineAutotuner(self.log_filepath, self.config_filepath)
        telemetry = autotuner.load_telemetry()
        self.assertEqual(len(telemetry), 3)

        score = autotuner.compute_cost_score(telemetry)
        self.assertGreater(score, 0.0)

    def test_high_jitter_optimization_trigger(self):
        synthetic_data = [
            {'cross_track_error': 0.02, 'angular_jitter': 0.25, 'stop_and_go_count': 0},
            {'cross_track_error': 0.03, 'angular_jitter': 0.30, 'stop_and_go_count': 0}
        ]

        with open(self.log_filepath, 'w') as f:
            for p in synthetic_data:
                f.write(json.dumps(p) + '\n')

        autotuner = MPPIOfflineAutotuner(self.log_filepath, self.config_filepath)
        telemetry = autotuner.load_telemetry()
        params = autotuner.optimize_parameters(telemetry)

        # High jitter should adjust inflation radius to 0.35 and increase path align weight
        self.assertEqual(params['inflation_radius'], 0.35)
        self.assertEqual(params['cost_scaling_factor'], 4.5)
        self.assertEqual(params['path_align_weight'], 14.0)

    def test_yaml_config_update(self):
        autotuner = MPPIOfflineAutotuner(self.log_filepath, self.config_filepath)
        new_params = {
            'inflation_radius': 0.40,
            'cost_scaling_factor': 4.0,
            'path_align_weight': 12.0,
            'obstacle_weight': 14.0
        }

        success = autotuner.update_config_file(new_params)
        self.assertTrue(success)

        import yaml
        with open(self.config_filepath, 'r') as f:
            updated_cfg = yaml.safe_load(f)

        mppi = updated_cfg['controller_server']['ros__parameters']['FollowPath']
        local_costmap = updated_cfg['local_costmap']['local_costmap']['ros__parameters']['inflation_layer']

        self.assertEqual(mppi['PathAlign']['cost_weight'], 12.0)
        self.assertEqual(mppi['Obstacle']['cost_weight'], 14.0)
        self.assertEqual(local_costmap['inflation_radius'], 0.40)
        self.assertEqual(local_costmap['cost_scaling_factor'], 4.0)


if __name__ == '__main__':
    unittest.main()
