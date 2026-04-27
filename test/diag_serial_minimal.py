#!/usr/bin/env python3
import serial
import time
import sys

def test_baud(port, baud):
    print(f"\n--- Testing {port} at {baud} baud (DTR/RTS Manual) ---")
    try:
        # Apriamo senza handshake hardware
        ser = serial.Serial(
            port=port, 
            baudrate=baud, 
            timeout=1, 
            write_timeout=2,
            rtscts=False,
            dsrdtr=False
        )
        
        # Sequenza di sblocco specifica per ESP32S3 USB-JTAG
        ser.dtr = False
        ser.rts = False
        time.sleep(0.1)
        ser.dtr = True # Spesso necessario per abilitare i dati
        ser.rts = False
        time.sleep(0.1)
        
        print(f"Port opened. DTR={ser.dtr}, RTS={ser.rts}")
        
        # Pulisci buffer
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
        # Invia un newline per pulire eventuali residui nel parser dell'ESP32
        ser.write(b'\n')
        ser.flush()
        time.sleep(0.1)

        # Test 1: PING
        print("Sending PING...")
        ser.write(b'PING\n')
        ser.flush()
        
        # Leggi più righe in caso di spazzatura iniziale
        for _ in range(5):
            response = ser.readline().decode('utf-8', errors='replace').strip()
            if response:
                print(f"Response: '{response}'")
                if "PONG" in response or "OK" in response:
                    break
        
        # Test 2: LED_EFFECT
        print("Sending LED_EFFECT:THINKING...")
        ser.write(b'LED_EFFECT:THINKING\n')
        ser.flush()
        
        response = ser.readline().decode('utf-8', errors='replace').strip()
        print(f"Response: '{response}'")
        
        ser.close()
        return True
    except serial.SerialTimeoutException:
        print("Error: Serial Write Timeout (STILL BUSY)")
    except Exception as e:
        print(f"Error: {e}")
    return False

if __name__ == "__main__":
    # Testiamo prima a 115200 (che è quello del YAML)
    if not test_baud('/dev/ttyACM0', 115200):
        print("\nFallback a 921600...")
        test_baud('/dev/ttyACM0', 921600)
