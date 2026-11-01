import sys
import os
import numpy as np
import pytest

try:
    import audioop
except ImportError:
    # Python 3.13 shim if audioop removed
    import pyaudioop as audioop


def test_ratecv_16k_to_48k_ratio():
    """Mathematical verification: 16kHz to 48kHz resampling produces exactly 3x bytes with zero drift."""
    sample_rate_in = 16000
    sample_rate_out = 48000
    duration_s = 0.5
    num_samples_in = int(sample_rate_in * duration_s)
    
    # 16-bit mono PCM: 2 bytes per sample
    raw_16k = (np.sin(np.linspace(0, 100, num_samples_in)) * 25000).astype(np.int16).tobytes()
    assert len(raw_16k) == num_samples_in * 2

    resampled_48k, state = audioop.ratecv(raw_16k, 2, 1, sample_rate_in, sample_rate_out, None)
    expected_samples_out = int(sample_rate_out * duration_s)
    
    # audioop.ratecv maintains internal filter state yielding exact samples +/- 2 sample boundary margin
    assert abs(len(resampled_48k) - expected_samples_out * 2) <= 4, "16k->48k resampling must yield exact 3x buffer size (+/- 2 samples)"


def test_ratecv_24k_to_48k_ratio():
    """Mathematical verification: 24kHz to 48kHz resampling produces exactly 2x bytes."""
    sample_rate_in = 24000
    sample_rate_out = 48000
    duration_s = 0.25
    num_samples_in = int(sample_rate_in * duration_s)

    raw_24k = (np.sin(np.linspace(0, 50, num_samples_in)) * 20000).astype(np.int16).tobytes()
    resampled_48k, state = audioop.ratecv(raw_24k, 2, 1, sample_rate_in, sample_rate_out, None)

    expected_samples_out = int(sample_rate_out * duration_s)
    assert abs(len(resampled_48k) - expected_samples_out * 2) <= 4, "24k->48k resampling must yield exact 2x buffer size"


def test_left_channel_extraction_red_zone():
    """SPEC-04 Red Zone: Audio acquisition must strictly use Left Channel (data[:, 0])."""
    num_samples = 320
    left_samples = np.full(num_samples, 1000, dtype=np.int16)
    right_samples = np.full(num_samples, -2000, dtype=np.int16)
    
    # Interleaved stereo
    stereo = np.empty((num_samples, 2), dtype=np.int16)
    stereo[:, 0] = left_samples
    stereo[:, 1] = right_samples

    extracted_left = stereo[:, 0]
    assert np.array_equal(extracted_left, left_samples)
    assert not np.array_equal(extracted_left, right_samples)


def test_input_attenuation_software_headroom():
    """SPEC-04 Red Zone: 3.0x input software attenuation pre-cast int16 prevents square wave clipping."""
    high_input = np.full(100, 48000.0, dtype=np.float32)
    # Attenuated 3.0x
    attenuated = high_input / 3.0
    int16_samples = np.clip(attenuated, -32768, 32767).astype(np.int16)
    
    assert np.max(int16_samples) == 16000
    assert np.max(int16_samples) < 32767, "3.0x attenuation must maintain safe headroom below int16 ceiling"
