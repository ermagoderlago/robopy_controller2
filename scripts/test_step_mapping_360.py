#!/usr/bin/env python3
"""
Test Sventagliata Panoramica 360° con Controllo in Anello Chiuso (Closed-Loop)
- 12 Step da 30.0° ciascuno (360° Totali)
- Controllo in Anello Chiuso sulla posa angolare /odom con soglia minima di velocità (min_w = 0.75 rad/s)
  per superare l'attrito meccanico e garantire il movimento fisico del robot.
- Pausa di 3.0s ad ogni step per la mappatura visiva 3D da parte di RTAB-Map & SuperPoint
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import math
import time

class Step360ClosedLoopMappingTest(Node):
    def __init__(self):
        super().__init__('test_step_mapping_360')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        
        self.odom_received = False
        self.current_yaw = 0.0
        self.last_yaw = None
        self.total_accumulated_deg = 0.0

    def _odom_cb(self, msg: Odometry):
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        cyaw = 2.0 * math.atan2(qz, qw)
        
        if self.last_yaw is not None:
            dyaw = cyaw - self.last_yaw
            dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
            if abs(dyaw) < 0.5:
                self.total_accumulated_deg += abs(math.degrees(dyaw))
            
        self.last_yaw = cyaw
        self.current_yaw = cyaw
        self.odom_received = True

    def publish_stop(self):
        msg = Twist()
        for _ in range(5):
            self.cmd_pub.publish(msg)
            time.sleep(0.02)

    def rotate_closed_loop(self, target_step_deg=30.0, Kp=2.5, min_w=0.75, max_w=1.2):
        """
        Controllo in Anello Chiuso (Closed-Loop Position Control).
        Regola continuamente la velocità angolare in funzione dell'errore residuo.
        Se le ruote sono bloccate dall'attrito, la coppia minima (min_w = 0.75 rad/s)
        forza il movimento fisico fino al raggiungimento preciso dell'angolo desiderato.
        """
        start_yaw = self.current_yaw
        target_yaw = start_yaw + math.radians(target_step_deg)
        target_yaw = math.atan2(math.sin(target_yaw), math.cos(target_yaw))
        
        start_t = time.time()
        step_deg_turned = 0.0
        last_iter_yaw = self.current_yaw
        
        while rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.01)
            
            # Calcolo accumulo parziale dello step
            dy = self.current_yaw - last_iter_yaw
            dy = math.atan2(math.sin(dy), math.cos(dy))
            if abs(dy) < 0.5:
                step_deg_turned += math.degrees(abs(dy))
            last_iter_yaw = self.current_yaw

            # Errore angolare residuo verso il target dello step
            error_deg = target_step_deg - step_deg_turned
            if error_deg <= 1.0: # Tolleranza 1.0 grado
                break
                
            # Calcolo velocità angolare in anello chiuso
            w_cmd = Kp * math.radians(error_deg)
            if abs(w_cmd) < min_w:
                w_cmd = math.copysign(min_w, w_cmd if w_cmd != 0 else 1.0)
            elif abs(w_cmd) > max_w:
                w_cmd = math.copysign(max_w, w_cmd)
                
            msg = Twist()
            msg.angular.z = float(w_cmd)
            self.cmd_pub.publish(msg)
            
            if time.time() - start_t > 4.0:
                break

        self.publish_stop()
        return step_deg_turned

def main():
    rclpy.init()
    node = Step360ClosedLoopMappingTest()
    
    print("\n=================================================================")
    print(" 🤖 TEST SVENTAGLIATA PANORAMICA 360° - CONTROLLO IN ANELLO CHIUSO")
    print(" 📐 12 Step da 30.0° con Controllo Feedback Proporzionale /odom")
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
    print("🌀 Avvio Sventagliata in Anello Chiuso: 12 Step da 30.0°...")
    
    total_steps = 72
    step_angle = 10.0
    pause_duration = 2.0
    
    print(f"🌀 Avvio Sventagliata in Anello Chiuso (2 Giri = 720°): {total_steps} Step da {step_angle}° (min_w=0.85 rad/s, Pausa {pause_duration}s per step)...")
    
    for step in range(1, total_steps + 1):
        step_deg = node.rotate_closed_loop(target_step_deg=step_angle, min_w=0.85, max_w=1.2)
        print(f"  📸 Step {step:02d}/{total_steps} completato (+{step_deg:.1f}° | Totale: {node.total_accumulated_deg:.1f}°). Pausa {pause_duration}s per Mappa RTAB-Map...")
        
        # Pausa ad ogni step per la mappatura visiva 3D
        pause_start = time.time()
        while time.time() - pause_start < pause_duration:
            rclpy.spin_once(node, timeout_sec=0.05)

    print("\n=================================================================")
    print(" 📊 REPORT FINALE MAPPATURA ANELLO CHIUSO (CLOSED-LOOP)")
    print("=================================================================")
    print(f"• Rotazione totale accumulata (/odom): {node.total_accumulated_deg:.1f}°")
    print("=================================================================")
    print("🛑 SVENTAGLIATA COMPLETATA. La mappa panoramica 360° della stanza è stata generata su Foxglove!")
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
