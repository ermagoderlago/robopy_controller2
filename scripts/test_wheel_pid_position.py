#!/usr/bin/env python3
"""
test_wheel_pid_position.py - Controllo di Posizione Angolare ad Anello Chiuso (PID)
Posiziona la ruota esattamente a 360° (o N ticks) decelerando in modo fluido
fino all'arresto perfetto senza inerzia residua o overshoot.
"""

import serial
import time
import json
import math
import sys

class WheelPIDPositionController:
    def __init__(self, port='/dev/ttyUSB0', baud=115200):
        self.port = port
        self.baud = baud
        self.serial = None
        
        # Parametri PID di posizione
        self.kp = 0.0018       # Guadagno proporzionale
        self.ki = 0.00005      # Guadagno integrale per azzerare l'errore statico
        self.kd = 0.0006       # Guadagno derivativo per smorzare l'inerzia
        self.min_duty = 0.17   # Deadband attrito statico riduttore (17%)
        self.max_duty = 0.35   # Velocità massima consentita a banco (35%)
        self.integral_limit = 0.10

    def connect(self):
        try:
            self.serial = serial.Serial(self.port, self.baud, timeout=0.03)
            time.sleep(0.3)
            self.stop_motors()
            return True
        except Exception as e:
            print(f"❌ Errore connessione seriale: {e}")
            return False

    def stop_motors(self):
        if self.serial and self.serial.is_open:
            for _ in range(5):
                self.serial.write(b'{"T":1,"L":0.0,"R":0.0}\n')
                time.sleep(0.01)
            self.serial.flush()

    def get_latest_telemetry(self, timeout=0.6):
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = self.serial.readline().decode('utf-8', errors='ignore').strip()
            if line.startswith('{') and '"odr"' in line:
                try:
                    d = json.loads(line)
                    return d.get('odl'), d.get('odr')
                except Exception:
                    pass
        return None, None

    def drive_right_wheel_pid(self, target_ticks=750, timeout_sec=4.0):
        """Esegue il posizionamento angolare PID ad anello chiuso sulla ruota destra."""
        # 1. Lettura baseline
        odl_0, odr_0 = self.get_latest_telemetry()
        if odr_0 is None:
            time.sleep(0.2)
            odl_0, odr_0 = self.get_latest_telemetry()

        if odr_0 is None:
            print("❌ Errore: Impossibile leggere telemetria encoder.")
            return

        print("\n" + "="*70)
        print(f"🎯 CONTROLLO PID DI POSIZIONE RUOTA DESTRA")
        print(f"   Target Desiderato: {target_ticks} ticks | Tick Iniziali: odr = {odr_0}")
        print(f"   Parametri PID: Kp={self.kp}, Ki={self.ki}, Kd={self.kd} | Deadband={self.min_duty*100:.0f}%")
        print("="*70)

        # La ruota destra ha incremento negativo quando gira in avanti (odr diminuisce)
        # target_odr = odr_0 - target_ticks
        target_odr = odr_0 - target_ticks

        integral = 0.0
        last_error = target_ticks
        last_time = time.time()
        settled_start_time = None

        t_start = time.time()
        cur_odr = odr_0

        while (time.time() - t_start) < timeout_sec:
            now = time.time()
            dt = now - last_time
            if dt <= 0:
                dt = 0.02
            last_time = now

            # Leggi encoder istantaneo
            line = self.serial.readline().decode('utf-8', errors='ignore').strip()
            if line.startswith('{') and '"odr"' in line:
                try:
                    d = json.loads(line)
                    val = d.get('odr')
                    if val is not None:
                        cur_odr = val
                except Exception:
                    pass

            # Calcolo errore di posizione (in ticks)
            delta_traveled = odr_0 - cur_odr
            error_to_target = target_ticks - delta_traveled

            # Controllo di convergenza
            if error_to_target <= 3:
                # Target raggiunto o superato: stop immediato
                self.stop_motors()
                break

            # Calcolo PID
            integral += error_to_target * dt
            integral = max(min(integral, self.integral_limit / (self.ki if self.ki > 0 else 1.0)), -self.integral_limit / (self.ki if self.ki > 0 else 1.0))
            
            derivative = (error_to_target - last_error) / dt
            last_error = error_to_target

            # Rampa di decelerazione morbida nell'ultimo 20% di corsa
            if error_to_target < 180:
                ramp_factor = max(0.0, error_to_target / 180.0)
                duty_r = self.min_duty + (self.max_duty - self.min_duty) * (ramp_factor ** 1.5)
            else:
                u_pid = (self.kp * error_to_target) + (self.ki * integral) + (self.kd * derivative)
                duty_mag = self.min_duty + (self.max_duty - self.min_duty) * min(abs(u_pid), 1.0)
                duty_r = min(duty_mag, self.max_duty)

            # Invio comando su canale R
            cmd_str = f'{{"T":1,"L":0.0,"R":{duty_r:.3f}}}\n'
            self.serial.write(cmd_str.encode('utf-8'))
            time.sleep(0.015)

        # Arresto finale
        self.stop_motors()
        time.sleep(0.4)

        # Lettura verifica finale
        odl_f, odr_f = self.get_latest_telemetry()
        if odr_f is None:
            odr_f = cur_odr

        actual_delta = abs(odr_f - odr_0)
        final_err = target_ticks - actual_delta
        print("\n" + "-"*70)
        print(f"📊 REPORT FINALE POSIZIONAMENTO PID:")
        print(f"   • Tick Iniziali: {odr_0} -> Tick Finali: {odr_f}")
        print(f"   • Delta Ticks Effettivi: {actual_delta} (Target: {target_ticks})")
        print(f"   • Errore Finale di Posizionamento: {final_err:+d} ticks ({final_err/target_ticks*360:.2f}°)")
        print(f"   • Frazione di Giro Eseguita: {actual_delta / target_ticks:.4f} giri")
        print("-" * 70 + "\n")

    def close(self):
        self.stop_motors()
        if self.serial:
            self.serial.close()

def main():
    target = 750
    if len(sys.argv) > 1:
        try:
            target = int(sys.argv[1])
        except ValueError:
            target = 750

    controller = WheelPIDPositionController()
    if controller.connect():
        try:
            controller.drive_right_wheel_pid(target_ticks=target, timeout_sec=4.5)
        finally:
            controller.close()

if __name__ == '__main__':
    main()
