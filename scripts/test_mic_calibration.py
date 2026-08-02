#!/usr/bin/env python3
"""
Script di Calibrazione e Diagnostica Microfonica per ReSpeaker Lite
===================================================================
Misura il profilo del rumore di fondo (silenzio) e il segnale vocale,
calcola il Signal-to-Noise Ratio (SNR), rileva eventuale clipping
e raccomanda i parametri ottimali per stt_gain e noise_gate_threshold.

Uso:
    python3 scripts/test_mic_calibration.py
"""

import time
import numpy as np
import pyaudio

SAMPLE_RATE = 16000
CHUNK_SIZE  = 960
CHANNELS    = 2

def main():
    print("=" * 65)
    print("🎙️ TOOL DI CALIBRAZIONE E DIAGNOSTICA AUDIO MARCUS VUI")
    print("=" * 65)
    
    pa = pyaudio.PyAudio()
    target_idx = None
    
    info = pa.get_host_api_info_by_index(0)
    for i in range(info.get('deviceCount')):
        dev = pa.get_device_info_by_host_api_device_index(0, i)
        name = dev.get('name', '')
        if 'respeaker' in name.lower() and dev.get('maxInputChannels') > 0:
            target_idx = i
            print(f"✅ Trovato dispositivo ReSpeaker: Index {i} ('{name}')")
            break
            
    if target_idx is None:
        print("⚠️ ReSpeaker non trovato esplicitamente, provo ad aprire il dispositivo default.")
        
    try:
        stream = pa.open(
            rate=SAMPLE_RATE,
            channels=CHANNELS,
            format=pyaudio.paInt16,
            input=True,
            input_device_index=target_idx,
            frames_per_buffer=CHUNK_SIZE
        )
    except Exception as e:
        print(f"❌ Impossibile aprire lo stream audio: {e}")
        return

    print("\n--- FASE 1: Registrazione Silenzio Ambientale (2 secondi) ---")
    print("Mani ferme, fai silenzio nella stanza...")
    time.sleep(1.0)
    print("🔴 REGISTRAZIONE SILENZIO IN CORSO...")
    
    silence_frames = []
    num_silence_chunks = int(2.0 * SAMPLE_RATE / CHUNK_SIZE)
    for _ in range(num_silence_chunks):
        data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        audio = np.frombuffer(data, dtype=np.int16)
        l_ch = audio[::2]
        silence_frames.extend(l_ch)
        
    silence_arr = np.array(silence_frames, dtype=np.float32)
    silence_rms = np.sqrt(np.mean(silence_arr ** 2))
    silence_peak = np.max(np.abs(silence_arr))
    
    print(f"✅ Silenzio completato: RMS = {silence_rms:.2f} | Peak = {silence_peak:.0f}")

    print("\n--- FASE 2: Registrazione Parlato di Prova (3 secondi) ---")
    print("Pronuncia chiaramente a voce normale: 'Marcus, come stai?'")
    time.sleep(1.0)
    print("🔴 REGISTRAZIONE VOCE IN CORSO... PARLA ORA!")
    
    speech_frames = []
    num_speech_chunks = int(3.0 * SAMPLE_RATE / CHUNK_SIZE)
    for _ in range(num_speech_chunks):
        data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        audio = np.frombuffer(data, dtype=np.int16)
        l_ch = audio[::2]
        speech_frames.extend(l_ch)
        
    stream.stop_stream()
    stream.close()
    pa.terminate()
    
    speech_arr = np.array(speech_frames, dtype=np.float32)
    speech_rms = np.sqrt(np.mean(speech_arr ** 2))
    speech_peak = np.max(np.abs(speech_arr))
    
    snr_db = 20 * np.log10((speech_rms + 1e-5) / (silence_rms + 1e-5))
    clipping_ratio = np.mean(np.abs(speech_arr) >= 32000) * 100.0

    print("\n" + "=" * 65)
    print("📊 RISULTATI DELL'ANALISI AUDIO")
    print("=" * 65)
    print(f"  • RMS Rumore di Fondo (Silenzio): {silence_rms:7.2f}")
    print(f"  • RMS Segnale Vocale:            {speech_rms:7.2f}")
    print(f"  • Picco Massimo Registrato:      {speech_peak:7.0f} / 32767")
    print(f"  • Rapporto Segnale-Rumore (SNR): {snr_db:7.2f} dB")
    print(f"  • Clipping / Saturation:         {clipping_ratio:7.2f} % dei campioni")

    print("\n💡 RACCOMANDAZIONE PARAMETRI ROS 2:")
    if clipping_ratio > 0.5:
        print("  ⚠️ RILEVATO CLIPPING VOCALE! Il guadagno precedente (30x) era troppo alto.")
        rec_gain = 2.0
    else:
        rec_gain = min(4.0, max(1.5, 8000.0 / (speech_rms + 1e-5)))
        
    rec_gate = min(4500.0, max(800.0, silence_rms * rec_gain * 1.3 + 300.0))

    print(f"  👉 stt_gain consigliato:              {rec_gain:.2f} (default raccomandato 2.50)")
    print(f"  👉 noise_gate_threshold consigliato:  {rec_gate:.1f}")
    print("=" * 65)

if __name__ == '__main__':
    main()
