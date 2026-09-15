#!/usr/bin/env python3
# depth_to_scan_wrapper.py

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image, CameraInfo, LaserScan
import numpy as np
from cv_bridge import CvBridge
import math

class DepthToScanWrapper(Node):
    def __init__(self):
        super().__init__('depth_to_scan_wrapper')
        
        # Parametri
        self.declare_parameter('output_frame', 'camera_link')
        self.declare_parameter('range_min', 0.3)
        self.declare_parameter('range_max', 2.5)
        self.declare_parameter('scan_height', 10)
        self.declare_parameter('scan_time', 0.033)
        
        self.output_frame = self.get_parameter('output_frame').value
        self.range_min = self.get_parameter('range_min').value
        self.range_max = self.get_parameter('range_max').value
        self.scan_height = self.get_parameter('scan_height').value
        self.scan_time = self.get_parameter('scan_time').value
        
        # Ultimi messaggi ricevuti
        self.last_depth_msg = None
        self.last_camera_info = None
        
        # Bridge per conversioni OpenCV
        self.bridge = CvBridge()
        
        # QoS per migliore sincronizzazione
        qos_profile = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST
        )
        
        # Sottoscrizioni
        self.depth_sub = self.create_subscription(
            Image,
            '/oak/stereo/image_raw',
            self.depth_callback,
            qos_profile
        )
        
        self.camera_info_sub = self.create_subscription(
            CameraInfo,
            '/oak/stereo/camera_info',
            self.camera_info_callback,
            qos_profile
        )
        
        # Publisher per laser scan
        self.scan_pub = self.create_publisher(
            LaserScan,
            '/scan',
            10
        )
        
        self.get_logger().info('Depth to LaserScan Wrapper started')
        
    def depth_callback(self, msg):
        """Memorizza l'ultima immagine depth"""
        self.last_depth_msg = msg
        
        # Se abbiamo entrambi, processa
        if self.last_depth_msg and self.last_camera_info:
            self.process_scan()
    
    def camera_info_callback(self, msg):
        """Memorizza l'ultima camera_info"""
        self.last_camera_info = msg
        
        # Se abbiamo entrambi, processa
        if self.last_depth_msg and self.last_camera_info:
            self.process_scan()
    
    def process_scan(self):
        """Converte l'immagine depth in laser scan"""
        try:
            # Converte l'immagine
            depth_image = self.bridge.imgmsg_to_cv2(
                self.last_depth_msg, 
                desired_encoding='passthrough'
            )
            
            # Estrai parametri della camera
            height = self.last_camera_info.height
            width = self.last_camera_info.width
            fx = self.last_camera_info.k[0]  # Focale x
            fy = self.last_camera_info.k[4]  # Focale y
            cx = self.last_camera_info.k[2]  # Centro x
            cy = self.last_camera_info.k[5]  # Centro y
            
            # Calcola l'indice di riga centrale
            center_row = height // 2
            start_row = max(0, center_row - self.scan_height // 2)
            end_row = min(height, center_row + self.scan_height // 2)
            
            # Crea il messaggio LaserScan
            scan_msg = LaserScan()
            scan_msg.header = self.last_depth_msg.header
            scan_msg.header.frame_id = self.output_frame
            
            scan_msg.angle_min = -math.pi / 2  # -90 gradi
            scan_msg.angle_max = math.pi / 2   # +90 gradi
            scan_msg.angle_increment = math.pi / (width - 1)
            
            scan_msg.time_increment = 0.0
            scan_msg.scan_time = self.scan_time
            
            scan_msg.range_min = self.range_min
            scan_msg.range_max = self.range_max
            
            # Inizializza ranges
            scan_msg.ranges = [float('inf')] * width
            
            # Processa le righe selezionate
            for row in range(start_row, end_row):
                for col in range(width):
                    depth = depth_image[row, col] / 1000.0  # Converti mm in metri
                    
                    if depth > 0 and self.range_min <= depth <= self.range_max:
                        # Calcola la distanza nel piano orizzontale
                        x = (col - cx) * depth / fx
                        z = (row - cy) * depth / fy
                        range_val = math.sqrt(x**2 + depth**2)
                        
                        # Mantieni il valore più vicino
                        if range_val < scan_msg.ranges[col]:
                            scan_msg.ranges[col] = range_val
            
            # Sostituisci infiniti con 0 (per compatibilità)
            for i in range(width):
                if scan_msg.ranges[i] == float('inf'):
                    scan_msg.ranges[i] = 0.0
            
            # Pubblica
            self.scan_pub.publish(scan_msg)
            
        except Exception as e:
            self.get_logger().error(f'Error processing scan: {str(e)}')

def main(args=None):
    rclpy.init(args=args)
    node = DepthToScanWrapper()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()