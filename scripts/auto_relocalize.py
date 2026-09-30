#!/usr/bin/env python3
"""
auto_relocalize.py — Routine autonoma di localizzazione e verifica allineamento Scan-to-Map (AMCL)
Marcus Robot / ROS 2 Jazzy

Funzionalità (FM-NAV-033):
1. Inietta l'ultima posa nota salvata (Pose Persistence) su /initialpose
2. Riceve la mappa (/map) e lo scan LiDAR 360° (/scan)
3. Calcola l'indice di allineamento Scan-to-Map (Match Ratio): percentuale di raggi laser ToF
   che colpiscono le pareti note della mappa nell'intorno della posa stimata da AMCL
4. Se il robot si trova su mappa vuota o in modalità SLAM (<50 celle occupate), supera il check automaticamente
5. Se il robot è già allineato (Match Ratio >= 70% e Covarianza < 0.08), non esegue alcuno spin e termina con successo
6. Se il robot è disallineato o spostato (Match Ratio < 70%), esegue una rotazione attiva a 360° (0.30 rad/s)
   disperdendo le particelle AMCL (/reinitialize_global_localization) fino a far combaciare i punti laser con i muri
7. Non appena raggiunge la convergenza (Score >= 65% e Covarianza < 0.08), arresta i motori e salva la posa
"""

import sys
import os
import time
import math
import argparse
import yaml
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist, Point, Quaternion
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import OccupancyGrid
from std_srvs.srv import Empty


def euler_to_quaternion(yaw: float):
    return [0.0, 0.0, math.sin(yaw * 0.5), math.cos(yaw * 0.5)]


def quaternion_to_yaw(q) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


class AutoLocalizerNode(Node):
    def __init__(self, target_pose_file=None, force_global=False, inject_only=False, check_only=False):
        super().__init__('auto_localizer')
        self.target_pose_file = target_pose_file or '/mnt/ssd/last_known_pose.yaml'
        self.force_global = force_global
        self.inject_only = inject_only
        self.check_only = check_only

        # Publishers
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

        # QoS transient local per la mappa statica
        map_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # Subscribers
        self.amcl_pose_sub = self.create_subscription(
            PoseWithCovarianceStamped,
            '/amcl_pose',
            self.amcl_pose_callback,
            10
        )
        self.scan_sub = self.create_subscription(
            LaserScan,
            '/scan',
            self.scan_callback,
            10
        )
        self.map_sub = self.create_subscription(
            OccupancyGrid,
            '/map',
            self.map_callback,
            map_qos
        )

        # Internal state
        self.latest_pose = None
        self.latest_cov_trace = 999.0
        self.latest_scan = None
        self.map_grid = None
        self.map_2d = None
        self.map_res = 0.05
        self.map_ox = 0.0
        self.map_oy = 0.0
        self.map_w = 0
        self.map_h = 0
        self.is_empty_map = False

        self.converged = False
        self.converged_count = 0

        self.get_logger().info("🤖 AutoLocalizer Node con Scan-to-Map Matching avviato.")

    def amcl_pose_callback(self, msg: PoseWithCovarianceStamped):
        self.latest_pose = msg.pose.pose
        cov = msg.pose.covariance
        # Trace di covarianza (x + y + yaw)
        self.latest_cov_trace = float(cov[0] + cov[7] + cov[35])

    def scan_callback(self, msg: LaserScan):
        self.latest_scan = msg

    def map_callback(self, msg: OccupancyGrid):
        self.map_grid = msg
        self.map_res = float(msg.info.resolution)
        self.map_w = int(msg.info.width)
        self.map_h = int(msg.info.height)
        self.map_ox = float(msg.info.origin.position.x)
        self.map_oy = float(msg.info.origin.position.y)

        # Converte in array 2D numpy (H, W)
        raw_data = np.array(msg.data, dtype=np.int8)
        self.map_2d = raw_data.reshape((self.map_h, self.map_w))

        # Conta celle occupate (>= 50)
        occupied_count = int(np.sum(self.map_2d >= 50))
        if occupied_count < 50:
            self.is_empty_map = True
            self.get_logger().info(f"🗺️ Mappa con poche celle occupate ({occupied_count}): considerata mappa vuota/SLAM.")
        else:
            self.is_empty_map = False

    def compute_alignment_quality(self):
        """
        Calcola la percentuale di raggi ToF del LiDAR (/scan) che colpiscono pareti note (/map).
        Ritorna: (match_ratio: float, cov_trace: float, valid_points: int)
        """
        if self.is_empty_map:
            return (1.0, 0.0, 0)

        if self.map_2d is None or self.latest_scan is None or self.latest_pose is None:
            return (0.0, self.latest_cov_trace, 0)

        rx = self.latest_pose.position.x
        ry = self.latest_pose.position.y
        ryaw = quaternion_to_yaw(self.latest_pose.orientation)

        # Offset geometrico RPLIDAR C1 rispetto a base_link su Marcus:
        # x=0.08m, y=0.0m, yaw=pi (orientato a 180° all'indietro)
        lx = rx + 0.08 * math.cos(ryaw)
        ly = ry + 0.08 * math.sin(ryaw)
        lyaw = ryaw + math.pi

        scan = self.latest_scan
        ranges = np.array(scan.ranges, dtype=np.float32)
        angles = lyaw + scan.angle_min + np.arange(len(ranges)) * scan.angle_increment

        # Filtra raggi validi ToF (0.15m <= r <= 8.0m)
        valid_mask = (ranges >= 0.15) & (ranges <= 8.0) & np.isfinite(ranges)
        ranges = ranges[valid_mask]
        angles = angles[valid_mask]

        if len(ranges) < 25:
            return (0.0, self.latest_cov_trace, 0)

        # Sottocampionamento per calcolo in tempo reale (< 2ms)
        ranges = ranges[::2]
        angles = angles[::2]

        px = lx + ranges * np.cos(angles)
        py = ly + ranges * np.sin(angles)

        # Coordinate nella matrice mappa
        gx = np.floor((px - self.map_ox) / self.map_res).astype(np.int32)
        gy = np.floor((py - self.map_oy) / self.map_res).astype(np.int32)

        in_bounds = (gx >= 1) & (gx < self.map_w - 1) & (gy >= 1) & (gy < self.map_h - 1)
        gx = gx[in_bounds]
        gy = gy[in_bounds]

        if len(gx) == 0:
            return (0.0, self.latest_cov_trace, 0)

        # Verifica finestra di tolleranza 3x3 (+/- 1 cella = +/- 5cm su mappa 0.05m)
        hits = np.zeros(len(gx), dtype=bool)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                hits |= (self.map_2d[gy + dy, gx + dx] >= 50)

        inliers = int(np.sum(hits))
        total = int(len(gx))
        ratio = float(inliers) / float(total) if total > 0 else 0.0

        return (ratio, self.latest_cov_trace, total)

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

            cov = [0.0] * 36
            cov[0] = 0.04
            cov[7] = 0.04
            cov[35] = 0.06
            msg.pose.covariance = cov

            self.initialpose_pub.publish(msg)
            self.get_logger().info(f"📍 Iniezione posa su /initialpose: x={x:.3f}m, y={y:.3f}m, yaw={math.degrees(yaw):.1f}°")
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
        self.get_logger().info("🌐 Global Localization attivata: particelle AMCL disperse sulla mappa.")
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
            with open('/mnt/ssd/last_known_pose.yaml', 'w', encoding='utf-8') as f:
                yaml.dump(data, f)
            self.get_logger().info(f"💾 Nuova posa allineata salvata in {self.target_pose_file}")
        except Exception as e:
            self.get_logger().error(f"Errore salvataggio posa: {e}")

    def run_routine(self) -> bool:
        # Attesa ricezione mappa e scan (fino a 5s)
        wait_start = time.time()
        while rclpy.ok() and (self.map_grid is None or self.latest_scan is None):
            rclpy.spin_once(self, timeout_sec=0.1)
            if time.time() - wait_start > 5.0:
                self.get_logger().warn("Timeout attesa /map o /scan. Procedo con le informazioni disponibili.")
                break

        # Se la mappa è vuota (meno di 50 celle) -> modalità SLAM pura, nessun allineamento richiesto
        if self.is_empty_map:
            self.get_logger().info("🗺️ Mappa vuota rilevata (modalità SLAM / esplorazione iniziale). Allineamento superato.")
            return True

        # Modalità solo check
        if self.check_only:
            for _ in range(10):
                rclpy.spin_once(self, timeout_sec=0.1)
            ratio, cov, pts = self.compute_alignment_quality()
            self.get_logger().info(f"🔍 Check Allineamento: Match={ratio*100:.1f}%, Cov={cov:.4f}, Beams={pts}")
            return (ratio >= 0.65 and cov < 0.12)

        # 1. Prova prima iniezione posa persistente
        injected = False
        if not self.force_global:
            injected = self.inject_pose_from_file()
            # Attendi 1.5s per consentire ad AMCL di ricevere la posa e processare i primi scan
            settle_start = time.time()
            while time.time() - settle_start < 1.5:
                rclpy.spin_once(self, timeout_sec=0.1)

        # 2. Controllo Scan-to-Map immediato: il robot è già allineato?
        ratio, cov, pts = self.compute_alignment_quality()
        self.get_logger().info(f"📊 Verifica Iniziale: Match={ratio*100:.1f}%, Cov Trace={cov:.4f} (raggi ToF: {pts})")

        if ratio >= 0.70 and cov < 0.08:
            self.get_logger().info("🎯 Robot GIÀ ALLINEATO alla mappa! Nessuna rotazione necessaria.")
            return True

        if self.inject_only:
            self.get_logger().info("Modalità --inject-only completata (senza rotazione di verifica).")
            return True

        # Se lo score iniziale è molto basso (< 40%) o se forzato, disperdi le particelle globalmente
        if self.force_global or ratio < 0.40 or not injected:
            self.call_global_localization()
            time.sleep(0.5)

        # 3. Rotazione di allineamento e scansione a 360° (Spin-to-Align)
        self.get_logger().info("🔄 Avvio rotazione lenta di allineamento a 360° sul posto (0.30 rad/s)...")
        twist = Twist()
        twist.angular.z = 0.30

        start_time = time.time()
        max_duration = 28.0  # Tempo sufficiente per giro completo a 0.30 rad/s (~21s)
        last_log = time.time()
        converged_checks = 0

        while rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.05)
            elapsed = time.time() - start_time

            ratio, cov, pts = self.compute_alignment_quality()

            if time.time() - last_log >= 1.5:
                self.get_logger().info(f"⏳ Allineamento in corso ({elapsed:.1f}s)... Match: {ratio*100:.1f}% (target >=65%), Cov: {cov:.4f} (target <0.08)")
                last_log = time.time()

            # Condizione rigorosa di successo: sia la covarianza AMCL che il match geometrico devono essere validi
            if ratio >= 0.65 and cov < 0.08:
                converged_checks += 1
                if converged_checks >= 3:
                    self.get_logger().info(f"🎯 ALLINEAMENTO CONFERMATO in {elapsed:.1f}s! Match: {ratio*100:.1f}%, Cov: {cov:.4f}")
                    self.converged = True
                    break
            else:
                converged_checks = 0

            if elapsed > max_duration:
                self.get_logger().warn(f"⏱️ Timeout rotazione ({max_duration}s). Match finale: {ratio*100:.1f}%, Cov: {cov:.4f}")
                break

            self.cmd_vel_pub.publish(twist)
            time.sleep(0.02)

        # Stop immediato motori
        stop_twist = Twist()
        for _ in range(5):
            self.cmd_vel_pub.publish(stop_twist)
            time.sleep(0.05)

        self.get_logger().info("🛑 Motori arrestati.")

        if self.converged:
            self.save_converged_pose()
            return True
        else:
            self.get_logger().error(f"❌ Allineamento fallito o incompleto (Match: {ratio*100:.1f}%). I punti laser non combaciano con i muri.")
            return False


def main():
    parser = argparse.ArgumentParser(description="Marcus Auto Localizer con Scan-to-Map Matching")
    parser.add_argument('--pose-file', type=str, default=None, help="Percorso del file YAML con x, y, yaw")
    parser.add_argument('--force-global', action='store_true', help="Forza la dispersione uniforme globale")
    parser.add_argument('--inject-only', action='store_true', help="Inietta solo la posa senza eseguire rotazione")
    parser.add_argument('--check-only', action='store_true', help="Verifica l'allineamento attuale senza muovere il robot")
    args = parser.parse_args()

    rclpy.init()
    node = AutoLocalizerNode(
        target_pose_file=args.pose_file,
        force_global=args.force_global,
        inject_only=args.inject_only,
        check_only=args.check_only
    )

    try:
        success = node.run_routine()
        sys.exit(0 if success else 1)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
