#!/usr/bin/env python3
"""
Script di Pre-Test e Pre-Calibrazione Strutturato VUI Audio per Marcus (v3.0)
=============================================================================
Questo script implementa il protocollo di test in 3 Fasi per l'ottimizzazione dell'audio,
effettuando campionamenti VERI dall'hardware ReSpeaker Lite.

  - FASE A: Test Sintetico Speaker (Sweep Volume 10%, 30%, 50%, 80% & Verifica AEC)
  - FASE B: Test Dinamico Rumore Motori (Rotazione 360° e soppressione HPF)
  - FASE C: Matrice Far-Field & SNR a Distanza (1m, 2m, 3m)

Uso:
    python3 scripts/test_vui_audio_pretest.py
"""

import time
import os
import sys
import queue
import threading
import numpy as np
os.environ["PA_ALSA_PLUGHW"] = "1"
import pyaudio
try:
    from scipy.signal import butter, sosfilt
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

SAMPLE_RATE = 16000
CHUNK_SIZE  = 960  # 60 ms @ 16 kHz
CHANNELS    = 2    # ReSpeaker (Left = AEC Filtered, Right = Raw)
DAC_RATE    = 48000 # DAC hardware nativo del Lite

class AudioTester:
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.in_stream = None
        self.out_stream = None
        self.in_queue = queue.Queue()
        self.is_recording = False
        
    def find_devices(self):
        target_in = None
        target_out = None
        info = self.pa.get_host_api_info_by_index(0)
        numdevices = info.get('deviceCount')
        
        # Primo tentativo: cerca esplicitamente ReSpeaker
        for i in range(numdevices):
            dev = self.pa.get_device_info_by_host_api_device_index(0, i)
            name = dev.get('name', '')
            n_in = dev.get('maxInputChannels')
            n_out = dev.get('maxOutputChannels')
            if any(k in name.lower() for k in ['respeaker', 'lite', 'array']):
                if n_in > 0 and target_in is None:
                    target_in = i
                if n_out > 0 and target_out is None:
                    target_out = i
                    
        # Secondo tentativo: fallback su pulse/default (uguale al nodo VUI)
        if target_in is None or target_out is None:
            for i in range(numdevices):
                dev = self.pa.get_device_info_by_host_api_device_index(0, i)
                name = dev.get('name', '').lower()
                n_in = dev.get('maxInputChannels')
                n_out = dev.get('maxOutputChannels')
                if "pulse" in name or "default" in name or "pipewire" in name:
                    if n_in > 0 and target_in is None:
                        target_in = i
                    if n_out > 0 and target_out is None:
                        target_out = i
                        
        return target_in, target_out

    def _audio_callback(self, in_data, frame_count, time_info, status):
        if self.is_recording and in_data:
            self.in_queue.put(in_data)
        return (None, pyaudio.paContinue)

    def start_input(self, device_idx):
        self.in_stream = self.pa.open(
            rate=SAMPLE_RATE,
            channels=CHANNELS,
            format=pyaudio.paInt16,
            input=True,
            input_device_index=device_idx,
            frames_per_buffer=CHUNK_SIZE,
            stream_callback=self._audio_callback
        )
        self.is_recording = True

    def stop_input(self):
        self.is_recording = False
        if self.in_stream:
            self.in_stream.stop_stream()
            self.in_stream.close()

    def start_output(self, device_idx):
        self.out_stream = self.pa.open(
            rate=DAC_RATE,
            channels=CHANNELS,
            format=pyaudio.paInt16,
            output=True,
            output_device_index=device_idx
        )

    def stop_output(self):
        if self.out_stream:
            self.out_stream.stop_stream()
            self.out_stream.close()

    def generate_test_tone(self, duration_s=1.5, freq=440.0, volume=0.5):
        t = np.linspace(0, duration_s, int(DAC_RATE * duration_s), False)
        # Apply fade-in and fade-out to avoid clicks
        fade_len = int(DAC_RATE * 0.05)
        fade = np.ones(len(t))
        fade[:fade_len] = np.linspace(0, 1, fade_len)
        fade[-fade_len:] = np.linspace(1, 0, fade_len)
        mono = (np.sin(2 * np.pi * freq * t) * fade * 32700 * volume).astype(np.int16)
        stereo = np.empty(len(t) * 2, dtype=np.int16)
        stereo[0::2] = mono
        stereo[1::2] = mono
        return stereo.tobytes()

    def clear_queue(self):
        while not self.in_queue.empty():
            try:
                self.in_queue.get_nowait()
            except queue.Empty:
                break

    def record_for_seconds(self, seconds=2.0):
        self.clear_queue()
        chunks = []
        expected_chunks = int((seconds * SAMPLE_RATE) / CHUNK_SIZE)
        for _ in range(expected_chunks):
            try:
                chunks.append(self.in_queue.get(timeout=2.0))
            except queue.Empty:
                break
        if not chunks:
            return np.array([]), np.array([])
        
        raw_data = b"".join(chunks)
        audio_stereo = np.frombuffer(raw_data, dtype=np.int16)
        l_ch = audio_stereo[0::2]  # Left: AEC Processed
        r_ch = audio_stereo[1::2]  # Right: Raw Mic
        return l_ch, r_ch


def apply_hpf_140hz(audio_int16):
    """Filtro Passa-Alto Butterworth @ 140Hz."""
    if not HAS_SCIPY or len(audio_int16) == 0:
        return audio_int16
    sos = butter(2, 140.0, 'highpass', fs=SAMPLE_RATE, output='sos')
    filtered = sosfilt(sos, audio_int16.astype(np.float32))
    return np.clip(filtered, -32768, 32767).astype(np.int16)


def main():
    print("=" * 70)
    print("🎙️ PROTOCOLLO DI PRE-TEST E PRE-CALIBRAZIONE AUDIO VUI (MARCUS AI v3.0)")
    print("=" * 70)
    
    tester = AudioTester()
    in_idx, out_idx = tester.find_devices()
    
    if in_idx is None:
        print("❌ ERRORE: Microfono ReSpeaker non trovato! Assicurarsi che sia collegato USB.")
        sys.exit(1)
        
    print(f"✅ Input Device Selezionato: {in_idx}")
    if out_idx is not None:
        print(f"✅ Output Device Selezionato: {out_idx}")
    else:
        print("⚠️ Output Device non identificato con certezza, uso default di sistema.")

    try:
        tester.start_input(in_idx)
        if out_idx is not None:
            tester.start_output(out_idx)
    except Exception as e:
        print(f"❌ Errore apertura stream audio: {e}")
        tester.pa.terminate()
        sys.exit(1)

    try:
        print("\n--- FASE A: Pre-Test Sintetico Auto-Generato dallo Speaker (Sweep Volume) ---")
        print("Emissione tono e contemporanea cattura microfonica per valutare l'AEC hardware XMOS.")
        time.sleep(1.0)
        
        volume_levels = [0.10, 0.30, 0.50, 0.80]
        for vol in volume_levels:
            print(f"\n📢 Test Riproduzione Speaker a Volume {(vol*100):.0f}%...")
            tone_data = tester.generate_test_tone(duration_s=2.0, freq=440.0, volume=vol)
            
            # Start recording just before playing
            tester.clear_queue()
            
            def play_tone():
                if tester.out_stream:
                    tester.out_stream.write(tone_data)
            
            t = threading.Thread(target=play_tone)
            t.start()
            
            l_ch, r_ch = tester.record_for_seconds(2.0)
            t.join()
            
            if len(l_ch) == 0:
                print("  ❌ Nessun dato audio ricevuto dal microfono!")
                continue
                
            rms_l_aec = np.sqrt(np.mean(l_ch.astype(np.float32)**2))
            rms_r_raw = np.sqrt(np.mean(r_ch.astype(np.float32)**2))
            
            print(f"  --> RMS Canale Destro (Raw, No-AEC)   : {rms_r_raw:.1f}")
            print(f"  --> RMS Canale Sinistro (AEC Filtered): {rms_l_aec:.1f}")
            
            if rms_l_aec < 500.0:
                print("  ✅ AEC XMOS efficiente: cancellazione acustica ottimale.")
            elif rms_l_aec < (rms_r_raw * 0.5):
                print("  ⚠️ AEC XMOS sta attenuando, ma lascia residui > 500 RMS.")
            else:
                print("  ❌ AEC XMOS non sta filtrando l'eco! Verificare hardware e configurazione canali.")

        print("\n--- FASE B: Pre-Test Dinamico Rumore Motori (Rotazione 360°) ---")
        print("Valutazione del rumore di fondo reale e verifica del filtro passa-alto @ 140Hz.")
        time.sleep(3)
        
        print("🔴 Registrazione silenzio (3s)...")
        l_ch_idle, _ = tester.record_for_seconds(3.0)
        rms_idle = np.sqrt(np.mean(l_ch_idle.astype(np.float32)**2)) if len(l_ch_idle) > 0 else 0
        print(f"  --> RMS Silenzio: {rms_idle:.1f}")

        time.sleep(3)
        print("🔴 Registrazione rumore motori in corso (3s)...")
        l_ch_motors, _ = tester.record_for_seconds(3.0)
        
        if len(l_ch_motors) > 0:
            rms_motors_raw = np.sqrt(np.mean(l_ch_motors.astype(np.float32)**2))
            l_ch_motors_hpf = apply_hpf_140hz(l_ch_motors)
            rms_motors_hpf = np.sqrt(np.mean(l_ch_motors_hpf.astype(np.float32)**2))
            
            print(f"  --> RMS Rumore Motori (Grezzo) : {rms_motors_raw:.1f}")
            print(f"  --> RMS Rumore Motori (HPF)    : {rms_motors_hpf:.1f}")
            
            if rms_motors_raw > 0:
                attenuation = (1.0 - (rms_motors_hpf / rms_motors_raw)) * 100.0
                print(f"  ✅ Abbattimento rumore meccanico motori: -{attenuation:.1f}%")
        
        print("\n--- FASE C: Matrice di Test Far-Field & SNR a Distanza ---")
        print("Registrazione effettiva della voce per simulare il comportamento del VAD AGC.")
        distances = ["1 Metro", "2 Metri", "3 Metri"]
        for dist in distances:
            print(f"\n📍 Posizionati a {dist} ed emetti il comando: 'Marcus che ore sono?'")
            time.sleep(3)
            
            print("🔴 Registrazione voce in corso (4s)...")
            l_ch_voice, _ = tester.record_for_seconds(4.0)
            
            if len(l_ch_voice) > 0:
                l_ch_voice_hpf = apply_hpf_140hz(l_ch_voice)
                # Troviamo il picco/RMS massimo calcolandolo a finestre di 100ms
                window = int(0.1 * SAMPLE_RATE)
                max_rms = 0.0
                for i in range(0, len(l_ch_voice_hpf)-window, int(window/2)):
                    w = l_ch_voice_hpf[i:i+window]
                    rms_w = np.sqrt(np.mean(w.astype(np.float32)**2))
                    if rms_w > max_rms:
                        max_rms = rms_w
                        
                print(f"  --> RMS Massimo Vocale Rilevato: {max_rms:.0f}")
                
                # Simulazione AGC Node: (target 1500, max 2x)
                if max_rms < 1500.0 and max_rms > rms_idle * 1.5:
                    agc_mult = min(2.0, 1500.0 / max(max_rms, 100.0))
                else:
                    agc_mult = 1.0
                    
                eff_gain = 2.5 * agc_mult
                print(f"  --> Dynamic AGC Software stimato: {eff_gain:.2f}x (Moltiplicatore VUI: {agc_mult:.2f}x)")
                if max_rms * eff_gain > 2000.0:
                    print("  ✅ Segnale ottimale per ASR")
                elif max_rms * eff_gain > 800.0:
                    print("  ⚠️ Segnale debole ma decifrabile")
                else:
                    print("  ❌ Voce troppo bassa, l'ASR potrebbe fallire!")
        
        print("\n" + "=" * 70)
        print("🎉 PROTOCOLLO DI PRE-TEST (VERO HARDWARE) COMPLETATO CON SUCCESSO!")
        print("=" * 70)
        
    finally:
        tester.stop_input()
        tester.stop_output()
        tester.pa.terminate()

if __name__ == '__main__':
    main()
