#!/usr/bin/env python3
"""
test_audio_quality.py — Verifica qualità audio PCM dal ReSpeaker
================================================================
Testa che i campioni int32 dal firmware contengano audio reale
e che l'estrazione MSW funzioni correttamente.

Salva un file WAV per ispezione manuale.

Uso:
    python3 test_audio_quality.py                    # 5 secondi
    python3 test_audio_quality.py --duration 10      # 10 secondi
    python3 test_audio_quality.py --output test.wav  # file custom
"""

import sys
import os
import time
import struct
import array
import argparse
import wave

DEFAULT_PORT = '/dev/ttyACM0'
DEFAULT_BAUD = 921600
SAMPLE_RATE  = 16000

GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
RESET  = '\033[0m'


def main():
    parser = argparse.ArgumentParser(description='Test qualità audio ReSpeaker')
    parser.add_argument('--port', default=DEFAULT_PORT)
    parser.add_argument('--baud', type=int, default=DEFAULT_BAUD)
    parser.add_argument('--duration', type=int, default=5, help='Durata registrazione (secondi)')
    parser.add_argument('--output', default='/tmp/respeaker_test.wav', help='File WAV di output')
    args = parser.parse_args()

    try:
        import serial
    except ImportError:
        print(f"{RED}pip install pyserial{RESET}")
        return

    print(f"\n{BOLD}{'='*60}")
    print(f"  ReSpeaker Audio Quality Test")
    print(f"  Porta: {args.port} | Durata: {args.duration}s")
    print(f"{'='*60}{RESET}\n")

    # Apri seriale
    try:
        ser = serial.Serial(port=args.port, baudrate=args.baud, timeout=2.0, write_timeout=2.0)
        ser.reset_input_buffer()
        print(f"  {GREEN}✅{RESET} Porta aperta")
    except Exception as e:
        print(f"  {RED}❌{RESET} Errore: {e}")
        return

    # Avvia streaming
    ser.write(b'AUDIO_START\n')
    ser.flush()
    time.sleep(0.3)
    ser.reset_input_buffer()  # svuota dati vecchi

    print(f"  {CYAN}🎤{RESET} Registrazione in corso... PARLA nel microfono!")
    print()

    all_raw_audio = bytearray()
    all_msw_audio = bytearray()
    frame_count = 0
    errors = 0
    start = time.time()

    while time.time() - start < args.duration:
        if not ser.in_waiting:
            time.sleep(0.005)
            continue

        try:
            line = ser.readline()
            if not line or not line.startswith(b'AUDIO_PCM:'):
                continue

            header = line.decode('utf-8', errors='replace').strip()
            n_bytes = int(header.split(':')[1])

            # Leggi payload
            pcm = b''
            remaining = n_bytes
            read_start = time.time()
            while remaining > 0 and time.time() - read_start < 2.0:
                chunk = ser.read(min(remaining, 4096))
                if chunk:
                    pcm += chunk
                    remaining -= len(chunk)

            if len(pcm) != n_bytes:
                errors += 1
                continue

            frame_count += 1

            # Analisi RAW (int16 interleaved = int32 packed)
            raw_int16 = array.array('h', pcm)

            # Estrai MSW (audio reale) — stessa logica del fix nel nodo
            msw_int16 = raw_int16[1::2]
            # Estrai LSW (padding/DC)
            lsw_int16 = raw_int16[0::2]

            msw_bytes = msw_int16.tobytes()
            all_raw_audio.extend(pcm)
            all_msw_audio.extend(msw_bytes)

            # Calcola metriche
            msw_peak = max(abs(s) for s in msw_int16) if msw_int16 else 0
            lsw_peak = max(abs(s) for s in lsw_int16) if lsw_int16 else 0
            msw_avg = sum(abs(s) for s in msw_int16) / len(msw_int16) if msw_int16 else 0
            lsw_avg = sum(abs(s) for s in lsw_int16) / len(lsw_int16) if lsw_int16 else 0

            # DC offset (media non-assoluta)
            msw_dc = sum(msw_int16) / len(msw_int16) if msw_int16 else 0

            # Stampa ogni 10 frame
            if frame_count <= 3 or frame_count % 10 == 0:
                bar_msw = '█' * min(40, msw_peak // 800)
                elapsed = time.time() - start
                print(
                    f"  [{elapsed:5.1f}s] Frame #{frame_count:4d}: "
                    f"MSW peak={msw_peak:6d} avg={msw_avg:6.0f} dc={msw_dc:+7.0f} "
                    f"| LSW peak={lsw_peak:6d}  {bar_msw}")

        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  {YELLOW}⚠️{RESET} Errore: {e}")

    # Stop streaming
    ser.write(b'AUDIO_STOP\n')
    ser.flush()
    time.sleep(0.2)
    ser.close()

    elapsed = time.time() - start

    print(f"\n{BOLD}{'='*60}")
    print(f"  RISULTATI")
    print(f"{'='*60}{RESET}\n")

    print(f"  Frame ricevuti:  {frame_count}")
    print(f"  Errori:          {errors}")
    print(f"  Durata:          {elapsed:.1f}s")
    print(f"  Raw bytes:       {len(all_raw_audio)}")
    print(f"  Audio MSW bytes: {len(all_msw_audio)}")

    if len(all_msw_audio) == 0:
        print(f"\n  {RED}❌ Nessun audio catturato!{RESET}")
        return

    # Analisi finale
    final_int16 = array.array('h', bytes(all_msw_audio))
    final_peak = max(abs(s) for s in final_int16)
    final_avg = sum(abs(s) for s in final_int16) / len(final_int16)
    final_dc = sum(final_int16) / len(final_int16)

    # Calcola se il segnale è "vivo" (varianza significativa)
    mean = final_dc
    variance = sum((s - mean)**2 for s in final_int16) / len(final_int16)
    std_dev = variance ** 0.5

    print(f"\n  {BOLD}Analisi audio MSW (solo campioni reali):{RESET}")
    print(f"  Campioni totali: {len(final_int16)}")
    print(f"  Picco:           {final_peak}")
    print(f"  Media abs:       {final_avg:.1f}")
    print(f"  DC offset:       {final_dc:+.1f}")
    print(f"  Std deviation:   {std_dev:.1f}")

    if std_dev < 10:
        print(f"\n  {RED}❌ AUDIO MORTO{RESET}: deviazione standard {std_dev:.1f} (quasi silenzio)")
        print(f"     Il mic potrebbe non essere inizializzato (codec AIC3204)")
    elif std_dev < 200:
        print(f"\n  {YELLOW}⚠️  AUDIO BASSO{RESET}: std={std_dev:.1f}")
        print(f"     Probabile solo rumore di fondo. Hai parlato nel microfono?")
    else:
        print(f"\n  {GREEN}✅ AUDIO VIVO!{RESET}: std={std_dev:.1f}, peak={final_peak}")
        print(f"     Il microfono sta catturando suono reale!")

    if abs(final_dc) > 5000:
        print(f"\n  {YELLOW}⚠️  DC OFFSET ALTO{RESET}: {final_dc:+.0f}")
        print(f"     Il codec potrebbe avere un problema di bias")

    # Salva WAV
    try:
        with wave.open(args.output, 'w') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(bytes(all_msw_audio))
        print(f"\n  {GREEN}📁{RESET} WAV salvato: {args.output}")
        duration_wav = len(final_int16) / SAMPLE_RATE
        print(f"     Durata: {duration_wav:.1f}s | {SAMPLE_RATE}Hz mono 16-bit")
        print(f"\n  Per ascoltarlo:")
        print(f"     aplay {args.output}")
        print(f"     # oppure scaricalo: scp marcus:{args.output} .")
    except Exception as e:
        print(f"  {RED}❌{RESET} Errore salvataggio WAV: {e}")

    # Salva anche la versione RAW (int32) per confronto
    raw_wav = args.output.replace('.wav', '_raw32.wav')
    try:
        # Il raw contiene int32 packed come 2×int16, quindi 2 canali a 16 bit
        with wave.open(raw_wav, 'w') as wf:
            wf.setnchannels(2)    # LSW+MSW come "2 canali"
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(bytes(all_raw_audio))
        print(f"  {GREEN}📁{RESET} RAW salvato: {raw_wav} (per confronto, 2ch)")
    except Exception as e:
        pass

    print()


if __name__ == '__main__':
    main()
