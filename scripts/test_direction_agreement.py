#!/usr/bin/env python3
"""
Test Validazione Direzionale 1:1 (Destra/Sinistra)
1. Invia w = +0.6 rad/s (Sinistra / CCW) per 1.0s -> Mappa/Odom deve ruotare a Sinistra (+Yaw)
2. Pausa 2.0s
3. Invia w = -0.6 rad/s (Destra / CW) per 1.0s -> Mappa/Odom deve ruotare a Destra (-Yaw)
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import math
import time

class DirectionTester(Node):
    def __init__(self):
        super().__init__('test_direction_agreement')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        self.current_yaw = None

    def _odom_cb(self, msg: Odometry):
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        self.current_yaw = math.degrees(2.0 * math.atan2(qz, qw))

def main():
    rclpy.init()
    node = DirectionTester()
    
    print("\n=================================================================")
    print(" 🤖 TEST VALIDAZIONE DIREZIONALE 1:1 (DESTRA / SINISTRA)")
    print("=================================================================")
    print("Attesa odometria /odom...")
    
    t0 = time.time()
    while rclpy.ok() and node.current_yaw is None:
        rclpy.spin_once(node, timeout_sec=0.1)
        if time.time() - t0 > 5.0:
            print("❌ Timeout /odom.")
            return

    yaw0 = node.current_yaw
    print(f"✅ Connesso. Yaw Iniziale: {yaw0:.1f}°\n")

    # --- TEST 1: ROTAZIONE SINISTRA (w = +0.6 rad/s) ---
    print("🌀 1. Invio comando SINISTRA (w = +0.6 rad/s)... [Il robot fisico deve girare a SINISTRA]")
    cmd_left = Twist()
    cmd_left.angular.z = +0.60
    t_start = time.time()
    while time.time() - t_start < 1.0:
        node.cmd_pub.publish(cmd_left)
        rclpy.spin_once(node, timeout_sec=0.05)
        
    cmd_stop = Twist()
    for _ in range(5):
        node.cmd_pub.publish(cmd_stop)
        time.sleep(0.02)
    time.sleep(0.5)
    rclpy.spin_once(node, timeout_sec=0.1)
    
    yaw1 = node.current_yaw
    dyaw1 = yaw1 - yaw0
    dyaw1 = math.degrees(math.atan2(math.sin(math.radians(dyaw1)), math.cos(math.radians(dyaw1))))
    print(f"   📊 Risultato /odom: dYaw = {dyaw1:+.1f}° ({'SINISTRA/CCW ✅' if dyaw1 > 0 else 'DESTRA/CW ❌'})")

    print("\n⏳ Pausa 2.0s...")
    time.sleep(2.0)

    # --- TEST 2: ROTAZIONE DESTRA (w = -0.6 rad/s) ---
    print("\n🌀 2. Invio comando DESTRA (w = -0.6 rad/s)... [Il robot fisico deve girare a DESTRA]")
    cmd_right = Twist()
    cmd_right.angular.z = -0.60
    t_start = time.time()
    while time.time() - t_start < 1.0:
        node.cmd_pub.publish(cmd_right)
        rclpy.spin_once(node, timeout_sec=0.05)
        
    for _ in range(5):
        node.cmd_pub.publish(cmd_stop)
        time.sleep(0.02)
    time.sleep(0.5)
    rclpy.spin_once(node, timeout_sec=0.1)
    
    yaw2 = node.current_yaw
    dyaw2 = yaw2 - yaw1
    dyaw2 = math.degrees(math.atan2(math.sin(math.radians(dyaw2)), math.cos(math.radians(dyaw2))))
    print(f"   📊 Risultato /odom: dYaw = {dyaw2:+.1f}° ({'DESTRA/CW ✅' if dyaw2 < 0 else 'SINISTRA/CCW ❌'})")

    print("\n=================================================================")
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
