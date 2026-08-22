#!/usr/bin/env python3
import os
os.environ['RMW_IMPLEMENTATION'] = 'rmw_fastrtps_cpp'
os.environ['FASTRTPS_TRANSPORT_USE_SHM'] = '0'
os.environ['OPENCV_SKIP_PYTHON_LOAD_EXTRA_MODULES'] = '1'

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import torch
import torchvision.transforms as T
import cv2
import numpy as np
import time

class MidasDepthNode(Node):
    def __init__(self):
        super().__init__('midas_depth_node')

        self.bridge = CvBridge()
        self.subscription = self.create_subscription(
            Image,
            '/rgb/image',
            self.image_callback,
            10)
        self.publisher = self.create_publisher(Image, '/rgb/depth', 10)

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.dtype = torch.float16 if self.device.type == "cuda" else torch.float32

        self.get_logger().info(f"Caricamento modello MiDaS_small su {self.device} (dtype: {self.dtype})")
        self.model = torch.hub.load("intel-isl/MiDaS", "MiDaS_small", trust_repo=True)
        self.model.to(self.device, dtype=self.dtype).eval()

        self.transform = torch.hub.load("intel-isl/MiDaS", "transforms").small_transform

    def image_callback(self, msg):
        try:
            start = time.time()

            # Convert ROS -> OpenCV
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            rgb = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)

            input_tensor = self.transform(rgb).to(self.device, dtype=self.dtype)

            with torch.no_grad():
                prediction = self.model(input_tensor)

                prediction = torch.nn.functional.interpolate(
                    prediction.unsqueeze(1),
                    size=cv_image.shape[:2],
                    mode="bicubic",
                    align_corners=False,
                ).squeeze()

            depth = prediction.cpu().numpy().astype(np.float32)

            # Publish
            depth_msg = self.bridge.cv2_to_imgmsg(depth, encoding="32FC1")
            depth_msg.header.stamp = msg.header.stamp
            depth_msg.header.frame_id = "camera_frame"
            self.publisher.publish(depth_msg)

            end = time.time()
            self.get_logger().info(f"Frame processato in {end - start:.2f} s")

        except Exception as e:
            self.get_logger().error(f"Errore nella stima della profondità: {e}")

def main(args=None):
    rclpy.init(args=args)
    node = MidasDepthNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()



