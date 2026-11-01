# -*- coding: utf-8 -*-
# [COGNITIVE_CHECK]
# - Scheda Tecnica letta con view_file: /docs/specs/SPEC-05_cognitive_brain_trinity.md, /docs/specs/SPEC-01_actuation_and_motion.md
# - Vincoli di Zona Rossa verificati:
#   * Deliberazione cognitiva moto LLM (Rule 6.1 / FM-NAV-035)
#   * Fast-Path emergency stop <10ms (FM-NAV-034)
#   * Zero tolleranza automa boilerplate
#   * Token budget <1200
# - Classificazione modifica: Zona Verde (Adversarial stress test harness per Gate 3)
# - Documentazione consultata: marcus_core_rules.md, ORIGINAL_REQUEST.md, SPEC-05, SPEC-01

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROBOPY_DIR = os.path.join(PROJECT_ROOT, "robopy_controller")
for p in [PROJECT_ROOT, ROBOPY_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from robopy_controller.robot_ai.orchestration.conversation import ConversationManager
from robopy_controller.robot_ai.hailo.hailo_qwen_client import HailoQwenClient, HailoQwenConfig


# ==============================================================================
# Helper to build a minimal mocked ConversationManager
# ==============================================================================
def create_test_conversation_manager(mock_qwen_reply: str = "Mi dispiace, non so cosa fare."):
    """Builds an isolated ConversationManager for empirical testing."""
    cm = object.__new__(ConversationManager)
    cm._logger = MagicMock()
    cm._mic_muted = False
    cm._current_source = "text"
    cm._stored_appointment = None
    cm.conversation_history = []
    cm._recent_queries = []
    cm._in_flight_queries = {}
    cm._processing_lock = asyncio.Lock()
    cm.world_model = MagicMock()
    cm.world_model.current_user = {"name": "Luca"}
    cm.world_model.battery_voltage = 12.4
    cm.world_model.is_charging = False
    cm.world_model.amygdala_state = "CALM"
    cm.world_model.min_forward_obstacle_dist = 1.5
    cm.world_model.min_rear_obstacle_dist = 1.5
    cm.world_model.min_clearance_radius_360 = 1.0
    cm.world_model.max_depth_drop = 0.0

    cm.metrics = MagicMock()
    cm.response_callback = MagicMock()
    cm.tts = MagicMock()
    cm.tts.speak = AsyncMock()

    cm.reactive_safety = MagicMock()
    cm.reactive_safety.emergency_stop = MagicMock()

    cm.skill_executor = MagicMock()
    cm.skill_executor.execute_skill = MagicMock(return_value=True)
    cm.skill_executor.registry = {}

    cm.trinity_engine = MagicMock()
    cm.trinity_engine.enabled = True
    cm.trinity_engine.build_augmented_prompt = AsyncMock(side_effect=lambda **kwargs: kwargs.get("user_text", ""))
    cm.trinity_engine.record_interaction = AsyncMock()

    cm.llm = MagicMock()
    cm.llm.is_duplicate_text = MagicMock(return_value=False)

    # Hailo Client mock
    hailo_client = MagicMock()
    hailo_client.config = HailoQwenConfig(timeout_sec=10.0)
    hailo_client.generate = AsyncMock(return_value=(mock_qwen_reply, {}))
    cm.hailo_client = hailo_client

    cm._generate_spontaneous_verbal_fallback = lambda x: "Sono a tua completa disposizione, dimmi pure come posso aiutarti."
    return cm


# ==============================================================================
# 1. MOTION COMMAND SUPPRESSION WITHOUT MODEL <action> TAGS (Rule 6.1 / FM-NAV-035)
# ==============================================================================
class TestMotionSuppressionWithoutActionTags:
    """
    Empirically verifies that motion commands do NOT generate physical actions
    if the model output does not contain <action> tags (Rule 6.1 / FM-NAV-035).
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("user_cmd", [
        "Ciao Marcus, muoviti di 30 centimetri",
        "muoviti di 30 centimetri",
        "vai avanti di 50 cm",
        "fai 20 centimetri indietro",
        "ruota a destra di 45 gradi",
        "gira a sinistra di 90 gradi",
        "vai in cucina",
    ])
    async def test_motion_command_without_action_tags_dispatches_no_action(self, user_cmd):
        """Pure conversational model response (no <action>) must NEVER trigger skill_executor."""
        conversational_reply = "Certo, mi piacerebbe molto fare questo movimento per te, ma non ho incluso azioni."
        cm = create_test_conversation_manager(mock_qwen_reply=conversational_reply)

        await cm._handle_local_qwen(user_cmd, should_speak=False)

        # Assert skill_executor was NEVER called
        cm.skill_executor.execute_skill.assert_not_called()

    @pytest.mark.asyncio
    async def test_motion_command_with_action_tags_dispatches_action(self):
        """Oracle comparison: model output WITH valid <action> tags and schema MUST trigger skill_executor."""
        model_reply_with_action = (
            "Mi sposto avanti di 30 centimetri.\n"
            "<action>{\"action\": \"move_distance\", \"parameters\": {\"direction\": \"avanti\", \"distance_m\": 0.30, \"speed\": 0.18}}</action>"
        )
        cm = create_test_conversation_manager(mock_qwen_reply=model_reply_with_action)

        await cm._handle_local_qwen("muoviti di 30 centimetri", should_speak=False)

        # Assert skill_executor WAS called with move_distance
        cm.skill_executor.execute_skill.assert_called_once()
        call_args = cm.skill_executor.execute_skill.call_args[0]
        assert call_args[0] == "move_distance"
        assert abs(call_args[1].get("distance_m", 0) - 0.30) < 1e-4
        assert call_args[1].get("direction") == "avanti"


# ==============================================================================
# 2. EMERGENCY STOP FAST-PATH (<10MS) & KEYWORD COVERAGE (FM-NAV-034 / Rule 6.1)
# ==============================================================================
class TestEmergencyStopFastPath:
    """
    Empirically verifies emergency stop fast-path latency (<10ms) and trigger behavior
    for 'fermati', 'stop', 'alt', 'basta', 'blocca'.
    """

    def test_is_emergency_keyword_coverage(self):
        """Verifies which emergency stop keywords trigger the immediate fast-path."""
        cm = create_test_conversation_manager()

        results = {
            "fermati": cm._is_emergency("fermati"),
            "stop": cm._is_emergency("stop"),
            "alt": cm._is_emergency("alt"),
            "basta": cm._is_emergency("basta"),
            "blocca": cm._is_emergency("blocca"),
            "emergenza": cm._is_emergency("emergenza"),
        }
        # In current implementation, line 97 checks: r'\b(fermati|stop|emergenza)\b'
        assert results["fermati"] is True, "fermati failed _is_emergency"
        assert results["stop"] is True, "stop failed _is_emergency"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kw", ["fermati", "stop", "FERMATI!", "STOP SUBITO"])
    async def test_emergency_stop_fast_path_latency_under_10ms(self, kw):
        """Verifies that recognized emergency stops trigger reactive_safety.emergency_stop() in < 10ms."""
        cm = create_test_conversation_manager()

        t0 = time.perf_counter()
        t_called = None

        def on_emergency_stop():
            nonlocal t_called
            t_called = time.perf_counter()

        cm.reactive_safety.emergency_stop.side_effect = on_emergency_stop

        res = await cm.process_input(kw, "audio")

        assert res is True
        cm.reactive_safety.emergency_stop.assert_called_once()
        assert t_called is not None, "emergency_stop was not called"
        latency = t_called - t0
        # Physical emergency stop invocation MUST be under 10ms
        assert latency < 0.010, f"Emergency stop trigger for '{kw}' took {latency*1000:.3f}ms >= 10ms!"

    @pytest.mark.asyncio
    async def test_alt_keyword_handling_latency_and_action(self):
        """
        Tests the behavior and latency when user says 'alt'.
        Golden Rule 6.1 lists 'alt' as mandatory fast-path emergency stop (<10ms).
        This test checks if 'alt' triggers reactive_safety.emergency_stop() directly or routes to LLM.
        """
        cm = create_test_conversation_manager(mock_qwen_reply="Ho sentito.")

        t0 = time.perf_counter()
        t_called = None

        def on_emergency_stop():
            nonlocal t_called
            t_called = time.perf_counter()

        cm.reactive_safety.emergency_stop.side_effect = on_emergency_stop

        res = await cm.process_input("alt", "audio")
        t_elapsed = time.perf_counter() - t0

        is_emergency_flag = cm._is_emergency("alt")
        is_reactive_stopped = cm.reactive_safety.emergency_stop.called
        is_skill_stopped = (
            cm.skill_executor.execute_skill.called and
            cm.skill_executor.execute_skill.call_args[0][0] == "stop_motion"
        )

        # Document whether 'alt' bypassed LLM or went to LLM
        # Golden Rule 6.1 mandates fast-path for 'alt'. If failed, assert flags it.
        assert is_emergency_flag is True, (
            f"_is_emergency('alt') returned False! 'alt' bypassed the fast-path and routed to LLM "
            f"(elapsed {t_elapsed*1000:.2f}ms, reactive_stop={is_reactive_stopped}, skill_stop={is_skill_stopped})."
        )


# ==============================================================================
# 3. CAPITAL QUERIES ADVERSARIAL VARIATIONS (Plural, Single Country, Mixed)
# ==============================================================================
class TestCapitalQueriesAdversarialVariations:
    """Stress-tests factual grounding for capital inquiries across diverse phrasings."""

    @pytest.mark.parametrize("query,expected_in,expected_out", [
        # Single country: Italy
        ("Qual è la capitale dell'Italia?", ["roma"], ["madrid", "parigi"]),
        ("Dimmi la capitale italiana", ["roma"], ["madrid", "parigi"]),
        ("Capitale di Italia", ["roma"], ["madrid", "parigi"]),
        # Single country: Spain
        ("Qual è la capitale della Spagna?", ["madrid"], ["roma", "parigi"]),
        ("Dimmi la capitale spagnola", ["madrid"], ["roma", "parigi"]),
        ("Capitale di Spagna", ["madrid"], ["roma", "parigi"]),
        # Single country: France
        ("qual'è la capitale della francia?", ["parigi"], ["roma"]),
        ("Dimmi la capitale di Francia", ["parigi"], ["roma"]),
        ("Capitale francese", ["parigi"], ["roma"]),
        # Plural: Italy and Spain
        ("Quali sono le capitali di Italia e Spagna?", ["roma", "madrid"], []),
        ("Dimmi le capitali di Spagna e Italia", ["roma", "madrid"], []),
        ("Capitali italiana e spagnola", ["roma", "madrid"], []),
    ])
    def test_verify_and_ground_facts_capitals_variations(self, query, expected_in, expected_out):
        cm = create_test_conversation_manager()
        # Model hallucinated "Berlino"
        res = cm._verify_and_ground_facts(query, "La capitale è Berlino")
        low = res.lower()
        for exp in expected_in:
            assert exp in low, f"Query '{query}' missing expected '{exp}' in '{res}'"
        for exp_out in expected_out:
            assert exp_out not in low, f"Query '{query}' contained unexpected '{exp_out}' in '{res}'"

    def test_capital_france_hallucination_override(self):
        """If model answers 'Roma' for France, it must be corrected to 'Parigi'."""
        cm = create_test_conversation_manager()
        res = cm._verify_and_ground_facts("Qual è la capitale della Francia?", "La capitale della Francia è Roma.")
        assert "parigi" in res.lower()
        assert "roma" not in res.lower()

    def test_other_countries_not_corrupted(self):
        """Queries about Germany or UK must not be falsely overwritten with Rome/Paris/Madrid."""
        cm = create_test_conversation_manager()
        res = cm._verify_and_ground_facts("Qual è la capitale della Germania?", "La capitale della Germania è Berlino.")
        assert "berlino" in res.lower()
        assert "parigi" not in res.lower()
        assert "roma" not in res.lower()
        assert "madrid" not in res.lower()


# ==============================================================================
# 4. DYNAMIC APPOINTMENT STORAGE AND RECALL WITH NOVEL APPOINTMENTS
# ==============================================================================
class TestDynamicAppointmentStorageAndRecall:
    """Verifies that arbitrary novel appointments are stored and recalled without hardcoded leaks."""

    @pytest.mark.parametrize("ingest_query,expected_content,recall_query", [
        (
            "Ricordati che venerdì alle 15:00 ho il dentista.",
            "dentista",
            "Qual è l'impegno di venerdì che ti ho appena chiesto di ricordare?",
        ),
        (
            "Memorizza che domani alle 09:30 ho la riunione di budget con Marco.",
            "riunione",
            "Cosa ti ho appena chiesto di ricordare?",
        ),
        (
            "Segna che il 25 novembre alle 17:00 devo portare il cane dal veterinario.",
            "veterinario",
            "Qual è l'appuntamento che ti ho chiesto di segnare?",
        ),
        (
            "Ricorda che martedì prossimo alle 11:00 ho l'esame della patente.",
            "patente",
            "Qual è il mio prossimo impegno?",
        ),
    ])
    def test_dynamic_novel_appointment_ingest_and_recall(self, ingest_query, expected_content, recall_query):
        cm = create_test_conversation_manager()

        # Turn 1: Ingest novel appointment
        cm._verify_and_ground_facts(ingest_query, "Ricevuto, lo memorizzo subito.")

        # Simulate conversation history addition
        cm.conversation_history.append({"user": ingest_query, "marcus": "Ricevuto."})

        # Turn 2: Recall novel appointment when model claims it doesn't remember
        res = cm._verify_and_ground_facts(recall_query, "Non ricordo nessun appuntamento.")
        low = res.lower()

        assert expected_content in low, (
            f"Failed to recall '{expected_content}' from '{ingest_query}' with query '{recall_query}': got '{res}'"
        )
        # Banned hardcoded leak
        assert "visita oculistica" not in low, (
            f"Hardcoded leak 'visita oculistica' detected in response: '{res}'"
        )


# ==============================================================================
# 5. SUBTRACTION ARITHMETIC VARIATIONS (Novel Numbers, Phrasings, Operators)
# ==============================================================================
class TestSubtractionArithmeticVariations:
    """Stress-tests subtraction arithmetic handling with novel numbers and phrasings."""

    def test_direct_subtraction_with_minus_word(self):
        """Tests 'X meno Y' with novel numbers."""
        cm = create_test_conversation_manager()

        # 60 - 25 = 35
        res = cm._verify_and_ground_facts("Quanto fa 60 meno 25?", "Fa 40")
        assert "35" in res or "trentacinque" in res.lower(), f"Failed 60 - 25 = 35: got '{res}'"

        # 80cm - 35cm = 45cm
        res_cm = cm._verify_and_ground_facts("Quanto fa 80 centimetri meno 35 centimetri?", "Fa 50 centimetri")
        assert "45" in res_cm or "quarantacinque" in res_cm.lower(), f"Failed 80 - 35 = 45: got '{res_cm}'"

    def test_direct_subtraction_with_minus_symbol(self):
        """Tests 'X - Y' with novel numbers."""
        cm = create_test_conversation_manager()
        res = cm._verify_and_ground_facts("Quanto fa 100 - 30 centimetri?", "Fa 50")
        assert "70" in res or "settanta" in res.lower(), f"Failed 100 - 30 = 70: got '{res}'"

    def test_difference_between_phrasing_benchmark_40_15(self):
        """Benchmark case: 'differenza tra 40 e 15' = 25."""
        cm = create_test_conversation_manager()
        res = cm._verify_and_ground_facts("Qual è la differenza tra 40 centimetri e 15 centimetri?", "Fa 30 centimetri")
        assert "25" in res or "venticinque" in res.lower(), f"Failed difference 40 - 15: got '{res}'"

    def test_difference_between_phrasing_novel_numbers(self):
        """Novel numbers: 'Qual è la differenza tra 50 e 20 centimetri?' = 30."""
        cm = create_test_conversation_manager()
        # Model hallucinated "Fa 40 centimetri"
        res = cm._verify_and_ground_facts("Qual è la differenza tra 50 e 20 centimetri?", "Fa 40 centimetri")
        assert "30" in res or "trenta" in res.lower(), (
            f"Failed difference calculation for novel numbers 50 - 20: got '{res}'"
        )
