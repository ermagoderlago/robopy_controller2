#!/usr/bin/env python3
"""
Emergency disengagement script for Marcus.
Backs up 0.25m from obstacle, then pivots 60 degrees away to clear furniture legs.
Keeps motion gate and nav intent open during the maneuver.
"""

import math
import time
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool
from nav_msgs.msg import Odometry


class MarcusDisengageNode(Node):
    def __init__(self):
        super().__init__('marcus_disengage_node')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.gate_pub = self.create_publisher(Bool, '/robot/motion_gate', 10)
        self.intent_pub = self.create_publisher(Bool, '/robot/nav_intent', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self.odom_callback, 10)

        self.current_pose = None

    def odom_callback(self, msg: Odometry):
        px = msg.pose.pose.position.x
        py = msg.pose.pose.position.y
        o = msg.pose.pose.orientation
        siny = 2.0 * (o.w * o.z + o.x * o.y)
        cosy = 1.0 - 2.0 * (o.y * o.y + o.z * o.z)
        yaw = math.atan2(siny, cosy)
        self.current_pose = (px, py, yaw)

    def wait_for_odom(self, timeout=3.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            rclpy.spin_once(self, timeout_sec=0.05)
            if self.current_pose is not None:
                return True
        return False

    def keep_gate_open(self):
        msg = Bool()
        msg.data = True
        self.gate_pub.publish(msg)
        self.intent_pub.publish(msg)

    def stop_motors(self):
        stop_cmd = Twist()
        for _ in range(5):
            self.keep_gate_open()
            self.cmd_pub.publish(stop_cmd)
            rclpy.spin_once(self, timeout_sec=0.02)
            time.sleep(0.02)

    def execute_disengage(self, backup_dist=0.25, backup_speed=0.10, pivot_rad=1.05, pivot_speed=0.40):
        if not self.wait_for_odom():
            self.get_logger().error("Cannot read /odom!")
            return False

        init_x, init_y, init_yaw = self.current_pose
        self.get_logger().info(f"📍 Baseline pose: x={init_x:.3f}m, y={init_y:.3f}m, yaw={math.degrees(init_yaw):.1f}°")

        # Step 1: Reverse back
        self.get_logger().info(f"⬅️ STEP 1: Reversing {backup_dist:.2f}m at {backup_speed:.2f} m/s...")
        cmd = Twist()
        cmd.linear.x = -abs(backup_speed)

        t_start = time.time()
        max_duration = (backup_dist / backup_speed) * 1.5 + 1.0
        while time.time() - t_start < max_duration:
            self.keep_gate_open()
            self.cmd_pub.publish(cmd)
            rclpy.spin_once(self, timeout_sec=0.02)
            time.sleep(0.02)

            cur_x, cur_y, _ = self.current_pose
            traveled = math.hypot(cur_x - init_x, cur_y - init_y)
            if traveled >= backup_dist:
                self.get_logger().info(f"✅ Backup reached: {traveled*100.0:.1f} cm")
                break

        self.stop_motors()
        time.sleep(0.5)

        # Step 2: Pivot away from obstacle
        self.wait_for_odom()
        piv_init_x, piv_init_y, piv_init_yaw = self.current_pose
        self.get_logger().info(f"🔄 STEP 2: Pivoting {math.degrees(pivot_rad):.1f}° at {pivot_speed:.2f} rad/s...")

        cmd = Twist()
        cmd.angular.z = pivot_speed

        t_start = time.time()
        max_piv_duration = (pivot_rad / pivot_speed) * 1.5 + 1.0
        while time.time() - t_start < max_piv_duration:
            self.keep_gate_open()
            self.cmd_pub.publish(cmd)
            rclpy.spin_once(self, timeout_sec=0.02)
            time.sleep(0.02)

            _, _, cur_yaw = self.current_pose
            dyaw = cur_yaw - piv_init_yaw
            dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
            if abs(dyaw) >= pivot_rad:
                self.get_logger().info(f"✅ Pivot reached: {math.degrees(dyaw):.1f}°")
                break

        self.stop_motors()
        time.sleep(0.5)

        self.wait_for_odom()
        final_x, final_y, final_yaw = self.current_pose
        total_dx = final_x - init_x
        total_dy = final_y - init_y
        total_dist = math.hypot(total_dx, total_dy)
        total_dyaw = final_yaw - init_yaw
        total_dyaw = math.atan2(math.sin(total_dyaw), math.cos(total_dyaw))

        self.get_logger().info(
            f"🎉 DISENGAGE COMPLETE!\n"
            f"   Final pose: x={final_x:.3f}m, y={final_y:.3f}m, yaw={math.degrees(final_yaw):.1f}°\n"
            f"   Net translation: {total_dist*100.0:.1f} cm | Net rotation: {math.degrees(total_dyaw):.1f}°"
        )
        return True


def main():
    rclpy.init()
    node = MarcusDisengageNode()
    try:
        node.execute_disengage()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
