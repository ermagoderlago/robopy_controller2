#!/usr/bin/env python3
import serial
import time
import json

def test_channels():
    s = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.1)
    time.sleep(0.3)

    tests = [
        ("TEST A: L=0.5, R=0.5", 0.5, 0.5),
        ("TEST B: L=0.5, R=0.0", 0.5, 0.0),
        ("TEST C: L=0.0, R=0.5", 0.0, 0.5),
    ]

    for label, l, r in tests:
        print(f"\n--- {label} (2 secondi) ---")
        t0 = time.time()
        odl_0, odr_0 = None, None
        while time.time() - t0 < 0.4:
            line = s.readline().decode('utf-8', errors='ignore').strip()
            if line.startswith('{') and '"odl"' in line:
                try:
                    d = json.loads(line)
                    odl_0, odr_0 = d.get('odl'), d.get('odr')
                except Exception:
                    pass
                    
        cmd = json.dumps({"T": 1, "L": l, "R": r}) + "\n"
        t_end = time.time() + 2.0
        while time.time() < t_end:
            s.write(cmd.encode())
            time.sleep(0.05)
            
        s.write(b'{"T":1,"L":0.0,"R":0.0}\n')
        time.sleep(0.5)

        t0 = time.time()
        odl_1, odr_1 = odl_0, odr_0
        while time.time() - t0 < 0.4:
            line = s.readline().decode('utf-8', errors='ignore').strip()
            if line.startswith('{') and '"odl"' in line:
                try:
                    d = json.loads(line)
                    odl_1, odr_1 = d.get('odl'), d.get('odr')
                except Exception:
                    pass
        d_l = (odl_1 - odl_0) if (odl_0 and odl_1) else 0
        d_r = (odr_1 - odr_0) if (odr_0 and odr_1) else 0
        print(f"   Risultato: Δ(odl) = {d_l:+d}, Δ(odr) = {d_r:+d}")

    s.close()

if __name__ == '__main__':
    test_channels()
