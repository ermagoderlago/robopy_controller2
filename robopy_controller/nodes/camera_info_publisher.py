#!/usr/bin/env python3
#camera_info_publisher.py

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo
from std_msgs.msg import Header
import numpy as np

class CameraInfoPublisher(Node):
    def __init__(self):
        super().__init__('camera_info_publisher')
        
        # Parametri
        self.declare_parameter('frame_id', 'camera_optical_frame')  # Frame camera ottico standard ROS
        self.declare_parameter('camera_name', 'camera')
        self.declare_parameter('image_width', 320)
        self.declare_parameter('image_height', 200)
        
        # Parametri intrinseci per OAK-D Lite (modifica con i tuoi valori calibrati)
        self.declare_parameter('camera_matrix', [320.0, 0.0, 160.0, 0.0, 320.0, 100.0, 0.0, 0.0, 1.0])
        self.declare_parameter('distortion_coefficients', [0.0, 0.0, 0.0, 0.0, 0.0])
        
        # Publisher
        self.publisher = self.create_publisher(CameraInfo, 'camera_info', 10)
        
        # Timer
        self.timer = self.create_timer(0.1, self.publish_camera_info)  # 10Hz
        
        self.get_logger().info('Camera Info Publisher avviato')
    
    def publish_camera_info(self):
        msg = CameraInfo()
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.get_parameter('frame_id').value
        
        msg.height = self.get_parameter('image_height').value
        msg.width = self.get_parameter('image_width').value
        
        # Matrice intrinseca K (3x3 row-major)
        camera_matrix = self.get_parameter('camera_matrix').value
        msg.k = camera_matrix
        
        # Matrice di distorsione D (5x1)
        distortion = self.get_parameter('distortion_coefficients').value
        msg.d = distortion
        
        # Matrice di proiezione P (3x4 row-major)
        msg.p = [
            camera_matrix[0], camera_matrix[1], camera_matrix[2], 0.0,
            camera_matrix[3], camera_matrix[4], camera_matrix[5], 0.0,
            camera_matrix[6], camera_matrix[7], camera_matrix[8], 0.0
        ]
        
        # Matrice di rototraslazione R (identity)
        msg.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        
        msg.distortion_model = 'plumb_bob'
        
        self.publisher.publish(msg)

def main():
    rclpy.init()
    node = CameraInfoPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()