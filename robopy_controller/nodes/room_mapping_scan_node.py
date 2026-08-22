#!/usr/bin/env python3
"""
Room Mapping Scan Node for Marcus Robot
========================================
Esegue una scansione a 360° / 720° a passi discreti di 15°, fermandosi per 5 secondi
ad ogni intervallo per consentire alla fotocamera stereo (OAK-D Lite) e a RTAB-Map
di acquisire fotogrammi nitidi e nuvole di punti senza motion blur per la mappatura 2.5D/3D.

Parametri principali:
- total_degrees: 720.0 (default)
- step_degrees: 15.0 (default)
- pause_seconds: 5.0 (default)
- angular_speed: 0.35 rad/s (~20°/s)
"""

import sys
import time
import math
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry

class RoomMappingScanNode(Node):
    def __init__(self):
        super().__init__('room_mapping_scan_node')

        # Declare parameters
        self.declare_parameter('total_degrees', 720.0)
        self.declare_parameter('step_degrees', 15.0)
        self.declare_parameter('pause_seconds', 5.0)
        self.declare_parameter('angular_speed', 0.35)  # rad/s (~20°/s)
        self.declare_parameter('cmd_vel_topic', '/cmd_vel')

        # Retrieve parameter values
        self.total_degrees = float(self.get_parameter('total_degrees').value)
        self.step_degrees = float(self.get_parameter('step_degrees').value)
        self.pause_seconds = float(self.get_parameter('pause_seconds').value)
        self.angular_speed = float(self.get_parameter('angular_speed').value)
        self.cmd_vel_topic = str(self.get_parameter('cmd_vel_topic').value)

        self.cmd_vel_pub = self.create_publisher(Twist, self.cmd_vel_topic, 10)

        self.current_yaw = 0.0
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)

        self.total_steps = int(math.ceil(self.total_degrees / self.step_degrees))
        self.get_logger().info(
            f"🗺️ Initializing Room Mapping Scan: Total = {self.total_degrees}°, "
            f"Step = {self.step_degrees}°, Pause = {self.pause_seconds}s, Total Steps = {self.total_steps}"
        )

    def _odom_cb(self, msg: Odometry):
        q = msg.pose.pose.orientation
        # Extract yaw from quaternion
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self.current_yaw = math.atan2(siny_cosp, cosy_cosp)

    def stop_robot(self):
        msg = Twist()
        msg.linear.x = 0.0
        msg.linear.y = 0.0
        msg.linear.z = 0.0
        msg.angular.x = 0.0
        msg.angular.y = 0.0
        msg.angular.z = 0.0
        self.cmd_vel_pub.publish(msg)

    def run_scan(self):
        self.get_logger().info("🚀 Starting 720° Room Mapping Scan Routine...")
        time.sleep(1.0)  # Wait for publishers/subscribers to settle

        target_step_rad = math.radians(self.step_degrees)
        rot_time_sec = target_step_rad / self.angular_speed

        accumulated_degrees = 0.0

        for step in range(1, self.total_steps + 1):
            accumulated_degrees += self.step_degrees
            self.get_logger().info(
                f"🔄 [Step {step}/{self.total_steps}] Rotating {self.step_degrees}° "
                f"(Progress: {accumulated_degrees:.1f}° / {self.total_degrees:.1f}°)..."
            )

            # 1. Rotate step_degrees with initial 150ms Torque Kick (Stiction Kick)
            t_start = time.monotonic()
            t_end = t_start + rot_time_sec

            twist_kick = Twist()
            twist_kick.angular.z = max(self.angular_speed * 1.4, 0.70)

            twist_cruise = Twist()
            twist_cruise.angular.z = self.angular_speed

            while time.monotonic() < t_end:
                elapsed = time.monotonic() - t_start
                if elapsed < 0.15:
                    self.cmd_vel_pub.publish(twist_kick)
                else:
                    self.cmd_vel_pub.publish(twist_cruise)
                
                rclpy.spin_once(self, timeout_sec=0.05)
                time.sleep(0.05)

            # 2. Stop robot
            self.stop_robot()
            self.get_logger().info(
                f"⏸️ [Step {step}/{self.total_steps}] Stopped. Pausing for {self.pause_seconds}s "
                f"for RTAB-Map frame & pointcloud capture..."
            )

            # 3. Pause for pause_seconds while sending 0 cmd_vel periodically for watchdog
            pause_start = time.monotonic()
            pause_end = pause_start + self.pause_seconds

            while time.monotonic() < pause_end:
                self.stop_robot()
                rclpy.spin_once(self, timeout_sec=0.1)
                time.sleep(0.1)

        self.stop_robot()
        self.get_logger().info(
            f"✅ Room Mapping Scan Completed Successfully! Total rotated: {accumulated_degrees:.1f}°."
        )

def main(args=None):
    rclpy.init(args=args)
    node = RoomMappingScanNode()
    try:
        node.run_scan()
    except KeyboardInterrupt:
        node.get_logger().info("⚠️ Room Mapping Scan interrupted by user.")
    finally:
        node.stop_robot()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
