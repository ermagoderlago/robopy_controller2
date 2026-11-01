import sys
import os
import numpy as np
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.robot_ai.services.audio_buffer_manager import AudioBufferManager


def test_audio_buffer_manager_lifecycle():
    mgr = AudioBufferManager(max_mic_chunks=10, max_speaker_chunks=10)
    
    # 1. Zero clipping verification on int16 chunks
    chunk_16k = (np.sin(np.linspace(0, 10, 960)) * 32000).astype(np.int16).tobytes()
    pushed = mgr.push_mic_chunk(chunk_16k)
    assert pushed is True
    
    # Verify FIFO retrieval
    popped = mgr.pop_mic_chunk()
    assert popped == chunk_16k
    assert mgr.pop_mic_chunk() is None


def test_audio_buffer_manager_echo_suppression_and_barge_in():
    barge_in_fired = []

    def on_barge_in():
        barge_in_fired.append(True)

    mgr = AudioBufferManager(max_mic_chunks=10, max_speaker_chunks=10, barge_in_energy_thresh=0.20)
    mgr.set_barge_in_callback(on_barge_in)
    
    # Set speaker playing
    mgr.set_speaker_playing(True)
    assert mgr.is_speaker_playing() is True

    # Low-energy mic chunk (echo) should be dropped
    low_energy_chunk = (np.random.normal(0, 500, 960)).astype(np.int16).tobytes()
    pushed = mgr.push_mic_chunk(low_energy_chunk)
    assert pushed is False
    assert mgr.pop_mic_chunk() is None

    # High-energy mic chunks (user speaking over speaker -> barge-in)
    loud_speech_chunk = (np.sin(np.linspace(0, 50, 960)) * 28000).astype(np.int16).tobytes()
    mgr.push_mic_chunk(loud_speech_chunk)
    mgr.push_mic_chunk(loud_speech_chunk)

    assert len(barge_in_fired) > 0, "Barge-in callback must be invoked on sustained loud voice"
    assert mgr.is_speaker_playing() is False


def test_audio_buffer_clear_and_metrics():
    mgr = AudioBufferManager(max_mic_chunks=5, max_speaker_chunks=5)
    
    for _ in range(5):
        mgr.push_mic_chunk(b"\x00\x00" * 480)
        mgr.push_speaker_chunk(b"\x00\x00" * 480)

    metrics = mgr.get_metrics()
    assert metrics["mic_queued"] == 5
    assert metrics["speaker_queued"] == 5

    mgr.clear_mic_buffer()
    mgr.clear_speaker_buffer()

    metrics = mgr.get_metrics()
    assert metrics["mic_queued"] == 0
    assert metrics["speaker_queued"] == 0
