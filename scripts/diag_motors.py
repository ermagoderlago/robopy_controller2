#!/usr/bin/env python3
import serial
import time
import json

s = serial.Serial('/dev/motor_driver', 115200, timeout=0.1)
s.dtr = True; s.rts = True
time.sleep(0.1)
s.dtr = False; s.rts = False
print("Waiting 3.5s for ESP32 full boot...")
time.sleep(3.5)
s.reset_input_buffer()

print("Disabling closed-loop PID (forcing open loop)...")
for _ in range(10):
    s.write(b'{"T":133,"pid":0}\n')
    time.sleep(0.1)
    line = s.readline().decode('utf-8', errors='ignore').strip()
    if line.startswith('{"T":1001') and '"pid":0' in line:
        print(f"Verified open-loop mode: {line}")
        break

def test_cmd(desc, cmd_str, duration=1.0):
    print(f"\n--- {desc} ---")
    s.reset_input_buffer()
    t0 = time.time()
    while time.time() - t0 < duration:
        s.write(cmd_str.encode())
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{"T":1001'):
            print(line)
        time.sleep(0.05)
    s.write(b'{"T":1,"L":0.0,"R":0.0}\n')
    time.sleep(0.5)

test_cmd("Left Open Loop +0.60 (PWM 153)", '{"T":1,"L":0.60,"R":0.0}\n', 1.0)
test_cmd("Left Open Loop +0.80 (PWM 204)", '{"T":1,"L":0.80,"R":0.0}\n', 1.0)
test_cmd("Left Open Loop +1.00 (PWM 255)", '{"T":1,"L":1.00,"R":0.0}\n', 1.0)

test_cmd("Left Open Loop -0.60 (PWM -153)", '{"T":1,"L":-0.60,"R":0.0}\n', 1.0)
test_cmd("Left Open Loop -0.80 (PWM -204)", '{"T":1,"L":-0.80,"R":0.0}\n', 1.0)
test_cmd("Left Open Loop -1.00 (PWM -255)", '{"T":1,"L":-1.00,"R":0.0}\n', 1.0)

s.write(b'{"T":1,"L":0.0,"R":0.0}\n')
s.close()
print("\nOpen loop stiction search finished.")
