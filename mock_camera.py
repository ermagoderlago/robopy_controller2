import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
import cv2
import numpy as np
import time

class MockCamera(Node):
    def __init__(self):
        super().__init__('mock_camera')
        self.pub = self.create_publisher(CompressedImage, '/rgb/image/compressed', 10)
        self.timer = self.create_timer(1.0, self.publish_image)
        self.get_logger().info("Mock Camera started")

    def publish_image(self):
        img = np.zeros((300, 300, 3), dtype=np.uint8)
        # Draw something
        cv2.putText(img, "Test", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.format = "jpeg"
        msg.data = np.array(cv2.imencode('.jpg', img)[1]).tobytes()
        
        self.pub.publish(msg)
        # self.get_logger().info("Published image")

def main(args=None):
    rclpy.init(args=args)
    node = MockCamera()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
