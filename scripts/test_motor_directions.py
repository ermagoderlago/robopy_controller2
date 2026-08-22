#!/usr/bin/env python3
"""
test_motor_directions.py - Test Individuale di Direzione e Polarità Motori / Encoder
"""
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import time

class MotorDirectionTester(Node):
    def __init__(self):
        super().__init__('test_motor_directions')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.wheel_sub = self.create_subscription(Odometry, '/odom_wheel', self._wheel_cb, 10)
        self.last_odom = None

    def _wheel_cb(self, msg: Odometry):
        self.last_odom = msg

    def stop(self):
        msg = Twist()
        for _ in range(5):
            self.cmd_pub.publish(msg)
            time.sleep(0.02)

    def test_cmd(self, name, linear_x, angular_z, duration=1.0):
        print(f"\n--- TEST: {name} (linear={linear_x}, angular={angular_z}) ---")
        for _ in range(5):
            rclpy.spin_once(self, timeout_sec=0.05)
            
        x0 = self.last_odom.pose.pose.position.x if self.last_odom else 0.0
        y0 = self.last_odom.pose.pose.position.y if self.last_odom else 0.0
        
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
            
        x1 = self.last_odom.pose.pose.position.x if self.last_odom else 0.0
        y1 = self.last_odom.pose.pose.position.y if self.last_odom else 0.0
        print(f"   Delta Posizione: dx = {x1 - x0:+.4f} m, dy = {y1 - y0:+.4f} m")

def main():
    rclpy.init()
    node = MotorDirectionTester()
    time.sleep(1.0)
    for _ in range(10):
        rclpy.spin_once(node, timeout_sec=0.05)
        
    print("🎯 TEST POLARITÀ CINEMATICA MOTORI MARCUS")
    # 1. Forward
    node.test_cmd("AVANZAMENTO RETTILINEO (+0.15 m/s)", linear_x=0.15, angular_z=0.0, duration=0.8)
    # 2. Backward
    node.test_cmd("RETROMARCIA RETTILINEA (-0.15 m/s)", linear_x=-0.15, angular_z=0.0, duration=0.8)
    # 3. Turn Left (in place)
    node.test_cmd("ROTAZIONE PURA A SINISTRA (+0.60 rad/s)", linear_x=0.0, angular_z=0.60, duration=0.8)
    # 4. Turn Right (in place)
    node.test_cmd("ROTAZIONE PURA A DESTRA (-0.60 rad/s)", linear_x=0.0, angular_z=-0.60, duration=0.8)
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
