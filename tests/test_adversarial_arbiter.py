"""
Adversarial Test Suite: ConnectivityArbiter & HybridRouter Stress Harness
==========================================================================
Challenger: challenger_m2_2 (Empirical Challenger)
Milestone 2: Cognitive Intent & Fast-Path Safety Arbiter

Adversarial Attack Vectors:
1. Rapid oscillation across the 500ms threshold (hysteresis / state flipping).
2. Injected network drops and socket timeouts.
3. High-concurrency multithreaded stress with zero dropped requests.
4. Verification of strictly <100ms failover latency budget and zero unhandled exceptions.
5. Pathological inputs: boundary latencies, NaN/Inf, massive strings, embedded tokens.
"""

from __future__ import annotations

import math
import os
import random
import socket
import sys
import threading
import time
from typing import List, Tuple
from unittest.mock import MagicMock, patch

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
)


# ==============================================================================
# 1. RAPID OSCILLATION & STATE FLIPPING (HYSTERESIS STRESS)
# ==============================================================================
class TestOscillationHysteresis:
    """Attacks ConnectivityArbiter with rapid oscillation across the 500ms threshold."""

    def test_rapid_oscillation_across_500ms_threshold(self) -> None:
        """
        Injects 2,000 rapidly oscillating ping samples (450ms vs 550ms) across 500ms.
        Verifies:
        - Arbiter does not crash or raise exceptions.
        - HybridRouter dispatches 100% of requests without dropping or deadlocking.
        - Every transition occurs strictly within < 100ms (target < 1ms).
        """
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE, initial_ema_ms=500.0)
        router = HybridRouter(connectivity_arbiter=arbiter)

        oscillation_samples = [450.0, 550.0] * 1000  # 2,000 samples
        state_transitions = 0
        prev_state = arbiter.state
        max_failover_latency_us = 0.0

        for i, sample in enumerate(oscillation_samples):
            t0 = time.perf_counter_ns()
            new_state = arbiter.record_ping_sample(sample)
            latency_us = (time.perf_counter_ns() - t0) / 1000.0

            if latency_us > max_failover_latency_us:
                max_failover_latency_us = latency_us

            if new_state != prev_state:
                state_transitions += 1
                prev_state = new_state

            # Immediately test routing through HybridRouter during oscillation
            prompt = "Raccontami una storia su Marte" if i % 2 == 0 else "Vai in cucina"
            decision = router.route(prompt)

            assert isinstance(decision, RoutingDecision)
            assert decision.destination in (RouteTarget.LOCAL_QWEN, RouteTarget.GEMINI_LIVE)
            assert decision.confidence > 0.0
            assert decision.latency_us > 0.0

            if prompt == "Vai in cucina":
                assert decision.destination == RouteTarget.LOCAL_QWEN
            else:
                if new_state == ConnectivityState.OFFLINE_LOCAL:
                    assert decision.destination == RouteTarget.LOCAL_QWEN
                    assert decision.failover_applied is True
                else:
                    assert decision.destination == RouteTarget.GEMINI_LIVE
                    assert decision.failover_applied is False

        assert router.total_routed == len(oscillation_samples)
        # Verify failover latency is strictly < 100 ms (100,000 us)
        assert max_failover_latency_us < 100000.0, f"Max latency {max_failover_latency_us} us exceeded 100ms!"

    def test_boundary_micro_jitter_hysteresis(self) -> None:
        """
        Tests micro-jitter directly around 500.0 ms (e.g. 499.8 ms vs 500.2 ms).
        Observes state flipping characteristics under small perturbation.
        """
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE, initial_ema_ms=500.0)
        router = HybridRouter(connectivity_arbiter=arbiter)

        jitter_samples = [499.8, 500.2] * 500
        transitions = 0
        last_state = arbiter.state

        for sample in jitter_samples:
            state = arbiter.record_ping_sample(sample)
            if state != last_state:
                transitions += 1
                last_state = state

            # Route conversational query
            decision = router.route("Che tempo fa oggi?")
            assert decision.destination in (RouteTarget.LOCAL_QWEN, RouteTarget.GEMINI_LIVE)

        # Micro-jitter of 0.2ms around 500.0ms:
        # EMA = 0.7 * EMA + 0.3 * sample
        # 0.7 * 500 + 0.3 * 500.2 = 500.06 (> 500 -> OFFLINE_LOCAL)
        # 0.7 * 500.06 + 0.3 * 499.8 = 499.98 (<= 500 -> DEGRADED)
        # Demonstrates that every alternation flips state (no Schmitt deadband).
        assert transitions > 0
        assert router.total_routed == len(jitter_samples)


# ==============================================================================
# 2. INJECTED NETWORK DROPS & SOCKET TIMEOUTS
# ==============================================================================
class TestNetworkDropsAndSocketTimeouts:
    """Attacks ConnectivityArbiter with injected network drops, socket timeouts, and errors."""

    def test_injected_socket_timeout_via_mock(self) -> None:
        """
        Mocks socket.create_connection to raise socket.timeout.
        Verifies immediate failover to OFFLINE_LOCAL in strictly < 100ms.
        """
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE, initial_ema_ms=45.0)
        router = HybridRouter(connectivity_arbiter=arbiter)

        with patch("socket.create_connection", side_effect=socket.timeout("Socket connection timed out")):
            t0 = time.perf_counter_ns()
            measured = arbiter.probe_once(timeout_s=0.1)
            duration_ms = (time.perf_counter_ns() - t0) / 1_000_000.0

            assert measured == 9999.0
            assert arbiter.state == ConnectivityState.OFFLINE_LOCAL
            assert arbiter.is_offline is True
            assert arbiter.should_route_to_local() is True
            assert duration_ms < 100.0  # Probe + failover execution < 100 ms

            # Subsequent conversational request must immediately failover to LOCAL_QWEN
            decision = router.route("Spiegami la teoria della relatività")
            assert decision.destination == RouteTarget.LOCAL_QWEN
            assert decision.failover_applied is True
            assert decision.reason == "fallback_offline"

    def test_injected_socket_os_errors(self) -> None:
        """
        Injects various OS-level socket errors: ConnectionRefusedError, EHOSTUNREACH, etc.
        Verifies arbiter handles all exceptions cleanly without unhandled crashes.
        """
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE, initial_ema_ms=30.0)
        errors = [
            ConnectionRefusedError("Connection refused by remote host"),
            ConnectionResetError("Connection reset by peer"),
            OSError(101, "Network is unreachable"),
            OSError(113, "No route to host"),
        ]

        for err in errors:
            with patch("socket.create_connection", side_effect=err):
                res = arbiter.probe_once()
                assert res == 9999.0
                assert arbiter.is_offline is True
                assert arbiter.consecutive_failures >= 1

    def test_flapping_burst_network_drops(self) -> None:
        """
        Simulates intermittent connection bursts: 5 socket drops followed by 1 fast sample.
        Verifies arbiter stability and router zero-drop invariant over 100 flapping cycles.
        """
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE, initial_ema_ms=40.0)
        router = HybridRouter(connectivity_arbiter=arbiter)

        total_requests = 0
        for cycle in range(50):
            # 5 consecutive failures
            for f in range(5):
                arbiter.record_failure(reason=f"drop_burst_{cycle}_{f}")
                dec = router.route("Raccontami una fiaba")
                total_requests += 1
                assert dec.destination == RouteTarget.LOCAL_QWEN
                assert dec.failover_applied is True

            # 1 successful ping
            arbiter.record_ping_sample(50.0)
            dec = router.route("Raccontami una fiaba")
            total_requests += 1
            # EMA latency after 50ms sample will be low, so state returns to ONLINE
            assert dec.destination == RouteTarget.GEMINI_LIVE
            assert dec.failover_applied is False

        assert router.total_routed == total_requests
        assert router.failover_count == 50 * 5


# ==============================================================================
# 3. HIGH CONCURRENCY MULTITHREADED STRESS HARNESS
# ==============================================================================
class TestHighConcurrencyStress:
    """Stress-tests HybridRouter and ConnectivityArbiter under heavy concurrent load."""

    def test_concurrent_routing_with_injected_drops(self) -> None:
        """
        Launches 10 worker threads concurrently dispatching 20,000 requests,
        while 2 background threads inject random ping samples, network drops, and offline toggles.
        Verifies:
        - Zero unhandled exceptions across all threads.
        - Zero dropped requests (every dispatch yields a valid RoutingDecision).
        - Emergency stop is NEVER misrouted under concurrent disturbance.
        """
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE, initial_ema_ms=80.0)
        router = HybridRouter(connectivity_arbiter=arbiter)

        stop_stress = threading.Event()
        unhandled_exceptions: List[Exception] = []
        latencies_ms: List[float] = []
        lock = threading.Lock()

        test_prompts = [
            ("Fermati!", RouteTarget.FAST_PATH_STOP),
            ("Stop!", RouteTarget.FAST_PATH_STOP),
            ("Alt!", RouteTarget.FAST_PATH_STOP),
            ("Vai in cucina", RouteTarget.LOCAL_QWEN),
            ("Naviga verso il salotto", RouteTarget.LOCAL_QWEN),
            ("Qual è il voltaggio della batteria?", RouteTarget.LOCAL_QWEN),
            ("Accendi la luce", RouteTarget.LOCAL_QWEN),
            ("Raccontami una storia su Leonardo da Vinci", None),  # target depends on connectivity
            ("Cosa ne pensi della musica jazz?", None),            # target depends on connectivity
        ]

        def worker_dispatcher(num_requests: int) -> None:
            try:
                for _ in range(num_requests):
                    prompt, expected_target = random.choice(test_prompts)
                    t0 = time.perf_counter_ns()
                    decision = router.route(prompt)
                    dt_ms = (time.perf_counter_ns() - t0) / 1_000_000.0

                    assert isinstance(decision, RoutingDecision)
                    assert decision.destination in (
                        RouteTarget.FAST_PATH_STOP,
                        RouteTarget.LOCAL_QWEN,
                        RouteTarget.GEMINI_LIVE,
                    )

                    # Strict safety invariance: emergency stop must NEVER route anywhere else
                    if expected_target == RouteTarget.FAST_PATH_STOP:
                        assert decision.destination == RouteTarget.FAST_PATH_STOP

                    # Operational must ALWAYS route to LOCAL_QWEN
                    if expected_target == RouteTarget.LOCAL_QWEN:
                        assert decision.destination == RouteTarget.LOCAL_QWEN

                    with lock:
                        latencies_ms.append(dt_ms)
            except Exception as e:
                with lock:
                    unhandled_exceptions.append(e)

        def background_network_perturbator() -> None:
            while not stop_stress.is_set():
                action = random.choice(["ping_fast", "ping_slow", "drop", "toggle_offline"])
                try:
                    if action == "ping_fast":
                        arbiter.record_ping_sample(random.uniform(20.0, 150.0))
                    elif action == "ping_slow":
                        arbiter.record_ping_sample(random.uniform(550.0, 1200.0))
                    elif action == "drop":
                        arbiter.record_failure(reason="adversarial_drop")
                    elif action == "toggle_offline":
                        arbiter.set_offline(random.choice([True, False]))
                except Exception as e:
                    with lock:
                        unhandled_exceptions.append(e)
                time.sleep(0.001)

        # Start 2 background disturbance threads
        disturb_threads = [
            threading.Thread(target=background_network_perturbator, daemon=True)
            for _ in range(2)
        ]
        for t in disturb_threads:
            t.start()

        # Start 10 worker threads (1,500 requests each = 15,000 total requests)
        num_workers = 10
        reqs_per_worker = 1500
        workers = [
            threading.Thread(target=worker_dispatcher, args=(reqs_per_worker,))
            for _ in range(num_workers)
        ]

        for w in workers:
            w.start()

        for w in workers:
            w.join()

        stop_stress.set()
        for t in disturb_threads:
            t.join(timeout=0.5)

        assert len(unhandled_exceptions) == 0, f"Encountered {len(unhandled_exceptions)} unhandled exceptions: {unhandled_exceptions[:3]}"
        assert len(latencies_ms) == num_workers * reqs_per_worker

        # Check maximum dispatch latency across all 15,000 concurrent executions
        max_latency = max(latencies_ms)
        p99_latency = sorted(latencies_ms)[int(len(latencies_ms) * 0.99)]
        print(f"\n[CONCURRENT STRESS] 15,000 requests: p99={p99_latency:.3f}ms, max={max_latency:.3f}ms")
        assert max_latency < 100.0, f"Max routing latency {max_latency:.2f} ms exceeded 100 ms budget!"


# ==============================================================================
# 4. PATHOLOGICAL EDGE CASES & MALFORMED INPUTS
# ==============================================================================
class TestPathologicalInputs:
    """Stress-tests Arbiter and Router against extreme and adversarial edge cases."""

    def test_extreme_and_negative_latency_samples(self) -> None:
        """Verifies handling of extreme, zero, and negative latency samples."""
        arbiter = ConnectivityArbiter(initial_ema_ms=100.0)

        # Negative sample (should clamp to 0.0)
        arbiter.record_ping_sample(-50.0)
        assert arbiter.latency_ema_ms >= 0.0
        assert round(arbiter.latency_ema_ms, 2) == 70.0  # 0.7 * 100 + 0.3 * 0 = 70.0

        # Zero sample
        arbiter.record_ping_sample(0.0)
        assert round(arbiter.latency_ema_ms, 2) == 49.0  # 0.7 * 70 + 0.3 * 0 = 49.0

        # Massive latency sample (1,000,000 ms)
        arbiter.record_ping_sample(1_000_000.0)
        assert arbiter.latency_ema_ms > 500.0
        assert arbiter.state == ConnectivityState.OFFLINE_LOCAL
        assert arbiter.should_route_to_local() is True

    def test_massive_string_routing(self) -> None:
        """Pushes massive 100,000 character string through HybridRouter."""
        router = HybridRouter()
        massive_prompt = "Spiegami per favore " + ("parola " * 10000)

        t0 = time.perf_counter_ns()
        decision = router.route(massive_prompt)
        dt_ms = (time.perf_counter_ns() - t0) / 1_000_000.0

        assert decision.destination in (RouteTarget.LOCAL_QWEN, RouteTarget.GEMINI_LIVE)
        assert dt_ms < 50.0  # Should still process within 50 ms

    def test_embedded_emergency_stop_in_long_conversational_text(self) -> None:
        """
        Adversarial prompt: emergency stop keyword embedded inside long chatter.
        Safety invariant: Fast-Path must trigger regardless of context.
        """
        router = HybridRouter()
        sneaky_prompt = "Oggi stavo pensando a cosa fare quando all'improvviso fermati subito perché c'è un pericolo"
        decision = router.route(sneaky_prompt)

        assert decision.destination == RouteTarget.FAST_PATH_STOP
        assert decision.matched_category == "emergency_stop"

    def test_special_characters_and_whitespace(self) -> None:
        """Verifies router resilience against special characters, newlines, and null-like inputs."""
        router = HybridRouter()
        weird_inputs = [
            "",
            "   ",
            "\n\t\r",
            "??? !!! ...",
            "@#$%^&*()_+",
            "1234567890",
            "   FERMATI   \n",
        ]

        for inp in weird_inputs:
            decision = router.route(inp)
            assert isinstance(decision, RoutingDecision)
            assert decision.destination in (
                RouteTarget.FAST_PATH_STOP,
                RouteTarget.LOCAL_QWEN,
                RouteTarget.GEMINI_LIVE,
            )
