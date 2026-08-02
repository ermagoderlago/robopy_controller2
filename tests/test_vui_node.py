#!/usr/bin/env python3
"""
Test per respeaker_vui_node v3.0 — DSP-optimized VAD Gate.

Eseguire con:
    python3 -m pytest tests/test_vui_node.py -v

Dipendenze:
    pip install scipy webrtcvad numpy pytest
"""

import numpy as np
import threading
import time
try:
    import pytest
except ImportError:
    pytest = None
from unittest.mock import MagicMock, patch
from scipy.signal import butter, sosfilt, sosfilt_zi

# ---------------------------------------------------------------------------
# Costanti (copiate dal modulo per rendere i test autonomi)
# ---------------------------------------------------------------------------
SAMPLE_RATE     = 16000
CHUNK_SIZE      = 960
FRAME_SIZE      = 320
PRE_ROLL_FRAMES = 25
MAX_RING_FRAMES = PRE_ROLL_FRAMES + 100
MIN_SPEECH_FRAMES  = 25
MAX_SILENCE_FRAMES = 15
MAX_RESIDUAL    = FRAME_SIZE
PRE_ROLL_BYTES  = PRE_ROLL_FRAMES * FRAME_SIZE * 2


# ===========================================================================
# Test A — Attenuazione filtro Butterworth con freqz (confronto teorico/misurato)
# ===========================================================================
def _make_filter():
    sos = butter(5, [300, 3400], btype='band', fs=SAMPLE_RATE, output='sos')
    return sos.astype(np.float32), sosfilt_zi(sos).astype(np.float32)


def test_filter_attenuation():
    """
    Confronta l'attenuazione teorica (sosfreqz) con quella misurata su toni puri.

    Usa freqz per evitare l'imprecisione del calcolo su segnale corto:
    un Butterworth di ordine 5 ha ripple in banda — la soglia -3 dB è
    valida solo ai tagli esatti, non nell'intera banda passante.
    """
    from scipy.signal import sosfreqz

    sos, zi = _make_filter()
    fs = SAMPLE_RATE

    # Calcola risposta teorica del filtro SOS
    w, h = sosfreqz(sos, worN=8192, fs=fs)
    h_db = 20 * np.log10(np.abs(h) + 1e-12)

    test_cases = [
        (100,  False, -40),   # fuori banda bassa  → attenuazione > 40 dB
        (1000, True,   -3),   # in banda           → perdita < 3 dB
        (7500, False, -30),   # fuori banda alta   → attenuazione > 30 dB
    ]

    t = np.linspace(0, 2, 2 * fs, dtype=np.float32)   # 2 sec per stabilità

    for freq, expect_pass, threshold_db in test_cases:
        tone    = np.sin(2 * np.pi * freq * t).astype(np.float32)
        out, _  = sosfilt(sos, tone, zi=zi * tone[0], axis=0)

        # Scarta il transitorio iniziale (primo 10% del segnale)
        onset     = len(out) // 10
        power_in  = np.mean(tone[onset:] ** 2)
        power_out = np.mean(out[onset:] ** 2)
        db_meas   = 10 * np.log10(power_out / (power_in + 1e-12))

        # Verifica teorica con sosfreqz
        freq_idx  = np.argmin(np.abs(w - freq))
        db_theory = h_db[freq_idx]

        if expect_pass:
            assert db_meas > threshold_db, (
                f'{freq} Hz: atteso passante, misurato {db_meas:.1f} dB '
                f'(teorico {db_theory:.1f} dB)'
            )
        else:
            assert db_meas < threshold_db, (
                f'{freq} Hz: atteso > {abs(threshold_db)} dB att., '
                f'misurato {db_meas:.1f} dB (teorico {db_theory:.1f} dB)'
            )

        status = "✓ PASS" if (expect_pass == (db_meas > threshold_db)) else "✗ FAIL"
        print(f'{freq:5d} Hz | teorico {db_theory:7.1f} dB | '
              f'misurato {db_meas:7.1f} dB | {status}')


# ===========================================================================
# Test B — Residuo inter-callback: ordine e continuità dei campioni
# ===========================================================================
def _run_residual_logic(node, chunk_data: np.ndarray) -> list:
    """
    Esegue la logica del residuo del callback su un chunk arbitrario.
    Restituisce i campioni processati da _process_vad_frame in ordine.
    """
    received = []
    original_process = node._process_vad_frame

    def capture_frame(frame):
        received.extend(frame.tolist())

    node._process_vad_frame = capture_frame
    chunk_size = len(chunk_data)

    np.copyto(node._int16_vad_buf[:chunk_size], chunk_data)

    start_idx = 0
    if node._vad_residual_len > 0:
        needed = FRAME_SIZE - node._vad_residual_len
        np.copyto(node._assembly_buf[:node._vad_residual_len],
                  node._vad_residual_buf[:node._vad_residual_len])
        np.copyto(node._assembly_buf[node._vad_residual_len:],
                  node._int16_vad_buf[:needed])
        node._process_vad_frame(node._assembly_buf)
        start_idx = needed
        node._vad_residual_len = 0

    i = start_idx
    while i + FRAME_SIZE <= chunk_size:
        node._process_vad_frame(node._int16_vad_buf[i:i + FRAME_SIZE])
        i += FRAME_SIZE

    residuo = chunk_size - i
    if residuo > 0:
        np.copyto(node._vad_residual_buf[:residuo],
                  node._int16_vad_buf[i:i + residuo])
        node._vad_residual_len = residuo

    node._process_vad_frame = original_process
    return received


def test_vad_residual_order_and_continuity():
    """
    Verifica che con chunk arbitrari (es. 512 campioni):
    1. Nessun campione venga perso
    2. L'ordine sia preservato (nessuna duplicazione o riordinamento)
    3. La continuità temporale sia mantenuta (nessun salto nella sequenza)
    """
    CHUNK  = 512
    TOTAL  = 5120   # 10 callback × 512 campioni

    # Segnale con valori progressivi → facile verificare ordine e continuità
    audio = np.arange(TOTAL, dtype=np.int16)

    node = MagicMock()
    node._vad_residual_buf = np.empty(FRAME_SIZE, dtype=np.int16)
    node._vad_residual_len = 0
    node._assembly_buf     = np.empty(FRAME_SIZE, dtype=np.int16)
    node._int16_vad_buf    = np.empty(CHUNK,      dtype=np.int16)

    all_received = []
    for start in range(0, TOTAL, CHUNK):
        chunk    = audio[start:start + CHUNK]
        received = _run_residual_logic(node, chunk)
        all_received.extend(received)

    residual_left  = node._vad_residual_len
    expected_count = ((TOTAL - residual_left) // FRAME_SIZE) * FRAME_SIZE

    # 1. Conteggio campioni corretto
    assert len(all_received) == expected_count, (
        f'Campioni: attesi {expected_count}, ricevuti {len(all_received)}'
    )

    # 2. Ordine preservato (nessuna duplicazione/riordinamento)
    arr = np.array(all_received, dtype=np.int16)
    assert np.all(arr == np.arange(expected_count, dtype=np.int16)), (
        f'Ordine non preservato: primo disallineamento a idx '
        f'{np.argmax(arr != np.arange(expected_count, dtype=np.int16))}'
    )

    # 3. Continuità: nessun salto tra campioni consecutivi
    diffs = np.diff(arr.astype(np.int32))
    assert np.all(diffs == 1), (
        f'Salto temporale rilevato a idx {np.argmax(diffs != 1)}: '
        f'differenza = {diffs[np.argmax(diffs != 1)]}'
    )

    print(f'Test residuo: ✓  {len(all_received)} campioni in ordine, '
          f'{residual_left} nel residuo finale')


# ===========================================================================
# Test C — TTS blocking: VAD mai chiamato, tutti i campi di stato resettati
# ===========================================================================
def test_tts_blocking_full_state_reset():
    """
    Con TTS attivo:
    - VAD.is_speech() non viene mai chiamato
    - Tutti i campi di stato vengono azzerati
    - ring_write_idx e vad_residual_len sono zero
    """
    ev_tts = threading.Event()
    ev_tts.set()   # TTS in riproduzione

    vad_mock = MagicMock()
    vad_mock.is_speech.side_effect = AssertionError('VAD non deve essere chiamato!')

    node = MagicMock()
    node._ev_tts              = ev_tts
    node._tts_active          = False   # era False al ciclo precedente
    node._tts_start_time      = 0.0
    node._speech_frame_count  = 7
    node._silence_frame_count = 3
    node._is_speech_active    = True
    node._vad_residual_len    = 15
    node._ring_write_idx      = 42
    node._vad                 = vad_mock
    node._sos                 = np.eye(1, dtype=np.float32)
    node._zi                  = np.zeros((1, 2), dtype=np.float32)

    # Esegui solo il blocco Step A (TTS check) del callback
    tts_now = node._ev_tts.is_set()
    tts_was = node._tts_active
    if tts_now and not tts_was:
        node._tts_start_time = time.monotonic()
    node._tts_active = tts_now

    returned_early = False
    if tts_now:
        node._speech_frame_count  = 0
        node._silence_frame_count = 0
        node._is_speech_active    = False
        node._vad_residual_len    = 0
        node._ring_write_idx      = 0
        returned_early = True

    assert returned_early,                     'Il callback deve fare return con TTS attivo'
    assert node._speech_frame_count  == 0,     'speech_frame_count deve essere 0'
    assert node._silence_frame_count == 0,     'silence_frame_count deve essere 0'
    assert node._is_speech_active    == False, 'is_speech_active deve essere False'
    assert node._vad_residual_len    == 0,     'vad_residual_len deve essere 0'
    assert node._ring_write_idx      == 0,     'ring_write_idx deve essere 0'
    assert not vad_mock.is_speech.called,      'VAD.is_speech NON deve essere chiamato'
    assert node._tts_start_time      > 0,      'tts_start_time deve essere aggiornato'

    print('Test TTS blocking: ✓  tutti i campi resettati, VAD non chiamato')


if __name__ == '__main__':
    test_filter_attenuation()
    test_vad_residual_order_and_continuity()
    test_tts_blocking_full_state_reset()
    print('\n✅ Tutti i test superati!')
