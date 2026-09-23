#!/usr/bin/env python3
"""
test_odometry_spin_verification.py

Verifica fisica dell'odometria e della localizzazione durante una rotazione sul posto (in-place spin).
Metodo di verifica:
1. Registra heading iniziale da /odom, TF map->base_link, e scan LiDAR /scan completo a 360° (720 raggi).
2. Comanda una rotazione controllata (omega = +0.45 rad/s) in anello chiuso su /odom fino a raggiungere 360°.
3. Arresta i motori e attende la stabilizzazione.
4. Esegue la cross-correlazione a 360° tra il profilo LiDAR iniziale e finale per trovare l'angolo fisico REALE ruotato dal robot con precisione di 0.5° (risoluzione C1).
5. Calcola il rapporto esatto Odometria / Fisico e l'eventuale correzione residua di rotational_wheel_separation.
"""

import os
import sys
import math
import time

for p in ["/home/robopy/ros2_venv/lib/python3.11/site-packages"]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan
import tf2_ros

def normalize_angle(angle):
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle

def quat_to_yaw(q):
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)

class SpinVerificationNode(Node):
    def __init__(self):
        super().__init__('spin_verification_node')
        self.pub_cmd = self.create_publisher(Twist, '/cmd_vel', 10)
        self.sub_odom = self.create_subscription(Odometry, '/odom', self.odom_cb, 10)
        self.sub_scan = self.create_subscription(LaserScan, '/scan', self.scan_cb, 10)
        
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)
        
        self.current_yaw_odom = None
        self.start_yaw_odom = None
        self.last_yaw_odom = None
        self.accumulated_yaw = 0.0
        
        self.scan_msg = None
        self.ready = False
        self.tracking_active = False

    def odom_cb(self, msg: Odometry):
        yaw = quat_to_yaw(msg.pose.pose.orientation)
        self.current_yaw_odom = yaw
        
        if self.start_yaw_odom is None:
            self.start_yaw_odom = yaw
            self.last_yaw_odom = yaw
            self.ready = True
            return
            
        if self.tracking_active:
            dyaw = normalize_angle(yaw - self.last_yaw_odom)
            self.accumulated_yaw += dyaw
            
        self.last_yaw_odom = yaw

    def scan_cb(self, msg: LaserScan):
        self.scan_msg = msg

    def get_full_scan(self):
        if self.scan_msg is None:
            return None
        return list(self.scan_msg.ranges)

    def get_cardinal_ranges(self):
        if self.scan_msg is None:
            return None
        ranges = self.scan_msg.ranges
        n = len(ranges)
        f_idx = 0
        l_idx = n // 4
        r_idx = n // 2
        rt_idx = 3 * n // 4
        
        def safe_range(idx):
            val = ranges[idx]
            if math.isnan(val) or math.isinf(val) or val <= 0.05:
                neighbors = [ranges[(idx + k) % n] for k in range(-3, 4) if not math.isnan(ranges[(idx + k) % n]) and not math.isinf(ranges[(idx + k) % n])]
                return sum(neighbors)/len(neighbors) if neighbors else 0.0
            return val
            
        return {
            'front': safe_range(f_idx),
            'left': safe_range(l_idx),
            'rear': safe_range(r_idx),
            'right': safe_range(rt_idx)
        }

    def get_robot_pose_in_map(self):
        try:
            t = self.tf_buffer.lookup_transform('map', 'base_link', rclpy.time.Time(), timeout=rclpy.duration.Duration(seconds=0.5))
            x = t.transform.translation.x
            y = t.transform.translation.y
            yaw = quat_to_yaw(t.transform.rotation)
            return (x, y, math.degrees(yaw))
        except Exception:
            return None

    def stop_robot(self):
        t = Twist()
        for _ in range(5):
            self.pub_cmd.publish(t)
            time.sleep(0.02)

def compute_lidar_rotation(scan_initial, scan_final):
    """
    Trova la rotazione angolare ottimale tra scan_initial e scan_final via cross-correlazione.
    Ritorna la rotazione fisica in gradi (tra -180° e +180° rispetto a 360°/0°).
    """
    n = len(scan_initial)
    if n != len(scan_final) or n == 0:
        return 0.0
        
    best_shift = 0
    best_error = float('inf')
    
    # Valuta tutti gli shift possibili (da 0 a n-1)
    for shift in range(n):
        total_diff = 0.0
        valid_count = 0
        for i in range(0, n, 2):  # campiona a step 2 per efficienza
            r0 = scan_initial[i]
            r1 = scan_final[(i + shift) % n]
            if (not math.isnan(r0)) and (not math.isnan(r1)) and (not math.isinf(r0)) and (not math.isinf(r1)):
                if 0.15 < r0 < 8.0 and 0.15 < r1 < 8.0:
                    diff = abs(r0 - r1)
                    # Penalità per scarti oltre 0.5m (outlier / occlusioni)
                    total_diff += min(diff, 0.50)
                    valid_count += 1
        if valid_count > n // 6:
            avg_diff = total_diff / valid_count
            if avg_diff < best_error:
                best_error = avg_diff
                best_shift = shift
                
    deg_per_beam = 360.0 / n
    # Lo shift k indica che scan_final[i+k] corrisponde a scan_initial[i]
    # Questo significa che il sensore è ruotato di -shift (o 360 - shift)
    rot_deg = (n - best_shift) * deg_per_beam
    if rot_deg > 180.0:
        rot_deg -= 360.0
    return rot_deg, best_error

def main():
    rclpy.init()
    node = SpinVerificationNode()

    print("=" * 65)
    print("🤖 MARCUS — TEST DI PRECISIONE ODOMETRIA & SCAN MATCHING LIDAR")
    print("=" * 65)
    print("In attesa di connessione a /odom, /scan e TF...")
    
    start_wait = time.time()
    while time.time() - start_wait < 5.0 and (not node.ready or node.scan_msg is None):
        rclpy.spin_once(node, timeout_sec=0.1)

    if not node.ready or node.scan_msg is None:
        print("❌ ERRORE: Nessun dato da /odom o /scan.")
        rclpy.shutdown()
        return

    # Registra stato iniziale
    init_scan = node.get_full_scan()
    init_odom_deg = math.degrees(node.current_yaw_odom)
    init_ranges = node.get_cardinal_ranges()
    init_map_pose = node.get_robot_pose_in_map()
    
    print("\n📍 STATO INIZIALE:")
    print(f"• Yaw /odom iniziale:        {init_odom_deg:+.1f}°")
    if init_map_pose:
        print(f"• Posa TF map->base_link:    x={init_map_pose[0]:.3f}m, y={init_map_pose[1]:.3f}m, yaw={init_map_pose[2]:+.1f}°")
    if init_ranges:
        print(f"• Distanze LiDAR iniziali:   F={init_ranges['front']:.2f}m, L={init_ranges['left']:.2f}m, B={init_ranges['rear']:.2f}m, R={init_ranges['right']:.2f}m")
    
    target_deg = 360.0
    target_rad = math.radians(target_deg)
    omega = 0.45  # rad/s
    
    print(f"\n🚀 AVVIO ROTAZIONE: target /odom = {target_deg:.0f}° a omega = +{omega:.2f} rad/s...")
    
    node.accumulated_yaw = 0.0
    node.last_yaw_odom = node.current_yaw_odom
    node.tracking_active = True
    
    t_cmd = Twist()
    t_cmd.linear.x = 0.0
    t_cmd.angular.z = omega
    
    start_time = time.time()
    max_duration = 20.0
    last_print = 0.0
    
    while time.time() - start_time < max_duration:
        rclpy.spin_once(node, timeout_sec=0.03)
        accum = abs(node.accumulated_yaw)
        accum_deg = math.degrees(accum)
        
        now = time.time()
        if now - last_print > 1.0:
            pct = min(100.0, (accum_deg / target_deg) * 100.0)
            print(f"  [Progresso] Odom: {accum_deg:5.1f}° / {target_deg:.0f}° ({pct:4.1f}%) | w_cmd={t_cmd.angular.z:.2f}")
            last_print = now
            
        remaining = target_rad - accum
        if remaining <= 0:
            print(f"  🎯 Target {target_deg:.0f}° raggiunto su /odom!")
            break
        elif remaining < math.radians(25.0):
            t_cmd.angular.z = max(0.20, omega * (remaining / math.radians(25.0)))
        else:
            t_cmd.angular.z = omega
            
        node.pub_cmd.publish(t_cmd)
        
    node.stop_robot()
    node.tracking_active = False
    duration = time.time() - start_time
    
    print("\n🛑 Arresto motori. Attesa stabilizzazione sensori (2s)...")
    stop_wait = time.time()
    while time.time() - stop_wait < 2.0:
        rclpy.spin_once(node, timeout_sec=0.05)
        
    final_scan = node.get_full_scan()
    final_odom_deg = math.degrees(node.current_yaw_odom)
    final_accum_deg = math.degrees(abs(node.accumulated_yaw))
    final_ranges = node.get_cardinal_ranges()
    final_map_pose = node.get_robot_pose_in_map()
    
    # Calcolo Ground-Truth LiDAR
    lidar_delta_deg, fit_err = compute_lidar_rotation(init_scan, final_scan)
    # L'angolo totale ruotato fisicamente è 360° + lidar_delta_deg (dove lidar_delta_deg è l'errore residuo di orientamento rispetto a 360°)
    physical_total_deg = 360.0 + lidar_delta_deg
    
    print("\n" + "=" * 65)
    print("📊 REPORT FINALE VERIFICA ODOMETRIA & GROUND TRUTH")
    print("=" * 65)
    print(f"1. TEMPO DI ESECUZIONE:          {duration:.2f} s")
    print(f"2. ODOMETRIA /odom:")
    print(f"   • Yaw iniziale:               {init_odom_deg:+.1f}°")
    print(f"   • Yaw finale:                 {final_odom_deg:+.1f}°")
    print(f"   • Delta /odom integrato:      {final_accum_deg:.1f}°")
    print(f"3. GROUND TRUTH LIDAR /scan:")
    print(f"   • Rotazione fisica reale:     {physical_total_deg:.1f}° (Residuo vs 360°: {lidar_delta_deg:+.1f}°)")
    print(f"   • Accuratezza matching LiDAR: errore medio fit = {fit_err:.3f} m")
    
    scale_ratio = final_accum_deg / physical_total_deg if physical_total_deg != 0 else 1.0
    print(f"4. CALIBRAZIONE RAPPORTO SCALA:")
    print(f"   • Rapporto (Odom / Fisico):   {scale_ratio:.4f}")
    
    if abs(scale_ratio - 1.0) < 0.03:
        print("   🎉 ECCELLENTE! Errore inferiore al 3%: Odometria perfettamente calibrata!")
    else:
        print(f"   ℹ️ Correzione suggerita per rotational_wheel_separation: moltiplicare per {scale_ratio:.4f}")
        
    print("\n5. STATO LOCALIZZAZIONE AMCL (TF map -> base_link):")
    if init_map_pose and final_map_pose:
        dx = final_map_pose[0] - init_map_pose[0]
        dy = final_map_pose[1] - init_map_pose[1]
        dyaw = normalize_angle(math.radians(final_map_pose[2] - init_map_pose[2]))
        print(f"   • Posa Iniziale:  x={init_map_pose[0]:.3f}m, y={init_map_pose[1]:.3f}m, yaw={init_map_pose[2]:+.1f}°")
        print(f"   • Posa Finale:    x={final_map_pose[0]:.3f}m, y={final_map_pose[1]:.3f}m, yaw={final_map_pose[2]:+.1f}°")
        print(f"   • Delta mappa:    dx={dx:+.3f}m, dy={dy:+.3f}m, dyaw={math.degrees(dyaw):+.1f}°")
        if math.hypot(dx, dy) < 0.15 and abs(math.degrees(dyaw)) < 15.0:
            print("   ✅ AMCL HA MANTENUTO IL TRACKING!")
        else:
            print("   ℹ️ AMCL ha corretto la posa.")
            
    print("=" * 65)
    rclpy.shutdown()

if __name__ == '__main__':
    main()
