"""
Empirical Challenger Test Suite: Milestone 2 Iteration 2 (Safety Hardening & Linguistic Adversarial Attack)
=============================================================================================================
Author: challenger_m2_iter2_1
Target: HybridRouter, FastPathStopHandler, CognitiveIntentClassifier, ConnectivityArbiter

Validates:
1. 100% resolution of the 5 previously failing categories identified in challenger_m2_1 handoff.
2. Adversarial attacks across:
   - Case permutations (mixed, uppercase, title, alternating).
   - Accented variations (acute, grave, multi-diacritic).
   - Complex punctuation (quotes, braces, ellipsis, inverted Spanish-style punctuation, dashes).
   - Multiple stop words combined in single utterance.
   - Situational awareness variations with contractions and apostrophes.
   - False positive boundaries (ensuring non-stop words never trigger emergency stop).
   - Latency budget profiles under adversarial payloads.
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Tuple

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.robot_ai.arbitration import (
    FastPathStopHandler,
    CognitiveIntentClassifier,
    ConnectivityArbiter,
    ConnectivityState,
    HybridRouter,
    RouteTarget,
    RoutingDecision,
)


# ==============================================================================
# SECTION 1: VERIFICATION OF THE 5 PREVIOUSLY FAILING CATEGORIES
# ==============================================================================

class TestFiveRemediatedCategories:
    """Verifies that all 5 previously failing categories are 100% resolved."""

    @pytest.fixture(autouse=True)
    def setup_router(self):
        self.router = HybridRouter(enable_logging=False)

    @pytest.mark.parametrize("phrase", [
        "Arresta il robot!",
        "Arresta subito",
        "Arresta la navigazione",
        "Arresta la marcia",
        "Arresta i motori",
    ])
    def test_imperative_arresta_commands_resolved(self, phrase: str):
        decision = self.router.route(phrase)
        assert decision.destination == RouteTarget.FAST_PATH_STOP, (
            f"Imperative command '{phrase}' failed to trigger FAST_PATH_STOP! Got: {decision.destination}"
        )
        assert decision.matched_category == "emergency_stop"
        assert decision.latency_us < 10000.0

    @pytest.mark.parametrize("phrase", [
        "Fèrmati!",
        "Férmati!",
        "Arrèsto!",
        "Arrèsto di emergenza!",
        "Blòcca tutto!",
        "Blòccati subito!",
        "Fèrma i motori!",
    ])
    def test_accented_stop_commands_resolved(self, phrase: str):
        decision = self.router.route(phrase)
        assert decision.destination == RouteTarget.FAST_PATH_STOP, (
            f"Accented command '{phrase}' failed to trigger FAST_PATH_STOP! Got: {decision.destination}"
        )
        assert decision.matched_category == "emergency_stop"
        assert decision.latency_us < 10000.0

    @pytest.mark.parametrize("phrase", [
        "Fermalo!",
        "Bloccalo!",
        "Arrestalo!",
        "Arrestalo subito!",
        "Fermalo immediatamente!",
        "Bloccalo adesso!",
    ])
    def test_enclitic_pronoun_stops_resolved(self, phrase: str):
        decision = self.router.route(phrase)
        assert decision.destination == RouteTarget.FAST_PATH_STOP, (
            f"Enclitic command '{phrase}' failed to trigger FAST_PATH_STOP! Got: {decision.destination}"
        )
        assert decision.matched_category == "emergency_stop"
        assert decision.latency_us < 10000.0

    @pytest.mark.parametrize("phrase", [
        "Cancella il moto",
        "Cancella il moto!",
        "cancella il moto subito",
        "Non avanzare",
        "non avanzare assolutamente",
    ])
    def test_articled_motion_cancel_resolved(self, phrase: str):
        decision = self.router.route(phrase)
        assert decision.destination == RouteTarget.FAST_PATH_STOP, (
            f"Motion cancel command '{phrase}' failed to trigger FAST_PATH_STOP! Got: {decision.destination}"
        )
        assert decision.matched_category == "emergency_stop"
        assert decision.latency_us < 10000.0

    @pytest.mark.parametrize("query", [
        "Dov'è il robot?",
        "Dov'e il robot?",
        "Dov’è il robot?",
        "Dov’e’ il robot?",
        "Dov'è la cucina?",
        "Dov'è la base di ricarica?",
        "Dov'è finito Marcus?",
    ])
    def test_situational_queries_resolved(self, query: str):
        decision = self.router.route(query)
        assert decision.destination == RouteTarget.LOCAL_QWEN, (
            f"Situational query '{query}' failed to route to LOCAL_QWEN! Got: {decision.destination}"
        )
        assert decision.matched_category in ("operational_query", "operational", "motion")
        assert decision.latency_us < 1500.0


# ==============================================================================
# SECTION 2: ADVERSARIAL LINGUISTIC ATTACKS
# ==============================================================================

class TestAdversarialLinguisticAttacks:
    """Stress tests HybridRouter with adversarial case, punctuation, and multi-word variations."""

    @pytest.fixture(autouse=True)
    def setup_router(self):
        self.router = HybridRouter(enable_logging=False)

    @pytest.mark.parametrize("prompt,expected_target", [
        # Mixed and extreme casing
        ("ARRESTA IL ROBOT!", RouteTarget.FAST_PATH_STOP),
        ("arresta il robot!", RouteTarget.FAST_PATH_STOP),
        ("aRrEsTa Il RoBoT!", RouteTarget.FAST_PATH_STOP),
        ("FÈRMATI SUBITO!", RouteTarget.FAST_PATH_STOP),
        ("fÈrMaTi SuBiTo!", RouteTarget.FAST_PATH_STOP),
        ("bLoCcAlO aDeSsO!", RouteTarget.FAST_PATH_STOP),
        ("cAnCeLlA iL mOtO!", RouteTarget.FAST_PATH_STOP),
        ("dOv'È iL rObOt?", RouteTarget.LOCAL_QWEN),
        # Punctuation variations
        ("Arresta, subito!", RouteTarget.FAST_PATH_STOP),
        ("Fèrmati... adesso!", RouteTarget.FAST_PATH_STOP),
        ("«Fermalo!»", RouteTarget.FAST_PATH_STOP),
        ("“Bloccalo!”", RouteTarget.FAST_PATH_STOP),
        ("¿Arrestalo subito?", RouteTarget.FAST_PATH_STOP),
        ("¡Férmati!", RouteTarget.FAST_PATH_STOP),
        ("Stop... basta... alt!", RouteTarget.FAST_PATH_STOP),
        ("Non ti muovere... fermati!", RouteTarget.FAST_PATH_STOP),
        ("NON AVANZARE!!!", RouteTarget.FAST_PATH_STOP),
        ("Dov'è il robot?!", RouteTarget.LOCAL_QWEN),
        ("Dov'è la cucina?!?", RouteTarget.LOCAL_QWEN),
        # Multi-stop word combinations
        ("Stop fermati subito!", RouteTarget.FAST_PATH_STOP),
        ("Alt, basta, fermalo!", RouteTarget.FAST_PATH_STOP),
        ("Bloccalo, arresta tutto!", RouteTarget.FAST_PATH_STOP),
        ("Interrompi e cancella il moto!", RouteTarget.FAST_PATH_STOP),
        ("Basta fermati stop alt!", RouteTarget.FAST_PATH_STOP),
        # Conversational framing around stop token
        ("Marcus ti prego fermati subito", RouteTarget.FAST_PATH_STOP),
        ("Ehi robot stop adesso", RouteTarget.FAST_PATH_STOP),
        ("Attenzione pericolo blocca i motori", RouteTarget.FAST_PATH_STOP),
        ("Pericolo imminente arresta la marcia", RouteTarget.FAST_PATH_STOP),
    ])
    def test_adversarial_stop_and_query_variations(self, prompt: str, expected_target: RouteTarget):
        decision = self.router.route(prompt)
        assert decision.destination == expected_target, (
            f"Adversarial prompt '{prompt}' expected {expected_target.value}, got {decision.destination.value}"
        )

    def test_false_positive_preservation(self):
        """
        Adversarial Boundary Test:
        Confirms that words sharing roots with stop keywords do NOT trigger emergency stop.
        """
        non_stop_phrases = [
            ("altalena", "contains 'alt'"),
            ("bastone", "contains 'basta'"),
            ("fermatura", "contains 'ferma'"),
            ("la prossima fermata", "contains 'ferma' in 'fermata'"),
            ("asfalto", "contains 'alt'"),
            ("abbastanza", "contains 'basta'"),
            ("Perché ti sei fermato prima?", "conversational question"),
            ("La macchina si è arrestata", "conversational observation"),
        ]
        for phrase, reason in non_stop_phrases:
            decision = self.router.route(phrase)
            assert decision.destination != RouteTarget.FAST_PATH_STOP, (
                f"False positive triggered for '{phrase}' ({reason})! Matched token: {decision.matched_category}"
            )


# ==============================================================================
# SECTION 3: LATENCY BUDGET PROFILE UNDER ADVERSARIAL PAYLOADS
# ==============================================================================

class TestAdversarialLatencyProfile:
    """Verifies that Unicode normalization and regex expansion strictly adhere to latency budgets."""

    def test_fast_path_stop_adversarial_latency_profile(self):
        handler = FastPathStopHandler(enable_logging=False)
        adversarial_samples = [
            "Arresta il robot!",
            "Fèrmati subito!",
            "Férmati immediatamente!",
            "Arrèsto di emergenza!",
            "Bloccalo adesso!",
            "Cancella il moto!",
            "Non avanzare per nessun motivo!",
            "«Fermalo!»",
            "Stop fermati basta alt!",
        ]

        latencies_us: List[float] = []
        for _ in range(500):
            for sample in adversarial_samples:
                t0 = time.perf_counter_ns()
                res = handler.handle(sample)
                dt_us = (time.perf_counter_ns() - t0) / 1000.0
                latencies_us.append(dt_us)
                assert res.triggered is True

        latencies_us.sort()
        n = len(latencies_us)
        avg_us = sum(latencies_us) / n
        p50_us = latencies_us[int(n * 0.50)]
        p95_us = latencies_us[int(n * 0.95)]
        p99_us = latencies_us[int(n * 0.99)]
        max_us = latencies_us[-1]

        print(f"\n[ADVERSARIAL FAST-PATH LATENCY] n={n}: avg={avg_us:.2f}us, p50={p50_us:.2f}us, p95={p95_us:.2f}us, p99={p99_us:.2f}us, max={max_us:.2f}us")

        assert p99_us < 10000.0, f"p99 latency {p99_us:.2f} us exceeded 10.0 ms budget!"
        assert avg_us < 50.0, f"Average latency {avg_us:.2f} us unexpectedly high for regex evaluation!"
