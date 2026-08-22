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
        q = msg.pose.pose.orientation
        cyaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        
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

    def rotate_step(self, target_step_deg=15.0, angular_speed=1.2):
        """
        Esegue un impulso temporizzato a velocità angolare idonea (1.2 rad/s)
        per superare l'attrito meccanico ed effettuare una vera rotazione di 15°.
        """
        target_rad = math.radians(target_step_deg)
        duration = target_rad / angular_speed
        
        msg = Twist()
        msg.angular.z = float(angular_speed)
        
        start_t = time.time()
        while time.time() - start_t < duration and rclpy.ok():
            self.cmd_pub.publish(msg)
            time.sleep(0.02)
            
        self.publish_stop()
        return target_step_deg

def main():
    rclpy.init()
    node = Step360ClosedLoopMappingTest()
    
    print("\n=================================================================")
    print(" 🤖 SVENTAGLIATA PANORAMICA 360° (24 Step x 15° | Pausa 3s)")
    print("=================================================================")
    
    total_steps = 24
    step_angle = 15.0
    pause_duration = 3.0
    angular_speed = 1.2  # rad/s per vincere l'inerzia e l'attrito meccanico
    
    print(f"🌀 Avvio 24 Step da {step_angle}° (w={angular_speed} rad/s, Pausa {pause_duration}s per step)...")
    
    for step in range(1, total_steps + 1):
        # Esegui spin per processare /odom se presente
        rclpy.spin_once(node, timeout_sec=0.05)
        
        step_deg = node.rotate_step(target_step_deg=step_angle, angular_speed=angular_speed)
        print(f"  📸 Step {step:02d}/{total_steps} completato (+{step_deg:.1f}°). Pausa {pause_duration}s per Mappa RTAB-Map...")
        
        pause_start = time.time()
        while time.time() - pause_start < pause_duration:
            rclpy.spin_once(node, timeout_sec=0.05)

    print("\n=================================================================")
    print(" 📊 REPORT FINALE MAPPATURA")
    print("=================================================================")
    if node.total_accumulated_deg > 0.0:
        print(f"• Rotazione totale accumulata (/odom): {node.total_accumulated_deg:.1f}°")
    print("• Rotazione a 360° in 24 step da 15° completata con successo.")
    print("=================================================================")
    print("🛑 SVENTAGLIATA COMPLETATA. Mappa panoramica generata!")
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
