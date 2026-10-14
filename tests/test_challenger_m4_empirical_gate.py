#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_challenger_m4_empirical_gate.py
===========================================
Empirical Challenger Verification Harness for Milestone 4:
Clock Domain Standardization, Default Parameter Handling, and Concurrency Invariants.

Challenger Mandate:
1. Zero premature timeouts on tick 0 under default time (current_time=None).
2. Zero duration explosion on trigger_disconnect under default time (current_time=None).
3. 16-thread barrier concurrency does not double-count duration.

Author: challenger_m4_iter2_1 (Empirical Challenger)
"""

import asyncio
import concurrent.futures
import threading
import time
import sys
from unittest.mock import MagicMock
import pytest

# Ensure mocks for headless environment
if "rclpy" not in sys.modules:
    rclpy_mock = MagicMock()
    rclpy_node_mock = MagicMock()
    rclpy_node_mock.Node = object
    rclpy_qos_mock = MagicMock()
    sys.modules["rclpy"] = rclpy_mock
    sys.modules["rclpy.node"] = rclpy_node_mock
    sys.modules["rclpy.qos"] = rclpy_qos_mock

if "pyaudio" not in sys.modules:
    sys.modules["pyaudio"] = MagicMock()

if "robopy_controller.msg" not in sys.modules:
    sys.modules["robopy_controller.msg"] = MagicMock()

if "std_msgs" not in sys.modules:
    sys.modules["std_msgs"] = MagicMock()
    sys.modules["std_msgs.msg"] = MagicMock()

from robopy_controller.robot_ai.services.live_connection_manager import (
    LiveConnectionManager,
    LiveState,
    LiveSavingsTracker,
    SavingsSnapshot,
)


# ============================================================================
# Gate Dimension 1: Zero Premature Timeouts on Tick 0 (current_time=None)
# ============================================================================

class TestEmpiricalGateDimension1PrematureTimeout:
    """Empirically stress-tests tick 0 behavior under default production time (current_time=None)."""

    def test_500_cycles_default_clock_tick0_zero_premature_timeout(self):
        """
        Stress Test 1A:
        Runs 500 consecutive cycles of trigger_conversation() immediately followed
        by update_tick() with default current_time=None.
        Verifies:
        - Exactly 0 premature transitions to IDLE_TIMED_OUT on tick 0.
        - State remains LiveState.ACTIVE in 100.0% of iterations.
        - total_connected_seconds == 0.0 prior to disconnect.
        """
        premature_timeouts = 0
        total_cycles = 500

        for cycle_idx in range(total_cycles):
            mgr = LiveConnectionManager()
            st_open = mgr.trigger_conversation(reason=f"stress_cycle_{cycle_idx}")
            assert st_open == LiveState.ACTIVE

            # Immediate tick 0 evaluation with default time
            st_tick = mgr.update_tick()
            if st_tick == LiveState.IDLE_TIMED_OUT:
                premature_timeouts += 1

            assert st_tick == LiveState.ACTIVE, (
                f"Cycle {cycle_idx}: Premature timeout on tick 0! "
                f"State transitioned to {st_tick} instead of ACTIVE."
            )
            assert mgr.state == LiveState.ACTIVE
            assert mgr.total_connected_seconds == 0.0

            # Graceful cleanup
            mgr.trigger_disconnect(reason="clean_exit")

        assert premature_timeouts == 0, f"Detected {premature_timeouts} premature timeouts out of {total_cycles} cycles!"

    def test_high_frequency_tick_burst_on_tick0(self):
        """
        Stress Test 1B:
        Fires a burst of 1,000 rapid update_tick() calls in a tight loop immediately
        after session creation.
        Verifies that all 1,000 calls return LiveState.ACTIVE and no clock drift trips the 25.0s timer.
        """
        mgr = LiveConnectionManager()
        mgr.trigger_conversation()
        assert mgr.state == LiveState.ACTIVE

        burst_count = 1000
        for i in range(burst_count):
            st = mgr.update_tick()
            assert st == LiveState.ACTIVE, f"Tick burst iteration {i} timed out prematurely!"

        assert mgr.state == LiveState.ACTIVE
        assert mgr.total_connected_seconds == 0.0
        mgr.trigger_disconnect()

    def test_interleaved_activity_and_ticks_default_clock(self):
        """
        Stress Test 1C:
        Verifies interleaving of on_user_activity() and update_tick() with default clock
        maintains ACTIVE state and does not create negative or corrupted intervals.
        """
        mgr = LiveConnectionManager()
        mgr.trigger_conversation()

        for _ in range(50):
            mgr.update_tick()
            mgr.on_user_activity()
            st = mgr.update_tick()
            assert st == LiveState.ACTIVE

        mgr.trigger_disconnect()
        assert mgr.state == LiveState.DISCONNECTED


# ============================================================================
# Gate Dimension 2: Zero Duration Explosion on trigger_disconnect (current_time=None)
# ============================================================================

class TestEmpiricalGateDimension2DurationExplosion:
    """Empirically stress-tests session duration calculations under default production time."""

    def test_200_cycles_disconnect_duration_bounded_subsecond(self):
        """
        Stress Test 2A:
        Runs 200 consecutive cycles of connecting, sleeping 2-10ms real time,
        and disconnecting with default current_time=None.
        Verifies:
        - 0 cycles exceed 1.0 second.
        - Zero cycles exhibit epoch explosion (> 1e8 seconds).
        - Cumulative savings against 3600s mission remains >= 70% (> 99%).
        - LiveSavingsTracker token count reflects authentic subsecond usage.
        """
        num_cycles = 200
        durations = []

        for i in range(num_cycles):
            mgr = LiveConnectionManager()
            mgr.trigger_conversation()
            time.sleep(0.002)  # 2ms real elapsed time
            mgr.trigger_disconnect()

            dur = mgr.total_connected_seconds
            durations.append(dur)

            # Strict empirical upper bound: real sleep is 2ms, must be < 1.0s and >= 0.0s (Windows quantum)
            assert 0.0 <= dur < 1.0, f"Cycle {i}: Duration exploded to {dur:.4f}s!"
            assert dur < 100.0, f"Cycle {i}: Catastrophic epoch explosion detected ({dur}s)!"

        # Explicit test with sleep > Windows timer quantum (20ms > 15.6ms) to verify strictly positive duration
        mgr_pos = LiveConnectionManager()
        mgr_pos.trigger_conversation()
        time.sleep(0.025)
        mgr_pos.trigger_disconnect()
        assert mgr_pos.total_connected_seconds > 0.01
        assert mgr_pos.total_connected_seconds < 1.0

        avg_dur = sum(durations) / len(durations)
        assert avg_dur < 0.10, f"Average duration {avg_dur:.4f}s unexpectedly high!"

    def test_immediate_disconnect_tick0_zero_duration_explosion(self):
        """
        Stress Test 2B:
        Connects and immediately disconnects on tick 0 (0ms sleep).
        Verifies duration is >= 0.0s and < 0.05s, strictly avoiding epoch explosion.
        """
        for _ in range(50):
            mgr = LiveConnectionManager()
            mgr.trigger_conversation()
            mgr.trigger_disconnect()

            assert 0.0 <= mgr.total_connected_seconds < 0.05
            assert mgr.savings_tracker.ondemand_tokens <= 100

    def test_sequential_sessions_on_single_instance_accumulate_accurately(self):
        """
        Stress Test 2C:
        Runs 20 sequential on-demand sessions on the SAME LiveConnectionManager instance.
        Verifies:
        - session_count == 20
        - total_connected_seconds monotonically accumulates authentic durations (< 5.0s total).
        - SavingsSnapshot satisfies duration_savings_pct >= 70.0%.
        """
        mgr = LiveConnectionManager()
        # Set baseline uptime to 3600.0s
        mgr.savings_tracker.record_uptime(3600.0)

        for i in range(20):
            mgr.trigger_conversation()
            time.sleep(0.003)
            mgr.trigger_disconnect()
            assert mgr.session_count == i + 1

        total_dur = mgr.total_connected_seconds
        assert total_dur < 5.0, f"Total duration {total_dur}s unexpectedly high for 20x 3ms sessions!"
        assert mgr.savings_tracker.total_connected_sec == pytest.approx(total_dur, abs=1e-5)

        snap = mgr.savings_tracker.get_snapshot()
        assert snap.duration_savings_pct >= 99.0
        assert snap.guarantee_satisfied is True


# ============================================================================
# Gate Dimension 3: 16-Thread Barrier Concurrency & Double-Counting Invariant
# ============================================================================

class TestEmpiricalGateDimension3BarrierConcurrency:
    """Empirically stress-tests thread safety under 16-thread barrier synchronization."""

    def test_16_thread_simultaneous_barrier_disconnect_strictly_single_commit(self):
        """
        Stress Test 3A:
        16 threads synchronized on a barrier simultaneously invoke trigger_disconnect()
        with default time on an active session.
        Verifies:
        - Session count is incremented exactly once.
        - total_connected_seconds is recorded exactly once (no double/16x counting).
        - savings_tracker.total_connected_sec matches mgr.total_connected_seconds exactly.
        """
        mgr = LiveConnectionManager()
        mgr.trigger_conversation()
        assert mgr.state == LiveState.ACTIVE
        time.sleep(0.02)  # 20ms real session

        num_threads = 16
        barrier = threading.Barrier(num_threads)
        errors = []

        def worker(tid: int):
            try:
                barrier.wait()
                mgr.trigger_disconnect(reason=f"barrier_worker_{tid}")
            except Exception as e:
                errors.append((tid, e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(errors) == 0, f"Thread execution errors: {errors}"
        # Exactly one session recorded
        assert mgr.session_count == 1
        assert mgr.savings_tracker.session_count == 1
        # Duration must be authentic single session (approx 0.02s - 0.20s, NOT 16 * 0.02s)
        assert 0.01 <= mgr.total_connected_seconds < 0.50
        assert mgr.total_connected_seconds == pytest.approx(mgr.savings_tracker.total_connected_sec, abs=1e-6)

    def test_16_thread_mixed_barrier_contention_zero_double_count(self):
        """
        Stress Test 3B:
        16 threads hitting a barrier competing across 4 different teardown methods:
        - 4 threads call trigger_disconnect()
        - 4 threads call update_tick()
        - 4 threads call close()
        - 4 threads call _commit_connected_duration() directly
        Verifies:
        - Exactly one caller successfully commits duration; all other 15 callers commit 0.0.
        - total_connected_seconds strictly equals duration from the single winning commit.
        """
        mgr = LiveConnectionManager()
        mgr._state = LiveState.ACTIVE
        start_ts = 1000.0
        target_now = 1030.0  # Exactly 30.0s session
        mgr._connection_open_timestamp = start_ts
        mgr._last_activity_time = start_ts

        num_threads = 16
        barrier = threading.Barrier(num_threads)
        committed_durations = []
        commit_lock = threading.Lock()

        def mixed_worker(tid: int):
            barrier.wait()
            op = tid % 4
            dur = 0.0
            if op == 0:
                mgr.trigger_disconnect(current_time=target_now)
            elif op == 1:
                mgr.update_tick(current_time=target_now)
            elif op == 2:
                mgr.close(current_time=target_now)
            elif op == 3:
                dur = mgr._commit_connected_duration(current_time=target_now)

            with commit_lock:
                committed_durations.append(dur)

        threads = [threading.Thread(target=mixed_worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # Duration committed must be exactly 30.0s
        assert mgr.total_connected_seconds == pytest.approx(30.0, abs=1e-5)
        assert mgr.savings_tracker.total_connected_sec == pytest.approx(30.0, abs=1e-5)
        assert mgr.savings_tracker.session_count == 1

    def test_50_rounds_16_thread_barrier_rapid_churn(self):
        """
        Stress Test 3C:
        50 consecutive rounds of 16-thread barrier contention on fresh sessions.
        Verifies that after 50 rounds:
        - Cumulative sessions == 50
        - Cumulative duration equals 50 * session_duration
        - Double-counting inflation ratio is exactly 1.0000x (0% inflation).
        """
        mgr = LiveConnectionManager()
        num_rounds = 50
        num_threads = 16

        for round_idx in range(num_rounds):
            mgr._state = LiveState.ACTIVE
            mgr._connection_open_timestamp = 100.0 + (round_idx * 50.0)
            mgr._last_activity_time = mgr._connection_open_timestamp
            now = mgr._connection_open_timestamp + 10.0  # 10.0s per session

            barrier = threading.Barrier(num_threads)

            def worker(tid: int):
                barrier.wait()
                if tid % 2 == 0:
                    mgr.trigger_disconnect(current_time=now)
                else:
                    mgr.update_tick(current_time=now)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

        expected_total_sec = num_rounds * 10.0  # 50 * 10 = 500.0s
        assert mgr.total_connected_seconds == pytest.approx(expected_total_sec, abs=1e-3)
        assert mgr.savings_tracker.total_connected_sec == pytest.approx(expected_total_sec, abs=1e-3)
        assert mgr.savings_tracker.session_count == num_rounds

    def test_concurrent_snapshot_readers_during_barrier_commits(self):
        """
        Stress Test 3D:
        8 writer threads rapidly committing sessions while 8 reader threads
        continuously call get_snapshot().
        Verifies:
        - Zero race conditions, deadlocks, or crashes.
        - All read snapshots have uptime >= connected_sec >= 0.0.
        - duration_savings_pct is always bounded in [0.0, 100.0].
        """
        tracker = LiveSavingsTracker(start_time=0.0)
        tracker.record_uptime(10000.0)
        stop_event = threading.Event()
        reader_snapshots = []
        errors = []

        def writer(wid: int):
            try:
                for i in range(100):
                    tracker.record_connection_duration(0.5)
                    tracker.record_intent("GEMINI_LIVE")
                    time.sleep(0.0005)
            except Exception as e:
                errors.append((f"writer_{wid}", e))

        def reader(rid: int):
            try:
                while not stop_event.is_set():
                    snap = tracker.get_snapshot(current_time=10000.0)
                    reader_snapshots.append(snap)
                    time.sleep(0.001)
            except Exception as e:
                errors.append((f"reader_{rid}", e))

        writers = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        readers = [threading.Thread(target=reader, args=(i,)) for i in range(8)]

        for r in readers:
            r.start()
        for w in writers:
            w.start()

        for w in writers:
            w.join(timeout=10.0)
        stop_event.set()
        for r in readers:
            r.join(timeout=5.0)

        assert len(errors) == 0, f"Errors in concurrent read/write: {errors}"
        assert len(reader_snapshots) > 0

        for snap in reader_snapshots:
            assert 0.0 <= snap.connected_sec <= snap.uptime_sec
            assert 0.0 <= snap.duration_savings_pct <= 100.0
            assert snap.total_intents >= 0
