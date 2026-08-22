#!/usr/bin/env python3
import serial
import time
import json

def test_serial():
    try:
        s = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.1)
    except Exception as e:
        print(f"Error opening serial: {e}")
        return

    time.sleep(0.5)
    print("Reading baseline telemetry:")
    for _ in range(5):
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line:
            print("  RAW:", line)
        time.sleep(0.05)

    print("\nSending Forward Command (T:1, L: 0.15, R: 0.15):")
    cmd = json.dumps({"T": 1, "L": 0.15, "R": 0.15}) + "\n"
    s.write(cmd.encode('utf-8'))
    t_end = time.time() + 1.0
    while time.time() < t_end:
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line and line.startswith('{'):
            print("  FWD:", line)
        time.sleep(0.05)

    print("\nStopping:")
    stop_cmd = json.dumps({"T": 1, "L": 0.0, "R": 0.0}) + "\n"
    s.write(stop_cmd.encode('utf-8'))
    time.sleep(0.5)
    s.close()

if __name__ == '__main__':
    test_serial()
