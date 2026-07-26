#!/usr/bin/env python3
"""
Test Isolato dell'Odometria Meccanica Ruote (/odom) - SENZA Correzione Visiva SLAM
Esegue:
1. Azzeramento Odometria Ruote /odom
2. Rotazione controllata 360° in 12 step da 30°
3. Report comparativo del delta di traslazione (X, Y) e rotazione (Yaw) della pura odometria ruote
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import math
import time

class RawWheelOdomTest(Node):
    def __init__(self):
        super().__init__('test_raw_wheel_odom')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        
        self.odom_received = False
        self.start_x = None
        self.start_y = None
        self.current_x = 0.0
        self.current_y = 0.0
        self.current_yaw = 0.0
        self.last_yaw = None
        self.total_accumulated_deg = 0.0

    def _odom_cb(self, msg: Odometry):
        self.current_x = msg.pose.pose.position.x
        self.current_y = msg.pose.pose.position.y
        
        if self.start_x is None:
            self.start_x = self.current_x
            self.start_y = self.current_y

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

    def rotate_step(self, target_step_deg=30.0, w_speed=0.80):
        step_accumulated_deg = 0.0
        last_step_yaw = self.current_yaw
        
        msg = Twist()
        msg.angular.z = float(w_speed)
        
        start_t = time.time()
        while rclpy.ok() and step_accumulated_deg < target_step_deg:
            self.cmd_pub.publish(msg)
            rclpy.spin_once(self, timeout_sec=0.01)
            
            dyaw = self.current_yaw - last_step_yaw
            dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
            if abs(dyaw) < 0.5:
                step_accumulated_deg += abs(math.degrees(dyaw))
            last_step_yaw = self.current_yaw
            
            if time.time() - start_t > 4.0:
                break

        self.publish_stop()
        return step_accumulated_deg

def main():
    rclpy.init()
    node = RawWheelOdomTest()
    
    print("\n=================================================================")
    print(" 🤖 TEST ISOLATO ODOMETRIA RUOTE (/odom) - SENZA VISUAL SLAM")
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
            
    print(f"✅ Odometria /odom connessa! Posa Iniziale: X={node.start_x:.3f}m, Y={node.start_y:.3f}m\n")
    print("🌀 Esecuzione 12 Step di Rotazione da 30°...")
    
    for step in range(1, 13):
        step_deg = node.rotate_step(target_step_deg=30.0, w_speed=0.80)
        dx = node.current_x - node.start_x
        dy = node.current_y - node.start_y
        dist = math.hypot(dx, dy)
        print(f"  ⚙️ Step {step:02d}/12 (+{step_deg:.1f}°) | Traslazione Drift: X={dx:+.3f}m, Y={dy:+.3f}m | Dist={dist:.3f}m")
        
        pause_start = time.time()
        while time.time() - pause_start < 2.0:
            rclpy.spin_once(node, timeout_sec=0.05)

    final_dx = node.current_x - node.start_x
    final_dy = node.current_y - node.start_y
    final_dist = math.hypot(final_dx, final_dy)

    print("\n=================================================================")
    print(" 📊 REPORT FINALE PURA ODOMETRIA RUOTE MECCANICA (/odom)")
    print("=================================================================")
    print(f"• Rotazione Totale Accumulata (/odom): {node.total_accumulated_deg:.1f}°")
    print(f"• Deriva Lineare Totale (X, Y): X={final_dx:+.3f}m, Y={final_dy:+.3f}m")
    print(f"• Errore di Posizione Totale: {final_dist:.3f} metri")
    print("=================================================================")
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
