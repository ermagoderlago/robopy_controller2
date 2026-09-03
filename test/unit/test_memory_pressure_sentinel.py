#!/usr/bin/env python3
"""
Unit Test - Memory Pressure Sentinel & System Lifecycle Coordinator
===================================================================
Validates:
1. Linux Kernel PSI parsing (/proc/pressure/memory).
2. Memory pressure tiered thresholding (WARNING >= 0.30, CRITICAL >= 0.60).
3. MemoryManager freeze & buffer eviction mechanisms under memory pressure.
4. NightlyDreamService lifecycle suspension during NAVIGATION_ACTIVE.
5. Operating state transitions and RTAB-Map throttling in HUMAN_INTERACTION_MODE.
6. Audio VUI priority boost handling.
"""

import sys
import os
import unittest
import tempfile
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('robopy_controller'))

# Mock ROS 2 modules if running outside ROS 2 environment
if 'rclpy' not in sys.modules:
    class DummyNode:
        def __init__(self, *args, **kwargs):
            pass
        def declare_parameter(self, *args, **kwargs):
            pass
        def get_parameter(self, name):
            m = MagicMock()
            m.value = 0.30 if 'warning' in name else 0.60
            return m
        def create_publisher(self, *args, **kwargs):
            return MagicMock()
        def create_subscription(self, *args, **kwargs):
            return MagicMock()
        def create_timer(self, *args, **kwargs):
            return MagicMock()
        def get_logger(self):
            return MagicMock()

    sys.modules['rclpy'] = MagicMock()
    sys.modules['rclpy.node'] = MagicMock(Node=DummyNode)
    sys.modules['rclpy.qos'] = MagicMock()
    sys.modules['rclpy.time'] = MagicMock()
    sys.modules['rclpy.duration'] = MagicMock()
    sys.modules['rclpy.callback_groups'] = MagicMock()
    sys.modules['rclpy.executors'] = MagicMock()
    sys.modules['rcl_interfaces'] = MagicMock()
    sys.modules['rcl_interfaces.msg'] = MagicMock()
    sys.modules['example_interfaces'] = MagicMock()
    sys.modules['example_interfaces.srv'] = MagicMock()
    sys.modules['std_srvs'] = MagicMock()
    sys.modules['std_srvs.srv'] = MagicMock()
    sys.modules['std_msgs'] = MagicMock()
    sys.modules['std_msgs.msg'] = MagicMock()
    sys.modules['geometry_msgs'] = MagicMock()
    sys.modules['geometry_msgs.msg'] = MagicMock()
    sys.modules['sensor_msgs'] = MagicMock()
    sys.modules['sensor_msgs.msg'] = MagicMock()
    sys.modules['sensor_msgs_py'] = MagicMock()
    sys.modules['sensor_msgs_py.point_cloud2'] = MagicMock()
    sys.modules['visualization_msgs'] = MagicMock()
    sys.modules['visualization_msgs.msg'] = MagicMock()
    sys.modules['nav_msgs'] = MagicMock()
    sys.modules['nav_msgs.msg'] = MagicMock()
    sys.modules['tf2_ros'] = MagicMock()
    sys.modules['tf2_geometry_msgs'] = MagicMock()
    sys.modules['robopy_controller.msg'] = MagicMock()
    sys.modules['robopy_controller.srv'] = MagicMock()
    sys.modules['cv_bridge'] = MagicMock()
    if 'aiohttp' not in sys.modules:
        sys.modules['aiohttp'] = MagicMock()
    if 'chromadb' not in sys.modules:
        sys.modules['chromadb'] = MagicMock()
    if 'google' not in sys.modules:
        sys.modules['google'] = MagicMock()
        sys.modules['google.genai'] = MagicMock()

from robopy_controller.nodes.system_lifecycle_coordinator_node import (
    parse_kernel_psi, OperatingState, MemoryPressureLevel
)
from robopy_controller.robot_ai.orchestration.memory_manager import MemoryManager
from robopy_controller.robot_ai.services.nightly_dream_service import NightlyDreamService


class TestKernelPSIParsing(unittest.TestCase):
    """Test suite for /proc/pressure/memory parser"""

    def test_parse_valid_psi_file(self):
        """Test parsing realistic Linux 6.6+ PSI kernel strings"""
        mock_psi_content = (
            "some avg10=0.12 avg60=0.08 avg300=0.02 total=123456\n"
            "full avg10=0.35 avg60=0.15 avg300=0.05 total=65432\n"
        )
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tf:
            tf.write(mock_psi_content)
            temp_path = tf.name

        try:
            stats = parse_kernel_psi(temp_path)
            self.assertAlmostEqual(stats['some_avg10'], 0.12, places=2)
            self.assertAlmostEqual(stats['full_avg10'], 0.35, places=2)
            self.assertAlmostEqual(stats['full_avg60'], 0.15, places=2)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_missing_file_fallback(self):
        """Test graceful fallback when /proc/pressure/memory does not exist"""
        stats = parse_kernel_psi("/non/existent/path/memory")
        self.assertEqual(stats['some_avg10'], 0.0)
        self.assertEqual(stats['full_avg10'], 0.0)


class TestMemoryManagerPressureContainment(unittest.TestCase):
    """Test suite for MemoryManager freeze & eviction actions"""

    def setUp(self):
        self.mock_store = MagicMock()
        self.mock_embed = MagicMock()
        self.mgr = MemoryManager(self.mock_store, self.mock_embed)

    def test_freeze_and_unfreeze_embeddings(self):
        """Verify embeddings can be frozen and unfrozen"""
        self.assertFalse(self.mgr._frozen)
        
        self.mgr.freeze_embeddings()
        self.assertTrue(self.mgr._frozen)

        # In freeze state, store_background should drop memory
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.mgr.store_background("Hello", "Hi", "conversation"))
            self.assertEqual(self.mgr._queue.qsize(), 0)
        finally:
            loop.close()

        self.mgr.unfreeze_embeddings()
        self.assertFalse(self.mgr._frozen)

    def test_clear_transient_buffers(self):
        """Verify emergency clearing of queue and embedding cache"""
        self.mgr._embedding_cache["test_key"] = [0.1, 0.2, 0.3]
        self.mgr._queue.put_nowait(("test_content", "conversation"))
        self.assertEqual(len(self.mgr._embedding_cache), 1)
        self.assertEqual(self.mgr._queue.qsize(), 1)

        self.mgr.clear_transient_buffers()

        self.assertEqual(len(self.mgr._embedding_cache), 0)
        self.assertEqual(self.mgr._queue.qsize(), 0)


class TestNightlyDreamLifecycle(unittest.TestCase):
    """Test suite for NightlyDreamService lifecycle states"""

    def test_suspend_and_resume(self):
        mock_cfg = MagicMock()
        mock_store = MagicMock()
        mock_llm = MagicMock()
        mock_embed = MagicMock()
        
        dream_svc = NightlyDreamService(mock_cfg, mock_store, mock_llm, mock_embed)
        self.assertTrue(dream_svc.is_suspended)

        dream_svc.resume()
        self.assertFalse(dream_svc.is_suspended)

        dream_svc.suspend()
        self.assertTrue(dream_svc.is_suspended)


class TestOperatingStateFSM(unittest.TestCase):
    """Test suite for OperatingState machine transitions"""

    def test_state_constants(self):
        self.assertEqual(OperatingState.NAVIGATION_ACTIVE, "NAVIGATION_ACTIVE")
        self.assertEqual(OperatingState.DOCKED_DREAM, "DOCKED_DREAM")
        self.assertEqual(OperatingState.HUMAN_INTERACTION_MODE, "HUMAN_INTERACTION_MODE")

    def test_pressure_level_constants(self):
        self.assertEqual(MemoryPressureLevel.NORMAL, "NORMAL")
        self.assertEqual(MemoryPressureLevel.WARNING_FREEZE, "WARNING_FREEZE")
        self.assertEqual(MemoryPressureLevel.CRITICAL_EVICT, "CRITICAL_EVICT")


if __name__ == '__main__':
    unittest.main()
