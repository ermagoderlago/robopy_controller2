#!/usr/bin/env python3
"""
inspect_localization.py
Ispeziona la situazione corrente:
1. Posa AMCL (/amcl_pose) e covarianza
2. Posa Odometria (/odom)
3. Scan LiDAR (/scan) distanze cardinali
4. Transform TF map -> odom e odom -> base_link
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
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan
import tf2_ros

from rclpy.qos import QoSProfile, QoSDurabilityPolicy, QoSReliabilityPolicy

class LocalizationInspector(Node):
    def __init__(self):
        super().__init__('loc_inspector')
        self.amcl_pose = None
        self.amcl_cov = None
        self.odom_pose = None
        self.scan_msg = None
        
        amcl_qos = QoSProfile(depth=1, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL, reliability=QoSReliabilityPolicy.RELIABLE)
        self.sub_amcl = self.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', self.amcl_cb, amcl_qos)
        self.sub_odom = self.create_subscription(Odometry, '/odom', self.odom_cb, 10)
        self.sub_scan = self.create_subscription(LaserScan, '/scan', self.scan_cb, 10)
        
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

    def amcl_cb(self, msg):
        self.amcl_pose = msg.pose.pose
        self.amcl_cov = msg.pose.covariance

    def odom_cb(self, msg):
        self.odom_pose = msg.pose.pose

    def scan_cb(self, msg):
        self.scan_msg = msg

    def euler_from_quat(self, q):
        siny = 2.0 * (q.w * q.z + q.x * q.y)
        cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        return math.degrees(math.atan2(siny, cosy))

def main():
    rclpy.init()
    node = LocalizationInspector()
    
    # Aspetta dati per 3 secondi
    start = time.time()
    while time.time() - start < 3.0:
        rclpy.spin_once(node, timeout_sec=0.1)
        if node.amcl_pose is not None and node.odom_pose is not None and node.scan_msg is not None:
            break
            
    print("=" * 60)
    print("📍 STATO ATTUALE LOCALIZZAZIONE & ODOMETRIA")
    print("=" * 60)
    
    if node.amcl_pose:
        p = node.amcl_pose.position
        yaw = node.euler_from_quat(node.amcl_pose.orientation)
        cov_x = node.amcl_cov[0]
        cov_y = node.amcl_cov[7]
        cov_yaw = node.amcl_cov[35]
        print(f"AMCL Pose in 'map':")
        print(f"  Posizione: x={p.x:.3f} m, y={p.y:.3f} m")
        print(f"  Heading (Yaw): {yaw:+.1f}°")
        print(f"  Covarianza: var_x={cov_x:.4f}, var_y={cov_y:.4f}, var_yaw={cov_yaw:.4f}")
    else:
        print("AMCL Pose: NESSUNA RICEVUTA")
        
    if node.odom_pose:
        p = node.odom_pose.position
        yaw = node.euler_from_quat(node.odom_pose.orientation)
        print(f"\nOdometry Pose in 'odom':")
        print(f"  Posizione: x={p.x:.3f} m, y={p.y:.3f} m")
        print(f"  Heading (Yaw): {yaw:+.1f}°")
    else:
        print("\nOdometry Pose: NESSUNA RICEVUTA")
        
    # Check TF map -> odom
    try:
        t_map_odom = node.tf_buffer.lookup_transform('map', 'odom', rclpy.time.Time())
        trans = t_map_odom.transform.translation
        rot = t_map_odom.transform.rotation
        yaw = node.euler_from_quat(rot)
        print(f"\nTF map -> odom:")
        print(f"  Offset: x={trans.x:.3f} m, y={trans.y:.3f} m")
        print(f"  Yaw: {yaw:+.1f}°")
    except Exception as e:
        print(f"\nTF map -> odom: ERRORE ({e})")

    # Check TF map -> base_link
    try:
        t_map_base = node.tf_buffer.lookup_transform('map', 'base_link', rclpy.time.Time())
        trans = t_map_base.transform.translation
        rot = t_map_base.transform.rotation
        yaw = node.euler_from_quat(rot)
        print(f"\nTF map -> base_link (ROBOT POSITION IN MAP):")
        print(f"  Posizione: x={trans.x:.3f} m, y={trans.y:.3f} m")
        print(f"  Heading: {yaw:+.1f}°")
    except Exception as e:
        print(f"\nTF map -> base_link: ERRORE ({e})")

    # Scan laser
    if node.scan_msg:
        ranges = node.scan_msg.ranges
        n = len(ranges)
        print(f"\nLiDAR /scan ({n} beams):")
        # Front (index 0 or depending on layout)
        # Laser has yaw=3.1415 in TF, frame is 'laser'
        f_idx = 0
        r_idx = n // 4
        b_idx = n // 2
        l_idx = 3 * n // 4
        print(f"  Raggi: 0°={ranges[0]:.2f}m, 90°={ranges[r_idx]:.2f}m, 180°={ranges[b_idx]:.2f}m, 270°={ranges[l_idx]:.2f}m")
    
    print("=" * 60)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
