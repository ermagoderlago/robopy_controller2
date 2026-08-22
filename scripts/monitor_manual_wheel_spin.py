#!/usr/bin/env python3
"""
monitor_manual_wheel_spin.py - Monitoraggio Passivo in Tempo Reale della Rotazione Manuale
I motori sono COMPLETAMENTE SPENTI. L'utente ruota a mano la ruota destra (o sinistra) di 360°
e il programma calcola istantaneamente i tick/giro (CPR) reali.
"""

import serial
import time
import json
import sys

def monitor_manual_rotation(duration_sec=20.0):
    try:
        s = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.05)
    except Exception as e:
        print(f"❌ Errore apertura seriale: {e}")
        return

    time.sleep(0.3)
    # Assicura motori spenti a 0
    s.write(b'{"T":1,"L":0.0,"R":0.0}\n')
    time.sleep(0.1)

    # 1. Lettura baseline
    odl_0, odr_0 = None, None
    t0 = time.time()
    while time.time() - t0 < 0.8:
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{') and '"odr"' in line:
            try:
                d = json.loads(line)
                odl_0, odr_0 = d.get('odl'), d.get('odr')
            except Exception:
                pass

    if odl_0 is None or odr_0 is None:
        print("❌ Errore lettura telemetria iniziale.")
        s.close()
        return

    print("\n" + "="*70)
    print("🟢 PRONTO! I MOTORI SONO COMPLETAMENTE DISATTIVATI.")
    print(f"   Tick Iniziali: odl = {odl_0} (SX) | odr = {odr_0} (DX)")
    print("   👉 Gira adesso la ruota a mano esattamente di 360° (1 giro completo).")
    print("   (Hai 20 secondi; premi Ctrl+C o aspetta per il report finale)")
    print("="*70)

    last_odl, last_odr = odl_0, odr_0
    t_start = time.time()

    try:
        while time.time() - t_start < duration_sec:
            line = s.readline().decode('utf-8', errors='ignore').strip()
            if line.startswith('{') and '"odr"' in line:
                try:
                    d = json.loads(line)
                    cur_odl = d.get('odl')
                    cur_odr = d.get('odr')
                    if cur_odl is not None and cur_odr is not None:
                        d_l = cur_odl - odl_0
                        d_r = cur_odr - odr_0
                        last_odl, last_odr = cur_odl, cur_odr
                        sys.stdout.write(f"\r📡 LIVE DELTA: Ruota SX = {d_l:+5d} tick | Ruota DX = {d_r:+5d} tick")
                        sys.stdout.flush()
                except Exception:
                    pass
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass

    s.close()

    total_d_l = last_odl - odl_0
    total_d_r = last_odr - odr_0

    print("\n\n" + "="*70)
    print("📊 REPORT FINALE ROTAZIONE MANUALE:")
    print(f"   • Delta Ruota Sinistra (odl): {total_d_l:+d} ticks")
    print(f"   • Delta Ruota Destra   (odr): {total_d_r:+d} ticks")
    print(f"   • CPR 1 GIRO RUOTA DESTRA:   {abs(total_d_r)} ticks/giro")
    print(f"   • CPR 1 GIRO RUOTA SINISTRA: {abs(total_d_l)} ticks/giro")
    print("="*70 + "\n")

if __name__ == '__main__':
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
    monitor_manual_rotation(duration_sec=dur)
