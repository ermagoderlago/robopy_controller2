# test_2d_static.py
#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
import cv2
import numpy as np
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import time

class StaticTestNode(Node):
    def __init__(self):
        super().__init__('static_2d_test')
        
        # Publisher per debug
        self.pub_static = self.create_publisher(Image, '/test/static_matches', 10)
        self.bridge = CvBridge()
        
        # Sottoscrizione all'immagine mono
        self.sub_mono = self.create_subscription(
            Image,
            '/camera/image_raw',
            self.mono_callback,
            10
        )
        
        self.prev_frame = None
        self.prev_kpts = None
        self.prev_desc = None
        
        self.get_logger().info("🚀 TEST STATICO 2D AVVIATO - Robot FERMO")
        self.get_logger().info("🔍 Controllare:")
        self.get_logger().info("   1. Keypoints dovrebbero essere negli stessi pixel")
        self.get_logger().info("   2. Linee di matching dovrebbero essere corte/orizzontali")
        self.get_logger().info("   3. NESSUN movimento verticale o diagonale")
    
    def mono_callback(self, msg):
        # Converti in OpenCV
        frame = self.bridge.imgmsg_to_cv2(msg, 'mono8')
        
        # Estrai FAST corners (semplice e stabile)
        fast = cv2.FastFeatureDetector_create(threshold=20)
        kpts = fast.detect(frame, None)
        
        # Converti in array numpy
        kpts_array = np.array([[kp.pt[0], kp.pt[1]] for kp in kpts])
        
        if self.prev_frame is not None:
            # Matching ottico semplice (Lucas-Kanade)
            prev_pts = self.prev_kpts.astype(np.float32)
            curr_pts, status, _ = cv2.calcOpticalFlowPyrLK(
                self.prev_frame, frame, prev_pts, None
            )
            
            # Filtra punti buoni
            good_new = curr_pts[status == 1]
            good_old = prev_pts[status == 1]
            
            # Calcola spostamento medio
            if len(good_new) > 0:
                displacement = np.mean(np.linalg.norm(good_new - good_old, axis=1))
                
                # DA FERMO lo spostamento dovrebbe essere < 0.5 pixel
                if displacement > 2.0:
                    self.get_logger().warn(f"⚠️ MOVIMENTO ANOMALO: {displacement:.2f} px")
                
                # Visualizza
                vis = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                for new, old in zip(good_new, good_old):
                    a, b = new.astype(int), old.astype(int)
                    cv2.line(vis, tuple(a), tuple(b), (0, 255, 0), 1)
                    cv2.circle(vis, tuple(a), 3, (0, 0, 255), -1)
                
                # Pubblica immagine debug
                debug_msg = self.bridge.cv2_to_imgmsg(vis, 'bgr8')
                self.pub_static.publish(debug_msg)
        
        # Salva per prossimo frame
        self.prev_frame = frame.copy()
        self.prev_kpts = kpts_array[:100]  # Limita a 100 punti

def main(args=None):
    rclpy.init(args=args)
    node = StaticTestNode()
    rclpy.spin(node)
    rclpy.shutdown()

if __name__ == '__main__':
    main()