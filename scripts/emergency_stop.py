#!/usr/bin/env python3
import serial
import time

def emergency_stop():
    try:
        s = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.2)
        time.sleep(0.1)
        for _ in range(10):
            s.write(b'{"T":1,"L":0.0,"R":0.0}\n')
            s.write(b'{"T":1,"L":0,"R":0}\n')
            time.sleep(0.02)
        s.flush()
        s.close()
        print("🛑 EMERGENCY STOP: ZERO PWM SENT TO ESP32 MOTORS SUCCESSFULLY")
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == '__main__':
    emergency_stop()
