#!/usr/bin/env python3
"""
test_spin_360_cpr1440.py
Esegue uno spin di 360° a 20Hz (omega=0.30 rad/s) e misura:
- Angolo iniziale /odom
- Angolo finale /odom
- Delta totale integrato
Verifica se con ticks_per_rev=1440 l'angolo integrato è vicino a 360° (prima era 740°!).
"""

import os
import sys
import math
import time
import threading

for p in ["/home/robopy/ros2_venv/lib/python3.11/site-packages"]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry

class SpinTester(Node):
    def __init__(self):
        super().__init__('spin_tester_1440')
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.sub = self.create_subscription(Odometry, '/odom', self.odom_cb, 10)
        self.start_yaw = None
        self.current_yaw = None
        self.last_yaw = None
        self.accumulated_rad = 0.0
        self.ready = threading.Event()
        self.running = False

    def odom_cb(self, msg: Odometry):
        q = msg.pose.pose.orientation
        siny = 2.0 * (q.w * q.z + q.x * q.y)
        cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        yaw = math.atan2(siny, cosy)
        
        if self.start_yaw is None:
            self.start_yaw = yaw
            self.last_yaw = yaw
            self.current_yaw = yaw
            self.ready.set()
            return
            
        if self.running:
            dy = yaw - self.last_yaw
            if dy > math.pi: dy -= 2 * math.pi
            if dy < -math.pi: dy += 2 * math.pi
            self.accumulated_rad += dy
            
        self.last_yaw = yaw
        self.current_yaw = yaw

    def stop(self):
        t = Twist()
        for _ in range(8):
            self.pub.publish(t)
            time.sleep(0.02)

def main():
    rclpy.init()
    node = SpinTester()
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    print("=" * 60)
    print("🔄 TEST SPIN 360° CON CPR=1440 & WHEEL_SEP=0.285")
    print("=" * 60)
    if not node.ready.wait(timeout=5.0):
        print("❌ Timeout connessione /odom")
        rclpy.shutdown()
        return

    omega = 0.60
    duration = 10.5  # secondi teorici per 1 giro (2*pi / 0.60)
    dt = 1.0 / 20.0

    print(f"Yaw iniziale /odom: {math.degrees(node.start_yaw):+.1f}°")
    print(f"Avvio comando continuo a 20Hz: omega={omega:.2f} rad/s per {duration:.1f}s...")
    time.sleep(1.0)

    t = Twist()
    t.angular.z = omega
    t.linear.x = 0.0

    node.running = True
    start = time.time()
    while time.time() - start < duration:
        node.pub.publish(t)
        time.sleep(dt)

    node.running = False
    node.stop()
    time.sleep(0.5)

    accum_deg = math.degrees(abs(node.accumulated_rad))
    final_deg = math.degrees(node.current_yaw)

    print("\n" + "=" * 60)
    print("📊 RISULTATO ROTAZIONE 360°")
    print("=" * 60)
    print(f"• Angolo iniziale:           {math.degrees(node.start_yaw):+.1f}°")
    print(f"• Angolo finale:             {final_deg:+.1f}°")
    print(f"• Totale /odom integrato:    {accum_deg:.1f}°")
    print(f"• Valore atteso teorico:     360.0°")
    print(f"• Errore residuo:            {accum_deg - 360.0:+.1f}° ({(accum_deg / 360.0):.2f}x)")
    print("=" * 60)
    
    if abs(accum_deg - 360.0) < 30.0:
        print("🎉 ECCELLENTE! L'odometria è calibrata e allineata a 360°!")
    elif accum_deg < 50.0:
        print("⚠️ Il robot non ha ruotato a sufficienza (attrito o stiction).")
    else:
        print(f"ℹ️ Rapporto scala attuale: {accum_deg / 360.0:.3f}")

    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
