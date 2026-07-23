#!/usr/bin/env python3
"""
Script Test Avanzamento 30cm Singolo - Marcus
============================================
Esegue un singolo avanzamento target di 30 cm basato sull'odometria /odom corrente e si ferma,
permettendo la misurazione fisica con metro a terra.
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
    print("⚠️ ROS 2 non rilevato localmente.")


class SingleForwardTestNode(Node if HAS_ROS else object):
    def __init__(self):
        if not HAS_ROS:
            return
        super().__init__('single_forward_test_node')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        
        self.current_x: Optional[float] = None
        self.current_y: Optional[float] = None
        self.current_yaw: Optional[float] = None
        self.odom_received = False
        
        self.last_pose: Optional[Tuple[float, float]] = None
        self.accumulated_path_m: float = 0.0

    def _odom_cb(self, msg: Odometry):
        cx = msg.pose.pose.position.x
        cy = msg.pose.pose.position.y
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        cyaw = 2.0 * math.atan2(qz, qw)
        
        if self.last_pose is not None:
            lx, ly = self.last_pose
            ds = math.sqrt((cx - lx)**2 + (cy - ly)**2)
            # Ignora micro-rumori e sbalzi anomali sopra 0.5m/step
            if ds < 0.5:
                self.accumulated_path_m += ds
                
        self.last_pose = (cx, cy)
        self.current_x = cx
        self.current_y = cy
        self.current_yaw = cyaw
        self.odom_received = True

    def reset_path(self):
        self.accumulated_path_m = 0.0
        self.last_pose = None

    def publish_twist(self, vx: float, wz: float):
        twist = Twist()
        twist.linear.x = float(vx)
        twist.angular.z = float(wz)
        self.cmd_pub.publish(twist)

    def stop(self):
        for _ in range(5):
            self.publish_twist(0.0, 0.0)
            time.sleep(0.02)


async def run_single_forward(node: SingleForwardTestNode, target_m: float = 0.30, Kp: float = 2.5, Ki: float = 0.8):
    print(f"\n🚀 Avvio Test Singolo Avanzamento: Target = {target_m*100:.1f} cm (Kp={Kp}, Ki={Ki})")
    
    await asyncio.sleep(0.5)
    node.reset_path()
    
    accum_error = 0.0
    t_start = time.monotonic()
    t_max = t_start + 15.0
    dt = 0.05
    last_print = 0.0
    
    final_moved = 0.0
    while time.monotonic() < t_max:
        moved = node.accumulated_path_m
        final_moved = moved
        elapsed = time.monotonic() - t_start
        error = target_m - moved
        
        if elapsed - last_print >= 0.5:
            print(f"  ⏱️ [{elapsed:.1f}s] Percorso accumulato: {moved*100:.2f} cm (error={error*100:.2f} cm)")
            last_print = elapsed
        
        if error <= 0.008:  # Tolleranza 8mm
            print(f"\n✅ TARGET RAGGIUNTO DALL'ODOMETRIA! /odom calcola: {moved*100:.2f} cm in {elapsed:.2f}s (Error={error*1000:.1f}mm)")
            break
            
        accum_error += error * dt
        cmd_v = Kp * error + Ki * accum_error
        if elapsed < 0.20:
            cmd_v = max(cmd_v, 0.22)
            
        cmd_v = min(max(abs(cmd_v), 0.15), 0.25)
        node.publish_twist(cmd_v, 0.0)
        await asyncio.sleep(dt)
        
    node.stop()
    await asyncio.sleep(0.8)
    final_moved = node.accumulated_path_m
    print(f"\n🛑 ROBOT FERMO. Percorso totale registrato da /odom: {final_moved*100:.2f} cm ({final_moved:.4f} m)")
    print("📏 Ora puoi effettuare la misurazione reale con il metro sul pavimento.")


def main():
    if not HAS_ROS:
        return
    rclpy.init()
    node = SingleForwardTestNode()
    
    import threading
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()
    
    print("=" * 60)
    print(" 🤖 TEST SINGOLO AVANZAMENTO 30 CM - MARCUS")
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
