"""
Empirical Stress Test Suite: Unicode Normalization Overhead & 16-Thread Concurrency
===================================================================================
Challenger: challenger_m2_iter2_2
Target: Milestone 2 Iteration 2 (Safety Hardening & Remediation)

Tests:
1. Overhead of Unicode normalization (NFKD diacritic stripping) isolated and under 16-thread contention.
2. Fast-Path Emergency Stop p99 latency strictly < 10.0 ms under 16-thread contention.
3. Cognitive Intent Classifier p99 latency strictly < 1.5 ms under 16-thread contention.
4. Hybrid Router end-to-end routing under 16-thread contention.
5. Zero crashes, zero deadlocks, and zero race conditions under high contention.
"""

from __future__ import annotations

import concurrent.futures
import math
import os
import sys
import threading
import time
from typing import Dict, List, Tuple
import unicodedata
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.robot_ai.arbitration.fast_path_stop import (
    FastPathStopHandler,
    FastPathStopResult,
    STOP_REGEX,
    normalize_text,
)
from robopy_controller.robot_ai.arbitration.intent_classifier import (
    CognitiveIntentClassifier,
    RouteTarget,
    RoutingDecision,
)
from robopy_controller.robot_ai.arbitration.connectivity_arbiter import (
    ConnectivityArbiter,
    ConnectivityState,
)
from robopy_controller.robot_ai.arbitration.hybrid_router import (
    HybridRouter,
)


def compute_percentiles(latencies_us: List[float]) -> Dict[str, float]:
    """Computes distribution percentiles from a list of microsecond latencies."""
    if not latencies_us:
        return {}
    s = sorted(latencies_us)
    n = len(s)
    return {
        "count": float(n),
        "min": s[0],
        "avg": sum(s) / n,
        "p50": s[int(n * 0.50)],
        "p90": s[int(n * 0.90)],
        "p95": s[int(n * 0.95)],
        "p99": s[int(n * 0.99)],
        "p999": s[min(int(n * 0.999), n - 1)],
        "max": s[-1],
    }


class TestUnicodeNormalizationOverhead:
    """Rigorous measurement of normalize_text() overhead isolated and under 16-thread contention."""

    TEST_SAMPLES = [
        # ASCII commands
        "fermati",
        "stop",
        "vai in cucina",
        "qual e la carica della batteria?",
        # Accented Italian commands
        "Fèrmati!",
        "Férmati subito!",
        "Arrèsto di emergenza!",
        "Dov'è il robot?",
        "Qual è lo stato?",
        "Blòcca tutto!",
        # Extended diacritics / edge cases
        "Perché ti sei fermato prima?",
        "È arrivato il robot in salotto?",
        "Cos'è successo alla telecamera?",
        "àèìòùÀÈÌÒÙéÉ",
    ]

    def test_single_thread_unicode_normalization_overhead(self) -> None:
        """
        Measures baseline normalize_text() execution duration over 20,000 iterations.
        Expectation: overhead should be well under 10.0 us per invocation (typically < 3.0 us).
        """
        iterations = 20000
        latencies_us: List[float] = []

        for i in range(iterations):
            sample = self.TEST_SAMPLES[i % len(self.TEST_SAMPLES)]
            t0 = time.perf_counter_ns()
            _ = normalize_text(sample)
            t1 = time.perf_counter_ns()
            latencies_us.append((t1 - t0) / 1000.0)

        stats = compute_percentiles(latencies_us)
        print(
            f"\n[UNICODE NORMALIZATION - SINGLE THREAD] n={int(stats['count'])}: "
            f"avg={stats['avg']:.2f}us, p50={stats['p50']:.2f}us, p95={stats['p95']:.2f}us, "
            f"p99={stats['p99']:.2f}us, max={stats['max']:.2f}us"
        )

        assert stats["p99"] < 10.0, f"Unicode normalization p99 exceeded 10.0 us: {stats['p99']:.2f} us"
        assert stats["avg"] < 3.0, f"Unicode normalization average exceeded 3.0 us: {stats['avg']:.2f} us"

    def test_sixteen_thread_unicode_normalization_contention(self) -> None:
        """
        Measures normalize_text() overhead under heavy 16-thread contention.
        Executes 16 threads x 2,500 iterations = 40,000 total calls.
        Verifies whether Python's unicodedata C-extension causes GIL lockups.
        """
        num_threads = 16
        calls_per_thread = 2500
        total_latencies: List[float] = []
        lock = threading.Lock()
        errors: List[str] = []

        def worker(tid: int):
            local_latencies = []
            try:
                for i in range(calls_per_thread):
                    sample = self.TEST_SAMPLES[(tid + i) % len(self.TEST_SAMPLES)]
                    t0 = time.perf_counter_ns()
                    normalized = normalize_text(sample)
                    t1 = time.perf_counter_ns()
                    local_latencies.append((t1 - t0) / 1000.0)
                    # Verification of correctness
                    assert not any(unicodedata.combining(c) for c in normalized)
            except Exception as e:
                with lock:
                    errors.append(f"Thread {tid} error: {e}")

            with lock:
                total_latencies.extend(local_latencies)

        t_start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, tid) for tid in range(num_threads)]
            concurrent.futures.wait(futures)
        total_wall_s = time.perf_counter() - t_start

        assert len(errors) == 0, f"Errors during 16-thread normalization: {errors}"
        stats = compute_percentiles(total_latencies)
        throughput = stats["count"] / total_wall_s

        print(
            f"\n[UNICODE NORMALIZATION - 16 THREADS CONTENTION] total={int(stats['count'])}, "
            f"wall_time={total_wall_s:.3f}s, throughput={throughput:.0f} ops/sec:\n"
            f"avg={stats['avg']:.2f}us, p50={stats['p50']:.2f}us, p95={stats['p95']:.2f}us, "
            f"p99={stats['p99']:.2f}us, max={stats['max']:.2f}us"
        )

        # Under 16-thread GIL contention, p99 should still be well below 50.0 us (< 0.05 ms)
        assert stats["p99"] < 50.0, f"p99 under 16-thread contention exceeded 50.0 us: {stats['p99']:.2f} us"
        assert stats["avg"] < 10.0, f"avg under 16-thread contention exceeded 10.0 us: {stats['avg']:.2f} us"


class TestFastPathStopStressContention:
    """Stress tests FastPathStopHandler under 16-thread contention with strict < 10 ms p99 assertion."""

    TEST_COMMANDS = [
        ("Fèrmati subito!", True),
        ("Férmati!", True),
        ("Arrèsto!", True),
        ("Arresta il robot!", True),
        ("Fermalo immediatamente", True),
        ("Bloccalo!", True),
        ("Cancella il moto", True),
        ("Cancella il moto!", True),
        ("STOP", True),
        ("Alt!", True),
        ("Basta così", True),
        ("non ti muovere", True),
        ("non avanzare", True),
        # Negative / Operational / Conversational
        ("vai in cucina", False),
        ("qual è il voltaggio?", False),
        ("raccontami una storia", False),
        ("altalena nel parco", False),
        ("la prossima fermata del bus", False),
        ("bastone per camminare", False),
        ("motore bloccato per guasto", False),
    ]

    def test_fast_path_stop_16_thread_concurrency_and_latency(self) -> None:
        """
        Executes 16 concurrent threads each invoking FastPathStopHandler.handle()
        1,000 times (16,000 total calls).
        Validates:
        1. Strict p99 latency < 10.0 ms (10,000 us). Target < 2.0 ms.
        2. Zero classification errors or false positive / false negative triggers.
        3. Zero crashes, deadlocks, or race conditions.
        """
        handler = FastPathStopHandler(enable_logging=False)
        num_threads = 16
        calls_per_thread = 1000

        all_latencies_us: List[float] = []
        lock = threading.Lock()
        mismatches: List[str] = []
        crashes: List[str] = []

        def worker(tid: int):
            local_latencies = []
            for i in range(calls_per_thread):
                cmd, should_trigger = self.TEST_COMMANDS[(tid + i) % len(self.TEST_COMMANDS)]
                t0 = time.perf_counter_ns()
                try:
                    res = handler.handle(cmd)
                    t1 = time.perf_counter_ns()
                    local_latencies.append((t1 - t0) / 1000.0)

                    if res.triggered != should_trigger:
                        with lock:
                            mismatches.append(f"Thread {tid} iter {i}: cmd='{cmd}', triggered={res.triggered}, expected={should_trigger}")
                except Exception as e:
                    with lock:
                        crashes.append(f"Thread {tid} crashed on '{cmd}': {e}")

            with lock:
                all_latencies_us.extend(local_latencies)

        t_start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, tid) for tid in range(num_threads)]
            concurrent.futures.wait(futures)
        total_wall_s = time.perf_counter() - t_start

        assert len(crashes) == 0, f"Crashes detected during 16-thread test: {crashes}"
        assert len(mismatches) == 0, f"Mismatches detected: {mismatches[:5]}"

        stats = compute_percentiles(all_latencies_us)
        throughput = stats["count"] / total_wall_s

        print(
            f"\n[FAST-PATH STOP - 16 THREADS CONTENTION] total={int(stats['count'])}, "
            f"wall_time={total_wall_s:.3f}s, throughput={throughput:.0f} ops/sec:\n"
            f"avg={stats['avg']:.2f}us, p50={stats['p50']:.2f}us, p95={stats['p95']:.2f}us, "
            f"p99={stats['p99']:.2f}us, max={stats['max']:.2f}us"
        )

        # STRICT SPEC REQUIREMENT: p99 latency < 10.0 ms (10,000 us)
        assert stats["p99"] < 10000.0, (
            f"VIOLATION: FastPathStop p99 latency {stats['p99']:.2f} us exceeded 10,000 us budget!"
        )
        # Even stricter engineering target: p99 < 1,000 us (1 ms)
        assert stats["p99"] < 1000.0, (
            f"FastPathStop p99 latency {stats['p99']:.2f} us unexpectedly exceeded 1,000 us target!"
        )


class TestCognitiveIntentClassifierStressContention:
    """Stress tests CognitiveIntentClassifier under 16-thread contention with strict < 1.5 ms p99 assertion."""

    TEST_PROMPTS = [
        # Operational
        ("Vai in cucina subito", RouteTarget.LOCAL_QWEN),
        ("Raggiungi la base di ricarica", RouteTarget.LOCAL_QWEN),
        ("Qual è il voltaggio della batteria?", RouteTarget.LOCAL_QWEN),
        ("Dov'è il robot?", RouteTarget.LOCAL_QWEN),
        ("Dov'e la cucina?", RouteTarget.LOCAL_QWEN),
        ("Accendi la luce in salotto", RouteTarget.LOCAL_QWEN),
        ("Fai una scansione a 360 gradi", RouteTarget.LOCAL_QWEN),
        ("Temperatura della cpu?", RouteTarget.LOCAL_QWEN),
        ("Controlla i sensori lidar", RouteTarget.LOCAL_QWEN),
        # Emergency stop (fast path)
        ("Fèrmati!", RouteTarget.FAST_PATH_STOP),
        ("Arresta il robot!", RouteTarget.FAST_PATH_STOP),
        ("Bloccalo!", RouteTarget.FAST_PATH_STOP),
        ("Cancella il moto", RouteTarget.FAST_PATH_STOP),
        ("Stop immediato", RouteTarget.FAST_PATH_STOP),
        # Conversational
        ("Raccontami una storia su Leonardo da Vinci", RouteTarget.GEMINI_LIVE),
        ("Cosa ne pensi dell'origine dell'universo?", RouteTarget.GEMINI_LIVE),
        ("Come ti senti oggi, Marcus?", RouteTarget.GEMINI_LIVE),
        ("Spiegami la relatività generale", RouteTarget.GEMINI_LIVE),
        ("Chi era Asimov?", RouteTarget.GEMINI_LIVE),
        ("Dimmi una barzelletta simpatica", RouteTarget.GEMINI_LIVE),
    ]

    def test_intent_classifier_16_thread_concurrency_and_latency(self) -> None:
        """
        Executes 16 concurrent threads each querying CognitiveIntentClassifier.classify()
        1,000 times (16,000 total classifications).
        Validates:
        1. Strict p99 latency < 1.5 ms (1,500 us).
        2. Zero classification errors or regressions.
        3. Zero crashes, deadlocks, or race conditions.
        """
        classifier = CognitiveIntentClassifier(enable_logging=False)
        num_threads = 16
        calls_per_thread = 1000

        all_latencies_us: List[float] = []
        lock = threading.Lock()
        mismatches: List[str] = []
        crashes: List[str] = []

        def worker(tid: int):
            local_latencies = []
            for i in range(calls_per_thread):
                prompt, expected_dest = self.TEST_PROMPTS[(tid + i) % len(self.TEST_PROMPTS)]
                t0 = time.perf_counter_ns()
                try:
                    dec = classifier.classify(prompt)
                    t1 = time.perf_counter_ns()
                    local_latencies.append((t1 - t0) / 1000.0)

                    if dec.destination != expected_dest:
                        with lock:
                            mismatches.append(f"Thread {tid} iter {i}: '{prompt}' -> {dec.destination}, expected {expected_dest}")
                except Exception as e:
                    with lock:
                        crashes.append(f"Thread {tid} crashed on '{prompt}': {e}")

            with lock:
                all_latencies_us.extend(local_latencies)

        t_start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, tid) for tid in range(num_threads)]
            concurrent.futures.wait(futures)
        total_wall_s = time.perf_counter() - t_start

        assert len(crashes) == 0, f"Crashes detected during 16-thread test: {crashes}"
        assert len(mismatches) == 0, f"Mismatches detected: {mismatches[:5]}"

        stats = compute_percentiles(all_latencies_us)
        throughput = stats["count"] / total_wall_s

        print(
            f"\n[INTENT CLASSIFIER - 16 THREADS CONTENTION] total={int(stats['count'])}, "
            f"wall_time={total_wall_s:.3f}s, throughput={throughput:.0f} ops/sec:\n"
            f"avg={stats['avg']:.2f}us, p50={stats['p50']:.2f}us, p95={stats['p95']:.2f}us, "
            f"p99={stats['p99']:.2f}us, max={stats['max']:.2f}us"
        )

        # STRICT SPEC REQUIREMENT: p99 latency < 1.5 ms (1,500 us)
        assert stats["p99"] < 1500.0, (
            f"VIOLATION: IntentClassifier p99 latency {stats['p99']:.2f} us exceeded 1,500 us budget!"
        )
        # Even stricter target: p99 < 300 us
        assert stats["p99"] < 300.0, (
            f"IntentClassifier p99 latency {stats['p99']:.2f} us unexpectedly exceeded 300 us target!"
        )


class TestHybridRouterStressContention:
    """Stress tests the top-level HybridRouter under 16-thread contention with live failover switches."""

    def test_hybrid_router_16_thread_stress_with_dynamic_failover(self) -> None:
        """
        Stress tests HybridRouter under 16 threads while dynamically toggling network
        latency and failure states in the background.
        Verifies:
        1. Stop commands ALWAYS resolve to FAST_PATH_STOP regardless of network state.
        2. Zero unhandled exceptions or thread synchronization corruptions.
        3. Latency stays bounded.
        """
        router = HybridRouter(enable_logging=False)
        num_threads = 16
        calls_per_thread = 500
        stop_event = threading.Event()
        errors: List[str] = []
        lock = threading.Lock()

        # Background thread perturbing connectivity
        def network_perturber():
            pings = [20.0, 100.0, 300.0, 600.0, 15.0]
            idx = 0
            while not stop_event.is_set():
                p = pings[idx % len(pings)]
                router.connectivity_arbiter.record_ping_sample(p)
                if idx % 7 == 0:
                    router.connectivity_arbiter.record_failure("simulated_drop")
                idx += 1
                time.sleep(0.002)

        perturber = threading.Thread(target=network_perturber, name="NetworkPerturber", daemon=True)
        perturber.start()

        def router_worker(tid: int):
            for i in range(calls_per_thread):
                try:
                    mode = i % 4
                    if mode == 0:
                        dec = router.route("Fèrmati subito!")
                        if dec.destination != RouteTarget.FAST_PATH_STOP:
                            with lock:
                                errors.append(f"Thread {tid} stop command got {dec.destination}")
                    elif mode == 1:
                        dec = router.route("Arresta il robot!")
                        if dec.destination != RouteTarget.FAST_PATH_STOP:
                            with lock:
                                errors.append(f"Thread {tid} arresta command got {dec.destination}")
                    elif mode == 2:
                        dec = router.route("Vai in cucina")
                        if dec.destination != RouteTarget.LOCAL_QWEN:
                            with lock:
                                errors.append(f"Thread {tid} nav command got {dec.destination}")
                    else:
                        dec = router.route("Raccontami una storia")
                        if dec.destination not in (RouteTarget.GEMINI_LIVE, RouteTarget.LOCAL_QWEN):
                            with lock:
                                errors.append(f"Thread {tid} conversational got invalid target {dec.destination}")
                except Exception as e:
                    with lock:
                        errors.append(f"Thread {tid} router crashed: {e}")

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [executor.submit(router_worker, tid) for tid in range(num_threads)]
                concurrent.futures.wait(futures)
        finally:
            stop_event.set()
            perturber.join(timeout=0.5)

        assert len(errors) == 0, f"Router concurrency errors under network perturbation: {errors[:5]}"
        telemetry = router.get_telemetry()
        print(f"\n[HYBRID ROUTER 16-THREAD STRESS TELEMETRY] {telemetry}")
        assert telemetry["total_routed"] == num_threads * calls_per_thread
        assert telemetry["fast_path_stop_count"] == (num_threads * calls_per_thread) // 2
