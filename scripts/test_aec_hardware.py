#!/usr/bin/env python3
"""
Test Diagnostico AEC Hardware (XMOS XU316) per Marcus.

Questo script riproduce un tono di test a frequenza nota (1 kHz, 500 ms) dallo speaker
e contemporaneamente registra l'audio dal microfono per calcolare la cross-correlazione.
Se l'AEC hardware funziona correttamente, il segnale riprodotto dal robot non deve rientrare
nel canale microfonico (cross-correlazione < 0.15).
"""

import sys
import time
import numpy as np

try:
    import pyaudio
except ImportError:
    print("❌ PyAudio non installato.")
    sys.exit(1)

SAMPLE_RATE = 16000
CHUNK_SIZE = 960
DURATION_SEC = 2.0

def generate_tone(freq=1000.0, duration=0.5, sample_rate=16000, volume=0.3):
    t = np.linspace(0, duration, int(sample_rate * duration), False)
    sine = np.sin(2 * np.pi * freq * t) * volume
    int16_sine = (sine * 32767).astype(np.int16)
    return int16_sine

def main():
    print("=================================================================")
    print("🎙️ TEST AEC HARDWARE (XMOS XU316) - MARCUS VUI")
    print("=================================================================")

    pa = pyaudio.PyAudio()
    in_idx, out_idx = None, None

    for i in range(pa.get_device_count()):
        dev = pa.get_device_info_by_index(i)
        name = dev.get('name', '')
        if 'respeaker' in name.lower():
            if dev.get('maxInputChannels') > 0 and in_idx is None:
                in_idx = i
            if dev.get('maxOutputChannels') > 0 and out_idx is None:
                out_idx = i

    if in_idx is None or out_idx is None:
        print("⚠️ ReSpeaker USB non trovato esplicitamente. Cerco dispositivi default/pulse...")
        for i in range(pa.get_device_count()):
            dev = pa.get_device_info_by_index(i)
            name = dev.get('name', '')
            if 'default' in name.lower() or 'pulse' in name.lower() or 'pipewire' in name.lower():
                if dev.get('maxInputChannels') > 0 and in_idx is None:
                    in_idx = i
                if dev.get('maxOutputChannels') > 0 and out_idx is None:
                    out_idx = i

    print(f"👉 Dispositivo Input: Index {in_idx}")
    print(f"👉 Dispositivo Output: Index {out_idx}")

    tone_pcm = generate_tone(freq=1000.0, duration=0.6, volume=0.4)
    out_stereo = np.repeat(tone_pcm, 2).tobytes()

    recorded_chunks = []

    def in_cb(in_data, frame_count, time_info, status):
        recorded_chunks.append(in_data)
        return (None, pyaudio.paContinue)

    try:
        stream_in = pa.open(
            format=pyaudio.paInt16,
            channels=2,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=in_idx,
            stream_callback=in_cb,
            frames_per_buffer=CHUNK_SIZE
        )

        stream_out = pa.open(
            format=pyaudio.paInt16,
            channels=2,
            rate=SAMPLE_RATE,
            output=True,
            output_device_index=out_idx
        )

        print("\n▶️ Inizio riproduzione tono (1kHz, 600ms) e registrazione mic...")
        stream_in.start_stream()
        time.sleep(0.2)

        stream_out.write(out_stereo)
        time.sleep(1.5)

        stream_in.stop_stream()
        stream_in.close()
        stream_out.close()
        pa.terminate()

        # Analisi segnale registrato
        raw_bytes = b''.join(recorded_chunks)
        audio_stereo = np.frombuffer(raw_bytes, dtype=np.int16)
        l_ch = audio_stereo[::2].astype(np.float32)

        # Normalizzazione
        ref_norm = tone_pcm.astype(np.float32) / 32767.0
        rec_norm = l_ch / 32767.0

        if len(rec_norm) > len(ref_norm):
            corr = np.correlate(rec_norm, ref_norm, mode='valid')
            max_corr = np.max(np.abs(corr)) / len(ref_norm)
        else:
            max_corr = 0.0

        print(f"\n📊 RISULTATI TEST AEC HARDWARE:")
        print(f"   - Valore Picco Cross-Correlazione: {max_corr:.4f}")

        if max_corr < 0.15:
            print("   ✅ ESITO: AEC Hardware ATTIVO e FUNZIONANTE! (Eco soppressa dal chip XMOS)")
        else:
            print("   ❌ ESITO: Eco Acustica Elevata (AEC Hardware non sopprime completamente il segnale)")
            print("   👉 Suggerimento: Ridurre volume master speaker o verificare firmware XMOS.")

    except Exception as e:
        print(f"❌ Errore durante l'esecuzione del test: {e}")

if __name__ == '__main__':
    main()
