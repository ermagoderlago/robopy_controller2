#!/usr/bin/env python3
import serial
import time
import json

def test_formats():
    try:
        s = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.1)
    except Exception as e:
        print(f"Error opening serial: {e}")
        return

    time.sleep(0.5)

    formats = [
        ("Format 1: T:1, L:0.6, R:0.6", '{"T":1,"L":0.6,"R":0.6}\n'),
        ("Format 2: T:1, L:-0.6, R:-0.6", '{"T":1,"L":-0.6,"R":-0.6}\n'),
        ("Format 3: T:1, L:200, R:200", '{"T":1,"L":200,"R":200}\n'),
        ("Format 4: T:1, L:-200, R:-200", '{"T":1,"L":-200,"R":-200}\n'),
    ]

    for name, cmd_str in formats:
        print(f"\n🧪 {name}")
        t0 = time.time()
        start_odl, start_odr = None, None
        while time.time() - t0 < 0.4:
            line = s.readline().decode('utf-8', errors='ignore').strip()
            if line.startswith('{') and '"odl"' in line:
                try:
                    d = json.loads(line)
                    start_odl = d.get('odl')
                    start_odr = d.get('odr')
                except Exception:
                    pass
        
        s.write(cmd_str.encode('utf-8'))
        t_end = time.time() + 0.8
        end_odl, end_odr = start_odl, start_odr
        while time.time() < t_end:
            line = s.readline().decode('utf-8', errors='ignore').strip()
            if line.startswith('{') and '"odl"' in line:
                try:
                    d = json.loads(line)
                    end_odl = d.get('odl')
                    end_odr = d.get('odr')
                except Exception:
                    pass
            time.sleep(0.02)
            
        s.write(b'{"T":1,"L":0.0,"R":0.0}\n')
        s.write(b'{"T":1,"L":0,"R":0}\n')
        time.sleep(0.5)
        
        d_odl = (end_odl - start_odl) if (end_odl is not None and start_odl is not None) else 0
        d_odr = (end_odr - start_odr) if (end_odr is not None and start_odr is not None) else 0
        print(f"   odl_0={start_odl}, odl_1={end_odl} (Δ={d_odl:+d}) | odr_0={start_odr}, odr_1={end_odr} (Δ={d_odr:+d})")

    s.close()

if __name__ == '__main__':
    test_formats()
