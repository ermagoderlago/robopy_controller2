#!/usr/bin/env python3
"""
Diagnostic Script: Raw Encoder Ticks Sign Test During In-Place Spin
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import time

class RawEncoderDiag(Node):
    def __init__(self):
        super().__init__('diagnose_raw_encoder_ticks')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        self.odom_received = False
        self.poses = []

    def _odom_cb(self, msg: Odometry):
        self.odom_received = True
        self.poses.append((
            msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9,
            msg.pose.pose.position.x,
            msg.pose.pose.position.y,
            msg.twist.twist.linear.x,
            msg.twist.twist.angular.z
        ))

def main():
    rclpy.init()
    node = RawEncoderDiag()
    
    print("\n=================================================================")
    print(" 🤖 DIAGNOSTICA SEGNO ENCODER DURANTE ROTAZIONE IN LOCO")
    print("=================================================================")
    
    start_t = time.time()
    while rclpy.ok() and not node.odom_received:
        rclpy.spin_once(node, timeout_sec=0.1)
        if time.time() - start_t > 5.0:
            print("❌ Timeout connessione /odom.")
            node.destroy_node()
            rclpy.shutdown()
            return
            
    print("✅ Connesso a /odom. Invio comando di rotazione CCW (w=+0.80 rad/s) per 1.5 secondi...")
    
    msg = Twist()
    msg.angular.z = 0.80
    
    t0 = time.time()
    while time.time() - t0 < 1.5:
        node.cmd_pub.publish(msg)
        rclpy.spin_once(node, timeout_sec=0.05)
        
    # Stop
    stop_msg = Twist()
    for _ in range(5):
        node.cmd_pub.publish(stop_msg)
        time.sleep(0.02)
        
    print("\n--- POSIZIONI TRACCIATE DA /odom DURANTE LA ROTAZIONE ---")
    if len(node.poses) >= 2:
        p_start = node.poses[0]
        p_end = node.poses[-1]
        dx = p_end[1] - p_start[1]
        dy = p_end[2] - p_start[2]
        print(f"Posa Iniziale: X={p_start[1]:.4f}m, Y={p_start[2]:.4f}m")
        print(f"Posa Finale:   X={p_end[1]:.4f}m, Y={p_end[2]:.4f}m")
        print(f"Spostamento Lineare (Drift Spurio): dX={dx:+.4f}m, dY={dy:+.4f}m, Dist={math.hypot(dx, dy):.4f}m")
        
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
