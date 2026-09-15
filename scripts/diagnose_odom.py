#!/usr/bin/env python3
"""
Diagnostic script: reads /home/robopy/robopy/logs/waveshare_motor_driver.log
or reads serial to verify exact ticks and delta per cycle.
"""
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import time, math

class OdomDiagnostics(Node):
    def __init__(self):
        super().__init__('odom_diagnostics')
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.records = []
        self.sub = self.create_subscription(Odometry, '/odom', self.cb, 100)

    def cb(self, msg):
        p = msg.pose.pose.position
        o = msg.pose.pose.orientation
        siny = 2.0 * (o.w * o.z + o.x * o.y)
        cosy = 1.0 - 2.0 * (o.y * o.y + o.z * o.z)
        yaw = math.atan2(siny, cosy)
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self.records.append((t, p.x, p.y, yaw, msg.twist.twist.linear.x, msg.twist.twist.angular.z))

    def run_test(self):
        print("Waiting for /odom baseline...")
        t0 = time.time()
        while time.time() - t0 < 1.0:
            rclpy.spin_once(self, timeout_sec=0.05)
        
        self.records.clear()
        print("Sending forward pulse: v=0.08 m/s for 1.0s...")
        cmd = Twist()
        cmd.linear.x = 0.08
        t_start = time.time()
        while time.time() - t_start < 1.0:
            self.pub.publish(cmd)
            rclpy.spin_once(self, timeout_sec=0.02)
            time.sleep(0.02)

        print("Stopping motors...")
        stop = Twist()
        for _ in range(5):
            self.pub.publish(stop)
            rclpy.spin_once(self, timeout_sec=0.02)
            time.sleep(0.02)

        time.sleep(0.5)
        for _ in range(10):
            rclpy.spin_once(self, timeout_sec=0.05)

        print(f"\n--- Total Odom Packets Recorded: {len(self.records)} ---")
        if self.records:
            first = self.records[0]
            last = self.records[-1]
            dx = last[1] - first[1]
            dy = last[2] - first[2]
            dyaw = last[3] - first[3]
            dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
            print(f"Initial: x={first[1]:.4f}, y={first[2]:.4f}, yaw={math.degrees(first[3]):.2f}°")
            print(f"Final:   x={last[1]:.4f}, y={last[2]:.4f}, yaw={math.degrees(last[3]):.2f}°")
            print(f"Delta:   dx={dx*100:.2f}cm, dy={dy*100:.2f}cm, dist={math.hypot(dx,dy)*100:.2f}cm, dyaw={math.degrees(dyaw):.2f}°")

def main():
    rclpy.init()
    d = OdomDiagnostics()
    try:
        d.run_test()
    finally:
        d.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
