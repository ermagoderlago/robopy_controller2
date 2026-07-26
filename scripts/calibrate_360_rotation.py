#!/usr/bin/env python3
"""
Test di Calibrazione Automatica Rotazione 360° & Loop Closure Visivo RTAB-Map / SuperPoint
- Esegue una sventagliata di 360° con step da 5.0° e 4.0 secondi di stop ad ogni passo.
- Rileva l'angolo odometrico accumulato (/odom) e gli eventi di Visual Loop Closure di RTAB-Map.
- Calcola in automatico la costante esatta di 'rotational_wheel_separation' per far coincidere
  1 giro completo sul pavimento con 360.0° esatti su /odom e Foxglove.
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rtabmap_msgs.msg import Info
import math
import time

class AutoCalibrate360RotationNode(Node):
    def __init__(self):
        super().__init__('calibrate_360_rotation')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        self.rtab_info_sub = self.create_subscription(Info, '/rtabmap/info', self._rtab_info_cb, 10)
        
        self.odom_received = False
        self.current_yaw = 0.0
        self.last_yaw = None
        self.total_accumulated_deg = 0.0
        self.loop_closure_detected = False
        self.loop_closure_yaw = None

    def _odom_cb(self, msg: Odometry):
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        cyaw = 2.0 * math.atan2(qz, qw)
        
        if self.last_yaw is not None:
            dyaw = cyaw - self.last_yaw
            dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
            if abs(dyaw) < 0.5:
                self.total_accumulated_deg += math.degrees(abs(dyaw))
            
        self.last_yaw = cyaw
        self.current_yaw = cyaw
        self.odom_received = True

    def _rtab_info_cb(self, msg: Info):
        # RTAB-Map pubblica loop closure id quando riconosce la scena iniziale
        if getattr(msg, 'loop_closure_id', 0) > 0 or getattr(msg, 'ref_id', 0) > 0:
            if self.total_accumulated_deg > 300.0 and not self.loop_closure_detected:
                self.loop_closure_detected = True
                self.loop_closure_yaw = self.total_accumulated_deg
                self.get_logger().info(f"🎉 VISUAL LOOP CLOSURE RILEVATO DA RTAB-MAP! Angolo odometrico al loop closure: {self.loop_closure_yaw:.1f}°")

    def publish_stop(self):
        msg = Twist()
        for _ in range(5):
            self.cmd_pub.publish(msg)
            time.sleep(0.02)

    def rotate_step_closed_loop(self, target_step_deg=5.0, Kp=2.5, min_w=0.75, max_w=1.2):
        step_deg_turned = 0.0
        last_iter_yaw = self.current_yaw
        start_t = time.time()
        
        while rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.01)
            
            dy = self.current_yaw - last_iter_yaw
            dy = math.atan2(math.sin(dy), math.cos(dy))
            if abs(dy) < 0.5:
                step_deg_turned += math.degrees(abs(dy))
            last_iter_yaw = self.current_yaw

            error_deg = target_step_deg - step_deg_turned
            if error_deg <= 0.8: # Tolleranza 0.8 gradi per step fino da 5°
                break
                
            w_cmd = Kp * math.radians(error_deg)
            if abs(w_cmd) < min_w:
                w_cmd = math.copysign(min_w, w_cmd if w_cmd != 0 else 1.0)
            elif abs(w_cmd) > max_w:
                w_cmd = math.copysign(max_w, w_cmd)
                
            msg = Twist()
            msg.angular.z = float(w_cmd)
            self.cmd_pub.publish(msg)
            
            if time.time() - start_t > 3.0:
                break

        self.publish_stop()
        return step_deg_turned

def main():
    rclpy.init()
    node = AutoCalibrate360RotationNode()
    
    print("\n=================================================================")
    print(" 🎯 TEST CALIBRAZIONE AUTOMATICA ROTAZIONE 360° & VISUAL SLAM")
    print(" 📐 Base Interasse Ruote Fisico: 285 mm (0.285 m)")
    print(" 📐 Protocollo: 72 Step da 5.0° con Pausa di 4.0s (SuperPoint SLAM)")
    print("=================================================================")
    print("Attesa connessione odometria /odom...")
    
    start_t = time.time()
    while rclpy.ok() and not node.odom_received:
        rclpy.spin_once(node, timeout_sec=0.1)
        if time.time() - start_t > 5.0:
            print("❌ Impossibile connettersi a /odom.")
            node.destroy_node()
            rclpy.shutdown()
            return
            
    print("✅ Odometria /odom connessa!\n")
    print("🌀 Avvio Sventagliata di Calibrazione: 72 Step da 5.0° (Pausa 4.0s ad ogni step)...")
    
    total_steps = 72
    for step in range(1, total_steps + 1):
        step_deg = node.rotate_step_closed_loop(target_step_deg=5.0)
        print(f"  📸 Step {step:02d}/72 (+{step_deg:.1f}° | Totale /odom: {node.total_accumulated_deg:.1f}°). Pausa 4.0s per SLAM...")
        
        pause_start = time.time()
        while time.time() - pause_start < 4.0:
            rclpy.spin_once(node, timeout_sec=0.05)

    base_track_width = 0.285 # 285 mm
    measured_odom_deg = node.total_accumulated_deg
    
    # Se è stato rilevato il Visual Loop Closure da RTAB-Map
    target_reference_deg = 360.0
    if node.loop_closure_detected and node.loop_closure_yaw is not None:
        measured_odom_deg = node.loop_closure_yaw
        print(f"\n🎉 Visual Loop Closure RTAB-Map confermato a {measured_odom_deg:.1f}° odometrici!")

    scale_factor = measured_odom_deg / target_reference_deg
    calibrated_track_width = base_track_width * scale_factor

    print("\n=================================================================")
    print(" 📊 RISULTATI CALIBRAZIONE ANGOLARE 360°")
    print("=================================================================")
    print(f"• Interasse Ruote Base (Fisico):        {base_track_width:.3f} m (285 mm)")
    print(f"• Angolo Accumulato Misurato (/odom):    {measured_odom_deg:.1f}° (Riferimento Reale: 360.0°)")
    print(f"• Fattore di Scala Calibrato:           {scale_factor:.4f}")
    print(f"• NUOVO Interasse Rotazionale Calibrato: {calibrated_track_width:.4f} m ({calibrated_track_width*1000:.1f} mm)")
    print("=================================================================")
    print(f"💡 Suggerimento: Impostare rotational_wheel_separation:={calibrated_track_width:.4f} nei file di configurazione.")
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
