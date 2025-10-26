import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import Header
import numpy as np
import cv2
from cv_bridge import CvBridge

class CameraPublisherNode(Node):
    def __init__(self):
        super().__init__('camera_publisher_node')

        self.declare_parameter('device_id', 0)  # /dev/video0
        self.declare_parameter('width', 640)
        self.declare_parameter('height', 480)
        self.declare_parameter('frame_id', 'camera_frame')
        self.declare_parameter('fps', 10.0)

        device_id = self.get_parameter('device_id').get_parameter_value().integer_value
        self.width = self.get_parameter('width').get_parameter_value().integer_value
        self.height = self.get_parameter('height').get_parameter_value().integer_value
        self.frame_id = self.get_parameter('frame_id').get_parameter_value().string_value
        fps = self.get_parameter('fps').get_parameter_value().double_value

        self.bridge = CvBridge()
        self.image_pub = self.create_publisher(Image, '/rgb/image', 10)
        self.camera_info_pub = self.create_publisher(CameraInfo, '/rgb/camera_info', 10)

        self.cap = cv2.VideoCapture(device_id)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

        if not self.cap.isOpened():
            self.get_logger().error(f"Camera /dev/video{device_id} non disponibile.")
            raise RuntimeError(f"Cannot open /dev/video{device_id}")

        self.get_logger().info(f"Streaming da /dev/video{device_id} a {fps} FPS")
        self.timer = self.create_timer(1.0 / fps, self.publish_image)

    def publish_image(self):
        ret, frame = self.cap.read()
        if not ret:
            self.get_logger().warn("Frame non letto dalla camera.")
            return

        msg = self.bridge.cv2_to_imgmsg(frame, encoding='bgr8')
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id
        self.image_pub.publish(msg)

        cam_info = CameraInfo()
        cam_info.header = msg.header
        cam_info.width = self.width
        cam_info.height = self.height
        cam_info.k = [self.width, 0, self.width / 2,
                      0, self.width, self.height / 2,
                      0, 0, 1]
        cam_info.d = [0.0] * 5
        cam_info.r = [1.0, 0.0, 0.0,
                      0.0, 1.0, 0.0,
                      0.0, 0.0, 1.0]
        cam_info.p = [self.width, 0, self.width / 2, 0,
                      0, self.width, self.height / 2, 0,
                      0, 0, 1, 0]
        self.camera_info_pub.publish(cam_info)

    def destroy_node(self):
        if self.cap.isOpened():
            self.cap.release()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = CameraPublisherNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()




