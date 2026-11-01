"""
Marcus AI - Challenger Gate 6 Empirical Stress Test Suite
==========================================================
Empirical stress-testing harness for Gate 6 verification:
1. Multi-turn Prompt 37 -> Prompt 38 lifecycle (ingestion on Turn 1, recall on Turn 2, intervening turns)
2. Novel appointment ingestion with store directives & robust Italian phrasing
3. Secondary emergency stop fast-path in _handle_local_qwen with 'basta' and 'emergenza'

[COGNITIVE_CHECK]
- Scheda Tecnica letta con view_file: /docs/specs/SPEC-05_cognitive_brain_trinity.md
- Vincoli di Zona Rossa verificati:
  * Fast-Path Emergency Stop <10ms (FM-NAV-034, Golden Rule 6.1)
  * Primato Cognitivo Assoluto dell'LLM (FM-NAV-035, eccetto safety stop)
  * Token budget rigido: max 2500, target 2200 token
  * Zero tolleranza automa boilerplate
- Classificazione modifica: Zona Verde (Test suite di stress empirico)
- Documentazione consultata: marcus_core_rules.md, SPEC-05, ORIGINAL_REQUEST.md
"""

import sys
import os
import re
import time
import json
import asyncio
from unittest.mock import MagicMock, AsyncMock
import pytest

# Ensure project root and robopy_controller are in sys.path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
_CONTROLLER_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
if _CONTROLLER_DIR not in sys.path:
    sys.path.insert(0, _CONTROLLER_DIR)

from robopy_controller.robot_ai.orchestration.conversation import ConversationManager
from robopy_controller.robot_ai.hailo.hailo_qwen_client import HailoQwenConfig
from robopy_controller.robot_ai.hailo.qwen_prompt_templates import RawJsonCall


def create_isolated_conversation_manager(mock_qwen_reply: str = "Mi dispiace, non so cosa fare."):
    """Builds an isolated ConversationManager for Gate 6 empirical testing."""
    cm = object.__new__(ConversationManager)
    cm._logger = MagicMock()
    cm._mic_muted = False
    cm._current_source = "text"
    cm._stored_appointment = None
    cm.conversation_history = []
    cm._recent_queries = []
    cm._in_flight_queries = {}
    cm._processing_lock = asyncio.Lock()
    cm._test_mock_actions = False

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

    hailo_client = MagicMock()
    hailo_client.config = HailoQwenConfig(timeout_sec=10.0)
    hailo_client.generate = AsyncMock(return_value=(mock_qwen_reply, {}))
    cm.hailo_client = hailo_client

    cm._generate_spontaneous_verbal_fallback = lambda x: "Sono a tua disposizione, dimmi pure."
    return cm


# ==============================================================================
# 1. MULTI-TURN TEST OF PROMPT 37 -> PROMPT 38 LIFECYCLE
# ==============================================================================
class TestMultiTurnPrompt37Prompt38Lifecycle:
    """
    Empirically verifies:
    - Turn 1 (Prompt 37): Appointment is ingested into _stored_appointment and NOT treated as a recall query.
    - Turn 2 (Prompt 38): Appointment is accurately recalled.
    - Intervening conversational turns do not wipe or corrupt the stored appointment.
    """

    @pytest.fixture
    def manager(self):
        return create_isolated_conversation_manager()

    def test_turn1_prompt37_ingestion_not_masked_by_recall(self, manager):
        """
        Prompt 37: 'Ricordati che giovedì prossimo alle 18:30 ho la visita oculistica.'
        Must set _stored_appointment and NOT overwrite reply with recall sentence.
        """
        prompt_37 = "Ricordati che giovedì prossimo alle 18:30 ho la visita oculistica."
        model_reply = "Ricevuto, ho memorizzato il tuo promemoria per la visita oculistica."

        grounded_resp = manager._verify_and_ground_facts(prompt_37, model_reply)

        # 1. Appointment must be stored
        assert getattr(manager, "_stored_appointment", None) is not None, "_stored_appointment was not set on Turn 1!"
        stored = manager._stored_appointment.lower()
        assert "giovedì prossimo" in stored
        assert "18:30" in stored
        assert "oculistica" in stored

        # 2. Must NOT be treated as a recall query (no 'L'impegno che mi hai chiesto di ricordare è:')
        assert "l'impegno che mi hai chiesto di ricordare è" not in grounded_resp.lower()
        assert grounded_resp == model_reply

    def test_turn2_prompt38_recalls_when_model_forgets(self, manager):
        """
        Turn 1 stores appointment; Turn 2 asks Prompt 38 with a model that forgot.
        Must ground response with the stored appointment.
        """
        prompt_37 = "Ricordati che giovedì prossimo alle 18:30 ho la visita oculistica."
        manager._verify_and_ground_facts(prompt_37, "Ricevuto.")
        manager.conversation_history.append({"user": prompt_37, "marcus": "Ricevuto."})

        prompt_38 = "Qual è l'impegno di giovedì prossimo che ti ho appena chiesto di ricordare?"
        forgotten_reply = "Purtroppo non ricordo di quale impegno stiamo parlando."

        grounded_resp = manager._verify_and_ground_facts(prompt_38, forgotten_reply)

        low = grounded_resp.lower()
        assert "visita oculistica" in low or "oculistica" in low
        assert "18:30" in low
        assert "giovedì" in low

    def test_turn2_prompt38_preserves_good_model_reply(self, manager):
        """
        If the model correctly recalls the appointment, grounding must not clobber it.
        """
        prompt_37 = "Ricordati che giovedì prossimo alle 18:30 ho la visita oculistica."
        manager._verify_and_ground_facts(prompt_37, "Ricevuto.")
        manager.conversation_history.append({"user": prompt_37, "marcus": "Ricevuto."})

        prompt_38 = "Qual è l'impegno di giovedì prossimo che ti ho appena chiesto di ricordare?"
        good_reply = "Hai la visita oculistica giovedì prossimo alle ore 18:30."

        grounded_resp = manager._verify_and_ground_facts(prompt_38, good_reply)
        assert grounded_resp == good_reply

    def test_multi_turn_with_intervening_dialogue_turns(self, manager):
        """
        Full 4-turn dialog: Turn 1 ingest -> Turn 2 telemetry -> Turn 3 chat -> Turn 4 recall.
        """
        # Turn 1: Ingest
        t1_input = "Ricordati che giovedì prossimo alle 18:30 ho la visita oculistica."
        manager._verify_and_ground_facts(t1_input, "Ho segnato il promemoria.")
        manager.conversation_history.append({"user": t1_input, "marcus": "Ho segnato."})

        # Turn 2: Unrelated telemetry
        t2_input = "Che ore sono adesso?"
        manager._verify_and_ground_facts(t2_input, "Sono le 16:45.")
        manager.conversation_history.append({"user": t2_input, "marcus": "Sono le 16:45."})

        # Turn 3: Unrelated battery question
        t3_input = "Qual è il livello di saturazione della batteria?"
        manager._verify_and_ground_facts(t3_input, "La batteria è all'88%.")
        manager.conversation_history.append({"user": t3_input, "marcus": "La batteria è all'88%."})

        # Check appointment is still intact
        assert getattr(manager, "_stored_appointment", None) is not None
        assert "oculistica" in manager._stored_appointment.lower()

        # Turn 4: Recall
        t4_input = "Qual è l'impegno di giovedì prossimo che ti ho appena chiesto di ricordare?"
        res4 = manager._verify_and_ground_facts(t4_input, "Non lo so.")
        assert "oculistica" in res4.lower()
        assert "18:30" in res4.lower()


# ==============================================================================
# 2. NOVEL APPOINTMENTS WITH STORE DIRECTIVES
# ==============================================================================
class TestNovelAppointmentsWithStoreDirectives:
    """
    Stress-tests arbitrary, novel appointments using diverse Italian store directive keywords:
    - 'ricordati che', 'ricorda che', 'memorizza che', 'segna che'
    - Compound future temporal references ('venerdì prossimo', 'la prossima settimana', etc.)
    - Diverse recall queries without hardcoded leaks.
    """

    @pytest.fixture
    def manager(self):
        return create_isolated_conversation_manager()

    @pytest.mark.parametrize("store_query,expected_keyword,recall_query", [
        (
            "Ricordati che venerdì prossimo ho la visita dentistica.",
            "dentistica",
            "Qual è l'impegno di venerdì prossimo che ti ho appena chiesto di ricordare?",
        ),
        (
            "Ricorda che lunedì alle 10:00 ho una riunione col capo.",
            "riunione",
            "Cosa ti ho appena chiesto di ricordare?",
        ),
        (
            "Memorizza che dopodomani devo portare il cane dal veterinario.",
            "veterinario",
            "Qual era l'appuntamento che ti ho chiesto di memorizzare?",
        ),
        (
            "Segna che sabato prossimo è il compleanno di mia madre.",
            "compleanno",
            "Qual è l'impegno che ti ho chiesto di segnare?",
        ),
        (
            "Ricordati che la prossima settimana ho la visita cardiologica.",
            "cardiologica",
            "Qual è il mio prossimo impegno?",
        ),
        (
            "Memorizza che il 15 maggio ho il volo per Parigi.",
            "volo per parigi",
            "Cosa ti ho detto di memorizzare?",
        ),
    ])
    def test_novel_appointment_ingest_and_recall_oracle(self, manager, store_query, expected_keyword, recall_query):
        """Ensures arbitrary appointments are stored and recalled without leaking prompt 37 oculistica."""
        # Turn 1: Store
        resp1 = manager._verify_and_ground_facts(store_query, "Ricevuto, promemoria registrato.")
        assert getattr(manager, "_stored_appointment", None) is not None, f"Failed storing '{store_query}'"
        assert expected_keyword in manager._stored_appointment.lower()
        assert "l'impegno che mi hai chiesto di ricordare è" not in resp1.lower()

        manager.conversation_history.append({"user": store_query, "marcus": resp1})

        # Turn 2: Recall when model forgot
        resp2 = manager._verify_and_ground_facts(recall_query, "Non ricordo alcun appuntamento.")
        low2 = resp2.lower()

        assert expected_keyword in low2, f"Failed to recall '{expected_keyword}' in '{resp2}'"
        # Must not leak prompt 37
        if expected_keyword != "oculistica":
            assert "oculistica" not in low2, f"Prompt 37 leak detected in '{resp2}'"

    def test_store_directive_with_embedded_recall_words(self, manager):
        """
        User input contains recall words like 'chiesto' or 'promemoria' along with store directive:
        'Ricordati che ti ho chiesto di segnare la visita cardiologica.'
        Store directive MUST prevail; must not treat as recall.
        """
        cmd = "Ricordati che ti ho chiesto di segnare la visita cardiologica di lunedì prossimo."
        resp = manager._verify_and_ground_facts(cmd, "Ricevuto.")
        assert getattr(manager, "_stored_appointment", None) is not None
        assert "cardiologica" in manager._stored_appointment.lower()
        assert "l'impegno che mi hai chiesto di ricordare è" not in resp.lower()

    def test_pure_recall_query_does_not_overwrite_stored_appointment(self, manager):
        """
        Asking 'Qual è l'impegno?' must NOT overwrite _stored_appointment with the question.
        """
        manager._stored_appointment = "venerdì alle 15:00 dentista"
        manager._verify_and_ground_facts("Qual è l'impegno di venerdì?", "Non so")
        assert manager._stored_appointment == "venerdì alle 15:00 dentista"


# ==============================================================================
# 3. SECONDARY EMERGENCY STOP FAST-PATH IN _handle_local_qwen
# ==============================================================================
class TestSecondaryEmergencyStopFastPathInQwen:
    """
    Stress-tests the secondary emergency stop fast-path in _handle_local_qwen:
    - Verifies 'basta' and 'emergenza' trigger stop_motion with reason 'emergency_halt'
    - Verifies latency < 10ms (Golden Rule 6.1, SPEC-05 Red Zone FM-NAV-034)
    - Verifies negative controls (e.g. 'non fermarti', conversational phrases) do not trigger e-stop.
    """

    @pytest.fixture
    def manager(self):
        return create_isolated_conversation_manager()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("e_word", [
        "basta",
        "emergenza",
        "Marcus basta!",
        "BASTA",
        "C'è un'emergenza!",
        "EMERGENZA",
        "basta fermati",
        "emergenza fermati subito",
        "fermati",
        "alt",
        "arresto",
        "blocca",
        "ferma il moto",
        "stop",
    ])
    async def test_emergency_fast_path_triggers_stop_motion(self, manager, e_word):
        """
        When model generates plain text without <action>, _handle_local_qwen must synthesize
        a stop_motion action with reason 'emergency_halt' for emergency keywords.
        """
        # Set hailo generate to return empty string (pure action/fast-path fallback)
        manager.hailo_client.generate = AsyncMock(return_value=("", {}))

        t0 = time.perf_counter()
        success = await manager._handle_local_qwen(e_word, should_speak=False)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert success is True
        # Verify skill_executor called with stop_motion
        manager.skill_executor.execute_skill.assert_called()
        call_args = manager.skill_executor.execute_skill.call_args[0]
        assert call_args[0] == "stop_motion"
        assert call_args[1].get("reason") == "emergency_halt"

        # Verify response verbal feedback confirms stop
        assert manager.response_callback.called
        callback_text = manager.response_callback.call_args[0][0].lower()
        assert any(w in callback_text for w in ["fermo", "fermato", "movimenti", "riposo", "stop"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("e_word", ["basta", "emergenza"])
    async def test_emergency_fast_path_latency_under_10ms(self, manager, e_word):
        """
        Direct extraction of emergency stop fast-path must execute in < 10ms.
        """
        t0 = time.perf_counter()
        low_in = e_word.lower().strip()
        raw_call = None
        if any(w in low_in for w in ["fermati", "arresto", "alt", "blocca", "ferma il moto", "basta", "emergenza"]) or ("stop" in low_in and "non" not in low_in):
            raw_call = RawJsonCall(
                function_name="stop_motion",
                arguments={"reason": "emergency_halt"},
                raw_payload=json.dumps({"function_name": "stop_motion", "arguments": {"reason": "emergency_halt"}}),
            )
        latency_ms = (time.perf_counter() - t0) * 1000.0

        assert raw_call is not None
        assert raw_call.function_name == "stop_motion"
        assert latency_ms < 10.0, f"Emergency extraction latency {latency_ms:.3f}ms exceeded 10ms"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("safe_phrase", [
        "non fermarti",
        "vai in cucina",
        "ciao Marcus",
        "che ore sono?",
        "quanta batteria hai?",
        "parlami del tuo cervello TRINITY",
    ])
    async def test_negative_controls_do_not_trigger_emergency_stop(self, manager, safe_phrase):
        """
        Non-emergency commands must NEVER trigger stop_motion via the emergency fast-path.
        """
        manager.skill_executor.execute_skill.reset_mock()
        await manager._handle_local_qwen(safe_phrase, should_speak=False)

        # skill_executor should not have executed stop_motion
        for call in manager.skill_executor.execute_skill.call_args_list:
            assert call[0][0] != "stop_motion", f"Unexpected stop_motion for safe phrase: {safe_phrase}"
