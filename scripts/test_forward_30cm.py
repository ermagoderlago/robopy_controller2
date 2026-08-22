#!/usr/bin/env python3
"""
Script Test Avanzamento 30cm con Correzione di Rotta (Heading Keep PID) - Marcus
==============================================================================
Esegue un avanzamento di 30 cm con anello di controllo della rotta (Yaw Lock):
mantiene il robot su una traiettoria perfettamente rettilinea correggendo automaticamente
qualsiasi deriva a destra o sinistra tramite feedback odometrico.
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


class SingleForwardTestNode(Node if HAS_ROS else object):
    def __init__(self):
        if not HAS_ROS:
            return
        super().__init__('single_forward_test_node')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        self.vo_sub = self.create_subscription(Odometry, '/vo/odom', self._vo_cb, 10)
        
        self.odom_received = False
        self.vo_received = False
        
        self.current_x: float = 0.0
        self.current_y: float = 0.0
        self.current_yaw: float = 0.0
        
        self.last_pose: Optional[Tuple[float, float]] = None
        self.accumulated_path_m: float = 0.0
        
        self.last_vo_pose: Optional[Tuple[float, float]] = None
        self.accumulated_vo_m: float = 0.0

    def _odom_cb(self, msg: Odometry):
        cx = msg.pose.pose.position.x
        cy = msg.pose.pose.position.y
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        cyaw = 2.0 * math.atan2(qz, qw)
        
        if self.last_pose is not None:
            ds = math.sqrt((cx - self.last_pose[0])**2 + (cy - self.last_pose[1])**2)
            if ds < 0.5:
                self.accumulated_path_m += ds
                
        self.last_pose = (cx, cy)
        self.current_x = cx
        self.current_y = cy
        self.current_yaw = cyaw
        self.odom_received = True

    def _vo_cb(self, msg: Odometry):
        cx = msg.pose.pose.position.x
        cy = msg.pose.pose.position.y
        if self.last_vo_pose is not None:
            ds = math.sqrt((cx - self.last_vo_pose[0])**2 + (cy - self.last_vo_pose[1])**2)
            if ds < 0.5:
                self.accumulated_vo_m += ds
        self.last_vo_pose = (cx, cy)
        self.vo_received = True

    def reset_path(self):
        self.accumulated_path_m = 0.0
        self.accumulated_vo_m = 0.0
        self.last_pose = None
        self.last_vo_pose = None

    def publish_twist(self, vx: float, wz: float):
        twist = Twist()
        twist.linear.x = float(vx)
        twist.angular.z = float(wz)
        self.cmd_pub.publish(twist)

    def stop(self):
        for _ in range(5):
            self.publish_twist(0.0, 0.0)
            time.sleep(0.02)


async def run_single_forward(node: SingleForwardTestNode, target_m: float = 0.30, Kp_lin: float = 2.5, Ki_lin: float = 0.8, Kp_yaw: float = 2.0):
    print(f"\n🚀 Avvio Test Avanzamento Rettilineo 30 cm con Heading Keep PID (Kp_lin={Kp_lin}, Kp_yaw={Kp_yaw})")
    
    await asyncio.sleep(0.5)
    node.reset_path()
    
    yaw0 = node.current_yaw
    print(f"🧭 Rotta Iniziale Bloccata (Yaw0): {math.degrees(yaw0):.2f}°")
    
    accum_error = 0.0
    t_start = time.monotonic()
    t_max = t_start + 15.0
    dt = 0.05
    last_print = 0.0
    
    while time.monotonic() < t_max:
        moved_odom = node.accumulated_path_m
        moved_vo = node.accumulated_vo_m
        cyaw = node.current_yaw
        elapsed = time.monotonic() - t_start
        error = target_m - moved_odom
        
        # Correzione di Rotta (Heading Compensation)
        yaw_error = yaw0 - cyaw
        # Normalizzazione angolo in [-pi, pi]
        yaw_error = (yaw_error + math.pi) % (2.0 * math.pi) - math.pi
        w_cmd = Kp_yaw * yaw_error
        w_cmd = min(max(w_cmd, -0.4), 0.4)
        
        if elapsed - last_print >= 0.5:
            vo_str = f"{moved_vo*100:.2f} cm" if node.vo_received else "N/A"
            drift_deg = math.degrees(yaw_error)
            print(f"  ⏱️ [{elapsed:.1f}s] Odom: {moved_odom*100:.2f}cm | VO: {vo_str} | Deriva Yaw: {drift_deg:+.2f}° (w_corr={w_cmd:+.3f})")
            last_print = elapsed
        
        if error <= 0.008:  # Tolleranza 8mm
            print(f"\n✅ TARGET RAGGIUNTO! /odom: {moved_odom*100:.2f} cm in {elapsed:.2f}s (Error={error*1000:.1f}mm)")
            break
            
        accum_error += error * dt
        cmd_v = Kp_lin * error + Ki_lin * accum_error
        if elapsed < 0.20:
            cmd_v = max(cmd_v, 0.22)
            
        cmd_v = min(max(abs(cmd_v), 0.15), 0.25)
        node.publish_twist(cmd_v, w_cmd)
        await asyncio.sleep(dt)
        
    node.stop()
    await asyncio.sleep(0.8)
    
    f_odom = node.accumulated_path_m
    f_vo = node.accumulated_vo_m
    final_drift = math.degrees((yaw0 - node.current_yaw + math.pi) % (2.0 * math.pi) - math.pi)
    
    print("\n" + "=" * 65)
    print(" 📊 REPORT FINALE AVANZAMENTO RETTILINEO 30 CM & ROTTA")
    print("=" * 65)
    print(f"• Percorso Odometria Ruote (/odom): {f_odom*100:.2f} cm  ({f_odom:.4f} m)")
    if node.vo_received:
        print(f"• Percorso Odometria Visiva (/vo/odom): {f_vo*100:.2f} cm  ({f_vo:.4f} m)")
    print(f"• Deriva Angolare Finale di Rotta:  {final_drift:+.2f}°")
    print("=" * 65)
    print("🛑 ROBOT FERMO. Segui lo spostamento su Foxglove e misura sul pavimento.")


def main():
    if not HAS_ROS:
        return
    rclpy.init()
    node = SingleForwardTestNode()
    
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()
    
    print("=" * 60)
    print(" 🤖 TEST AVANZAMENTO 30 CM CON HEADING KEEP - MARCUS")
    print("=" * 60)
    print("Attesa connessione odometria /odom...")
    
    for _ in range(50):
        if node.odom_received:
            break
        time.sleep(0.1)
            
    if not node.odom_received:
        print("❌ Impossibile connettersi a /odom.")
        return

    print("✅ Odometria /odom connessa!")
    asyncio.run(run_single_forward(node, target_m=0.30))
    node.stop()
    time.sleep(0.5)


if __name__ == '__main__':
    main()
