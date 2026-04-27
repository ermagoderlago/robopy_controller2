#!/usr/bin/env python3
"""
Test di comunicazione seriale diretta con ReSpeaker Lite (USB / XIAO ESP32S3).
Questo script bypassa ROS 2 e tenta di parlare direttamente al firmware.
"""

import serial
import time
import sys

def test_serial(port='/dev/ttyACM0', baud=921600):
    print(f"--- Tentativo di connessione su {port} a {baud} baud ---")
    try:
        with serial.Serial(port, baud, timeout=1) as ser:
            print(f"Porta {port} aperta con successo.")
            
            # Attendi un momento per il reset (alcuni ESP si resettano all'apertura)
            time.sleep(1)
            
            effects = ["LISTENING", "THINKING", "SUCCESS", "ERROR", "IDLE"]
            
            for eff in effects:
                cmd = f"LED_EFFECT:{eff}\n"
                print(f"Invio comando: {cmd.strip()}")
                ser.write(cmd.encode('utf-8'))
                
                # Leggi eventuali risposte (il firmware custom risponde ad alcuni comandi)
                time.sleep(0.5)
                if ser.in_waiting:
                    resp = ser.read_all().decode('utf-8', errors='replace')
                    print(f"Risposta ricevuta: {resp.strip()}")
                
                time.sleep(1.5)
                
            print("Test completato.")
            
    except serial.SerialException as e:
        print(f"Errore seriale: {e}")
    except Exception as e:
        print(f"Errore imprevisto: {e}")

if __name__ == "__main__":
    # Permette di passare la porta come argomento
    port = sys.argv[1] if len(sys.argv) > 1 else '/dev/ttyACM0'
    
    print("Test ReSpeaker Lite Direct Serial")
    print("---------------------------------")
    print("1. Test Baudrate Custom (921600) - Usato dal firmware ESPHome del robot")
    test_serial(port, 921600)
    
    print("\n2. Test Baudrate Standard (115200) - Comune in firmware generici")
    test_serial(port, 115200)
    
    print("\nSe nessuno dei due funziona, il firmware installato potrebbe non supportare")
    print("il protocollo 'LED_EFFECT:'.")
