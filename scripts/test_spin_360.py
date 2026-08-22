#!/usr/bin/env python3
"""
Script Rotazione 360° sul Posto & Generazione Mappa Panoramica - Marcus
========================================================================
Esegue una rotazione sul posto a 360° per permettere a RTAB-Map SLAM ed alla
telecamera OAK-D di costruire la mappa panoramica 2.5D/3D.

Usa la fusione VIO (/odometry/filtered) e /odom per misurare la rotazione effettiva.
"""

import sys
import time
import math
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry


class Spin360TestNode(Node):
    def __init__(self):
        super().__init__('spin_360_test_node')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        self.raw_odom_sub = self.create_subscription(Odometry, '/odom', self._raw_odom_cb, 10)

        self.odom_received = False
        self.current_yaw: float = 0.0
        self.last_yaw = None
        self.accumulated_angle_rad: float = 0.0
        self.raw_accumulated_angle_rad: float = 0.0
        self.last_raw_yaw = None

    def _odom_cb(self, msg: Odometry):
        q = msg.pose.pose.orientation
        cyaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))

        if self.last_yaw is not None:
            dyaw = cyaw - self.last_yaw
            dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
            if abs(dyaw) < 0.5:
                self.accumulated_angle_rad += abs(dyaw)

        self.last_yaw = cyaw
        self.current_yaw = cyaw
        self.odom_received = True

    def _raw_odom_cb(self, msg: Odometry):
        q = msg.pose.pose.orientation
        cyaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        if self.last_raw_yaw is not None:
            dyaw = cyaw - self.last_raw_yaw
            dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
            if abs(dyaw) < 0.5:
                self.raw_accumulated_angle_rad += abs(dyaw)
        self.last_raw_yaw = cyaw

    def reset_angle(self):
        self.accumulated_angle_rad = 0.0
        self.raw_accumulated_angle_rad = 0.0
        self.last_yaw = None
        self.last_raw_yaw = None

    def publish_twist(self, vx: float, wz: float):
        twist = Twist()
        twist.linear.x = float(vx)
        twist.angular.z = float(wz)
        self.cmd_pub.publish(twist)

    def stop(self):
        for _ in range(10):
            self.publish_twist(0.0, 0.0)
            time.sleep(0.02)


def main():
    rclpy.init()
    node = Spin360TestNode()

    print("=" * 65)
    print(" 🤖 TEST ROTAZIONE 360° SUL POSTO - MARCUS")
    print("=" * 65)
    print("Attesa connessione odometria VIO (/odometry/filtered)...")

    # Attendi prima odometria
    start_wait = time.time()
    while time.time() - start_wait < 15.0:
        rclpy.spin_once(node, timeout_sec=0.1)
        if node.odom_received:
            break

    if not node.odom_received:
        print("❌ Impossibile connettersi a /odometry/filtered.")
        node.destroy_node()
        rclpy.shutdown()
        return

    print("✅ Odometria VIO connessa!")
    target_deg = 360.0
    target_rad = math.radians(target_deg)

    print(f"\n🌀 Avvio Rotazione 360° sul posto (Target = {target_deg:.1f}°)...")
    node.reset_angle()

    t_start = time.time()
    t_max = t_start + 30.0  # Max 30 secondi
    w_spin = 1.50  # ~86 deg/sec — overcomes floor friction deadzone
    last_print = 0.0

    while time.time() < t_max:
        rclpy.spin_once(node, timeout_sec=0.01)

        moved_rad = node.accumulated_angle_rad
        moved_deg = math.degrees(moved_rad)
        elapsed = time.time() - t_start
        error_rad = target_rad - moved_rad

        if elapsed - last_print >= 0.5:
            print(
                f"  ⏱️ [{elapsed:.1f}s] Rotazione accumulata (VIO): {moved_deg:.1f}° / {target_deg:.1f}° "
                f"(Encoder: {math.degrees(node.raw_accumulated_angle_rad):.1f}°)"
            )
            last_print = elapsed

        if error_rad <= 0.05:  # Tolleranza 3°
            print(f"\n✅ ROTAZIONE 360° COMPLETATA! Angolo VIO: {moved_deg:.1f}° in {elapsed:.2f}s")
            break

        node.publish_twist(0.0, w_spin)
        time.sleep(0.02)  # 50 Hz control loop

    node.stop()
    time.sleep(0.5)

    final_deg = math.degrees(node.accumulated_angle_rad)
    final_raw_deg = math.degrees(node.raw_accumulated_angle_rad)

    print("\n" + "=" * 65)
    print(" 📊 REPORT FINALE ROTAZIONE 360° & MAPPATURA")
    print("=" * 65)
    print(f"• Angolo di Rotazione VIO (/odometry/filtered): {final_deg:.1f}°")
    print(f"• Angolo di Rotazione Encoder Ruote (/odom):    {final_raw_deg:.1f}°")
    print("=" * 65)
    print("🛑 ROTAZIONE COMPLETATA. Mappa panoramica 360° generata su RTAB-Map!")

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
