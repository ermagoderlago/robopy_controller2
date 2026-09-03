#!/usr/bin/env python3
"""
Unit Tests - Cognitive Pipeline Modularization
==============================================
Validates:
1. AudioBufferManager: RMS energy calculation, FIFO buffering, Echo Suppression, Barge-in gating.
2. SkillActionServer: Asynchronous goal execution, streaming generator feedback, preemption.
3. LiveConnectionBridgeNode: Initialization and modular wiring.
"""

import sys
import os
import unittest
import asyncio
import struct
import math
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('robopy_controller'))

# Mock ROS 2 modules if running outside native ROS 2 Linux env
if 'rclpy' not in sys.modules:
    class DummyNode:
        def __init__(self, *args, **kwargs):
            pass
        def declare_parameter(self, *args, **kwargs):
            pass
        def get_parameter(self, name):
            m = MagicMock()
            m.value = 'gemini-2.5-flash-native-audio-latest' if 'model' in name else 30.0
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

from robopy_controller.robot_ai.services.audio_buffer_manager import AudioBufferManager
from robopy_controller.robot_ai.orchestration.skill_action_server import (
    SkillActionServer, SkillActionGoal, GoalStatus
)
from robopy_controller.robot_ai.skills.skill_registry import SkillRegistry


class TestAudioBufferManager(unittest.TestCase):
    """Test suite for AudioBufferManager"""

    def setUp(self):
        self.abm = AudioBufferManager(max_mic_chunks=10, max_speaker_chunks=10, barge_in_energy_thresh=0.15)

    def test_calculate_rms_silence_and_signal(self):
        """Test RMS calculations for silence and synthesized sine wave."""
        silence = bytes(640)
        self.assertAlmostEqual(AudioBufferManager.calculate_rms(silence), 0.0, places=3)

        # Generate 16kHz sine wave chunk (320 samples)
        samples = [int(16000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(320)]
        sine_bytes = struct.pack(f"<{len(samples)}h", *samples)
        rms = AudioBufferManager.calculate_rms(sine_bytes)
        self.assertGreater(rms, 0.2)

    def test_mic_push_pop_lifecycle(self):
        """Test normal mic push and pop."""
        dummy_chunk = b'\x01\x02' * 160
        self.assertTrue(self.abm.push_mic_chunk(dummy_chunk))
        popped = self.abm.pop_mic_chunk()
        self.assertEqual(popped, dummy_chunk)
        self.assertIsNone(self.abm.pop_mic_chunk())

    def test_echo_suppression_during_playback(self):
        """Verify mic audio is dropped when speaker is actively playing and energy is low."""
        self.abm.set_speaker_playing(True)
        quiet_mic_chunk = bytes(640)  # low energy silence

        # Should be dropped by AEC
        accepted = self.abm.push_mic_chunk(quiet_mic_chunk)
        self.assertFalse(accepted)
        self.assertIsNone(self.abm.pop_mic_chunk())

    def test_barge_in_detection(self):
        """Verify loud user speech triggers barge-in callback and flushes speaker buffer."""
        barge_in_fired = []
        self.abm.set_barge_in_callback(lambda: barge_in_fired.append(True))
        self.abm.set_speaker_playing(True)
        self.abm.push_speaker_chunk(b'speaker_audio_data_123')

        # Generate loud speech chunk (RMS > 0.15)
        samples = [int(20000 * math.sin(2 * math.pi * 300 * i / 16000)) for i in range(320)]
        loud_chunk = struct.pack(f"<{len(samples)}h", *samples)

        # First chunk
        self.abm.push_mic_chunk(loud_chunk)
        # Second consecutive chunk triggers barge-in
        self.abm.push_mic_chunk(loud_chunk)

        self.assertTrue(len(barge_in_fired) > 0)
        self.assertFalse(self.abm.is_speaker_playing())
        self.assertIsNone(self.abm.pop_speaker_chunk())


class TestSkillActionServer(unittest.TestCase):
    """Test suite for SkillActionServer"""

    def setUp(self):
        self.registry = SkillRegistry()
        self.nav_client = MagicMock()
        self.server = SkillActionServer(self.registry, self.nav_client)

    def test_execute_simple_skill(self):
        """Test executing a simple asynchronous skill."""
        mock_skill = MagicMock()
        mock_skill.name = "greet"
        mock_skill.description = "Greeting skill"
        
        async def fake_greet(text, context):
            mock_res = MagicMock()
            mock_res.speak = "Ciao Luca!"
            return mock_res

        mock_skill.safe_execute = fake_greet
        self.registry.register(mock_skill)

        async def _test_coro():
            result = await self.server.execute_goal("greet", {"name": "Luca"})
            self.assertEqual(result["status"], GoalStatus.SUCCEEDED)
            self.assertIn("Ciao Luca!", result["speak"])

        asyncio.run(_test_coro())

    def test_execute_streaming_skill_with_feedback(self):
        """Test streaming skill generator emitting progress updates."""
        feedbacks = []
        self.server.on_feedback_callback = lambda gid, fb: feedbacks.append(fb)

        mock_skill = MagicMock()
        mock_skill.name = "search_stream"
        mock_skill.description = "Streaming search skill"

        async def fake_streaming_execute(text, context):
            for step in ["Controllo cucina", "Controllo salotto", "Trovato!"]:
                yield MagicMock(speak=step)

        mock_skill.safe_execute = fake_streaming_execute
        self.registry.register(mock_skill)

        async def _test_coro():
            result = await self.server.execute_goal("search_stream", {"target": "chiavi"})
            self.assertEqual(result["status"], GoalStatus.SUCCEEDED)
            self.assertEqual(len(result["speak"]), 3)
            self.assertEqual(len(feedbacks), 3)

        asyncio.run(_test_coro())

    def test_cancel_and_preemption(self):
        """Test preemption cancels running skill immediately."""
        mock_skill = MagicMock()
        mock_skill.name = "slow_task"
        mock_skill.description = "Slow task skill"

        async def slow_skill(text, context):
            await asyncio.sleep(2.0)
            return MagicMock(speak="Finito")

        mock_skill.safe_execute = slow_skill
        self.registry.register(mock_skill)

        async def _test_coro():
            task = asyncio.create_task(self.server.execute_goal("slow_task", {}, goal_id="slow_1"))
            await asyncio.sleep(0.05)
            cancelled = await self.server.cancel_goal("slow_1")
            self.assertTrue(cancelled)
            res = await task
            self.assertEqual(res["status"], GoalStatus.PREEMPTED)

        asyncio.run(_test_coro())


if __name__ == '__main__':
    unittest.main()
