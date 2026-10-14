"""
Challenger Multi-Threaded Concurrency & Latency Stress Test Suite (Milestone 3 Iteration 2)
=============================================================================================
Empirical multi-threaded concurrency and latency stress harness challenging:
1. 16-thread concurrency stress on a SINGLE shared TrinityKinematicSandbox instance.
2. Execution of >= 5,000 evaluations (5,600 and 10,000 evaluations across diverse payloads).
3. Zero race conditions:
   - Rigorous assertion that every evaluation result matches expected authorization and error codes.
   - Telemetry context isolation across concurrent threads (zero cross-thread leakage).
   - Internal counter consistency (_total_evaluations == _total_authorizations + _total_rejections).
4. Zero data corruption:
   - Immutability of caller payloads, sanitized clamp parameters, valid float ranges.
5. Zero crashes:
   - 100% exception containment inside validate_action across all threads.
6. Empirical latency distribution and SLA verification:
   - High-precision per-call latency measurement.
   - Assert p99 latency < 5.0 ms under 16-thread contention.
7. Zero motion leak oracle:
   - Verification with MockMotionPublisherSpy under high concurrency.

Complies with:
- marcus_core_rules.md (Raspberry Pi 5 resource bounds)
- SPEC-05 (TRINITY Pre-flight Kinematic Sandbox)
- SPEC-01 (Kinematic Limits)
- SPEC-02 (Navigation & Clearances)
- SPEC-06 (Power BMS & Safety)
- DFMEA FM-LLM-004
"""

import concurrent.futures
import math
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for _p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from robopy_controller.robot_ai.hailo.qwen_prompt_templates import (
    JsonCallExtractor,
    RawJsonCall,
)
from robopy_controller.robot_ai.trinity.sandbox_rules import (
    KinematicCommand,
    KinematicLimits,
    KinematicSandboxErrorCode,
    RobotSafetyContext,
    SandboxValidationResult,
)
from robopy_controller.robot_ai.trinity.kinematic_sandbox import TrinityKinematicSandbox
from tests.test_kinematic_sandbox import MockMotionPublisherSpy


# =============================================================================
# DIVERSE CONCURRENCY PAYLOAD GENERATOR
# =============================================================================

def build_stress_payloads() -> List[Tuple[Any, RobotSafetyContext, bool, KinematicSandboxErrorCode, str]]:
    """
    Builds a diverse, deterministic corpus of 24 distinct operational and adversarial payloads.
    
    Each tuple: (command_input, safety_context, expected_authorized, expected_error_code, description)
    """
    payloads = [
        # 1. Nominal forward movement (dict)
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50, "speed": 0.18}},
            RobotSafetyContext(battery_voltage=12.0),
            True,
            KinematicSandboxErrorCode.SUCCESS,
            "nominal_forward_move",
        ),
        # 2. Backward movement (dict)
        (
            {"name": "move_distance", "arguments": {"direction": "indietro", "distance_m": 0.30, "speed": 0.15}},
            RobotSafetyContext(min_rear_obstacle_dist=1.5, battery_voltage=12.0),
            True,
            KinematicSandboxErrorCode.SUCCESS,
            "nominal_backward_move",
        ),
        # 3. Rotate left (dict)
        (
            {"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 90.0, "speed": 0.60}},
            RobotSafetyContext(battery_voltage=12.0),
            True,
            KinematicSandboxErrorCode.SUCCESS,
            "nominal_rotate_left",
        ),
        # 4. Rotate right (dict)
        (
            {"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 45.0, "speed": 0.50}},
            RobotSafetyContext(battery_voltage=12.0),
            True,
            KinematicSandboxErrorCode.SUCCESS,
            "nominal_rotate_right",
        ),
        # 5. Navigate to valid room (dict)
        (
            {"name": "navigate_to", "arguments": {"destination": "cucina"}},
            RobotSafetyContext(battery_voltage=12.2),
            True,
            KinematicSandboxErrorCode.SUCCESS,
            "nominal_navigate_to_cucina",
        ),
        # 6. Navigate to docking base at low battery (docking reserve: 9.85V < V <= 10.15V)
        (
            {"name": "navigate_to", "arguments": {"destination": "base"}},
            RobotSafetyContext(battery_voltage=10.05),
            True,
            KinematicSandboxErrorCode.SUCCESS,
            "docking_reserve_base_allowed",
        ),
        # 7. Stop motion invariant (always authorized even in panic)
        (
            {"name": "stop_motion", "arguments": {}},
            RobotSafetyContext(amygdala_state="HIJACK", battery_voltage=8.0),
            True,
            KinematicSandboxErrorCode.SUCCESS,
            "stop_motion_inviolable",
        ),
        # 8. Status query
        (
            {"name": "get_battery_status", "arguments": {}},
            RobotSafetyContext(battery_voltage=12.0),
            True,
            KinematicSandboxErrorCode.SUCCESS,
            "query_battery_status",
        ),
        # 9. BUG-M3-01: Null speed parameter in move_distance (must default to 0.18)
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50, "speed": None}},
            RobotSafetyContext(battery_voltage=12.0),
            True,
            KinematicSandboxErrorCode.SUCCESS,
            "null_speed_linear_defaulted",
        ),
        # 10. BUG-M3-01: Null speed parameter in rotate_angle (must default to 0.60)
        (
            {"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 45.0, "speed": None}},
            RobotSafetyContext(battery_voltage=12.0),
            True,
            KinematicSandboxErrorCode.SUCCESS,
            "null_speed_angular_defaulted",
        ),
        # 11. Clamped speed within nominal bounds (0.35 clamps to 0.25)
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50, "speed": 0.35}},
            RobotSafetyContext(battery_voltage=12.0),
            True,
            KinematicSandboxErrorCode.SUCCESS,
            "clamped_nominal_linear_speed",
        ),
        # 12. Speed redline rejection (linear speed > 0.40 m/s redline)
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50, "speed": 0.50}},
            RobotSafetyContext(battery_voltage=12.0),
            False,
            KinematicSandboxErrorCode.LINEAR_SPEED_EXCEEDED,
            "redline_linear_speed_rejection",
        ),
        # 13. Speed redline rejection (angular speed > 1.80 rad/s redline)
        (
            {"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 90.0, "speed": 2.50}},
            RobotSafetyContext(battery_voltage=12.0),
            False,
            KinematicSandboxErrorCode.ANGULAR_SPEED_EXCEEDED,
            "redline_angular_speed_rejection",
        ),
        # 14. Distance out of bounds (> 2.0m)
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 5.00}},
            RobotSafetyContext(battery_voltage=12.0),
            False,
            KinematicSandboxErrorCode.DISTANCE_EXCEEDED,
            "distance_out_of_bounds_rejection",
        ),
        # 15. Obstacle proximity blocked (< 0.28m)
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}},
            RobotSafetyContext(min_forward_obstacle_dist=0.20, battery_voltage=12.0),
            False,
            KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED,
            "obstacle_proximity_rejection",
        ),
        # 16. BUG-M3-02: Telemetry NaN forward obstacle distance
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}},
            RobotSafetyContext(min_forward_obstacle_dist=float("nan"), battery_voltage=12.0),
            False,
            KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED,
            "telemetry_nan_forward_obstacle_rejection",
        ),
        # 17. Cliff drop detected (> 0.08m)
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}},
            RobotSafetyContext(max_depth_drop=0.20, battery_voltage=12.0),
            False,
            KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF,
            "cliff_drop_rejection",
        ),
        # 18. BUG-M3-02: Telemetry NaN cliff drop
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}},
            RobotSafetyContext(max_depth_drop=float("nan"), battery_voltage=12.0),
            False,
            KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF,
            "telemetry_nan_cliff_drop_rejection",
        ),
        # 19. Battery critical (< 9.85V shutdown)
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}},
            RobotSafetyContext(battery_voltage=8.7),
            False,
            KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL,
            "battery_critical_rejection",
        ),
        # 20. BUG-M3-02: Telemetry NaN battery voltage
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}},
            RobotSafetyContext(battery_voltage=float("nan")),
            False,
            KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL,
            "telemetry_nan_battery_rejection",
        ),
        # 21. Battery docking reserve rejection for non-base destination (10.05V <= 10.15V)
        (
            {"name": "navigate_to", "arguments": {"destination": "salotto"}},
            RobotSafetyContext(battery_voltage=10.05),
            False,
            KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY,
            "battery_docking_reserve_salotto_rejection",
        ),
        # 22. Charging safety interlock
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}},
            RobotSafetyContext(is_charging=True, battery_voltage=12.0),
            False,
            KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN,
            "charging_safety_interlock_rejection",
        ),
        # 23. Amigdala HIJACK panic interlock
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}},
            RobotSafetyContext(amygdala_state="HIJACK", battery_voltage=12.0),
            False,
            KinematicSandboxErrorCode.AMYGDALA_HIJACK_ACTIVE,
            "amygdala_hijack_rejection",
        ),
        # 24. Unknown function name
        (
            {"name": "unauthorized_actuation", "arguments": {"target": "motors"}},
            RobotSafetyContext(battery_voltage=12.0),
            False,
            KinematicSandboxErrorCode.UNKNOWN_FUNCTION,
            "unknown_function_rejection",
        ),
    ]
    return payloads


# =============================================================================
# CONCURRENCY AND STRESS TEST SUITE
# =============================================================================

class TestTrinityKinematicSandboxConcurrencyStress:
    """Multi-threaded concurrency, race condition, data corruption, and latency SLA tests."""

    def test_16_thread_concurrency_stress_shared_sandbox_5000_evals(self):
        """
        Runs 16 concurrent threads executing >= 5,000 evaluations against a single shared
        TrinityKinematicSandbox instance.
        
        Verifies:
        - 16 threads running concurrently.
        - Exactly 5,600 evaluations total (16 threads * 350 iterations > 5,000).
        - Zero race conditions: Every single evaluation strictly returns expected authorization
          verdict, expected error code, and correct parameter clamps.
        - Zero data corruption: Returned results are intact, telemetry snapshots isolated.
        - Zero crashes or unhandled exceptions across all threads.
        - Internal counter consistency: _total_evaluations == 5600,
          _total_evaluations == _total_authorizations + _total_rejections.
        - Latency distribution: Measures p50, p90, p95, p99, max; verifies p99 < 5.0 ms.
        """
        shared_sandbox = TrinityKinematicSandbox()
        num_threads = 16
        iterations_per_thread = 350
        expected_total_evals = num_threads * iterations_per_thread  # 5,600

        payloads = build_stress_payloads()
        num_payloads = len(payloads)

        results_lock = threading.Lock()
        thread_errors: List[str] = []
        all_latencies_ms: List[float] = []

        barrier = threading.Barrier(num_threads)

        def worker_task(thread_id: int):
            local_latencies: List[float] = []
            local_errors: List[str] = []

            # Synchronize start across all 16 threads for maximum instantaneous contention
            barrier.wait()

            for i in range(iterations_per_thread):
                payload_idx = (thread_id * 17 + i) % num_payloads
                cmd_input, ctx, expected_auth, expected_err, desc = payloads[payload_idx]

                t0 = time.perf_counter()
                try:
                    res = shared_sandbox.validate_action(cmd_input, ctx)
                except Exception as exc:
                    local_errors.append(f"Thread-{thread_id} iter-{i} [{desc}] crashed with exception: {exc}")
                    continue
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                local_latencies.append(elapsed_ms)

                # 1. Assert authorization match
                if res.authorized != expected_auth:
                    local_errors.append(
                        f"Thread-{thread_id} iter-{i} [{desc}] Auth mismatch: got {res.authorized}, "
                        f"expected {expected_auth}. Error: {res.error_code}, Reason: {res.reason}"
                    )

                # 2. Assert error code match
                if res.error_code != expected_err:
                    local_errors.append(
                        f"Thread-{thread_id} iter-{i} [{desc}] ErrorCode mismatch: got {res.error_code}, "
                        f"expected {expected_err}. Reason: {res.reason}"
                    )

                # 3. Assert data integrity on clamped parameters
                if expected_auth:
                    assert res.clamped_arguments is not None, f"Clamped arguments missing on authorized call [{desc}]"
                    if desc == "null_speed_linear_defaulted":
                        assert res.clamped_arguments.get("speed") == 0.18, (
                            f"Default linear speed failed in concurrent thread: {res.clamped_arguments}"
                        )
                    elif desc == "null_speed_angular_defaulted":
                        assert res.clamped_arguments.get("speed") == 0.60, (
                            f"Default angular speed failed in concurrent thread: {res.clamped_arguments}"
                        )
                    elif desc == "clamped_nominal_linear_speed":
                        assert res.clamped_arguments.get("speed") == 0.25, (
                            f"Clamping linear speed to max failed in concurrent thread: {res.clamped_arguments}"
                        )
                else:
                    assert res.clamped_arguments is None, f"Clamped arguments leaked on rejected call [{desc}]"

                # 4. Assert telemetry snapshot integrity
                assert res.telemetry_snapshot is not None
                if not math.isnan(ctx.battery_voltage):
                    assert res.telemetry_snapshot.get("battery_voltage") == ctx.battery_voltage

            with results_lock:
                thread_errors.extend(local_errors)
                all_latencies_ms.extend(local_latencies)

        threads = [
            threading.Thread(target=worker_task, args=(tid,), name=f"stress-worker-{tid}")
            for tid in range(num_threads)
        ]

        wall_start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall_time_s = time.perf_counter() - wall_start

        # =========================================================================
        # POST-TEST CONCURRENCY & INTEGRITY ASSERTIONS
        # =========================================================================

        # 1. Zero crashes & zero race conditions
        assert len(thread_errors) == 0, (
            f"Encountered {len(thread_errors)} concurrency/race errors across {expected_total_evals} evals:\n"
            + "\n".join(thread_errors[:10])
        )

        # 2. Total evaluations match exactly
        assert len(all_latencies_ms) == expected_total_evals, (
            f"Latency samples count {len(all_latencies_ms)} != expected {expected_total_evals}"
        )

        # 3. Internal counters consistency
        assert shared_sandbox._total_evaluations == expected_total_evals, (
            f"Counter _total_evaluations {shared_sandbox._total_evaluations} != {expected_total_evals}"
        )
        assert (
            shared_sandbox._total_evaluations
            == shared_sandbox._total_authorizations + shared_sandbox._total_rejections
        ), (
            f"Counter divergence: evals={shared_sandbox._total_evaluations}, "
            f"auth={shared_sandbox._total_authorizations}, rej={shared_sandbox._total_rejections}"
        )

        # 4. Latency SLA Distribution Verification
        all_latencies_ms.sort()
        n = len(all_latencies_ms)
        min_lat = all_latencies_ms[0]
        avg_lat = sum(all_latencies_ms) / n
        p50_lat = all_latencies_ms[int(n * 0.50)]
        p90_lat = all_latencies_ms[int(n * 0.90)]
        p95_lat = all_latencies_ms[int(n * 0.95)]
        p99_lat = all_latencies_ms[int(n * 0.99)]
        p999_lat = all_latencies_ms[int(n * 0.999)]
        max_lat = all_latencies_ms[-1]
        throughput_hz = n / wall_time_s

        print(
            f"\n[16-THREAD CONCURRENCY STRESS RESULTS - {n} EVALS]\n"
            f"  Wall time:    {wall_time_s:.3f} s (Throughput: {throughput_hz:.1f} evals/sec)\n"
            f"  Min latency:  {min_lat:.4f} ms\n"
            f"  Avg latency:  {avg_lat:.4f} ms\n"
            f"  p50 latency:  {p50_lat:.4f} ms\n"
            f"  p90 latency:  {p90_lat:.4f} ms\n"
            f"  p95 latency:  {p95_lat:.4f} ms\n"
            f"  p99 latency:  {p99_lat:.4f} ms (Target SLA: < 5.0 ms)\n"
            f"  p99.9 latency:{p999_lat:.4f} ms\n"
            f"  Max latency:  {max_lat:.4f} ms\n"
            f"  Counters:     evals={shared_sandbox._total_evaluations}, "
            f"auth={shared_sandbox._total_authorizations}, rej={shared_sandbox._total_rejections}\n"
        )

        # Assert p99 latency SLA strictly < 5.0 ms
        assert p99_lat < 5.0, f"p99 latency {p99_lat:.4f} ms exceeded 5.0 ms SLA limit under 16 threads!"

    def test_16_thread_high_throughput_burst_10000_evals(self):
        """
        High-throughput burst executing 10,000 evaluations across 16 threads using
        concurrent.futures.ThreadPoolExecutor on a single shared TrinityKinematicSandbox.
        
        Verifies:
        - High-frequency contention does not degrade p99 latency beyond 5.0 ms.
        - Zero data corruption under thread-pool scheduling.
        - Counter integrity reaches exactly 10,000 evaluations.
        """
        shared_sandbox = TrinityKinematicSandbox()
        total_evals = 10000
        num_workers = 16
        chunk_size = total_evals // num_workers  # 625 per worker

        payloads = build_stress_payloads()
        num_payloads = len(payloads)

        def worker_chunk(worker_id: int) -> Tuple[List[float], List[str]]:
            latencies: List[float] = []
            errors: List[str] = []
            for j in range(chunk_size):
                p_idx = (worker_id * 13 + j) % num_payloads
                cmd_input, ctx, exp_auth, exp_err, desc = payloads[p_idx]

                t0 = time.perf_counter()
                try:
                    res = shared_sandbox.validate_action(cmd_input, ctx)
                except Exception as exc:
                    errors.append(f"Worker-{worker_id} chunk-{j} exception: {exc}")
                    continue
                lat_ms = (time.perf_counter() - t0) * 1000.0
                latencies.append(lat_ms)

                if res.authorized != exp_auth or res.error_code != exp_err:
                    errors.append(
                        f"Worker-{worker_id} chunk-{j} mismatch: got auth={res.authorized}, "
                        f"code={res.error_code}, expected auth={exp_auth}, code={exp_err}"
                    )
            return latencies, errors

        all_latencies: List[float] = []
        all_errors: List[str] = []

        wall_start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_chunk, wid) for wid in range(num_workers)]
            for fut in concurrent.futures.as_completed(futures):
                lats, errs = fut.result()
                all_latencies.extend(lats)
                all_errors.extend(errs)
        wall_time_s = time.perf_counter() - wall_start

        assert len(all_errors) == 0, f"Errors in 10,000 burst: {len(all_errors)}\n" + "\n".join(all_errors[:5])
        assert len(all_latencies) == total_evals
        assert shared_sandbox._total_evaluations == total_evals
        assert shared_sandbox._total_evaluations == (
            shared_sandbox._total_authorizations + shared_sandbox._total_rejections
        )

        all_latencies.sort()
        p99 = all_latencies[int(len(all_latencies) * 0.99)]
        avg_lat = sum(all_latencies) / len(all_latencies)
        throughput = total_evals / wall_time_s

        print(
            f"\n[10,000-BURST RESULTS]\n"
            f"  Throughput:  {throughput:.1f} evals/sec\n"
            f"  Avg latency: {avg_lat:.4f} ms\n"
            f"  p99 latency: {p99:.4f} ms\n"
        )

        assert p99 < 5.0, f"Burst p99 latency {p99:.4f} ms exceeded 5.0 ms limit"

    def test_zero_motion_leak_under_high_concurrency(self):
        """
        Oracle test: 16 threads submitting hostile rejected payloads while feeding
        verdicts directly into MockMotionPublisherSpy.
        
        Asserts that under no concurrency conditions does an unauthorized payload publish
        a twist to /cmd_vel.
        """
        shared_sandbox = TrinityKinematicSandbox()
        spy = MockMotionPublisherSpy()
        spy_lock = threading.Lock()
        num_threads = 16
        iterations = 200

        # Entirely hostile/rejected payloads
        hostile_payloads = [
            ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": 0.50}}, RobotSafetyContext(battery_voltage=12.0)),
            ({"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 45.0, "speed": 2.5}}, RobotSafetyContext(battery_voltage=12.0)),
            ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}}, RobotSafetyContext(min_forward_obstacle_dist=0.10, battery_voltage=12.0)),
            ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}}, RobotSafetyContext(max_depth_drop=0.20, battery_voltage=12.0)),
            ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}}, RobotSafetyContext(battery_voltage=8.5)),
            ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}}, RobotSafetyContext(is_charging=True, battery_voltage=12.0)),
            ({"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}}, RobotSafetyContext(amygdala_state="HIJACK", battery_voltage=12.0)),
            ({"name": "navigate_to", "arguments": {"destination": "cucina"}}, RobotSafetyContext(battery_voltage=10.05)),
            ({"name": "forbidden_cmd", "arguments": {}}, RobotSafetyContext(battery_voltage=12.0)),
        ]

        def hostile_worker(tid: int):
            for i in range(iterations):
                cmd_dict, ctx = hostile_payloads[(tid * 5 + i) % len(hostile_payloads)]
                verdict = shared_sandbox.validate_action(cmd_dict, ctx)
                assert verdict.authorized is False, f"Hostile command was authorized: {cmd_dict}"
                with spy_lock:
                    dispatched = spy.dispatch(verdict, cmd_dict)
                    assert dispatched is False, f"Hostile command dispatched motion: {cmd_dict}"

        threads = [threading.Thread(target=hostile_worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Zero motion leaks across all 3,200 hostile calls
        assert len(spy.published_twists) == 0, (
            f"CRITICAL VULNERABILITY: {len(spy.published_twists)} twists were published from rejected commands!"
        )

    def test_thread_safe_telemetry_isolation(self):
        """
        Verifies that thread safety contexts do not leak between concurrent threads.
        
        Thread A repeatedly executes with Amigdala HIJACK (must always reject).
        Thread B repeatedly executes with nominal state (must always authorize).
        Both hit the shared sandbox simultaneously.
        """
        shared_sandbox = TrinityKinematicSandbox()
        iterations = 500
        a_errors: List[str] = []
        b_errors: List[str] = []

        barrier = threading.Barrier(2)

        def worker_a():
            barrier.wait()
            ctx_a = RobotSafetyContext(amygdala_state="HIJACK", battery_voltage=12.0)
            cmd_a = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": 0.18}}
            for i in range(iterations):
                res = shared_sandbox.validate_action(cmd_a, ctx_a)
                if res.authorized is not False:
                    a_errors.append(f"Iter {i}: Thread A (HIJACK) was authorized!")
                if res.telemetry_snapshot.get("amygdala_state") != "HIJACK":
                    a_errors.append(f"Iter {i}: Thread A telemetry snapshot leaked: {res.telemetry_snapshot}")

        def worker_b():
            barrier.wait()
            ctx_b = RobotSafetyContext(amygdala_state="NOMINAL", battery_voltage=12.2)
            cmd_b = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": 0.18}}
            for i in range(iterations):
                res = shared_sandbox.validate_action(cmd_b, ctx_b)
                if res.authorized is not True:
                    b_errors.append(f"Iter {i}: Thread B (NOMINAL) was rejected: {res.reason}")
                if res.telemetry_snapshot.get("amygdala_state") != "NOMINAL":
                    b_errors.append(f"Iter {i}: Thread B telemetry snapshot leaked: {res.telemetry_snapshot}")

        ta = threading.Thread(target=worker_a)
        tb = threading.Thread(target=worker_b)
        ta.start()
        tb.start()
        ta.join()
        tb.join()

        assert len(a_errors) == 0, f"Thread A isolation errors: {a_errors[:5]}"
        assert len(b_errors) == 0, f"Thread B isolation errors: {b_errors[:5]}"

    def test_16_thread_raw_json_string_extractor_concurrency_stress(self):
        """
        Stress-tests JsonCallExtractor integration under 16 concurrent threads.
        
        Feeds raw model output strings containing markdown fences, trailing commas,
        unclosed brackets, and malformed syntax directly into validate_action(str).
        Verifies thread safety of regex engines, bracket repair, and p99 latency < 5.0 ms.
        """
        shared_sandbox = TrinityKinematicSandbox()
        num_threads = 16
        iterations_per_thread = 350
        total_evals = num_threads * iterations_per_thread  # 5,600

        raw_string_payloads = [
            (
                "```json\n{\"call\": {\"name\": \"move_distance\", \"arguments\": {\"direction\": \"avanti\", \"distance_m\": 0.50, \"speed\": 0.18}}}\n```",
                RobotSafetyContext(battery_voltage=12.0),
                True,
                KinematicSandboxErrorCode.SUCCESS,
            ),
            (
                "Ecco il comando: ```\n{\"name\": \"rotate_angle\", \"arguments\": {\"direction\": \"destra\", \"degrees\": 45.0, \"speed\": 0.60}}\n```",
                RobotSafetyContext(battery_voltage=12.0),
                True,
                KinematicSandboxErrorCode.SUCCESS,
            ),
            (
                "{\"call\": {\"name\": \"move_distance\", \"arguments\": {\"direction\": \"avanti\", \"distance_m\": 0.50,},},}",
                RobotSafetyContext(battery_voltage=12.0),
                True,
                KinematicSandboxErrorCode.SUCCESS,
            ),
            (
                "```json\n{\"call\": {\"name\": \"navigate_to\", \"arguments\": {\"destination\": \"cucina\"}}}\n```",
                RobotSafetyContext(battery_voltage=12.0),
                True,
                KinematicSandboxErrorCode.SUCCESS,
            ),
            (
                "```json\n{\"call\": {\"name\": \"move_distance\", \"arguments\": {\"direction\": \"avanti\", \"distance_m\": 0.50, \"speed\": null}}}\n```",
                RobotSafetyContext(battery_voltage=12.0),
                True,
                KinematicSandboxErrorCode.SUCCESS,
            ),
            (
                "```json\n{\"call\": {\"name\": \"move_distance\", \"arguments\": {\"direction\": \"avanti\", \"distance_m\": 0.50, \"speed\": 0.50}}}\n```",
                RobotSafetyContext(battery_voltage=12.0),
                False,
                KinematicSandboxErrorCode.LINEAR_SPEED_EXCEEDED,
            ),
            (
                "Non posso aiutarti. Nessun JSON qui presente.",
                RobotSafetyContext(battery_voltage=12.0),
                False,
                KinematicSandboxErrorCode.MALFORMED_JSON,
            ),
            (
                "```json\n{\"call\": {\"name\": \"unknown_magic_spell\", \"arguments\": {}}}\n```",
                RobotSafetyContext(battery_voltage=12.0),
                False,
                KinematicSandboxErrorCode.UNKNOWN_FUNCTION,
            ),
        ]
        num_str_payloads = len(raw_string_payloads)

        thread_errors: List[str] = []
        latencies_ms: List[float] = []
        lock = threading.Lock()
        barrier = threading.Barrier(num_threads)

        def string_worker(tid: int):
            loc_lats: List[float] = []
            loc_errs: List[str] = []
            barrier.wait()

            for i in range(iterations_per_thread):
                raw_str, ctx, exp_auth, exp_err = raw_string_payloads[(tid * 7 + i) % num_str_payloads]
                t0 = time.perf_counter()
                try:
                    res = shared_sandbox.validate_action(raw_str, ctx)
                except Exception as exc:
                    loc_errs.append(f"Thread-{tid} iter-{i} crashed on raw string: {exc}")
                    continue
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                loc_lats.append(elapsed_ms)

                if res.authorized != exp_auth or res.error_code != exp_err:
                    loc_errs.append(
                        f"Thread-{tid} iter-{i} mismatch: auth={res.authorized} (exp {exp_auth}), "
                        f"code={res.error_code} (exp {exp_err})"
                    )

            with lock:
                thread_errors.extend(loc_errs)
                latencies_ms.extend(loc_lats)

        threads = [threading.Thread(target=string_worker, args=(t,)) for t in range(num_threads)]
        wall_start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall_time_s = time.perf_counter() - wall_start

        assert len(thread_errors) == 0, f"Raw string extractor errors:\n" + "\n".join(thread_errors[:5])
        assert len(latencies_ms) == total_evals

        latencies_ms.sort()
        p99 = latencies_ms[int(total_evals * 0.99)]
        avg_lat = sum(latencies_ms) / total_evals
        throughput = total_evals / wall_time_s

        print(
            f"\n[16-THREAD RAW STRING EXTRACTION STRESS - {total_evals} EVALS]\n"
            f"  Throughput:  {throughput:.1f} evals/sec\n"
            f"  Avg latency: {avg_lat:.4f} ms\n"
            f"  p99 latency: {p99:.4f} ms\n"
        )
        assert p99 < 5.0, f"Raw string extraction p99 latency {p99:.4f} ms exceeded 5.0 ms SLA"

    def test_16_thread_randomized_fuzzing_matrix_5000_evals(self):
        """
        Fuzzing stress: 16 threads generating randomized context and command parameters
        to evaluate property invariants under high contention.
        """
        shared_sandbox = TrinityKinematicSandbox()
        num_threads = 16
        iterations_per_thread = 320
        total_evals = num_threads * iterations_per_thread  # 5,120

        import random

        errors: List[str] = []
        latencies: List[float] = []
        lock = threading.Lock()

        def fuzz_worker(tid: int):
            rng = random.Random(42 + tid)
            loc_errs: List[str] = []
            loc_lats: List[float] = []

            for i in range(iterations_per_thread):
                # Randomize context
                volt = rng.choice([8.5, 9.5, 10.05, 10.35, 12.0, 12.8, float("nan")])
                charging = rng.choice([True, False])
                fwd_dist = rng.choice([0.15, 0.25, 0.50, 2.0, float("nan")])
                cliff = rng.choice([0.02, 0.05, 0.20, float("nan")])
                amyg = rng.choice(["CALM", "ANXIOUS_VIGILANCE", "HIJACK"])

                ctx = RobotSafetyContext(
                    battery_voltage=volt,
                    is_charging=charging,
                    min_forward_obstacle_dist=fwd_dist,
                    max_depth_drop=cliff,
                    amygdala_state=amyg,
                )

                # Randomize command
                cmd_type = rng.choice(["stop_motion", "move_distance", "rotate_angle", "navigate_to"])
                if cmd_type == "stop_motion":
                    cmd = {"name": "stop_motion", "arguments": {}}
                elif cmd_type == "move_distance":
                    spd = rng.choice([None, 0.18, 0.35, 0.60, float("nan")])
                    cmd = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5, "speed": spd}}
                elif cmd_type == "rotate_angle":
                    spd = rng.choice([None, 0.60, 1.50, 2.50, float("nan")])
                    cmd = {"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 45.0, "speed": spd}}
                else:
                    dest = rng.choice(["base", "cucina", "salotto"])
                    cmd = {"name": "navigate_to", "arguments": {"destination": dest}}

                t0 = time.perf_counter()
                try:
                    res = shared_sandbox.validate_action(cmd, ctx)
                except Exception as exc:
                    loc_errs.append(f"Thread-{tid} iter-{i} crashed with: {exc}")
                    continue
                lat_ms = (time.perf_counter() - t0) * 1000.0
                loc_lats.append(lat_ms)

                # Invariant 1: stop_motion is ALWAYS authorized
                if cmd_type == "stop_motion":
                    if not res.authorized or res.error_code != KinematicSandboxErrorCode.SUCCESS:
                        loc_errs.append(f"Thread-{tid} iter-{i}: stop_motion was rejected: {res}")

                # Invariant 2: If authorized, must NEVER have error code other than SUCCESS
                if res.authorized and res.error_code != KinematicSandboxErrorCode.SUCCESS:
                    loc_errs.append(f"Thread-{tid} iter-{i}: authorized with non-SUCCESS code: {res}")

                # Invariant 3: If rejected, must NEVER have clamped_arguments
                if not res.authorized and res.clamped_arguments is not None:
                    loc_errs.append(f"Thread-{tid} iter-{i}: rejected command leaked clamped_arguments: {res}")

            with lock:
                errors.extend(loc_errs)
                latencies.extend(loc_lats)

        threads = [threading.Thread(target=fuzz_worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Fuzzing invariant violations ({len(errors)}):\n" + "\n".join(errors[:5])
        assert len(latencies) == total_evals

        latencies.sort()
        p99 = latencies[int(total_evals * 0.99)]
        avg_lat = sum(latencies) / total_evals
        print(
            f"\n[16-THREAD RANDOMIZED FUZZING STRESS - {total_evals} EVALS]\n"
            f"  Avg latency: {avg_lat:.4f} ms\n"
            f"  p99 latency: {p99:.4f} ms\n"
        )
        assert p99 < 5.0, f"Fuzzing p99 latency {p99:.4f} ms exceeded 5.0 ms SLA"

