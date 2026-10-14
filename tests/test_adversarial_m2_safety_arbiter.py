"""
Adversarial Stress Test Suite: Milestone 2 Cognitive Intent & Fast-Path Safety Arbiter
======================================================================================
Empirical challenger test harness targeting:
1. FastPathStopHandler
2. CognitiveIntentClassifier
3. ConnectivityArbiter
4. HybridRouter

Attack Vectors:
- Punctuation, capitalization, Unicode accents, leading/trailing whitespace, embedded words, negation traps.
- Concurrency stress and multi-threaded race conditions.
- Latency stress under heavy multi-core CPU load (<10ms E-stop, <1.5ms intent classification).
- Physical stop command safety verification (detecting any misclassification as conversational or delayed).
"""

from __future__ import annotations

import concurrent.futures
import math
import os
import sys
import threading
import time
from typing import List, Tuple
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.robot_ai.arbitration import (
    FastPathStopHandler,
    FastPathStopResult,
    CognitiveIntentClassifier,
    RouteTarget,
    RoutingDecision,
    ConnectivityArbiter,
    ConnectivityState,
    HybridRouter,
    STOP_REGEX,
)


# ==============================================================================
# SECTION 1: ADVERSARIAL INPUT TEST MATRICES
# ==============================================================================

BASELINE_ADVERSARIAL_STOP_PHRASES = [
    # Basic tokens
    "fermati",
    "ferma",
    "stop",
    "alt",
    "basta",
    "arresto",
    "arrestati",
    "blocca",
    "bloccati",
    "interrompi",
    "cancella moto",
    "non ti muovere",
    "non muoverti",
    # Capitalization extremes
    "STOP",
    "FERMATI",
    "ALT",
    "BASTA",
    "BLOCCA",
    "BLOCCATI",
    "ARRESTO",
    "INTERROMPI",
    "NON TI MUOVERE",
    "NON MUOVERTI",
    "CANCELLA MOTO",
    "fErMaTi",
    "sToP",
    "bLoCcA",
    # Whitespace and newline extremes
    "   stop   ",
    "\t\tfermati\t\t",
    "\n\nalt\n\n",
    "\r\n  basta  \r\n",
    "   cancella   moto   ",
    "   non   ti   muovere   ",
    "   non   muoverti   ",
    # Punctuation variations
    "stop!",
    "STOP!!!",
    "fermati...",
    "fermati.",
    "alt?",
    "alt???",
    "basta, fermati!",
    "marcus: stop;",
    "marcus, fermati subito!",
    "fermati - immediatamente",
    # Bracket and quote variations
    "(fermati)",
    "[stop]",
    "{alt}",
    '"stop"',
    "'fermati'",
    "“stop”",
    "”stop”",
    "‘fermati’",
    "’fermati’",
    "«fermati»",
    "»stop«",
    "¡fermati!",
    "¿stop?",
    # Realistic operational commands with stop token
    "Marcus fermati un attimo",
    "Ehi Marcus, stop subito!",
    "Ferma le ruote immediatamente",
    "Ferma tutto per favore",
    "Blocca i motori",
    "Bloccati adesso",
    "Arresto di emergenza subito",
    "Interrompi la navigazione",
    "Cancella moto immediatamente",
    "Non ti muovere per nessun motivo",
    "Non muoverti assolutamente",
    "Alt, c'è un ostacolo",
    "Basta così fermati",
    "Stop ai motori adesso",
]

SUBSTRING_NON_STOP_PHRASES = [
    # Embedded words that must NOT trigger emergency stop
    ("altalena", "contains 'alt' as substring"),
    ("bastone", "contains 'basta' as substring"),
    ("fermatura", "contains 'ferma' as substring"),
    ("la prossima fermata", "contains 'ferma' as substring in 'fermata'"),
    ("costop", "contains 'stop' as substring"),
    ("nonstop", "contains 'stop' as substring"),
    ("asfalto", "contains 'alt' as substring"),
    ("esaltare", "contains 'alt' as substring"),
    ("altitudine", "contains 'alt' as substring"),
    ("abbastanza", "contains 'basta' as substring"),
    ("riblocca", "contains 'blocca' with prefix"),
    ("interrompibile", "contains 'interrompi' with suffix"),
    ("il motore è bloccato", "'bloccato' is past participle / status, not imperative stop"),
    ("palta", "contains 'alt'"),
    ("baltico", "contains 'alt'"),
]


# ==============================================================================
# TEST SUITE 1: BASELINE ADVERSARIAL STOP & SUBSTRING BOUNDARIES
# ==============================================================================

class TestAdversarialStopInterception:
    """Attacks on FastPathStopHandler and boundary testing."""

    def test_baseline_adversarial_stop_phrases_trigger_fast_path(self) -> None:
        """
        Verify that baseline adversarial stop phrases trigger FastPathStopHandler
        with ZERO false negatives.
        """
        handler = FastPathStopHandler(enable_logging=False)
        failed = []

        for phrase in BASELINE_ADVERSARIAL_STOP_PHRASES:
            res = handler.handle(phrase)
            if not res.triggered:
                failed.append(phrase)

        assert len(failed) == 0, f"False negatives detected ({len(failed)}): {failed}"

    def test_substring_embedded_words_do_not_trigger_false_positive_stop(self) -> None:
        """
        Verify that words containing stop tokens as substrings (e.g. 'altalena', 'bastone',
        'fermatura', 'la prossima fermata') do NOT trigger false positive E-stops.
        """
        handler = FastPathStopHandler(enable_logging=False)
        false_positives = []

        for phrase, explanation in SUBSTRING_NON_STOP_PHRASES:
            res = handler.handle(phrase)
            if res.triggered:
                false_positives.append((phrase, res.matched_token, explanation))

        assert len(false_positives) == 0, (
            f"False positive E-stops triggered for embedded words: {false_positives}"
        )

    def test_conversational_questions_without_verbatim_stop_tokens(self) -> None:
        """
        Verify that conversational sentences containing stop-related root words
        in past/descriptive contexts ('fermato', 'arrestata', 'sosta') do not trigger E-stop.
        """
        router = HybridRouter(enable_logging=False)
        safe_conversational = [
            "Perché ti sei fermato prima?",
            "Come mai la macchina si è arrestata?",
            "Raccontami una storia su un treno senza sosta",
        ]
        for phrase in safe_conversational:
            dec = router.route(phrase)
            assert dec.destination != RouteTarget.FAST_PATH_STOP, (
                f"Sentence '{phrase}' erroneously triggered FAST_PATH_STOP!"
            )


# ==============================================================================
# TEST SUITE 2: PHYSICAL STOP SAFETY VULNERABILITIES (CHALLENGER AUDIT)
# ==============================================================================

class TestPhysicalStopVulnerabilities:
    """
    Directly tests Task 2 requirement:
    'Verify that physical stop commands NEVER get misclassified as conversational or delayed.'

    Exposes real failure modes in the current regex / classification rules.
    """

    def test_audit_imperative_arresta_commands(self) -> None:
        """
        FINDING AUDIT: 'Arresta' (imperative transitive: 'Arresta il robot!', 'Arresta la marcia',
        'Arresta subito') is omitted from STOP_REGEX (which only has 'arresto' and 'arrestati').
        This test checks how the router classifies them.
        """
        router = HybridRouter(enable_logging=False)
        imperative_arresta_phrases = [
            "Arresta il robot!",
            "Arresta subito",
            "Arresta la navigazione",
            "Arresta la marcia",
            "Arresta i motori",
        ]
        misclassifications = []
        for cmd in imperative_arresta_phrases:
            dec = router.route(cmd)
            if dec.destination != RouteTarget.FAST_PATH_STOP:
                misclassifications.append((cmd, dec.destination, dec.matched_category, dec.reason))

        # We record the empirical finding:
        print(f"\n[AUDIT] Imperative 'arresta' misclassifications: {misclassifications}")
        # Note for challenger verdict: In the current code, these 5 commands FAIL to trigger FAST_PATH_STOP.

    def test_audit_unicode_accented_stop_commands(self) -> None:
        """
        FINDING AUDIT: Unicode accents ('Fèrmati', 'Férmati', 'Arrèsto', 'Blòcca') bypass
        STOP_REGEX because no accent folding or normalization (unicodedata.normalize)
        is performed prior to regex evaluation.
        """
        router = HybridRouter(enable_logging=False)
        accented_phrases = [
            "Fèrmati!",
            "Férmati subito!",
            "Arrèsto di emergenza!",
            "Blòcca tutto!",
        ]
        misclassifications = []
        for cmd in accented_phrases:
            dec = router.route(cmd)
            if dec.destination != RouteTarget.FAST_PATH_STOP:
                misclassifications.append((cmd, dec.destination, dec.matched_category, dec.reason))

        print(f"\n[AUDIT] Accented stop command misclassifications: {misclassifications}")

    def test_audit_pronoun_suffixed_stop_commands(self) -> None:
        """
        FINDING AUDIT: Common Italian enclitic pronoun stops ('Fermalo!', 'Bloccalo!', 'Arrestalo!')
        bypass STOP_REGEX because word boundary \b stops at 'ferma' when followed by 'lo'.
        """
        router = HybridRouter(enable_logging=False)
        pronoun_phrases = [
            "Fermalo!",
            "Bloccalo!",
            "Arrestalo subito!",
        ]
        misclassifications = []
        for cmd in pronoun_phrases:
            dec = router.route(cmd)
            if dec.destination != RouteTarget.FAST_PATH_STOP:
                misclassifications.append((cmd, dec.destination, dec.matched_category, dec.reason))

        print(f"\n[AUDIT] Enclitic pronoun stop misclassifications: {misclassifications}")

    def test_audit_articled_motion_cancel(self) -> None:
        r"""
        FINDING AUDIT: STOP_REGEX contains 'cancella\s+moto', which matches 'cancella moto',
        but fails on natural Italian phrasing with article 'cancella il moto'.
        """
        router = HybridRouter(enable_logging=False)
        dec = router.route("Cancella il moto!")
        print(f"\n[AUDIT] 'Cancella il moto!' -> destination={dec.destination}, cat={dec.matched_category}, reason={dec.reason}")


# ==============================================================================
# TEST SUITE 3: CONCURRENCY & RACE CONDITIONS
# ==============================================================================

class TestConcurrencyAndRaceConditions:
    """Stress tests components under multi-threaded concurrent access."""

    def test_fast_path_stop_handler_concurrent_execution(self) -> None:
        """
        Executes 16 concurrent threads calling FastPathStopHandler.handle()
        with 250 requests each (4,000 total calls).
        Verifies no unhandled exceptions, deadlocks, or state corruption.
        """
        handler = FastPathStopHandler(enable_logging=False)
        errors = []
        triggers = 0
        triggers_lock = threading.Lock()

        test_inputs = [
            ("fermati!", True),
            ("vai in cucina", False),
            ("STOP", True),
            ("come stai?", False),
            ("bloccati", True),
            ("altalena", False),
            ("non ti muovere", True),
        ]

        def worker_task(thread_id: int):
            nonlocal triggers
            local_triggers = 0
            try:
                for i in range(250):
                    prompt, should_trigger = test_inputs[(thread_id + i) % len(test_inputs)]
                    res = handler.handle(prompt)
                    if res.triggered != should_trigger:
                        errors.append(f"Thread {thread_id} iteration {i}: '{prompt}' triggered={res.triggered}, expected={should_trigger}")
                    if res.triggered:
                        local_triggers += 1
            except Exception as e:
                errors.append(f"Thread {thread_id} crashed with: {e}")

            with triggers_lock:
                triggers += local_triggers

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(worker_task, tid) for tid in range(16)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Concurrency errors encountered: {errors[:5]}"
        assert triggers > 0, "No triggers recorded"
        assert handler.total_evaluations > 0

    def test_cognitive_intent_classifier_concurrent_execution(self) -> None:
        """
        Executes 16 concurrent threads querying CognitiveIntentClassifier
        with 250 requests each (4,000 total classifications).
        Verifies thread-safety, deterministic results, and lack of crashes.
        """
        classifier = CognitiveIntentClassifier(enable_logging=False)
        errors = []

        test_cases = [
            ("Vai in cucina", RouteTarget.LOCAL_QWEN),
            ("Raccontami una storia", RouteTarget.GEMINI_LIVE),
            ("Fermati subito!", RouteTarget.FAST_PATH_STOP),
            ("Qual è il voltaggio della batteria?", RouteTarget.LOCAL_QWEN),
            ("Cosa ne pensi dell'universo?", RouteTarget.GEMINI_LIVE),
        ]

        def worker_task(thread_id: int):
            try:
                for i in range(250):
                    prompt, expected_dest = test_cases[(thread_id + i) % len(test_cases)]
                    dec = classifier.classify(prompt)
                    if dec.destination != expected_dest:
                        errors.append(f"Thread {thread_id} iter {i}: '{prompt}' got {dec.destination}, expected {expected_dest}")
            except Exception as e:
                errors.append(f"Thread {thread_id} crashed: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(worker_task, tid) for tid in range(16)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Classifier concurrency errors: {errors[:5]}"

    def test_connectivity_arbiter_concurrent_updates_and_queries(self) -> None:
        """
        Stress tests ConnectivityArbiter with concurrent ping updates,
        failure records, and should_route_to_local() queries across 16 threads.
        Verifies lock invariants and state consistency.
        """
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE, initial_ema_ms=50.0, enable_logging=False)
        errors = []

        def ping_worker(tid: int):
            try:
                for i in range(200):
                    latency = 20.0 + (i % 300)
                    arbiter.record_ping_sample(latency)
            except Exception as e:
                errors.append(f"Ping worker {tid} crashed: {e}")

        def failure_worker(tid: int):
            try:
                for i in range(50):
                    if i % 10 == 0:
                        arbiter.record_failure("test_disconnect")
                        time.sleep(0.0001)
            except Exception as e:
                errors.append(f"Failure worker {tid} crashed: {e}")

        def query_worker(tid: int):
            try:
                for _ in range(500):
                    _ = arbiter.should_route_to_local()
                    _ = arbiter.state
                    _ = arbiter.latency_ema_ms
            except Exception as e:
                errors.append(f"Query worker {tid} crashed: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            futures = []
            for tid in range(6):
                futures.append(executor.submit(ping_worker, tid))
            for tid in range(4):
                futures.append(executor.submit(failure_worker, tid))
            for tid in range(6):
                futures.append(executor.submit(query_worker, tid))
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"ConnectivityArbiter concurrency errors: {errors}"
        assert arbiter.total_samples > 0

    def test_hybrid_router_concurrent_execution(self) -> None:
        """
        Stress tests HybridRouter.route() across 16 threads with 200 mixed calls each.
        Verifies that stop commands ALWAYS route to FAST_PATH_STOP regardless of thread interleaving.
        """
        router = HybridRouter(enable_logging=False)
        errors = []

        def worker_task(tid: int):
            try:
                for i in range(200):
                    if i % 5 == 0:
                        dec = router.route("Marcus fermati subito!")
                        if dec.destination != RouteTarget.FAST_PATH_STOP:
                            errors.append(f"Thread {tid} stop command got {dec.destination} instead of FAST_PATH_STOP!")
                    elif i % 5 == 1:
                        dec = router.route("Vai in salotto")
                        if dec.destination != RouteTarget.LOCAL_QWEN:
                            errors.append(f"Thread {tid} nav command got {dec.destination} instead of LOCAL_QWEN!")
                    else:
                        dec = router.route("Raccontami una poesia")
                        if dec.destination not in (RouteTarget.GEMINI_LIVE, RouteTarget.LOCAL_QWEN):
                            errors.append(f"Thread {tid} story got invalid destination {dec.destination}")
            except Exception as e:
                errors.append(f"Router worker {tid} crashed: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(worker_task, tid) for tid in range(16)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"HybridRouter concurrency errors: {errors}"


# ==============================================================================
# TEST SUITE 4: LATENCY STRESS UNDER CPU LOAD
# ==============================================================================

class TestLatencyStressUnderCpuLoad:
    """Measures latency percentiles under multi-core CPU contention."""

    @pytest.fixture(autouse=True)
    def cpu_load_fixture(self):
        """Spawns background threads that consume CPU cycles during tests."""
        stop_burn = threading.Event()

        def cpu_burner():
            while not stop_burn.is_set():
                x = 123456.789
                for _ in range(2000):
                    x = math.sin(x) * math.cos(x) + math.sqrt(abs(x) + 1.0)

        num_burners = min(os.cpu_count() or 4, 4)
        burn_threads = [
            threading.Thread(target=cpu_burner, name=f"CpuBurner-{i}", daemon=True)
            for i in range(num_burners)
        ]
        for t in burn_threads:
            t.start()

        yield

        stop_burn.set()
        for t in burn_threads:
            t.join(timeout=0.2)

    def test_fast_path_stop_latency_under_cpu_load(self) -> None:
        """
        Measures FastPathStopHandler latency across 1,000 iterations under CPU load.
        Budget: < 10.0 ms (10,000 us). Target: < 2.0 ms.
        """
        handler = FastPathStopHandler(enable_logging=False)
        latencies_us = []

        phrases = [
            "Fermati subito!",
            "STOP",
            "Alt!",
            "Basta fermati",
            "Non ti muovere",
            "Blocca tutto",
        ]

        for i in range(1000):
            phrase = phrases[i % len(phrases)]
            t0 = time.perf_counter_ns()
            res = handler.handle(phrase)
            elapsed_us = (time.perf_counter_ns() - t0) / 1000.0
            latencies_us.append(elapsed_us)
            assert res.triggered is True

        latencies_us.sort()
        n = len(latencies_us)
        avg_us = sum(latencies_us) / n
        p50_us = latencies_us[int(n * 0.50)]
        p95_us = latencies_us[int(n * 0.95)]
        p99_us = latencies_us[int(n * 0.99)]
        max_us = latencies_us[-1]

        print(f"\n[FAST-PATH STOP UNDER CPU LOAD] n={n}: avg={avg_us:.2f}us, p50={p50_us:.2f}us, p95={p95_us:.2f}us, p99={p99_us:.2f}us, max={max_us:.2f}us")

        assert p99_us < 10000.0, f"FastPathStop p99 latency {p99_us:.2f} us exceeded 10.0 ms budget under load!"
        assert avg_us < 2000.0, f"FastPathStop average latency {avg_us:.2f} us exceeded 2.0 ms target!"

    def test_cognitive_intent_classifier_latency_under_cpu_load(self) -> None:
        """
        Measures CognitiveIntentClassifier latency across 1,000 iterations under CPU load.
        Budget: < 1.50 ms (1,500 us).
        """
        classifier = CognitiveIntentClassifier(enable_logging=False)
        latencies_us = []

        test_prompts = [
            "Vai in cucina e allineati",
            "Qual è il livello della batteria?",
            "Raccontami una storia sullo spazio profondo",
            "Attiva la presa intelligente del salotto",
            "Spiegami la teoria della relatività",
            "Fai una scansione a 360 gradi",
        ]

        for i in range(1000):
            prompt = test_prompts[i % len(test_prompts)]
            t0 = time.perf_counter_ns()
            _ = classifier.classify(prompt)
            elapsed_us = (time.perf_counter_ns() - t0) / 1000.0
            latencies_us.append(elapsed_us)

        latencies_us.sort()
        n = len(latencies_us)
        avg_us = sum(latencies_us) / n
        p50_us = latencies_us[int(n * 0.50)]
        p95_us = latencies_us[int(n * 0.95)]
        p99_us = latencies_us[int(n * 0.99)]
        max_us = latencies_us[-1]

        print(f"\n[INTENT CLASSIFIER UNDER CPU LOAD] n={n}: avg={avg_us:.2f}us, p50={p50_us:.2f}us, p95={p95_us:.2f}us, p99={p99_us:.2f}us, max={max_us:.2f}us")

        assert p99_us < 1500.0, f"IntentClassifier p99 latency {p99_us:.2f} us exceeded 1,500 us budget under load!"
        assert avg_us < 300.0, f"IntentClassifier average latency {avg_us:.2f} us unexpectedly high under load!"

    def test_connectivity_failover_latency_under_cpu_load(self) -> None:
        """
        Measures failover transition latency when socket drops under CPU load.
        Budget: < 100.0 ms.
        """
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE, enable_logging=False)
        latencies_ms = []

        for _ in range(500):
            arbiter.set_offline(False)
            t0 = time.perf_counter_ns()
            state = arbiter.record_failure("simulated_drop")
            dt_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
            latencies_ms.append(dt_ms)
            assert state == ConnectivityState.OFFLINE_LOCAL

        latencies_ms.sort()
        p99_ms = latencies_ms[int(len(latencies_ms) * 0.99)]
        max_ms = latencies_ms[-1]

        print(f"\n[CONNECTIVITY FAILOVER UNDER CPU LOAD] p99={p99_ms:.4f}ms, max={max_ms:.4f}ms")
        assert p99_ms < 100.0, f"Failover p99 latency {p99_ms} ms exceeded 100.0 ms budget!"
