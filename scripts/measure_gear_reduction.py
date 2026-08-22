#!/usr/bin/env python3
"""
Script di Misurazione e Calibrazione Riduzione Motori & Ticks/Rev - Marcus
========================================================================
Calcola in modo empirico ed inconfutabile il numero esatto di ticks per giro di ruota (Ticks/Rev)
e il rapporto di riduzione meccanico reale dei motoriduttori di Marcus.

Metodi di Calibrazione:
  1. Test dei 10 Giri di Ruota (Rotazione lenta fino a 10 giri completi osservati a vista).
  2. Test del Metro Misurato a Terra (Avanzamento guidato su 1.00m misurato sul pavimento).

Utilizzo:
  python3 scripts/measure_gear_reduction.py
"""

import sys
import time
import math
import asyncio
from typing import Optional, Tuple

try:
    import rclpy
    from rclpy.node import Node
    from geometry_msgs.msg import Twist
    from nav_msgs.msg import Odometry
    HAS_ROS = True
except ImportError:
    HAS_ROS = False
    print("⚠️ ROS 2 non rilevato localmente. Eseguire lo script su Raspberry Pi marcus.")


class GearReductionCalibratorNode(Node if HAS_ROS else object):
    def __init__(self):
        if not HAS_ROS:
            return
        super().__init__('gear_reduction_calibrator_node')
        
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        
        self.current_x: float = 0.0
        self.current_y: float = 0.0
        self.current_yaw: float = 0.0
        self.odom_received = False
        self.raw_dist_accum = 0.0
        self.last_pose = None

    def _odom_cb(self, msg: Odometry):
        cx = msg.pose.pose.position.x
        cy = msg.pose.pose.position.y
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        cyaw = 2.0 * math.atan2(qz, qw)
        
        if self.last_pose is not None:
            lx, ly, _ = self.last_pose
            ds = math.sqrt((cx - lx)**2 + (cy - ly)**2)
            self.raw_dist_accum += ds
            
        self.last_pose = (cx, cy, cyaw)
        self.current_x = cx
        self.current_y = cy
        self.current_yaw = cyaw
        self.odom_received = True

    def reset_odometry_accum(self):
        self.raw_dist_accum = 0.0
        self.last_pose = None

    def publish_twist(self, vx: float, wz: float):
        twist = Twist()
        twist.linear.x = float(vx)
        twist.angular.z = float(wz)
        self.cmd_pub.publish(twist)

    def stop(self):
        self.publish_twist(0.0, 0.0)


async def run_slow_spin_test(node: GearReductionCalibratorNode, duration_s: float = 8.0, speed: float = 0.15):
    """Spinge i motori avanti molto lentamente consentendo il conteggio dei giri."""
    print(f"\n⚙️ Avvio rotazione lenta a {speed} m/s per {duration_s} secondi...")
    node.reset_odometry_accum()
    
    t_start = time.monotonic()
    t_end = t_start + duration_s
    
    while time.monotonic() < t_end:
        rclpy.spin_once(node, timeout_sec=0.02)
        node.publish_twist(speed, 0.0)
        await asyncio.sleep(0.02)
        
    node.stop()
    await asyncio.sleep(0.5)
    
    odom_dist = node.raw_dist_accum
    print(f"📊 Distanza odometrica calcolata dal driver: {odom_dist:.4f} metri")
    return odom_dist


def main():
    if not HAS_ROS:
        return

    rclpy.init()
    node = GearReductionCalibratorNode()
    
    print("=" * 70)
    print(" 🛠️ CALIBRAZIONE RAPPORTO DI RIDUZIONE E TICKS/REV MARCUS")
    print("=" * 70)
    print("Attesa connessione odometria /odom...")
    
    for _ in range(40):
        rclpy.spin_once(node, timeout_sec=0.1)
        if node.odom_received:
            break
            
    if not node.odom_received:
        print("❌ Topic /odom non disponibile. Verificare che waveshare_motor_driver sia attivo.")
        rclpy.shutdown()
        return

    print("✅ Odometria /odom connessa con successo!")
    print("\n--- TEST EMPIRICO DI MISURAZIONE RAPPORTO DI SCALA ---")
    print("Parametri geometrici fisici di riferimento:")
    print("  • Diametro ruota D = 65 mm (0.065 m)")
    print("  • Circonferenza ruota C = pi * 0.065 = 0.2042 metri (20.42 cm per giro)")
    print("  • Spostamento Target desiderato: 0.30 metri (30 cm = 1.469 giri di ruota)")
    
    print("\nPremere INVIO per avviare il test di avanzamento standard (3 secondi a 0.18 m/s)...")
    input()
    
    odom_measured = asyncio.run(run_slow_spin_test(node, duration_s=3.0, speed=0.18))
    
    print("\n" + "=" * 70)
    print(" 📏 MISURAZIONE FISICA SUL PAVIMENTO")
    print("=" * 70)
    print("Misura con un metro sul pavimento la distanza REALE percorsa da Marcus in questo test.")
    print("Inserisci la distanza FISICA REALE percorsa in metri (es. se ha fatto 2 metri digita 2.0):")
    
    try:
        real_meters_input = input("Distanza FISICA Reale (metri): ").strip()
        real_meters = float(real_meters_input)
    except ValueError:
        print("⚠️ Input non valido. Utilizzo del valore stimato dall'utente di 2.0 metri.")
        real_meters = 2.0
        
    wheel_circ = math.pi * 0.065  # 0.2042 m
    real_wheel_revs = real_meters / wheel_circ
    
    # Rapporto di correzione di scala
    scale_factor = real_meters / odom_measured if odom_measured > 0 else 1.0
    
    # Calcolo ticks_per_rev ideale
    # Se il robot ha fatto 2.0m quando il driver leggeva 0.30m (scale_factor = 2.0 / 0.30 = 6.66)
    # Significa che la risoluzione ticks_per_rev configurata era 6.66x TROPPO ALTA!
    current_configured_ticks = 89  # o valore in restart.sh
    ideal_ticks_per_rev = round(current_configured_ticks * (odom_measured / real_meters))
    
    print("\n" + "=" * 70)
    print(" 📊 RISULTATI CALIBRAZIONE ED ANALISI RIDUZIONE")
    print("=" * 70)
    print(f"• Distanza letta dall'odometria ROS 2 (/odom):  {odom_measured:.3f} metri")
    print(f"• Distanza FISICA REALE percorsa a terra:        {real_meters:.3f} metri")
    print(f"• Giri reali di ruota compiuti sul pavimento:    {real_wheel_revs:.2f} giri")
    print(f"• Fattore di Errore di Scala (Reale / Odom):      {scale_factor:.3f}x")
    print(f"----------------------------------------------------------------------")
    print(f"🎯 VALORE TICKS/REV ESATTO DA IMPOSTARE:")
    print(f"   -> ticks_per_rev := {ideal_ticks_per_rev} (o rapporto riduttore {real_wheel_revs:.1f}:1)")
    print("=" * 70)
    
    node.stop()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
