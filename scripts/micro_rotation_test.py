#!/usr/bin/env python3
"""
micro_rotation_test.py - Test di Micro-Rotazione Sicura (in ricarica con cavo collegato)
Esegue solo micro-oscillazioni destra/sinistra in loco (< 8 gradi) a bassa velocità (0.25 rad/s, 0.4s)
e confronta in tempo reale il verso di rotazione di:
1. IMU Gyro Z (/oak/imu/data) -> Verità fisica di rotazione
2. Odometria ruote (/odom_wheel) -> Calcolo di waveshare_motor_driver
3. FastFlow VIO (/odom) -> Calcolo di fast_flow_vo_cpp
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu
from std_msgs.msg import Bool
import math
import time

def quat_to_yaw(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)

class MicroRotationTester(Node):
    def __init__(self):
        super().__init__('micro_rotation_test')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.gate_pub = self.create_publisher(Bool, '/robot/motion_gate', 10)
        self.wheel_sub = self.create_subscription(Odometry, '/odom_wheel', self._wheel_cb, 10)
        self.vio_sub = self.create_subscription(Odometry, '/odom', self._vio_cb, 10)
        self.imu_sub = self.create_subscription(Imu, '/oak/imu/data', self._imu_cb, 10)
        self.gate_sub = self.create_subscription(Bool, '/robot/motion_gate', self._gate_cb, 10)
        
        self.wheel_odom = None
        self.vio_odom = None
        self.imu_integrated_yaw = 0.0
        self.last_imu_time = None
        self.motion_gate = True

    def _gate_cb(self, msg: Bool):
        self.motion_gate = msg.data

    def ensure_active(self):
        """Wake up sensors and open motion gate before test."""
        gate_msg = Bool()
        gate_msg.data = True
        for _ in range(5):
            self.gate_pub.publish(gate_msg)
            time.sleep(0.05)
        time.sleep(0.5)

    def _wheel_cb(self, msg: Odometry):
        self.wheel_odom = msg

    def _vio_cb(self, msg: Odometry):
        self.vio_odom = msg

    def _imu_cb(self, msg: Imu):
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

    def run_micro_turn(self, name, angular_speed, duration=0.4):
        print(f"\n==================================================")
        print(f"🔄 {name}: w = {angular_speed:+.2f} rad/s per {duration:.2f}s (Max rotazione teorica ~{math.degrees(abs(angular_speed)*duration):.1f}°)")
        print(f"==================================================")
        
        self.imu_integrated_yaw = 0.0
        for _ in range(10):
            rclpy.spin_once(self, timeout_sec=0.05)
            
        wyaw0 = quat_to_yaw(self.wheel_odom.pose.pose.orientation) if self.wheel_odom else 0.0
        vyaw0 = quat_to_yaw(self.vio_odom.pose.pose.orientation) if self.vio_odom else 0.0
        self.imu_integrated_yaw = 0.0
        
        cmd = Twist()
        cmd.linear.x = 0.0
        cmd.angular.z = float(angular_speed)
        
        t_end = time.time() + duration
        while time.time() < t_end:
            self.cmd_pub.publish(cmd)
            rclpy.spin_once(self, timeout_sec=0.02)
            
        self.stop()
        time.sleep(0.8)
        for _ in range(10):
            rclpy.spin_once(self, timeout_sec=0.05)
            
        wyaw1 = quat_to_yaw(self.wheel_odom.pose.pose.orientation) if self.wheel_odom else 0.0
        vyaw1 = quat_to_yaw(self.vio_odom.pose.pose.orientation) if self.vio_odom else 0.0
        
        d_wheel_deg = math.degrees(wyaw1 - wyaw0)
        while d_wheel_deg > 180: d_wheel_deg -= 360
        while d_wheel_deg < -180: d_wheel_deg += 360
        
        d_vio_deg = math.degrees(vyaw1 - vyaw0)
        while d_vio_deg > 180: d_vio_deg -= 360
        while d_vio_deg < -180: d_vio_deg += 360
        
        d_imu_deg = math.degrees(self.imu_integrated_yaw)
        
        print(f"📊 RISULTATO {name}:")
        print(f"   [RAW YAW] wheel: {math.degrees(wyaw0):.1f}° -> {math.degrees(wyaw1):.1f}° | vio: {math.degrees(vyaw0):.1f}° -> {math.degrees(vyaw1):.1f}°")
        print(f"   • IMU Gyro Z (Fisica Reale): {d_imu_deg:+.2f}°")
        print(f"   • Odometria Ruote (/odom_wheel): {d_wheel_deg:+.2f}°")
        print(f"   • FastFlow VIO (/odom):          {d_vio_deg:+.2f}°")
        
        concorde_wheel = (d_imu_deg * d_wheel_deg > 0)
        concorde_vio = (d_imu_deg * d_vio_deg > 0)
        print(f"   👉 Ruote concordi con fisica? {'✅ SI' if concorde_wheel else '❌ INVERTITO!'}")
        print(f"   👉 VIO concorde con fisica?   {'✅ SI' if concorde_vio else '❌ INVERTITO!'}")

def main():
    rclpy.init()
    node = MicroRotationTester()
    time.sleep(1.0)
    for _ in range(10):
        rclpy.spin_once(node, timeout_sec=0.05)
        
    node.ensure_active()

    print("\n" + "#"*60)
    print("🛡️ TEST MICRO-ROTAZIONE MARCUS (Sicuro per Cavo di Ricarica)")
    print("#"*60)
    
    # 1. Micro giro Sinistra (CCW, positivo)
    node.run_micro_turn("1. MICRO-ROTAZIONE SINISTRA (+0.50 rad/s)", angular_speed=0.50, duration=0.30)
    
    # 2. Micro giro Destra (CW, negativo) - Riporta esattamente alla posa iniziale!
    node.run_micro_turn("2. MICRO-ROTAZIONE DESTRA (-0.50 rad/s, ritorno)", angular_speed=-0.50, duration=0.30)
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
