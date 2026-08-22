#motion_detector_node.py
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import Bool
from cv_bridge import CvBridge
import cv2
import numpy as np
import time


class MotionDetectorNode(Node):
    def __init__(self):
        super().__init__('motion_detector_node')

        self.subscription = self.create_subscription(
            Image,
            '/rgb/image',
            self.image_callback,
            10)

        self.publisher = self.create_publisher(Bool, '/motion_detected', 10)
        self.bridge = CvBridge()
        self.last_frame = None
        self.motion_timeout = 120.0  # secondi
        self.motion_detected = False
        self.last_motion_time = time.time()
        self.timer = self.create_timer(1.0, self.check_motion_timeout)

        self.get_logger().info("Motion detector node started")

    def image_callback(self, msg):
        try:
            current_frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            gray = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21, 21), 0)

            if self.last_frame is None:
                self.last_frame = gray
                return

            frame_delta = cv2.absdiff(self.last_frame, gray)
            thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)[1]
            thresh = cv2.dilate(thresh, None, iterations=2)
            contours, _ = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            motion = any(cv2.contourArea(c) > 500 for c in contours)

            if motion:
                if not self.motion_detected:
                    self.motion_detected = True
                    self.get_logger().info("Movimento rilevato")
                self.last_motion_time = time.time()
            
            msg = Bool()
            msg.data = self.motion_detected
            self.publisher.publish(msg)
            self.last_frame = gray

        except Exception as e:
            self.get_logger().error(f"Errore nel processamento immagine: {e}")

    def check_motion_timeout(self):
        if self.motion_detected and time.time() - self.last_motion_time > self.motion_timeout:
            self.motion_detected = False
            self.get_logger().info("Movimento terminato (timeout)")
            msg = Bool()
            msg.data = False
            self.publisher.publish(msg)

def main(args=None):
    rclpy.init(args=args)
    node = MotionDetectorNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
