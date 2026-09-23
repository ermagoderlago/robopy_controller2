#!/usr/bin/env python3
import os
import sys
import math

for p in ["/home/robopy/ros2_venv/lib/python3.11/site-packages"]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan

def main():
    rclpy.init()
    node = Node('scan_analyzer')
    msg = None
    
    def cb(m):
        nonlocal msg
        msg = m
        
    sub = node.create_subscription(LaserScan, '/scan', cb, 10)
    for _ in range(50):
        rclpy.spin_once(node, timeout_sec=0.1)
        if msg is not None:
            break
            
    if msg is None:
        print("Timeout ricezione /scan")
        return
        
    r = msg.ranges
    n = len(r)
    print(f"Total beams: {n}")
    print("\nDistanze ogni 30°:")
    for deg in range(0, 360, 30):
        idx = int(deg * n / 360.0) % n
        print(f"  {deg:3d}° (idx {idx:3d}): {r[idx]:.2f} m")
        
    # Cerchiamo dove si trova il raggio ~1.08m (parete frontale iniziale)
    print("\nRaggi vicini a 1.08m (1.00m - 1.15m):")
    matches = [(int(i * 360.0 / n), r[i]) for i in range(n) if 1.00 <= r[i] <= 1.15]
    for deg, dist in matches[::5]: # campiona ogni 5
        print(f"  Angolo {deg:3d}°: {dist:.2f} m")
        
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
