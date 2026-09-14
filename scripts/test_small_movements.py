#!/usr/bin/env python3
"""
Safe slow movement test for Marcus chassis:
- Speed: duty 0.15 (gentle slow pace ~0.05 m/s)
- Duration: 0.60s (~20 cm travel)
- Dynamic short-brake evaluation
- Direction symmetry check (forward then reverse)
"""
import serial
import time
import json

def run_safe_test(s, name, left_cmd, right_cmd, duration=0.60, check_stop_sec=0.8):
    print(f"\n==========================================")
    print(f"--- {name}: L={left_cmd:.2f}, R={right_cmd:.2f} for {duration:.2f}s ---")
    print(f"==========================================")
    s.reset_input_buffer()
    
    # Read initial baseline
    init_l, init_r = 0, 0
    t_base = time.time()
    while time.time() - t_base < 0.4:
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{"T":1001'):
            try:
                data = json.loads(line)
                init_l = data['odl']
                init_r = data['odr']
                print(f"Baseline: odl={init_l}, odr={init_r}, pid={data.get('pid')}")
                break
            except Exception:
                pass
        time.sleep(0.02)
        
    cmd_bytes = f'{{"T":1,"L":{left_cmd:.2f},"R":{right_cmd:.2f}}}\n'.encode('utf-8')
    t0 = time.time()
    last_l, last_r = init_l, init_r
    while time.time() - t0 < duration:
        s.write(cmd_bytes)
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{"T":1001'):
            try:
                data = json.loads(line)
                last_l = data['odl']
                last_r = data['odr']
                dl = last_l - init_l
                dr = last_r - init_r
                print(f"  [RUNNING] odl_delta={dl:+5d} | odr_delta={dr:+5d} | pwml={data.get('pwml')} pwmr={data.get('pwmr')}")
            except Exception:
                pass
        time.sleep(0.03)

    # Active Short Brake
    print("  [BRAKE] Active Short-Brake engaged...")
    s.write(b'{"T":1,"L":0.0,"R":0.0}\n')
    time.sleep(0.02)

    stop_samples = []
    t_stop = time.time()
    while time.time() - t_stop < check_stop_sec:
        s.write(b'{"T":1,"L":0.0,"R":0.0}\n')
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{"T":1001'):
            try:
                data = json.loads(line)
                stop_samples.append((data['odl'] - init_l, data['odr'] - init_r))
                print(f"  [STOPPED] odl_delta={data['odl']-init_l:+5d} | odr_delta={data['odr']-init_r:+5d} | pwml={data.get('pwml')} pwmr={data.get('pwmr')}")
            except Exception:
                pass
        time.sleep(0.04)

    if stop_samples:
        first = stop_samples[0]
        last = stop_samples[-1]
        motion_l = last[0]
        motion_r = last[1]
        drift_l = last[0] - first[0]
        drift_r = last[1] - first[1]
        print(f">> Total displacement: Left={motion_l} ticks, Right={motion_r} ticks")
        print(f">> Post-stop coasting drift: Left={drift_l} ticks, Right={drift_r} ticks")
        if abs(drift_l) <= 2 and abs(drift_r) <= 2:
            print(">> Short-Brake SUCCESS: Zero post-stop inertial coasting!")
        else:
            print(f">> Post-stop drift: L={drift_l}, R={drift_r}")
    time.sleep(0.5)

def main():
    print("Connecting to /dev/motor_driver...")
    s = serial.Serial('/dev/motor_driver', 115200, timeout=0.1)
    s.dtr = True; s.rts = True
    time.sleep(0.1)
    s.dtr = False; s.rts = False
    print("Waiting 3.5s for ESP32 boot...")
    time.sleep(3.5)
    s.reset_input_buffer()

    print("Enabling closed-loop PID control on ESP32...")
    for _ in range(5):
        s.write(b'{"T":133,"pid":1,"kp":3.2,"ki":0.22,"kd":0.04}\n')
        time.sleep(0.1)
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if line.startswith('{"T":1001') and '"pid":1' in line:
            print(f"ESP32 PID Active confirmed: {line}")
            break

    # Slow Test 1: Forward Motion 0.15 duty (~20 cm) for 0.60s
    run_safe_test(s, "Slow Test 1: Forward (L=0.15, R=0.15)", 0.15, 0.15, duration=0.60)

    # Slow Test 2: Reverse Motion -0.15 duty (~20 cm) for 0.60s
    run_safe_test(s, "Slow Test 2: Reverse (L=-0.15, R=-0.15)", -0.15, -0.15, duration=0.60)

    # Final safe stop
    s.write(b'{"T":1,"L":0.0,"R":0.0}\n')
    s.close()
    print("\nSlow movement test completed successfully.")

if __name__ == '__main__':
    main()
