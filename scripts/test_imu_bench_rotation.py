#!/usr/bin/env python3
"""
test_imu_bench_rotation.py - Verifica Live Giroscopio OAK-D con Compensazione Inclinazione 8° UP
Ruota Marcus a mano sul banco di 90° o 180° per verificare la stima d'imbardata IMU a 200 Hz.
"""

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu
import math
import time
import sys

class IMUBenchTest(Node):
    def __init__(self):
        super().__init__('test_imu_bench_rotation')
        self.imu_sub = self.create_subscription(Imu, '/oak/imu/data', self._imu_cb, 10)
        self.esp_sub = self.create_subscription(Imu, '/imu/esp32', self._esp_cb, 10)
        
        # OAK-D Camera pitch UP 8.0° = -0.1396 rad (REP-103)
        self.pitch_rad = math.radians(-8.0)
        
        self.cam_yaw_deg = 0.0
        self.last_cam_t = None
        
        self.esp_yaw_deg = 0.0
        self.last_esp_t = None
        
        self.has_cam = False

    def _imu_cb(self, msg: Imu):
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        gx = msg.angular_velocity.x
        gy = msg.angular_velocity.y
        gz = msg.angular_velocity.z
        
        # Compensate camera 8° pitch UP rotation into robot base frame
        # In base_link: +Z is vertical UP (Yaw), +X is Forward, +Y is Left
        w_z_robot = -gx * math.sin(self.pitch_rad) + gz * math.cos(self.pitch_rad)
        
        if self.last_cam_t is not None:
            dt = t - self.last_cam_t
            if 0 < dt < 0.2 and abs(w_z_robot) > 0.01:
                self.cam_yaw_deg += math.degrees(w_z_robot * dt)
        self.last_cam_t = t
        self.has_cam = True

    def _esp_cb(self, msg: Imu):
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        gz = msg.angular_velocity.z
        if self.last_esp_t is not None:
            dt = t - self.last_esp_t
            if 0 < dt < 0.2 and abs(gz) > 0.01:
                self.esp_yaw_deg += math.degrees(gz * dt)
        self.last_esp_t = t

def main():
    rclpy.init()
    node = IMUBenchTest()
    print("\n" + "="*70)
    print("📡 TEST GIROSCOPIO OAK-D (Compensato 8° Pitch UP) & IMU CHASSIS")
    print("="*70)
    print("Attesa ricezione pacchetti IMU...")
    
    t0 = time.time()
    while time.time() - t0 < 3.0 and not node.has_cam:
        rclpy.spin_once(node, timeout_sec=0.1)
        
    print("🟢 Ricezione attiva! Ruota Marcus a mano (es. 90° a sinistra o 180°).")
    print("Premi Ctrl+C per terminare il test.\n")
    
    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.05)
            sys.stdout.write(f"\r🧭 YAW INTEGRATO: IMU Camera (8° pitch) = {node.cam_yaw_deg:+6.1f}° | IMU Chassis = {node.esp_yaw_deg:+6.1f}°")
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass

    print("\n\n" + "="*70)
    print(f"📊 REPORT FINALE ROTAZIONE:")
    print(f"   • Yaw IMU Camera OAK-D: {node.cam_yaw_deg:+.2f}°")
    print(f"   • Yaw IMU ESP32 Chassis: {node.esp_yaw_deg:+.2f}°")
    print("="*70 + "\n")
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
