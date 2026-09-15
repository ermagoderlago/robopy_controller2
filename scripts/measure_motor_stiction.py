#!/usr/bin/env python3
import serial
import json
import time
import threading

latest_odl = None
latest_odr = None
running = True

def reader(ser):
    global latest_odl, latest_odr, running
    while running:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line.startswith('{'):
                data = json.loads(line)
                if 'odl' in data and 'odr' in data:
                    latest_odl = data['odl']
                    latest_odr = data['odr']
        except Exception:
            pass

def main():
    global running, latest_odl, latest_odr
    ser = serial.Serial('/dev/motor_driver', 115200, timeout=0.1)
    time.sleep(1.0)
    ser.reset_input_buffer()
    
    # Disable ESP32 PID
    ser.write(b'{"T":133,"pid":0,"kp":0,"ki":0,"kd":0}\n')
    time.sleep(0.1)
    # Enable telemetry
    ser.write(b'{"T":131,"cmd":1}\n')
    time.sleep(0.2)
    
    th = threading.Thread(target=reader, args=(ser,), daemon=True)
    th.start()
    
    # Wait for initial telemetry
    t0 = time.time()
    while (latest_odl is None or latest_odr is None) and time.time() - t0 < 3.0:
        time.sleep(0.05)
        
    print(f"Initial ticks: odl={latest_odl}, odr={latest_odr}")
    
    # CPR = 657 ticks/rev, radius = 0.0335m -> meters_per_tick = 2*pi*r / 657 = 0.0003204m
    m_per_tick = (2.0 * 3.14159265 * 0.0335) / 657.0
    
    duties = [-0.12, -0.15, -0.18, -0.20, -0.22, -0.25]
    print(f"{'Duty':>6} | {'Left Ticks':>10} | {'Right Ticks':>11} | {'L Speed (m/s)':>13} | {'R Speed (m/s)':>13}")
    print("-" * 65)
    
    for d in duties:
        l0 = latest_odl
        r0 = latest_odr
        
        cmd = json.dumps({"T": 1, "L": d, "R": d}, separators=(',', ':')) + "\n"
        t_start = time.time()
        while time.time() - t_start < 0.6:
            ser.write(cmd.encode('utf-8'))
            time.sleep(0.05)
            
        ser.write(b'{"T":1,"L":0.0,"R":0.0}\n')
        time.sleep(0.1)
        ser.write(b'{"T":1,"L":0.0,"R":0.0}\n')
        time.sleep(0.2)
        
        l1 = latest_odl
        r1 = latest_odr
        dl = l1 - l0
        dr = r1 - r0
        vl = (dl * m_per_tick) / 0.6
        vr = (dr * m_per_tick) / 0.6
        print(f"{d:>6.2f} | {dl:>10} | {dr:>11} | {vl:>13.3f} | {vr:>13.3f}", flush=True)
        time.sleep(0.5)
        
    running = False
    ser.write(b'{"T":1,"L":0.0,"R":0.0}\n')
    ser.close()

if __name__ == '__main__':
    main()
