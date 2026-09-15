#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
import time, math

rclpy.init()
node = Node("odom_inspector")
samples = []

def cb(msg):
    p = msg.pose.pose.position
    o = msg.pose.pose.orientation
    siny = 2.0 * (o.w * o.z + o.x * o.y)
    cosy = 1.0 - 2.0 * (o.y * o.y + o.z * o.z)
    th = math.atan2(siny, cosy)
    samples.append((msg.header.stamp.sec + msg.header.stamp.nanosec*1e-9, p.x, p.y, math.degrees(th), msg.twist.twist.linear.x, msg.twist.twist.angular.z))

sub = node.create_subscription(Odometry, "/odom", cb, 10)
t0 = time.time()
while time.time() - t0 < 1.0:
    rclpy.spin_once(node, timeout_sec=0.05)

print(f"Captured {len(samples)} samples. Current state:")
if samples:
    s = samples[-1]
    print(f"  t={s[0]:.2f} x={s[1]:.4f}m y={s[2]:.4f}m th={s[3]:.2f}° v={s[4]:.3f}m/s w={s[5]:.3f}rad/s")
node.destroy_node()
rclpy.shutdown()
