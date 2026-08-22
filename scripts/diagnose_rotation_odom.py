#!/usr/bin/env python3
"""
diagnose_rotation_odom.py - Diagnostica e Calibrazione Automatica Odometria & SLAM in Rotazione
Esegue:
1. Monitoraggio simultaneo di /odom_wheel, /oak/imu/data, /odom (VIO) e TF
2. Rotazione controllata sinistra/destra a bassa velocità
3. Calcolo comparativo di Yaw, Traslazione, Drift e Rapporti di Scala
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu
import math
import time
import sys

def quat_to_yaw(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)

class OdomDiagnosticsNode(Node):
    def __init__(self):
        super().__init__('diagnose_rotation_odom')
        
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.wheel_sub = self.create_subscription(Odometry, '/odom_wheel', self._wheel_cb, 10)
        self.vio_sub = self.create_subscription(Odometry, '/odom', self._vio_cb, 10)
        self.imu_sub = self.create_subscription(Imu, '/oak/imu/data', self._imu_cb, 10)
        
        # State
        self.wheel_yaw = 0.0
        self.wheel_x = 0.0
        self.wheel_y = 0.0
        self.has_wheel = False
        
        self.vio_yaw = 0.0
        self.vio_x = 0.0
        self.vio_y = 0.0
        self.has_vio = False
        
        self.imu_gyro_z = 0.0
        self.imu_integrated_yaw = 0.0
        self.last_imu_time = None
        self.has_imu = False
        
        self.get_logger().info("📡 Sottoscritto a /odom_wheel, /odom, /oak/imu/data...")

    def _wheel_cb(self, msg: Odometry):
        self.wheel_x = msg.pose.pose.position.x
        self.wheel_y = msg.pose.pose.position.y
        self.wheel_yaw = quat_to_yaw(msg.pose.pose.orientation)
        self.has_wheel = True

    def _vio_cb(self, msg: Odometry):
        self.vio_x = msg.pose.pose.position.x
        self.vio_y = msg.pose.pose.position.y
        self.vio_yaw = quat_to_yaw(msg.pose.pose.orientation)
        self.has_vio = True

    def _imu_cb(self, msg: Imu):
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        gz = msg.angular_velocity.z
        self.imu_gyro_z = gz
        
        if self.last_imu_time is not None:
            dt = t - self.last_imu_time
            if 0 < dt < 0.2:
                # Integrate gyro Z (rad)
                self.imu_integrated_yaw += gz * dt
        self.last_imu_time = t
        self.has_imu = True

    def stop(self):
        msg = Twist()
        for _ in range(5):
            self.cmd_pub.publish(msg)
            time.sleep(0.02)

    def run_rotation_test(self, speed=0.80, duration=1.0):
        self.get_logger().info("⏳ Attesa ricezione primi pacchetti sensori...")
        timeout = time.time() + 5.0
        while time.time() < timeout and not (self.has_wheel and self.has_vio):
            rclpy.spin_once(self, timeout_sec=0.1)

        print("\n" + "="*70)
        print("🎯 INIZIO TEST ROTAZIONE CONTROLLATA MARCUS (0.80 rad/s)")
        print("="*70)
        
        # Reset relative references
        w_yaw_0 = self.wheel_yaw
        w_x_0 = self.wheel_x
        w_y_0 = self.wheel_y
        
        v_yaw_0 = self.vio_yaw
        v_x_0 = self.vio_x
        v_y_0 = self.vio_y
        
        self.imu_integrated_yaw = 0.0
        
        print("\n--- FASE 1: ROTAZIONE A SINISTRA (1.0s @ +0.80 rad/s) ---")
        cmd = Twist()
        cmd.angular.z = speed
        t_end = time.time() + duration
        while time.time() < t_end:
            self.cmd_pub.publish(cmd)
            rclpy.spin_once(self, timeout_sec=0.03)
        self.stop()
        time.sleep(1.0)
        
        # Capture Phase 1 metrics
        for _ in range(15):
            rclpy.spin_once(self, timeout_sec=0.05)
            
        d_wheel_yaw_1 = math.degrees(self.wheel_yaw - w_yaw_0)
        d_vio_yaw_1 = math.degrees(self.vio_yaw - v_yaw_0)
        d_imu_yaw_1 = math.degrees(self.imu_integrated_yaw)
        d_vio_trans_1 = math.hypot(self.vio_x - v_x_0, self.vio_y - v_y_0)
        d_wheel_trans_1 = math.hypot(self.wheel_x - w_x_0, self.wheel_y - w_y_0)
        
        print(f"📊 RISULTATI FASE 1 (Sinistra):")
        print(f"   - Wheel Encoders Yaw: {d_wheel_yaw_1:+.2f}° (Trans: {d_wheel_trans_1:.4f}m)")
        print(f"   - IMU Gyro Z Yaw:    {d_imu_yaw_1:+.2f}°")
        print(f"   - FastFlow VIO Yaw:   {d_vio_yaw_1:+.2f}° (Trans: {d_vio_trans_1:.4f}m)")

        print("\n--- FASE 2: ROTAZIONE A DESTRA (1.0s @ -0.80 rad/s) ---")
        cmd.angular.z = -speed
        t_end = time.time() + duration
        while time.time() < t_end:
            self.cmd_pub.publish(cmd)
            rclpy.spin_once(self, timeout_sec=0.03)
        self.stop()
        time.sleep(1.0)
        
        for _ in range(15):
            rclpy.spin_once(self, timeout_sec=0.05)
            
        d_wheel_yaw_2 = math.degrees(self.wheel_yaw - w_yaw_0)
        d_vio_yaw_2 = math.degrees(self.vio_yaw - v_yaw_0)
        d_imu_yaw_2 = math.degrees(self.imu_integrated_yaw)
        d_vio_trans_2 = math.hypot(self.vio_x - v_x_0, self.vio_y - v_y_0)
        d_wheel_trans_2 = math.hypot(self.wheel_x - w_x_0, self.wheel_y - w_y_0)
        
        print(f"📊 RISULTATI FASE 2 (Destra / Ritorno al punto iniziale):")
        print(f"   - Wheel Encoders Yaw: {d_wheel_yaw_2:+.2f}° (Target residuo: ~0°)")
        print(f"   - IMU Gyro Z Yaw:    {d_imu_yaw_2:+.2f}° (Target residuo: ~0°)")
        print(f"   - FastFlow VIO Yaw:   {d_vio_yaw_2:+.2f}° (Target residuo: ~0°)")
        print(f"   - Total Wheel Trans:  {d_wheel_trans_2:.4f}m")
        print(f"   - Total VIO Trans:    {d_vio_trans_2:.4f}m")

        print("\n" + "="*70)
        print("🔍 ANALISI DISCREPANZE & CALIBRAZIONE")
        print("="*70)
        if abs(d_wheel_yaw_1) > 1.0 and abs(d_imu_yaw_1) > 1.0:
            scale_wheel_imu = d_imu_yaw_1 / d_wheel_yaw_1
            print(f"📈 Rapporto IMU / Wheel: {scale_wheel_imu:.3f}")
        
        if abs(d_vio_yaw_1) > 1.0 and abs(d_imu_yaw_1) > 1.0:
            scale_vio_imu = d_vio_yaw_1 / d_imu_yaw_1
            print(f"📈 Rapporto VIO / IMU:   {scale_vio_imu:.3f}")
            
        if abs(d_vio_yaw_1) > 1.0 and abs(d_wheel_yaw_1) > 1.0:
            scale_vio_wheel = d_vio_yaw_1 / d_wheel_yaw_1
            print(f"📈 Rapporto VIO / Wheel: {scale_vio_wheel:.3f}")
        print("="*70 + "\n")

def main():
    rclpy.init()
    node = OdomDiagnosticsNode()
    try:
        node.run_rotation_test(speed=0.25, duration=1.5)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
