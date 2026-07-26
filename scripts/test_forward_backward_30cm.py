#!/usr/bin/env python3
"""
Test Pura Odometria Meccanica: Avanzamento 30 cm + Arretramento 30 cm
- Nessuna correzione visiva
- Misura precisa della posa /odom (x, y, yaw) prima, durante e dopo il movimento
- Verifica dello scostamento rettilineo e della rotazione indesiderata
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import math
import time

class TestForwardBackward30cm(Node):
    def __init__(self):
        super().__init__('test_forward_backward_30cm')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        
        self.current_x = 0.0
        self.current_y = 0.0
        self.current_yaw = 0.0
        self.odom_received = False

    def _odom_cb(self, msg: Odometry):
        self.current_x = msg.pose.pose.position.x
        self.current_y = msg.pose.pose.position.y
        
        q = msg.pose.pose.orientation
        siny_cosp = 2 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1 - 2 * (q.y * q.y + q.z * q.z)
        self.current_yaw = math.atan2(siny_cosp, cosy_cosp)
        self.odom_received = True

    def publish_stop(self):
        msg = Twist()
        for _ in range(5):
            self.cmd_pub.publish(msg)
            time.sleep(0.02)

    def run_phase(self, linear_x, duration_sec, phase_name):
        print(f"\n⚙️ Avvio Fase: {phase_name} (vx={linear_x:+.2f} m/s per {duration_sec}s)...")
        start_x, start_y, start_yaw = self.current_x, self.current_y, self.current_yaw
        
        msg = Twist()
        msg.linear.x = float(linear_x)
        msg.angular.z = 0.0
        
        start_t = time.time()
        while rclpy.ok() and (time.time() - start_t < duration_sec):
            self.cmd_pub.publish(msg)
            rclpy.spin_once(self, timeout_sec=0.02)
            
        self.publish_stop()
        time.sleep(1.0)
        
        end_x, end_y, end_yaw = self.current_x, self.current_y, self.current_yaw
        dx = end_x - start_x
        dy = end_y - start_y
        dist = math.sqrt(dx**2 + dy**2)
        dyaw_deg = math.degrees(math.atan2(math.sin(end_yaw - start_yaw), math.cos(end_yaw - start_yaw)))
        
        print(f"  📍 Posa Iniziale: x={start_x:+.3f}m, y={start_y:+.3f}m, yaw={math.degrees(start_yaw):+.2f}°")
        print(f"  📍 Posa Finale:   x={end_x:+.3f}m, y={end_y:+.3f}m, yaw={math.degrees(end_yaw):+.2f}°")
        print(f"  📊 Distanza Calcolata Odom: {dist:.3f} m (dx={dx:+.3f}m, dy={dy:+.3f}m)")
        print(f"  📊 Delta Yaw Registrato:    {dyaw_deg:+.2f}°")
        return dist, dyaw_deg

def main():
    rclpy.init()
    node = TestForwardBackward30cm()
    
    print("\n=================================================================")
    print(" 🚗 TEST PURA ODOMETRIA MECCANICA: 30 CM AVANTI + 30 CM INDIETRO")
    print("=================================================================")
    print("Attesa ricezione /odom...")
    
    start_t = time.time()
    while rclpy.ok() and not node.odom_received:
        rclpy.spin_once(node, timeout_sec=0.1)
        if time.time() - start_t > 10.0:
            print("❌ Timeout ricezione /odom.")
            node.destroy_node()
            rclpy.shutdown()
            return
            
    print("✅ Topic /odom connesso!")
    
    x0, y0, yaw0 = node.current_x, node.current_y, node.current_yaw
    print(f"\n📍 Posa di Partenza Assoluta: x={x0:+.3f}m, y={y0:+.3f}m, yaw={math.degrees(yaw0):+.2f}°\n")
    
    # 1. Avanzamento 30 cm (+0.10 m/s per 3.0s)
    dist_fwd, yaw_fwd = node.run_phase(0.10, 3.0, "AVANZAMENTO 30 CM")
    time.sleep(2.0)
    
    # 2. Arretramento 30 cm (-0.10 m/s per 3.0s)
    dist_bwd, yaw_bwd = node.run_phase(-0.10, 3.0, "ARRETRAMENTO 30 CM")
    
    x_end, y_end, yaw_end = node.current_x, node.current_y, node.current_yaw
    total_drift = math.sqrt((x_end - x0)**2 + (y_end - y0)**2)
    total_yaw_drift = math.degrees(math.atan2(math.sin(yaw_end - yaw0), math.cos(yaw_end - yaw0)))
    
    print("\n=================================================================")
    print(" 📊 RISULTATI FINALI PURA ODOMETRIA MECCANICA")
    print("=================================================================")
    print(f"• Distanza Avanzamento 30 cm: {dist_fwd:.3f} m (Delta Yaw: {yaw_fwd:+.2f}°)")
    print(f"• Distanza Arretramento 30 cm: {dist_bwd:.3f} m (Delta Yaw: {yaw_bwd:+.2f}°)")
    print(f"• Errore Residuo Posizione:   {total_drift:.3f} m ({total_drift*100:.1f} cm)")
    print(f"• Errore Residuo Angolare:    {total_yaw_drift:+.2f}°")
    print("=================================================================\n")
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
