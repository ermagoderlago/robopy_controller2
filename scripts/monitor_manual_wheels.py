import serial
import time
import json

def main():
    s = serial.Serial('/dev/motor_driver', 115200, timeout=1.0)
    time.sleep(0.5)
    s.reset_input_buffer()
    print("Reading encoders for 10 seconds. Move wheels by hand or test...")
    t_end = time.time() + 10.0
    while time.time() < t_end:
        raw = s.readline().decode('utf-8', errors='ignore').strip()
        if raw.startswith('{"T":1001'):
            d = json.loads(raw)
            print(f"odl: {d.get('odl'):6d} | odr: {d.get('odr'):6d} | v: {d.get('v')/3000.0:.2f}V", end='\r')
    print("\nDone.")
    s.close()

if __name__ == '__main__':
    main()
