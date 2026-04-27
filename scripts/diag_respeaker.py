#!/usr/bin/env python3
"""
Diagnostica hardware ReSpeaker Lite 2-Mic USB.
Esegui sul Raspberry Pi per verificare:
  1. Audio in ingresso (microfono) â€” livelli RMS, P2P, clipping
  2. Audio in uscita (speaker) â€” beep test a 48kHz
  3. Porta seriale /dev/ttyACM0 â€” comunicazione firmware
  4. Salva registrazione WAV per analisi offline

Uso: python3 diag_respeaker.py
"""

import sys
import time
import os
import struct
import wave
import numpy as np

try:
    import pyaudio
except ImportError:
    print("âŒ PyAudio non installato: pip install pyaudio")
    sys.exit(1)

DEVICE_NAME = "ReSpeaker"
SAMPLE_RATE = 16000
CHANNELS = 2
RECORD_SECONDS = 5
CHUNK = 960

def find_device(pa, name_filter):
    """Trova il device audio per nome."""
    info = pa.get_host_api_info_by_index(0)
    n = info.get('deviceCount')
    in_idx = out_idx = None
    print(f"\nðŸ“‹ Dispositivi audio ({n} trovati):")
    for i in range(n):
        dev = pa.get_device_info_by_host_api_device_index(0, i)
        dname = dev.get('name')
        n_in = dev.get('maxInputChannels')
        n_out = dev.get('maxOutputChannels')
        default_rate = dev.get('defaultSampleRate')
        marker = ""
        if name_filter.lower() in dname.lower():
            if n_in > 0 and in_idx is None:
                in_idx = i
                marker += " â† INPUT"
            if n_out > 0 and out_idx is None:
                out_idx = i
                marker += " â† OUTPUT"
        print(f"  [{i}] {dname} (in:{n_in}, out:{n_out}, rate:{default_rate}){marker}")
    return in_idx, out_idx

def test_microphone(pa, device_idx):
    """Test microfono: registra e analizza."""
    print(f"\n{'='*60}")
    print(f"ðŸŽ¤ TEST MICROFONO (device idx={device_idx})")
    print(f"{'='*60}")
    print(f"   Registrazione di {RECORD_SECONDS} secondi...")
    print(f"   âš ï¸  PARLA VICINO AL MICROFONO per testare i livelli!")
    print()

    frames = []
    rms_history = []
    p2p_history = []

    stream = pa.open(
        rate=SAMPLE_RATE,
        channels=CHANNELS,
        format=pyaudio.paInt16,
        input=True,
        input_device_index=device_idx,
        frames_per_buffer=CHUNK
    )

    start = time.time()
    chunk_count = 0
    while time.time() - start < RECORD_SECONDS:
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)
        chunk_count += 1

        stereo = np.frombuffer(data, dtype=np.int16)
        l_ch = stereo[::2]
        r_ch = stereo[1::2]

        rms_l = np.sqrt(np.mean(l_ch.astype(np.float32)**2))
        rms_r = np.sqrt(np.mean(r_ch.astype(np.float32)**2))
        p2p_l = int(np.max(l_ch)) - int(np.min(l_ch))
        p2p_r = int(np.max(r_ch)) - int(np.min(r_ch))

        rms_history.append((rms_l, rms_r))
        p2p_history.append((p2p_l, p2p_r))

        if chunk_count % 16 == 0:  # ogni ~1s
            elapsed = time.time() - start
            print(f"  [{elapsed:.1f}s] L: RMS={rms_l:.0f} P2P={p2p_l:5d} | "
                  f"R: RMS={rms_r:.0f} P2P={p2p_r:5d}")

    stream.stop_stream()
    stream.close()

    # Statistiche
    rms_arr = np.array(rms_history)
    p2p_arr = np.array(p2p_history)

    print(f"\nðŸ“Š STATISTICHE ({chunk_count} chunks):")
    print(f"   Canal LEFT:")
    print(f"     RMS  â€” min={rms_arr[:,0].min():.0f}  max={rms_arr[:,0].max():.0f}  avg={rms_arr[:,0].mean():.0f}")
    print(f"     P2P  â€” min={p2p_arr[:,0].min()}  max={p2p_arr[:,0].max()}  avg={p2p_arr[:,0].mean():.0f}")
    print(f"   Canal RIGHT:")
    print(f"     RMS  â€” min={rms_arr[:,1].min():.0f}  max={rms_arr[:,1].max():.0f}  avg={rms_arr[:,1].mean():.0f}")
    print(f"     P2P  â€” min={p2p_arr[:,1].min()}  max={p2p_arr[:,1].max()}  avg={p2p_arr[:,1].mean():.0f}")

    # Verifica clipping
    max_p2p = max(p2p_arr[:,0].max(), p2p_arr[:,1].max())
    if max_p2p > 60000:
        print(f"\n   âš ï¸  ATTENZIONE: P2P={max_p2p} > 60000 â€” CLIPPING HARDWARE RILEVATO!")
        print(f"   â†’ L'attenuazione software /3.0 Ã¨ necessaria.")
    elif max_p2p > 40000:
        print(f"\n   ðŸŸ¡ P2P={max_p2p} â€” livello alto, l'attenuazione /3.0 Ã¨ corretta.")
    elif max_p2p < 500:
        print(f"\n   âŒ P2P={max_p2p} < 500 â€” MICROFONO QUASI MUTO!")
        print(f"   â†’ Verifica connessione USB, firmware, alimentazione.")
    else:
        print(f"\n   âœ… P2P={max_p2p} â€” livelli normali.")

    # Salva WAV
    wav_path = "/tmp/diag_respeaker_recording.wav"
    with wave.open(wav_path, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b''.join(frames))
    print(f"\n   ðŸ’¾ Registrazione salvata: {wav_path}")

    return rms_arr, p2p_arr

def test_speaker(pa, device_idx):
    """Test speaker: genera un beep a 48kHz."""
    print(f"\n{'='*60}")
    print(f"ðŸ”Š TEST SPEAKER (device idx={device_idx})")
    print(f"{'='*60}")

    # Rileva rate nativo
    dev_info = pa.get_device_info_by_index(device_idx)
    hw_rate = int(dev_info.get('defaultSampleRate', 16000))
    print(f"   HW native rate: {hw_rate} Hz")

    # Genera beep (1kHz, 0.5s)
    duration = 0.5
    freq = 1000
    t = np.linspace(0, duration, int(hw_rate * duration), False)
    fade = np.linspace(1.0, 0.0, len(t))
    mono = (np.sin(2 * np.pi * freq * t) * fade * 8000).astype(np.int16)
    stereo = np.repeat(mono, 2)  # mono -> stereo interleaved

    print(f"   Riproduzione beep {freq}Hz @ {hw_rate}Hz stereo...")
    try:
        stream = pa.open(
            rate=hw_rate,
            channels=2,
            format=pyaudio.paInt16,
            output=True,
            output_device_index=device_idx
        )
        stream.write(stereo.tobytes())
        time.sleep(0.1)
        stream.stop_stream()
        stream.close()
        print(f"   âœ… Beep riprodotto con successo a {hw_rate}Hz!")
    except Exception as e:
        print(f"   âŒ Errore speaker: {e}")

    # Test anche a 16kHz per confronto
    print(f"\n   Test confronto a 16000 Hz (potrebbe avere effetto chipmunk)...")
    try:
        mono_16k = (np.sin(2 * np.pi * freq * np.linspace(0, duration, int(16000 * duration), False)) * 8000).astype(np.int16)
        stereo_16k = np.repeat(mono_16k, 2)
        stream = pa.open(
            rate=16000,
            channels=2,
            format=pyaudio.paInt16,
            output=True,
            output_device_index=device_idx
        )
        stream.write(stereo_16k.tobytes())
        time.sleep(0.1)
        stream.stop_stream()
        stream.close()
        if hw_rate != 16000:
            print(f"   ðŸŸ¡ Se il beep a 16kHz suona piÃ¹ acuto/veloce rispetto a {hw_rate}Hz,")
            print(f"      conferma che il DAC NON resampla e serve ratecv esplicito.")
        else:
            print(f"   âœ… Rate nativo 16kHz â€” nessun resampling necessario.")
    except Exception as e:
        print(f"   âŒ Errore speaker 16kHz: {e}")

def test_serial():
    """Test porta seriale per LED controller."""
    print(f"\n{'='*60}")
    print(f"ðŸ’¡ TEST PORTA SERIALE (LED)")
    print(f"{'='*60}")

    port = "/dev/ttyACM0"
    if not os.path.exists(port):
        print(f"   âŒ Porta {port} NON trovata.")
        print(f"   â†’ Il firmware standard del ReSpeaker non abilita la seriale USB JTAG.")
        print(f"   â†’ Per controllare i LED serve il firmware custom con:")
        print(f"     CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG: y")
        return False

    try:
        import serial
        ser = serial.Serial(port, 115200, timeout=2)
        print(f"   âœ… Porta {port} aperta a 115200 baud")

        # Invia un comando LED di test
        ser.write(b"LED_EFFECT:IDLE\n")
        time.sleep(0.5)
        response = ser.read(ser.in_waiting) if ser.in_waiting else b""
        print(f"   TX: LED_EFFECT:IDLE")
        print(f"   RX: {response!r} ({len(response)} bytes)")

        if len(response) == 0:
            print(f"   âš ï¸  Nessuna risposta â€” il firmware potrebbe non supportare comandi LED via seriale.")
        else:
            print(f"   âœ… Comunicazione seriale funzionante!")

        ser.close()
        return True

    except ImportError:
        print(f"   âš ï¸  pyserial non installato (pip install pyserial)")
        return False
    except Exception as e:
        print(f"   âŒ Errore seriale: {e}")
        return False


def main():
    print("â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—")
    print("â•‘       Diagnostica ReSpeaker Lite 2-Mic USB               â•‘")
    print("â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•")

    pa = pyaudio.PyAudio()

    try:
        in_idx, out_idx = find_device(pa, DEVICE_NAME)

        if in_idx is None:
            print(f"\nâŒ ERRORE: Device '{DEVICE_NAME}' input non trovato!")
            print("   Verifica la connessione USB del ReSpeaker.")
            return

        # Test microfono
        test_microphone(pa, in_idx)

        # Test speaker
        if out_idx is not None:
            test_speaker(pa, out_idx)
        else:
            print(f"\nâš ï¸  Nessun device di output '{DEVICE_NAME}' trovato.")

        # Test seriale
        test_serial()

        # Riepilogo
        print(f"\n{'='*60}")
        print(f"ðŸ“‹ RIEPILOGO DIAGNOSTICO")
        print(f"{'='*60}")
        print(f"  Input device:  [{in_idx}]")
        print(f"  Output device: [{out_idx}]")
        if out_idx:
            dev_info = pa.get_device_info_by_index(out_idx)
            print(f"  Output HW rate: {int(dev_info.get('defaultSampleRate'))} Hz")
        print(f"  Registrazione: /tmp/diag_respeaker_recording.wav")
        print(f"\n  Per ascoltare la registrazione:")
        print(f"  aplay -D plughw:0,0 /tmp/diag_respeaker_recording.wav")

    finally:
        pa.terminate()


if __name__ == '__main__':
    main()
