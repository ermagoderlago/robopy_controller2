#!/usr/bin/env python3
"""
Script di Tuning & Test PID Movimento Relativo - Marcus
======================================================
Esegue sequenze di test in avanti (max 30cm) e indietro a diverse velocità (lenta, normale, veloce)
e rotazioni a 90°, misurando con precisione lo spostamento reale dagli encoder su /odom.

Utilizzo su Raspberry Pi:
  python3 scripts/tune_pid_motion.py
"""

import sys
import time
import math
import asyncio
from typing import Optional, Tuple

try:
    import rclpy
    from rclpy.node import Node
    from geometry_msgs.msg import Twist
    from nav_msgs.msg import Odometry
    HAS_ROS = True
except ImportError:
    HAS_ROS = False
    print("⚠️ ROS 2 non rilevato localmente. Lo script può essere eseguito sul Raspberry Pi host.")


class PIDMotionTunerNode(Node if HAS_ROS else object):
    def __init__(self):
        if not HAS_ROS:
            return
        super().__init__('pid_motion_tuner_node')
        self.get_logger().info("🎯 Inizializzazione PID Motion Tuner Node...")
        
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom_sub = self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        
        self.current_x: Optional[float] = None
        self.current_y: Optional[float] = None
        self.current_yaw: Optional[float] = None
        self.odom_received = False

    def _odom_cb(self, msg: Odometry):
        self.current_x = msg.pose.pose.position.x
        self.current_y = msg.pose.pose.position.y
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        self.current_yaw = 2.0 * math.atan2(qz, qw)
        self.odom_received = True

    def get_pose(self) -> Optional[Tuple[float, float, float]]:
        if not self.odom_received:
            return None
        return (self.current_x, self.current_y, self.current_yaw)

    def publish_twist(self, vx: float, wz: float):
        twist = Twist()
        twist.linear.x = float(vx)
        twist.angular.z = float(wz)
        self.cmd_pub.publish(twist)

    def stop(self):
        self.publish_twist(0.0, 0.0)


async def run_pid_movement(
    tuner: PIDMotionTunerNode,
    direction: str,
    target_val: float, # metri per lineare, gradi per angolare
    speed_cruise: float,
    Kp: float = 1.8,
    Ki: float = 0.4,
    Kd: float = 0.05
) -> dict:
    """Esegue un singolo movimento PID in closed-loop loggando l'accuratezza."""
    print(f"\n🚀 Avvio Test Movimento: '{direction}' target={target_val} (v_cruise={speed_cruise:.2f}, Kp={Kp}, Ki={Ki}, Kd={Kd})")
    
    # Attesa posa iniziale
    for _ in range(20):
        rclpy.spin_once(tuner, timeout_sec=0.05)
        if tuner.get_pose() is not None:
            break
            
    start_pose = tuner.get_pose()
    if start_pose is None:
        print("❌ Errore: Nessun dato odometrico /odom ricevuto!")
        return {"success": False, "error": "No Odom"}
        
    x0, y0, yaw0 = start_pose
    is_linear = direction in ("avanti", "indietro", "forward", "backward")
    target_dist = abs(target_val) if is_linear else None
    target_rad = math.radians(abs(target_val)) if not is_linear else None
    
    max_speed = 0.25 if is_linear else 1.0
    tolerance = 0.008 if is_linear else math.radians(2.0)
    
    accum_error = 0.0
    prev_error = 0.0
    t_start = time.monotonic()
    t_max = t_start + 10.0
    
    max_overshoot = 0.0
    history_moved = []
    
    dt = 0.05
    while time.monotonic() < t_max:
        rclpy.spin_once(tuner, timeout_sec=0.01)
        curr_pose = tuner.get_pose()
        if curr_pose is None:
            await asyncio.sleep(dt)
            continue
            
        cx, cy, cyaw = curr_pose
        elapsed = time.monotonic() - t_start
        
        if is_linear:
            moved = math.sqrt((cx - x0)**2 + (cy - y0)**2)
            error = target_dist - moved
            history_moved.append(moved)
            if moved > target_dist:
                max_overshoot = max(max_overshoot, moved - target_dist)
                
            if abs(error) <= tolerance:
                print(f"✅ TARGET RAGGIUNTO! Spostamento reale: {moved:.4f}m in {elapsed:.2f}s (Error={error*1000:.1f}mm)")
                break
                
            d_error = (error - prev_error) / dt if dt > 0 else 0.0
            prev_error = error
            accum_error += error * dt
            
            cmd_v = Kp * error + Ki * accum_error + Kd * d_error
            # Spunto iniziale nei primi 200ms per vincere la strizione
            if elapsed < 0.20:
                cmd_v = max(cmd_v, 0.22)
                
            cmd_v = min(max(abs(cmd_v), 0.14), max_speed)
            cmd_vx = cmd_v if direction in ("avanti", "forward") else -cmd_v
            tuner.publish_twist(cmd_vx, 0.0)
            
        else: # Angolare
            dyaw = cyaw - yaw0
            dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
            moved = abs(dyaw)
            error = target_rad - moved
            history_moved.append(math.degrees(moved))
            if moved > target_rad:
                max_overshoot = max(max_overshoot, moved - target_rad)
                
            if abs(error) <= tolerance:
                print(f"✅ TARGET RAGGIUNTO! Rotazione reale: {math.degrees(moved):.2f}° in {elapsed:.2f}s (Error={math.degrees(error):.2f}°)")
                break
                
            d_error = (error - prev_error) / dt if dt > 0 else 0.0
            prev_error = error
            accum_error += error * dt
            
            cmd_w = Kp * error + Ki * accum_error + Kd * d_error
            if elapsed < 0.20:
                cmd_w = max(cmd_w, 0.70)
                
            cmd_w = min(max(abs(cmd_w), 0.45), max_speed)
            cmd_wz = cmd_w if direction in ("sinistra", "left") else -cmd_w
            tuner.publish_twist(0.0, cmd_wz)
            
        await asyncio.sleep(dt)
        
    tuner.stop()
    await asyncio.sleep(0.5)
    
    total_time = time.monotonic() - t_start
    final_pose = tuner.get_pose()
    final_moved = math.sqrt((final_pose[0] - x0)**2 + (final_pose[1] - y0)**2) if is_linear else abs(math.atan2(math.sin(final_pose[2] - yaw0), math.cos(final_pose[2] - yaw0)))
    
    return {
        "success": True,
        "target": target_val,
        "final_moved": final_moved,
        "error": (target_dist - final_moved) if is_linear else (target_rad - final_moved),
        "overshoot": max_overshoot,
        "elapsed_s": total_time
    }


async def main():
    if not HAS_ROS:
        print("❌ ROS 2 non disponibile. Impossibile eseguire lo script di tuning.")
        return
        
    rclpy.init()
    tuner = PIDMotionTunerNode()
    
    print("=" * 60)
    print(" 🤖 TEST & TUNING PID MOVIMENTO RELATIVO MARCUS")
    print("=" * 60)
    print("Attesa connessione odometria /odom...")
    
    for _ in range(40):
        rclpy.spin_once(tuner, timeout_sec=0.1)
        if tuner.odom_received:
            break
            
    if not tuner.odom_received:
        print("❌ Impossibile connettersi al topic /odom. Verificare che waveshare_motor_driver sia attivo!")
        rclpy.shutdown()
        return

    print("✅ Odometria /odom connessa con successo!")
    print("\nInizio sequenza di collaudo e tuning PID...")
    
    tests = [
        {"dir": "avanti", "val": 0.30, "speed": 0.18, "desc": "Avanzamento 30cm (Velocità Normale)"},
        {"dir": "indietro", "val": 0.30, "speed": 0.18, "desc": "Ritorno Indietro 30cm (Velocità Normale)"},
        {"dir": "avanti", "val": 0.15, "speed": 0.12, "desc": "Avanzamento Lento 15cm"},
        {"dir": "indietro", "val": 0.15, "speed": 0.12, "desc": "Ritorno Lento 15cm"},
        {"dir": "sinistra", "val": 90.0, "speed": 0.60, "desc": "Rotazione 90° Sinistra"},
        {"dir": "destra", "val": 90.0, "speed": 0.60, "desc": "Rotazione 90° Destra (Ritorno)"},
    ]
    
    results = []
    for t in tests:
        print(f"\n📋 Test: {t['desc']}")
        res = await run_pid_movement(tuner, t["dir"], t["val"], t["speed"])
        results.append((t["desc"], res))
        await asyncio.sleep(1.5)  # Pausa tra movimenti
        
    print("\n" + "=" * 60)
    print(" 📊 REPORT FINALE DI PRECISONE E TUNING PID")
    print("=" * 60)
    for desc, res in results:
        if res.get("success"):
            target_str = f"{res['target']:.2f}m" if "cm" in desc or "Avanzamento" in desc or "Ritorno" in desc else f"{res['target']}°"
            moved_str = f"{res['final_moved']:.3f}m" if "m" in target_str else f"{math.degrees(res['final_moved']):.1f}°"
            print(f"• {desc:40s} | Target: {target_str:6s} | Effettivo: {moved_str:7s} | Tempo: {res['elapsed_s']:.2f}s")
        else:
            print(f"• {desc:40s} | ERRORE: {res.get('error')}")
            
    print("=" * 60)
    tuner.stop()
    rclpy.shutdown()


if __name__ == '__main__':
    asyncio.run(main())
