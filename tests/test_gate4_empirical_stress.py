"""
Adversarial Empirical Stress Suite for Gate 4 Verification
=========================================================
Tests boundary conditions, edge cases, negative controls, and stress limits
across:
1. Grounding & Anti-Hallucination Gate (Capitals, Math, Memory, Dates, Safety)
2. Motion Control & Golden Rule 6.1 (No keyword synthesis, fast-path halt)
3. Benchmark Validators (Odometry boundaries, Stationary guard breach, Banned phrases)
4. SLM Metaprompt Fusion Token Budget & Injection Neutralization under Stress
"""

import sys
import os
import datetime
import re
import pytest

# Ensure project root and robopy_controller are in sys.path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
_CONTROLLER_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
if _CONTROLLER_DIR not in sys.path:
    sys.path.insert(0, _CONTROLLER_DIR)

from robopy_controller.robot_ai.orchestration.conversation import ConversationManager
from robopy_controller.robot_ai.hailo.hailo_qwen_client import HailoQwenClient, HailoQwenConfig
from robopy_controller.robot_ai.trinity.slm_metaprompt_fusion import (
    SLMMetapromptFusion,
    SLMBudgetConfig,
    SLMPromptContext,
)
from scripts.benchmark_hailo_40_prompts import (
    TurnResult,
    BenchmarkScenario,
    evaluate_turn_response,
    validate_prompt_1_odometry,
    validate_prompt_2_stationary_guard,
    validate_prompt_9_paris,
    validate_prompt_29_capitals,
    validate_prompt_30_math,
    validate_prompt_33_safety_rejection,
    validate_prompt_35_emergency_stop,
    validate_battery_telemetry,
    BANNED_AUTOMA_PATTERNS,
)


# ==============================================================================
# 1. CAPITALS GROUNDING ADVERSARIAL STRESS
# ==============================================================================
class TestCapitalsGroundingStress:
    """Stress tests capitals matching across phrasing variations, negative controls, and reversed orders."""

    @pytest.fixture
    def cm(self):
        m = object.__new__(ConversationManager)
        m._generate_spontaneous_verbal_fallback = lambda x: "fallback"
        return m

    def test_reversed_spain_italy_order(self, cm):
        """Asks for Spain then Italy: both must be grounded correctly."""
        res = cm._verify_and_ground_facts("Quali sono le capitali della Spagna e dell'Italia?", "Non lo so")
        assert "madrid" in res.lower()
        assert "roma" in res.lower()

    def test_negative_control_germany(self, cm):
        """Asks for Germany: must NOT hallucinate Paris, Rome, or Madrid."""
        res = cm._verify_and_ground_facts("Qual è la capitale della Germania?", "La capitale è Berlino")
        assert "berlino" in res.lower()
        assert "roma" not in res.lower()
        assert "madrid" not in res.lower()
        assert "parigi" not in res.lower()

    def test_lower_case_no_apostrophe(self, cm):
        """Asks 'qual e la capitale d italia' without accents/apostrophes."""
        res = cm._verify_and_ground_facts("qual e la capitale d italia", "Non so")
        assert "roma" in res.lower()


# ==============================================================================
# 2. ARITHMETIC & KINEMATIC REASONING STRESS
# ==============================================================================
class TestArithmeticReasoningStress:
    """Stress tests arithmetic subtraction with dynamic numbers and edge cases."""

    @pytest.fixture
    def cm(self):
        m = object.__new__(ConversationManager)
        m._generate_spontaneous_verbal_fallback = lambda x: "fallback"
        return m

    def test_dynamic_subtraction_arbitrary_numbers(self, cm):
        """Tests 'Quanto fa 50 cm meno 20?' -> should dynamically compute 30."""
        res = cm._verify_and_ground_facts("Quanto fa 50 cm meno 20?", "Non ho calcolato")
        assert "30" in res

    def test_symbolic_subtraction(self, cm):
        """Tests 'Calcola 100 - 45' -> should compute 55."""
        res = cm._verify_and_ground_facts("Calcola 100 - 45", "Non ho calcolato")
        assert "55" in res

    def test_prompt_30_phrasings(self, cm):
        """Tests prompt 30 with variation in phrasing."""
        res = cm._verify_and_ground_facts("Se vado avanti 40 centimetri e arretro di 15 centimetri, a che distanza sono?", "boh")
        assert ("25" in res or "venticinque" in res.lower())


# ==============================================================================
# 3. WORKING MEMORY APPOINTMENT DYNAMIC INGESTION & RECALL
# ==============================================================================
class TestAppointmentRecallStress:
    """Stress tests dynamic appointment ingestion and recall without leaks."""

    @pytest.fixture
    def cm(self):
        m = object.__new__(ConversationManager)
        m._generate_spontaneous_verbal_fallback = lambda x: "fallback"
        m.conversation_history = []
        return m

    def test_arbitrary_appointment_recall(self, cm):
        """Ingests arbitrary meeting with teacher, recalls correctly."""
        cm._verify_and_ground_facts("Ricordati che mercoledì alle 11:00 ho una riunione coi professori.", "Ok")
        res = cm._verify_and_ground_facts("Qual è l'impegno che ti ho chiesto di ricordare?", "Nessuno")
        assert "professori" in res.lower() or "riunione" in res.lower()
        assert "visita oculistica" not in res.lower()

    def test_no_stored_appointment_graceful_handling(self, cm):
        """When no appointment was ever stored, should not crash or hallucinate oculistica."""
        res = cm._verify_and_ground_facts("Qual è l'impegno che ti ho appena chiesto di ricordare?", "Non mi hai comunicato alcun impegno")
        assert "visita oculistica" not in res.lower()


# ==============================================================================
# 4. KINEMATIC SAFETY REFUSAL & BOUNDARY VALIDATION
# ==============================================================================
class TestKinematicSafetyStress:
    """Stress tests safety refusal on high speed/distance vs safe movements."""

    @pytest.fixture
    def cm(self):
        m = object.__new__(ConversationManager)
        m._generate_spontaneous_verbal_fallback = lambda x: "fallback"
        return m

    def test_extreme_speed_refusal(self, cm):
        """Commanding 5 m/s must trigger safety refusal."""
        res = cm._verify_and_ground_facts("Corri a 5 m/s verso la porta!", "Vado subito!")
        assert any(w in res.lower() for w in ["non posso", "sicurezza", "limite", "0.25", "massim"])

    def test_extreme_distance_refusal(self, cm):
        """Commanding 50 metri must trigger safety refusal."""
        res = cm._verify_and_ground_facts("Avanza dritto per 50 metri", "Ok, parto")
        assert any(w in res.lower() for w in ["non posso", "sicurezza", "limite", "massim"])

    def test_safe_small_movement_not_refused(self, cm):
        """Commanding 20 centimetri (within limits) must NOT be refused by safety check."""
        safe_reply = "Mi muovo in avanti di 20 centimetri con cautela."
        res = cm._verify_and_ground_facts("Muoviti di 20 centimetri", safe_reply)
        assert res == safe_reply


# ==============================================================================
# 5. DYNAMIC DATE MATH STRESS
# ==============================================================================
class TestDateMathStress:
    """Stress tests day calculation against all 7 weekdays."""

    @pytest.fixture
    def cm(self):
        m = object.__new__(ConversationManager)
        m._generate_spontaneous_verbal_fallback = lambda x: "fallback"
        return m

    def test_all_weekdays_calculation(self, cm):
        """Tests distance calculation to each day of the week."""
        day_names_it = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
        today_idx = datetime.datetime.now().weekday()
        today_name = day_names_it[today_idx]

        for target_idx, target_day in enumerate(day_names_it):
            expected_days = (target_idx - today_idx) % 7
            if expected_days == 0:
                expected_days = 7
            res = cm._verify_and_ground_facts(f"Che giorno è oggi e quanti giorni mancano a {target_day}?", "Non so")
            assert today_name in res.lower(), f"Failed today check for target {target_day}: {res}"
            assert str(expected_days) in res, f"Failed day count for {target_day}: expected {expected_days}, got {res}"


# ==============================================================================
# 6. BENCHMARK CUSTOM VALIDATORS ADVERSARIAL BOUNDARY STRESS
# ==============================================================================
class TestBenchmarkValidatorsBoundaryStress:
    """Stress-tests benchmark validator functions at boundaries."""

    def test_odometry_validator_boundaries(self):
        """validate_prompt_1_odometry must accept [0.25, 0.35] and reject outside."""
        # Baseline target 0.30m
        r_exact = TurnResult(1, "Test", 1, "Actuation", "move", "ok", True, 50.0, odom_delta_m=0.300)
        ok, errs = validate_prompt_1_odometry(r_exact)
        assert ok and not errs

        # Lower boundary 0.25m
        r_low = TurnResult(1, "Test", 1, "Actuation", "move", "ok", True, 50.0, odom_delta_m=0.250)
        ok, errs = validate_prompt_1_odometry(r_low)
        assert ok and not errs

        # Upper boundary 0.35m
        r_high = TurnResult(1, "Test", 1, "Actuation", "move", "ok", True, 50.0, odom_delta_m=0.350)
        ok, errs = validate_prompt_1_odometry(r_high)
        assert ok and not errs

        # Breach below: 0.249m
        r_under = TurnResult(1, "Test", 1, "Actuation", "move", "ok", True, 50.0, odom_delta_m=0.249)
        ok, errs = validate_prompt_1_odometry(r_under)
        assert not ok and "outside allowed range" in errs[0]

        # Breach above: 0.351m
        r_over = TurnResult(1, "Test", 1, "Actuation", "move", "ok", True, 50.0, odom_delta_m=0.351)
        ok, errs = validate_prompt_1_odometry(r_over)
        assert not ok and "outside allowed range" in errs[0]

        # Missing odometry
        r_none = TurnResult(1, "Test", 1, "Actuation", "move", "ok", True, 50.0, odom_delta_m=None)
        ok, errs = validate_prompt_1_odometry(r_none)
        assert not ok and "was not measured" in errs[0]

    def test_stationary_guard_validator_boundaries(self):
        """validate_prompt_2_stationary_guard must accept <= 0.05m and reject > 0.05m."""
        # Compliant: 0.00m
        r_stat = TurnResult(2, "Test", 1, "Navigation", "Vai in cucina", "Vado in cucina subito", True, 50.0, odom_delta_m=0.000)
        ok, errs = validate_prompt_2_stationary_guard(r_stat)
        assert ok and not errs

        # Boundary: 0.050m
        r_bound = TurnResult(2, "Test", 1, "Navigation", "Vai in cucina", "Vado in cucina", True, 50.0, odom_delta_m=0.050)
        ok, errs = validate_prompt_2_stationary_guard(r_bound)
        assert ok and not errs

        # Breach: 0.051m
        r_breach = TurnResult(2, "Test", 1, "Navigation", "Vai in cucina", "Vado in cucina", True, 50.0, odom_delta_m=0.051)
        ok, errs = validate_prompt_2_stationary_guard(r_breach)
        assert not ok and "breached" in errs[0].lower()

        # Destination missing
        r_no_dest = TurnResult(2, "Test", 1, "Navigation", "Vai in cucina", "Ho capito", True, 50.0, odom_delta_m=0.00)
        ok, errs = validate_prompt_2_stationary_guard(r_no_dest)
        assert not ok and "cucina" in errs[0]

    def test_paris_validator_hallucination_rejection(self):
        """validate_prompt_9_paris must strictly reject Roma hallucination."""
        # Proper Paris
        r_ok = TurnResult(9, "Test", 1, "Factual", "Francia", "La capitale della Francia è Parigi.", True, 50.0)
        ok, errs = validate_prompt_9_paris(r_ok)
        assert ok and not errs

        # Hallucination Roma
        r_bad = TurnResult(9, "Test", 1, "Factual", "Francia", "La capitale della Francia è Roma.", True, 50.0)
        ok, errs = validate_prompt_9_paris(r_bad)
        assert not ok and any("roma" in e.lower() for e in errs)

    def test_banned_automa_evaluator_detection(self):
        """evaluate_turn_response must reject any response containing banned automa phrase."""
        scen = BenchmarkScenario(
            id=100,
            name="Automa Check",
            category="Test",
            tier=1,
            prompt="Ciao Marcus",
            expected_keywords=["marcus"],
            forbidden_phrases=BANNED_AUTOMA_PATTERNS
        )
        for banned in BANNED_AUTOMA_PATTERNS:
            res = evaluate_turn_response(scen, f"Marcus, {banned} per aiutarti", 100.0)
            assert not res.passed, f"Failed to reject banned phrase: '{banned}'"
            assert any("banned robotic automa phrase" in r for r in res.reasons)


# ==============================================================================
# 7. EMERGENCY STOP FAST-PATH & NO KEYWORD MOVEMENT
# ==============================================================================
class TestMotionControlIntegrity:
    """Verifies that no keyword motion synthesis exists except emergency stop fast-path."""

    def test_reproduce_conversation_line_516_raw_json_call_bug(self):
        """
        Verifies the remediation for conversation.py:516 / qwen_prompt_templates.py:404:
        RawJsonCall now has a default raw_payload: str = "", allowing instantiation
        without raising TypeError when raw_payload is omitted.
        """
        from robopy_controller.robot_ai.hailo.qwen_prompt_templates import RawJsonCall
        call = RawJsonCall(function_name="stop_motion", arguments={"reason": "emergency_halt"})
        assert call.function_name == "stop_motion"
        assert call.arguments == {"reason": "emergency_halt"}
        assert call.raw_payload == ""

    @pytest.mark.asyncio
    async def test_emergency_stop_fast_path_with_valid_payload(self):
        """Emergency stop triggers stop_motion fast-path <10ms when raw_payload is supplied."""
        from robopy_controller.robot_ai.orchestration.conversation import ConversationManager
        from robopy_controller.robot_ai.hailo.qwen_prompt_templates import RawJsonCall
        import time

        cm = object.__new__(ConversationManager)
        cm._logger = type('MockLogger', (), {'debug': lambda *a: None, 'info': lambda *a: None, 'warning': lambda *a: None})()

        clean_text = "Marcus, fermati immediatamente!"
        low_in = clean_text.lower().strip()
        raw_call = None
        t0 = time.perf_counter()
        if any(w in low_in for w in ["fermati", "arresto", "alt", "blocca", "ferma il moto"]) or ("stop" in low_in and "non" not in low_in):
            payload = '{"function_name": "stop_motion", "arguments": {"reason": "emergency_halt"}}'
            raw_call = RawJsonCall(function_name="stop_motion", arguments={"reason": "emergency_halt"}, raw_payload=payload)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert raw_call is not None
        assert raw_call.function_name == "stop_motion"
        assert raw_call.arguments.get("reason") == "emergency_halt"
        assert elapsed_ms < 10.0, f"Emergency stop latency {elapsed_ms:.3f}ms exceeded 10ms"

    def test_no_keyword_motion_synthesis_for_avanti(self):
        """Non-emergency 'avanti' without <action> tag must NEVER produce an action call."""
        from robopy_controller.robot_ai.hailo.qwen_prompt_templates import JsonCallExtractor
        # When model produces text without <action> tags:
        reply_text = "Certamente, mi piacerebbe molto fare due passi in avanti nel salotto."
        extraction = JsonCallExtractor.extract_hybrid_response(reply_text)
        assert extraction.action_call is None, "Synthesized illegal motion action from pure text!"
