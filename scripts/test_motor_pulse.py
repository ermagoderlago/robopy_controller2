#!/usr/bin/env python3
"""
test_motor_pulse.py
Invia un impulso di movimento (avanti o rotazione) e legge i log seriali.
"""

import os
import sys
import time

for p in ["/home/robopy/ros2_venv/lib/python3.11/site-packages"]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist

def main():
    rclpy.init()
    node = Node('pulse_test')
    pub = node.create_publisher(Twist, '/cmd_vel', 10)
    time.sleep(0.5)

    msg = Twist()
    msg.linear.x = 0.0
    msg.angular.z = 0.35

    print("▶️ Invio impulso rotazione: w = 0.35 rad/s per 2.0 secondi...")
    start = time.time()
    while time.time() - start < 2.0:
        pub.publish(msg)
        time.sleep(0.05)

    # Stop
    stop_msg = Twist()
    for _ in range(5):
        pub.publish(stop_msg)
        time.sleep(0.02)

    print("⏹️ Stop inviato.")
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
