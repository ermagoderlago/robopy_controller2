#!/usr/bin/env python3
import serial
import time
import json

s = serial.Serial('/dev/motor_driver', 115200, timeout=1.0)
s.dtr = True
s.rts = True
time.sleep(0.1)
s.dtr = False
s.rts = False
time.sleep(2.5)
s.reset_input_buffer()

print("--- Telemetry before PID command ---")
for _ in range(5):
    line = s.readline().decode('utf-8', errors='ignore').strip()
    if line:
        print("RX:", line)

print("\n--- Sending PID configuration (T:133) ---")
s.write(b'{"T":133,"pid":1,"kp":3.2,"ki":0.22,"kd":0.04}\n')
s.flush()
time.sleep(0.2)

print("\n--- Telemetry after PID command ---")
for _ in range(5):
    line = s.readline().decode('utf-8', errors='ignore').strip()
    if line:
        print("RX:", line)

s.close()
