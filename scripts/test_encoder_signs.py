#!/usr/bin/env python3
"""
Test encoder signs directly by subscribing to /odom and printing delta_x, delta_y, delta_yaw
"""
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import math
import time

class EncoderSignTester(Node):
    def __init__(self):
        super().__init__('test_encoder_signs')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        self.last_pose = None
        self.current_pose = None

    def _odom_cb(self, msg: Odometry):
        self.current_pose = msg.pose.pose

def main():
    rclpy.init()
    node = EncoderSignTester()
    print("Connessione /odom...")
    t0 = time.time()
    while rclpy.ok() and node.current_pose is None:
        rclpy.spin_once(node, timeout_sec=0.1)
        if time.time() - t0 > 5.0:
            print("Timeout!")
            return

    p0 = node.current_pose
    print(f"Posa Iniziale: X={p0.position.x:.4f}, Y={p0.position.y:.4f}")

    print("\n--- TEST 1: ROTAZIONE ANTIOARARIA (CCW) w = +0.80 rad/s per 1.0s ---")
    cmd = Twist()
    cmd.angular.z = 0.80
    t_start = time.time()
    while time.time() - t_start < 1.0:
        node.cmd_pub.publish(cmd)
        rclpy.spin_once(node, timeout_sec=0.05)
    
    # Stop
    cmd_stop = Twist()
    for _ in range(5):
        node.cmd_pub.publish(cmd_stop)
        time.sleep(0.02)
    rclpy.spin_once(node, timeout_sec=0.2)
    
    p1 = node.current_pose
    dx1 = p1.position.x - p0.position.x
    dy1 = p1.position.y - p0.position.y
    dist1 = math.hypot(dx1, dy1)
    qz1 = p1.orientation.z
    qw1 = p1.orientation.w
    yaw1 = math.degrees(2.0 * math.atan2(qz1, qw1))
    
    print(f"Risultato CCW: dX={dx1:+.4f}m, dY={dy1:+.4f}m | Dist Drift={dist1:.4f}m | Yaw={yaw1:.1f}°")

    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
