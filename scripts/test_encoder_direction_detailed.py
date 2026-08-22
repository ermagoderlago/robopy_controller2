#!/usr/bin/env python3
"""
test_encoder_direction_detailed.py - Analisi Dettagliata Direzione Motori ed Encoder
"""
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import math
import time

def quat_to_yaw(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)

class DetailedDirectionTest(Node):
    def __init__(self):
        super().__init__('test_encoder_direction_detailed')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.wheel_sub = self.create_subscription(Odometry, '/odom_wheel', self._wheel_cb, 10)
        self.odom = None

    def _wheel_cb(self, msg: Odometry):
        self.odom = msg

    def stop(self):
        msg = Twist()
        for _ in range(5):
            self.cmd_pub.publish(msg)
            time.sleep(0.02)

    def run_step(self, label, linear_x, angular_z, duration=0.6):
        print(f"\n==================================================")
        print(f"▶️ STEP: {label} (lin={linear_x}, ang={angular_z})")
        print(f"==================================================")
        for _ in range(5):
            rclpy.spin_once(self, timeout_sec=0.05)
            
        x0 = self.odom.pose.pose.position.x if self.odom else 0.0
        y0 = self.odom.pose.pose.position.y if self.odom else 0.0
        yaw0 = quat_to_yaw(self.odom.pose.pose.orientation) if self.odom else 0.0
        
        cmd = Twist()
        cmd.linear.x = float(linear_x)
        cmd.angular.z = float(angular_z)
        
        t_end = time.time() + duration
        while time.time() < t_end:
            self.cmd_pub.publish(cmd)
            rclpy.spin_once(self, timeout_sec=0.02)
            
        self.stop()
        time.sleep(1.0)
        for _ in range(5):
            rclpy.spin_once(self, timeout_sec=0.05)
            
        x1 = self.odom.pose.pose.position.x if self.odom else 0.0
        y1 = self.odom.pose.pose.position.y if self.odom else 0.0
        yaw1 = quat_to_yaw(self.odom.pose.pose.orientation) if self.odom else 0.0
        
        dx = x1 - x0
        dy = y1 - y0
        dyaw_deg = math.degrees(yaw1 - yaw0)
        while dyaw_deg > 180: dyaw_deg -= 360
        while dyaw_deg < -180: dyaw_deg += 360
        
        print(f"📊 Risultato {label}:")
        print(f"   • Delta X:   {dx:+.4f} m (Avanti atteso > 0, Indietro < 0)")
        print(f"   • Delta Y:   {dy:+.4f} m (Rettilineo atteso ~ 0)")
        print(f"   • Delta Yaw: {dyaw_deg:+.2f}° (SX atteso > 0, DX atteso < 0)")

def main():
    rclpy.init()
    node = DetailedDirectionTest()
    time.sleep(1.0)
    for _ in range(10):
        rclpy.spin_once(node, timeout_sec=0.05)
        
    node.run_step("1. AVANTI", linear_x=0.15, angular_z=0.0, duration=0.6)
    node.run_step("2. INDIETRO", linear_x=-0.15, angular_z=0.0, duration=0.6)
    node.run_step("3. ROTAZIONE SX", linear_x=0.0, angular_z=0.50, duration=0.6)
    node.run_step("4. ROTAZIONE DX", linear_x=0.0, angular_z=-0.50, duration=0.6)
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
