#!/usr/bin/env python3
"""
Script Test Diagnostico Rotazione a Step di 30° - Marcus
=========================================================
Esegue 12 rotazioni passo-passo di 30.0° alla volta in senso antiorario (+1.0).
Per ogni step di 30°, registra e confronta:
 1. Angolo calcolato dall'Odometria Ruote (/odom)
 2. Angolo calcolato dall'Odometria Visiva SuperPoint (/vo/odom)
 3. Deriva accumulata sulla mappa RTAB-Map su Foxglove
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


class StepRotation30TestNode(Node if HAS_ROS else object):
    def __init__(self):
        if not HAS_ROS:
            return
        super().__init__('step_rotation_30_test_node')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        self.vo_sub = self.create_subscription(Odometry, '/vo/odom', self._vo_cb, 10)
        
        self.odom_received = False
        self.vo_received = False
        
        self.current_yaw: float = 0.0
        self.last_yaw: Optional[float] = None
        self.step_angle_rad: float = 0.0
        self.total_angle_rad: float = 0.0
        
        self.last_vo_yaw: Optional[float] = None
        self.step_vo_rad: float = 0.0
        self.total_vo_rad: float = 0.0

    def _odom_cb(self, msg: Odometry):
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        cyaw = 2.0 * math.atan2(qz, qw)
        
        if self.last_yaw is not None:
            dyaw = cyaw - self.last_yaw
            dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
            if abs(dyaw) < 0.5:
                self.step_angle_rad += abs(dyaw)
                self.total_angle_rad += abs(dyaw)
                
        self.last_yaw = cyaw
        self.current_yaw = cyaw
        self.odom_received = True

    def _vo_cb(self, msg: Odometry):
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        cyaw = 2.0 * math.atan2(qz, qw)
        
        if self.last_vo_yaw is not None:
            dyaw = cyaw - self.last_vo_yaw
            dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
            if abs(dyaw) < 0.5:
                self.step_vo_rad += abs(dyaw)
                self.total_vo_rad += abs(dyaw)
                
        self.last_vo_yaw = cyaw
        self.vo_received = True

    def reset_step(self):
        self.step_angle_rad = 0.0
        self.step_vo_rad = 0.0
        self.last_yaw = None
        self.last_vo_yaw = None

    def publish_twist(self, vx: float, wz: float):
        twist = Twist()
        twist.linear.x = float(vx)
        twist.angular.z = float(wz)
        self.cmd_pub.publish(twist)

    def stop(self):
        for _ in range(5):
            self.publish_twist(0.0, 0.0)
            time.sleep(0.02)


async def run_single_30deg_step(node: StepRotation30TestNode, step_idx: int, direction_sign: float = 1.0, target_deg: float = 30.0):
    target_rad = math.radians(target_deg)
    dir_str = "ANTIORARIO (SX)" if direction_sign > 0 else "ORARIO (DX)"
    
    print(f"\n🔄 [STEP {step_idx}] Avvio Rotazione 30° ({dir_str})...")
    await asyncio.sleep(0.3)
    node.reset_step()
    
    t_start = time.monotonic()
    t_max = t_start + 6.0
    dt = 0.05
    last_print = 0.0
    accum_error = 0.0
    
    while time.monotonic() < t_max:
        moved_rad = node.step_angle_rad
        moved_deg = math.degrees(moved_rad)
        elapsed = time.monotonic() - t_start
        error_rad = target_rad - moved_rad
        
        if elapsed - last_print >= 0.4:
            vo_deg = math.degrees(node.step_vo_rad) if node.vo_received else 0.0
            print(f"  ⏱️ [{elapsed:.1f}s] Step {step_idx}: Odom = {moved_deg:.1f}° / 30.0° | VO = {vo_deg:.1f}°")
            last_print = elapsed
            
        if error_rad <= 0.025:  # Tolleranza ~1.4 gradi
            print(f"✅ [STEP {step_idx}] TARGET 30° RAGGIUNTO! /odom = {moved_deg:.1f}° in {elapsed:.2f}s")
            break
            
        accum_error += error_rad * dt
        cmd_w = 2.5 * error_rad + 0.8 * accum_error
        cmd_w = min(max(abs(cmd_w), 0.70), 0.90) * direction_sign
        
        node.publish_twist(0.0, cmd_w)
        await asyncio.sleep(dt)
        
    node.stop()
    await asyncio.sleep(0.5)
    
    f_step_odom = math.degrees(node.step_angle_rad)
    f_step_vo = math.degrees(node.step_vo_rad) if node.vo_received else 0.0
    f_tot_odom = math.degrees(node.total_angle_rad)
    
    print("\n" + "-" * 60)
    print(f" 📊 RISULTATI STEP {step_idx} (Target 30.0°):")
    print(f" • Odometria Ruote (/odom):         {f_step_odom:.1f}°")
    print(f" • Odometria Visiva SuperPoint (/vo): {f_step_vo:.1f}°")
    print(f" • Rotazione Totale Accumulata:      {f_tot_odom:.1f}°")
    print("-" * 60)
    return f_step_odom, f_step_vo


async def main_loop():
    if not HAS_ROS:
        return
    rclpy.init()
    node = StepRotation30TestNode()
    
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()
    
    print("=" * 65)
    print(" 🤖 TEST DIAGNOSTICO ROTAZIONE A STEP DI 30° - MARCUS")
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
    direction_sign = 1.0  # Sensu antiorario (SX)
    
    for step_count in range(1, 13):
        await run_single_30deg_step(node, step_idx=step_count, direction_sign=direction_sign, target_deg=30.0)
        print(f"🛑 STEP {step_count}/12 COMPLETATO. Pausa 2.0s per osservare Foxglove...")
        await asyncio.sleep(2.0)
        
    print("\n" + "=" * 65)
    print(f" 🎉 TUTTI I 12 STEP DA 30° COMPLETATI! Rotazione totale: {math.degrees(node.total_angle_rad):.1f}°")
    print("=" * 65)
    node.stop()


def main():
    if HAS_ROS:
        asyncio.run(main_loop())


if __name__ == '__main__':
    main()
