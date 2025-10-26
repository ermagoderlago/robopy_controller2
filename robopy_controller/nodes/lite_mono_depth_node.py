#!/usr/bin/env python3

#import os
#os.environ['RMW_IMPLEMENTATION'] = 'rmw_fastrtps_cpp'
#os.environ['FASTRTPS_TRANSPORT_USE_SHM'] = '0'
#os.environ['OMP_NUM_THREADS'] = '1'

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import torch
import cv2
import numpy as np
from ament_index_python.packages import get_package_share_directory
import os

from robopy_controller.networks.litemono import LiteMono

class LiteMonoDepthNode(Node):
    def __init__(self):
        super().__init__('lite_mono_depth_node')

        self.declare_parameter("image_topic", "/rgb/image")
        self.declare_parameter("depth_topic", "/depth/image")

        image_topic = self.get_parameter("image_topic").get_parameter_value().string_value
        depth_topic = self.get_parameter("depth_topic").get_parameter_value().string_value

        self.bridge = CvBridge()
        self.publisher = self.create_publisher(Image, depth_topic, 10)
        self.subscription = self.create_subscription(Image, image_topic, self.image_callback, 10)

        # Carica il modello
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = LiteMono().to(self.device)

        package_dir = get_package_share_directory('robopy_controller')
        weights_dir = os.path.join(package_dir, 'weights')
        encoder_path = os.path.join(weights_dir, 'encoder.pth')
        depth_path = os.path.join(weights_dir, 'depth.pth')

        #self.model.encoder.load_state_dict(torch.load(encoder_path, map_location=self.device))
        #self.model.decoder.load_state_dict(torch.load(depth_path, map_location=self.device))
        self.model.eval()

        self.get_logger().info("Lite-Mono model loaded and node initialized.")

    def image_callback(self, msg):
        try:
            # Converti immagine ROS -> OpenCV
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding="rgb8")
            input_img = cv2.resize(cv_image, (640, 192))  # Assicurati che sia compatibile col modello

            # Preprocessing
            img_tensor = torch.from_numpy(input_img).float().permute(2, 0, 1).unsqueeze(0) / 255.0
            img_tensor = img_tensor.to(self.device)

            with torch.no_grad():
                pred = self.model(img_tensor)
                # Prendi la stima a risoluzione più alta (di solito "disp", 0)
                disp = pred[("disp", 0)].squeeze().cpu().numpy()
                # Converti la disparità in profondità (evita divisione per zero)
                min_disp = 1e-6
                depth = 1.0 / np.maximum(disp, min_disp)

            # Crea il messaggio Image ROS
            depth_msg = Image()
            depth_msg.header = msg.header
            depth_msg.height = depth.shape[0]
            depth_msg.width = depth.shape[1]
            depth_msg.encoding = "32FC1"
            depth_msg.is_bigendian = False
            depth_msg.step = depth.shape[1] * 4  # 4 bytes per float32
            depth_msg.data = depth.astype(np.float32).tobytes()
            self.publisher.publish(depth_msg)

        except Exception as e:
            self.get_logger().error(f"Errore nel processing dell'immagine: {str(e)}")

def main(args=None):
    rclpy.init(args=args)
    node = LiteMonoDepthNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
