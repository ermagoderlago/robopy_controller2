#!/usr/bin/env python3
"""
Script Test Movimento All'Indietro 30cm & Verificatore Odometria - Marcus
========================================================================
Esegue un arretramento controllato di 30 cm basato su anello di retroazione PID su /odom,
verificando contemporaneamente la concordanza tra Odometria Ruote, Odometria Filtrata (EKF)
e Odometria Visiva (VO).
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


class BackwardTestVerificationNode(Node if HAS_ROS else object):
    def __init__(self):
        if not HAS_ROS:
            return
        super().__init__('backward_test_verification_node')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        
        # Subscriptions
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        self.ekf_sub = self.create_subscription(Odometry, '/odometry/filtered', self._ekf_cb, 10)
        self.vo_sub = self.create_subscription(Odometry, '/vo/odom', self._vo_cb, 10)
        
        self.odom_received = False
        self.ekf_received = False
        self.vo_received = False
        
        # Wheel Odom Tracking
        self.last_odom_pose: Optional[Tuple[float, float]] = None
        self.accum_odom_m: float = 0.0
        
        # EKF Tracking
        self.last_ekf_pose: Optional[Tuple[float, float]] = None
        self.accum_ekf_m: float = 0.0
        
        # VO Tracking
        self.last_vo_pose: Optional[Tuple[float, float]] = None
        self.accum_vo_m: float = 0.0

    def _odom_cb(self, msg: Odometry):
        cx = msg.pose.pose.position.x
        cy = msg.pose.pose.position.y
        if self.last_odom_pose is not None:
            ds = math.sqrt((cx - self.last_odom_pose[0])**2 + (cy - self.last_odom_pose[1])**2)
            if ds < 0.5:
                self.accum_odom_m += ds
        self.last_odom_pose = (cx, cy)
        self.odom_received = True

    def _ekf_cb(self, msg: Odometry):
        cx = msg.pose.pose.position.x
        cy = msg.pose.pose.position.y
        if self.last_ekf_pose is not None:
            ds = math.sqrt((cx - self.last_ekf_pose[0])**2 + (cy - self.last_ekf_pose[1])**2)
            if ds < 0.5:
                self.accum_ekf_m += ds
        self.last_ekf_pose = (cx, cy)
        self.ekf_received = True

    def _vo_cb(self, msg: Odometry):
        cx = msg.pose.pose.position.x
        cy = msg.pose.pose.position.y
        if self.last_vo_pose is not None:
            ds = math.sqrt((cx - self.last_vo_pose[0])**2 + (cy - self.last_vo_pose[1])**2)
            if ds < 0.5:
                self.accum_vo_m += ds
        self.last_vo_pose = (cx, cy)
        self.vo_received = True

    def reset_trackers(self):
        self.accum_odom_m = 0.0
        self.accum_ekf_m = 0.0
        self.accum_vo_m = 0.0
        self.last_odom_pose = None
        self.last_ekf_pose = None
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


async def run_backward_test(node: BackwardTestVerificationNode, target_m: float = 0.30, Kp: float = 2.5, Ki: float = 0.8):
    print(f"\n🚀 Avvio Test Movimento All'Indietro: Target = {target_m*100:.1f} cm (Kp={Kp}, Ki={Ki})")
    
    await asyncio.sleep(0.5)
    node.reset_trackers()
    
    accum_error = 0.0
    t_start = time.monotonic()
    t_max = t_start + 15.0
    dt = 0.05
    last_print = 0.0
    
    while time.monotonic() < t_max:
        moved_odom = node.accum_odom_m
        moved_ekf = node.accum_ekf_m
        moved_vo = node.accum_vo_m
        
        elapsed = time.monotonic() - t_start
        error = target_m - moved_odom
        
        if elapsed - last_print >= 0.5:
            vo_str = f"{moved_vo*100:.2f} cm" if node.vo_received else "N/A"
            ekf_str = f"{moved_ekf*100:.2f} cm" if node.ekf_received else "N/A"
            print(f"  ⏱️ [{elapsed:.1f}s] Odom: {moved_odom*100:.2f}cm | EKF: {ekf_str} | VO: {vo_str} (error={error*100:.2f}cm)")
            last_print = elapsed
        
        if error <= 0.008:  # Tolleranza 8mm
            print(f"\n✅ TARGET ARRETRAMENTO RAGGIUNTO! /odom: {moved_odom*100:.2f} cm in {elapsed:.2f}s (Error={error*1000:.1f}mm)")
            break
            
        accum_error += error * dt
        # Per muoversi all'indietro: velocita lineare NEGATIVA (-v)
        cmd_v = -(Kp * error + Ki * accum_error)
        if elapsed < 0.20:
            cmd_v = min(cmd_v, -0.22)
            
        # Limita velocita tra -0.15 e -0.25 m/s
        cmd_v = max(min(cmd_v, -0.15), -0.25)
        node.publish_twist(cmd_v, 0.0)
        await asyncio.sleep(dt)
        
    node.stop()
    await asyncio.sleep(0.8)
    
    f_odom = node.accum_odom_m
    f_ekf = node.accum_ekf_m
    f_vo = node.accum_vo_m
    
    print("\n" + "=" * 65)
    print(" 📊 REPORT DI VERIFICA SPOSTAMENTO ALL'INDIETRO & CONCORDANZA ODOM")
    print("=" * 65)
    print(f"• Odometria Ruote (/odom):          {f_odom*100:.2f} cm  ({f_odom:.4f} m)")
    if node.ekf_received:
        print(f"• Odometria Filtrata EKF (/odometry/filtered): {f_ekf*100:.2f} cm  ({f_ekf:.4f} m)")
    if node.vo_received:
        print(f"• Odometria Visiva VO (/visual_odom/odom):     {f_vo*100:.2f} cm  ({f_vo:.4f} m)")
    print("=" * 65)
    print("🛑 ROBOT FERMO. Misura ora a terra col metro la distanza reale percorsa all'indietro.")


def main():
    if not HAS_ROS:
        return
    rclpy.init()
    node = BackwardTestVerificationNode()
    
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()
    
    print("=" * 65)
    print(" 🤖 TEST ARRETRAMENTO 30 CM & VERIFICA CONCORDANZA ODOMETRIE")
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
    asyncio.run(run_backward_test(node, target_m=0.30))
    node.stop()
    time.sleep(0.5)


if __name__ == '__main__':
    main()
