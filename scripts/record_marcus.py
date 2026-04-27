#!/usr/bin/env python3
"""
Script 1 di 2 â€” Registra audio mentre dici "Marcus".
Salva il file in /tmp/test_marcus.wav

Uso: python3 record_marcus.py
"""

import wave
import sys
import time
import numpy as np

try:
    import pyaudio
except ImportError:
    print("âŒ PyAudio non installato")
    sys.exit(1)

DEVICE_NAME  = "ReSpeaker"
SAMPLE_RATE  = 16000   # Rate standard supportato
CHANNELS     = 2
RECORD_SECS  = 5
CHUNK        = 960
OUTPUT_FILE  = "/tmp/test_marcus_stereo.wav"


def find_input_device(pa):
    info = pa.get_host_api_info_by_index(0)
    for i in range(info.get('deviceCount')):
        dev  = pa.get_device_info_by_host_api_device_index(0, i)
        name = dev.get('name', '')
        if DEVICE_NAME.lower() in name.lower() and dev.get('maxInputChannels', 0) > 0:
            print(f"âœ… Dispositivo trovato: [{i}] {name}")
            return i
    print("âš ï¸  ReSpeaker non trovato, uso il default")
    return None


def main():
    pa      = pyaudio.PyAudio()
    idx     = find_input_device(pa)
    frames  = []
    peaks   = []

    print(f"\n{'='*50}")
    print(f"ðŸŽ¤ Inizia a parlare tra 1 secondo...")
    print(f"   Durata registrazione: {RECORD_SECS}s")
    print(f"   Ripeti 'MARCUS' piÃ¹ volte chiaramente!")
    print(f"{'='*50}\n")

    time.sleep(1.0)
    print("ðŸ”´ REC â€” Parla ora!")

    stream = pa.open(
        rate=SAMPLE_RATE, channels=CHANNELS,
        format=pyaudio.paInt16, input=True,
        input_device_index=idx, frames_per_buffer=CHUNK
    )

    start = time.time()
    while time.time() - start < RECORD_SECS:
        data   = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data) # STEREO RAW 16kHz
        
        stereo = np.frombuffer(data, dtype=np.int16)
        l_ch   = stereo[::2]
        p2p    = int(np.max(l_ch)) - int(np.min(l_ch))
        elapsed = time.time() - start
        bar    = "â–ˆ" * min(40, p2p // 1000)
        print(f"\r  [{elapsed:.1f}s] P2P={p2p:5d} |{bar:<40}|", end="", flush=True)

    print("\nðŸ”µ STOP")
    stream.stop_stream()
    stream.close()
    pa.terminate()

    # Salva come Stereo 16kHz
    all_data = b"".join(frames)
    with wave.open(OUTPUT_FILE, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(all_data)

    max_p2p = max(peaks) if peaks else 0
    avg_p2p = sum(peaks) // len(peaks) if peaks else 0

    print(f"\nðŸ“Š Statistiche registrazione:")
    print(f"   Max P2P: {max_p2p}  (buono se > 8000 mentre parli)")
    print(f"   Avg P2P: {avg_p2p}")
    print(f"   Chunks:  {len(frames)}")

    if max_p2p < 2000:
        print("\nâš ï¸  Il segnale Ã¨ molto basso. Avvicinati al microfono!")
    elif max_p2p > 30000:
        print("\nâš ï¸  Segnale saturato. Allontanati un po'.")
    else:
        print("\nâœ… Livelli audio OK.")

    print(f"\nðŸ’¾ Salvato: {OUTPUT_FILE}")
    print(f"\nOra esegui:")
    print(f"  python3 /mnt/ssd/robopy_controller_host/scripts/test_porcupine_offline.py")


if __name__ == '__main__':
    main()
