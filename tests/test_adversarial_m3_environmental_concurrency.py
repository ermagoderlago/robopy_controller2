"""
Adversarial Empirical Challenge Test Suite (Milestone 3 / Fase 3 Gate)
======================================================================
Empirical verification harness targeting:
1. Environmental boundaries:
   - Zero-clearance obstacles (forward, rear, 360-rotation, negative distances).
   - Negative cliff drops (Delta Z >= 0.15m exact cutoff, sub-threshold Delta Z = 0.149m, extreme drops).
2. Power boundaries & rapid voltage oscillations:
   - 12.70V charging lock boundary (12.699V vs 12.700V, rapid cyclic oscillation).
   - 10.15V docking reserve boundary (10.149V vs 10.151V, docking-only enforcement).
   - 9.85V emergency cutoff boundary (9.849V vs 9.851V, emergency shutdown).
3. Multi-threaded concurrency:
   - 16 concurrent threads attacking a single shared TrinityKinematicSandbox instance.
   - Zero race conditions, zero cross-thread state corruption, deterministic verdicts.
4. Latency distribution under CPU contention:
   - Measure latency profile (p50, p90, p95, p99, max) with background CPU burners active.
   - Assert p99 latency < 5.0 ms.
5. Inviolable Zero Motion Leak Verification:
   - Spy on mock motion publishers to assert 0 twist commands leaked across all rejections.

Standards compliance:
- SPEC-01 (Actuation & Motion Limits)
- SPEC-02 (Navigation, Clearances & Cliff Drops)
- SPEC-05 (Cognitive TRINITY Pre-flight Gate & Latency Budget)
- SPEC-06 (BMS Voltage Cutoffs & Safety Interlocks)
- marcus_core_rules.md §6.1
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

from robopy_controller.robot_ai.trinity.sandbox_rules import (
    KinematicCommand,
    KinematicLimits,
    KinematicSandboxErrorCode,
    RobotSafetyContext,
    SandboxValidationResult,
)
from robopy_controller.robot_ai.trinity.kinematic_sandbox import TrinityKinematicSandbox


class MotionLeakDetectionSpy:
    """Mock publisher spy detecting any illicit motion publications."""

    def __init__(self) -> None:
        self.published_twists: List[Dict[str, float]] = []
        self._lock = threading.Lock()

    def dispatch(self, verdict: SandboxValidationResult, cmd: Dict[str, Any]) -> bool:
        """Dispatches physical motion ONLY if explicitly authorized."""
        if not verdict.authorized:
            return False

        with self._lock:
            fn = cmd.get("name") or cmd.get("action") or cmd.get("function")
            params = verdict.sanitized_parameters
            if fn == "move_distance":
                speed = float(params.get("speed", 0.18))
                direction = params.get("direction", "avanti")
                v = speed if direction == "avanti" else -speed
                self.published_twists.append({"linear": v, "angular": 0.0})
            elif fn == "rotate_angle":
                speed = float(params.get("speed", 0.60))
                direction = params.get("direction", "sinistra")
                w = speed if direction == "sinistra" else -speed
                self.published_twists.append({"linear": 0.0, "angular": w})
            elif fn == "stop_motion":
                self.published_twists.append({"linear": 0.0, "angular": 0.0})
            else:
                self.published_twists.append({"linear": 0.0, "angular": 0.0, "fn": fn})
        return True


# =============================================================================
# 1. ADVERSARIAL ENVIRONMENTAL & CLIFF HAZARD TESTS
# =============================================================================

def test_zero_clearance_forward_obstacles():
    """Attacks forward motion with zero and sub-buffer clearances."""
    sandbox = TrinityKinematicSandbox()
    spy = MotionLeakDetectionSpy()

    zero_forward_cases = [
        # Zero and negative clearances
        (0.00, 0.10, "Zero clearance"),
        (0.001, 0.10, "Sub-millimeter clearance"),
        (-0.05, 0.10, "Corrupt negative clearance"),
        # Sub-minimum forward clearance (< 0.28m)
        (0.279, 0.05, "Just below minimum forward clearance (0.28m)"),
        (0.200, 0.05, "Well below minimum forward clearance"),
        # Below required distance + buffer (e.g. dist 0.50m + buffer 0.20m = 0.70m required)
        (0.699, 0.50, "Just below buffer requirement"),
        (0.350, 0.50, "Well below buffer requirement"),
    ]

    for obstacle_dist, req_dist, label in zero_forward_cases:
        ctx = RobotSafetyContext(min_forward_obstacle_dist=obstacle_dist)
        cmd = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": req_dist}}
        res = sandbox.validate_action(cmd, ctx)

        assert not res.authorized, f"Leak: {label} (dist={obstacle_dist}m) was authorized!"
        assert res.error_code == KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED, (
            f"Expected OBSTACLE_PROXIMITY_BLOCKED for {label}, got {res.error_code}"
        )
        spy.dispatch(res, cmd)

    assert len(spy.published_twists) == 0, "Motion leaked on zero forward clearance obstacles!"


def test_zero_clearance_rear_obstacles():
    """Attacks backward motion with zero, negative, and sub-buffer rear clearances."""
    sandbox = TrinityKinematicSandbox()
    spy = MotionLeakDetectionSpy()

    zero_rear_cases = [
        (0.00, 0.20, "Zero rear clearance"),
        (0.001, 0.20, "Sub-millimeter rear clearance"),
        (-0.10, 0.20, "Corrupt negative rear clearance"),
        (0.349, 0.20, "Just below rear buffer requirement (0.20m + 0.15m = 0.35m)"),
        (0.200, 0.20, "Obstacle right behind rear bumper"),
    ]

    for rear_dist, req_dist, label in zero_rear_cases:
        ctx = RobotSafetyContext(min_rear_obstacle_dist=rear_dist)
        cmd = {"name": "move_distance", "arguments": {"direction": "indietro", "distance_m": req_dist}}
        res = sandbox.validate_action(cmd, ctx)

        assert not res.authorized, f"Leak: {label} (rear_dist={rear_dist}m) was authorized!"
        assert res.error_code == KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED
        spy.dispatch(res, cmd)

    assert len(spy.published_twists) == 0, "Motion leaked on zero rear clearance obstacles!"


def test_zero_clearance_rotation_radius():
    """Attacks in-place rotation with zero and sub-threshold clearance radii."""
    sandbox = TrinityKinematicSandbox()
    spy = MotionLeakDetectionSpy()

    rotation_cases = [
        (0.00, "Zero radius"),
        (0.001, "Near-zero radius"),
        (-0.05, "Negative clearance radius"),
        (0.279, "Just below 0.28m rotation threshold"),
        (0.150, "Cramped corner rotation"),
    ]

    for radius, label in rotation_cases:
        ctx = RobotSafetyContext(min_clearance_radius_360=radius)
        cmd = {"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 90.0}}
        res = sandbox.validate_action(cmd, ctx)

        assert not res.authorized, f"Leak: {label} (r={radius}m) was authorized!"
        assert res.error_code == KinematicSandboxErrorCode.INSUFFICIENT_ROTATION_CLEARANCE
        spy.dispatch(res, cmd)

    assert len(spy.published_twists) == 0, "Motion leaked on cramped rotation radius!"


def test_negative_cliff_drop_boundary_at_15cm():
    """
    Stress-tests the negative obstacle / cliff drop safety invariant (SPEC-02: Delta Z >= 0.15m).
    Evaluates exact boundary (0.149m vs 0.150m vs 0.151m vs 1.0m) across multiple commands.
    """
    sandbox = TrinityKinematicSandbox()
    spy = MotionLeakDetectionSpy()

    # 1. Delta Z = 0.149m (14.9 cm, just below 15cm threshold): motion allowed if other limits pass
    ctx_safe = RobotSafetyContext(max_depth_drop=0.149, min_forward_obstacle_dist=3.0)
    cmd_fwd = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}}
    res_safe = sandbox.validate_action(cmd_fwd, ctx_safe)
    assert res_safe.authorized, "Sub-threshold drop (14.9cm) should be authorized under nominal limits"
    spy.dispatch(res_safe, cmd_fwd)
    assert len(spy.published_twists) == 1

    # 2. Cliff boundaries >= 0.15m must all be rejected with NEGATIVE_OBSTACLE_CLIFF
    cliff_values = [0.150, 0.1500001, 0.151, 0.20, 0.35, 1.20]
    motion_commands = [
        {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.30}},
        {"name": "move_distance", "arguments": {"direction": "indietro", "distance_m": 0.30}},
        {"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 45.0}},
        {"name": "navigate_to", "arguments": {"destination": "salotto"}},
        {"name": "start_frontier_exploration", "arguments": {}},
    ]

    for cliff in cliff_values:
        for cmd in motion_commands:
            ctx = RobotSafetyContext(max_depth_drop=cliff)
            res = sandbox.validate_action(cmd, ctx)
            assert not res.authorized, f"Cliff leak at Delta Z = {cliff}m for command {cmd['name']}"
            assert res.error_code == KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF, (
                f"Expected NEGATIVE_OBSTACLE_CLIFF at Delta Z = {cliff}m, got {res.error_code}"
            )
            # Spy must reject
            dispatched = spy.dispatch(res, cmd)
            assert not dispatched

    # Published count must remain exactly 1 (the single safe command from step 1)
    assert len(spy.published_twists) == 1, "Motion leaked during cliff drop attacks!"

    # 3. Emergency invariant: stop_motion must ALWAYS succeed even at a cliff edge
    ctx_cliff_edge = RobotSafetyContext(max_depth_drop=0.80)
    stop_cmd = {"name": "stop_motion", "arguments": {}}
    res_stop = sandbox.validate_action(stop_cmd, ctx_cliff_edge)
    assert res_stop.authorized, "stop_motion must be authorized even at a cliff edge!"


# =============================================================================
# 2. POWER & VOLTAGE OSCILLATION BOUNDARY ATTACKS
# =============================================================================

def test_charging_lock_boundary_12_70v_and_oscillations():
    """
    Stress-tests charging interlock boundary at 12.70V (SPEC-06 & FM-NAV-035).
    Evaluates 12.699V vs 12.700V vs 12.701V and cyclic oscillation.
    """
    sandbox = TrinityKinematicSandbox()
    spy = MotionLeakDetectionSpy()

    move_cmd = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}}

    # 1. Below 12.70V and not charging -> authorized
    ctx_sub = RobotSafetyContext(battery_voltage=12.699, is_charging=False)
    res_sub = sandbox.validate_action(move_cmd, ctx_sub)
    assert res_sub.authorized, "12.699V (not charging) must be authorized"

    # 2. At exactly 12.700V -> charging lock rejected
    ctx_exact = RobotSafetyContext(battery_voltage=12.700, is_charging=False)
    res_exact = sandbox.validate_action(move_cmd, ctx_exact)
    assert not res_exact.authorized
    assert res_exact.error_code == KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN

    # 3. Above 12.70V -> charging lock rejected
    ctx_above = RobotSafetyContext(battery_voltage=12.701, is_charging=False)
    res_above = sandbox.validate_action(move_cmd, ctx_above)
    assert not res_above.authorized
    assert res_above.error_code == KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN

    # 4. Explicit is_charging flag at lower voltage (e.g. 12.0V) -> rejected
    ctx_flag = RobotSafetyContext(battery_voltage=12.000, is_charging=True)
    res_flag = sandbox.validate_action(move_cmd, ctx_flag)
    assert not res_flag.authorized
    assert res_flag.error_code == KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN

    # 5. Rapid voltage oscillation test (100 cycles alternating between 12.69V and 12.71V)
    for cycle in range(100):
        # Odd cycle: 12.69V (discharged/operating)
        res_low = sandbox.validate_action(move_cmd, RobotSafetyContext(battery_voltage=12.690, is_charging=False))
        assert res_low.authorized, f"Failed at oscillation cycle {cycle} low"

        # Even cycle: 12.71V (charging ripple)
        res_high = sandbox.validate_action(move_cmd, RobotSafetyContext(battery_voltage=12.710, is_charging=False))
        assert not res_high.authorized, f"Failed at oscillation cycle {cycle} high"
        assert res_high.error_code == KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN
        spy.dispatch(res_high, move_cmd)

    assert len(spy.published_twists) == 0, "Motion leaked during 12.70V charging voltage oscillations!"


def test_docking_reserve_boundary_10_15v_and_oscillations():
    """
    Stress-tests docking reserve cutoff at 10.15V (SPEC-06).
    Evaluates 10.149V vs 10.150V vs 10.151V:
    - Below or equal to 10.15V: ONLY navigate_to("base") is authorized.
    - Above 10.15V: general motion permitted (clamped under ECO mode).
    """
    sandbox = TrinityKinematicSandbox()
    spy = MotionLeakDetectionSpy()

    dock_cmd = {"name": "navigate_to", "arguments": {"destination": "base"}}
    room_cmd = {"name": "navigate_to", "arguments": {"destination": "cucina"}}
    move_cmd = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50}}

    # At 10.150V (exact boundary)
    ctx_at = RobotSafetyContext(battery_voltage=10.150)
    res_dock_at = sandbox.validate_action(dock_cmd, ctx_at)
    assert res_dock_at.authorized, "navigate_to('base') must be authorized at 10.15V"

    res_room_at = sandbox.validate_action(room_cmd, ctx_at)
    assert not res_room_at.authorized
    assert res_room_at.error_code == KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY

    res_move_at = sandbox.validate_action(move_cmd, ctx_at)
    assert not res_move_at.authorized
    assert res_move_at.error_code == KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY

    # At 10.149V (just below boundary)
    ctx_below = RobotSafetyContext(battery_voltage=10.149)
    assert sandbox.validate_action(dock_cmd, ctx_below).authorized
    res_move_below = sandbox.validate_action(move_cmd, ctx_below)
    assert not res_move_below.authorized
    assert res_move_below.error_code == KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY

    # At 10.151V (just above boundary, in ECO mode range <= 10.40V)
    ctx_above = RobotSafetyContext(battery_voltage=10.151)
    res_move_above = sandbox.validate_action(move_cmd, ctx_above)
    assert res_move_above.authorized, "move_distance must be authorized at 10.151V (clamped to ECO speed)"
    # Clamped to ECO speed (max 0.125 m/s)
    assert res_move_above.clamped_arguments["speed"] <= 0.125

    # Rapid oscillation test: 100 cycles between 10.14V and 10.16V
    for cycle in range(100):
        # 10.14V: move rejected, dock authorized
        r_move = sandbox.validate_action(move_cmd, RobotSafetyContext(battery_voltage=10.140))
        r_dock = sandbox.validate_action(dock_cmd, RobotSafetyContext(battery_voltage=10.140))
        assert not r_move.authorized and r_dock.authorized
        spy.dispatch(r_move, move_cmd)

        # 10.16V: move authorized, dock authorized
        r_move2 = sandbox.validate_action(move_cmd, RobotSafetyContext(battery_voltage=10.160))
        assert r_move2.authorized

    assert len(spy.published_twists) == 0, "Motion leaked during 10.15V docking oscillations!"


def test_shutdown_critical_boundary_9_85v_and_oscillations():
    """
    Stress-tests emergency shutdown cutoff at 9.85V (SPEC-06).
    Evaluates 9.849V vs 9.850V vs 9.851V:
    - At or below 9.85V: ALL motion (including navigate_to("base")) is rejected with POWER_BATTERY_CRITICAL.
    - Above 9.85V (e.g. 9.86V): navigate_to("base") is permitted.
    - stop_motion ALWAYS authorized.
    """
    sandbox = TrinityKinematicSandbox()
    spy = MotionLeakDetectionSpy()

    dock_cmd = {"name": "navigate_to", "arguments": {"destination": "base"}}
    stop_cmd = {"name": "stop_motion", "arguments": {}}
    move_cmd = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.20}}

    # 1. At 9.850V (exact boundary)
    ctx_exact = RobotSafetyContext(battery_voltage=9.850)
    res_dock = sandbox.validate_action(dock_cmd, ctx_exact)
    assert not res_dock.authorized
    assert res_dock.error_code == KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL

    res_move = sandbox.validate_action(move_cmd, ctx_exact)
    assert not res_move.authorized
    assert res_move.error_code == KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL

    # stop_motion is always authorized
    res_stop = sandbox.validate_action(stop_cmd, ctx_exact)
    assert res_stop.authorized

    # 2. At 9.849V (below boundary)
    ctx_sub = RobotSafetyContext(battery_voltage=9.849)
    assert not sandbox.validate_action(dock_cmd, ctx_sub).authorized
    assert sandbox.validate_action(stop_cmd, ctx_sub).authorized

    # 3. At 9.851V (just above critical threshold) -> in docking reserve, docking allowed
    ctx_above = RobotSafetyContext(battery_voltage=9.851)
    res_dock_above = sandbox.validate_action(dock_cmd, ctx_above)
    assert res_dock_above.authorized, "navigate_to('base') must be authorized at 9.851V"

    # Rapid oscillation test: 100 cycles between 9.84V and 9.86V
    for cycle in range(100):
        # 9.84V: dock rejected
        r_dock = sandbox.validate_action(dock_cmd, RobotSafetyContext(battery_voltage=9.840))
        assert not r_dock.authorized
        assert r_dock.error_code == KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL
        spy.dispatch(r_dock, dock_cmd)

        # 9.86V: dock authorized
        r_dock2 = sandbox.validate_action(dock_cmd, RobotSafetyContext(battery_voltage=9.860))
        assert r_dock2.authorized

    assert len(spy.published_twists) == 0, "Motion leaked during 9.85V critical voltage oscillations!"


# =============================================================================
# 3. MULTI-THREADED CONCURRENCY STRESS HARNESS
# =============================================================================

def test_multithreaded_concurrent_evaluations_against_shared_sandbox():
    """
    Attacks a single shared instance of TrinityKinematicSandbox with 16 concurrent threads
    executing 250 diverse requests each (total 4,000 evaluations).
    Verifies:
    - 0 race conditions, deadlocks, or unhandled exceptions.
    - 100% deterministic verdicts (authorization and error codes match ground truth exactly).
    - Zero cross-thread corruption of arguments or telemetry snapshots.
    """
    sandbox = TrinityKinematicSandbox()
    num_threads = 16
    calls_per_thread = 250
    total_calls = num_threads * calls_per_thread

    # Catalog of test templates with strictly defined expected verdicts
    templates = [
        # (cmd, context, expected_auth, expected_err_code)
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50, "speed": 0.18}},
            RobotSafetyContext(battery_voltage=12.2, min_forward_obstacle_dist=3.0),
            True,
            KinematicSandboxErrorCode.SUCCESS,
        ),
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50, "speed": 0.50}},
            RobotSafetyContext(battery_voltage=12.2, min_forward_obstacle_dist=3.0),
            False,
            KinematicSandboxErrorCode.LINEAR_SPEED_EXCEEDED,
        ),
        (
            {"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 90.0, "speed": 0.60}},
            RobotSafetyContext(battery_voltage=12.0, min_clearance_radius_360=1.0),
            True,
            KinematicSandboxErrorCode.SUCCESS,
        ),
        (
            {"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 90.0, "speed": 0.60}},
            RobotSafetyContext(battery_voltage=12.0, min_clearance_radius_360=0.20),
            False,
            KinematicSandboxErrorCode.INSUFFICIENT_ROTATION_CLEARANCE,
        ),
        (
            {"name": "navigate_to", "arguments": {"destination": "base"}},
            RobotSafetyContext(battery_voltage=10.0),
            True,
            KinematicSandboxErrorCode.SUCCESS,
        ),
        (
            {"name": "navigate_to", "arguments": {"destination": "salotto"}},
            RobotSafetyContext(battery_voltage=10.0),
            False,
            KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY,
        ),
        (
            {"name": "stop_motion", "arguments": {"reason": "panic"}},
            RobotSafetyContext(battery_voltage=8.5, amygdala_state="HIJACK"),
            True,
            KinematicSandboxErrorCode.SUCCESS,
        ),
        (
            {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.30}},
            RobotSafetyContext(max_depth_drop=0.25),
            False,
            KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF,
        ),
    ]

    worker_errors: List[str] = []
    thread_results_count = [0] * num_threads

    def worker_job(thread_idx: int):
        for i in range(calls_per_thread):
            template_idx = (thread_idx + i) % len(templates)
            cmd, ctx, exp_auth, exp_err = templates[template_idx]
            
            try:
                res = sandbox.validate_action(cmd, ctx)
                if res.authorized != exp_auth:
                    worker_errors.append(
                        f"T{thread_idx} call {i}: Expected auth={exp_auth}, got {res.authorized} for {cmd['name']}"
                    )
                if res.error_code != exp_err:
                    worker_errors.append(
                        f"T{thread_idx} call {i}: Expected error={exp_err}, got {res.error_code}"
                    )
                thread_results_count[thread_idx] += 1
            except Exception as e:
                worker_errors.append(f"T{thread_idx} call {i} exception: {str(e)}")

    threads: List[threading.Thread] = []
    start_barrier = threading.Barrier(num_threads)

    def synchronized_worker(thread_idx: int):
        start_barrier.wait()
        worker_job(thread_idx)

    t_start = time.perf_counter()
    for t_id in range(num_threads):
        t = threading.Thread(target=synchronized_worker, args=(t_id,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=10.0)

    elapsed_wall = time.perf_counter() - t_start

    assert len(worker_errors) == 0, f"Concurrency errors encountered ({len(worker_errors)}): {worker_errors[:5]}"
    assert sum(thread_results_count) == total_calls, "Not all concurrent calls completed!"
    assert elapsed_wall < 5.0, f"Concurrent benchmark took too long: {elapsed_wall:.2f}s for {total_calls} calls"


# =============================================================================
# 4. LATENCY DISTRIBUTION & CPU CONTENTION STRESS HARNESS
# =============================================================================

def test_latency_distribution_under_cpu_contention():
    """
    Measures pre-flight validation latency distribution while background CPU burner
    threads generate active CPU contention (simulating Raspberry Pi 5 load).
    Asserts:
    - Sample size: 1,000 iterations across varied input types.
    - p99 latency < 5.0 ms (SPEC-05 requirement).
    - Mean latency < 1.0 ms.
    """
    sandbox = TrinityKinematicSandbox()
    num_burners = 4
    stop_event = threading.Event()

    def cpu_burner():
        """Busy loop burning arithmetic CPU cycles."""
        x = 0
        while not stop_event.is_set():
            x = (x + 1) * 31 % 1000003

    burner_threads: List[threading.Thread] = []
    for _ in range(num_burners):
        bt = threading.Thread(target=cpu_burner, daemon=True)
        burner_threads.append(bt)
        bt.start()

    latencies_ms: List[float] = []
    iterations = 1000

    # Test corpus containing raw JSON strings, dictionaries, and KinematicCommands
    mixed_corpus = [
        '{"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50, "speed": 0.20}}',
        '{"function": "rotate_angle", "parameters": {"direction": "destra", "degrees": 45.0, "speed": 0.60}}',
        '```json\n{"call": {"name": "navigate_to", "arguments": {"destination": "cucina"}}}\n```',
        {"name": "stop_motion", "arguments": {"reason": "safety"}},
        {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.30}},
        {"name": "get_battery_status", "arguments": {}},
    ]

    try:
        # Warmup
        for _ in range(50):
            sandbox.validate_action(mixed_corpus[0])

        # Timed executions
        for i in range(iterations):
            sample = mixed_corpus[i % len(mixed_corpus)]
            t0 = time.perf_counter()
            res = sandbox.validate_action(sample)
            t_eval = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(t_eval)

    finally:
        stop_event.set()
        for bt in burner_threads:
            bt.join(timeout=0.5)

    assert len(latencies_ms) == iterations

    latencies_sorted = sorted(latencies_ms)
    min_lat = latencies_sorted[0]
    p50_lat = latencies_sorted[int(iterations * 0.50)]
    p90_lat = latencies_sorted[int(iterations * 0.90)]
    p95_lat = latencies_sorted[int(iterations * 0.95)]
    p99_lat = latencies_sorted[int(iterations * 0.99)]
    max_lat = latencies_sorted[-1]
    mean_lat = sum(latencies_sorted) / iterations

    print(f"\n--- Pre-Flight Latency Distribution Under CPU Contention (n={iterations}) ---")
    print(f"Min:    {min_lat:.3f} ms")
    print(f"Mean:   {mean_lat:.3f} ms")
    print(f"p50:    {p50_lat:.3f} ms")
    print(f"p90:    {p90_lat:.3f} ms")
    print(f"p95:    {p95_lat:.3f} ms")
    print(f"p99:    {p99_lat:.3f} ms")
    print(f"Max:    {max_lat:.3f} ms")

    # Critical assertions
    assert p99_lat < 5.0, f"p99 latency ({p99_lat:.3f} ms) exceeded 5.0 ms budget!"
    assert mean_lat < 1.0, f"Mean latency ({mean_lat:.3f} ms) exceeded 1.0 ms budget!"


# =============================================================================
# 5. COGNITIVE AMYGDALA INTERLOCK ATTACKS
# =============================================================================

def test_cognitive_amygdala_adversarial_states():
    """
    Stress-tests cognitive interlocks under Amigdala PANIC/HIJACK and ANXIOUS_VIGILANCE.
    Verifies:
    - HIJACK blocks 100% of physical movement, but allows stop_motion and queries.
    - ANXIOUS_VIGILANCE throttles linear speed to 50% (0.125 m/s) and angular speed to 50% (0.50 rad/s).
    - System not ready state blocks motion with SYSTEM_NOT_READY.
    """
    sandbox = TrinityKinematicSandbox()
    spy = MotionLeakDetectionSpy()

    move_cmd = {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50, "speed": 0.25}}
    rot_cmd = {"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 90.0, "speed": 1.00}}

    # 1. Amigdala HIJACK -> Complete physical motion veto
    ctx_hijack = RobotSafetyContext(amygdala_state="HIJACK")
    res_move_hijack = sandbox.validate_action(move_cmd, ctx_hijack)
    assert not res_move_hijack.authorized
    assert res_move_hijack.error_code == KinematicSandboxErrorCode.AMYGDALA_HIJACK_ACTIVE
    spy.dispatch(res_move_hijack, move_cmd)

    res_rot_hijack = sandbox.validate_action(rot_cmd, ctx_hijack)
    assert not res_rot_hijack.authorized
    assert res_rot_hijack.error_code == KinematicSandboxErrorCode.AMYGDALA_HIJACK_ACTIVE
    spy.dispatch(res_rot_hijack, rot_cmd)

    # Invariant: stop_motion still works under HIJACK
    res_stop = sandbox.validate_action({"name": "stop_motion", "arguments": {}}, ctx_hijack)
    assert res_stop.authorized

    # Invariant: queries still work under HIJACK
    res_diag = sandbox.validate_action({"name": "get_system_health", "arguments": {}}, ctx_hijack)
    assert res_diag.authorized

    # 2. Amigdala ANXIOUS_VIGILANCE -> 50% speed clamping
    ctx_anxious = RobotSafetyContext(amygdala_state="ANXIOUS_VIGILANCE")
    res_move_anxious = sandbox.validate_action(move_cmd, ctx_anxious)
    assert res_move_anxious.authorized
    assert res_move_anxious.clamped_arguments["speed"] <= 0.125, "Linear speed was not clamped to 0.125 m/s"

    res_rot_anxious = sandbox.validate_action(rot_cmd, ctx_anxious)
    assert res_rot_anxious.authorized
    assert res_rot_anxious.clamped_arguments["speed"] <= 0.50, "Angular speed was not clamped to 0.50 rad/s"

    # 3. System not ready check
    ctx_not_ready = RobotSafetyContext(system_ready=False)
    res_not_ready = sandbox.validate_action(move_cmd, ctx_not_ready)
    assert not res_not_ready.authorized
    assert res_not_ready.error_code == KinematicSandboxErrorCode.SYSTEM_NOT_READY

    assert len(spy.published_twists) == 0, "Motion leaked during Amigdala HIJACK and not-ready states!"
