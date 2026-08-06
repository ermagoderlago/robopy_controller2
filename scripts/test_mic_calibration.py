#!/usr/bin/env python3
"""
Script di Calibrazione, Diagnostica e Riproduzione Microfonica per ReSpeaker Lite
=================================================================================
Misura il profilo del rumore di fondo (silenzio), cattura il segnale vocale,
applica il guadagno ed il filtro del nodo VUI, registra il file WAV e riemette 
l'audio dagli altoparlanti per consentire la valutazione diretta dell'utente.

Uso:
    python3 scripts/test_mic_calibration.py
"""

import time
import wave
import os
import numpy as np
import pyaudio

SAMPLE_RATE = 16000
CHUNK_SIZE  = 960  # 60 ms @ 16 kHz
CHANNELS    = 2    # Stereo ReSpeaker (Left = AEC Filtered, Right = Raw)
STT_GAIN    = 2.5  # Guadagno di default applicato in respeaker_vui_node
WAV_PATH    = "/tmp/test_mic_recording.wav"

def main():
    print("=" * 65)
    print("🎙️ TOOL DI DIAGNOSTICA, CALIBRAZIONE E RIPRODUZIONE AUDIO MARCUS VUI")
    print("=" * 65)
    
    pa = pyaudio.PyAudio()
    target_idx = None
    
    info = pa.get_host_api_info_by_index(0)
    for i in range(info.get('deviceCount')):
        dev = pa.get_device_info_by_host_api_device_index(0, i)
        name = dev.get('name', '')
        if any(k in name.lower() for k in ['respeaker', 'lite', 'array']) and dev.get('maxInputChannels') > 0:
            target_idx = i
            print(f"✅ Trovato dispositivo ReSpeaker: Index {i} ('{name}')")
            break
            
    if target_idx is None:
        print("⚠️ ReSpeaker non trovato esplicitamente per nome, uso del dispositivo di input di default.")
        
    try:
        stream_in = pa.open(
            rate=SAMPLE_RATE,
            channels=CHANNELS,
            format=pyaudio.paInt16,
            input=True,
            input_device_index=target_idx,
            frames_per_buffer=CHUNK_SIZE
        )
    except Exception as e:
        print(f"❌ Impossibile aprire lo stream di input audio via PyAudio: {e}")
        print("💡 Procedo con la cattura diretta ALSA di fallback...")
        stream_in = None

    print("\n--- FASE 1: Registrazione Silenzio Ambientale (2 secondi) ---")
    print("Mani ferme, fai silenzio nella stanza...")
    time.sleep(1.0)
    print("🔴 REGISTRAZIONE SILENZIO IN CORSO...")
    
    silence_frames = []
    num_silence_chunks = int(2.0 * SAMPLE_RATE / CHUNK_SIZE)
    if stream_in:
        for _ in range(num_silence_chunks):
            data = stream_in.read(CHUNK_SIZE, exception_on_overflow=False)
            audio = np.frombuffer(data, dtype=np.int16)
            l_ch = audio[::2] # Canale sinistro AEC Hardware
            silence_frames.extend(l_ch)
    else:
        # Fallback arecord
        os.system(f"arecord -D plughw:0,0 -f S16_LE -r {SAMPLE_RATE} -c 2 -d 2 /tmp/silence.wav >/dev/null 2>&1")
        with wave.open("/tmp/silence.wav", "rb") as wf:
            raw_b = wf.readframes(wf.getnframes())
            arr_b = np.frombuffer(raw_b, dtype=np.int16)
            silence_frames.extend(arr_b[::2])
        
    silence_arr = np.array(silence_frames, dtype=np.float32)
    silence_rms = np.sqrt(np.mean(silence_arr ** 2)) if len(silence_arr) > 0 else 100.0
    silence_peak = np.max(np.abs(silence_arr)) if len(silence_arr) > 0 else 100.0
    
    print(f"✅ Silenzio completato: RMS Rumore = {silence_rms:.2f} | Picco Rumore = {silence_peak:.0f}")

    print("\n--- FASE 2: Registrazione Vocale (5 secondi) ---")
    print("Pronuncia chiaramente a voce normale: 'Marcus io sono Luca e questa è la mia voce'")
    time.sleep(1.0)
    print("🔴 REGISTRAZIONE VOCE IN CORSO... PARLA ORA!")
    
    raw_speech_int16 = []
    processed_speech_int16 = []
    num_speech_chunks = int(5.0 * SAMPLE_RATE / CHUNK_SIZE)
    
    if stream_in:
        for _ in range(num_speech_chunks):
            data = stream_in.read(CHUNK_SIZE, exception_on_overflow=False)
            audio = np.frombuffer(data, dtype=np.int16)
            l_ch = audio[::2]
            raw_speech_int16.extend(l_ch)
            
            processed_float = l_ch.astype(np.float32) * STT_GAIN
            processed_clipped = np.clip(processed_float, -32768, 32767).astype(np.int16)
            processed_speech_int16.extend(processed_clipped)
            
        stream_in.stop_stream()
        stream_in.close()
    else:
        # Fallback arecord
        os.system(f"arecord -D plughw:0,0 -f S16_LE -r {SAMPLE_RATE} -c 2 -d 5 /tmp/speech.wav >/dev/null 2>&1")
        with wave.open("/tmp/speech.wav", "rb") as wf:
            raw_b = wf.readframes(wf.getnframes())
            arr_b = np.frombuffer(raw_b, dtype=np.int16)
            l_ch = arr_b[::2]
            raw_speech_int16.extend(l_ch)
            processed_float = l_ch.astype(np.float32) * STT_GAIN
            processed_clipped = np.clip(processed_float, -32768, 32767).astype(np.int16)
            processed_speech_int16.extend(processed_clipped)

    raw_arr = np.array(raw_speech_int16, dtype=np.int16)
    proc_arr = np.array(processed_speech_int16, dtype=np.int16)
    
    speech_arr_f32 = raw_arr.astype(np.float32)
    speech_rms = np.sqrt(np.mean(speech_arr_f32 ** 2)) if len(speech_arr_f32) > 0 else 500.0
    speech_peak = np.max(np.abs(speech_arr_f32)) if len(speech_arr_f32) > 0 else 1000.0
    
    snr_db = 20 * np.log10((speech_rms + 1e-5) / (silence_rms + 1e-5))
    clipping_ratio = np.mean(np.abs(proc_arr) >= 32000) * 100.0 if len(proc_arr) > 0 else 0.0

    print("\n" + "=" * 65)
    print("📊 RISULTATI DELL'ANALISI AUDIO")
    print("=" * 65)
    print(f"  • RMS Rumore di Fondo (Silenzio): {silence_rms:7.2f}")
    print(f"  • RMS Segnale Vocale:            {speech_rms:7.2f}")
    print(f"  • Picco Massimo Registrato:      {speech_peak:7.0f} / 32767")
    print(f"  • Rapporto Segnale-Rumore (SNR): {snr_db:7.2f} dB")
    print(f"  • Clipping / Saturation:         {clipping_ratio:7.2f} % dei campioni")

    # Salvataggio WAV
    with wave.open(WAV_PATH, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(proc_arr.tobytes())
    print(f"💾 Audio registrato salvato su: {WAV_PATH}")

    # FASE 3: RIEMISSIONE AUDIO DALL'ALTOPARLANTE
    print("\n--- FASE 3: Riproduzione Audio Registrato dagli Altoparlanti ---")
    print("🔊 RIPRODUZIONE IN CORSO (Ascolta la chiarezza della tua voce)...")
    
    try:
        stream_out = pa.open(
            rate=SAMPLE_RATE,
            channels=1,
            format=pyaudio.paInt16,
            output=True
        )
        stream_out.write(proc_arr.tobytes())
        stream_out.stop_stream()
        stream_out.close()
        print("✅ Riproduzione completata con successo via PyAudio!")
    except Exception as e:
        print(f"ℹ️ Riproduzione PyAudio: {e}. Esecuzione via aplay...")
        res = os.system(f"aplay -D plughw:0,0 {WAV_PATH} >/dev/null 2>&1 || aplay {WAV_PATH} >/dev/null 2>&1")
        if res == 0:
            print("✅ Riproduzione completata con successo via aplay!")
        else:
            print(f"⚠️ Riascolta manualmente con: aplay {WAV_PATH}")
        
    pa.terminate()

    print("\n💡 VALUTAZIONE PARAMETRI VUI:")
    if clipping_ratio > 0.5:
        print("  ⚠️ RILEVATO CLIPPING VOCALE! Ridurre stt_gain in respeaker_vui_node.")
    elif snr_db < 10.0:
        print("  ⚠️ SNR BASSO (< 10 dB). Avvicinati al microfono o aumenta stt_gain.")
    else:
        print("  ✅ QUALITÀ AUDIO OTTIMALE! SNR buono e zero distorsioni.")
    print("=" * 65)

if __name__ == '__main__':
    main()

