import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import torch
import torch.nn as nn
import numpy as np
import cv2
import os
import sys
import types

# --- Bypass per moduli mancanti nel checkpoint ---
# Modulo finto 'metrics'
sys.modules['metrics'] = types.ModuleType('metrics')

# Modulo 'models' alias al modulo reale dove è definita MobileNetSkipAdd
import robopy_controller.nodes.mobilenet_skipadd as mobilenet_module
sys.modules['models'] = mobilenet_module
# --------------------------------------------------

class FastDepth(nn.Module):
    def __init__(self):
        super(FastDepth, self).__init__()
        self.encoder = nn.Sequential(
            nn.Conv2d(3, 32, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1),
            nn.ReLU()
        )
        self.decoder = nn.Sequential(
            nn.ConvTranspose2d(64, 32, kernel_size=4, stride=2, padding=1),
            nn.ReLU(),
            nn.ConvTranspose2d(32, 1, kernel_size=4, stride=2, padding=1)
        )

    def forward(self, x):
        x = self.encoder(x)
        x = self.decoder(x)
        return x

class DepthPublisherNode(Node):
    def __init__(self):
        super().__init__('depth_publisher_node')
        self.subscription = self.create_subscription(
            Image,
            '/rgb/image',
            self.image_callback,
            10)
        self.publisher = self.create_publisher(Image, '/rgb/depth', 10)
        self.bridge = CvBridge()

        model_path = os.path.join(
            os.path.dirname(__file__),
            '..',
            'models',
            'mobilenet-nnconv5dw-skipadd-pruned.pth.tar'
        )
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.model = FastDepth().to(self.device)

        checkpoint = torch.load(model_path, map_location=self.device)
        # Alcuni checkpoint sono dizionari con key 'state_dict'
        state_dict = checkpoint.get('state_dict', checkpoint)
        self.model.load_state_dict(state_dict)
        self.model.eval()

    def preprocess(self, image):
        image = cv2.resize(image, (224, 224))
        image = image.astype(np.float32) / 255.0
        image = torch.from_numpy(image).permute(2, 0, 1).unsqueeze(0)
        return image.to(self.device)

    def postprocess(self, depth_tensor, original_shape):
        depth = depth_tensor.squeeze().cpu().detach().numpy()
        depth = cv2.resize(depth, (original_shape[1], original_shape[0]))
        depth = cv2.normalize(depth, None, 0, 255, cv2.NORM_MINMAX)
        return depth.astype(np.uint8)

    def image_callback(self, msg):
        try:
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='rgb8')
            input_tensor = self.preprocess(cv_image)
            with torch.no_grad():
                depth_tensor = self.model(input_tensor)
            depth_map = self.postprocess(depth_tensor, cv_image.shape[:2])
            depth_msg = self.bridge.cv2_to_imgmsg(depth_map, encoding='mono8')
            depth_msg.header.stamp = msg.header.stamp
            depth_msg.header.frame_id = msg.header.frame_id
            self.publisher.publish(depth_msg)
        except Exception as e:
            self.get_logger().error(f"Errore nella stima della profondità: {e}")

def main(args=None):
    rclpy.init(args=args)
    node = DepthPublisherNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()


