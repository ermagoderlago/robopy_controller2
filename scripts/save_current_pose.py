#!/usr/bin/env python3
"""
save_current_pose.py — Salva la trasformata TF attuale (map -> base_link) su file YAML
Utilizzo: python3 scripts/save_current_pose.py [/path/to/target_pose.yaml]
"""

import sys
import os
import time
import math
import yaml

import rclpy
from rclpy.node import Node
from rclpy.time import Time
from tf2_ros import Buffer, TransformListener, TransformException


def quaternion_to_yaw(q) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def main():
    target_file = sys.argv[1] if len(sys.argv) > 1 else '/mnt/ssd/last_known_pose.yaml'

    rclpy.init()
    node = Node('pose_saver')
    tf_buffer = Buffer()
    tf_listener = TransformListener(tf_buffer, node)

    # Attendi fino a 5 secondi che map -> base_link sia disponibile
    start = time.time()
    t = None
    while time.time() - start < 5.0 and rclpy.ok():
        rclpy.spin_once(node, timeout_sec=0.1)
        try:
            t = tf_buffer.lookup_transform('map', 'base_link', Time())
            break
        except TransformException:
            pass

    if t is not None:
        x = t.transform.translation.x
        y = t.transform.translation.y
        yaw = quaternion_to_yaw(t.transform.rotation)

        data = {
            'x': round(float(x), 4),
            'y': round(float(y), 4),
            'yaw': round(float(yaw), 4),
            'timestamp': time.time()
        }

        os.makedirs(os.path.dirname(os.path.abspath(target_file)), exist_ok=True)
        with open(target_file, 'w', encoding='utf-8') as f:
            yaml.dump(data, f)
        
        # Salva sempre anche il fallback universale
        with open('/mnt/ssd/last_known_pose.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(data, f)

        node.get_logger().info(f"✅ Posa attuale salvata: x={x:.3f}m, y={y:.3f}m, yaw={math.degrees(yaw):.1f}° -> {target_file}")
    else:
        node.get_logger().warn("⚠️ Impossibile determinare la posa attuale da TF map -> base_link.")

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
