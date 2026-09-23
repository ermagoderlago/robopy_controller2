#!/usr/bin/env python3
"""
calibrate_wheel_separation.py
==============================
Script di calibrazione interattiva per wheel_separation e ticks_per_rev di Marcus.

PROCEDURA:
1. Metti Marcus in uno spazio aperto, segnala la posizione iniziale con un nastro.
2. Esegui lo script: python3 scripts/calibrate_wheel_separation.py
3. Lo script invia un comando di rotazione di 360° (in-place).
4. Misura fisicamente quanti gradi ha ruotato il robot.
5. Lo script calcola il wheel_separation corretto e propone il parametro da usare.

REQUISITI:
- ROS_DOMAIN_ID=42, CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml settati
- Robot in modalità navigazione AMCL (motori attivi)
- Spazio libero di almeno 60cm intorno al robot

ATTENZIONE (SPEC-01 Zona Rossa):
- omega_max = 1.80 rad/s → usiamo 0.4 rad/s (lento, controllato)
- v_linear = 0.0 (puro spot-turn)
"""

import os
import sys
import math
import time
import threading

# Aggiungi venv al path
for p in ["/home/robopy/ros2_venv/lib/python3.11/site-packages"]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry

# ─── Parametri attuali (dal restart_hailo.sh) ────────────────────────────────
WHEEL_SEP_CURRENT   = 0.285   # m — da calibrare
WHEEL_RADIUS        = 0.0335  # m — D=67mm
TICKS_PER_REV       = 657     # da calibrare
MECHANICAL_AXLE_W   = 0.145   # m — interasse meccanico nominale (SPEC-01 §5)

# ─── Parametri di test ───────────────────────────────────────────────────────
TARGET_ROTATIONS    = 1       # numero di giri da comandare
OMEGA_CMD           = 0.40    # rad/s — molto lento, sicuro
PUBLISH_RATE_HZ     = 20
TOPIC_CMD_VEL       = "/cmd_vel"
TOPIC_ODOM          = "/odom"
# ─────────────────────────────────────────────────────────────────────────────


class WheelCalibrator(Node):
    def __init__(self):
        super().__init__("wheel_calibrator")
        self.pub = self.create_publisher(Twist, TOPIC_CMD_VEL, 10)
        self.sub = self.create_subscription(Odometry, TOPIC_ODOM,
                                             self._odom_cb, 10)
        self.odom_start_yaw = None
        self.odom_total_rad  = 0.0
        self.last_yaw        = None
        self.running         = False
        self.odom_received   = threading.Event()

    def _odom_cb(self, msg):
        q = msg.pose.pose.orientation
        siny = 2.0 * (q.w * q.z + q.x * q.y)
        cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        yaw  = math.atan2(siny, cosy)

        if self.odom_start_yaw is None:
            self.odom_start_yaw = yaw
            self.last_yaw       = yaw
            self.odom_received.set()
            return

        if self.running:
            delta = yaw - self.last_yaw
            # unwrap delta [-pi, pi]
            if delta >  math.pi: delta -= 2 * math.pi
            if delta < -math.pi: delta += 2 * math.pi
            self.odom_total_rad += delta
        self.last_yaw = yaw

    def stop(self):
        t = Twist()
        for _ in range(5):
            self.pub.publish(t)
            time.sleep(0.05)

    def spin_test(self):
        """Comanda TARGET_ROTATIONS giri e misura quanto l'odom pensa di aver girato."""
        target_rad = TARGET_ROTATIONS * 2.0 * math.pi
        duration   = target_rad / OMEGA_CMD          # secondi teorici
        dt         = 1.0 / PUBLISH_RATE_HZ

        print(f"\n[CALIBRA] Attesa prima ricezione odometria...")
        self.odom_received.wait(timeout=5.0)
        if self.odom_start_yaw is None:
            print("[ERRORE] Nessun messaggio /odom ricevuto. "
                  "Verificare CYCLONEDDS_URI e ROS_DOMAIN_ID=42.")
            return False

        print(f"[CALIBRA] Start! Comando {TARGET_ROTATIONS} giro/i @ {OMEGA_CMD} rad/s")
        print(f"[CALIBRA] Durata teorica: {duration:.1f}s")
        print(f"[CALIBRA] >>> GUARDA IL ROBOT: segna la posizione iniziale! <<<")
        time.sleep(2.0)

        t = Twist()
        t.angular.z = OMEGA_CMD
        self.running = True
        start = time.time()
        while time.time() - start < duration + 0.5:
            self.pub.publish(t)
            time.sleep(dt)

        self.running = False
        self.stop()
        return True

    def report(self, physical_degrees: float):
        """Calcola i parametri corretti dato quanti gradi ha ruotato fisicamente."""
        odom_deg     = math.degrees(abs(self.odom_total_rad))
        phys_rad     = math.radians(physical_degrees)
        odom_rad     = abs(self.odom_total_rad)
        target_rad   = TARGET_ROTATIONS * 2.0 * math.pi

        print("\n" + "="*60)
        print("  RISULTATI CALIBRAZIONE")
        print("="*60)
        print(f"  Rotazione comandata:      {math.degrees(target_rad):.1f}°")
        print(f"  Rotazione odometria:      {odom_deg:.1f}°")
        print(f"  Rotazione fisica reale:   {physical_degrees:.1f}°")
        print()

        # Il problema: odom pensa di aver girato odom_rad,
        # ma il robot fisico ha girato phys_rad.
        # Fattore di correzione:
        # odom_rad = (v_R - v_L) / wheel_sep_current * t
        # phys_rad = (v_R - v_L) / wheel_sep_correct * t
        # → wheel_sep_correct = wheel_sep_current * (odom_rad / phys_rad)

        if physical_degrees > 5.0 and odom_rad > 0.01:
            correction_factor    = odom_rad / phys_rad
            wheel_sep_corrected  = WHEEL_SEP_CURRENT * correction_factor
            print(f"  Fattore correzione:       {correction_factor:.4f}")
            print(f"  wheel_separation attuale: {WHEEL_SEP_CURRENT:.4f} m")
            print(f"  wheel_separation CORRETTO:{wheel_sep_corrected:.4f} m")
            print(f"  (interasse meccanico ref: {MECHANICAL_AXLE_W:.4f} m)")
            print()
            if physical_degrees > odom_deg:
                print("  ⚠️  Robot gira PIÙ di quanto dice l'odom (wheel_sep troppo grande).")
                print(f"      Ridurre wheel_separation da {WHEEL_SEP_CURRENT} → {wheel_sep_corrected:.4f}")
            else:
                print("  ⚠️  Robot gira MENO di quanto dice l'odom (wheel_sep troppo piccola).")
                print(f"      Aumentare wheel_separation da {WHEEL_SEP_CURRENT} → {wheel_sep_corrected:.4f}")
            print()
            print("  MODIFICA da applicare in restart_hailo.sh:")
            print(f"    -p wheel_separation:={wheel_sep_corrected:.4f} \\")
            print(f"    -p rotational_wheel_separation:={wheel_sep_corrected:.4f} \\")
            print()
            print("  VERIFICA: eseguire una seconda rotazione di 360° con il")
            print("  nuovo parametro e misurare nuovamente. Ripetere finché")
            print("  l'errore è < 5°.")
        else:
            print("  [ERRORE] Dati insufficienti per calcolo. Misura fisicamente i gradi.")

        print("="*60)


def main():
    rclpy.init()
    node = WheelCalibrator()

    # Spinner in background
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    print("="*60)
    print("  CALIBRATORE WHEEL_SEPARATION — Marcus")
    print("="*60)
    print(f"  Param corrente:  wheel_separation={WHEEL_SEP_CURRENT} m")
    print(f"  Interasse mec.:  {MECHANICAL_AXLE_W} m (SPEC-01 Zona Gialla)")
    print(f"  ticks_per_rev:   {TICKS_PER_REV}")
    print()
    print("  PROCEDURA:")
    print("  1. Metti un nastro o un segno sul pavimento sotto la coda del robot.")
    print("  2. Premi INVIO per avviare il giro di 360°.")
    print("  3. Misura quanti gradi il robot ha FISICAMENTE ruotato.")
    print("  4. Inserisci il valore misurato.")
    print()
    input("  Premi INVIO per iniziare...")

    ok = node.spin_test()
    if not ok:
        rclpy.shutdown()
        return

    print("\n[CALIBRA] Rotazione completata.")
    print("[CALIBRA] Misura ora con un goniometro/app quanti gradi ha ruotato il robot.")
    print("[CALIBRA] (dovrebbe essere ~360° se l'odometria fosse perfetta)")
    try:
        degrees_str = input("\n  Gradi fisici misurati (es. 185.0): ").strip()
        physical_degrees = float(degrees_str)
        node.report(physical_degrees)
    except (ValueError, KeyboardInterrupt):
        print("[ABORT] Inserimento annullato.")

    rclpy.shutdown()


if __name__ == "__main__":
    main()
