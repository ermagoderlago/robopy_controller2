#!/usr/bin/env python3
"""
test_compact_room_calibration.py - Test Compatto in Spazio Confinato (Camera da Letto, Max 20-30cm)
Esegue 4 micro-step deterministici a circuito chiuso con controllo di sicurezza hardware:
1. Rotazione pura sul posto a Sinistra (+25°)
2. Rotazione pura sul posto a Destra (-25° ritorno a 0°)
3. Avanzamento rettilineo controllato (+20 cm)
4. Retromarcia rettilinea controllata (-20 cm ritorno al punto iniziale)
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import math
import time
import sys

def quat_to_yaw(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)

class CompactRoomTest(Node):
    def __init__(self):
        super().__init__('test_compact_room_calibration')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.wheel_sub = self.create_subscription(Odometry, '/odom_wheel', self._wheel_cb, 10)
        self.odom = None
        self.has_odom = False

    def _wheel_cb(self, msg: Odometry):
        self.odom = msg
        self.has_odom = True

    def stop_robot(self):
        msg = Twist()
        for _ in range(5):
            self.cmd_pub.publish(msg)
            time.sleep(0.02)

    def rotate_in_place(self, target_deg, w_speed=0.45, timeout=4.0):
        """Esegue rotazione controllata sul posto fermandosi al raggiungimento del target."""
        start_yaw = quat_to_yaw(self.odom.pose.pose.orientation)
        start_x = self.odom.pose.pose.position.x
        start_y = self.odom.pose.pose.position.y
        
        cmd = Twist()
        sign = 1.0 if target_deg > 0 else -1.0
        cmd.angular.z = sign * abs(w_speed)
        
        t0 = time.time()
        while rclpy.ok() and (time.time() - t0 < timeout):
            rclpy.spin_once(self, timeout_sec=0.02)
            current_yaw = quat_to_yaw(self.odom.pose.pose.orientation)
            dyaw = current_yaw - start_yaw
            while dyaw > math.pi: dyaw -= 2.0 * math.pi
            while dyaw < -math.pi: dyaw += 2.0 * math.pi
            
            accum_deg = math.degrees(dyaw)
            if sign > 0 and accum_deg >= target_deg:
                break
            if sign < 0 and accum_deg <= target_deg:
                break
            self.cmd_pub.publish(cmd)
            
        self.stop_robot()
        time.sleep(1.0)
        for _ in range(5):
            rclpy.spin_once(self, timeout_sec=0.03)
            
        final_yaw = quat_to_yaw(self.odom.pose.pose.orientation)
        final_x = self.odom.pose.pose.position.x
        final_y = self.odom.pose.pose.position.y
        
        d_yaw_deg = math.degrees(final_yaw - start_yaw)
        while d_yaw_deg > 180: d_yaw_deg -= 360
        while d_yaw_deg < -180: d_yaw_deg += 360
        
        drift_trans = math.hypot(final_x - start_x, final_y - start_y)
        return d_yaw_deg, drift_trans

    def translate_straight(self, target_meters, v_speed=0.15, timeout=3.0):
        """Esegue traslazione rettilinea controllata fermandosi al target in metri."""
        start_x = self.odom.pose.pose.position.x
        start_y = self.odom.pose.pose.position.y
        start_yaw = quat_to_yaw(self.odom.pose.pose.orientation)
        
        cmd = Twist()
        sign = 1.0 if target_meters > 0 else -1.0
        cmd.linear.x = sign * abs(v_speed)
        
        t0 = time.time()
        while rclpy.ok() and (time.time() - t0 < timeout):
            rclpy.spin_once(self, timeout_sec=0.02)
            cur_x = self.odom.pose.pose.position.x
            cur_y = self.odom.pose.pose.position.y
            dist = math.hypot(cur_x - start_x, cur_y - start_y)
            if dist >= abs(target_meters):
                break
            self.cmd_pub.publish(cmd)
            
        self.stop_robot()
        time.sleep(1.0)
        for _ in range(5):
            rclpy.spin_once(self, timeout_sec=0.03)
            
        final_x = self.odom.pose.pose.position.x
        final_y = self.odom.pose.pose.position.y
        final_yaw = quat_to_yaw(self.odom.pose.pose.orientation)
        
        dx = final_x - start_x
        dy = final_y - start_y
        dist = math.hypot(dx, dy)
        d_yaw_deg = math.degrees(final_yaw - start_yaw)
        while d_yaw_deg > 180: d_yaw_deg -= 360
        while d_yaw_deg < -180: d_yaw_deg += 360
        
        return dist, dx, dy, d_yaw_deg

def main():
    rclpy.init()
    node = CompactRoomTest()
    print("⏳ Connessione odometria /odom_wheel...")
    t0 = time.time()
    while time.time() - t0 < 5.0 and not node.has_odom:
        rclpy.spin_once(node, timeout_sec=0.1)
        
    if not node.has_odom:
        print("❌ Timeout connessione odometria!")
        node.destroy_node()
        rclpy.shutdown()
        return

    print("\n" + "="*70)
    print("🏠 TEST COMPATTO MARCUS IN CAMERA (MAX SPOSTAMENTO 20 CM)")
    print("="*70)
    
    # Step 1: Rotazione SX +25°
    print("\n▶️ STEP 1: Rotazione Pura a Sinistra (+25.0° sul posto)...")
    d_yaw1, drift1 = node.rotate_in_place(+25.0, w_speed=0.45)
    print(f"   • Rotazione Eseguita: {d_yaw1:+.2f}° (Target: +25.0°)")
    print(f"   • Deriva Traslazionale: {drift1*1000:.1f} mm (Attesa < 15mm)")
    time.sleep(1.0)

    # Step 2: Rotazione DX -25° (Ritorno a 0°)
    print("\n▶️ STEP 2: Rotazione Pura a Destra (-25.0° ritorno al centro)...")
    d_yaw2, drift2 = node.rotate_in_place(-25.0, w_speed=0.45)
    print(f"   • Rotazione Eseguita: {d_yaw2:+.2f}° (Target: -25.0°)")
    print(f"   • Deriva Traslazionale Residua: {drift2*1000:.1f} mm")
    time.sleep(1.0)

    # Step 3: Avanzamento +20cm
    print("\n▶️ STEP 3: Avanzamento Rettilineo Deciso (+20 cm / +0.20 m)...")
    dist3, dx3, dy3, yaw3 = node.translate_straight(+0.20, v_speed=0.15)
    print(f"   • Distanza Avanzata: {dist3*100:.1f} cm (dx = {dx3:+.3f}m, dy = {dy3:+.3f}m)")
    print(f"   • Deriva Angolare in Rettilineo: {yaw3:+.2f}° (Attesa ~ 0°)")
    time.sleep(1.0)

    # Step 4: Retromarcia -20cm (Ritorno al punto di partenza)
    print("\n▶️ STEP 4: Retromarcia Rettilinea Decisa (-20 cm ritorno punto zero)...")
    dist4, dx4, dy4, yaw4 = node.translate_straight(-0.20, v_speed=0.15)
    print(f"   • Distanza Retromarcia: {dist4*100:.1f} cm (dx = {dx4:+.3f}m, dy = {dy4:+.3f}m)")
    print(f"   • Deriva Angolare Totale: {yaw4:+.2f}°")

    print("\n" + "="*70)
    print("✅ TEST COMPATTO COMPLETATO CON SUCCESSO!")
    print("="*70 + "\n")
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
