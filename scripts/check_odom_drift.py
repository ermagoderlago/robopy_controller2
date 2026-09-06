#!/usr/bin/env python3
import rclpy
from nav_msgs.msg import Odometry
import time

rclpy.init()
node = rclpy.create_node('drift_checker')
messages = []
node.create_subscription(Odometry, '/odom', lambda m: messages.append(m), 10)

t0 = time.time()
while len(messages) == 0 and time.time() - t0 < 3.0:
    rclpy.spin_once(node, timeout_sec=0.1)

if not messages:
    print("NO_MESSAGES")
    exit(1)

m0 = messages[-1]
p0 = m0.pose.pose.position
q0 = m0.pose.pose.orientation
time.sleep(3.0)

for _ in range(15):
    rclpy.spin_once(node, timeout_sec=0.05)

m1 = messages[-1]
p1 = m1.pose.pose.position
q1 = m1.pose.pose.orientation

print(f"T0: pos=({p0.x:.4f}, {p0.y:.4f}), ori=({q0.z:.4f}, {q0.w:.4f})")
print(f"T1: pos=({p1.x:.4f}, {p1.y:.4f}), ori=({q1.z:.4f}, {q1.w:.4f})")
print(f"DIFF: dx={p1.x - p0.x:.6f}, dy={p1.y - p0.y:.6f}, dz={q1.z - q0.z:.6f}, dw={q1.w - q0.w:.6f}")
node.destroy_node()
rclpy.shutdown()
