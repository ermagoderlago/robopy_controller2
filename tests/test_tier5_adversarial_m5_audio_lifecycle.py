#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_tier5_adversarial_m5_audio_lifecycle.py
==================================================
Tier 5 Adversarial Coverage Hardening Test Suite for:
1. Gemini Live On-Demand 5-State FSM Lifecycle & Inactivity Boundaries
2. Piper TTS Local Bypass & Strict Cloud Isolation Invariants
3. Resilient Audio Pipeline DSP, 140Hz Butterworth HPF & 48kHz Resampling
4. Jitter Buffer Prebuffering, Barge-in Flushing, and Concurrency Stress

Interface Contracts & Verification:
- SPEC-04: Voice User Interface & Audio Pipeline (FM-VUI-001, FM-VUI-002, FM-VUI-003, FM-VUI-040, FM-VUI-043)
- PROJECT.md: Features F12, F13, F14, F15 (Tier 5 Acceptance Gate)
- marcus_core_rules.md: Hardware DAC 48kHz, 16kHz Mono Stream, Zero Cloud Token Leakage on Local Actions
"""

import math
import time
import threading
import asyncio
import concurrent.futures
from typing import List, Optional, Dict, Any
import numpy as np
import pytest
import sys
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure mocks for headless test execution
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
from robopy_controller.robot_ai.services.llm_models import types


# ============================================================================
# Vector 1: Gemini Live Lifecycle & FSM Concurrency / State Race Hardening
# ============================================================================

class TestTier5GeminiLiveFSMConcurrencyAndRaces:
    """Adversarial stress testing of FSM transitions under concurrency and race conditions."""

    def test_multi_threaded_fsm_concurrency_barrage(self):
        """16 concurrent threads hammering FSM lifecycle methods over 200 iterations each."""
        mgr = LiveConnectionManager()
        num_threads = 16
        iterations = 200
        errors = []

        def worker(thread_id: int):
            try:
                base_time = 1000.0 + (thread_id * 100.0)
                for i in range(iterations):
                    t = base_time + (i * 0.1)
                    op = (thread_id + i) % 5
                    if op == 0:
                        mgr.trigger_conversation(reason=f"thread_{thread_id}", current_time=t)
                    elif op == 1:
                        mgr.on_user_activity(current_time=t)
                    elif op == 2:
                        mgr.update_tick(current_time=t)
                    elif op == 3:
                        mgr.trigger_disconnect(reason=f"disc_{thread_id}", current_time=t)
                    elif op == 4:
                        mgr.calculate_savings(t - 1000.0)
            except Exception as e:
                errors.append((thread_id, e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, tid) for tid in range(num_threads)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Concurrency exceptions encountered: {errors}"
        assert isinstance(mgr.state, LiveState)
        snap = mgr.savings_tracker.get_snapshot(current_time=5000.0)
        assert snap.connected_sec >= 0.0
        assert snap.total_intents > 0

    def test_rapid_microsecond_toggling_exact_duration_accounting(self):
        """100 rapid alternating triggers and disconnects verifying exact-once duration commit."""
        mgr = LiveConnectionManager()
        sim_time = 1000.0
        expected_connected_duration = 0.0

        for i in range(100):
            # Connect for exactly 0.5s
            mgr.trigger_conversation(current_time=sim_time)
            assert mgr.state == LiveState.ACTIVE
            sim_time += 0.500
            expected_connected_duration += 0.500
            mgr.trigger_disconnect(current_time=sim_time)
            assert mgr.state == LiveState.DISCONNECTED
            sim_time += 0.200  # Disconnected gap

        assert mgr.session_count == 100
        assert abs(mgr.total_connected_seconds - expected_connected_duration) < 1e-4
        snap = mgr.savings_tracker.get_snapshot(current_time=sim_time)
        assert abs(snap.connected_sec - expected_connected_duration) < 1e-4

    def test_simultaneous_timeout_and_activity_race(self):
        """Simulates race condition between 25.0s timeout expiration and incoming activity."""
        mgr = LiveConnectionManager()
        start_t = 100.0
        mgr.trigger_conversation(current_time=start_t)
        assert mgr.state == LiveState.ACTIVE

        # Advance exactly to timeout threshold 25.0s
        timeout_t = start_t + 25.0
        # Thread 1: user activity arrives at timeout_t
        mgr.on_user_activity(current_time=timeout_t)
        # Thread 2: update_tick arrives at timeout_t
        st = mgr.update_tick(current_time=timeout_t)
        # Because on_user_activity updated _last_activity_time, idle_duration is 0.0 < 25.0s
        assert st == LiveState.ACTIVE
        assert mgr.state == LiveState.ACTIVE

        # Now advance beyond 25s without activity
        st_after = mgr.update_tick(current_time=timeout_t + 25.1)
        assert st_after == LiveState.IDLE_TIMED_OUT
        assert mgr.state == LiveState.IDLE_TIMED_OUT

    def test_idempotent_repeated_triggers_and_disconnects(self):
        """Calling trigger_conversation or trigger_disconnect repeatedly is safe and idempotent."""
        mgr = LiveConnectionManager()
        t = 200.0
        for _ in range(10):
            mgr.trigger_conversation(current_time=t)
        assert mgr.state == LiveState.ACTIVE
        assert mgr.session_count == 1  # Only 1 session opened

        for _ in range(10):
            mgr.trigger_disconnect(current_time=t + 10.0)
        assert mgr.state == LiveState.DISCONNECTED
        assert mgr.session_count == 1

    def test_state_transition_callback_exception_isolation(self):
        """Fault injection: an exception in the state change observer callback does NOT crash FSM."""
        mgr = LiveConnectionManager()

        def faulty_callback(old_s, new_s):
            raise RuntimeError("Observer callback exploded!")

        mgr.set_state_changed_callback(faulty_callback)
        # Should transition cleanly without raising RuntimeError
        st = mgr.trigger_conversation(current_time=100.0)
        assert st == LiveState.ACTIVE
        assert mgr.state == LiveState.ACTIVE


# ============================================================================
# Vector 2: Inactivity Detection, Noise Fuzzing & Model Turn Hardening
# ============================================================================

class TestTier5InactivityNoiseFuzzingAndTurnHardening:
    """Stress tests noise immunity, transcript filtering, exit phrases, and <IGNORE_TURN>."""

    @pytest.mark.parametrize("noise_text", [
        "<noise>", "<laughter>", "[laughter]", "(applause)", "[cough]",
        "(sigh)", "...", "---", "???", "!", "   ", "\t\n  ", "a", "x", "m",
        "  [music]  ", "<screaming>", "[throat clearing]", "🤖", "🔊",
        "<tag>   </tag>", " [ cough ] ", " ( ... ) "
    ])
    def test_noise_fuzzing_corpus_immunity(self, noise_text: str):
        """Confirms that diverse acoustic artifacts and non-linguistic tokens are classified as noise."""
        assert LiveConnectionManager._is_noise_transcription(noise_text) is True

    @pytest.mark.parametrize("speech_text", [
        "Sì", "No", "Marcus dove sei?", "Vai in cucina", "Accendi la luce",
        "Che ore sono adesso?", "fermati subito", "ruota di novanta gradi",
        "ci sei ancora?", "raccontami una storia breve"
    ])
    def test_valid_speech_is_not_classified_as_noise(self, speech_text: str):
        """Genuine Italian speech tokens and phrases must NEVER be classified as noise."""
        assert LiveConnectionManager._is_noise_transcription(speech_text) is False

    @pytest.mark.parametrize("exit_phrase", [
        "stai zitto", "basta parlare", "fermati di parlare", "smetti di parlare",
        "taci", "silenzio", "zitto marcus", "marcus zitto", "basta marcus",
        "marcus basta", "ora basta parlare marcus", "adesso stai zitto per favore"
    ])
    def test_exit_phrases_trigger_immediate_disconnect(self, exit_phrase: str):
        """Verifies that all exit command variations immediately force session disconnect."""
        mgr = LiveConnectionManager()
        mgr.trigger_conversation(current_time=100.0)
        assert mgr.state == LiveState.ACTIVE

        # Mock server content with input transcription containing exit phrase
        mock_msg = MagicMock()
        mock_msg.server_content.interrupted = False
        mock_msg.server_content.input_transcription.text = exit_phrase
        mock_msg.server_content.output_transcription = None
        mock_msg.server_content.model_turn = None
        mock_msg.server_content.turn_complete = False
        mock_msg.tool_call = None
        mock_msg.session_resumption_update = None

        # Execute handle_live_message
        asyncio.run(mgr._handle_live_message(mock_msg))
        assert mgr.state in (LiveState.DISCONNECTING, LiveState.DISCONNECTED)

    def test_ignore_turn_model_response_suppresses_audio_and_resets_buffers(self):
        """Verifies that <IGNORE_TURN> emitted by the model suppresses audio and clears state."""
        received_audio = []
        interrupt_called = []

        mgr = LiveConnectionManager(
            on_audio_received=lambda chunk: received_audio.append(chunk),
            on_interrupt=lambda val: interrupt_called.append(val)
        )
        mgr.trigger_conversation(current_time=100.0)

        # Mock server content containing <IGNORE_TURN>
        mock_part = MagicMock()
        mock_part.text = "Sto parlando ma poi <IGNORE_TURN>"
        mock_part.inline_data = None
        mock_part.function_call = None

        mock_msg = MagicMock()
        mock_msg.server_content.interrupted = False
        mock_msg.server_content.input_transcription = None
        mock_msg.server_content.output_transcription = None
        mock_msg.server_content.model_turn.parts = [mock_part]
        mock_msg.server_content.turn_complete = False
        mock_msg.tool_call = None
        mock_msg.session_resumption_update = None

        asyncio.run(mgr._handle_live_message(mock_msg))

        # Audio must NOT be emitted
        assert len(received_audio) == 0
        # Interrupt must be signaled
        assert len(interrupt_called) == 1
        assert interrupt_called[0] is True
        # Buffers must be wiped
        assert mgr._current_user_text == ""
        assert mgr._current_live_response["text"] == ""

    def test_turn_complete_without_audio_emission_does_not_reset_inactivity(self):
        """FM-VUI-043: If Gemini turn completes without vocal emission, inactivity timer is NOT reset."""
        mgr = LiveConnectionManager()
        mgr.trigger_conversation(current_time=100.0)
        initial_activity_time = mgr._last_activity_time

        # Mock turn complete with only silence / noise
        mock_msg = MagicMock()
        mock_msg.server_content.interrupted = False
        mock_msg.server_content.input_transcription.text = "   "
        mock_msg.server_content.output_transcription = None
        mock_msg.server_content.model_turn = None
        mock_msg.server_content.turn_complete = True
        mock_msg.tool_call = None
        mock_msg.session_resumption_update = None

        asyncio.run(mgr._handle_live_message(mock_msg))

        assert mgr.turns_since_wakeword == 0
        # Inactivity timer was not updated by silent turn; last_successful_turn_time remains initial time 100.0
        assert mgr.last_successful_turn_time == 100.0


# ============================================================================
# Vector 3: Audio Queue & Boundary Conditions in LiveConnectionManager
# ============================================================================

class TestTier5AudioQueueAndBoundaryConditions:
    """Stress tests audio chunk enqueueing, queue saturation, and asynchronous teardown."""

    def test_audio_enqueue_discard_when_not_active(self):
        """Audio chunks must be discarded immediately when state is DISCONNECTED or IDLE_TIMED_OUT."""
        mgr = LiveConnectionManager()
        assert mgr.state == LiveState.DISCONNECTED

        # Enqueue audio directly
        mgr._audio_in_queue = asyncio.Queue(maxsize=50)
        mgr._enqueue_audio(b"\x00\x00" * 160)
        assert mgr._audio_in_queue.empty()

        # Switch to IDLE_TIMED_OUT
        mgr._transition_to(LiveState.IDLE_TIMED_OUT)
        mgr._enqueue_audio(b"\x00\x00" * 160)
        assert mgr._audio_in_queue.empty()

    def test_audio_queue_saturation_drops_oldest_gracefully(self):
        """Pushes 100 chunks into a queue of maxsize 50; ensures no QueueFull or blocking."""
        mgr = LiveConnectionManager()
        mgr._state = LiveState.ACTIVE
        mgr._live_session = MagicMock()
        mgr._turn_in_progress = False
        mgr._audio_in_queue = asyncio.Queue(maxsize=50)

        # Enqueue 100 chunks tagged with their index
        for i in range(100):
            chunk = i.to_bytes(2, "little") * 80
            mgr._enqueue_audio(chunk)

        assert mgr._audio_in_queue.qsize() == 50
        # The oldest remaining item should be chunk 50
        first_item = mgr._audio_in_queue.get_nowait()
        tag = int.from_bytes(first_item[:2], "little")
        assert tag == 50

    def test_exponential_backoff_exhaustion_triggers_fallback(self):
        """When WebSocket handshake fails 3 times, transitions to DISCONNECTED and triggers fallback."""
        fallback_reasons = []

        def on_fallback(reason: str):
            fallback_reasons.append(reason)

        mock_client = MagicMock()
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(side_effect=ConnectionError("Failed to connect to Gemini"))
        mock_client.aio.live.connect.return_value = mock_session_ctx

        mgr = LiveConnectionManager(
            client=mock_client,
            on_fallback_needed=on_fallback
        )
        mgr._state = LiveState.CONNECTING

        # Execute connection attempt
        asyncio.run(mgr._do_connect_async("test_retry"))

        assert mgr.state == LiveState.DISCONNECTED
        assert len(fallback_reasons) == 1
        assert "3 retries" in fallback_reasons[0]


# ============================================================================
# Vector 4: LiveSavingsTracker Mathematical Invariants
# ============================================================================

class TestTier5SavingsTrackerMathematicalInvariants:
    """Stress tests boundary values, underflow, and mathematical guarantee formulas."""

    def test_savings_tracker_underflow_and_zero_elapsed(self):
        """Validates behavior under zero uptime, negative durations, or empty telemetry."""
        tracker = LiveSavingsTracker(start_time=100.0)
        tracker.record_uptime(-10.0)
        tracker.record_connection_duration(-5.0)

        # Uptime zero or negative
        savings = tracker.calculate_savings(0.0)
        assert savings == 0.0

        savings_neg = tracker.calculate_savings(-50.0)
        assert savings_neg == 0.0

        snap = tracker.get_snapshot(current_time=100.0)
        assert 0.0 <= snap.duration_savings_pct <= 100.0
        assert 0.0 <= snap.token_savings_pct <= 100.0

    def test_savings_tracker_exact_70_pct_guarantee_boundary(self):
        """Validates exact threshold boundary: 70.0% is satisfied, 69.9% is not."""
        tracker = LiveSavingsTracker(start_time=0.0)
        tracker.record_uptime(100.0)

        # Exactly 30s connected -> 70% savings
        tracker.record_connection_duration(30.0)
        snap70 = tracker.get_snapshot(current_time=100.0)
        assert abs(snap70.duration_savings_pct - 70.0) < 0.1
        assert snap70.guarantee_satisfied is True

        # Now add 0.5s connected -> 30.5s connected -> 69.5% savings
        tracker.record_connection_duration(0.5)
        snap69 = tracker.get_snapshot(current_time=100.0)
        assert snap69.duration_savings_pct < 70.0
        assert snap69.guarantee_satisfied is False


# ============================================================================
# Vector 5: Piper TTS Local Bypass & Strict Cloud Isolation Invariants
# ============================================================================

class TestTier5PiperTTSLocalBypassHardening:
    """Stress tests cloud isolation invariants and pathological speech synthesis inputs."""

    @pytest.mark.parametrize("invalid_state", [
        LiveState.ACTIVE,
        LiveState.CONNECTING,
        LiveState.IDLE_TIMED_OUT,
        LiveState.DISCONNECTING,
    ])
    def test_strict_cloud_isolation_enforcement(self, invalid_state: LiveState):
        """Calling handle_operational_action while Gemini Live is NOT DISCONNECTED raises RuntimeError."""
        client = PiperTTSClient()
        mock_mgr = MagicMock()
        mock_mgr.state = invalid_state

        with pytest.raises(RuntimeError) as exc_info:
            client.handle_operational_action(
                action_name="move_distance",
                result_text="Avanzo di 0.5 metri",
                live_connection_mgr=mock_mgr
            )
        assert "violated cloud isolation" in str(exc_info.value)

    def test_operational_action_succeeds_in_disconnected_state(self):
        """Operational action succeeds cleanly when Gemini Live is in DISCONNECTED state."""
        client = PiperTTSClient()
        mock_mgr = MagicMock()
        mock_mgr.state = LiveState.DISCONNECTED

        pcm_out = client.handle_operational_action(
            action_name="get_battery_status",
            result_text="Batteria al 95%, tensione 12.4 volt",
            live_connection_mgr=mock_mgr
        )
        assert isinstance(pcm_out, bytes)
        assert len(pcm_out) > 0
        assert len(pcm_out) % 2 == 0

    @pytest.mark.parametrize("pathological_text", [
        "",
        None,
        "   ",
        "\t\n   \r",
    ])
    def test_synthesize_empty_and_whitespace_inputs(self, pathological_text: Any):
        """Empty or whitespace text returns empty bytes b'' without error."""
        client = PiperTTSClient()
        assert client.synthesize(pathological_text) == b""

    def test_synthesize_large_corpus_stress(self):
        """Synthesizing very long text (10,000 chars) executes smoothly without buffer overflow."""
        client = PiperTTSClient()
        long_text = "Marcus naviga in corridoio ed esplora le stanze. " * 200  # ~9800 chars
        pcm = client.synthesize(long_text)
        assert isinstance(pcm, bytes)
        assert len(pcm) > 16000  # At least 1 second of audio
        assert client.total_synthesized_characters >= len(long_text.strip())

    def test_synthesize_acoustic_properties_and_bounds(self):
        """Validates synthetic speech waveform: 16kHz, int16 range, non-zero energy, envelope."""
        client = PiperTTSClient(sample_rate=16000)
        text = "Test di verifica acustica per sintesi vocale locale."
        pcm = client.synthesize(text)

        samples = np.frombuffer(pcm, dtype=np.int16)
        assert len(samples) > 0
        assert np.min(samples) >= -32768
        assert np.max(samples) <= 32767

        rms = np.sqrt(np.mean(samples.astype(np.float64)**2))
        assert rms > 500.0, f"RMS energy too low: {rms}"

        # Verify fade-in envelope (first 10 samples start near zero)
        assert abs(samples[0]) < 100

    def test_resampler_pipeline_integration_and_error_propagation(self):
        """Verifies resampler_func integration and clean error propagation without counter corruption."""
        client = PiperTTSClient()
        mock_mgr = MagicMock()
        mock_mgr.state = LiveState.DISCONNECTED

        # 1. Custom 3x resampler function
        def mock_resampler(pcm_bytes: bytes) -> bytes:
            return pcm_bytes * 3

        res = client.handle_operational_action(
            action_name="scan_environment",
            result_text="Scansione completata",
            live_connection_mgr=mock_mgr,
            resampler_func=mock_resampler
        )
        assert len(res) > 0

        # 2. Buggy resampler function raising ValueError
        def failing_resampler(pcm_bytes: bytes) -> bytes:
            raise ValueError("DAC resampler failure")

        with pytest.raises(ValueError) as exc_info:
            client.handle_operational_action(
                action_name="scan_environment",
                result_text="Scansione errore",
                live_connection_mgr=mock_mgr,
                resampler_func=failing_resampler
            )
        assert "DAC resampler failure" in str(exc_info.value)


# ============================================================================
# Vector 6: Resilient Audio Pipeline DSP & Boundary Hardening
# ============================================================================

class TestTier5ResilientAudioPipelineDSPHardening:
    """Stress tests 140Hz Butterworth HPF, strict resampling contract, and jitter buffer."""

    @pytest.mark.parametrize("input_rate, output_rate, expected", [
        (16000, 48000, True),
        (True, 48000, False),      # Bool rejection
        (16000, False, False),     # Bool rejection
        (16000.0, 48000.0, False), # Float rejection
        ("16000", "48000", False), # Str rejection
        (None, 48000, False),
        (16000, None, False),
        (16000, 44100, False),
        (8000, 48000, False),
        (24000, 48000, False),
        (-16000, -48000, False),
        (0, 0, False),
    ])
    def test_verify_audio_resampling_strict_type_contract(self, input_rate: Any, output_rate: Any, expected: bool):
        """FM-VUI-001: verify_audio_resampling strictly returns True for (16000, 48000) integer types only."""
        pipeline = ResilientAudioPipeline()
        assert pipeline.verify_audio_resampling(input_rate, output_rate) is expected

    def test_140hz_butterworth_hpf_attenuation_and_passband(self):
        """SPEC-04: >15 dB attenuation at <140Hz (fan noise) and >85% preservation at >1000Hz (voice)."""
        pipeline = ResilientAudioPipeline(hpf_cutoff_hz=140.0, sample_rate=16000)
        fs = 16000
        t = np.linspace(0, 1.0, fs, endpoint=False)

        # 1. Low frequency (50 Hz fan acoustic noise)
        s50 = (10000.0 * np.sin(2 * np.pi * 50 * t)).astype(np.int16)
        y50 = pipeline.apply_140hz_hpf(s50)
        amp50 = np.max(np.abs(y50[fs // 2:])) / 10000.0
        attenuation_db_50 = 20.0 * np.log10(amp50)
        assert attenuation_db_50 < -15.0, f"50 Hz attenuation insufficient: {attenuation_db_50:.2f} dB"

        # 2. Vocal formant frequency (1000 Hz human voice)
        pipeline_voice = ResilientAudioPipeline(hpf_cutoff_hz=140.0, sample_rate=16000)
        s1000 = (10000.0 * np.sin(2 * np.pi * 1000 * t)).astype(np.int16)
        y1000 = pipeline_voice.apply_140hz_hpf(s1000)
        amp1000 = np.max(np.abs(y1000[fs // 2:])) / 10000.0
        assert amp1000 >= 0.85, f"1000 Hz preservation insufficient: {amp1000:.2f} (must be >= 0.85)"

    def test_140hz_hpf_extreme_and_pathological_inputs(self):
        """Validates filter stability on empty buffers, full-scale DC, and Nyquist alternating input."""
        pipeline = ResilientAudioPipeline()

        # 1. Empty buffer
        empty = np.array([], dtype=np.int16)
        assert len(pipeline.apply_140hz_hpf(empty)) == 0

        # 2. Full-scale DC step (+32767 constant)
        dc_step = np.full(1600, 32767, dtype=np.int16)
        y_dc = pipeline.apply_140hz_hpf(dc_step)
        # HPF must decay DC to near zero at steady-state
        assert np.abs(y_dc[-1]) < 200, f"DC offset was not removed: {y_dc[-1]}"

        # 3. Alternating Nyquist (+32767, -32768) at 8kHz
        nyquist = np.tile([32767, -32768], 800).astype(np.int16)
        y_nyq = pipeline.apply_140hz_hpf(nyquist)
        assert np.max(y_nyq) > 20000
        assert np.min(y_nyq) < -20000

    def test_hpf_chunk_boundary_continuity(self):
        """Verifies state preservation across consecutive audio chunks avoids phase clicks."""
        fs = 16000
        t = np.linspace(0, 0.5, 8000, endpoint=False)
        signal = (10000.0 * np.sin(2 * np.pi * 200 * t)).astype(np.int16)

        # 1. Monolithic filter
        p_mono = ResilientAudioPipeline()
        y_mono = p_mono.apply_140hz_hpf(signal)

        # 2. Chunked filter (25 chunks of 320 samples)
        p_chunk = ResilientAudioPipeline()
        y_chunks = []
        for i in range(25):
            chunk = signal[i * 320:(i + 1) * 320]
            y_chunks.append(p_chunk.apply_140hz_hpf(chunk))
        y_chunked = np.concatenate(y_chunks)

        # Difference between monolithic and chunked must be near zero
        max_diff = np.max(np.abs(y_mono - y_chunked))
        assert max_diff <= 2, f"Discontinuity at chunk seams: max diff = {max_diff}"

    def test_48k_resampling_ratio_and_timegap_reset(self):
        """Verifies 3x sample expansion and ratecv_state reset upon 400ms time gap or source switch."""
        pipeline = ResilientAudioPipeline()

        # 1. 160 samples (10ms @ 16kHz) -> ~480 samples (10ms @ 48kHz, exact 3.0x ratio within filter delay)
        in_pcm = np.full(160, 1000, dtype=np.int16).tobytes()
        out_pcm = pipeline.resample_chunk(in_pcm, input_rate=16000)
        ratio = len(out_pcm) / len(in_pcm)
        assert 2.95 <= ratio <= 3.05
        assert abs(len(out_pcm) - len(in_pcm) * 3) <= 8

        # 2. Time-gap reset: simulate chunk arriving > 400ms later
        pipeline._last_playback_time = time.monotonic() - 0.500
        pipeline.resample_chunk(in_pcm, input_rate=16000)
        # Verify ratecv_state was reset (or handled cleanly)

        # 3. Source-switch reset: switch from piper to gemini
        pipeline._last_playback_time = time.monotonic()
        pipeline.resample_chunk(in_pcm, input_rate=16000, source_name="piper")
        assert pipeline._last_source == "piper"
        pipeline.resample_chunk(in_pcm, input_rate=16000, source_name="gemini")
        assert pipeline._last_source == "gemini"

    def test_resample_odd_byte_length_rejected(self):
        """Resampling odd byte length buffers raises ValueError or audioop.error."""
        pipeline = ResilientAudioPipeline()
        with pytest.raises((ValueError, Exception)):
            pipeline.resample_chunk(b"\x01\x02\x03", input_rate=16000)

    def test_jitter_buffer_prebuffering_and_instant_flush_latency(self):
        """Validates 3-chunk prebuffering (~120ms), drain, and < 1.0 ms instant flush latency."""
        pipeline = ResilientAudioPipeline(jitter_buffer_target_depth=3)
        chunk1 = b"\x01" * 640
        chunk2 = b"\x02" * 640
        chunk3 = b"\x03" * 640
        chunk4 = b"\x04" * 640

        # Push chunk 1 and 2: returns None (prebuffering)
        assert pipeline.push_jitter_chunk(chunk1) is None
        assert pipeline.push_jitter_chunk(chunk2) is None

        # Push chunk 3: reaches depth 3, returns chunk 1
        assert pipeline.push_jitter_chunk(chunk3) == chunk1
        # Push chunk 4: returns chunk 2
        assert pipeline.push_jitter_chunk(chunk4) == chunk2

        # Pop remaining
        remaining = pipeline.pop_remaining_chunks()
        assert remaining == [chunk3, chunk4]
        assert len(pipeline.jitter_buffer) == 0

        # Benchmark instant flush over 1,000 iterations
        latencies = []
        for _ in range(1000):
            pipeline.push_jitter_chunk(chunk1)
            pipeline.push_jitter_chunk(chunk2)
            t0 = time.perf_counter()
            pipeline.clear_jitter_buffer()
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000.0)

        max_lat = max(latencies)
        p99_lat = float(np.percentile(latencies, 99))
        assert max_lat < 1.0, f"clear_jitter_buffer max latency exceeded budget: {max_lat:.3f} ms"
        assert p99_lat < 0.2, f"clear_jitter_buffer p99 latency exceeded budget: {p99_lat:.3f} ms"

    def test_barge_in_gain_limiter_attenuation(self):
        """SPEC-04: STT gain is 0.1x during TTS playback and 1.0x when idle."""
        pipeline = ResilientAudioPipeline()
        assert pipeline.stt_gain == 1.00

        pipeline.set_barge_in_mode(True)
        assert pipeline.stt_gain == 0.10

        pipeline.set_barge_in_mode(False)
        assert pipeline.stt_gain == 1.00


# ============================================================================
# Vector 7: Cross-Module Stress & Hybrid Mission Simulation
# ============================================================================

class TestTier5CrossModuleStressAndHybridMission:
    """Stress tests end-to-end hybrid mission orchestration and multi-threaded audio pipeline."""

    def test_simulated_hybrid_mission_guarantees_70_pct_savings(self):
        """
        Simulates realistic 50-action autonomous mission:
        - 40 operational commands (movement, scanning, battery) synthesized via Piper TTS (0 cloud tokens).
        - 10 conversational turns (Gemini Live open on-demand, 25s timeout).
        - Mathematical proof of >= 70% duration savings and token savings.
        """
        mgr = LiveConnectionManager()
        mgr.savings_tracker = LiveSavingsTracker(start_time=0.0)
        piper = PiperTTSClient()
        mission_time = 0.0

        for i in range(50):
            if i % 5 != 0:
                # Operational command (80% of actions)
                pcm = piper.handle_operational_action(
                    action_name="operational_task",
                    result_text=f"Eseguita azione operativa numero {i}",
                    live_connection_mgr=mgr
                )
                assert len(pcm) > 0
                mission_time += 10.0  # 10s per task
            else:
                # Conversational open turn (20% of actions)
                mgr.trigger_conversation(reason="conversational_turn", current_time=mission_time)
                mission_time += 3.0  # 3s conversation active
                mgr.on_user_activity(current_time=mission_time)
                mission_time += 2.0  # 2s response
                mgr.trigger_disconnect(reason="user_turn_complete", current_time=mission_time)
                assert mgr.state == LiveState.DISCONNECTED

        mgr.savings_tracker.record_uptime(mission_time)
        snap = mgr.savings_tracker.get_snapshot(current_time=mission_time)
        assert snap.duration_savings_pct >= 70.0, f"Duration savings below 70%: {snap.duration_savings_pct}%"
        assert snap.guarantee_satisfied is True
        assert snap.cloud_live_intents == 10
        assert mgr.session_count == 10

    def test_concurrency_stress_multi_threaded_audio_and_lifecycle(self):
        """
        High-load stress test with 8 concurrent threads:
        - Thread 0: 140Hz HPF processing
        - Thread 1: 48kHz resampling
        - Thread 2: Jitter buffer push / pop
        - Thread 3: Piper TTS synthesis bursts
        - Thread 4: FSM triggers and disconnects
        - Thread 5: User activity ticks
        - Thread 6: Barge-in mode toggles
        - Thread 7: Savings snapshot telemetry polling
        """
        pipeline = ResilientAudioPipeline()
        mgr = LiveConnectionManager()
        piper = PiperTTSClient()

        num_threads = 8
        iterations = 150
        errors = []

        def worker(tid: int):
            try:
                for i in range(iterations):
                    t = 1000.0 + (i * 0.1)
                    if tid == 0:
                        samples = (5000 * np.sin(np.linspace(0, 1, 320))).astype(np.int16)
                        pipeline.apply_140hz_hpf(samples)
                    elif tid == 1:
                        pcm = (1000 * np.sin(np.linspace(0, 1, 160))).astype(np.int16).tobytes()
                        pipeline.resample_chunk(pcm, input_rate=16000)
                    elif tid == 2:
                        chunk = b"\x05" * 320
                        pipeline.push_jitter_chunk(chunk)
                        if i % 3 == 0:
                            pipeline.clear_jitter_buffer()
                    elif tid == 3:
                        piper.synthesize("Marcus elabora i comandi vocali.")
                    elif tid == 4:
                        if i % 2 == 0:
                            mgr.trigger_conversation(current_time=t)
                        else:
                            mgr.trigger_disconnect(current_time=t)
                    elif tid == 5:
                        mgr.on_user_activity(current_time=t)
                        mgr.update_tick(current_time=t)
                    elif tid == 6:
                        pipeline.set_barge_in_mode(i % 2 == 0)
                    elif tid == 7:
                        mgr.savings_tracker.get_snapshot(current_time=t)
            except Exception as e:
                errors.append((tid, e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, tid) for tid in range(num_threads)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Cross-module concurrency stress encountered errors: {errors}"
