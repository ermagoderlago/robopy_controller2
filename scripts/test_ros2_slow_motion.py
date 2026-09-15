#!/usr/bin/env python3
"""
Test script for safe, controlled slow motion via ROS 2 /cmd_vel.
Performs a gentle forward pulse (e.g. 0.08 m/s for 0.5s ~ 4cm)
and records odometry before and after to verify S-Curve limiter,
closed-loop motor response, and odometry integration.
"""

import sys
import time
import math
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry

class SlowMotionTester(Node):
    def __init__(self):
        super().__init__('slow_motion_tester')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self.odom_callback, 10)
        self.current_pose = None
        self.initial_pose = None

    def odom_callback(self, msg: Odometry):
        px = msg.pose.pose.position.x
        py = msg.pose.pose.position.y
        o = msg.pose.pose.orientation
        siny = 2.0 * (o.w * o.z + o.x * o.y)
        cosy = 1.0 - 2.0 * (o.y * o.y + o.z * o.z)
        yaw = math.atan2(siny, cosy)
        self.current_pose = (px, py, yaw)

    def wait_for_odom(self, timeout_sec=3.0):
        t0 = time.time()
        while time.time() - t0 < timeout_sec:
            rclpy.spin_once(self, timeout_sec=0.05)
            if self.current_pose is not None:
                return True
        return False

    def run_pulse(self, speed=0.08, duration=0.5):
        if not self.wait_for_odom():
            self.get_logger().error("No odometry received from /odom!")
            return False

        self.initial_pose = self.current_pose
        self.get_logger().info(
            f"📍 START POSE: x={self.initial_pose[0]:.4f}m, "
            f"y={self.initial_pose[1]:.4f}m, "
            f"yaw={math.degrees(self.initial_pose[2]):.2f}°"
        )

        self.get_logger().info(f"🚀 Pulsing cmd_vel: v={speed:.2f} m/s for {duration:.2f}s...")
        cmd = Twist()
        cmd.linear.x = speed
        cmd.angular.z = 0.0

        t_start = time.time()
        while time.time() - t_start < duration:
            self.cmd_pub.publish(cmd)
            rclpy.spin_once(self, timeout_sec=0.02)
            time.sleep(0.02)

        # Stop command
        self.get_logger().info("🛑 Stopping motors...")
        stop_cmd = Twist()
        stop_cmd.linear.x = 0.0
        stop_cmd.angular.z = 0.0
        for _ in range(5):
            self.cmd_pub.publish(stop_cmd)
            rclpy.spin_once(self, timeout_sec=0.02)
            time.sleep(0.02)

        # Allow standstill lock to stabilize
        time.sleep(0.6)
        for _ in range(10):
            rclpy.spin_once(self, timeout_sec=0.05)

        final_pose = self.current_pose
        dx = final_pose[0] - self.initial_pose[0]
        dy = final_pose[1] - self.initial_pose[1]
        dist = math.hypot(dx, dy)
        dyaw = final_pose[2] - self.initial_pose[2]
        dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))

        self.get_logger().info(
            f"🏁 FINAL POSE: x={final_pose[0]:.4f}m, "
            f"y={final_pose[1]:.4f}m, "
            f"yaw={math.degrees(final_pose[2]):.2f}°"
        )
        self.get_logger().info(
            f"📊 RESULT: Displacement = {dist*100.0:.2f} cm | "
            f"Heading change = {math.degrees(dyaw):.2f}°"
        )
        return True

def main():
    rclpy.init()
    tester = SlowMotionTester()
    try:
        print("\n--- TEST 1: Gentle Forward Pulse (v=0.08 m/s, 1.2s ~ 10cm) ---")
        ok1 = tester.run_pulse(speed=0.08, duration=1.2)
        time.sleep(1.0)
        print("\n--- TEST 2: Gentle Reverse Pulse (v=-0.08 m/s, 1.2s ~ 10cm back) ---")
        ok2 = tester.run_pulse(speed=-0.08, duration=1.2)
        if ok1 and ok2:
            print("\n✅ Bidirectional slow motion test completed successfully.")
        else:
            print("\n❌ Bidirectional slow motion test failed.")
    finally:
        tester.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
