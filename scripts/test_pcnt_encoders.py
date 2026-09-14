import serial
import time
import json

def test_motor(s, name, left_cmd, right_cmd, duration=2.0):
    print(f"\n--- Testing {name} (L={left_cmd}, R={right_cmd}, duration={duration}s) ---")
    s.reset_input_buffer()
    time.sleep(0.1)

    init_l, init_r = 0, 0
    for _ in range(10):
        raw = s.readline().decode('utf-8', errors='ignore').strip()
        if raw.startswith('{"T":1001'):
            d = json.loads(raw)
            init_l, init_r = d.get('odl'), d.get('odr')
            break

    cmd = f'{{"T":1,"L":{left_cmd:.2f},"R":{right_cmd:.2f}}}\n'.encode()
    s.write(cmd)
    t_end = time.time() + duration
    while time.time() < t_end:
        raw = s.readline().decode('utf-8', errors='ignore').strip()
        if raw.startswith('{"T":1001'):
            d = json.loads(raw)
            print(f"odl: {d.get('odl')}, odr: {d.get('odr')}", end='\r')

    s.write(b'{"T":1,"L":0.00,"R":0.00}\n')
    time.sleep(0.3)

    s.reset_input_buffer()
    final_l, final_r = 0, 0
    for _ in range(10):
        raw = s.readline().decode('utf-8', errors='ignore').strip()
        if raw.startswith('{"T":1001'):
            d = json.loads(raw)
            final_l, final_r = d.get('odl'), d.get('odr')
            break

    dl = final_l - init_l
    dr = final_r - init_r
    print(f"\nResult {name} -> delta_odl: {dl}, delta_odr: {dr}")
    return dl, dr

def main():
    s = serial.Serial('/dev/motor_driver', 115200, timeout=1.0)
    time.sleep(0.5)

    # Test Channel L (Left motor: 30% for 2.0s)
    test_motor(s, "Channel L (30% 2.0s)", 0.30, 0.00, 2.0)
    time.sleep(1.0)

    # Test Channel R (Right motor: 30% for 2.0s)
    test_motor(s, "Channel R (30% 2.0s)", 0.00, 0.30, 2.0)

    s.close()

if __name__ == '__main__':
    main()
