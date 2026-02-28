#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import Twist
import time
import numpy as np

class TestWallFollow(Node):
    def __init__(self):
        super().__init__('test_wall_follow')
        self.sub = self.create_subscription(LaserScan, '/scan', self.scan_cb, 10)
        self.pub = self.create_publisher(Twist, 'cmd_vel', 10)
        self.latest_scan = None
        
    def scan_cb(self, msg):
        self.latest_scan = msg
        
    def run(self):
        print("Starting Wall Follow Test (Left-side follow, 0.5m target)...")
        target_dist = 0.5
        kp = 1.2
        base_v = 0.12
        
        try:
            while rclpy.ok() and self.latest_scan is None:
                rclpy.spin_once(self, timeout_sec=0.1)
                time.sleep(0.1)
                
            print("Scan data received, starting control...")
            while rclpy.ok():
                rclpy.spin_once(self, timeout_sec=1.0)
                if not self.latest_scan:
                    continue
                
                # Logic...
                
                ranges = np.array(self.latest_scan.ranges)
                ranges = np.where(np.isfinite(ranges), ranges, 10.0)
                num_ranges = len(ranges)
                
                # Assuming 0 is front
                idx_front = 0
                idx_left = (3 * num_ranges) // 4
                window = num_ranges // 12
                
                front_min = np.min(ranges[max(0, idx_front-window):idx_front+window])
                left_min = np.min(ranges[idx_left-window:idx_left+window])
                
                twist = Twist()
                if front_min < 0.4:
                    twist.linear.x = 0.0
                    twist.angular.z = -1.0
                    print(f"Wall Ahead! ({front_min:.2f}m) Turning Right")
                else:
                    twist.linear.x = base_v
                    error = left_min - target_dist
                    twist.angular.z = error * kp
                    if left_min > 1.2:
                        twist.angular.z = 0.6
                    print(f"Following. Left: {left_min:.2f}m, Error: {error:.2f}, AngularZ: {twist.angular.z:.2f}")
                
                self.pub.publish(twist)
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.pub.publish(Twist())
            print("Stopped.")

def main():
    rclpy.init()
    node = TestWallFollow()
    node.run()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
