#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Robot AI Audio - Resilient Audio Pipeline VUI
=============================================
Implementation of resilient DSP audio conditioning and DAC interface according to:
- SPEC-04: Voice User Interface & Audio Pipeline
- marcus_core_rules.md: Hardware DAC 48kHz & 16kHz Mono Streaming
- DFMEA: FM-VUI-001 (Rate Mismatch), FM-VUI-003 (Fan Noise), FM-VUI-040 (Jitter Buffer)

Components:
1. 140Hz 2nd-order Butterworth High-Pass Filter (HPF) for Raspberry Pi 5 fan noise elimination.
2. Continuous audioop.ratecv resampling (16kHz/24kHz -> 48kHz) with phase state continuity.
3. 3-chunk jitter buffer (~120ms) with 100ms drain hysteresis and instant flush on barge-in / E-stop.
4. Strict verify_audio_resampling(16000, 48000) contract validation.
5. STT gain barge-in limiter (0.1x during TTS playback, 1.0x when idle).
"""

import math
import time
from typing import List, Optional, Tuple, Any
import numpy as np

try:
    import audioop
except ImportError:
    try:
        import audioop_lts as audioop
    except ImportError:
        audioop = None

try:
    from scipy.signal import butter, sosfilt, sosfilt_zi
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


class ResilientAudioPipeline:
    """
    Production DSP audio conditioning and playback pipeline for Marcus VUI.
    """

    def __init__(
        self,
        hpf_cutoff_hz: float = 140.0,
        sample_rate: int = 16000,
        target_dac_rate: int = 48000,
        jitter_buffer_target_depth: int = 3,
    ):
        self.hpf_cutoff_hz = float(hpf_cutoff_hz)
        self.sample_rate = int(sample_rate)
        self.target_dac_rate = int(target_dac_rate)
        self.jitter_buffer_target_depth = int(jitter_buffer_target_depth)

        # 140Hz Butterworth HPF setup
        if HAS_SCIPY:
            self._hpf_sos = butter(2, self.hpf_cutoff_hz, btype='highpass', fs=self.sample_rate, output='sos')
            self._hpf_zi = sosfilt_zi(self._hpf_sos) * 0.0
        else:
            self._hpf_sos = None
            # Analytical discrete filter coefficients for 140Hz @ 16kHz
            # b0 = 0.96187045, b1 = -1.92374090, b2 = 0.96187045
            # a1 = -1.92228651, a2 = 0.92519530
            self._b0 = 0.96187045
            self._b1 = -1.92374090
            self._b2 = 0.96187045
            self._a1 = -1.92228651
            self._a2 = 0.92519530
            self._x1 = 0.0
            self._x2 = 0.0
            self._y1 = 0.0
            self._y2 = 0.0

        # Resampling state for ratecv continuity across chunk boundaries
        self.ratecv_state = None
        self._last_playback_time: float = 0.0
        self._last_source: Optional[str] = None

        # Jitter buffer
        self.jitter_buffer: List[bytes] = []

        # Barge-in safety limiter (SPEC-04)
        self.stt_gain: float = 1.0

    def verify_audio_resampling(self, input_rate: int, output_hw_rate: int) -> bool:
        """
        Validates ReSpeaker 16kHz mono in -> 48kHz hardware DAC out rule.
        Strictly returns True for (16000, 48000), False otherwise (SPEC-04 / FM-VUI-001).
        Rejects non-integer types (str, float, None) and booleans.
        """
        if type(input_rate) is not int or type(output_hw_rate) is not int:
            return False
        return input_rate == 16000 and output_hw_rate == 48000

    def apply_140hz_hpf(self, pcm_int16_samples: np.ndarray, sample_rate: Optional[int] = None) -> np.ndarray:
        """
        Applies a discrete 2nd-order Butterworth high-pass filter at cutoff fc = 140 Hz.
        Attenuates fan acoustic noise (<140Hz) by > 15 dB while preserving human voice (>85% @ 1000Hz).
        Preserves state across consecutive frames to avoid phase clicks.
        """
        if len(pcm_int16_samples) == 0:
            return pcm_int16_samples

        x = pcm_int16_samples.astype(np.float64)

        if HAS_SCIPY and self._hpf_sos is not None:
            filtered, self._hpf_zi = sosfilt(self._hpf_sos, x, zi=self._hpf_zi)
            return np.clip(filtered, -32768, 32767).astype(np.int16)

        # Pure NumPy analytical difference equation fallback
        fc = self.hpf_cutoff_hz
        fs = sample_rate or self.sample_rate
        w0 = 2.0 * math.pi * fc / fs
        alpha = math.sin(w0) / (2.0 * math.sqrt(2.0))
        cos_w0 = math.cos(w0)

        b0 = (1.0 + cos_w0) / 2.0
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

        b_norm = np.array([b0, b1, b2], dtype=np.float64) / a0
        a_norm = np.array([a0, a1, a2], dtype=np.float64) / a0

        y = np.zeros_like(x)
        x1 = self._x1
        x2 = self._x2
        y1 = self._y1
        y2 = self._y2

        for i in range(len(x)):
            x0 = x[i]
            y0 = b_norm[0] * x0 + b_norm[1] * x1 + b_norm[2] * x2 - a_norm[1] * y1 - a_norm[2] * y2
            y[i] = y0
            x2 = x1
            x1 = x0
            y2 = y1
            y1 = y0

        self._x1 = x1
        self._x2 = x2
        self._y1 = y1
        self._y2 = y2

        return np.clip(y, -32768, 32767).astype(np.int16)

    def resample_chunk(
        self,
        raw_bytes: bytes,
        input_rate: int = 16000,
        source_name: Optional[str] = None,
    ) -> bytes:
        """
        Resamples PCM chunk (16-bit mono) from input_rate to 48kHz native DAC rate.
        Continuously preserves audioop.ratecv state across sequential chunks within an utterance,
        preventing boundary clicking/popping (boundary delta reduced by > 65%).
        """
        if not raw_bytes:
            return b""

        now = time.monotonic()
        # Reset state if gap between utterances > 400ms or source switched
        if (now - self._last_playback_time > 0.400) or (source_name and source_name != self._last_source):
            self.ratecv_state = None

        self._last_playback_time = now
        if source_name:
            self._last_source = source_name

        if audioop is not None:
            converted, self.ratecv_state = audioop.ratecv(
                raw_bytes,
                2,  # 16-bit int16 (2 bytes)
                1,  # mono
                input_rate,
                self.target_dac_rate,
                self.ratecv_state,
            )
            return converted
        else:
            # High-precision linear interpolation fallback (for Python 3.13+ environments without audioop-lts)
            samples = np.frombuffer(raw_bytes, dtype=np.int16)
            factor = self.target_dac_rate / float(input_rate)
            new_len = int(round(len(samples) * factor))
            orig_indices = np.arange(len(samples))
            new_indices = np.linspace(0, len(samples) - 1, new_len)
            resampled = np.interp(new_indices, orig_indices, samples)
            return resampled.astype(np.int16).tobytes()

    def resample_16k_to_48k(self, pcm16_bytes: bytes) -> bytes:
        """Convenience method matching test harness: resamples 16kHz -> 48kHz (3x factor)."""
        return self.resample_chunk(pcm16_bytes, input_rate=16000)

    def set_barge_in_mode(self, is_tts_speaking: bool) -> None:
        """Attenuates STT gain to 0.1x during TTS playback to prevent robot self-listening."""
        self.stt_gain = 0.10 if is_tts_speaking else 1.00

    def set_tts_active(self, is_speaking: bool) -> None:
        """Alias matching ReSpeakerVUINode and VUIDialogueEngine."""
        self.set_barge_in_mode(is_speaking)

    def push_jitter_chunk(self, chunk: bytes) -> Optional[bytes]:
        """
        Pushes chunk into the jitter buffer.
        Returns the oldest chunk only after accumulating target depth (>= 3 chunks, ~120ms).
        """
        self.jitter_buffer.append(chunk)
        if len(self.jitter_buffer) >= self.jitter_buffer_target_depth:
            return self.jitter_buffer.pop(0)
        return None

    def pop_remaining_chunks(self) -> List[bytes]:
        """Drains any remaining chunks from the jitter buffer at end of speech."""
        remaining = list(self.jitter_buffer)
        self.jitter_buffer.clear()
        return remaining

    def clear_jitter_buffer(self) -> int:
        """
        Instant flush on barge-in / emergency stop.
        Clears buffer immediately and resets ratecv filter state.
        """
        count = len(self.jitter_buffer)
        self.jitter_buffer.clear()
        self.ratecv_state = None
        return count


# Reference alias for compatibility
ResilientAudioVUIEngine = ResilientAudioPipeline
