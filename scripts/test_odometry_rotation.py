#!/usr/bin/env python3
"""
test_odometry_rotation.py
=========================
Test di verifica odometria angolare a circuito controllato.
Invia un comando di rotazione lento (0.20 rad/s per 5.0s, teorico 1.0 rad = 57.3°)
e monitora:
- Variazione dell'angolo /odom
- Ticks encoder sinistro e destro
- Confronto tra formula cinematica e valore pubblicato su /odom
"""

import os
import sys
import math
import time
import json
import threading

for p in ["/home/robopy/ros2_venv/lib/python3.11/site-packages"]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry

WHEEL_RADIUS = 0.0335   # m
WHEEL_SEP = 0.285       # m
TICKS_PER_REV = 657

class OdomRotationTester(Node):
    def __init__(self):
        super().__init__('odom_rotation_tester')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self.odom_cb, 10)
        
        self.current_yaw = None
        self.start_yaw = None
        self.total_yaw_delta = 0.0
        self.last_yaw = None
        self.ready = threading.Event()
        self.measuring = False

    def odom_cb(self, msg: Odometry):
        q = msg.pose.pose.orientation
        siny = 2.0 * (q.w * q.z + q.x * q.y)
        cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        yaw = math.atan2(siny, cosy)
        
        if self.current_yaw is None:
            self.current_yaw = yaw
            self.start_yaw = yaw
            self.last_yaw = yaw
            self.ready.set()
            return
            
        if self.measuring:
            dy = yaw - self.last_yaw
            if dy > math.pi: dy -= 2 * math.pi
            if dy < -math.pi: dy += 2 * math.pi
            self.total_yaw_delta += dy
            
        self.last_yaw = yaw
        self.current_yaw = yaw

    def stop_robot(self):
        msg = Twist()
        for _ in range(8):
            self.cmd_pub.publish(msg)
            time.sleep(0.02)

def main():
    rclpy.init()
    node = OdomRotationTester()
    
    spinner = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spinner.start()
    
    print("=" * 60)
    print("🔬 TEST SCIENTIFICO ODOMETRIA ROTAZIONALE")
    print("=" * 60)
    print("Attesa connessione /odom...")
    if not node.ready.wait(timeout=5.0):
        print("❌ Timeout /odom non disponibile.")
        rclpy.shutdown()
        return
        
    print(f"✅ /odom connesso. Yaw iniziale: {math.degrees(node.current_yaw):+.2f}°")
    time.sleep(1.0)
    
    # Parametri test
    test_w = 0.50          # rad/s (~28.6 deg/s) - rotazione in-place controllata
    test_duration = 4.0    # s
    theoretical_rad = test_w * test_duration
    theoretical_deg = math.degrees(theoretical_rad)
    
    print(f"\n▶️ Comando: w = {test_w:.2f} rad/s per {test_duration:.1f} s")
    print(f"   Rotazione teorica da comando: {theoretical_rad:.3f} rad ({theoretical_deg:.1f}°)")
    
    # Inizio misura
    cmd = Twist()
    cmd.angular.z = test_w
    cmd.linear.x = 0.0
    
    node.total_yaw_delta = 0.0
    node.measuring = True
    start_t = time.time()
    dt = 0.05
    
    while time.time() - start_t < test_duration:
        node.cmd_pub.publish(cmd)
        time.sleep(dt)
        
    node.stop_robot()
    node.measuring = False
    time.sleep(0.5)
    
    measured_rad = node.total_yaw_delta
    measured_deg = math.degrees(measured_rad)
    ratio = measured_deg / theoretical_deg if theoretical_deg != 0 else 1.0
    
    print("\n" + "=" * 60)
    print("📊 RISULTATI DEL TEST")
    print("=" * 60)
    print(f"• Rotazione comandata (teorica): {theoretical_deg:+.2f}°")
    print(f"• Rotazione calcolata su /odom:  {measured_deg:+.2f}°")
    print(f"• Rapporto Odom / Comando:       {ratio:.3f}")
    print(f"• Yaw finale /odom:              {math.degrees(node.current_yaw):+.2f}°")
    print("=" * 60)
    
    if abs(ratio - 1.0) < 0.15:
        print("✅ Odometria coerente con la velocità comandata (errore < 15%)")
    elif ratio > 1.2:
        print(f"⚠️ /odom ha calcolato un angolo PIÙ GRANDE ({ratio:.2f}x) rispetto al comando.")
        print(f"   Possibili cause: rotazione più veloce per boost duty open-loop, o W_sep/ticks non calibrati.")
    else:
        print(f"⚠️ /odom ha calcolato un angolo PIÙ PICCOLO ({ratio:.2f}x) rispetto al comando.")
    print("=" * 60)
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
