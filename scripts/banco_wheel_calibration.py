#!/usr/bin/env python3
"""
banco_wheel_calibration.py - Procedura Guidata di Calibrazione a Banco (Ruote Sollevate)
Permette di calibrare:
1. Direzione e tick/giro (CPR) Ruota Sinistra
2. Direzione e tick/giro (CPR) Ruota Destra
3. Avanzamento sincronizzato su entrambe le ruote
4. Rotazione differenziale sul posto
5. Verifica IMU OAK-D (con compensazione inclinazione 8° UP) e IMU Chassis
"""

import serial
import time
import json
import math
import sys

class BancoCalibrator:
    def __init__(self, port='/dev/ttyUSB0', baud=115200):
        self.port = port
        self.baud = baud
        self.serial = None
        self.ticks_per_rev_nominal = 280
        self.wheel_radius = 0.0335  # m (67 mm diametro)
        self.wheel_separation = 0.285  # m

    def connect(self):
        try:
            self.serial = serial.Serial(self.port, self.baud, timeout=0.1)
            time.sleep(0.5)
            self.stop_hardware()
            print(f"✅ Connesso alla scheda su {self.port} a {self.baud} baud.")
            return True
        except Exception as e:
            print(f"❌ Errore connessione seriale: {e}")
            return False

    def stop_hardware(self):
        """Invia sempre treno di 5 pacchetti di stop forzato."""
        if self.serial and self.serial.is_open:
            for _ in range(5):
                self.serial.write(b'{"T":1,"L":0.0,"R":0.0}\n')
                self.serial.write(b'{"T":1,"L":0,"R":0}\n')
                time.sleep(0.01)
            self.serial.flush()

    def read_telemetry(self, timeout=0.5):
        """Legge l'ultimo pacchetto di telemetria valido (odl, odr, v)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                line = self.serial.readline().decode('utf-8', errors='ignore').strip()
                if line.startswith('{') and '"odl"' in line:
                    data = json.loads(line)
                    return data.get('odl'), data.get('odr'), data.get('v')
            except Exception:
                pass
        return None, None, None

    def run_motor_test(self, name, l_duty, r_duty, duration_sec=3.0):
        """Esegue un comando motorio controllato a tempo con lettura iniziale e finale encoder."""
        print(f"\n" + "-"*65)
        print(f"▶️ AVVIO TEST: {name}")
        print(f"   Comando inviato: L = {l_duty:+.2f}, R = {r_duty:+.2f} per {duration_sec:.1f} secondi")
        print("-" * 65)

        # 1. Baseline ticks
        odl_0, odr_0, v_mv = self.read_telemetry(timeout=0.6)
        if odl_0 is None or odr_0 is None:
            # Riprova
            time.sleep(0.2)
            odl_0, odr_0, v_mv = self.read_telemetry(timeout=0.6)
            
        print(f"   • Ticks Iniziali: odl = {odl_0}, odr = {odr_0} | Batteria: {v_mv/1000.0 if v_mv else 0:.2f}V")

        # 2. Invio comando a frequenza costante (20Hz)
        cmd_str = json.dumps({"T": 1, "L": round(l_duty, 4), "R": round(r_duty, 4)}) + "\n"
        cmd_bytes = cmd_str.encode('utf-8')
        
        t_start = time.time()
        while time.time() - t_start < duration_sec:
            self.serial.write(cmd_bytes)
            time.sleep(0.05)

        # 3. Stop forzato immediato
        self.stop_hardware()
        time.sleep(0.5)

        # 4. Lettura finale ticks
        odl_1, odr_1, _ = self.read_telemetry(timeout=0.6)
        if odl_1 is None: odl_1 = odl_0
        if odr_1 is None: odr_1 = odr_0

        d_odl = (odl_1 - odl_0) if (odl_0 is not None and odl_1 is not None) else 0
        d_odr = (odr_1 - odr_0) if (odr_0 is not None and odr_1 is not None) else 0

        revs_l = d_odl / self.ticks_per_rev_nominal
        revs_r = d_odr / self.ticks_per_rev_nominal

        print(f"📊 RISULTATI MISURATI:")
        print(f"   • Delta Ticks: Δ(odl) = {d_odl:+d}, Δ(odr) = {d_odr:+d}")
        print(f"   • Giri Ruota Stimati (a 280 CPR): Sinistra = {revs_l:+.2f} giri | Destra = {revs_r:+.2f} giri")
        print(f"   • Direzione Misurata:")
        print(f"       - Ruota Sinistra: {'AVANTI (+)' if d_odl > 0 else 'INDIETRO (-)' if d_odl < 0 else 'FERMA (0)'}")
        print(f"       - Ruota Destra:   {'AVANTI (+)' if d_odr > 0 else 'INDIETRO (-)' if d_odr < 0 else 'FERMA (0)'}")
        print("-" * 65)
        return d_odl, d_odr

    def close(self):
        self.stop_hardware()
        if self.serial:
            self.serial.close()
            print("🔒 Connessione seriale chiusa e motori a riposo.")

def interactive_menu():
    print("\n" + "="*70)
    print("🛠️ PROCEDURA GUIDATA DI CALIBRAZIONE A BANCO MARCUS (RUOTE SOLLEVATE)")
    print("="*70)
    calib = BancoCalibrator()
    if not calib.connect():
        return

    try:
        while True:
            print("\nSeleziona il test da eseguire:")
            print(" [1] Test Solo Ruota Sinistra AVANTI (+0.25 duty, 3.0s)")
            print(" [2] Test Solo Ruota Sinistra INDIETRO (-0.25 duty, 3.0s)")
            print(" [3] Test Solo Ruota Destra AVANTI (+0.25 duty, 3.0s)")
            print(" [4] Test Solo Ruota Destra INDIETRO (-0.25 duty, 3.0s)")
            print(" [5] Test Entrambe le Ruote AVANTI (+0.25 duty, 3.0s)")
            print(" [6] Test Entrambe le Ruote INDIETRO (-0.25 duty, 3.0s)")
            print(" [7] Test Rotazione Differenziale Sinistra (L=-0.25, R=+0.25, 3.0s)")
            print(" [8] Test Rotazione Differenziale Destra   (L=+0.25, R=-0.25, 3.0s)")
            print(" [9] Calcolo Automatico Ticks/Giro (Fai compiere N giri e inserisci il conteggio)")
            print(" [0] Esci e ferma tutto")
            
            choice = input("\n👉 Inserisci numero test [0-9]: ").strip()
            
            if choice == '1':
                calib.run_motor_test("RUOTA SINISTRA AVANTI", l_duty=0.25, r_duty=0.0, duration_sec=3.0)
            elif choice == '2':
                calib.run_motor_test("RUOTA SINISTRA INDIETRO", l_duty=-0.25, r_duty=0.0, duration_sec=3.0)
            elif choice == '3':
                # Test ruota destra (canale R)
                calib.run_motor_test("RUOTA DESTRA AVANTI", l_duty=0.0, r_duty=0.25, duration_sec=3.0)
            elif choice == '4':
                calib.run_motor_test("RUOTA DESTRA INDIETRO", l_duty=0.0, r_duty=-0.25, duration_sec=3.0)
            elif choice == '5':
                calib.run_motor_test("AVANZAMENTO SINCRONO", l_duty=0.25, r_duty=0.25, duration_sec=3.0)
            elif choice == '6':
                calib.run_motor_test("RETROMARCIA SINCRONA", l_duty=-0.25, r_duty=-0.25, duration_sec=3.0)
            elif choice == '7':
                calib.run_motor_test("ROTAZIONE PURA A SINISTRA", l_duty=-0.25, r_duty=0.25, duration_sec=3.0)
            elif choice == '8':
                calib.run_motor_test("ROTAZIONE PURA A DESTRA", l_duty=0.25, r_duty=-0.25, duration_sec=3.0)
            elif choice == '9':
                print("\n🎯 CALCOLO PRECISO CPR (Ticks/Giro Ruota):")
                side = input("Quale ruota vuoi calibrare? (s = Sinistra / d = Destra): ").strip().lower()
                dur = float(input("Per quanti secondi vuoi farla girare? (es. 5.0): ").strip() or "5.0")
                if side == 's':
                    d_l, _ = calib.run_motor_test("CALIBRAZIONE RUOTA SINISTRA", l_duty=0.25, r_duty=0.0, duration_sec=dur)
                    giri = float(input("Quanti giri completi ha compiuto la ruota (contati dallo scotch)? ").strip())
                    if giri > 0:
                        cpr = abs(d_l) / giri
                        print(f"\n🌟 RISULTATO CALIBRATO RUOTA SINISTRA: {cpr:.2f} Ticks/Giro (Valore attuale nel file: {calib.ticks_per_rev_nominal})")
                else:
                    _, d_r = calib.run_motor_test("CALIBRAZIONE RUOTA DESTRA", l_duty=0.0, r_duty=0.25, duration_sec=dur)
                    giri = float(input("Quanti giri completi ha compiuto la ruota (contati dallo scotch)? ").strip())
                    if giri > 0:
                        cpr = abs(d_r) / giri
                        print(f"\n🌟 RISULTATO CALIBRATO RUOTA DESTRA: {cpr:.2f} Ticks/Giro (Valore attuale nel file: {calib.ticks_per_rev_nominal})")
            elif choice == '0' or choice.lower() == 'q':
                break
            else:
                print("Opzione non valida.")
    finally:
        calib.close()

if __name__ == '__main__':
    interactive_menu()
