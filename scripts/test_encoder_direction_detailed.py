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
        self.vio_sub = self.create_subscription(Odometry, '/odom', self._vio_cb, 10)
        from sensor_msgs.msg import Imu
        self.imu_sub = self.create_subscription(Imu, '/oak/imu/data', self._imu_cb, 10)
        
        self.wheel_odom = None
        self.vio_odom = None
        self.imu_integrated_yaw = 0.0
        self.last_imu_time = None

    def _wheel_cb(self, msg: Odometry):
        self.wheel_odom = msg

    def _vio_cb(self, msg: Odometry):
        self.vio_odom = msg

    def _imu_cb(self, msg):
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        gz = msg.angular_velocity.z
        if self.last_imu_time is not None:
            dt = t - self.last_imu_time
            if 0 < dt < 0.2:
                self.imu_integrated_yaw += gz * dt
        self.last_imu_time = t

    def stop(self):
        msg = Twist()
        for _ in range(5):
            self.cmd_pub.publish(msg)
            time.sleep(0.02)

    def run_step(self, label, linear_x, angular_z, duration=0.6):
        print(f"\n==================================================")
        print(f"▶️ STEP: {label} (lin={linear_x:+.2f} m/s, ang={angular_z:+.2f} rad/s, t={duration}s)")
        print(f"==================================================")
        
        self.imu_integrated_yaw = 0.0
        for _ in range(10):
            rclpy.spin_once(self, timeout_sec=0.05)
            
        wx0 = self.wheel_odom.pose.pose.position.x if self.wheel_odom else 0.0
        wy0 = self.wheel_odom.pose.pose.position.y if self.wheel_odom else 0.0
        wyaw0 = quat_to_yaw(self.wheel_odom.pose.pose.orientation) if self.wheel_odom else 0.0
        
        vyaw0 = quat_to_yaw(self.vio_odom.pose.pose.orientation) if self.vio_odom else 0.0
        self.imu_integrated_yaw = 0.0
        
        cmd = Twist()
        cmd.linear.x = float(linear_x)
        cmd.angular.z = float(angular_z)
        
        t_end = time.time() + duration
        while time.time() < t_end:
            self.cmd_pub.publish(cmd)
            rclpy.spin_once(self, timeout_sec=0.02)
            
        self.stop()
        time.sleep(1.0)
        for _ in range(10):
            rclpy.spin_once(self, timeout_sec=0.05)
            
        wx1 = self.wheel_odom.pose.pose.position.x if self.wheel_odom else 0.0
        wy1 = self.wheel_odom.pose.pose.position.y if self.wheel_odom else 0.0
        wyaw1 = quat_to_yaw(self.wheel_odom.pose.pose.orientation) if self.wheel_odom else 0.0
        vyaw1 = quat_to_yaw(self.vio_odom.pose.pose.orientation) if self.vio_odom else 0.0
        
        dx_global = wx1 - wx0
        dy_global = wy1 - wy0
        dx_local = dx_global * math.cos(wyaw0) + dy_global * math.sin(wyaw0)
        dy_local = -dx_global * math.sin(wyaw0) + dy_global * math.cos(wyaw0)
        
        d_wheel_yaw = math.degrees(wyaw1 - wyaw0)
        while d_wheel_yaw > 180: d_wheel_yaw -= 360
        while d_wheel_yaw < -180: d_wheel_yaw += 360
        
        d_vio_yaw = math.degrees(vyaw1 - vyaw0)
        while d_vio_yaw > 180: d_vio_yaw -= 360
        while d_vio_yaw < -180: d_vio_yaw += 360
        
        d_imu_yaw = math.degrees(self.imu_integrated_yaw)
        
        print(f"📊 Risultato {label}:")
        print(f"   • Delta Local X (Forward):  {dx_local:+.4f} m (atteso > 0 per avanti)")
        print(f"   • Delta Local Y (Lateral):  {dy_local:+.4f} m (atteso ~ 0 per differenziale)")
        print(f"   • Wheel Encoders Yaw:       {d_wheel_yaw:+.2f}°")
        print(f"   • IMU Gyro Z Yaw (Fisica):  {d_imu_yaw:+.2f}°")
        print(f"   • FastFlow VIO Yaw:         {d_vio_yaw:+.2f}°")

def main():
    rclpy.init()
    node = DetailedDirectionTest()
    time.sleep(1.0)
    for _ in range(10):
        rclpy.spin_once(node, timeout_sec=0.05)
        
    print("🎯 TEST COMPLETO CINEMATICA E DIREZIONI MARCUS (LOCAL FRAME & SENSORS)")
    node.run_step("1. AVANTI", linear_x=0.15, angular_z=0.0, duration=0.6)
    node.run_step("2. INDIETRO", linear_x=-0.15, angular_z=0.0, duration=0.6)
    node.run_step("3. ROTAZIONE SX (CCW)", linear_x=0.0, angular_z=0.50, duration=0.6)
    node.run_step("4. ROTAZIONE DX (CW)", linear_x=0.0, angular_z=-0.50, duration=0.6)
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
