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

from LiteMono import LiteMono  # Importa il tuo modello definito

class LiteMonoDepthNode(Node):
    def __init__(self):
        super().__init__('lite_mono_depth_node')

        self.declare_parameter("image_topic", "/rgb/image")
        self.declare_parameter("depth_topic", "/rgb/depth")

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

        self.model.encoder.load_state_dict(torch.load(encoder_path, map_location=self.device))
        self.model.decoder.load_state_dict(torch.load(depth_path, map_location=self.device))
        self.model.eval()

        self.get_logger().info("Lite-Mono model loaded and node initialized.")

    def image_callback(self, msg):
        try:
            # Converti immagine ROS -> OpenCV
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding="rgb8")
            print("Forma immagine ricevuta:", cv_image.shape)  # <--- AGGIUNGI QUESTA RIGA
            input_img = cv2.resize(cv_image, (640, 192))  # Assicurati che sia compatibile col modello

            # Preprocessing
            img_tensor = torch.from_numpy(input_img).float().permute(2, 0, 1).unsqueeze(0) / 255.0
            img_tensor = img_tensor.to(self.device)

            with torch.no_grad():
                pred_disp = self.model(img_tensor)
                depth = pred_disp.squeeze().cpu().numpy()

            # Normalizza profondità per visualizzazione
            depth_scaled = (depth / depth.max() * 255.0).astype(np.uint8)

            # Converti in messaggio ROS e pubblica
            depth_msg = self.bridge.cv2_to_imgmsg(depth_scaled, encoding="mono8")
            depth_msg.header = msg.header
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