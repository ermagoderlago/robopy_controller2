"""
Test unitari per la mitigazione F3: VAD Energy-Gating su Vosk ASR.
Verifica il filtraggio a monte dei frame di silenzio per azzerare il carico CPU
del decoder Kaldi in idle, garantendo la preservazione del parlato e della wake word.
"""

import numpy as np
import pytest
from robopy_controller.robot_ai.services.local_asr_vosk import VoskASRManager


class MockVoskManager(VoskASRManager):
    """Sottoclasse mock che non carica il modello pesante Kaldi ma testa la logica di coda."""
    def __init__(self):
        super(VoskASRManager, self).__init__()
        import queue
        self._audio_queue = queue.Queue(maxsize=300)
        self._shutdown = False
        self._drop_count = 0
        self.energy_gating = True
        self._noise_floor_ema = 200.0
        self._hangover_chunks = 0
        self._max_hangover_chunks = 15
        self._min_rms_threshold = 150.0
        self._last_was_speech = False
        self.on_text_cb = None

    def is_active(self):
        return True


def _generate_sine_pcm(freq=440.0, duration_s=0.1, amplitude=3000, sample_rate=16000):
    """Genera un chunk PCM 16-bit sinusoidale con ampiezza nota."""
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    sine = amplitude * np.sin(2 * np.pi * freq * t)
    return sine.astype(np.int16).tobytes()


def _generate_silence_pcm(duration_s=0.1, noise_amp=50, sample_rate=16000):
    """Genera un chunk PCM con solo debole rumore di fondo."""
    noise = np.random.randint(-noise_amp, noise_amp + 1, int(sample_rate * duration_s), dtype=np.int16)
    return noise.tobytes()


def test_energy_gating_silence_rejection():
    """In silenzio puro (rumore basso), nessun frame deve entrare nella coda Vosk."""
    mgr = MockVoskManager()
    mgr.energy_gating = True
    mgr._noise_floor_ema = 200.0

    silence_chunk = _generate_silence_pcm(noise_amp=40)
    for _ in range(20):
        mgr.process_audio(silence_chunk)

    assert mgr._audio_queue.qsize() == 0, "I frame di silenzio puro devono essere scartati prima di Kaldi!"


def test_energy_gating_speech_admission():
    """Con parlato attivo (RMS elevato), i frame devono essere accodati a Kaldi."""
    mgr = MockVoskManager()
    mgr.energy_gating = True

    speech_chunk = _generate_sine_pcm(freq=300.0, amplitude=4000)
    for _ in range(5):
        mgr.process_audio(speech_chunk)

    assert mgr._audio_queue.qsize() == 5, "I frame con energia vocale devono essere accodati per la decodifica!"
    assert mgr._hangover_chunks == 15, "L'hangover counter deve essere resettato a 15 sul parlato."


def test_energy_gating_hangover_preservation():
    """Al termine del parlato, i 15 chunk di hangover devono essere preservati per non tagliare le code fonetiche."""
    mgr = MockVoskManager()
    mgr.energy_gating = True

    # 1 frame di parlato
    speech_chunk = _generate_sine_pcm(freq=300.0, amplitude=5000)
    mgr.process_audio(speech_chunk)
    assert mgr._audio_queue.qsize() == 1

    # Seguito da 10 frame di silenzio: devono comunque essere passati grazie all'hangover
    silence_chunk = _generate_silence_pcm(noise_amp=30)
    for _ in range(10):
        mgr.process_audio(silence_chunk)

    # 1 frame di speech + 10 frame di hangover = 11 frame totali
    assert mgr._audio_queue.qsize() == 11, "I frame di hangover devono essere preservati."
    assert mgr._hangover_chunks == 5, "L'hangover counter deve scalare gradualmente verso lo zero."


def test_disabled_energy_gating():
    """Se il gating energetico è disabilitato, tutti i frame (incluso silenzio) passano."""
    mgr = MockVoskManager()
    mgr.set_energy_gating(False)

    silence_chunk = _generate_silence_pcm(noise_amp=20)
    for _ in range(8):
        mgr.process_audio(silence_chunk)

    assert mgr._audio_queue.qsize() == 8, "Con gating disabilitato, tutti i frame devono passare."


def test_force_flush():
    """force_flush deve inserire b'FLUSH_CMD' in modo thread-safe."""
    mgr = MockVoskManager()
    mgr.force_flush()
    assert mgr._audio_queue.qsize() == 1
    assert mgr._audio_queue.get_nowait() == b"FLUSH_CMD"
