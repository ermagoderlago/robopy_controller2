#!/usr/bin/env python3
import rclpy
import time
import math
from rclpy.node import Node
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu
from nav_msgs.msg import Odometry

class RotationTester(Node):
    def __init__(self):
        super().__init__('test_rotation_symmetry')
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.gz_list = []
        self.odom_records = []
        self.imu_sub = self.create_subscription(Imu, '/oak/imu/data', self.imu_cb, 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self.odom_cb, 10)

    def imu_cb(self, msg):
        self.gz_list.append(msg.angular_velocity.z)

    def odom_cb(self, msg):
        p = msg.pose.pose.position
        o = msg.pose.pose.orientation
        siny = 2.0 * (o.w * o.z + o.x * o.y)
        cosy = 1.0 - 2.0 * (o.y * o.y + o.z * o.z)
        yaw = math.atan2(siny, cosy)
        self.odom_records.append((p.x, p.y, yaw))

    def stop(self):
        cmd = Twist()
        for _ in range(5):
            self.pub.publish(cmd)
            rclpy.spin_once(self, timeout_sec=0.02)
            time.sleep(0.02)

    def run_turn_test(self, w, name, duration=1.0):
        print(f"\n==========================================")
        print(f"--- TEST: {name} (w={w:+.2f} rad/s, duration={duration:.2f}s) ---")
        print(f"==========================================")
        self.gz_list.clear()
        self.odom_records.clear()
        
        cmd = Twist()
        cmd.angular.z = float(w)
        
        t0 = time.time()
        while time.time() - t0 < duration:
            self.pub.publish(cmd)
            rclpy.spin_once(self, timeout_sec=0.02)
            time.sleep(0.02)
            
        self.stop()
        time.sleep(0.5)
        for _ in range(10):
            rclpy.spin_once(self, timeout_sec=0.05)
            
        if self.odom_records:
            dyaw_deg = math.degrees(self.odom_records[-1][2] - self.odom_records[0][2])
            dx_cm = (self.odom_records[-1][0] - self.odom_records[0][0]) * 100.0
            dy_cm = (self.odom_records[-1][1] - self.odom_records[0][1]) * 100.0
            print(f"  Odom Result: dyaw={dyaw_deg:+.2f} deg, displacement=(dx={dx_cm:+.2f}cm, dy={dy_cm:+.2f}cm)")
        if self.gz_list:
            avg_w = sum(self.gz_list) / len(self.gz_list)
            print(f"  IMU Gyro Avg w_z = {avg_w:+.3f} rad/s ({math.degrees(avg_w):+.1f} deg/s) across {len(self.gz_list)} samples")

    def run_straight_test(self, v, name, duration=1.0):
        print(f"\n==========================================")
        print(f"--- TEST: {name} (v={v:+.2f} m/s, duration={duration:.2f}s) ---")
        print(f"==========================================")
        self.gz_list.clear()
        self.odom_records.clear()
        
        cmd = Twist()
        cmd.linear.x = float(v)
        
        t0 = time.time()
        while time.time() - t0 < duration:
            self.pub.publish(cmd)
            rclpy.spin_once(self, timeout_sec=0.02)
            time.sleep(0.02)
            
        self.stop()
        time.sleep(0.5)
        for _ in range(10):
            rclpy.spin_once(self, timeout_sec=0.05)
            
        if self.odom_records:
            dyaw_deg = math.degrees(self.odom_records[-1][2] - self.odom_records[0][2])
            dist_cm = math.hypot(self.odom_records[-1][0] - self.odom_records[0][0], self.odom_records[-1][1] - self.odom_records[0][1]) * 100.0
            print(f"  Odom Result: dist={dist_cm:.2f} cm, dyaw={dyaw_deg:+.2f} deg")
        if self.gz_list:
            avg_w = sum(self.gz_list) / len(self.gz_list)
            print(f"  IMU Gyro Avg w_z = {avg_w:+.3f} rad/s ({math.degrees(avg_w):+.1f} deg/s)")

def main():
    rclpy.init()
    tester = RotationTester()
    time.sleep(1.0)
    for _ in range(10):
        rclpy.spin_once(tester, timeout_sec=0.05)
        
    tester.run_straight_test(0.12, "STRAIGHT (+0.12 m/s)", duration=0.8)
    time.sleep(1.0)
    tester.run_turn_test(0.50, "TURN LEFT (+0.50 rad/s)", duration=0.8)
    time.sleep(1.0)
    tester.run_turn_test(-0.50, "TURN RIGHT (-0.50 rad/s)", duration=0.8)
    
    tester.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
