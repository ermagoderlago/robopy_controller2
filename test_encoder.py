#!/usr/bin/env python3
import time
import sys
import json

print("==================================================")
print(" 🔍 DIAGNOSTIC TOOL: ENCODER & MOTOR TESTER")
print("==================================================")

mode = None
if len(sys.argv) > 1:
    if sys.argv[1] in ['buildhat', 'bh']:
        mode = 'buildhat'
    elif sys.argv[1] in ['waveshare', 'ws']:
        mode = 'waveshare'

if not mode:
    print("Scegli la scheda a cui e' connesso il motore:")
    print("1 - Waveshare General Driver (ESP32 via /dev/ttyUSB0)")
    print("2 - Raspberry Pi Build HAT (Lego ports A/B/C/D)")
    try:
        choice = input("Inserisci 1 o 2: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nEsco.")
        sys.exit(0)
    if choice == '1':
        mode = 'waveshare'
    elif choice == '2':
        mode = 'buildhat'
    else:
        print("Scelta non valida. Esco.")
        sys.exit(1)

if mode == 'waveshare':
    import serial
    port = '/dev/ttyUSB0'
    baud = 115200
    print(f"\n🔌 Connessione a Waveshare ESP32 su {port} ({baud} baud)...")
    try:
        ser = serial.Serial(port, baud, timeout=1.0)
        # Reset ESP32
        print("🔄 Esecuzione sequenza di reset DTR/RTS...")
        ser.dtr = True
        ser.rts = True
        time.sleep(0.1)
        ser.dtr = False
        ser.rts = False
        print("⏳ Attesa avvio ESP32 (3s)...")
        time.sleep(3.0)
        
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
        # Send STOP command immediately
        print("🛑 Invio comando di stop: {'T': 1, 'L': 0.0, 'R': 0.0}")
        ser.write(b'{"T":1,"L":0.0,"R":0.0}\n')
        
        # Enable telemetry
        print("📈 Abilitazione telemetria...")
        ser.write(b'{"T":131,"cmd":1}\n')
        ser.write(b'{"T":1001}\n')
        
        print("\n🟢 Lettura in corso. Gira le ruote manualmente per verificare gli encoder.")
        print("Premi CTRL+C per terminare.\n")
        
        while True:
            line = ser.readline()
            if line:
                try:
                    line_str = line.decode('utf-8', errors='ignore').strip()
                    if line_str.startswith('{'):
                        data = json.loads(line_str)
                        if data.get('T') == 1001:
                            odl = data.get('odl')
                            odr = data.get('odr')
                            v = data.get('v')
                            print(f"Ticks -> L: {odl} | R: {odr} | Batt: {v}mV", end='\r')
                        else:
                            print(f"Raw: {line_str}")
                except Exception as e:
                    print(f"\nErrore di parsing: {e}")
            else:
                time.sleep(0.01)
                
    except KeyboardInterrupt:
        print("\n🛑 Programma interrotto dall'utente.")
    except Exception as e:
        print(f"\n❌ Errore seriale: {e}")

elif mode == 'buildhat':
    try:
        from buildhat import Hat, Motor
    except ImportError:
        try:
            from buildhat_alternative import Motor
        except ImportError:
            print("❌ Errore: libreria buildhat o buildhat_alternative non trovata!")
            sys.exit(1)
            
    print("\n🎩 Inizializzazione Build HAT...")
    ports = ['A', 'B', 'C', 'D']
    motors = {}
    for p in ports:
        try:
            print(f"Inizializzazione porta {p}...")
            m = Motor(p)
            motors[p] = m
            print(f"  ✅ Rilevato motore su porta {p}!")
        except Exception as e:
            pass
            
    if not motors:
        print("❌ Nessun motore rilevato sul Build HAT.")
        sys.exit(1)
        
    print("\n🟢 Lettura in corso per i motori rilevati.")
    print("Gira le ruote manualmente per verificare gli encoder.")
    print("Premi CTRL+C per terminare.\n")
    
    try:
        while True:
            output_parts = []
            for p, m in motors.items():
                try:
                    pos = m.get_position() if hasattr(m, 'get_position') else m.get_aposition()
                    output_parts.append(f"Porta {p}: {pos}")
                except Exception as e:
                    output_parts.append(f"Porta {p}: errore {e}")
            print(" | ".join(output_parts), end='\r')
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n🛑 Programma interrotto dall'utente.")
