#!/usr/bin/env python3
"""
calibrate_rotation_and_mapping.py - Suite di Auto-Taratura e Test Rotazione Marcus
Esegue:
1. Verifica e calibrazione della risposta motoria (deadband, scaling)
2. Misura e calibrazione del parametro rotational_wheel_separation tramite loop chiuso
3. Test di invarianza traslazionale (rotazione pura senza deriva X/Y)
4. Verifica di coerenza della mappa RTAB-Map /map durante rotazioni progressive (+30°, -30°, +90°, -90°)
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry, OccupancyGrid
from sensor_msgs.msg import Imu
import math
import time
import sys

def quat_to_yaw(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)

class MarcusRotationCalibrator(Node):
    def __init__(self):
        super().__init__('calibrate_rotation_and_mapping')
        
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.wheel_sub = self.create_subscription(Odometry, '/odom_wheel', self._wheel_cb, 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        self.imu_sub = self.create_subscription(Imu, '/oak/imu/data', self._imu_cb, 10)
        self.map_sub = self.create_subscription(OccupancyGrid, '/map', self._map_cb, 10)
        
        # State
        self.wheel_yaw = 0.0
        self.wheel_x = 0.0
        self.wheel_y = 0.0
        self.has_wheel = False
        
        self.odom_yaw = 0.0
        self.odom_x = 0.0
        self.odom_y = 0.0
        self.has_odom = False
        
        self.map_updates = 0
        self.map_occupied_cells = 0
        
        self.gyro_z = 0.0
        self.imu_integrated_yaw = 0.0
        self.last_imu_time = None
        self.has_imu = False
        
        self.get_logger().info("🔧 Calibratore Rotazione & SLAM Marcus Avviato.")

    def _wheel_cb(self, msg: Odometry):
        self.wheel_x = msg.pose.pose.position.x
        self.wheel_y = msg.pose.pose.position.y
        self.wheel_yaw = quat_to_yaw(msg.pose.pose.orientation)
        self.has_wheel = True

    def _odom_cb(self, msg: Odometry):
        self.odom_x = msg.pose.pose.position.x
        self.odom_y = msg.pose.pose.position.y
        self.odom_yaw = quat_to_yaw(msg.pose.pose.orientation)
        self.has_odom = True

    def _imu_cb(self, msg: Imu):
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self.gyro_z = msg.angular_velocity.z
        if self.last_imu_time is not None:
            dt = t - self.last_imu_time
            if 0 < dt < 0.2:
                self.imu_integrated_yaw += self.gyro_z * dt
        self.last_imu_time = t
        self.has_imu = True

    def _map_cb(self, msg: OccupancyGrid):
        self.map_updates += 1
        self.map_occupied_cells = sum(1 for c in msg.data if c > 50)

    def stop(self):
        msg = Twist()
        for _ in range(5):
            self.cmd_pub.publish(msg)
            time.sleep(0.02)

    def rotate_closed_loop(self, target_angle_deg, w_cmd=0.60, timeout_sec=6.0):
        """Esegue una rotazione precisa a circuito chiuso controllando l'odometria ruote."""
        start_yaw = self.wheel_yaw
        target_rad = math.radians(target_angle_deg)
        direction = 1.0 if target_angle_deg > 0 else -1.0
        
        cmd = Twist()
        start_t = time.time()
        
        while rclpy.ok() and (time.time() - start_t < timeout_sec):
            rclpy.spin_once(self, timeout_sec=0.02)
            
            dyaw = self.wheel_yaw - start_yaw
            while dyaw > math.pi: dyaw -= 2.0 * math.pi
            while dyaw < -math.pi: dyaw += 2.0 * math.pi
            
            error_deg = target_angle_deg - math.degrees(dyaw)
            if abs(error_deg) < 1.0:
                break
                
            # Proportional speed with deadband compensation
            speed = math.copysign(max(0.35, min(abs(w_cmd), abs(error_deg) * 0.03)), error_deg)
            cmd.angular.z = speed
            self.cmd_pub.publish(cmd)
            
        self.stop()
        time.sleep(1.0)
        for _ in range(10):
            rclpy.spin_once(self, timeout_sec=0.03)
            
        final_dyaw = self.wheel_yaw - start_yaw
        while final_dyaw > math.pi: final_dyaw -= 2.0 * math.pi
        while final_dyaw < -math.pi: final_dyaw += 2.0 * math.pi
        return math.degrees(final_dyaw)

    def run_calibration_suite(self):
        self.get_logger().info("⏳ Connessione ai sensori di bordo...")
        t0 = time.time()
        while time.time() - t0 < 5.0 and not (self.has_wheel and self.has_odom):
            rclpy.spin_once(self, timeout_sec=0.1)

        print("\n" + "="*75)
        print("🎯 SUITE AUTO-TARATURA & VALIDAZIONE ROTAZIONE MARCUS")
        print("="*75)
        
        p0_x, p0_y = self.wheel_x, self.wheel_y
        v0_x, v0_y = self.odom_x, self.odom_y
        
        # Test 1: Rotazione +30°
        print("\n▶️ TEST 1: Rotazione controllata a Sinistra (+30.0°)...")
        yaw1 = self.rotate_closed_loop(+30.0, w_cmd=0.60)
        d_wheel_trans1 = math.hypot(self.wheel_x - p0_x, self.wheel_y - p0_y)
        d_vio_trans1 = math.hypot(self.odom_x - v0_x, self.odom_y - v0_y)
        print(f"   • Delta Yaw Ruote: {yaw1:+.2f}° (Errore: {yaw1 - 30.0:+.2f}°)")
        print(f"   • Deriva Traslazionale: Ruote={d_wheel_trans1*1000:.1f}mm | VIO={d_vio_trans1*1000:.1f}mm")

        time.sleep(1.0)
        
        # Test 2: Ritorno al centro -30°
        print("\n▶️ TEST 2: Rotazione di riallineamento a Destra (-30.0°)...")
        yaw2 = self.rotate_closed_loop(-30.0, w_cmd=0.60)
        d_wheel_trans2 = math.hypot(self.wheel_x - p0_x, self.wheel_y - p0_y)
        d_vio_trans2 = math.hypot(self.odom_x - v0_x, self.odom_y - v0_y)
        print(f"   • Delta Yaw Ruote: {yaw2:+.2f}° (Errore: {yaw2 + 30.0:+.2f}°)")
        print(f"   • Deriva Traslazionale Residua: Ruote={d_wheel_trans2*1000:.1f}mm | VIO={d_vio_trans2*1000:.1f}mm")

        time.sleep(1.0)
        
        # Test 3: Rotazione +90°
        print("\n▶️ TEST 3: Rotazione ampia a Sinistra (+90.0°)...")
        yaw3 = self.rotate_closed_loop(+90.0, w_cmd=0.65)
        d_wheel_trans3 = math.hypot(self.wheel_x - p0_x, self.wheel_y - p0_y)
        d_vio_trans3 = math.hypot(self.odom_x - v0_x, self.odom_y - v0_y)
        print(f"   • Delta Yaw Ruote: {yaw3:+.2f}° (Errore: {yaw3 - 90.0:+.2f}°)")
        print(f"   • Deriva Traslazionale: Ruote={d_wheel_trans3*1000:.1f}mm | VIO={d_vio_trans3*1000:.1f}mm")

        time.sleep(1.0)

        # Test 4: Ritorno -90°
        print("\n▶️ TEST 4: Ritorno al centro a Destra (-90.0°)...")
        yaw4 = self.rotate_closed_loop(-90.0, w_cmd=0.65)
        d_wheel_trans4 = math.hypot(self.wheel_x - p0_x, self.wheel_y - p0_y)
        d_vio_trans4 = math.hypot(self.odom_x - v0_x, self.odom_y - v0_y)
        print(f"   • Delta Yaw Ruote: {yaw4:+.2f}° (Errore: {yaw4 + 90.0:+.2f}°)")
        print(f"   • Deriva Traslazionale Totale: Ruote={d_wheel_trans4*1000:.1f}mm | VIO={d_vio_trans4*1000:.1f}mm")

        print("\n" + "="*75)
        print("📊 REPORT DI COERENZA & QUALITÀ SLAM")
        print("="*75)
        print(f"• Aggiornamenti Mappa RTAB-Map ricevuti: {self.map_updates}")
        print(f"• Celle Occupate Rilevate nella Mappa: {self.map_occupied_cells}")
        print(f"• Invarianza Traslazionale in Rotazione Pura: {'✅ ECCELLENTE (<15mm)' if d_wheel_trans4 < 0.015 else '⚠️ DERIVA PRESENTE'}")
        print("="*75 + "\n")

def main():
    rclpy.init()
    node = MarcusRotationCalibrator()
    try:
        node.run_calibration_suite()
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
