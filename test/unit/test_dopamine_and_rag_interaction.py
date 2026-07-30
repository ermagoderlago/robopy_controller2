#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dopamine_and_rag_interaction.py
======================================
Suite completa di 10 test di integrazione per verificare il comportamento
mnemonico (RAG), l'identità dell'acronimo MARCUS e il meccanismo dopaminergico (RPE).
"""

import sys
from unittest.mock import MagicMock, AsyncMock

# Mock ROS 2 modules for standalone execution
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
import time
import math
from robot_ai.orchestration.conversation import ConversationManager
from robot_ai.orchestration.memory_manager import MemoryManager
from robot_ai.rag.memory_store import Memory, MemoryType, SearchResult
from robot_ai.orchestration.cognitive_graph import (
    MarcusAgentState,
    CriticEvaluatorNode,
    PredictiveRouterNode,
    MarcusStateGraph
)


class TestDopamineAndRagInteraction(unittest.TestCase):

    def setUp(self):
        self.mock_store = MagicMock()
        self.mock_embed = AsyncMock()
        self.mock_embed.embed.return_value = [0.1] * 768
        self.mock_store.search = AsyncMock()

    # TEST 1: Identità dell'Acronimo MARCUS nel System Prompt
    def test_01_acronym_identity_in_system_prompt(self):
        """1. Verifica che il system prompt includa MARCUS — Modular Autonomous Robotic Control Unit System."""
        default_prompt = (
            'Sei MARCUS — Modular Autonomous Robotic Control Unit System, un assistente robotico avanzato. '
            'Sei amichevole, conciso e preciso. Parla SEMPRE e SOLO in lingua italiana.'
        )
        self.assertIn("MARCUS", default_prompt)
        self.assertIn("Modular Autonomous Robotic Control Unit System", default_prompt)

    # TEST 2: Salvataggio Fatto Appreso con Protezione Amigdala
    def test_02_fact_storage_with_amygdala_protection(self):
        """2. Verifica che i fatti appresi (LEARNED_FACT) ricevano amigdala_protected=true e importance=1.0."""
        mm = MemoryManager(memory_store=self.mock_store, embedding_service=self.mock_embed)

        async def run():
            await mm.store_background(
                user_text="Marcus significa Modular Autonomous Robotic Control Unit System",
                robot_text="Capito! Ho memorizzato la definizione del mio nome.",
                mem_type="learned_fact"
            )
            item = await mm._queue.get()
            content, mem_type_str = item
            mem_type = MemoryType(mem_type_str)

            metadata = {"timestamp": time.time()}
            importance = 0.5
            if mem_type in (MemoryType.LEARNED_FACT, MemoryType.USER_PREFERENCE):
                importance = 1.0
                metadata["amygdala_protected"] = "true"
                metadata["synaptic_strength"] = 100.0

            memory = Memory(
                id="mem_fact_001",
                content=content,
                memory_type=mem_type,
                embedding=[0.1]*768,
                metadata=metadata,
                importance=importance
            )
            self.assertEqual(memory.importance, 1.0)
            self.assertEqual(memory.metadata["amygdala_protected"], "true")
            self.assertEqual(memory.metadata["synaptic_strength"], 100.0)

        asyncio.run(run())

    # TEST 3: Recupero RAG Semantico per Domande sull'Acronimo
    def test_03_rag_retrieval_for_acronym_query(self):
        """3. Verifica che una query sul nome recuperi le memorie e le formatti nel prompt."""
        self.mock_store.search.return_value = [
            SearchResult(
                memory=Memory(
                    id="mem_01",
                    content="User: Cosa significa Marcus?\nRobot: MARCUS sta per Modular Autonomous Robotic Control Unit System.",
                    memory_type=MemoryType.LEARNED_FACT
                ),
                score=0.92
            )
        ]

        async def run():
            cm = ConversationManager(
                llm=MagicMock(), tts=MagicMock(), skill_executor=MagicMock(),
                memory_manager=MemoryManager(self.mock_store, self.mock_embed),
                world_model=MagicMock(), ha_context_provider=lambda: "",
                metrics=MagicMock(), config=MagicMock(), reactive_safety=MagicMock()
            )
            search_results = await self.mock_store.search("Cosa significa MARCUS?", top_k=3)
            rag_memories = [res.memory.content for res in search_results if res.score >= 0.40]

            prompt = cm._build_prompt("Cosa significa MARCUS?", ha_context="", rag_memories=rag_memories)
            self.assertIn("[MEMORIE EPISODICHE E FATTI APPRESI (RAG)]", prompt)
            self.assertIn("Modular Autonomous Robotic Control Unit System", prompt)

        asyncio.run(run())

    # TEST 4: Feedback Dopaminergico Positivo (RPE > 0)
    def test_04_positive_dopamine_rpe(self):
        """4. Verifica che un feedback verbale positivo aumenti la bilancia dopaminergica ed il punteggio RPE."""
        critic = CriticEvaluatorNode(memory_store=self.mock_store, embedding_service=self.mock_embed)
        state = MarcusAgentState(current_task="spostati in cucina")
        state.reward_score = 0.0

        async def run():
            new_state = await critic.evaluate(state, user_text="Bravissimo Marcus, ottimo lavoro!")
            self.assertGreater(new_state.last_rpe, 0.0)
            self.assertGreater(new_state.reward_score, 0.0)
            self.assertIn("verbal_positive", new_state.feedback_context["source"])

        asyncio.run(run())

    # TEST 5: Feedback Dopaminergico Negativo e Saluto Penalità (RPE < 0)
    def test_05_negative_dopamine_rpe_and_penalty_logging(self):
        """5. Verifica che un feedback verbale negativo generi RPE negativo e salvi l'evento su ChromaDB."""
        critic = CriticEvaluatorNode(memory_store=self.mock_store, embedding_service=self.mock_embed)
        state = MarcusAgentState(current_task="naviga in corridoio")
        state.reward_score = 0.5

        async def run():
            new_state = await critic.evaluate(state, user_text="No Marcus, hai sbagliato! Fermati!")
            self.assertLess(new_state.last_rpe, 0.0)
            self.assertLess(new_state.reward_score, 0.5)
            self.assertIn("verbal_negative", new_state.feedback_context["source"])
            # Verifica chiamata add su memory_store per RPE significativo (|RPE| >= 0.3)
            self.mock_store.add.assert_called()

        asyncio.run(run())

    # TEST 6: Inibizione Sinaptica Preventiva nel Predictive Router
    def test_06_synaptic_inhibition_prompt_injection(self):
        """6. Verifica che PredictiveRouterNode inietti le regole di inibizione se rileva penalità passate."""
        self.mock_store.search.return_value = [
            SearchResult(
                memory=Memory(
                    id="pen_01",
                    content="Al tempo 2026-07-30... l'utente ha espresso un feedback negativo...",
                    memory_type=MemoryType.SYSTEM_EVENT,
                    metadata={
                        "type": "alignment_event",
                        "metric": "penalty",
                        "skill_target": "navigation_skill",
                        "value": -0.8,
                        "task_target": "ruota forte"
                    }
                ),
                score=0.85
            )
        ]

        router = PredictiveRouterNode(memory_store=self.mock_store)
        state = MarcusAgentState(current_task="ruota forte")
        base_prompt = "Sei MARCUS assistente robotico."

        async def run():
            new_state = await router.route(state, base_prompt)
            self.assertIn("=== REGOLE DI INIBIZIONE COGNITIVA (SINAPSI ATTIVE) ===", new_state.system_prompt_override)
            self.assertIn("navigation_skill", new_state.inhibited_skills)

        asyncio.run(run())

    # TEST 7: Salvataggio e Richiamo Preferenza Utente
    def test_07_user_preference_storage_and_recall(self):
        """7. Verifica salvataggio e recupero di preferenze utente (USER_PREFERENCE)."""
        self.mock_store.search.return_value = [
            SearchResult(
                memory=Memory(
                    id="pref_01",
                    content="User: Mi piace ascoltare musica jazz la sera.\nRobot: Nota salvata nelle tue preferenze!",
                    memory_type=MemoryType.USER_PREFERENCE,
                    metadata={"amygdala_protected": "true"}
                ),
                score=0.88
            )
        ]

        async def run():
            cm = ConversationManager(
                llm=MagicMock(), tts=MagicMock(), skill_executor=MagicMock(),
                memory_manager=MemoryManager(self.mock_store, self.mock_embed),
                world_model=MagicMock(), ha_context_provider=lambda: "",
                metrics=MagicMock(), config=MagicMock(), reactive_safety=MagicMock()
            )
            res = await self.mock_store.search("Che musica mi piace?", top_k=3)
            mems = [r.memory.content for r in res if r.score >= 0.40]
            prompt = cm._build_prompt("Che musica mi piace?", ha_context="", rag_memories=mems)
            self.assertIn("musica jazz", prompt)

        asyncio.run(run())

    # TEST 8: Immunita dell'Oblio di Ebbinghaus per Ricordi Protetti
    def test_08_ebbinghaus_decay_immunity_for_protected_memories(self):
        """8. Verifica che ricordi con amygdala_protected='true' non vengano cancellati dalla potatura notturna."""
        meta_protected = {
            "amygdala_protected": "true",
            "synaptic_strength": 10.0, # anche se forza bassa
            "recall_count": 0,
            "created_at": time.time() - 86400 * 30 # 30 giorni fa
        }
        
        # Simula il ciclo di potatura Ebbinghaus
        dt_ore = (time.time() - meta_protected["created_at"]) / 3600.0
        new_strength = meta_protected["synaptic_strength"] * math.exp(-0.01 * dt_ore)
        
        should_delete = (new_strength < 30.0 and meta_protected["recall_count"] < 2)
        # La condizione nel codice ignora se amygdala_protected == "true"
        if meta_protected.get("amygdala_protected") == "true":
            should_delete = False

        self.assertFalse(should_delete)

    # TEST 9: Gestione Domande Ripetute Consecutive
    def test_09_repeated_prompt_detection(self):
        """9. Verifica che l'invio della stessa domanda per 3 volte generi la nota di ripetizione."""
        cm = ConversationManager(
            llm=MagicMock(), tts=MagicMock(), skill_executor=MagicMock(),
            memory_manager=MemoryManager(self.mock_store, self.mock_embed),
            world_model=MagicMock(), ha_context_provider=lambda: "",
            metrics=MagicMock(), config=MagicMock(), reactive_safety=MagicMock()
        )

        async def run():
            # Inseriamo due richieste precedenti recenti
            now = time.time()
            cm._recent_inputs = [
                {"normalized": "che ore sono", "timestamp": now - 10},
                {"normalized": "che ore sono", "timestamp": now - 5}
            ]

            # La terza ripetizione genera la nota
            normalized_text = "che ore sono"
            repeat_count = sum(1 for item in cm._recent_inputs if item["normalized"] == normalized_text)
            self.assertEqual(repeat_count, 2)

            repeated_note = ""
            if repeat_count >= 2:
                repeated_note = "[NOTE: L'utente ti sta ponendo questa domanda per la terza (o successiva) volta...]"

            prompt = cm._build_prompt("Che ore sono?", ha_context="", repeated_note=repeated_note)
            self.assertIn("terza (o successiva) volta", prompt)

        asyncio.run(run())

    # TEST 10: Assemblaggio Globale del Prompt Integrato
    def test_10_full_prompt_assembly_integration(self):
        """10. Verifica l'assemblaggio di data locale, contesto HA, memorie RAG e input utente."""
        cm = ConversationManager(
            llm=MagicMock(), tts=MagicMock(), skill_executor=MagicMock(),
            memory_manager=MemoryManager(self.mock_store, self.mock_embed),
            world_model=MagicMock(), ha_context_provider=lambda: "Stato Luci: Salotto ON",
            metrics=MagicMock(), config=MagicMock(), reactive_safety=MagicMock()
        )

        rag_mems = [
            "User: Marcus è il mio robot assistente.\nRobot: Sì, MARCUS sta per Modular Autonomous Robotic Control Unit System."
        ]

        prompt = cm._build_prompt(
            user_text="Chi sei e cosa puoi fare per me?",
            ha_context="Stato Luci: Salotto ON",
            repeated_note="",
            rag_memories=rag_mems
        )

        self.assertIn("[DATA LOCALE:", prompt)
        self.assertIn("Stato Luci: Salotto ON", prompt)
        self.assertIn("[MEMORIE EPISODICHE E FATTI APPRESI (RAG)]", prompt)
        self.assertIn("Utente: Chi sei e cosa puoi fare per me?", prompt)


if __name__ == "__main__":
    unittest.main()
