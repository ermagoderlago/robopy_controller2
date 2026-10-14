#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_challenger_m4_lifecycle_adversarial.py
==================================================
Adversarial Stress and Empirical Challenge Test Suite for Milestone 4:
Gemini Live On-Demand 5-State FSM, Inactivity Auto-Disconnect, and Savings Guarantee.

Challenger Vectors:
1. Multi-threaded concurrency and stress (rapid triggers, disconnects, activity events).
2. 25.0s boundary precision (e.g. 24.999s vs 25.001s, activity right before timeout).
3. Inactivity immunity to noise, empty transcripts, and <IGNORE_TURN>.
4. LiveSavingsTracker mathematical bounds under stress workloads (confirm duration and token savings >= 70%).

Author: challenger_m4_1 (Empirical Challenger)
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
    WebSocketState,
    LiveSavingsTracker,
    SavingsSnapshot,
)


# ============================================================================
# Vector 1: Multi-threaded Concurrency and Stress
# ============================================================================

class TestAdversarialConcurrencyAndStress:
    """Stress-tests the FSM against concurrent thread contention, race conditions, and rapid toggling."""

    def test_concurrent_trigger_and_disconnect_no_deadlock_or_crash(self):
        """16 threads hammering trigger_conversation, trigger_disconnect, on_user_activity, update_tick."""
        mgr = LiveConnectionManager()
        iterations_per_thread = 200
        num_threads = 16
        errors = []

        def worker_loop(thread_id: int):
            try:
                base_time = 1000.0 + (thread_id * 50.0)
                for i in range(iterations_per_thread):
                    t = base_time + (i * 0.1)
                    op = (thread_id + i) % 4
                    if op == 0:
                        mgr.trigger_conversation(reason=f"thread_{thread_id}", current_time=t)
                    elif op == 1:
                        mgr.on_user_activity(current_time=t)
                    elif op == 2:
                        mgr.update_tick(current_time=t)
                    elif op == 3:
                        mgr.trigger_disconnect(reason=f"disconnect_{thread_id}", current_time=t)
            except Exception as e:
                errors.append((thread_id, e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker_loop, tid) for tid in range(num_threads)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Encountered concurrency exceptions: {errors}"
        # Final state must be a valid LiveState enum
        assert isinstance(mgr.state, LiveState)
        # Savings tracker must reflect non-negative values
        snap = mgr.savings_tracker.get_snapshot(current_time=3000.0)
        assert snap.connected_sec >= 0.0
        assert snap.total_intents > 0

    def test_rapid_burst_trigger_disconnect_state_consistency(self):
        """100 rapid alternating triggers and disconnects on the same instance."""
        mgr = LiveConnectionManager()
        # Calibrate start time for simulated timestamp execution
        curr_time = 500.0
        mgr.savings_tracker._start_time = curr_time

        for i in range(100):
            # Connect
            st_conn = mgr.trigger_conversation(current_time=curr_time)
            assert st_conn == LiveState.ACTIVE
            assert mgr.state == LiveState.ACTIVE

            # Activity
            curr_time += 2.0
            mgr.on_user_activity(current_time=curr_time)

            # Disconnect
            curr_time += 3.0
            st_disc = mgr.trigger_disconnect(current_time=curr_time)
            assert st_disc == LiveState.DISCONNECTED
            assert mgr.state == LiveState.DISCONNECTED

            curr_time += 10.0

        assert mgr.session_count == 100
        # 100 sessions of 5s each = 500s total connected
        assert mgr.total_connected_seconds == pytest.approx(500.0, abs=1e-3)
        assert mgr.savings_tracker.total_connected_sec == pytest.approx(500.0, abs=1e-3)
        # Total mission elapsed = 100 * 15.0 = 1500.0s
        snap = mgr.savings_tracker.get_snapshot(current_time=curr_time)
        assert snap.connected_sec == pytest.approx(500.0, abs=1e-3)
        assert snap.duration_savings_pct == pytest.approx((1.0 - 500.0 / 1500.0) * 100.0, abs=0.5)

    def test_empirical_clock_mixing_vulnerability_monotonic_vs_time(self):
        """
        CRITICAL VULNERABILITY REPRODUCTION:
        In live_connection_manager.py:
        - Line 527 (_do_connect_async): self._connection_open_timestamp = time.monotonic()
        - Line 383 (trigger_disconnect): now = current_time if current_time is not None else time.time()
        Calling trigger_disconnect() without arguments computes:
            dur = time.time() - time.monotonic() ~ 1.79 billion seconds (~56.7 years)!
        This corrupts total_connected_seconds, ondemand_tokens (44+ billion), and duration_savings_pct (0.0%).
        """
        loop = asyncio.new_event_loop()
        try:
            # We simulate a successful connect which sets _connection_open_timestamp = time.monotonic()
            mgr = LiveConnectionManager(loop=loop)
            mgr._state = LiveState.ACTIVE
            mgr._connection_open_timestamp = time.monotonic()

            # Now user triggers disconnect via trigger_disconnect() with default current_time=None
            time.sleep(0.01)
            mgr.trigger_disconnect("user_stop")

            # In the current implementation with clock mixing, dur explodes to > 1e8 seconds
            clock_delta = time.time() - time.monotonic()
            assert clock_delta > 1e8  # Epoch (1.79e9) vs boot time (1e6)
            is_corrupted = mgr.total_connected_seconds > 1e8
            assert not is_corrupted
            assert mgr.total_connected_seconds < 1.0
            if is_corrupted:
                snap = mgr.savings_tracker.get_snapshot()
                assert snap.duration_savings_pct == 0.0
                assert snap.token_savings_pct == 0.0
                assert mgr.savings_tracker.ondemand_tokens > 1e9
        finally:
            loop.close()

    def test_empirical_immediate_premature_timeout_from_clock_mixing(self):
        """
        CRITICAL VULNERABILITY REPRODUCTION:
        In live_connection_manager.py:
        - Line 416 (_reset_inactivity_timer): self._last_activity_time = time.monotonic()
        - Line 360 (update_tick): now = current_time if current_time is not None else time.monotonic()
        Calling update_tick() with default current_time=None uses monotonic clock.
        Ensures state remains ACTIVE and does not immediately force IDLE_TIMED_OUT.
        """
        mgr = LiveConnectionManager()
        mgr._state = LiveState.ACTIVE
        mgr._reset_inactivity_timer()

        # Inactivity timer was just reset; session should be ACTIVE for 25.0s
        st = mgr.update_tick()
        clock_delta = time.time() - time.monotonic()
        if clock_delta > 1e8:
            # With monotonic clock standardized, premature transition to IDLE_TIMED_OUT is prevented!
            assert st == LiveState.ACTIVE
            assert mgr.state == LiveState.ACTIVE
            assert mgr.total_connected_seconds < 1.0


    def test_concurrent_audio_enqueue_and_discard_when_not_active(self):
        """Verifies zero buffer bloat and instant discard of audio chunks when NOT in ACTIVE state."""
        loop = asyncio.new_event_loop()
        mgr = LiveConnectionManager(loop=loop)
        assert mgr.state == LiveState.DISCONNECTED

        # In DISCONNECTED: sending audio chunks should do nothing
        for _ in range(100):
            mgr.send_audio_chunk(b"\x00\x01" * 800)

        # Run loop briefly to process scheduled callbacks
        loop.call_soon(loop.stop)
        loop.run_forever()

        # Audio queue must remain empty because state != ACTIVE
        assert mgr._audio_in_queue is None or mgr._audio_in_queue.empty()
        loop.close()

    def test_concurrent_async_lifecycle_with_real_event_loop(self):
        """Tests threadsafe scheduling and teardown with a live background asyncio loop."""
        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        try:
            mgr = LiveConnectionManager(loop=loop)
            assert mgr.state == LiveState.DISCONNECTED

            errors = []
            def async_worker(tid: int):
                try:
                    for _ in range(25):
                        mgr.trigger_conversation(reason=f"worker_{tid}")
                        time.sleep(0.005)
                        mgr.on_user_activity()
                        time.sleep(0.005)
                        mgr.trigger_disconnect(reason=f"worker_{tid}")
                        time.sleep(0.005)
                except Exception as e:
                    errors.append((tid, e))

            threads = [threading.Thread(target=async_worker, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10.0)

            assert len(errors) == 0, f"Async worker errors: {errors}"
            assert isinstance(mgr.state, LiveState)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2.0)
            loop.close()


# ============================================================================
# Vector 2: 25.0s Boundary Precision
# ============================================================================

class TestBoundaryPrecision25s:
    """Stress-tests the exact 25.0s boundary precision and sub-second activity gating."""

    def test_exact_millisecond_boundary_24999ms_vs_25000ms_vs_25001ms(self):
        """Precision gate: 24.999s remains ACTIVE, 25.000s transitions to IDLE_TIMED_OUT."""
        mgr = LiveConnectionManager()
        start = 1000.0
        mgr.trigger_conversation(current_time=start)
        assert mgr.state == LiveState.ACTIVE

        # 24.999s of silence: must remain strictly ACTIVE
        st_pre = mgr.update_tick(current_time=start + 24.999)
        assert st_pre == LiveState.ACTIVE
        assert mgr.state == LiveState.ACTIVE

        # 25.000s of silence: exactly on the boundary -> IDLE_TIMED_OUT
        st_bound = mgr.update_tick(current_time=start + 25.000)
        assert st_bound == LiveState.IDLE_TIMED_OUT
        assert mgr.state == LiveState.IDLE_TIMED_OUT

    def test_boundary_precision_at_25001ms(self):
        """Precision gate: 25.001s transitions to IDLE_TIMED_OUT."""
        mgr = LiveConnectionManager()
        start = 1000.0
        mgr.trigger_conversation(current_time=start)
        assert mgr.state == LiveState.ACTIVE

        st_post = mgr.update_tick(current_time=start + 25.001)
        assert st_post == LiveState.IDLE_TIMED_OUT
        assert mgr.state == LiveState.IDLE_TIMED_OUT

    def test_user_activity_at_24999ms_prevents_timeout_at_25001ms(self):
        """User speech detected 1 millisecond before timeout resets the 25.0s window."""
        mgr = LiveConnectionManager()
        start = 2000.0
        mgr.trigger_conversation(current_time=start)
        assert mgr.state == LiveState.ACTIVE

        # Advance to 24.999s and register user activity
        mgr.on_user_activity(current_time=start + 24.999)

        # Advance to 25.001s (which is 25.001s since start, but only 0.002s since last speech)
        st_25001 = mgr.update_tick(current_time=start + 25.001)
        assert st_25001 == LiveState.ACTIVE, "Timed out prematurely despite speech at 24.999s!"

        # Advance to start + 49.998s (24.999s since last speech): still ACTIVE
        st_49998 = mgr.update_tick(current_time=start + 49.998)
        assert st_49998 == LiveState.ACTIVE

        # Advance to start + 50.000s (25.001s since last speech): now transitions to IDLE_TIMED_OUT
        st_50000 = mgr.update_tick(current_time=start + 50.000)
        assert st_50000 == LiveState.IDLE_TIMED_OUT

    def test_sub_second_burst_activity_boundary_series(self):
        """A series of activities right before timeout preserves ACTIVE state across minutes."""
        mgr = LiveConnectionManager()
        t = 100.0
        mgr.trigger_conversation(current_time=t)

        # 4 bursts at t=24.5, 49.0, 73.5, 98.0
        for offset in [24.5, 24.5, 24.5, 24.5]:
            t += offset
            mgr.update_tick(current_time=t - 0.1)
            assert mgr.state == LiveState.ACTIVE
            mgr.on_user_activity(current_time=t)

        # Total time elapsed is 98.0s, session should still be ACTIVE
        assert mgr.state == LiveState.ACTIVE
        # Advance 24.9s past last activity (t = 122.9s)
        mgr.update_tick(current_time=t + 24.9)
        assert mgr.state == LiveState.ACTIVE
        # Advance 25.1s past last activity (t = 123.1s) -> IDLE_TIMED_OUT
        mgr.update_tick(current_time=t + 25.1)
        assert mgr.state == LiveState.IDLE_TIMED_OUT

    def test_clock_skew_and_zero_delta_tick_does_not_timeout(self):
        """Zero or backward time delta does not trigger false timeout."""
        mgr = LiveConnectionManager()
        start = 500.0
        mgr.trigger_conversation(current_time=start)

        # Same timestamp
        assert mgr.update_tick(current_time=start) == LiveState.ACTIVE
        # Clock skew / NTP step backwards
        assert mgr.update_tick(current_time=start - 10.0) == LiveState.ACTIVE
        assert mgr.state == LiveState.ACTIVE


# ============================================================================
# Vector 3: Inactivity Immunity to Noise, Empty Transcripts, and <IGNORE_TURN>
# ============================================================================

class TestNoiseAndIgnoreTurnImmunity:
    """Verifies that non-linguistic noise and <IGNORE_TURN> do not reset the inactivity timer."""

    @pytest.mark.parametrize("noise_text", [
        "",
        "   ",
        "\t\n\r",
        "<noise>",
        "<laughter>",
        "<cough>",
        "<sigh>",
        "<throat-clearing>",
        "[groan]",
        "[cough]",
        "(laughter)",
        "(applause)",
        "...",
        "---",
        "?!",
        ".",
        "  ...  ",
        "a",
        "m",
        "h",
        "<noise> ... <laughter>",
        "<cough> [sigh] <noise>",
    ])
    def test_noise_corpus_classified_as_noise(self, noise_text):
        """Verifies that all non-linguistic noise variations are classified as noise."""
        assert LiveConnectionManager._is_noise_transcription(noise_text) is True

    @pytest.mark.parametrize("speech_text", [
        "Marcus vieni qui",
        "Dove si trova il salotto?",
        "Stop",
        "Ruota a sinistra",
        "Stato batteria",
        "Che ore sono?",
        "Vai in cucina",
        "Ciao",
        "Si",
        "No",
    ])
    def test_valid_speech_classified_as_speech(self, speech_text):
        """Verifies that genuine Italian words and phrases are classified as speech."""
        assert LiveConnectionManager._is_noise_transcription(speech_text) is False

    @pytest.mark.asyncio
    async def test_streaming_noise_transcription_does_not_reset_inactivity_timer(self):
        """Simulate ASR streaming of noise: timer handle is NOT reset."""
        mgr = LiveConnectionManager()
        mgr._state = LiveState.ACTIVE
        timer_reset_called = False

        # Monkey patch _reset_inactivity_timer to spy on calls
        original_reset = mgr._reset_inactivity_timer
        def spy_reset():
            nonlocal timer_reset_called
            timer_reset_called = True
            original_reset()
        mgr._reset_inactivity_timer = spy_reset

        # Build mock msg with noise transcription
        mock_msg = MagicMock()
        mock_msg.session_resumption_update = None
        mock_msg.tool_call = None
        mock_sc = MagicMock()
        mock_sc.interrupted = False
        mock_sc.input_transcription.text = "<cough>"
        mock_sc.output_transcription = None
        mock_sc.model_turn = None
        mock_sc.turn_complete = False
        mock_msg.server_content = mock_sc

        await mgr._handle_live_message(mock_msg)
        assert timer_reset_called is False, "Noise transcription '<cough>' triggered timer reset!"

    @pytest.mark.asyncio
    async def test_ignore_turn_suppresses_audio_and_prevents_timer_reset(self):
        """
        When model outputs <IGNORE_TURN>:
        1. No audio received callback.
        2. No timer reset on turn_complete.
        3. FSM remains ACTIVE (no crash, no premature disconnect).
        """
        audio_received = []
        turn_completed = []

        mgr = LiveConnectionManager(
            on_audio_received=lambda chunk: audio_received.append(chunk),
            on_turn_complete=lambda u, m: turn_completed.append((u, m))
        )
        mgr._state = LiveState.ACTIVE
        timer_reset_called = False

        original_reset = mgr._reset_inactivity_timer
        def spy_reset():
            nonlocal timer_reset_called
            timer_reset_called = True
            original_reset()
        mgr._reset_inactivity_timer = spy_reset

        # 1. Message with model_turn containing <IGNORE_TURN>
        mock_msg1 = MagicMock()
        mock_msg1.session_resumption_update = None
        mock_msg1.tool_call = None
        mock_sc1 = MagicMock()
        mock_sc1.interrupted = False
        mock_sc1.input_transcription = None
        mock_sc1.output_transcription = None

        part_ignore = MagicMock()
        part_ignore.text = "<IGNORE_TURN>"
        part_ignore.inline_data = None
        part_ignore.function_call = None

        mock_sc1.model_turn.parts = [part_ignore]
        mock_sc1.turn_complete = False
        mock_msg1.server_content = mock_sc1

        await mgr._handle_live_message(mock_msg1)

        assert len(audio_received) == 0
        assert mgr._turn_audio_emitted is False
        assert mgr._current_user_text == ""
        assert timer_reset_called is False

        # 2. Message indicating turn_complete
        mock_msg2 = MagicMock()
        mock_msg2.session_resumption_update = None
        mock_msg2.tool_call = None
        mock_sc2 = MagicMock()
        mock_sc2.interrupted = False
        mock_sc2.input_transcription = None
        mock_sc2.output_transcription = None
        mock_sc2.model_turn = None
        mock_sc2.turn_complete = True
        mock_msg2.server_content = mock_sc2

        await mgr._handle_live_message(mock_msg2)

        # turn_complete with no valid speech and no audio must NOT reset timer
        assert timer_reset_called is False
        assert mgr.state == LiveState.ACTIVE

    def test_continuous_noise_and_ignore_turns_still_times_out_at_25s(self):
        """A barrage of noise and ignore turns during 25 seconds does NOT prevent timeout at 25.0s."""
        mgr = LiveConnectionManager()
        start = 3000.0
        mgr.trigger_conversation(current_time=start)
        assert mgr.state == LiveState.ACTIVE

        # Noise at t = 3005.0, 3010.0, 3015.0, 3020.0
        # If any noise accidentally reset the timer, the timeout would be delayed past 3025.0
        for noise_time in [3005.0, 3010.0, 3015.0, 3020.0]:
            # Simulate noise transcription arrived
            if not LiveConnectionManager._is_noise_transcription("<laughter>"):
                mgr.on_user_activity(current_time=noise_time)
            mgr.update_tick(current_time=noise_time)
            assert mgr.state == LiveState.ACTIVE

        # At t = 3024.9s: still ACTIVE
        assert mgr.update_tick(current_time=start + 24.9) == LiveState.ACTIVE
        # At t = 3025.0s: deterministically IDLE_TIMED_OUT
        assert mgr.update_tick(current_time=start + 25.0) == LiveState.IDLE_TIMED_OUT
        assert mgr.state == LiveState.IDLE_TIMED_OUT


# ============================================================================
# Vector 4: LiveSavingsTracker Mathematical Bounds Under Stress Workloads
# ============================================================================

class TestLiveSavingsTrackerStressAndBounds:
    """Stress-tests mathematical bounds, duty cycles, and edge cases of LiveSavingsTracker."""

    def test_savings_bounds_under_various_duty_cycles(self):
        """
        Tests duty cycles from 0% to 50% in 5% increments over a 3600s mission.
        Confirms duration_savings_pct >= 70.0% if and only if duty cycle <= 30.0%.
        """
        mission_duration = 3600.0

        for duty_pct in range(0, 55, 5):
            tracker = LiveSavingsTracker(start_time=0.0)
            tracker.record_uptime(mission_duration)
            connected_duration = mission_duration * (duty_pct / 100.0)
            tracker.record_connection_duration(connected_duration)

            snapshot = tracker.get_snapshot(current_time=mission_duration)
            expected_savings = 100.0 - duty_pct
            assert snapshot.duration_savings_pct == pytest.approx(expected_savings, abs=1e-2)

            if duty_pct <= 30:
                assert snapshot.duration_savings_pct >= 70.0
                assert snapshot.guarantee_satisfied is True, f"Failed at duty cycle {duty_pct}%"
            else:
                assert snapshot.duration_savings_pct < 70.0
                assert snapshot.guarantee_satisfied is False, f"Unexpected pass at duty cycle {duty_pct}%"

    def test_token_savings_strictly_ge_70_under_standard_workloads(self):
        """Verifies token savings >= 70% across 1h, 2h, and 4h missions with typical robotics workload."""
        mission_configs = [
            (3600.0, 15, 3, 2, 40.0),    # 1h: 15 qwen, 3 stops, 2 live dialogues (40s each)
            (7200.0, 30, 6, 4, 45.0),    # 2h: 30 qwen, 6 stops, 4 live dialogues (45s each)
            (14400.0, 60, 12, 8, 40.0),  # 4h: 60 qwen, 12 stops, 8 live dialogues (40s each)
        ]

        for total_sec, num_qwen, num_stops, num_live, talk_dur in mission_configs:
            tracker = LiveSavingsTracker(start_time=0.0)
            tracker.record_uptime(total_sec)

            for _ in range(num_qwen):
                tracker.record_intent("LOCAL_QWEN")
            for _ in range(num_stops):
                tracker.record_intent("FAST_PATH_STOP")
            for _ in range(num_live):
                tracker.record_intent("GEMINI_LIVE")
                tracker.record_connection_duration(talk_dur)

            snapshot = tracker.get_snapshot(current_time=total_sec)

            assert snapshot.duration_savings_pct >= 70.0
            assert snapshot.token_savings_pct >= 70.0
            assert snapshot.guarantee_satisfied is True
            # In typical workloads, token savings is > 90%
            assert snapshot.token_savings_pct >= 90.0
            assert snapshot.call_savings_pct >= 75.0

    def test_savings_tracker_extreme_edge_cases(self):
        """Tests zero uptime, negative durations, future start times, and empty intents."""
        # Case A: Zero uptime
        t1 = LiveSavingsTracker(start_time=100.0)
        snap1 = t1.get_snapshot(current_time=100.0)
        assert snap1.uptime_sec == 0.0
        assert snap1.duration_savings_pct == 100.0

        # Case B: Negative connection duration recording (should clamp to 0.0)
        t2 = LiveSavingsTracker(start_time=0.0)
        t2.record_uptime(100.0)
        t2.record_connection_duration(-50.0)
        snap2 = t2.get_snapshot(current_time=100.0)
        assert snap2.connected_sec == 0.0
        assert snap2.duration_savings_pct == 100.0

        # Case C: Non-monotonic record_uptime calls
        t3 = LiveSavingsTracker(start_time=0.0)
        t3.record_uptime(500.0)
        t3.record_uptime(200.0)  # Should remain 500.0
        assert t3.total_uptime_sec == 500.0

        # Case D: Mixed destination casing and unknown intents
        t4 = LiveSavingsTracker(start_time=0.0)
        t4.record_intent("gemini_live")
        t4.record_intent("Fast_Path_Stop")
        t4.record_intent("QWEN2_LOCAL")
        t4.record_intent("random_custom_tool")
        assert t4.cloud_live_intents == 1
        assert t4.fast_path_stops == 1
        assert t4.local_intents == 2
        assert t4.total_intents == 4
        snap4 = t4.get_snapshot(current_time=10.0)
        assert snap4.call_savings_pct == 75.0

    def test_high_volume_concurrency_on_savings_tracker(self):
        """16 threads performing 10,000 updates on a single LiveSavingsTracker."""
        tracker = LiveSavingsTracker(start_time=0.0)
        num_threads = 16
        ops_per_thread = 500

        def tracker_worker(tid: int):
            for i in range(ops_per_thread):
                tracker.record_intent("LOCAL_QWEN" if i % 2 == 0 else "GEMINI_LIVE")
                tracker.record_connection_duration(0.1)
                tracker.record_uptime(float(tid * 100 + i))

        threads = [threading.Thread(target=tracker_worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert tracker.total_intents == num_threads * ops_per_thread
        assert tracker.total_connected_sec == pytest.approx(num_threads * ops_per_thread * 0.1, abs=1e-3)
        snap = tracker.get_snapshot(current_time=10000.0)
        assert isinstance(snap, SavingsSnapshot)
        assert snap.total_intents == 8000
