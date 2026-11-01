#!/usr/bin/env python3
"""
Unit Test Suite for 40-Prompt Hailo-10H Benchmark Runner
========================================================
Verifies scenario matrix completeness, rule integrity, canned phrase rejection,
anti-hallucination grounding, physical odometry displacement assertions,
stationary guard validation, and emergency stop fast-path latency.

[COGNITIVE_CHECK]
- Scheda Tecnica letta con view_file: /docs/specs/SPEC-01_actuation_and_motion.md,
  /docs/specs/SPEC-02_navigation_and_slam.md, /docs/specs/SPEC-03_vision_and_hailo_npu.md,
  /docs/specs/SPEC-04_audio_vui_pipeline.md, /docs/specs/SPEC-05_cognitive_brain_trinity.md
- Vincoli di Zona Rossa verificati: Pi 5 4GB RAM ceiling, sequential build discipline,
  no STVL 3D, audio 16kHz PCM, CPU core 2-3 pinning, kinematic sandbox speed limits
  (v <= 0.25 m/s, max step <= 2.0m), stationary guard on test bench, emergency stop fast-path <10ms.
- Classificazione modifica: Zona Verde (Unit test del runner e della matrice benchmark 40 prompt).
- Documentazione consultata: marcus_core_rules.md, ORIGINAL_REQUEST.md, TEST_INFRA.md, survey_actuation_benchmark.md.
"""

import sys
import os
import pytest
from typing import List, Dict, Any

# Ensure project root is in sys.path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.benchmark_hailo_40_prompts import (
    BENCHMARK_SCENARIOS,
    BANNED_AUTOMA_PATTERNS,
    BenchmarkScenario,
    TurnResult,
    evaluate_turn_response,
    validate_prompt_1_odometry,
    validate_prompt_2_stationary_guard,
    validate_prompt_9_paris,
    validate_prompt_29_capitals,
    validate_prompt_33_safety_rejection,
    validate_prompt_35_emergency_stop,
    validate_battery_telemetry,
    Hailo40PromptBenchmarkRunner,
)


class TestBenchmarkMatrixIntegrity:
    """Tests the structure, registration, and metadata of all 40 scenarios."""

    def test_exact_40_scenarios_registered(self):
        """Verifies exactly 40 scenarios are defined."""
        assert len(BENCHMARK_SCENARIOS) == 40, f"Expected 40 scenarios, found {len(BENCHMARK_SCENARIOS)}"

    def test_consecutive_ids_1_to_40(self):
        """Verifies scenario IDs form a strictly consecutive sequence from 1 to 40."""
        ids = [s.id for s in BENCHMARK_SCENARIOS]
        assert ids == list(range(1, 41)), "Scenario IDs must be consecutive from 1 to 40"

    def test_tier_distribution_matches_spec(self):
        """Verifies tier distribution matches TEST_INFRA.md (16 Tier 1, 16 Tier 2, 4 Tier 3, 4 Tier 4)."""
        tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}
        for s in BENCHMARK_SCENARIOS:
            tier_counts[s.tier] += 1

        assert tier_counts[1] == 16, f"Expected 16 Tier 1 core scenarios, got {tier_counts[1]}"
        assert tier_counts[2] == 16, f"Expected 16 Tier 2 boundary scenarios, got {tier_counts[2]}"
        assert tier_counts[3] == 4, f"Expected 4 Tier 3 cross-feature scenarios, got {tier_counts[3]}"
        assert tier_counts[4] == 4, f"Expected 4 Tier 4 real-world/emotional scenarios, got {tier_counts[4]}"

    def test_scenario_required_fields_non_empty(self):
        """Verifies all scenarios contain valid non-empty fields."""
        for s in BENCHMARK_SCENARIOS:
            assert s.name.strip(), f"Scenario {s.id} has empty name"
            assert s.category.strip(), f"Scenario {s.id} has empty category"
            assert s.prompt.strip(), f"Scenario {s.id} has empty prompt"
            assert len(s.expected_keywords) > 0, f"Scenario {s.id} has no expected keywords"
            assert len(s.forbidden_phrases) > 0, f"Scenario {s.id} has no forbidden phrases"

    def test_banned_automa_patterns_included_in_all_scenarios(self):
        """Verifies all scenarios enforce the project-wide banned automa patterns."""
        for s in BENCHMARK_SCENARIOS:
            for pattern in BANNED_AUTOMA_PATTERNS:
                assert pattern in s.forbidden_phrases, (
                    f"Scenario {s.id} missing banned pattern '{pattern}'"
                )

    def test_action_scenarios_have_proper_metadata(self):
        """Verifies action scenarios define required function names and arguments."""
        # Prompt 1: move_distance
        s1 = BENCHMARK_SCENARIOS[0]
        assert s1.action_type == "move_distance"
        assert s1.action_args == {"direction": "avanti", "distance_m": 0.30}

        # Prompt 2: navigate_to
        s2 = BENCHMARK_SCENARIOS[1]
        assert s2.action_type == "navigate_to"
        assert s2.action_args == {"destination": "cucina"}

        # Prompt 34: rotate_angle
        s34 = BENCHMARK_SCENARIOS[33]
        assert s34.action_type == "rotate_angle"
        assert s34.action_args == {"direction": "sinistra", "angle_deg": 15.0}

        # Prompt 35: stop_motion
        s35 = BENCHMARK_SCENARIOS[34]
        assert s35.action_type == "stop_motion"


class TestCannedAutomaRejection:
    """Verifies that robotic automa phrases are strictly detected and failed."""

    @pytest.mark.parametrize("banned_phrase", BANNED_AUTOMA_PATTERNS)
    def test_banned_phrases_cause_failure(self, banned_phrase):
        scenario = BENCHMARK_SCENARIOS[2]  # Prompt 3 ("che ore sono?")
        # Inject banned phrase into an otherwise valid answer
        corrupted_response = f"Sono le 10:30. {banned_phrase} per aiutarti."
        result = evaluate_turn_response(scenario, corrupted_response, latency_ms=450.0)
        assert not result.passed, f"Response containing '{banned_phrase}' should have failed!"
        assert any("banned robotic automa phrase" in r for r in result.reasons)

    def test_empty_response_fails(self):
        scenario = BENCHMARK_SCENARIOS[0]
        result = evaluate_turn_response(scenario, "", latency_ms=5000.0)
        assert not result.passed
        assert "Empty response" in result.reasons[0]


class TestFactualGroundingAndAntiHallucination:
    """Verifies factual knowledge validation and anti-hallucination barriers."""

    def test_prompt_9_paris_passes(self):
        s9 = BENCHMARK_SCENARIOS[8]
        valid_resp = "La capitale della Francia è Parigi."
        res = evaluate_turn_response(s9, valid_resp, latency_ms=400.0)
        assert res.passed

    def test_prompt_9_roma_hallucination_fails(self):
        s9 = BENCHMARK_SCENARIOS[8]
        hallucinated_resp = "La capitale della Francia è Roma."
        res = evaluate_turn_response(s9, hallucinated_resp, latency_ms=400.0)
        assert not res.passed
        assert any("Hallucination detected" in r or "Missing expected" in r for r in res.reasons)

    def test_prompt_17_wife_and_location(self):
        s17 = BENCHMARK_SCENARIOS[16]
        valid_resp = "Tua moglie si chiama Luisella Bonfanti e insegna a Colle Brianza."
        res = evaluate_turn_response(s17, valid_resp, latency_ms=450.0)
        assert res.passed

        wrong_resp = "Tua moglie si chiama Maria e insegna a Milano."
        res_wrong = evaluate_turn_response(s17, wrong_resp, latency_ms=450.0)
        assert not res_wrong.passed

    def test_prompt_18_children_count_and_youngest(self):
        s18 = BENCHMARK_SCENARIOS[17]
        valid_resp = "Hai 3 figli e il più piccolo si chiama Jacopo."
        res = evaluate_turn_response(s18, valid_resp, latency_ms=450.0)
        assert res.passed

    def test_prompt_19_age_difference(self):
        s19 = BENCHMARK_SCENARIOS[18]
        valid_resp = "Tra Isabella e Jacopo ci sono 7 anni di differenza."
        res = evaluate_turn_response(s19, valid_resp, latency_ms=450.0)
        assert res.passed

    def test_prompt_20_middle_child(self):
        s20 = BENCHMARK_SCENARIOS[19]
        valid_resp = "Il figlio di mezzo è Edoardo, che ha 16 anni."
        res = evaluate_turn_response(s20, valid_resp, latency_ms=450.0)
        assert res.passed

    def test_prompt_29_both_capitals_required(self):
        s29 = BENCHMARK_SCENARIOS[28]
        valid_resp = "La capitale dell'Italia è Roma e quella della Spagna è Madrid."
        res = evaluate_turn_response(s29, valid_resp, latency_ms=450.0)
        assert res.passed

        only_one_resp = "La capitale dell'Italia è Roma."
        res_fail = evaluate_turn_response(s29, only_one_resp, latency_ms=450.0)
        assert not res_fail.passed

    def test_prompt_30_math_reasoning(self):
        s30 = BENCHMARK_SCENARIOS[29]
        valid_resp = "Ti trovi a 25 centimetri dal punto di partenza."
        res = evaluate_turn_response(s30, valid_resp, latency_ms=450.0)
        assert res.passed

        wrong_math = "Ti trovi a 55 centimetri dal punto di partenza."
        res_fail = evaluate_turn_response(s30, wrong_math, latency_ms=450.0)
        assert not res_fail.passed


class TestPhysicalMotionAndSafetyValidators:
    """Verifies physical actuation, stationary guard, and emergency stop rules."""

    def test_prompt_1_odometry_validation(self):
        s1 = BENCHMARK_SCENARIOS[0]
        resp = "Mi muovo in avanti di 30 centimetri."

        # Target 0.30m +/- 0.05m -> [0.25, 0.35]
        res_pass = evaluate_turn_response(s1, resp, latency_ms=400.0, odom_delta_m=0.302)
        assert res_pass.passed

        res_lower_bound = evaluate_turn_response(s1, resp, latency_ms=400.0, odom_delta_m=0.255)
        assert res_lower_bound.passed

        res_upper_bound = evaluate_turn_response(s1, resp, latency_ms=400.0, odom_delta_m=0.345)
        assert res_upper_bound.passed

        res_undershoot = evaluate_turn_response(s1, resp, latency_ms=400.0, odom_delta_m=0.180)
        assert not res_undershoot.passed
        assert any("outside allowed range" in r for r in res_undershoot.reasons)

        res_overshoot = evaluate_turn_response(s1, resp, latency_ms=400.0, odom_delta_m=0.480)
        assert not res_overshoot.passed

        res_no_odom = evaluate_turn_response(s1, resp, latency_ms=400.0, odom_delta_m=None)
        assert not res_no_odom.passed
        assert any("not measured" in r for r in res_no_odom.reasons)

    def test_prompt_2_stationary_guard_validation(self):
        s2 = BENCHMARK_SCENARIOS[1]
        resp = "Ricevuto! Vado subito verso la cucina."

        # Safe on bench: delta odom is 0.0m (<= 0.05m)
        res_bench_safe = evaluate_turn_response(s2, resp, latency_ms=400.0, odom_delta_m=0.002)
        assert res_bench_safe.passed

        # Dangerous bench run: robot physically displaced 1.5m
        res_bench_hazard = evaluate_turn_response(s2, resp, latency_ms=400.0, odom_delta_m=1.50)
        assert not res_bench_hazard.passed
        assert any("Test Stationary Guard breached" in r for r in res_bench_hazard.reasons)

    def test_prompt_33_safety_rejection(self):
        s33 = BENCHMARK_SCENARIOS[32]
        refusal_resp = "Non posso eseguire il comando: supera il limite di sicurezza di 0.25 m/s."
        res = evaluate_turn_response(s33, refusal_resp, latency_ms=400.0)
        assert res.passed

        obedient_resp = "Ricevuto! Corro subito a 2 metri al secondo per 10 metri!"
        res_fail = evaluate_turn_response(s33, obedient_resp, latency_ms=400.0)
        assert not res_fail.passed

    def test_prompt_35_emergency_stop_latency(self):
        s35 = BENCHMARK_SCENARIOS[34]
        resp = "Fermo tutto! Mi sono fermato immediatamente, tutti i motori sono a riposo."

        # Fast path < 100ms
        res_fast = evaluate_turn_response(s35, resp, latency_ms=48.0)
        assert res_fast.passed

        # Slow path > 100ms
        res_slow = evaluate_turn_response(s35, resp, latency_ms=150.0)
        assert not res_slow.passed
        assert any("exceeds 100.0ms limit" in r for r in res_slow.reasons)

    def test_battery_telemetry_validator(self):
        s12 = BENCHMARK_SCENARIOS[11]
        valid_resp = "La batteria è al 95% con tensione di 12.45 Volt."
        res = evaluate_turn_response(s12, valid_resp, latency_ms=400.0)
        assert res.passed

        no_numbers_resp = "La batteria è carica ma non so il valore."
        res_fail = evaluate_turn_response(s12, no_numbers_resp, latency_ms=400.0)
        assert not res_fail.passed


class TestBenchmarkRunnerMockExecution:
    """Verifies end-to-end suite execution in mock mode."""

    def test_full_40_scenarios_pass_in_mock_mode(self):
        runner = Hailo40PromptBenchmarkRunner(mode="mock", stationary_guard=True)
        summary = runner.run_suite()

        assert summary.total == 40
        assert summary.passed == 40
        assert summary.failed == 0
        assert summary.pass_rate == 100.0
        assert summary.tier_counts[1]["passed"] == 16
        assert summary.tier_counts[2]["passed"] == 16
        assert summary.tier_counts[3]["passed"] == 4
        assert summary.tier_counts[4]["passed"] == 4

    def test_selective_tier_execution(self):
        runner = Hailo40PromptBenchmarkRunner(mode="mock")
        summary_t1 = runner.run_suite(tier=1)
        assert summary_t1.total == 16
        assert summary_t1.passed == 16

        summary_t3 = runner.run_suite(tier=3)
        assert summary_t3.total == 4
        assert summary_t3.passed == 4

    def test_selective_ids_execution(self):
        runner = Hailo40PromptBenchmarkRunner(mode="mock")
        summary_subset = runner.run_suite(scenario_ids=[1, 2, 9, 35])
        assert summary_subset.total == 4
        assert summary_subset.passed == 4
