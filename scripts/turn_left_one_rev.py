#!/usr/bin/env python3
"""
turn_left_one_rev.py - Fa compiere 1 GIRO controllato alla Ruota Sinistra a circuito chiuso
"""
import serial
import time
import json

def turn_left_wheel_one_rev(target_ticks=42, duty=0.20):
    s = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.05)
    time.sleep(0.3)
    
    # Baseline
    odl_0, odr_0 = None, None
    t0 = time.time()
    while time.time() - t0 < 0.6:
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{') and '"odl"' in line:
            try:
                d = json.loads(line)
                odl_0, odr_0 = d.get('odl'), d.get('odr')
            except Exception:
                pass

    print(f"\n🎯 AVVIO ROTAZIONE RUOTA SINISTRA: TARGET 1 GIRO (Cutoff: {target_ticks} ticks)")
    print(f"   Tick iniziali: odl = {odl_0}, odr = {odr_0}")

    cmd_bytes = f'{{"T":1,"L":{duty:.3f},"R":0.0}}\n'.encode('utf-8')
    stop_bytes = b'{"T":1,"L":0.0,"R":0.0}\n'

    t_start = time.time()
    last_odl = odl_0
    
    while time.time() - t_start < 3.0:
        s.write(cmd_bytes)
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{') and '"odl"' in line:
            try:
                d = json.loads(line)
                cur_odl = d.get('odl')
                if cur_odl is not None and odl_0 is not None:
                    delta_ticks = abs(cur_odl - odl_0)
                    last_odl = cur_odl
                    if delta_ticks >= target_ticks:
                        break
            except Exception:
                pass
        time.sleep(0.01)

    for _ in range(5):
        s.write(stop_bytes)
        time.sleep(0.01)
    s.flush()
    time.sleep(0.5)

    t0 = time.time()
    final_odl = last_odl
    while time.time() - t0 < 0.5:
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{') and '"odl"' in line:
            try:
                d = json.loads(line)
                final_odl = d.get('odl')
            except Exception:
                pass

    s.close()
    total_delta = abs(final_odl - odl_0) if (final_odl is not None and odl_0 is not None) else 0

    print(f"\n⏹️ TEST RUOTA SINISTRA 1 GIRO COMPLETATO!")
    print(f"   Tick iniziali: {odl_0} -> Tick finali: {final_odl}")
    print(f"   Delta Tick effettivi: {total_delta} ticks")
    print("="*60)

if __name__ == '__main__':
    turn_left_wheel_one_rev(target_ticks=42, duty=0.20)
