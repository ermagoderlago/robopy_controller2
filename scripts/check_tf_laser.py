#!/usr/bin/env python3
"""
check_tf_laser.py — Verifica in tempo reale la catena TF da 'laser' a 'map'
e la coerenza dell'orientamento quando il robot viene ruotato.
"""

import math
import time
import rclpy
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import Imu, LaserScan
from nav_msgs.msg import Odometry
from tf2_ros import Buffer, TransformListener, TransformException


def quat_to_yaw(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.degrees(math.atan2(siny_cosp, cosy_cosp))


class TFLaserChecker(Node):
    def __init__(self):
        super().__init__('check_tf_laser')
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        
        self.latest_imu = None
        self.latest_odom = None
        self.latest_scan = None
        
        self.create_subscription(Imu, '/oak/imu/data', self._imu_cb, 10)
        self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        self.create_subscription(LaserScan, '/scan', self._scan_cb, 10)

    def _imu_cb(self, msg):
        self.latest_imu = msg

    def _odom_cb(self, msg):
        self.latest_odom = msg

    def _scan_cb(self, msg):
        self.latest_scan = msg

    def sample(self):
        # Sample TF chain
        frames = ['map', 'odom', 'base_link', 'laser']
        tfs = {}
        
        pairs = [
            ('map', 'odom'),
            ('odom', 'base_link'),
            ('base_link', 'laser'),
            ('map', 'laser'),
            ('map', 'base_link')
        ]
        
        for parent, child in pairs:
            try:
                t = self.tf_buffer.lookup_transform(parent, child, Time())
                yaw = quat_to_yaw(t.transform.rotation)
                x = t.transform.translation.x
                y = t.transform.translation.y
                tfs[f'{parent}->{child}'] = (x, y, yaw)
            except TransformException as e:
                tfs[f'{parent}->{child}'] = str(e)
                
        return tfs


def main():
    rclpy.init()
    node = TFLaserChecker()
    
    print("⏳ Raccolta dati da TF, /odom, /oak/imu/data, /scan (2 secondi)...")
    start = time.time()
    while time.time() - start < 2.0:
        rclpy.spin_once(node, timeout_sec=0.05)
        
    tfs = node.sample()
    
    print("\n" + "="*65)
    print("📐 STATO DELLA CATENA DI TRASFORMAZIONE TF (REP-105)")
    print("="*65)
    for pair, val in tfs.items():
        if isinstance(val, tuple):
            print(f"  {pair:22s} : pos=({val[0]:+7.3f}, {val[1]:+7.3f}) m | yaw={val[2]:+7.2f}°")
        else:
            print(f"  {pair:22s} : ❌ ERRORE: {val}")
            
    print("\n" + "="*65)
    print("📡 TELEMETRIA SENSORI ORIENTAMENTO")
    print("="*65)
    if node.latest_odom:
        oyaw = quat_to_yaw(node.latest_odom.pose.pose.orientation)
        print(f"  /odom orientation yaw    : {oyaw:+7.2f}°")
    else:
        print("  /odom orientation yaw    : NESSUN DATO")
        
    if node.latest_imu:
        wz = node.latest_imu.angular_velocity.z
        print(f"  /oak/imu/data gyro Z     : {wz:+7.4f} rad/s ({math.degrees(wz):+7.2f}°/s)")
    else:
        print("  /oak/imu/data gyro Z     : NESSUN DATO")
        
    if node.latest_scan:
        n = len(node.latest_scan.ranges)
        r0 = node.latest_scan.ranges[0]
        r180 = node.latest_scan.ranges[n//2]
        print(f"  /scan frame_id           : '{node.latest_scan.header.frame_id}' (beams={n})")
        print(f"  /scan range 0° (davanti) : {r0:.2f} m | 180° (dietro): {r180:.2f} m")
    else:
        print("  /scan                    : NESSUN DATO")
    print("="*65 + "\n")
    
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
