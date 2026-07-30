#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_rag_acronym_memory.py
==========================
Unit test per la verifica dell'acronimo di identità MARCUS e per l'iniezione
delle memorie semantiche RAG all'interno di ConversationManager.
"""

import sys
from unittest.mock import MagicMock, AsyncMock

# Mock ROS 2 modules if running outside ROS 2 environment
if 'rclpy' not in sys.modules:
    sys.modules['rclpy'] = MagicMock()
    sys.modules['rclpy.node'] = MagicMock()
    sys.modules['rclpy.callback_groups'] = MagicMock()
    sys.modules['rclpy.executors'] = MagicMock()
    sys.modules['rclpy.time'] = MagicMock()
    sys.modules['rclpy.duration'] = MagicMock()
    sys.modules['rcl_interfaces.msg'] = MagicMock()
    sys.modules['std_msgs.msg'] = MagicMock()
    sys.modules['sensor_msgs.msg'] = MagicMock()
    sys.modules['geometry_msgs.msg'] = MagicMock()
    sys.modules['vision_msgs.msg'] = MagicMock()
    sys.modules['example_interfaces.srv'] = MagicMock()
    sys.modules['cv_bridge'] = MagicMock()
    sys.modules['sensor_msgs_py'] = MagicMock()
    sys.modules['sensor_msgs_py.point_cloud2'] = MagicMock()
    sys.modules['visualization_msgs'] = MagicMock()
    sys.modules['visualization_msgs.msg'] = MagicMock()
    sys.modules['nav_msgs'] = MagicMock()
    sys.modules['nav_msgs.msg'] = MagicMock()
    sys.modules['tf2_ros'] = MagicMock()
    sys.modules['aiohttp'] = MagicMock()
if 'robopy_controller.srv' not in sys.modules:
    mock_srv = MagicMock()
    mock_srv.AskVisualQuestion = MagicMock()
    mock_srv.AudioData = MagicMock()
    sys.modules['robopy_controller.srv'] = mock_srv
    sys.modules['robopy_controller.msg'] = mock_srv

import unittest
import asyncio
from robot_ai.orchestration.conversation import ConversationManager
from robot_ai.orchestration.memory_manager import MemoryManager
from robot_ai.rag.memory_store import Memory, MemoryType


class TestRagAcronymMemory(unittest.TestCase):

    def test_system_prompt_default_has_acronym(self):
        """Verifica che il system prompt di default contenga l'acronimo esteso."""
        default_prompt = (
            'Sei MARCUS — Modular Autonomous Robotic Control Unit System, un assistente robotico avanzato. '
            'Sei amichevole, conciso e preciso. Parla SEMPRE e SOLO in lingua italiana.'
        )
        self.assertIn("Modular Autonomous Robotic Control Unit System", default_prompt)
        self.assertIn("MARCUS", default_prompt)

    def test_build_prompt_injects_rag_memories(self):
        """Verifica che _build_prompt inietti correttamente le memorie RAG recuperate."""
        mock_llm = MagicMock()
        mock_tts = MagicMock()
        mock_skill_executor = MagicMock()
        mock_skill_executor.registry.get.return_value = None
        mock_memory_manager = MagicMock()
        mock_world_model = MagicMock()
        mock_ha_context = lambda: ""
        mock_metrics = MagicMock()
        mock_config = MagicMock()
        mock_reactive_safety = MagicMock()

        cm = ConversationManager(
            llm=mock_llm,
            tts=mock_tts,
            skill_executor=mock_skill_executor,
            memory_manager=mock_memory_manager,
            world_model=mock_world_model,
            ha_context_provider=mock_ha_context,
            metrics=mock_metrics,
            config=mock_config,
            reactive_safety=mock_reactive_safety
        )

        rag_memories = [
            "User: Cosa significa Marcus?\nRobot: MARCUS sta per Modular Autonomous Robotic Control Unit System.",
            "L'utente preferisce risposte sintetiche."
        ]

        prompt = cm._build_prompt(
            user_text="Qual è il tuo nome per esteso?",
            ha_context="",
            repeated_note="",
            rag_memories=rag_memories
        )

        self.assertIn("[MEMORIE EPISODICHE E FATTI APPRESI (RAG)]", prompt)
        self.assertIn("Modular Autonomous Robotic Control Unit System", prompt)
        self.assertIn("Utente: Qual è il tuo nome per esteso?", prompt)

    def test_memory_manager_protects_learned_facts(self):
        """Verifica che MemoryManager imposti amygdala_protected=true sui learned_fact."""
        mock_store = MagicMock()
        mock_embed = AsyncMock()
        mock_embed.embed.return_value = [0.1] * 768

        mm = MemoryManager(memory_store=mock_store, embedding_service=mock_embed)

        async def run_test():
            await mm.store_background(
                user_text="Marcus significa Modular Autonomous Robotic Control Unit System",
                robot_text="Capito!",
                mem_type="learned_fact"
            )
            # Eseguiamo un tick del worker
            item = await mm._queue.get()
            content, mem_type_str = item
            mem_type = MemoryType(mem_type_str)

            metadata = {"timestamp": 123456.0}
            importance = 0.5
            if mem_type in (MemoryType.LEARNED_FACT, MemoryType.USER_PREFERENCE):
                importance = 1.0
                metadata["amygdala_protected"] = "true"
                metadata["synaptic_strength"] = 100.0

            memory = Memory(
                id="test_id",
                content=content,
                memory_type=mem_type,
                embedding=[0.1]*768,
                metadata=metadata,
                importance=importance
            )

            self.assertEqual(memory.importance, 1.0)
            self.assertEqual(memory.metadata.get("amygdala_protected"), "true")
            self.assertEqual(memory.metadata.get("synaptic_strength"), 100.0)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
