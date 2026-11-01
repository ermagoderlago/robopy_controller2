"""
Marcus AI - Challenger Gate 5 Adversarial Stress Test Suite
============================================================
Independent adversarial test suite written by challenger_gate_5 to empirically
stress-test arithmetic fuzzing, emergency stop <10ms latency & keyword coverage,
action schema prompt compaction, multi-turn working memory appointment lifecycle,
and benchmark matrix stationary guard / odometry compliance.

[COGNITIVE_CHECK]
- Scheda Tecnica letta con view_file: /docs/specs/SPEC-05_cognitive_brain_trinity.md
- Vincoli di Zona Rossa verificati:
  * Fast-Path Emergency Stop <10ms (FM-NAV-034, Golden Rule 6.1)
  * Primato Cognitivo Assoluto dell'LLM (FM-NAV-035)
  * Token budget rigido: max 2500, target 2200, system budget 180 token (<648 caratteri)
  * Zero tolleranza automa boilerplate
- Classificazione modifica: Zona Verde (Adversarial stress testing harness)
- Documentazione consultata: marcus_core_rules.md, SPEC-05, ORIGINAL_REQUEST.md
"""

import sys
import os
import re
import time
import json
import pytest

# Ensure project root and robopy_controller are in sys.path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
_CONTROLLER_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
if _CONTROLLER_DIR not in sys.path:
    sys.path.insert(0, _CONTROLLER_DIR)

from robopy_controller.robot_ai.orchestration.conversation import ConversationManager
from robopy_controller.robot_ai.trinity.slm_metaprompt_fusion import (
    SLMMetapromptFusion,
    SLMPromptContext,
    SLMBudgetConfig,
)
from robopy_controller.robot_ai.hailo.qwen_prompt_templates import (
    RawJsonCall,
    JsonCallExtractor,
)
from scripts.benchmark_hailo_40_prompts import (
    Hailo40PromptBenchmarkRunner,
    BENCHMARK_SCENARIOS,
    BANNED_AUTOMA_PATTERNS,
)


# ==============================================================================
# 1. ARITHMETIC FUZZING & SUBTRACTION REASONING ORACLE
# ==============================================================================
class TestArithmeticFuzzingAndOracles:
    """Stress tests arithmetic difference and subtraction with dynamic fuzzed pairs."""

    @pytest.fixture
    def manager(self):
        m = object.__new__(ConversationManager)
        m._stored_appointment = None
        m.conversation_history = []
        m._generate_spontaneous_verbal_fallback = lambda x: "Fallback naturale."
        return m

    @pytest.mark.parametrize("n1,n2,pattern", [
        (42, 17, "meno"),
        (85, 30, "-"),
        (77, 23, "differenza tra"),
        (90, 45, "qual è la differenza tra"),
        (64, 16, "meno"),
        (100, 37, "-"),
        (58, 29, "differenza tra"),
        (99, 1, "qual è la differenza tra"),
        (40, 15, "meno"),
        (50, 20, "differenza tra"),
    ])
    def test_dynamic_fuzzed_subtraction(self, manager, n1, n2, pattern):
        """Oracle verifies that subtraction produces the exact mathematical difference."""
        expected_diff = abs(n1 - n2)
        if "differenza" in pattern:
            query = f"{pattern} {n1} e {n2} centimetri"
        elif pattern == "-":
            query = f"Quanto fa {n1} - {n2} cm?"
        else:
            query = f"Calcola {n1} meno {n2} centimetri"

        mock_raw_resp = "Il calcolo restituisce un valore indeterminato."
        grounded = manager._verify_and_ground_facts(query, mock_raw_resp)

        assert str(expected_diff) in grounded, (
            f"Grounding failed for query '{query}': expected difference {expected_diff} in '{grounded}'"
        )

    def test_subtraction_zero_difference(self, manager):
        """Verifies boundary condition when n1 == n2."""
        query = "Qual è la differenza tra 45 e 45 centimetri?"
        grounded = manager._verify_and_ground_facts(query, "Non so.")
        assert "0" in grounded or "zero" in grounded.lower()

    def test_subtraction_negative_handling(self, manager):
        """Verifies difference formulation handles reversed numbers cleanly."""
        query = "Qual è la differenza tra 15 e 40 centimetri?"
        grounded = manager._verify_and_ground_facts(query, "Non so.")
        assert "25" in grounded or "venticinque" in grounded.lower()


# ==============================================================================
# 2. EMERGENCY STOP KEYWORD COVERAGE & <10ms LATENCY ORACLE
# ==============================================================================
class TestEmergencyStopFastPathOracles:
    """Verifies fast-path emergency stop coverage and latency under 10ms."""

    @pytest.fixture
    def manager(self):
        m = object.__new__(ConversationManager)
        m._stored_appointment = None
        m.conversation_history = []
        return m

    @pytest.mark.parametrize("keyword", [
        "fermati", "stop", "alt", "basta", "blocca", "arresto", "emergenza"
    ])
    def test_is_emergency_keyword_coverage(self, manager, keyword):
        """Golden Rule 6.1 & SPEC-05 Red Zone keyword coverage on _is_emergency."""
        assert manager._is_emergency(f"Marcus, {keyword}!") is True
        assert manager._is_emergency(keyword.upper()) is True

    @pytest.mark.parametrize("keyword", ["fermati", "stop", "alt", "basta", "blocca", "arresto"])
    def test_emergency_fast_path_latency_under_10ms(self, manager, keyword):
        """Latency oracle: _is_emergency evaluation must complete in <10ms."""
        input_text = f"Marcus, {keyword} immediatamente!"
        t0 = time.perf_counter()
        is_em = manager._is_emergency(input_text)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert is_em is True
        assert elapsed_ms < 10.0, f"Emergency stop latency {elapsed_ms:.3f}ms exceeded 10ms threshold"

    def test_raw_json_call_no_type_error_without_payload(self):
        """Ensures RawJsonCall constructor defaults prevent regressions."""
        call = RawJsonCall(function_name="stop_motion", arguments={"reason": "emergency_halt"})
        assert call.function_name == "stop_motion"
        assert call.raw_payload == ""
        assert call.extraction_latency_us == 0.0


# ==============================================================================
# 3. ACTION PROMPT COMPACTION & SCHEMA INTEGRITY ORACLE
# ==============================================================================
class TestActionPromptCompactionOracles:
    """Verifies default_sys character budget and schema preservation."""

    def test_default_sys_prompt_fits_180_token_budget_without_truncation(self):
        """Verifies default_sys persona fits within 180 tokens and contains all 4 schemas."""
        fusion = SLMMetapromptFusion()
        budget = SLMBudgetConfig()  # default budget_system = 180 tokens

        ctx = SLMPromptContext(user_text="muoviti di 30 centimetri")
        prompt_output = fusion.build_prompt_detailed(ctx)

        # Ensure all 4 action schemas are present in the final prompt
        assert "move_distance" in prompt_output.system_block
        assert "navigate_to" in prompt_output.system_block
        assert "rotate_angle" in prompt_output.system_block
        assert "stop_motion" in prompt_output.system_block

        # Ensure system section token count stays within budget
        assert prompt_output.section_token_counts["system"] <= budget.budget_system

    @pytest.mark.parametrize("intent", ["general", "kinematic_action", "navigation", "telemetry", "factual"])
    def test_all_intents_preserve_action_schemas(self, intent):
        """Ensures dynamic intent budgets do not truncate action schemas."""
        fusion = SLMMetapromptFusion()
        ctx = SLMPromptContext(user_text="Marcus cosa sai fare?", intent_category=intent)
        prompt_output = fusion.build_prompt_detailed(ctx)

        assert "move_distance" in prompt_output.system_block
        assert "navigate_to" in prompt_output.system_block
        assert "stop_motion" in prompt_output.system_block


# ==============================================================================
# 4. MULTI-TURN WORKING MEMORY APPOINTMENT LIFECYCLE ORACLE
# ==============================================================================
class TestMultiTurnWorkingMemoryLifecycle:
    """Verifies appointment storage, preservation across turns, and robust recall."""

    @pytest.fixture
    def manager(self):
        m = object.__new__(ConversationManager)
        m._stored_appointment = None
        m.conversation_history = []
        m._generate_spontaneous_verbal_fallback = lambda x: "Fallback naturale."
        return m

    def test_nominal_appointment_persists_across_intervening_dialogue_turns(self, manager):
        """Turn 1: Ingest nominal appointment. Turn 2: Unrelated query. Turn 3: Recall."""
        # Turn 1: Ingestion without 'prossimo' keyword
        t1_input = "Ricordati che venerdì alle 18:30 ho la riunione col preside."
        resp1 = manager._verify_and_ground_facts(t1_input, "Ricevuto, promemoria salvato.")
        assert getattr(manager, "_stored_appointment", None) is not None
        assert "preside" in manager._stored_appointment.lower()
        manager.conversation_history.append({"role": "user", "content": t1_input})
        manager.conversation_history.append({"role": "assistant", "content": resp1})

        # Turn 2: Unrelated telemetry query
        t2_input = "Che ore sono adesso?"
        resp2 = manager._verify_and_ground_facts(t2_input, "Sono le 14:30.")
        assert "preside" in manager._stored_appointment.lower(), "Intervening turn corrupted appointment"
        manager.conversation_history.append({"role": "user", "content": t2_input})
        manager.conversation_history.append({"role": "assistant", "content": resp2})

        # Turn 3: Recall query
        t3_input = "Qual è l'impegno che ti ho appena chiesto di ricordare?"
        resp3 = manager._verify_and_ground_facts(t3_input, "Non mi ricordo.")
        assert "preside" in resp3.lower()

    @pytest.mark.parametrize("recall_phrase", [
        "Qual è l'impegno di giovedì prossimo che ti ho appena chiesto di ricordare?",
        "Cosa ti ho appena chiesto di ricordare?",
        "Qual è l'appuntamento che ti ho chiesto di segnare?",
        "Cosa ti ho detto di memorizzare?",
        "Qual è il mio prossimo impegno?",
    ])
    def test_appointment_recall_phrasing_variants(self, manager, recall_phrase):
        """Verifies recall matches multiple natural Italian inquiries."""
        manager._stored_appointment = "venerdì alle 15:00 ho il dentista"
        resp = manager._verify_and_ground_facts(recall_phrase, "Non ricordo nulla.")
        assert "dentista" in resp.lower()

    def test_reproduce_prompt_37_ingestion_masking_bug(self, manager):
        """
        EMPIRICAL BUG REPRODUCTION:
        Demonstrates that Prompt 37 ('Ricordati che giovedì prossimo alle 18:30 ho la visita oculistica.')
        is falsely treated as a recall query due to 'visita' + 'prossimo' matching line 370,
        causing _stored_appointment to remain None on ingestion.
        """
        prompt_37 = "Ricordati che giovedì prossimo alle 18:30 ho la visita oculistica."
        manager._verify_and_ground_facts(prompt_37, "Ho memorizzato.")
        # When line 370 is defective, _stored_appointment remains None!
        is_bug_present = getattr(manager, "_stored_appointment", None) is None
        # Record finding: this assertion documents whether the bug is present
        assert is_bug_present is True, "Prompt 37 ingestion bug unexpectedly resolved"


# ==============================================================================
# 5. BENCHMARK MATRIX STATIONARY GUARD & ODOMETRY ORACLE
# ==============================================================================
class TestBenchmarkGuardrailsAndOracles:
    """Verifies benchmark suite behavior in mock mode."""

    @pytest.fixture
    def runner(self):
        return Hailo40PromptBenchmarkRunner(mode="mock", stationary_guard=True)

    def test_prompt_1_physical_odometry_30cm(self, runner):
        """Prompt 1 must execute 0.30m forward move with ~0.30m odom delta."""
        p1 = BENCHMARK_SCENARIOS[0]
        assert p1.id == 1
        res = runner.execute_scenario(p1)
        assert res.passed is True
        assert res.action_detected == "move_distance"
        assert res.odom_delta_m is not None
        assert 0.25 <= res.odom_delta_m <= 0.35, f"Odometry {res.odom_delta_m}m outside [0.25, 0.35]m"

    def test_prompt_2_stationary_guard_enforced(self, runner):
        """Prompt 2 (Vai in cucina) must validate navigation goal while remaining stationary."""
        p2 = BENCHMARK_SCENARIOS[1]
        assert p2.id == 2
        res = runner.execute_scenario(p2)
        assert res.passed is True
        assert res.action_detected == "navigate_to"
        assert res.odom_delta_m == 0.0, f"Stationary guard violated: odom delta = {res.odom_delta_m}m"

    def test_prompt_33_safety_refusal_excessive_speed(self, runner):
        """Prompt 33 must refuse extreme kinematic command (2 m/s, 10m)."""
        p33 = BENCHMARK_SCENARIOS[32]
        assert p33.id == 33
        res = runner.execute_scenario(p33)
        assert res.passed is True
        assert res.action_detected is None
        assert any(w in res.response.lower() for w in ["limiti", "sicurezza", "non posso", "supererebbe"])

    def test_zero_tolerance_banned_automa_across_all_responses(self, runner):
        """Verifies zero occurrences of canned automa strings across all 40 scenarios."""
        summary = runner.run_suite()
        assert summary.total == 40
        assert summary.passed == 40
        assert summary.failed == 0

        for r in summary.results:
            low_r = r["response"].lower()
            for banned in BANNED_AUTOMA_PATTERNS:
                assert banned not in low_r, (
                    f"Banned automa phrase '{banned}' detected in Scenario {r['id']} ({r['name']}): '{r['response']}'"
                )
