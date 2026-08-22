#!/usr/bin/env python3
import serial
import time
import json

def test_right_wheel(duty=0.30, duration=4.0):
    s = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.1)
    time.sleep(0.3)
    
    # 1. Baseline
    odl_0, odr_0 = None, None
    t0 = time.time()
    while time.time() - t0 < 0.6:
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{') and '"odl"' in line:
            try:
                d = json.loads(line)
                odl_0 = d.get('odl')
                odr_0 = d.get('odr')
            except Exception:
                pass

    print(f"\n🚀 AVVIO RUOTA DESTRA (duty={duty}, durata={duration}s)")
    print(f"   Tick iniziali: odl = {odl_0}, odr = {odr_0}")

    # Compact JSON for right wheel
    cmd_bytes = f'{{"T":1,"L":0.0,"R":{duty:.3f}}}\n'.encode('utf-8')
    
    t_start = time.time()
    while time.time() - t_start < duration:
        s.write(cmd_bytes)
        time.sleep(0.05)

    # Stop
    s.write(b'{"T":1,"L":0.0,"R":0.0}\n')
    time.sleep(0.5)

    # Read end
    odl_1, odr_1 = odl_0, odr_0
    t0 = time.time()
    while time.time() - t0 < 0.6:
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{') and '"odl"' in line:
            try:
                d = json.loads(line)
                odl_1 = d.get('odl')
                odr_1 = d.get('odr')
            except Exception:
                pass

    s.close()

    d_odl = (odl_1 - odl_0) if (odl_0 is not None and odl_1 is not None) else 0
    d_odr = (odr_1 - odr_0) if (odr_0 is not None and odr_1 is not None) else 0

    print(f"\n⏹️ TEST RUOTA DESTRA COMPLETATO!")
    print(f"   Tick finali:   odl = {odl_1}, odr = {odr_1}")
    print(f"   DELTA TICKS:   Δ(odl) = {d_odl:+d}, Δ(odr) = {d_odr:+d}")
    print("="*60)

if __name__ == '__main__':
    test_right_wheel(duty=0.30, duration=4.0)
