#!/usr/bin/env python3
"""
respeaker_diag.py — Diagnostica progressiva ReSpeaker Lite
===========================================================
Testa in sequenza:
  1. Connessione USB e heartbeat
  2. LED (colori e effetti)
  3. Microfono (audio vivo vs congelato)
  4. Speaker (beep locale)

Uso:
    python3 respeaker_diag.py
    python3 respeaker_diag.py --port /dev/ttyACM1
"""

import sys
import time
import array
import argparse
import threading
import serial

PORT    = '/dev/ttyACM0'
BAUD    = 921600
TIMEOUT = 3.0

GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
RESET  = '\033[0m'

def ok(msg):  print(f"  {GREEN}✅ {msg}{RESET}")
def err(msg): print(f"  {RED}❌ {msg}{RESET}")
def warn(msg):print(f"  {YELLOW}⚠️  {msg}{RESET}")
def info(msg):print(f"  {CYAN}ℹ️  {msg}{RESET}")
def hdr(msg): print(f"\n{BOLD}{'─'*60}\n  {msg}\n{'─'*60}{RESET}")


def open_port(port, baud):
    try:
        s = serial.Serial(port=port, baudrate=baud, timeout=2.0, write_timeout=2.0)
        s.reset_input_buffer()
        return s
    except Exception as e:
        err(f"Impossibile aprire {port}: {e}")
        return None


def send(ser, cmd):
    ser.write((cmd + '\n').encode())
    ser.flush()


def wait_for(ser, prefix, timeout=3.0):
    """Legge righe finché non trova una che inizia con prefix."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            line = ser.readline()
            if line and line.startswith(prefix.encode()):
                return line.decode('utf-8', errors='replace').strip()
        except:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — Connessione e READY/HEARTBEAT
# ─────────────────────────────────────────────────────────────────────────────
def test_connection(ser):
    hdr("TEST 1 — Connessione USB e Heartbeat")

    # Aspetta READY (inviato al boot) o forza un HEARTBEAT_REQ
    send(ser, 'HEARTBEAT_REQ')
    resp = wait_for(ser, 'HEARTBEAT', timeout=4.0)
    if resp:
        ok(f"Risposta ricevuta: '{resp}'")
        return True
    else:
        err("Nessuna risposta a HEARTBEAT_REQ entro 4s")
        info("Possibili cause:")
        info("  - firmware non flashato correttamente")
        info("  - porta sbagliata (prova --port /dev/ttyACM1)")
        info("  - CONFIG_ESP_CONSOLE_NONE non attivo (GPIO43 occupato da UART)")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — LED
# ─────────────────────────────────────────────────────────────────────────────
def test_led(ser):
    hdr("TEST 2 — LED WS2812")

    tests = [
        ('LED_RGB:255,0,0',   'Rosso'),
        ('LED_RGB:0,255,0',   'Verde'),
        ('LED_RGB:0,0,255',   'Blu'),
        ('LED_RGB:255,255,0', 'Giallo'),
        ('LED_OFF',           'Spento'),
        ('LED_EFFECT:LISTENING', 'Effetto LISTENING'),
        ('LED_EFFECT:THINKING',  'Effetto THINKING'),
        ('LED_OFF',           'Spento finale'),
    ]

    for cmd, desc in tests:
        send(ser, cmd)
        time.sleep(0.6)
        print(f"  → {desc}: guarda il LED fisicamente ← ")

    # Verifica STATO
    send(ser, 'STATO')
    resp = wait_for(ser, 'STATO:', timeout=2.0)
    if resp:
        ok(f"STATO risposto: {resp}")
    else:
        warn("STATO non risposto — comando non riconosciuto?")

    ok("Test LED completato (verifica visivamente)")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — Microfono
# ─────────────────────────────────────────────────────────────────────────────
def test_microphone(ser):
    hdr("TEST 3 — Microfono I2S")

    info("Avvio streaming audio per 4 secondi...")
    info("PARLA nel microfono durante il test!")
    print()

    send(ser, 'AUDIO_START')
    resp = wait_for(ser, 'STREAM:ON', timeout=3.0)
    if not resp:
        err("STREAM:ON non ricevuto — AUDIO_START ignorato?")
        return False

    ok("Streaming avviato")

    frames = []
    frozen_count = 0
    last_signature = None
    start = time.time()

    while time.time() - start < 4.0:
        try:
            line = ser.readline()
            if not line:
                continue

            if line.startswith(b'AUDIO_PCM:'):
                header = line.decode('utf-8', errors='replace').strip()
                parts = header.split(':')
                n_bytes = int(parts[1])

                # Leggi payload
                pcm = b''
                remaining = n_bytes
                t0 = time.time()
                while remaining > 0 and time.time() - t0 < 1.0:
                    chunk = ser.read(min(remaining, 4096))
                    if chunk:
                        pcm += chunk
                        remaining -= len(chunk)

                if len(pcm) != n_bytes:
                    continue

                raw = array.array('h', pcm)
                msw = raw[1::2]  # campioni reali

                peak = max(abs(s) for s in msw) if msw else 0
                avg  = sum(abs(s) for s in msw) / len(msw) if msw else 0
                dc   = sum(msw) / len(msw) if msw else 0

                # Firma del frame per rilevare dati congelati
                sig = (peak, int(avg), int(dc))
                if sig == last_signature:
                    frozen_count += 1
                else:
                    frozen_count = 0
                last_signature = sig

                frames.append({'peak': peak, 'avg': avg, 'dc': dc})

                elapsed = time.time() - start
                bar = '█' * min(30, peak // 700)
                frozen_mark = f" {RED}FROZEN!{RESET}" if frozen_count > 3 else ""
                print(f"  [{elapsed:4.1f}s] peak={peak:6d} avg={avg:6.0f} dc={dc:+7.0f}  {bar}{frozen_mark}")

        except Exception as e:
            pass

    send(ser, 'AUDIO_STOP')
    wait_for(ser, 'STREAM:OFF', timeout=2.0)

    print()
    if not frames:
        err("Nessun frame audio ricevuto!")
        info("Possibili cause:")
        info("  - microphone.start non eseguito nel boot")
        info("  - i2s_din_pin sbagliato (deve essere GPIO43)")
        info("  - XU316 non alimentato (GPIO2 deve essere HIGH)")
        return False

    # Analisi finale
    peaks = [f['peak'] for f in frames]
    avgs  = [f['avg']  for f in frames]
    dcs   = [f['dc']   for f in frames]

    all_same = len(set(peaks)) == 1 and len(set([int(a) for a in avgs])) == 1
    max_peak = max(peaks)
    mean_avg = sum(avgs) / len(avgs)

    # Calcola varianza dei peak tra frame
    mean_peak = sum(peaks) / len(peaks)
    peak_variance = sum((p - mean_peak)**2 for p in peaks) / len(peaks)
    peak_std = peak_variance ** 0.5

    print(f"  Frame totali:    {len(frames)}")
    print(f"  Peak massimo:    {max_peak}")
    print(f"  Media avg:       {mean_avg:.0f}")
    print(f"  Std peak/frame:  {peak_std:.1f}")

    if all_same or peak_std < 1:
        err("DATI CONGELATI: tutti i frame identici")
        info("Il DMA I2S sta riciclando lo stesso buffer")
        info("Causa probabile: sequenza start/stop nel boot non ha resettato il DMA")
        info("Prova ad aumentare il delay tra stop e start a 500ms")
        return False
    elif max_peak < 100:
        err(f"AUDIO PIATTO: peak massimo solo {max_peak}")
        info("XU316 potrebbe non stare trasmettendo dati I2S")
        info("Verifica che GPIO2 sia HIGH (xu316_power ALWAYS_ON)")
        return False
    elif mean_avg < 50:
        warn(f"Audio molto basso (avg={mean_avg:.0f})")
        warn("Rumore di fondo quasi zero — hai parlato?")
        info("Se anche parlando non cambia: problema hardware o pin DIN errato")
        return True
    else:
        ok(f"Audio VIVO! peak={max_peak}, std={peak_std:.0f}")
        return True


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — Speaker (beep locale)
# ─────────────────────────────────────────────────────────────────────────────
def test_speaker(ser):
    hdr("TEST 4 — Speaker (beep locale)")

    info("Invio comando PLAY_BEEP...")
    send(ser, 'PLAY_BEEP')
    time.sleep(1.0)
    ok("Comando inviato — hai sentito un beep dallo speaker?")
    info("Se non senti nulla:")
    info("  - i2s_dout_pin deve essere GPIO44")
    info("  - lo speaker NS4830 deve essere alimentato")
    info("  - il BEEP_PCM deve essere definito in respeaker_helper.h")

    # Test speaker con audio dal Pi (chunk piccolo di silenzio)
    info("\nTest ricezione AUDIO_OUT (invio 64 byte di silenzio)...")
    silence = b'\x00' * 64
    send(ser, f'AUDIO_OUT:64')
    time.sleep(0.05)
    ser.write(silence)
    ser.flush()

    resp = wait_for(ser, 'SPEAKER:CHUNK_DONE', timeout=3.0)
    if resp:
        ok("SPEAKER:CHUNK_DONE ricevuto — ring buffer funziona")
    else:
        warn("SPEAKER:CHUNK_DONE non ricevuto entro 3s")
        info("Il ring buffer o il task spk_task potrebbero non girare")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 — Informazioni raw I2S
# ─────────────────────────────────────────────────────────────────────────────
def test_diag(ser):
    hdr("TEST 5 — Diagnostica AUDIO_LEVEL (raw I2S)")

    info("Attivo DIAG_ON per 3 secondi...")
    send(ser, 'DIAG_ON')

    levels = []
    start = time.time()
    while time.time() - start < 3.0:
        try:
            line = ser.readline()
            if line and line.startswith(b'AUDIO_LEVEL:'):
                val = int(line.decode().strip().split(':')[1])
                levels.append(val)
                bar = '█' * min(30, val // 700)
                print(f"  AUDIO_LEVEL={val:6d}  {bar}")
        except:
            pass

    send(ser, 'DIAG_OFF')

    if not levels:
        err("Nessun AUDIO_LEVEL ricevuto — DIAG_ON non supportato o mic fermo")
    else:
        max_level = max(levels)
        ok(f"Ricevuti {len(levels)} livelli, max={max_level}")
        if max_level < 10:
            warn("Livello quasi zero — microfono non risponde")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', default=PORT)
    parser.add_argument('--baud', type=int, default=BAUD)
    parser.add_argument('--skip-led', action='store_true')
    args = parser.parse_args()

    print(f"\n{BOLD}{'='*60}")
    print(f"  ReSpeaker Lite — Diagnostica Completa")
    print(f"  Porta: {args.port}")
    print(f"{'='*60}{RESET}\n")

    ser = open_port(args.port, args.baud)
    if not ser:
        sys.exit(1)

    ok(f"Porta {args.port} aperta")
    time.sleep(0.5)
    ser.reset_input_buffer()

    results = {}

    # Test 1: Connessione
    results['connessione'] = test_connection(ser)
    if not results['connessione']:
        err("Connessione fallita — impossibile continuare")
        ser.close()
        sys.exit(1)

    # Test 2: LED
    if not args.skip_led:
        test_led(ser)
        results['led'] = True  # verifica visiva

    # Test 3: Microfono
    results['microfono'] = test_microphone(ser)

    # Test 4: Speaker
    test_speaker(ser)

    # Test 5: Diag
    test_diag(ser)

    # Pulizia finale
    send(ser, 'LED_OFF')
    ser.close()

    # Riepilogo
    hdr("RIEPILOGO")
    for nome, esito in results.items():
        if esito:
            ok(nome)
        else:
            err(nome)

    print()
    if not results.get('microfono'):
        print(f"{BOLD}Prossimi passi per il microfono:{RESET}")
        print("  1. Verifica che xu316_power usi ALWAYS_ON (non ALWAYS_OFF)")
        print("  2. Aumenta delay tra microphone.stop e microphone.start a 500ms")
        print("  3. Aggiungi i2s_mode: master nel blocco i2s_audio")
        print("  4. Verifica fisicamente che il cavo USB sia sulla porta XIAO")
    print()


if __name__ == '__main__':
    main()