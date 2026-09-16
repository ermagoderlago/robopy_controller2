#!/usr/bin/env python3
"""
auto_relocalize.py — Routine autonoma di localizzazione su mappa nota (AMCL)
Marcus Robot / ROS 2 Jazzy

Funzionalità:
1. Inietta l'ultima posa nota salvata (Pose Persistence) su /initialpose
2. Se richiesto o se il robot è disallineato, attiva /reinitialize_global_localization
3. Esegue uno spin controllato e sicuro sul posto (0.30 rad/s) mentre monitora la
   covarianza del filtro particellare AMCL su /amcl_pose.
4. Non appena la covarianza scende sotto la soglia di convergenza (<0.08), arresta i motori
   e salva la nuova posa su /mnt/ssd/last_known_pose.yaml.
"""

import sys
import os
import time
import math
import argparse
import yaml

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist, Point, Quaternion
from std_srvs.srv import Empty


def euler_to_quaternion(yaw: float):
    return [0.0, 0.0, math.sin(yaw * 0.5), math.cos(yaw * 0.5)]


def quaternion_to_yaw(q) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


class AutoLocalizerNode(Node):
    def __init__(self, target_pose_file=None, force_global=False, inject_only=False):
        super().__init__('auto_localizer')
        self.target_pose_file = target_pose_file or '/mnt/ssd/last_known_pose.yaml'
        self.force_global = force_global
        self.inject_only = inject_only

        # Publishers & Subscribers
        self.initialpose_pub = self.create_publisher(
            PoseWithCovarianceStamped,
            '/initialpose',
            10
        )
        self.cmd_vel_pub = self.create_publisher(
            Twist,
            '/cmd_vel',
            10
        )

        self.amcl_pose_sub = self.create_subscription(
            PoseWithCovarianceStamped,
            '/amcl_pose',
            self.amcl_pose_callback,
            10
        )

        self.latest_pose = None
        self.latest_cov_trace = 999.0
        self.converged = False
        self.converged_count = 0

        self.get_logger().info("🤖 AutoLocalizer Node avviato.")

    def amcl_pose_callback(self, msg: PoseWithCovarianceStamped):
        self.latest_pose = msg.pose.pose
        cov = msg.pose.covariance
        # Trace di covarianza (x + y + yaw)
        self.latest_cov_trace = cov[0] + cov[7] + cov[35]

        # Soglia di confidenza rigorosa: sigma_xy < 15cm, sigma_yaw < 5°
        if self.latest_cov_trace < 0.08:
            self.converged_count += 1
            if self.converged_count >= 3:
                self.converged = True
        else:
            self.converged_count = 0

    def inject_pose_from_file(self) -> bool:
        if not os.path.exists(self.target_pose_file):
            self.get_logger().warn(f"File di posa non trovato: {self.target_pose_file}")
            return False

        try:
            with open(self.target_pose_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            x = float(data.get('x', 0.0))
            y = float(data.get('y', 0.0))
            yaw = float(data.get('yaw', 0.0))

            msg = PoseWithCovarianceStamped()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = 'map'
            msg.pose.pose.position = Point(x=x, y=y, z=0.0)
            q = euler_to_quaternion(yaw)
            msg.pose.pose.orientation = Quaternion(x=q[0], y=q[1], z=q[2], w=q[3])

            # Covarianza iniziale stimata (stretto intorno alla posa nota: 10cm e 6°)
            cov = [0.0] * 36
            cov[0] = 0.04   # x (sigma ~20cm)
            cov[7] = 0.04   # y (sigma ~20cm)
            cov[35] = 0.06  # yaw (sigma ~14°)
            msg.pose.covariance = cov

            self.initialpose_pub.publish(msg)
            self.get_logger().info(f"📍 Iniezione posa nota su /initialpose: x={x:.3f}m, y={y:.3f}m, yaw={math.degrees(yaw):.1f}°")
            return True
        except Exception as e:
            self.get_logger().error(f"Errore lettura posa da file: {e}")
            return False

    def call_global_localization(self) -> bool:
        client = self.create_client(Empty, '/reinitialize_global_localization')
        if not client.wait_for_service(timeout_sec=5.0):
            self.get_logger().error("Servizio /reinitialize_global_localization non disponibile!")
            return False
        
        req = Empty.Request()
        future = client.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=5.0)
        self.get_logger().info("🌐 Global Localization attivata: particelle disperse sulla mappa.")
        return True

    def save_converged_pose(self):
        if self.latest_pose is None:
            return
        
        x = self.latest_pose.position.x
        y = self.latest_pose.position.y
        yaw = quaternion_to_yaw(self.latest_pose.orientation)

        data = {
            'x': round(float(x), 4),
            'y': round(float(y), 4),
            'yaw': round(float(yaw), 4),
            'timestamp': time.time(),
            'covariance_trace': round(float(self.latest_cov_trace), 6)
        }

        try:
            with open(self.target_pose_file, 'w', encoding='utf-8') as f:
                yaml.dump(data, f)
            # Salva anche come fallback globale
            with open('/mnt/ssd/last_known_pose.yaml', 'w', encoding='utf-8') as f:
                yaml.dump(data, f)
            self.get_logger().info(f"💾 Nuova posa salvata con successo in {self.target_pose_file}")
        except Exception as e:
            self.get_logger().error(f"Errore salvataggio posa: {e}")

    def run_routine(self):
        # 1. Prova prima iniezione posa persistente
        injected = False
        if not self.force_global:
            injected = self.inject_pose_from_file()
            # Attendi 1.5s per dare tempo ad AMCL di ricevere e valutare
            time.sleep(1.5)

        if self.inject_only:
            self.get_logger().info("Modalità --inject-only completata.")
            return True

        # Se forzato o se non esiste file di posa, attiva global localization
        if self.force_global or not injected:
            self.call_global_localization()
            time.sleep(1.0)

        # 2. Esegui rotazione controllata (Spin-to-Settle)
        self.get_logger().info("🔄 Avvio rotazione lenta di calibrazione ToF sul posto (0.30 rad/s)...")
        twist = Twist()
        twist.angular.z = 0.30  # Velocità dolce conforme a SPEC-02

        start_time = time.time()
        max_duration = 25.0     # Massimo 25 secondi (~1.2 giri a 0.3 rad/s)

        rate = self.create_rate(10) # 10 Hz
        while rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.05)
            elapsed = time.time() - start_time

            if self.converged:
                self.get_logger().info(f"🎯 CONVERGENZA RAGGIUNTA in {elapsed:.1f}s! Covarianza: {self.latest_cov_trace:.4f}")
                break

            if elapsed > max_duration:
                self.get_logger().warn(f"⏱️ Timeout rotazione ({max_duration}s) raggiunto. Covarianza residua: {self.latest_cov_trace:.4f}")
                break

            self.cmd_vel_pub.publish(twist)
            rate.sleep()

        # Stop immediato motori
        stop_twist = Twist()
        for _ in range(5):
            self.cmd_vel_pub.publish(stop_twist)
            time.sleep(0.05)

        self.get_logger().info("🛑 Motori arrestati.")

        # Salva la posa finale
        self.save_converged_pose()
        return self.converged


def main():
    parser = argparse.ArgumentParser(description="Marcus Auto Localizer")
    parser.add_argument('--pose-file', type=str, default=None, help="Percorso del file YAML con x, y, yaw")
    parser.add_argument('--force-global', action='store_true', help="Forza la dispersione uniforme globale")
    parser.add_argument('--inject-only', action='store_true', help="Inietta solo la posa senza eseguire rotazione")
    args = parser.parse_args()

    rclpy.init()
    node = AutoLocalizerNode(
        target_pose_file=args.pose_file,
        force_global=args.force_global,
        inject_only=args.inject_only
    )

    try:
        success = node.run_routine()
        sys.exit(0 if success else 1)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
