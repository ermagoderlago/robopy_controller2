#!/usr/bin/env python3
"""
set_initial_pose.py
Invia una posa iniziale ad AMCL su /initialpose
"""

import os
import sys
import time

for p in ["/home/robopy/ros2_venv/lib/python3.11/site-packages"]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseWithCovarianceStamped

def main():
    rclpy.init()
    node = Node('initial_pose_setter')
    pub = node.create_publisher(PoseWithCovarianceStamped, '/initialpose', 10)
    
    # Aspetta publisher
    time.sleep(1.0)
    
    msg = PoseWithCovarianceStamped()
    msg.header.stamp = node.get_clock().now().to_msg()
    msg.header.frame_id = 'map'
    
    # Posa di prima: x=1.617, y=0.712, orientation: z=-0.9998, w=0.0201 (~177.7 deg)
    msg.pose.pose.position.x = 1.617
    msg.pose.pose.position.y = 0.712
    msg.pose.pose.position.z = 0.0
    msg.pose.pose.orientation.x = 0.0
    msg.pose.pose.orientation.y = 0.0
    msg.pose.pose.orientation.z = -0.9998
    msg.pose.pose.orientation.w = 0.0201
    
    cov = [0.0] * 36
    cov[0] = 0.25   # var x
    cov[7] = 0.25   # var y
    cov[35] = 0.068 # var yaw (~15 deg)
    msg.pose.covariance = cov
    
    for _ in range(5):
        pub.publish(msg)
        time.sleep(0.1)
        
    print("✅ Posa iniziale inviata ad AMCL: x=1.617, y=0.712, yaw=177.7°")
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
