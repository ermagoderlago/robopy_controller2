#!/usr/bin/env python3
import serial
import time
import json

s = serial.Serial('/dev/motor_driver', 115200, timeout=0.1)
s.dtr = True; s.rts = True
time.sleep(0.1)
s.dtr = False; s.rts = False
time.sleep(2.5)
s.reset_input_buffer()

def test_motor(name, cmd_bytes):
    print(f"\n--- Testing {name} ---")
    s.write(b'{"T":133,"pid":0}\n') # test in open-loop to isolate pure hardware response
    time.sleep(0.1)
    t0 = time.time()
    last_l, last_r = 0, 0
    while time.time() - t0 < 0.8:
        s.write(cmd_bytes)
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{'):
            try:
                d = json.loads(line)
                last_l = d['odl']
                last_r = d['odr']
                print(f"  [{name}] odl={last_l} odr={last_r}")
            except:
                pass
        time.sleep(0.05)
    s.write(b'{"T":1,"L":0.0,"R":0.0}\n')
    time.sleep(0.4)
    print(f"Result {name}: odl={last_l}, odr={last_r}")

test_motor('L_POS (+0.50)', b'{"T":1,"L":0.50,"R":0.0}\n')
time.sleep(1.0)
test_motor('L_NEG (-0.50)', b'{"T":1,"L":-0.50,"R":0.0}\n')
time.sleep(1.0)
test_motor('R_POS (+0.50)', b'{"T":1,"L":0.0,"R":0.50}\n')
time.sleep(1.0)
test_motor('R_NEG (-0.50)', b'{"T":1,"L":0.0,"R":-0.50}\n')

s.write(b'{"T":1,"L":0.0,"R":0.0}\n')
s.close()
print("\nIndividual motor diagnosis completed.")
