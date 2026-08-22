#!/usr/bin/env python3
"""
turn_exact_one_rev.py - Fa compiere ESATTAMENTE 1 GIRO controllato alla Ruota Destra (o Sinistra)
utilizzando il feedback in tempo reale dell'encoder a circuito chiuso.
"""
import serial
import time
import json

def turn_right_wheel_one_rev(target_ticks=590, duty=0.25):
    s = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.05)
    time.sleep(0.3)
    
    # Baseline
    odl_0, odr_0 = None, None
    t0 = time.time()
    while time.time() - t0 < 0.6:
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{') and '"odr"' in line:
            try:
                d = json.loads(line)
                odl_0, odr_0 = d.get('odl'), d.get('odr')
            except Exception:
                pass

    print(f"\n🎯 AVVIO ROTAZIONE TARGET: ESATTAMENTE 1 GIRO (Target: {target_ticks} ticks)")
    print(f"   Tick iniziali: odl = {odl_0}, odr = {odr_0}")

    cmd_bytes = f'{{"T":1,"L":0.0,"R":{duty:.3f}}}\n'.encode('utf-8')
    stop_bytes = b'{"T":1,"L":0.0,"R":0.0}\n'

    t_start = time.time()
    last_odr = odr_0
    
    while time.time() - t_start < 3.0:
        # Invia comando
        s.write(cmd_bytes)
        
        # Leggi feedback rapido
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{') and '"odr"' in line:
            try:
                d = json.loads(line)
                cur_odr = d.get('odr')
                if cur_odr is not None and odr_0 is not None:
                    delta_ticks = abs(cur_odr - odr_0)
                    last_odr = cur_odr
                    if delta_ticks >= target_ticks:
                        break
            except Exception:
                pass
        time.sleep(0.01)

    # Stop immediato
    for _ in range(5):
        s.write(stop_bytes)
        time.sleep(0.01)
    s.flush()
    time.sleep(0.5)

    # Lettura finale
    t0 = time.time()
    final_odr = last_odr
    while time.time() - t0 < 0.5:
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{') and '"odr"' in line:
            try:
                d = json.loads(line)
                final_odr = d.get('odr')
            except Exception:
                pass

    s.close()
    total_delta = abs(final_odr - odr_0) if (final_odr is not None and odr_0 is not None) else 0
    estimated_revs = total_delta / target_ticks

    print(f"\n⏹️ TEST 1 GIRO COMPLETATO!")
    print(f"   Tick iniziali: {odr_0} -> Tick finali: {final_odr}")
    print(f"   Delta Tick effettivi: {total_delta} (Target: {target_ticks})")
    print(f"   Giri ruota calcolati: {estimated_revs:.2f} giri")
    print("="*60)

if __name__ == '__main__':
    # CPR = 750 ticks/giro (Waveshare 1:34 standard). Con cutoff a 580 ticks @ duty 0.18, totale atteso = ~750 ticks (1.00 giro esatto)
    turn_right_wheel_one_rev(target_ticks=580, duty=0.18)
