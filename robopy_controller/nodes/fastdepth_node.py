#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import Image, CameraInfo
from cv_bridge import CvBridge
import cv2
import numpy as np

class FastDepthNode(Node):
    def __init__(self):
        super().__init__('fast_depth_node')
        
        qos = QoSProfile(reliability=QoSReliabilityPolicy.BEST_EFFORT, depth=3)
        
        # Sottoscrittori
        self.sub_image = self.create_subscription(
            Image, '/camera/image_raw', self.image_callback, qos)
        
        # Publisher
        self.pub_depth = self.create_publisher(Image, '/camera/depth/image_raw', qos)
        self.pub_depth_info = self.create_publisher(CameraInfo, '/camera/depth/camera_info', qos)
        
        self.bridge = CvBridge()
        
        # Parametri
        self.declare_parameter('frame_id', 'camera_frame')
        self.declare_parameter('max_depth', 5.0)
        self.declare_parameter('min_depth', 0.3)
        self.declare_parameter('use_edges', True)
        
        self.frame_id = self.get_parameter('frame_id').value
        self.max_depth = self.get_parameter('max_depth').value
        self.min_depth = self.get_parameter('min_depth').value
        self.use_edges = self.get_parameter('use_edges').value
        
        self.get_logger().info(f"FastDepth Node started - Range: {self.min_depth}-{self.max_depth}m")

    def image_callback(self, msg):
        try:
            # Converti l'immagine RGB
            cv_image = self.bridge.imgmsg_to_cv2(msg, "bgr8")
            
            # Stima depth
            depth_image = self.estimate_depth(cv_image)
            
            # Pubblica depth image
            depth_msg = self.bridge.cv2_to_imgmsg(depth_image, "32FC1")
            depth_msg.header = msg.header
            depth_msg.header.frame_id = self.frame_id
            self.pub_depth.publish(depth_msg)
            
            # Pubblica camera info per depth
            depth_info = CameraInfo()
            depth_info.header = msg.header
            depth_info.header.frame_id = self.frame_id
            depth_info.width = depth_image.shape[1]
            depth_info.height = depth_image.shape[0]
            depth_info.k = [500.0, 0, depth_info.width/2, 0, 500.0, depth_info.height/2, 0, 0, 1]
            depth_info.d = [0, 0, 0, 0, 0]
            depth_info.distortion_model = "plumb_bob"
            self.pub_depth_info.publish(depth_info)
            
        except Exception as e:
            self.get_logger().error(f"Depth estimation error: {e}")

    def estimate_depth(self, image):
        """Stima depth ottimizzata per Raspberry Pi"""
        height, width = image.shape[:2]
        
        # Converti in scala di grigi
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Calcola coordinate del centro
        center_x, center_y = width // 2, height // 2
        
        # Crea mappa di distanza dal centro (base geometrica)
        y_coords, x_coords = np.indices((height, width))
        distances = np.sqrt((x_coords - center_x)**2 + (y_coords - center_y)**2)
        max_dist = np.sqrt(center_x**2 + center_y**2)
        
        # Depth base basata sulla distanza dal centro
        base_depth = self.min_depth + (distances / max_dist) * (self.max_depth - self.min_depth)
        
        if self.use_edges:
            # Migliora la depth usando edge detection
            edges = cv2.Canny(gray, 50, 150)
            
            # Dilata gli edges per aree più ampie
            kernel = np.ones((3, 3), np.uint8)
            edges_dilated = cv2.dilate(edges, kernel, iterations=1)
            
            # Aree con edges sono più vicine
            edge_mask = edges_dilated > 0
            base_depth[edge_mask] *= 0.7  # Riduci depth del 30% nelle aree con edges
            
            # Aggiungi variazioni basate sull'intensità
            intensity_factor = gray.astype(np.float32) / 255.0
            base_depth *= (0.8 + 0.4 * intensity_factor)
        
        return base_depth.astype(np.float32)

def main(args=None):
    rclpy.init(args=args)
    node = FastDepthNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()