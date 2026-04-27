#!/usr/bin/env python3
"""
Script per identificare quale firmware è installato sul ReSpeaker Lite.
Tenta di comunicare sia con il protocollo ESPHome (custom) che con quello standard (se presente).
"""

import serial
import time
import sys

def identify_firmware(port='/dev/ttyACM0'):
    print(f"--- Vediamo chi c'è su {port} ---")
    
    # 1. Prova a 921600 (ESPHome custom)
    try:
        with serial.Serial(port, 921600, timeout=2) as ser:
            print("[INFO] Invio 'HEARTBEAT_REQ' (Protocollo ESPHome Custom)...")
            ser.write(b"HEARTBEAT_REQ\n")
            time.sleep(0.5)
            if ser.in_waiting:
                resp = ser.read_all().decode('utf-8', errors='replace')
                if "HEARTBEAT" in resp:
                    print(f"\n[RISULTATO] FIRMWARE CUSTOM RILEVATO! (Ricevuto: {resp.strip()})")
                    print("Il codice del robot dovrebbe funzionare correttamente.")
                    return
                else:
                    print(f"[DEBUG] Ricevuto dati non riconosciuti a 921600: {resp.strip()}")
            else:
                print("[INFO] Nessuna risposta a 921600.")
    except Exception as e:
        print(f"[ERRORE] Impossibile aprire porta a 921600: {e}")

    # 2. Prova a 115200 (Standard Seeed/Arduino)
    try:
        with serial.Serial(port, 115200, timeout=2) as ser:
            print("\n[INFO] Invio 'STATO' e attesa log di boot (Protocollo Standard/Generic)...")
            # Prova a resettare via DTR/RTS per vedere i log di boot
            ser.dtr = False
            time.sleep(0.1)
            ser.dtr = True
            
            time.sleep(1.0)
            if ser.in_waiting:
                resp = ser.read_all().decode('utf-8', errors='replace')
                print(f"[LOG RILEVATI] {resp.strip()}")
                if "ESP-ROM" in resp or "ESP32S3" in resp:
                    print("\n[RISULTATO] BOOTLOADER GENERICO RILEVATO.")
                    print("Sembra esserci un firmware standard o un semplice Arduino sketch.")
                else:
                    print("\n[RISULTATO] Dati rilevati ma non identificati come Marcus-firmware.")
            else:
                print("[INFO] Nessun log di boot ricevuto a 115200.")
    except Exception as e:
        print(f"[ERRORE] Impossibile aprire porta a 115200: {e}")

    print("\n[CONCLUSIONE] Non è stato rilevato il firmware Custom Marcus.")
    print("Probabilmente hai il firmware standard di Seeed che non supporta i LED via ROS 2.")
    print("Per risolvere, devi flashare il firmware custom usando:")
    print("  ./scripts/flash_respeaker.sh")

if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else '/dev/ttyACM0'
    identify_firmware(port)
