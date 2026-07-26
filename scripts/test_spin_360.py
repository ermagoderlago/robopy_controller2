#!/usr/bin/env python3
"""
Script Rotazione 360° sul Posto & Generazione Mappa Panoramica - Marcus
========================================================================
Esegue una rotazione completa a 360° per permettere a RTAB-Map SLAM ed alla telecamera OAK-D
di costruire la mappa panoramica 2.5D/3D visualizzabile su Foxglove.
"""

import sys
import time
import math
import asyncio
import threading
from typing import Optional, Tuple

try:
    import rclpy
    from rclpy.node import Node
    from geometry_msgs.msg import Twist
    from nav_msgs.msg import Odometry
    HAS_ROS = True
except ImportError:
    HAS_ROS = False
    print("⚠️ ROS 2 non rilevato localmente.")


class Spin360TestNode(Node if HAS_ROS else object):
    def __init__(self):
        if not HAS_ROS:
            return
        super().__init__('spin_360_test_node')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        
        self.odom_received = False
        self.current_yaw: float = 0.0
        self.last_yaw: Optional[float] = None
        self.accumulated_angle_rad: float = 0.0

    def _odom_cb(self, msg: Odometry):
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        cyaw = 2.0 * math.atan2(qz, qw)
        
        if self.last_yaw is not None:
            dyaw = cyaw - self.last_yaw
            dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
            if abs(dyaw) < 0.5:
                self.accumulated_angle_rad += abs(dyaw)
            
        self.last_yaw = cyaw
        self.current_yaw = cyaw
        self.odom_received = True

    def reset_angle(self):
        self.accumulated_angle_rad = 0.0
        self.last_yaw = None

    def publish_twist(self, vx: float, wz: float):
        twist = Twist()
        twist.linear.x = float(vx)
        twist.angular.z = float(wz)
        self.cmd_pub.publish(twist)

    def stop(self):
        for _ in range(5):
            self.publish_twist(0.0, 0.0)
            time.sleep(0.02)


async def run_spin_360(node: Spin360TestNode, target_deg: float = 360.0):
    target_rad = math.radians(target_deg)
    print(f"\n🌀 Avvio Rotazione 360° sul posto (Target = {target_deg:.1f}°)...")
    
    await asyncio.sleep(0.5)
    node.reset_angle()
    
    t_start = time.monotonic()
    t_max = t_start + 25.0
    dt = 0.05
    last_print = 0.0
    
    # Test spinta iniziale a 0.8 rad/s
    w_spin = 0.80
    
    while time.monotonic() < t_max:
        moved_rad = node.accumulated_angle_rad
        moved_deg = math.degrees(moved_rad)
        elapsed = time.monotonic() - t_start
        error_rad = target_rad - moved_rad
        
        if elapsed - last_print >= 0.5:
            print(f"  ⏱️ [{elapsed:.1f}s] Rotazione accumulata: {moved_deg:.1f}° / {target_deg:.1f}° (mancano {math.degrees(error_rad):.1f}°)")
            last_print = elapsed
            
        if error_rad <= 0.05:  # Tolleranza 3 gradi
            print(f"\n✅ ROTAZIONE 360° COMPLETATA! /odom: {moved_deg:.1f}° in {elapsed:.2f}s")
            break
            
        node.publish_twist(0.0, w_spin)
        await asyncio.sleep(dt)
        
    node.stop()
    await asyncio.sleep(1.0)
    
    final_deg = math.degrees(node.accumulated_angle_rad)
    print("\n" + "=" * 65)
    print(" 📊 REPORT FINALE ROTAZIONE 360° & MAPPATURA")
    print("=" * 65)
    print(f"• Angolo di Rotazione totale (/odom): {final_deg:.1f}°")
    print("=" * 65)
    print("🛑 ROTAZIONE COMPLETATA. Osserva la mappa panoramica 360° generata su Foxglove!")


def main():
    if not HAS_ROS:
        return
    rclpy.init()
    node = Spin360TestNode()
    
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()
    
    print("=" * 65)
    print(" 🤖 TEST ROTAZIONE 360° SUL POSTO - MARCUS")
    print("=" * 65)
    print("Attesa connessione odometria /odom...")
    
    for _ in range(50):
        if node.odom_received:
            break
        time.sleep(0.1)
            
    if not node.odom_received:
        print("❌ Impossibile connettersi a /odom.")
        return

    print("✅ Odometria /odom connessa!")
    asyncio.run(run_spin_360(node, target_deg=360.0))
    node.stop()
    time.sleep(0.5)


if __name__ == '__main__':
    main()
