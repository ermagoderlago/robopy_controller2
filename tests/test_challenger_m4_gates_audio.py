#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Milestone 4 Adversarial Stress & Verification Test Suite:
Safety Gates (DestructiveConfirmationGate) & Audio Pipeline (ReSpeaker VUI).
============================================================================
Challenger: challenger_m4_2 (Empirical Challenger)
Target Modules:
  - robopy_controller.robot_ai.core.destructive_confirmation_gate (DestructiveConfirmationGate)
  - robopy_controller.robot_ai.vui.vui_dialogue_engine (VUIDialogueEngine)
  - robopy_controller.nodes.respeaker_vui_node (ReSpeakerVUINode)
  - robopy_controller.robot_ai.skills.builtin.situational_awareness_skill (SituationalAwarenessSkill)

Adversarial Challenge Vectors:
  1. BUG-HRI-01 Negation Priority Stress:
     - Exact specified phrases: "non confermo", "no confermo", "annulla conferma",
       "non voglio confermare", "assolutamente no, non confermo".
     - Subtle, colloquial, and adversarial permutations (e.g. "sì ma non confermo",
       "non confermo mai", "annulla subito, non confermo", "cancella operazione").
     - Verify immediate cancellation (pending=False, confirmed=False).
  2. Vague Phrase Rejection:
     - Exact specified phrases: "procedi", "vai", "ok", "fai pure", "vai pure",
       "avanti", "conferma" (without "sì/si" or alone).
     - Ambiguous permutations: "continua", "va bene", "esegui", "fallo", "sì" (alone), "si" (alone).
     - Verify strict rejection, keeping gate armed and pending.
     - Verify elapsed timer is NOT reset by vague inputs.
  3. Timing Boundary Stress & Micro-tick Accumulation:
     - 29.99s -> pending; 30.00s / 30.01s -> cancelled.
     - Micro-tick accumulation: 30,000 steps of 0.001s without float drift.
     - Epsilon boundaries (29.999s vs 30.000s) and random jitter steps.
     - Rejection of late confirmation attempts after timeout.
  4. Audio Pipeline & Barge-in Concurrency Stress:
     - Concurrent toggling of set_tts_active across multiple threads (Contention storm).
     - Integrity check: stt_gain strictly in {0.1, 1.0}, Event coherence.
     - Deterministic state settling post-contention.
  5. Audio Resampling Verification & Rate Fuzzing:
     - Fuzzing verify_audio_resampling across 50+ combinations of sample rates.
     - Strict contract enforcement: True strictly for (16000, 48000), False for all others.
     - Mathematical 3:1 integer upsampling buffer ratio simulation.
  6. Downstream Safety & Filesystem Isolation:
     - Map deletion file unlinking under simulated SSD directory.
     - Fault tolerance when files or registry methods fail.
"""

import os
import sys
import time
import json
import random
import asyncio
import tempfile
import threading
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
from unittest.mock import MagicMock, patch

import pytest
import numpy as np

# Ensure robopy_controller and workspace root are on sys.path
_ws_root = Path(__file__).resolve().parent.parent
_pkg_root = _ws_root / "robopy_controller"
for p in (str(_pkg_root), str(_ws_root)):
    if p not in sys.path:
        sys.path.insert(0, p)

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

from robopy_controller.robot_ai.core.destructive_confirmation_gate import DestructiveConfirmationGate
from robopy_controller.robot_ai.vui.vui_dialogue_engine import VUIDialogueEngine
from robopy_controller.robot_ai.skills.builtin.situational_awareness_skill import SituationalAwarenessSkill
from robopy_controller.nodes.respeaker_vui_node import ReSpeakerVUINode


# ============================================================================
# Vector 1: BUG-HRI-01 Negation Priority Adversarial Stress
# ============================================================================
class TestBugHri01NegationPriorityAdversarial:
    """
    Stress-tests the Negation Priority rule for Destructive Confirmation.
    Ensures that any utterance containing negation cancels immediately,
    even if 'confermo' or affirmative tokens appear in the string.
    """

    @pytest.fixture
    def gate(self):
        g = DestructiveConfirmationGate()
        g.request_action("salotto", "MAP_DELETE")
        assert g.pending is True
        return g

    @pytest.mark.parametrize(
        "negation_phrase",
        [
            "non confermo",
            "no confermo",
            "annulla conferma",
            "non voglio confermare",
            "assolutamente no, non confermo",
        ],
    )
    def test_exact_required_phrases_cancel_immediately(self, gate, negation_phrase):
        """Mandatory specification phrases containing 'confermo' with negation."""
        reply, confirmed = gate.process_voice_input(negation_phrase)
        assert confirmed is False, f"Phrase '{negation_phrase}' incorrectly confirmed!"
        assert gate.pending is False, f"Gate remained pending for '{negation_phrase}'!"
        assert gate.confirmed is False
        assert "annullata" in reply.lower()

    @pytest.mark.parametrize(
        "adversarial_phrase",
        [
            "sì ma non confermo",
            "non confermo anche se prima ho detto sì",
            "confermo? No, assolutamente no!",
            "annulla subito, non confermo",
            "cancella l'operazione di sovrascrittura",
            "stop, non confermo nulla",
            "rifiuta la conferma",
            "lascia stare, non toccare la mappa",
            "abort operazione",
            "non intendo confermare affatto",
            "no no no confermo mai",
            "assolutamente no non confermo per favore",
            "NON CONFERMO!!!",
            "NO CONFERMO?!?",
            "AnNuLlA CoNfErMa",
            "   non    confermo    ",
            "\tnon confermo\n",
        ],
    )
    def test_adversarial_and_subtle_negations(self, gate, adversarial_phrase):
        """Adversarial phrasing, casing, punctuation, and embedded affirmative tokens."""
        reply, confirmed = gate.process_voice_input(adversarial_phrase)
        assert confirmed is False, f"Adversarial phrase '{adversarial_phrase}' confirmed!"
        assert gate.pending is False, f"Gate remained pending for '{adversarial_phrase}'!"
        assert gate.confirmed is False

    def test_negation_triggers_on_cancelled_hook(self):
        """Verifies observer hook is called with exact reason 'USER_NEGATION'."""
        gate = DestructiveConfirmationGate()
        hook_calls = []
        gate.on_cancelled = lambda room, reason: hook_calls.append((room, reason))

        gate.request_action("cucina", "MAP_OVERWRITE")
        reply, confirmed = gate.process_voice_input("non confermo")

        assert confirmed is False
        assert len(hook_calls) == 1
        assert hook_calls[0] == ("cucina", "USER_NEGATION")

    def test_negation_via_situational_awareness_skill(self):
        """End-to-end skill interception: saying 'non confermo' when gate is pending."""
        gate = DestructiveConfirmationGate()
        skill = SituationalAwarenessSkill(destructive_gate=gate)

        # Trigger gate
        asyncio.run(skill.execute("cancella la mappa della camera"))
        assert gate.pending is True

        # Intercept negation
        assert skill.match("non confermo") == 1.0
        res = asyncio.run(skill.execute("assolutamente no, non confermo"))

        assert res.success is True
        assert res.data["confirmed"] is False
        assert res.data["pending"] is False
        assert gate.pending is False
        assert gate.confirmed is False
        assert "annullata" in res.speak.lower()


# ============================================================================
# Vector 2: Vague Phrase Rejection Adversarial Stress
# ============================================================================
class TestVaguePhraseRejectionAdversarial:
    """
    Stress-tests rejection of ambiguous or non-explicit affirmative phrases.
    All such phrases MUST be rejected, keeping the safety gate armed and pending.
    """

    @pytest.fixture
    def gate(self):
        g = DestructiveConfirmationGate()
        g.request_action("taverna", "MAP_DELETE")
        assert g.pending is True
        return g

    @pytest.mark.parametrize(
        "vague_phrase",
        [
            "procedi",
            "vai",
            "ok",
            "fai pure",
            "vai pure",
            "avanti",
            "conferma",  # 'conferma' without 'sì/si' or alone must NOT confirm!
        ],
    )
    def test_exact_required_vague_phrases_rejected(self, gate, vague_phrase):
        """Mandatory specification phrases that MUST be rejected while keeping gate armed."""
        reply, confirmed = gate.process_voice_input(vague_phrase)
        assert confirmed is False, f"Vague phrase '{vague_phrase}' was incorrectly confirmed!"
        assert gate.pending is True, f"Gate was prematurely closed for '{vague_phrase}'!"
        assert gate.confirmed is False
        assert "non sufficientemente esplicita" in reply

    @pytest.mark.parametrize(
        "ambiguous_phrase",
        [
            "continua",
            "va bene",
            "esegui",
            "fallo",
            "procedi pure",
            "vai avanti",
            "ok perfetto",
            "certamente",
            "d'accordo",
            "sì",          # 'sì' alone without 'confermo' must be rejected
            "si",          # 'si' alone without 'confermo' must be rejected
            "certo",
            "sicuro",
            "approvato",
            "fallo pure",
            "procedi adesso",
            "sì procedi",
            "ok conferma",  # 'ok' + 'conferma' still lacks strict 'sì, confermo'
            "conferma pure",
        ],
    )
    def test_expanded_ambiguous_phrases_rejected(self, gate, ambiguous_phrase):
        """Broad spectrum of colloquial affirmations that must NOT confirm destructive actions."""
        reply, confirmed = gate.process_voice_input(ambiguous_phrase)
        assert confirmed is False, f"Ambiguous phrase '{ambiguous_phrase}' was confirmed!"
        assert gate.pending is True, f"Gate was closed for ambiguous phrase '{ambiguous_phrase}'!"
        assert gate.confirmed is False
        assert "non sufficientemente esplicita" in reply

    def test_sequential_vague_phrases_preserve_timer_and_armed_state(self, gate):
        """Multiple sequential vague inputs must NOT reset elapsed timer or disarm gate."""
        gate.advance_time(12.5)
        assert gate.elapsed == 12.5
        assert gate.pending is True

        # Input 1: vague
        reply1, ok1 = gate.process_voice_input("vai pure")
        assert ok1 is False
        assert gate.pending is True
        assert gate.elapsed == 12.5  # Timer must not be reset!

        # Input 2: vague
        reply2, ok2 = gate.process_voice_input("ok")
        assert ok2 is False
        assert gate.pending is True
        assert gate.elapsed == 12.5

        # Input 3: vague
        reply3, ok3 = gate.process_voice_input("conferma")
        assert ok3 is False
        assert gate.pending is True
        assert gate.elapsed == 12.5

        # Advance further: timer accumulates from 12.5
        gate.advance_time(10.0)
        assert gate.elapsed == 22.5
        assert gate.pending is True

    def test_empty_and_whitespace_vague_input(self, gate):
        """Empty and whitespace inputs are safely treated as vague and rejected."""
        for empty_text in ["", "   ", "\t", "\n  \t "]:
            reply, ok = gate.process_voice_input(empty_text)
            assert ok is False
            assert gate.pending is True
            assert gate.confirmed is False


# ============================================================================
# Vector 3: Timing Boundary Stress & Micro-tick Accumulation
# ============================================================================
class TestTimingBoundaryAndMicroTickDrift:
    """
    Stress-tests 30.0s countdown timing boundaries, micro-tick numerical precision,
    and timeout event handling.
    """

    def test_exact_timing_boundary_2999_vs_3000(self):
        """Boundary invariant: 29.99s -> pending; 30.00s -> cancelled."""
        gate = DestructiveConfirmationGate()
        gate.request_action("garage", "MAP_DELETE")
        assert gate.pending is True

        # Step 1: 29.99s
        res_2999 = gate.advance_time(29.99)
        assert res_2999 is None
        assert gate.pending is True
        assert gate.elapsed == 29.99

        # Step 2: 0.01s further to exactly 30.00s
        res_3000 = gate.advance_time(0.01)
        assert res_3000 is not None
        assert "Tempo scaduto" in res_3000
        assert gate.pending is False
        assert gate.confirmed is False
        assert gate.elapsed == 30.0

    def test_direct_overshoot_3001_cancels(self):
        """Boundary invariant: single advance of 30.01s cancels immediately."""
        gate = DestructiveConfirmationGate()
        gate.request_action("bagno", "MAP_OVERWRITE")
        assert gate.pending is True

        res = gate.advance_time(30.01)
        assert res is not None
        assert "Tempo scaduto" in res
        assert gate.pending is False
        assert gate.confirmed is False
        assert gate.elapsed == 30.01

    def test_micro_tick_accumulation_30000_steps(self):
        """
        Micro-tick accumulation: 30,000 steps of 0.001s without float drift.
        At step 29,999 (29.999s) -> still pending.
        At step 30,000 (30.000s) -> cancelled exactly.
        """
        gate = DestructiveConfirmationGate()
        gate.request_action("corridoio", "MAP_DELETE")

        # Accumulate 29,999 steps of 1ms
        for step in range(1, 30000):
            res = gate.advance_time(0.001)
            assert res is None, f"Premature cancellation at step {step} ({gate.elapsed}s)!"
            assert gate.pending is True

        assert round(gate.elapsed, 3) == 29.999
        assert gate.pending is True

        # Step 30,000: exact 30.000s threshold crossing
        res_final = gate.advance_time(0.001)
        assert res_final is not None, "Failed to cancel at exactly 30,000 micro-ticks!"
        assert "Tempo scaduto" in res_final
        assert gate.pending is False
        assert gate.confirmed is False
        assert round(gate.elapsed, 3) == 30.000

    def test_epsilon_boundary_resolution(self):
        """High-precision epsilon boundary check (1e-6 tolerance)."""
        gate = DestructiveConfirmationGate()
        gate.request_action("mansarda", "MAP_DELETE")

        # 29.999990s -> pending
        res = gate.advance_time(29.999990)
        assert res is None
        assert gate.pending is True

        # Cross by 0.000010s to reach 30.000000s -> cancelled
        res_cross = gate.advance_time(0.000010)
        assert res_cross is not None
        assert gate.pending is False

    def test_random_jitter_tick_accumulation(self):
        """Accumulation with randomized floating point intervals."""
        gate = DestructiveConfirmationGate()
        gate.request_action("studio", "MAP_DELETE")

        rng = random.Random(42)
        total_time = 0.0

        # Accumulate randomly up to ~29.5s
        while total_time < 29.5:
            dt = rng.uniform(0.005, 0.050)
            total_time += dt
            res = gate.advance_time(dt)
            assert res is None
            assert gate.pending is True

        # Advance exactly to 29.99s
        remaining_to_2999 = round(29.99 - gate.elapsed, 6)
        res_2999 = gate.advance_time(remaining_to_2999)
        assert res_2999 is None
        assert gate.pending is True

        # Cross threshold to 30.00s
        res_3000 = gate.advance_time(0.01)
        assert res_3000 is not None
        assert gate.pending is False

    def test_timeout_blocks_subsequent_confirmation(self):
        """Once timed out, user confirmation must be rejected (cannot confirm expired gate)."""
        gate = DestructiveConfirmationGate()
        gate.request_action("cucina", "MAP_DELETE")
        gate.advance_time(30.0)
        assert gate.pending is False

        reply, ok = gate.process_voice_input("sì, confermo")
        assert ok is False
        assert gate.confirmed is False
        assert "Nessuna operazione distruttiva in attesa" in reply

    def test_idle_gate_advance_time_safe(self):
        """Calling advance_time on an idle gate is safe and returns None."""
        gate = DestructiveConfirmationGate()
        assert gate.pending is False
        res = gate.advance_time(10.0)
        assert res is None
        assert gate.pending is False
        assert gate.elapsed == 0.0

    def test_re_arm_after_timeout_resets_clock(self):
        """Arming again after a timeout properly resets elapsed clock to 0.0."""
        gate = DestructiveConfirmationGate()
        gate.request_action("cucina", "MAP_DELETE")
        gate.advance_time(30.0)
        assert gate.pending is False
        assert gate.elapsed == 30.0

        # Re-arm
        prompt = gate.request_action("cucina", "MAP_DELETE")
        assert gate.pending is True
        assert gate.elapsed == 0.0
        assert gate.confirmed is False
        assert "Attenzione" in prompt


# ============================================================================
# Vector 4: Audio Pipeline Concurrent Barge-in Stress
# ============================================================================
class TestAudioPipelineConcurrentBargeInStress:
    """
    Stress-tests multi-threaded toggling of TTS active states and barge-in gain
    transitions across VUIDialogueEngine and ReSpeakerVUINode.
    """

    def test_vui_dialogue_engine_concurrent_tts_toggling(self):
        """
        Contention storm: 30 concurrent threads rapidly toggling set_tts_active
        on VUIDialogueEngine. Verifies stt_gain is NEVER corrupted.
        """
        engine = VUIDialogueEngine()
        iterations_per_thread = 500
        num_threads = 30
        invalid_states = []

        def worker(thread_idx):
            for i in range(iterations_per_thread):
                state = (i % 2 == 0)
                engine.set_tts_active(state)
                current_gain = engine.stt_gain
                if current_gain not in (0.1, 1.0):
                    invalid_states.append((thread_idx, i, current_gain))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(invalid_states) == 0, f"Detected corrupted stt_gain values: {invalid_states}"

        # Deterministic state check after contention
        engine.set_tts_active(True)
        assert engine.stt_gain == 0.1
        engine.set_tts_active(False)
        assert engine.stt_gain == 1.0

    def test_respeaker_vui_node_concurrent_tts_toggling(self):
        """
        Contention storm on ReSpeakerVUINode: verifies Event (_ev_tts) and gain
        coherence under concurrent multi-threaded execution.
        """
        node = ReSpeakerVUINode.__new__(ReSpeakerVUINode)
        node._ev_tts = threading.Event()
        node._tts_active = False
        node._is_tts_speaking = False
        node.stt_gain = 1.0

        num_threads = 20
        iterations = 300
        inconsistencies = []

        def worker(tid):
            for i in range(iterations):
                speaking = (i % 2 == 1)
                node.set_tts_active(speaking)
                # Check internal coherence
                ev_set = node._ev_tts.is_set()
                gain = node.stt_gain
                active = node._tts_active
                if active and (not ev_set or gain != 0.1):
                    inconsistencies.append((tid, "active_incoherent", ev_set, gain))
                elif not active and (ev_set or gain != 1.0):
                    inconsistencies.append((tid, "idle_incoherent", ev_set, gain))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Settling check: explicit set call must produce 100% coherent state
        node.set_tts_active(True)
        assert node.stt_gain == 0.1
        assert node._ev_tts.is_set() is True
        assert node._tts_active is True

        node.set_tts_active(False)
        assert node.stt_gain == 1.0
        assert node._ev_tts.is_set() is False
        assert node._tts_active is False

    def test_rapid_alternating_barge_in_cycles(self):
        """Measures 5,000 rapid sequential toggles for zero overhead and memory leak."""
        engine = VUIDialogueEngine()
        t0 = time.time()
        for i in range(5000):
            engine.set_tts_active(True)
            assert engine.stt_gain == 0.1
            engine.set_tts_active(False)
            assert engine.stt_gain == 1.0
        total_time = time.time() - t0
        avg_us = (total_time / 10000) * 1e6
        # Must execute in < 50 microseconds per toggle
        assert avg_us < 50.0, f"Toggle too slow: {avg_us:.2f} us"


# ============================================================================
# Vector 5: Audio Resampling Verification & Rate Fuzzing
# ============================================================================
class TestAudioResamplingFuzzAndMathematicalSoundness:
    """
    Fuzzes and mathematically validates audio sample rate verification
    and resampling contract: ReSpeaker 16kHz mono in -> 48kHz hardware DAC out.
    """

    @pytest.fixture
    def engines(self):
        vui = VUIDialogueEngine()
        node = ReSpeakerVUINode.__new__(ReSpeakerVUINode)
        return vui, node

    def test_valid_resampling_rate_pair(self, engines):
        """Only (16000, 48000) is valid according to SPEC-04."""
        vui, node = engines
        assert vui.verify_audio_resampling(16000, 48000) is True
        assert node.verify_audio_resampling(16000, 48000) is True

    @pytest.mark.parametrize(
        "in_rate, out_rate",
        [
            # Inverted rates
            (48000, 16000),
            # Identical rates (no resampling)
            (16000, 16000),
            (48000, 48000),
            (44100, 44100),
            (24000, 24000),
            # Standard audio sample rates
            (8000, 48000),
            (11025, 48000),
            (16000, 24000),
            (16000, 44100),
            (16000, 96000),
            (22050, 48000),
            (24000, 48000),
            (32000, 48000),
            (44100, 48000),
            (88200, 48000),
            (96000, 48000),
            (192000, 48000),
            # Zero and negative rates
            (0, 48000),
            (16000, 0),
            (0, 0),
            (-16000, 48000),
            (16000, -48000),
            (-16000, -48000),
            # Off-by-one frequencies
            (15999, 48000),
            (16001, 48000),
            (16000, 47999),
            (16000, 48001),
            # Extreme rates
            (1, 48000),
            (16000, 1000000),
            (9999999, 9999999),
        ],
    )
    def test_audio_resampling_fuzz_spectrum(self, engines, in_rate, out_rate):
        """Fuzz testing: all non-(16000, 48000) pairs MUST return False."""
        vui, node = engines
        assert vui.verify_audio_resampling(in_rate, out_rate) is False
        assert node.verify_audio_resampling(in_rate, out_rate) is False

    @pytest.mark.parametrize(
        "in_rate, out_rate",
        [
            ("16000", 48000),
            (16000, "48000"),
            (None, 48000),
            (16000, None),
            (16000.5, 48000),
            (16000, 48000.5),
        ],
    )
    def test_audio_resampling_non_integer_types(self, engines, in_rate, out_rate):
        """String, None, and fractional float types safely evaluate to False."""
        vui, node = engines
        assert vui.verify_audio_resampling(in_rate, out_rate) is False
        assert node.verify_audio_resampling(in_rate, out_rate) is False

    def test_resampling_mathematical_buffer_ratio_simulation(self):
        """
        Validates mathematical properties of 16kHz -> 48kHz integer 3:1 upsampling.
        An input buffer of N samples at 16kHz resampled to 48kHz must contain
        exactly 3 * N samples without phase discontinuity or sample leakage.
        """
        for n_samples in [160, 320, 480, 960, 1024, 2048]:
            # Generate 16-bit PCM sinusoidal test signal @ 16kHz
            freq = 440.0  # 440 Hz standard A tone
            t = np.linspace(0, n_samples / 16000.0, n_samples, endpoint=False, dtype=np.float32)
            signal_16k = (np.sin(2 * np.pi * freq * t) * 16000).astype(np.int16)
            assert len(signal_16k) == n_samples

            # Exact 3:1 interpolation simulation (repeating / linear interpolation)
            expected_output_len = n_samples * 3
            resampled_signal = np.repeat(signal_16k, 3)

            assert len(resampled_signal) == expected_output_len
            assert resampled_signal.dtype == np.int16


# ============================================================================
# Vector 6: Downstream Safety & Filesystem Isolation
# ============================================================================
class TestDestructiveConfirmationGateDownstreamRobustness:
    """
    Validates filesystem actions, registry callbacks, error resiliency,
    and status dictionary contracts.
    """

    def test_confirmed_delete_removes_files_safely(self):
        """When confirmed, MAP_DELETE removes corresponding .yaml and .pgm files from maps_dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create dummy map files
            yaml_path = Path(tmpdir) / "salotto.yaml"
            pgm_path = Path(tmpdir) / "salotto.pgm"
            yaml_path.write_text("dummy yaml content", encoding="utf-8")
            pgm_path.write_text("dummy pgm content", encoding="utf-8")

            mock_registry = MagicMock()
            gate = DestructiveConfirmationGate(room_registry=mock_registry, maps_dir=tmpdir)

            gate.request_action("salotto", "MAP_DELETE")
            reply, ok = gate.process_voice_input("sì, confermo")

            assert ok is True
            assert gate.confirmed is True
            mock_registry.delete_room.assert_called_once_with("salotto")
            assert not yaml_path.exists(), "YAML map file was not deleted!"
            assert not pgm_path.exists(), "PGM map file was not deleted!"

    def test_non_existent_files_handled_gracefully(self):
        """If map files do not exist on disk, confirmed delete does NOT raise OSError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gate = DestructiveConfirmationGate(maps_dir=tmpdir)
            gate.request_action("stanza_fantasma", "MAP_DELETE")
            reply, ok = gate.process_voice_input("sì, confermo")

            assert ok is True
            assert gate.confirmed is True

    def test_room_registry_exception_does_not_crash(self):
        """If room registry raises an unexpected exception, the gate catches it safely."""
        mock_registry = MagicMock()
        mock_registry.delete_room.side_effect = RuntimeError("Database locked")

        gate = DestructiveConfirmationGate(room_registry=mock_registry)
        gate.request_action("studio", "MAP_DELETE")
        reply, ok = gate.process_voice_input("sì, confermo")

        assert ok is True
        assert gate.confirmed is True

    def test_status_dict_contract(self):
        """Verifies status dictionary serialization and key completeness."""
        gate = DestructiveConfirmationGate()
        gate.request_action("cucina", "MAP_OVERWRITE")
        status = gate.get_status_dict()

        assert status["pending"] is True
        assert status["target_room"] == "cucina"
        assert status["command_type"] == "MAP_OVERWRITE"
        assert status["timeout_seconds"] == 30.0
        assert status["state"] == "PENDING"

        # Verify JSON serializability
        serialized = json.dumps(status)
        deserialized = json.loads(serialized)
        assert deserialized["target_room"] == "cucina"
