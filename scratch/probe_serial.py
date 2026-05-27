import serial
import time
import sys

port = '/dev/ttyACM0'
baud = 115200

print(f"Opening port {port} at {baud}...")
try:
    s = serial.Serial(port, baud, timeout=2)
    time.sleep(1)
    s.reset_input_buffer()
    
    # Send HEARTBEAT_REQ
    print("Sending: HEARTBEAT_REQ")
    s.write(b'HEARTBEAT_REQ\n')
    s.flush()
    
    # Read response
    resp = s.readline().decode('utf-8', errors='replace').strip()
    print(f"Response: '{resp}'")
    s.close()
    if resp == 'HEARTBEAT' or resp == 'PONG':
        print("SUCCESS: ReSpeaker is alive and responding correctly!")
        sys.exit(0)
    else:
        print("WARNING: Did not receive expected HEARTBEAT response.")
        sys.exit(1)
except Exception as e:
    print(f"ERROR connecting to ReSpeaker: {e}")
    sys.exit(2)
