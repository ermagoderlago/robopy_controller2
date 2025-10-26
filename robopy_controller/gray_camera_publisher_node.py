#!/usr/bin/env python3
import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy
from sensor_msgs.msg import Image, CameraInfo
from cv_bridge import CvBridge
import threading


class LibcameraGrayPublisher(Node):
    def __init__(self):
        super().__init__('libcamera_gray_publisher')

        qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10
        )

        self.publisher_image = self.create_publisher(Image, '/rgb/image', qos)
        self.publisher_info = self.create_publisher(CameraInfo, '/rgb/camera_info', qos)
        self.bridge = CvBridge()

        self.declare_parameter('width', 640)
        self.declare_parameter('height', 480)

        self.width = self.get_parameter('width').value
        self.height = self.get_parameter('height').value

        self.latest_stamp = None

        # Apri la FIFO come sorgente video
        self.cap = cv2.VideoCapture("/tmp/camera.yuv")

        if not self.cap.isOpened():
            self.get_logger().error("Impossibile aprire /tmp/camera.yuv")
            return

        self.get_logger().info("VideoCapture aperto su /tmp/camera.yuv")

        # Thread per la lettura continua dei frame
        self.running = True
        self.thread = threading.Thread(target=self.capture_loop)
        self.thread.start()

        self.timer = self.create_timer(0.1, self.publish_camera_info)

    def capture_loop(self):
        while self.running and self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret:
                continue

            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_YUV2GRAY_420)
                stamp = self.get_clock().now().to_msg()
                msg = self.bridge.cv2_to_imgmsg(gray, encoding="mono8")
                msg.header.stamp = stamp
                msg.header.frame_id = "camera_frame"
                self.publisher_image.publish(msg)
                self.latest_stamp = stamp
            except Exception as e:
                self.get_logger().error(f"Errore nella conversione frame: {e}")

    def publish_camera_info(self):
        if not self.latest_stamp:
            return

        cam_info = CameraInfo()
        cam_info.header.stamp = self.latest_stamp
        cam_info.header.frame_id = "camera_frame"
        cam_info.width = self.width
        cam_info.height = self.height
        cam_info.k = [2953.33, 0, 1375.38, 0, 2956.28, 933.83, 0, 0, 1]
        cam_info.p = [2953.33, 0, 1375.38, 0, 0, 2956.28, 933.83, 0, 0, 0, 1, 0]
        cam_info.r = [1, 0, 0, 0, 1, 0, 0, 0, 1]
        cam_info.d = [-0.44289643, 0.27617767, 0.0009852, 0.00142857, -0.09458454]
        cam_info.distortion_model = "plumb_bob"

        self.publisher_info.publish(cam_info)

    def destroy_node(self):
        self.running = False
        if self.cap.isOpened():
            self.cap.release()
        self.thread.join()
        self.get_logger().info("Pipeline fermata")
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = LibcameraGrayPublisher()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()

