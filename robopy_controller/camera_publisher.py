#!/usr/bin/env python3
#camera_publisher.py
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from cv_bridge import CvBridge
import numpy as np
from picamera2 import Picamera2
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

# Usa QoS RELIABLE invece di BEST_EFFORT
qos = QoSProfile(
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
    history=HistoryPolicy.KEEP_LAST
)

class CameraPublisher(Node):
    def __init__(self):
        super().__init__('camera_publisher')

        #self.publisher_image = self.create_publisher(Image, '/raw_image', 10)
        #self.publisher_info = self.create_publisher(CameraInfo, '/camera_info', 10)

        self.publisher_image = self.create_publisher(Image, '/camera/image_raw', qos)
        self.publisher_info = self.create_publisher(CameraInfo, '/camera/camera_info', qos)

        self.bridge = CvBridge()

        self.declare_parameter('width', 320)
        self.declare_parameter('height', 240)
        self.width = self.get_parameter('width').get_parameter_value().integer_value
        self.height = self.get_parameter('height').get_parameter_value().integer_value

        # Inizializza Picamera2 senza preview
        self.picam2 = Picamera2()
        config = self.picam2.create_still_configuration(
            main={"size": (self.width, self.height)}
        )
        self.picam2.configure(config)
        self.picam2.start()

        self.timer = self.create_timer(0.1, self.publish_frame)  # ~10 FPS

    def publish_frame(self):
        try:
            frame = self.picam2.capture_array()
            msg = self.bridge.cv2_to_imgmsg(frame, encoding="rgb8")
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = "camera_frame"

            self.publisher_image.publish(msg)
            self.publisher_info.publish(self.get_camera_info(msg.header.stamp))

        except Exception as e:
            self.get_logger().warn(f"Errore durante la pubblicazione del frame: {e}")

    def get_camera_info(self, stamp):
        cam_info = CameraInfo()
        cam_info.header.stamp = stamp
        cam_info.header.frame_id = "camera_frame"
        cam_info.width = self.width
        cam_info.height = self.height

        fx = 2953.33141
        fy = 2956.28570
        cx = 1375.38647
        cy = 933.83090

        cam_info.k = [fx, 0, cx,
                      0, fy, cy,
                      0, 0, 1]

        cam_info.d = [-0.44289643, 0.27617767, 0.0009852, 0.00142857, -0.09458454]
        cam_info.distortion_model = "plumb_bob"

        cam_info.r = [1, 0, 0,
                      0, 1, 0,
                      0, 0, 1]

        cam_info.p = [fx, 0, cx, 0,
                      0, fy, cy, 0,
                      0, 0, 1, 0]

        return cam_info

def main(args=None):
    rclpy.init(args=args)
    node = CameraPublisher()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
