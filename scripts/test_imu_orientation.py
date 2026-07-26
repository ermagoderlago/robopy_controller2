#!/usr/bin/env python3
"""
Test Diagnostico per l'Identificazione dell'Orientamento IMU Chassis (ESP32)
- Esegue un avanzamento di 20 cm (+0.10 m/s per 2.0s)
- Pausa di 1.0s
- Esegue un arretramento di 20 cm (-0.10 m/s per 2.0s)
- Pausa di 1.0s
- Esegue una rotazione a sinistra (+0.50 rad/s per ~1.0s)
- Rileva e registra i valori medi dell'accelerometro (ax, ay, az) e del giroscopio (gx, gy, gz)
  pubblicati da /imu/esp32 per mappare la rotazione fisica a 90° sullo standard ROS REP-103.
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu
import math
import time

class TestIMUOrientationNode(Node):
    def __init__(self):
        super().__init__('test_imu_orientation')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.imu_sub_esp = self.create_subscription(Imu, '/imu/esp32', self._imu_cb, 10)
        self.imu_sub_oak = self.create_subscription(Imu, '/oak/imu/data', self._imu_cb, 10)
        
        self.imu_received = False
        self.last_imu_msg = None
        
        # Buffer per la media campionaria durante ogni fase
        self.samples_accel = []
        self.samples_gyro = []
        self.is_recording = False

    def _imu_cb(self, msg: Imu):
        self.last_imu_msg = msg
        self.imu_received = True
        
        if self.is_recording:
            ax = msg.linear_acceleration.x
            ay = msg.linear_acceleration.y
            az = msg.linear_acceleration.z
            
            gx = msg.angular_velocity.x
            gy = msg.angular_velocity.y
            gz = msg.angular_velocity.z
            
            self.samples_accel.append((ax, ay, az))
            self.samples_gyro.append((gx, gy, gz))

    def publish_stop(self):
        msg = Twist()
        for _ in range(5):
            self.cmd_pub.publish(msg)
            time.sleep(0.02)

    def start_recording(self):
        self.samples_accel.clear()
        self.samples_gyro.clear()
        self.is_recording = True

    def stop_recording(self):
        self.is_recording = False
        if not self.samples_accel:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
            
        avg_ax = sum(s[0] for s in self.samples_accel) / len(self.samples_accel)
        avg_ay = sum(s[1] for s in self.samples_accel) / len(self.samples_accel)
        avg_az = sum(s[2] for s in self.samples_accel) / len(self.samples_accel)
        
        avg_gx = sum(s[0] for s in self.samples_gyro) / len(self.samples_gyro)
        avg_gy = sum(s[1] for s in self.samples_gyro) / len(self.samples_gyro)
        avg_gz = sum(s[2] for s in self.samples_gyro) / len(self.samples_gyro)
        
        return (avg_ax, avg_ay, avg_az), (avg_gx, avg_gy, avg_gz)

    def run_motion_phase(self, linear_x, angular_z, duration_sec, phase_name):
        print(f"\n⚙️ Avvio Fase: {phase_name} (vx={linear_x} m/s, wz={angular_z} rad/s per {duration_sec}s)...")
        self.start_recording()
        
        msg = Twist()
        msg.linear.x = float(linear_x)
        msg.angular.z = float(angular_z)
        
        start_t = time.time()
        while rclpy.ok() and (time.time() - start_t < duration_sec):
            self.cmd_pub.publish(msg)
            rclpy.spin_once(self, timeout_sec=0.02)
            
        self.publish_stop()
        acc, gyro = self.stop_recording()
        
        print(f"  📊 Media Accelerometro (ax, ay, az): ({acc[0]:+.3f}, {acc[1]:+.3f}, {acc[2]:+.3f}) m/s²")
        print(f"  📊 Media Giroscopio    (gx, gy, gz): ({gyro[0]:+.3f}, {gyro[1]:+.3f}, {gyro[2]:+.3f}) rad/s")
        return acc, gyro

def main():
    rclpy.init()
    node = TestIMUOrientationNode()
    
    print("\n=================================================================")
    print(" 🧭 TEST DIAGNOSTICO ORIENTAMENTO IMU CHASSIS (ESP32)")
    print(" 📐 Mappatura Assi Fisici Ruotati di 90° verso ROS REP-103")
    print("=================================================================")
    print("Attesa connessione IMU Chassis /imu/esp32...")
    
    start_t = time.time()
    while rclpy.ok() and not node.imu_received:
        rclpy.spin_once(node, timeout_sec=0.1)
        if time.time() - start_t > 25.0:
            print("❌ Impossibile connettersi a /imu/esp32 entro 25 secondi.")
            node.destroy_node()
            rclpy.shutdown()
            return
            
    print("✅ IMU Chassis /imu/esp32 connessa!\n")
    
    # 1. Misura del bias di stazionarietà (Robot Fermo)
    print("📍 Misura Bias IMU a Robot Fermo (2.0s)...")
    acc_still, gyro_still = node.run_motion_phase(0.0, 0.0, 2.0, "STAZIONARIO")
    time.sleep(1.0)
    
    # 2. Avanzamento 20 cm (+X nominale)
    acc_fwd, gyro_fwd = node.run_motion_phase(0.10, 0.0, 2.0, "AVANZAMENTO 20 CM")
    time.sleep(1.0)
    
    # 3. Arretramento 20 cm (-X nominale)
    acc_bwd, gyro_bwd = node.run_motion_phase(-0.10, 0.0, 2.0, "ARRETRAMENTO 20 CM")
    time.sleep(1.0)
    
    # 4. Rotazione a Sinistra (+Z nominale)
    acc_left, gyro_left = node.run_motion_phase(0.0, 0.50, 1.5, "ROTAZIONE SINISTRA 30°")
    
    # Analisi Delta Accelerazione per identificare l'asse X di avanzamento
    delta_ax_fwd = acc_fwd[0] - acc_still[0]
    delta_ay_fwd = acc_fwd[1] - acc_still[1]
    delta_az_fwd = acc_fwd[2] - acc_still[2]
    
    deltas = {'ax': delta_ax_fwd, 'ay': delta_ay_fwd, 'az': delta_az_fwd}
    primary_x_axis = max(deltas, key=lambda k: abs(deltas[k]))
    sign_x = "+" if deltas[primary_x_axis] > 0 else "-"
    
    # Analisi Giroscopio in Rotazione a Sinistra per identificare l'asse Z di rotazione
    delta_gx_rot = gyro_left[0] - gyro_still[0]
    delta_gy_rot = gyro_left[1] - gyro_still[1]
    delta_gz_rot = gyro_left[2] - gyro_still[2]
    
    deltas_g = {'gx': delta_gx_rot, 'gy': delta_gy_rot, 'gz': delta_gz_rot}
    primary_z_axis = max(deltas_g, key=lambda k: abs(deltas_g[k]))
    sign_z = "+" if deltas_g[primary_z_axis] > 0 else "-"

    print("\n=================================================================")
    print(" 📊 RISULTATI ANALISI MAPPATURA ASSI IMU CHASSIS")
    print("=================================================================")
    print(f"• Asse di Avanzamento Principale (ROS +X): {sign_x}{primary_x_axis} (Delta Acc: {deltas[primary_x_axis]:+.3f} m/s²)")
    print(f"• Asse di Rotazione Sinistra   (ROS +Z): {sign_z}{primary_z_axis} (Delta Gyro: {deltas_g[primary_z_axis]:+.3f} rad/s)")
    print("=================================================================")
    print("🛑 TEST DIAGNOSTICO COMPLETATO con successo!")
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
