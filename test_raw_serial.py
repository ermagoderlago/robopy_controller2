#!/usr/bin/env python3
import serial
import time
import sys

print("Opening serial port /dev/ttyUSB0 at 115200...")
try:
    ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.1)
    
    print("Resetting ESP32...")
    ser.dtr = True
    ser.rts = True
    time.sleep(0.1)
    ser.dtr = False
    ser.rts = False
    
    print("Listening for 5 seconds...")
    start_time = time.time()
    while time.time() - start_time < 5.0:
        data = ser.read(100)
        if data:
            print(f"Received {len(data)} bytes:")
            print(f"  HEX: {data.hex()}")
            print(f"  ASCII: {data.decode('utf-8', errors='replace')}")
        time.sleep(0.1)
except Exception as e:
    print(f"Error: {e}")
