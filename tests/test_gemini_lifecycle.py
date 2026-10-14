#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_gemini_lifecycle.py
==============================
Comprehensive Verification Test Suite for Milestone 4:
Gemini Live On-Demand 5-State FSM, Inactivity Auto-Disconnect,
LiveSavingsTracker Guarantee (>= 70%), Resilient Audio Pipeline VUI,
140Hz HPF Fan Noise Suppression, 48kHz Native Resampling & State Continuity,
3-Chunk Jitter Buffer, and Piper TTS Local Bypass.

Interface Contracts & Verification:
- SPEC-04: Audio VUI Pipeline (FM-VUI-001, FM-VUI-003, FM-VUI-040, FM-VUI-043)
- PROJECT.md: Features F12, F13, F14
- marcus_core_rules.md: DAC 48kHz Resampling, 16kHz Mono Stream
"""

import math
import time
import threading
import asyncio
from typing import List, Optional
import numpy as np
import pytest

try:
    import audioop
except ImportError:
    try:
        import audioop_lts as audioop
    except ImportError:
        audioop = None

import sys
from unittest.mock import MagicMock

# Mock ROS 2 and hardware modules for headless execution
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
from robopy_controller.robot_ai.audio.resilient_audio_pipeline import ResilientAudioPipeline
from robopy_controller.robot_ai.services.piper_tts_service import PiperTTSClient
from robopy_controller.nodes.respeaker_vui_node import ReSpeakerVUINode
from robopy_controller.robot_ai.vui.vui_dialogue_engine import VUIDialogueEngine


# ============================================================================
# Vector 1: 5-State On-Demand FSM Lifecycle Transitions
# ============================================================================

class TestGeminiLiveFSMTransitions:
    """Validates the 5 states: DISCONNECTED -> CONNECTING -> ACTIVE -> IDLE_TIMED_OUT -> DISCONNECTING -> DISCONNECTED."""

    def test_initial_state_is_disconnected(self):
        mgr = LiveConnectionManager()
        assert mgr.state == LiveState.DISCONNECTED
        assert mgr.current_state == LiveState.DISCONNECTED
        assert mgr.session_count == 0

    def test_transition_to_connecting_and_active_on_demand(self):
        state_transitions = []
        mgr = LiveConnectionManager()
        mgr.set_state_changed_callback(lambda old_s, new_s: state_transitions.append((old_s, new_s)))

        # Trigger conversation on-demand (simulating HybridRouter RouteTarget.GEMINI_LIVE)
        st = mgr.trigger_conversation(reason="intent_gemini_live", current_time=100.0)
        assert st == LiveState.ACTIVE
        assert mgr.state == LiveState.ACTIVE
        assert mgr.session_count == 1
        assert len(state_transitions) >= 1
        assert (LiveState.DISCONNECTED, LiveState.CONNECTING) in state_transitions
        assert (LiveState.CONNECTING, LiveState.ACTIVE) in state_transitions

    def test_25s_inactivity_boundary_transitions_to_idle_timed_out(self):
        mgr = LiveConnectionManager()
        mgr.trigger_conversation(current_time=100.0)
        assert mgr.state == LiveState.ACTIVE

        # At 24.9s of silence: still ACTIVE
        s1 = mgr.update_tick(current_time=124.9)
        assert s1 == LiveState.ACTIVE

        # At 25.0s of silence: deterministically transitions to IDLE_TIMED_OUT
        s2 = mgr.update_tick(current_time=125.0)
        assert s2 == LiveState.IDLE_TIMED_OUT
        assert mgr.state == LiveState.IDLE_TIMED_OUT

    def test_explicit_disconnect_transitions_to_disconnected(self):
        state_transitions = []
        mgr = LiveConnectionManager()
        mgr.set_state_changed_callback(lambda old_s, new_s: state_transitions.append((old_s, new_s)))

        mgr.trigger_conversation(current_time=50.0)
        assert mgr.state == LiveState.ACTIVE

        # Explicit stop / fast-path
        st = mgr.trigger_disconnect(reason="fast_path_stop", current_time=55.0)
        assert st == LiveState.DISCONNECTED
        assert mgr.state == LiveState.DISCONNECTED
        assert (LiveState.ACTIVE, LiveState.DISCONNECTING) in state_transitions
        assert (LiveState.DISCONNECTING, LiveState.DISCONNECTED) in state_transitions

    def test_reconnection_from_idle_timed_out(self):
        mgr = LiveConnectionManager()
        mgr.trigger_conversation(current_time=100.0)
        mgr.update_tick(current_time=125.0)
        assert mgr.state == LiveState.IDLE_TIMED_OUT

        # User speaks again after timeout -> fresh session
        mgr.trigger_conversation(current_time=200.0)
        assert mgr.state == LiveState.ACTIVE
        assert mgr.session_count == 2


# ============================================================================
# Vector 2: Inactivity Timer Resets vs Gating Immunity
# ============================================================================

class TestInactivityTimerGating:
    """
    Validates that user speech and Marcus vocal emissions reset the 25.0s timer,
    while background noise, TV chatter, and <IGNORE_TURN> do NOT reset it (FM-VUI-043 / FIX-15).
    """

    def test_user_activity_resets_25s_timer(self):
        mgr = LiveConnectionManager()
        mgr.trigger_conversation(current_time=100.0)
        assert mgr.state == LiveState.ACTIVE

        # Advance 15s (t = 115.0)
        mgr.update_tick(current_time=115.0)
        assert mgr.state == LiveState.ACTIVE

        # User speaks at t = 115.0
        mgr.on_user_activity(current_time=115.0)

        # Advance another 15s to t = 130.0 (30s after session start, but only 15s after user speech)
        st = mgr.update_tick(current_time=130.0)
        assert st == LiveState.ACTIVE, "Session timed out prematurely despite user activity reset!"

        # Advance to t = 140.0 (25.0s after user speech at 115.0)
        st_timeout = mgr.update_tick(current_time=140.0)
        assert st_timeout == LiveState.IDLE_TIMED_OUT

    def test_noise_transcription_helper(self):
        """Validates _is_noise_transcription on TV, cough, laughter, and short sounds."""
        assert LiveConnectionManager._is_noise_transcription("") is True
        assert LiveConnectionManager._is_noise_transcription("   ") is True
        assert LiveConnectionManager._is_noise_transcription("<laughter>") is True
        assert LiveConnectionManager._is_noise_transcription("<cough>") is True
        assert LiveConnectionManager._is_noise_transcription("[sigh]") is True
        assert LiveConnectionManager._is_noise_transcription("...") is True
        assert LiveConnectionManager._is_noise_transcription("Dove si trova la cucina?") is False
        assert LiveConnectionManager._is_noise_transcription("Stato della batteria") is False

    @pytest.mark.asyncio
    async def test_async_exponential_backoff_on_connect_failure(self):
        """Verifies exponential backoff with max 3 retries and failover callback."""
        fallback_calls = []

        class MockFailingClient:
            class MockAio:
                class MockLive:
                    def connect(self, *args, **kwargs):
                        class FailingCtx:
                            async def __aenter__(self):
                                raise ConnectionError("WebSocket 503 Service Unavailable")
                            async def __aexit__(self, *args):
                                pass
                        return FailingCtx()
                live = MockLive()
            aio = MockAio()

        loop = asyncio.get_running_loop()
        mgr = LiveConnectionManager(
            client=MockFailingClient(),
            loop=loop,
            on_fallback_needed=lambda reason: fallback_calls.append(reason),
        )
        mgr._transition_to(LiveState.CONNECTING)
        await mgr._do_connect_async(reason="test_backoff")

        assert mgr.state == LiveState.DISCONNECTED
        assert len(fallback_calls) == 1
        assert "3 retries" in fallback_calls[0]


# ============================================================================
# Vector 3: Mathematical Proof of Savings Guarantee (>= 70%)
# ============================================================================

class TestLiveSavingsTrackerMathematicalProof:
    """
    Validates duration, token, and call savings across simulated missions,
    strictly proving duration_savings_pct >= 70.0%.
    """

    def test_1_hour_mission_with_operational_and_dialogue_bursts(self):
        tracker = LiveSavingsTracker(start_time=0.0)
        # Total mission: 3600 seconds (1 hour)
        tracker.record_uptime(3600.0)

        # 15 operational commands (routed locally to Qwen2 / Fast-path)
        for _ in range(12):
            tracker.record_intent("LOCAL_QWEN")
        for _ in range(3):
            tracker.record_intent("FAST_PATH_STOP")

        # 2 conversational dialogues:
        # Dialogue 1: 15s active talk + 25s timeout tail = 40s
        tracker.record_intent("GEMINI_LIVE")
        tracker.record_connection_duration(40.0)

        # Dialogue 2: 20s active talk + 25s timeout tail = 45s
        tracker.record_intent("GEMINI_LIVE")
        tracker.record_connection_duration(45.0)

        snapshot = tracker.get_snapshot(current_time=3600.0)

        # Total connected seconds = 85.0s out of 3600.0s
        assert snapshot.connected_sec == 85.0
        # Expected duration savings: (1.0 - 85/3600) * 100 = 97.64%
        assert snapshot.duration_savings_pct >= 70.0
        assert snapshot.duration_savings_pct == pytest.approx(97.64, abs=0.1)
        assert snapshot.guarantee_satisfied is True

        # Call savings: 15 local out of 17 total = 88.24%
        assert snapshot.call_savings_pct == pytest.approx(88.24, abs=0.1)

        # Token savings should exceed 95%
        assert snapshot.token_savings_pct >= 90.0

    def test_stress_boundary_condition_maximum_dialogue(self):
        """Even with 10 full conversations per hour, duration savings remain >= 70%."""
        tracker = LiveSavingsTracker(start_time=0.0)
        tracker.record_uptime(3600.0)

        # 10 conversations of 40s each = 400s total connected
        for _ in range(10):
            tracker.record_intent("GEMINI_LIVE")
            tracker.record_connection_duration(40.0)

        snapshot = tracker.get_snapshot(current_time=3600.0)
        # (1.0 - 400/3600) * 100 = 88.89% >= 70.0%
        assert snapshot.duration_savings_pct >= 70.0
        assert snapshot.guarantee_satisfied is True

    def test_pure_operational_mission_yields_100pct_duration_savings(self):
        tracker = LiveSavingsTracker(start_time=0.0)
        tracker.record_uptime(1800.0)
        for _ in range(20):
            tracker.record_intent("LOCAL_QWEN")

        snapshot = tracker.get_snapshot(current_time=1800.0)
        assert snapshot.connected_sec == 0.0
        assert snapshot.duration_savings_pct == 100.0
        assert snapshot.guarantee_satisfied is True
        assert snapshot.call_savings_pct == 100.0


# ============================================================================
# Vector 4: Resilient Audio Pipeline & 140Hz HPF Fan Noise Suppression
# ============================================================================

class TestResilientAudioPipelineDSP:
    """Validates 140Hz 2nd-order Butterworth HPF fan noise suppression (SPEC-04 / FM-VUI-003)."""

    def test_140hz_hpf_attenuates_60hz_pi5_active_cooler_noise(self):
        pipeline = ResilientAudioPipeline(hpf_cutoff_hz=140.0, sample_rate=16000)
        t = np.linspace(0, 0.1, 1600, endpoint=False)
        fan_noise_60hz = (np.sin(2 * np.pi * 60.0 * t) * 10000).astype(np.int16)

        filtered = pipeline.apply_140hz_hpf(fan_noise_60hz)

        rms_in = np.sqrt(np.mean(fan_noise_60hz.astype(float) ** 2))
        rms_out = np.sqrt(np.mean(filtered.astype(float) ** 2))

        # Attenuation >= 15 dB means rms_out < rms_in * 0.35
        assert rms_out < rms_in * 0.35, f"60Hz noise not attenuated sufficiently: {rms_out} vs {rms_in}"

    def test_140hz_hpf_preserves_1000hz_vocal_fundamental(self):
        pipeline = ResilientAudioPipeline(hpf_cutoff_hz=140.0, sample_rate=16000)
        t = np.linspace(0, 0.1, 1600, endpoint=False)
        voice_tone_1000hz = (np.sin(2 * np.pi * 1000.0 * t) * 10000).astype(np.int16)

        filtered = pipeline.apply_140hz_hpf(voice_tone_1000hz)

        rms_in = np.sqrt(np.mean(voice_tone_1000hz.astype(float) ** 2))
        rms_out = np.sqrt(np.mean(filtered.astype(float) ** 2))

        # Preserved with > 85% transmission
        assert rms_out > rms_in * 0.85, f"1000Hz speech tone excessively attenuated: {rms_out} vs {rms_in}"

    def test_140hz_hpf_blocks_dc_offset(self):
        pipeline = ResilientAudioPipeline(hpf_cutoff_hz=140.0, sample_rate=16000)
        dc_step = np.full(1600, 5000, dtype=np.int16)
        filtered = pipeline.apply_140hz_hpf(dc_step)
        # Final samples must decay towards zero
        assert abs(int(filtered[-1])) < 50


# ============================================================================
# Vector 5: 48kHz Native Resampling & audioop.ratecv State Continuity
# ============================================================================

class TestAudioResamplingAndStateContinuity:
    """Validates verify_audio_resampling contract and ratecv state preservation (SPEC-04 / FM-VUI-001)."""

    def test_strict_resampling_contract_validation(self):
        pipeline = ResilientAudioPipeline()
        vui = VUIDialogueEngine()
        node = ReSpeakerVUINode.__new__(ReSpeakerVUINode)

        for engine in [pipeline, vui, node]:
            assert engine.verify_audio_resampling(16000, 48000) is True
            assert engine.verify_audio_resampling(24000, 48000) is False
            assert engine.verify_audio_resampling(48000, 16000) is False
            assert engine.verify_audio_resampling(16000, 16000) is False
            assert engine.verify_audio_resampling("16000", 48000) is False
            assert engine.verify_audio_resampling(None, 48000) is False
            assert engine.verify_audio_resampling(16000.0, 48000) is False
            assert engine.verify_audio_resampling(True, 48000) is False

    @pytest.mark.skipif(audioop is None, reason="audioop not available in environment")
    def test_audioop_ratecv_state_preservation_reduces_boundary_discontinuity(self):
        """
        Continuous passing of ratecv state reduces sample jumps at chunk boundaries
        by > 60%, preventing audible clicks.
        """
        fs = 16000
        t1 = np.linspace(0, 0.02, int(fs * 0.02), endpoint=False)
        t2 = np.linspace(0.02, 0.04, int(fs * 0.02), endpoint=False)
        chunk1 = (np.sin(2 * np.pi * 440.0 * t1) * 20000).astype(np.int16).tobytes()
        chunk2 = (np.sin(2 * np.pi * 440.0 * t2) * 20000).astype(np.int16).tobytes()

        # Resample with state preservation
        res1_state, state = audioop.ratecv(chunk1, 2, 1, 16000, 48000, None)
        res2_state, _ = audioop.ratecv(chunk2, 2, 1, 16000, 48000, state)

        # Resample without state preservation
        res1_nostate, _ = audioop.ratecv(chunk1, 2, 1, 16000, 48000, None)
        res2_nostate, _ = audioop.ratecv(chunk2, 2, 1, 16000, 48000, None)

        delta_with_state = abs(int(np.frombuffer(res2_state, np.int16)[0]) - int(np.frombuffer(res1_state, np.int16)[-1]))
        delta_without_state = abs(int(np.frombuffer(res2_nostate, np.int16)[0]) - int(np.frombuffer(res1_nostate, np.int16)[-1]))

        assert delta_with_state < delta_without_state
        assert delta_with_state < delta_without_state * 0.40  # >= 60% reduction in boundary jump

    def test_resampling_output_length_exact_3_to_1_ratio(self):
        pipeline = ResilientAudioPipeline(target_dac_rate=48000)
        # 1600 samples of 16kHz mono int16 = 3200 bytes
        input_pcm16 = np.zeros(1600, dtype=np.int16).tobytes()
        resampled = pipeline.resample_chunk(input_pcm16, input_rate=16000)
        # Resampled length must be exactly 4800 samples = 9600 bytes (+/- 4 bytes interpolation boundary)
        assert len(resampled) in (9596, 9600, 9604)


# ============================================================================
# Vector 6: 3-Chunk Jitter Buffer & Barge-In Flush
# ============================================================================

class TestJitterBufferAndFlush:
    """Validates 3-chunk jitter buffer depth (~120ms) and instant flush (SPEC-04 / FM-VUI-040)."""

    def test_jitter_buffer_prebuffering_and_output(self):
        pipeline = ResilientAudioPipeline(jitter_buffer_target_depth=3)
        # Push chunk 1 -> buffer has 1, returns None
        assert pipeline.push_jitter_chunk(b"chunk_1") is None
        # Push chunk 2 -> buffer has 2, returns None
        assert pipeline.push_jitter_chunk(b"chunk_2") is None
        # Push chunk 3 -> buffer reaches target depth 3, returns oldest (chunk_1)
        out1 = pipeline.push_jitter_chunk(b"chunk_3")
        assert out1 == b"chunk_1"
        # Push chunk 4 -> returns chunk_2
        out2 = pipeline.push_jitter_chunk(b"chunk_4")
        assert out2 == b"chunk_2"

    def test_instant_flush_on_barge_in_or_estop(self):
        pipeline = ResilientAudioPipeline(jitter_buffer_target_depth=3)
        pipeline.push_jitter_chunk(b"c1")
        pipeline.push_jitter_chunk(b"c2")
        assert len(pipeline.jitter_buffer) == 2

        # User speaks / E-stop occurs -> instant flush
        flushed_count = pipeline.clear_jitter_buffer()
        assert flushed_count == 2
        assert len(pipeline.jitter_buffer) == 0
        assert pipeline.ratecv_state is None

    def test_barge_in_gain_limiter_attenuation(self):
        pipeline = ResilientAudioPipeline()
        assert pipeline.stt_gain == 1.00
        pipeline.set_barge_in_mode(is_tts_speaking=True)
        assert pipeline.stt_gain == 0.10
        pipeline.set_barge_in_mode(is_tts_speaking=False)
        assert pipeline.stt_gain == 1.00


# ============================================================================
# Vector 7: Transparent Piper TTS Local Bypass
# ============================================================================

class TestPiperTTSLocalBypass:
    """
    Validates that operational actions (motion, battery status, navigation)
    are synthesized locally via Piper TTS while Gemini Live stays strictly DISCONNECTED.
    """

    def test_operational_action_keeps_gemini_disconnected(self):
        fsm = LiveConnectionManager()
        piper = PiperTTSClient()
        audio = ResilientAudioPipeline()

        # Operational action: query battery
        action_name = "get_battery_status"
        response_text = "Batteria all'88 percento, tensione 11.8 Volt, stato nominale."

        # Ensure Gemini Live starts DISCONNECTED
        assert fsm.state == LiveState.DISCONNECTED

        # Synthesize via Piper bypass
        pcm16 = piper.handle_operational_action(
            action_name=action_name,
            result_text=response_text,
            live_connection_mgr=fsm,
            resampler_func=audio.resample_16k_to_48k
        )

        # Output reaches DAC at 48kHz
        assert len(pcm16) > 0
        # Gemini WebSocket remained completely untouched and DISCONNECTED
        assert fsm.state == LiveState.DISCONNECTED
        assert fsm.session_count == 0
        assert fsm.savings_tracker.cloud_live_intents == 0

    def test_piper_synthesize_word_proportional_duration(self):
        piper = PiperTTSClient()
        short_pcm = piper.synthesize("Stop")
        long_pcm = piper.synthesize("Procedo con l'esplorazione autonoma e la mappatura a frontiera dell'ambiente.")
        assert len(long_pcm) > len(short_pcm)


# ============================================================================
# Vector 8: Default Production Clocks (current_time=None) & Zero Duration Explosion
# ============================================================================

class TestDefaultProductionClockLifecycleAndSavings:
    """
    Comprehensive verification suite for Milestone 4 default production clocks:
    1. Zero duration explosion (< 1.0s authentic elapsed time).
    2. Zero premature timeout on tick 0 (stays ACTIVE).
    3. Deterministic 25.0s boundary transitions under monotonic clock.
    4. on_user_activity() reset under monotonic clock.
    5. Clean duration savings >= 70.0% and token savings >= 70.0% under default clock.
    6. Async event loop connection and timer execution with default clocks.
    7. Atomic read-and-clear of _connection_open_timestamp.
    8. Multi-thread concurrency with zero double-counting.
    """

    def test_default_clock_session_activation_and_tick_no_premature_timeout(self):
        """
        Ensures that invoking trigger_conversation() and update_tick() with default
        clocks (current_time=None) maintains ACTIVE state and does NOT instantly
        time out due to clock domain mismatch.
        """
        mgr = LiveConnectionManager()
        st = mgr.trigger_conversation(reason="default_clock_test")
        assert st == LiveState.ACTIVE
        assert mgr.state == LiveState.ACTIVE

        # Immediate tick with default clock (simulating 10Hz/20Hz supervisor tick)
        st_tick = mgr.update_tick()
        assert st_tick == LiveState.ACTIVE, (
            f"Premature timeout detected! State became {st_tick}. "
            f"Check for clock domain mixing between time.time and time.monotonic."
        )
        assert mgr.state == LiveState.ACTIVE
        assert mgr.total_connected_seconds < 1.0

    def test_default_clock_trigger_disconnect_zero_duration_explosion(self):
        """
        Ensures that trigger_disconnect() with default clock records strictly
        sub-second connection duration (< 1.0s), preventing 1.79e9s metric explosion.
        """
        mgr = LiveConnectionManager()
        mgr.trigger_conversation()
        assert mgr.state == LiveState.ACTIVE

        # Simulate brief conversation turn (20ms)
        time.sleep(0.02)

        st_disc = mgr.trigger_disconnect(reason="user_stop")
        assert st_disc == LiveState.DISCONNECTED
        assert mgr.state == LiveState.DISCONNECTED

        # Duration must reflect authentic elapsed time (< 1.0s, not ~1.79e9s)
        assert 0.01 <= mgr.total_connected_seconds < 1.0, (
            f"Duration exploded to {mgr.total_connected_seconds}s! Expected < 1.0s."
        )

        # Snapshot verification with mission uptime
        mgr.savings_tracker.record_uptime(3600.0)
        snapshot = mgr.savings_tracker.get_snapshot()
        assert snapshot.connected_sec < 1.0
        assert snapshot.actual_ondemand_tokens < 100, (
            f"Ondemand tokens exploded to {snapshot.actual_ondemand_tokens}!"
        )
        assert snapshot.duration_savings_pct >= 70.0
        assert snapshot.guarantee_satisfied is True

    @pytest.mark.asyncio
    async def test_default_clock_async_connected_session_tick_and_disconnect(self):
        """
        Verifies async connection with mock LiveClient:
        1. _do_connect_async sets _connection_open_timestamp via monotonic clock.
        2. update_tick() with default clock keeps state ACTIVE.
        3. trigger_disconnect() records authentic duration (< 1.0s).
        """
        class MockLiveSession:
            pass

        class MockClient:
            class MockAio:
                class MockLive:
                    def connect(self, *args, **kwargs):
                        class ConnCtx:
                            async def __aenter__(self):
                                return MockLiveSession()
                            async def __aexit__(self, *args):
                                pass
                        return ConnCtx()
                live = MockLive()
            aio = MockAio()

        loop = asyncio.get_running_loop()
        mgr = LiveConnectionManager(client=MockClient(), loop=loop)
        mgr._transition_to(LiveState.CONNECTING)
        await mgr._do_connect_async(reason="test_async_default_clock")

        assert mgr.state == LiveState.ACTIVE
        assert mgr._connection_open_timestamp is not None

        # Tick with default clock: must stay ACTIVE
        st_tick = mgr.update_tick()
        assert st_tick == LiveState.ACTIVE
        assert mgr.total_connected_seconds == 0.0

        # Wait 20ms then disconnect with default clock
        await asyncio.sleep(0.02)
        st_disc = mgr.trigger_disconnect()
        assert st_disc in [LiveState.DISCONNECTING, LiveState.DISCONNECTED]
        await asyncio.sleep(0.02)
        assert mgr.state == LiveState.DISCONNECTED
        assert 0.01 <= mgr.total_connected_seconds < 1.0

    def test_default_clock_25s_inactivity_boundary_with_monotonic_patch(self, monkeypatch):
        """
        Validates exact 25.0s boundary precision with default clocks (current_time=None)
        by deterministically advancing time.monotonic.
        """
        current_mono = 1000.0

        def mock_monotonic():
            return current_mono

        monkeypatch.setattr(time, "monotonic", mock_monotonic)

        mgr = LiveConnectionManager()
        # Trigger conversation with default clock (uses mock_monotonic -> 1000.0)
        st = mgr.trigger_conversation(reason="patch_mono_test")
        assert st == LiveState.ACTIVE

        # Advance monotonic clock by 24.999s -> 1024.999
        current_mono = 1000.0 + 24.999
        st_pre = mgr.update_tick()
        assert st_pre == LiveState.ACTIVE, "Timed out prematurely at 24.999s!"
        assert mgr.state == LiveState.ACTIVE

        # Advance monotonic clock to exactly 25.000s -> 1025.000
        current_mono = 1000.0 + 25.000
        st_timeout = mgr.update_tick()
        assert st_timeout == LiveState.IDLE_TIMED_OUT, "Failed to time out at 25.000s!"
        assert mgr.state == LiveState.IDLE_TIMED_OUT

        # Duration must be exactly 25.0s, not exploded
        assert mgr.total_connected_seconds == pytest.approx(25.0, abs=1e-3)

    def test_default_clock_user_activity_resets_inactivity_boundary(self, monkeypatch):
        """
        Validates that on_user_activity() with default clock resets the 25.0s window
        so that timeout occurs at t_speech + 25.0s.
        """
        current_mono = 2000.0

        def mock_monotonic():
            return current_mono

        monkeypatch.setattr(time, "monotonic", mock_monotonic)

        mgr = LiveConnectionManager()
        mgr.trigger_conversation()
        assert mgr.state == LiveState.ACTIVE

        # Advance 15.0s to 2015.0
        current_mono = 2015.0
        assert mgr.update_tick() == LiveState.ACTIVE

        # User speaks at 2015.0 with default clock
        mgr.on_user_activity()

        # Advance another 15.0s to 2030.0 (30.0s after start, but only 15.0s after speech)
        current_mono = 2030.0
        assert mgr.update_tick() == LiveState.ACTIVE

        # Advance to 2039.999s (24.999s after speech): still ACTIVE
        current_mono = 2039.999
        assert mgr.update_tick() == LiveState.ACTIVE

        # Advance to 2040.000s (25.000s after speech): transitions to IDLE_TIMED_OUT
        current_mono = 2040.000
        assert mgr.update_tick() == LiveState.IDLE_TIMED_OUT
        assert mgr.total_connected_seconds == pytest.approx(40.0, abs=1e-3)

    def test_default_clock_savings_guarantee_ge_70_percent(self, monkeypatch):
        """
        Validates that LiveConnectionManager with default clocks guarantees
        duration_savings_pct >= 70.0% and token_savings_pct >= 70.0% in a 3600s mission.
        """
        current_mono = 5000.0

        def mock_monotonic():
            return current_mono

        monkeypatch.setattr(time, "monotonic", mock_monotonic)

        mgr = LiveConnectionManager()
        # Mission uptime: 3600.0s
        mgr.savings_tracker.record_uptime(3600.0)

        # 10 local operational actions
        for _ in range(10):
            mgr.savings_tracker.record_intent("LOCAL_QWEN")

        # 2 dialogue bursts:
        # Burst 1: 40s duration (t = 5100 -> 5140)
        current_mono = 5100.0
        mgr.trigger_conversation()
        current_mono = 5140.0
        mgr.trigger_disconnect()

        # Burst 2: 45s duration (t = 5500 -> 5545)
        current_mono = 5500.0
        mgr.trigger_conversation()
        current_mono = 5545.0
        mgr.trigger_disconnect()

        # End of mission at 3600s after start (5000 + 3600 = 8600.0)
        current_mono = 8600.0
        snap = mgr.savings_tracker.get_snapshot()

        # Total connected: 40 + 45 = 85.0s
        assert snap.connected_sec == pytest.approx(85.0, abs=1e-2)
        # Expected duration savings: (1.0 - 85/3600) * 100 = 97.64% >= 70%
        assert snap.duration_savings_pct >= 70.0
        assert snap.duration_savings_pct == pytest.approx(97.64, abs=0.1)
        assert snap.token_savings_pct >= 70.0
        assert snap.guarantee_satisfied is True
        assert mgr.calculate_savings(3600.0) >= 70.0

    @pytest.mark.asyncio
    async def test_default_clock_async_timer_callback_fires_and_records_duration(self):
        """
        Validates that when the async event loop timer fires, it uses monotonic time
        and records authentic duration without exploding total_connected_seconds.
        """
        loop = asyncio.get_running_loop()
        mgr = LiveConnectionManager(loop=loop)
        mgr.INACTIVITY_TIMEOUT_SEC = 0.05

        transitions = []
        mgr.set_state_changed_callback(lambda o, n: transitions.append((o, n)))

        mgr._state = LiveState.ACTIVE
        mgr._connection_open_timestamp = time.monotonic()
        mgr._reset_inactivity_timer()

        assert mgr.state == LiveState.ACTIVE

        # Wait for async timer to fire (0.05s timeout + buffer)
        await asyncio.sleep(0.08)

        # Must have transitioned through IDLE_TIMED_OUT
        assert (LiveState.ACTIVE, LiveState.IDLE_TIMED_OUT) in transitions
        assert mgr.state in [LiveState.IDLE_TIMED_OUT, LiveState.DISCONNECTED]
        assert 0.04 <= mgr.total_connected_seconds < 0.20, (
            f"Unexpected duration {mgr.total_connected_seconds}s after async timer fire!"
        )
        assert mgr.savings_tracker.ondemand_tokens < 100

    def test_atomic_connection_open_timestamp_read_and_clear(self):
        """
        Validates that concurrent or successive teardown calls (update_tick,
        trigger_disconnect, _on_inactivity_timer_fired) read and clear
        _connection_open_timestamp atomically, preventing double-duration counting.
        """
        mgr = LiveConnectionManager()
        mgr.trigger_conversation(current_time=100.0)
        assert mgr.state == LiveState.ACTIVE

        # First teardown via update_tick
        mgr.update_tick(current_time=125.0)
        assert mgr.state == LiveState.IDLE_TIMED_OUT
        dur1 = mgr.total_connected_seconds
        assert dur1 == pytest.approx(25.0, abs=1e-3)
        assert mgr._connection_open_timestamp is None

        # Second teardown via trigger_disconnect at same or later time
        mgr.trigger_disconnect(current_time=126.0)
        dur2 = mgr.total_connected_seconds
        assert dur2 == dur1, "Duration was incremented twice by successive teardowns!"

    def test_concurrent_disconnect_and_tick_no_double_counting(self):
        """
        Verifies that simultaneous execution of update_tick(), trigger_disconnect(),
        and close() across 16 threads records connection duration exactly once.
        """
        mgr = LiveConnectionManager()
        mgr._state = LiveState.ACTIVE
        mgr._connection_open_timestamp = 100.0
        now = 125.0  # 25s session
        num_threads = 16
        barrier = threading.Barrier(num_threads)

        def worker(tid: int):
            barrier.wait()
            if tid % 3 == 0:
                mgr.trigger_disconnect(current_time=now)
            elif tid % 3 == 1:
                mgr.update_tick(current_time=now)
            else:
                mgr.close(current_time=now)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Connection duration must be recorded exactly once
        assert mgr.total_connected_seconds == pytest.approx(25.0, abs=1e-3)
        assert mgr.savings_tracker.session_count == 1
        assert mgr.savings_tracker.total_connected_sec == pytest.approx(25.0, abs=1e-3)
        assert mgr.state in [LiveState.DISCONNECTED, LiveState.IDLE_TIMED_OUT, LiveState.DISCONNECTING]

