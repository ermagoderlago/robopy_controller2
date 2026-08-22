import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import cv2
import numpy as np
from LiteDepth.models import create_model
import torch

class DepthNode(Node):
    def __init__(self):
        super().__init__('depth_node')
        self.bridge = CvBridge()
        self.sub = self.create_subscription(Image, '/camera/image_raw', self.callback, 10)
        self.pub = self.create_publisher(Image, '/depth/image_raw', 10)
        
        # Inizializza il modello LiteDepth
        self.model = create_model("AdaBins")
        checkpoint = torch.load("dpt_swin2_large_384.pt", map_location=torch.device('cpu'))
        self.model.load_state_dict(checkpoint['model'])
        self.model.eval()

    def callback(self, msg):
        # Converti ROS Image -> OpenCV
        cv_image = self.bridge.imgmsg_to_cv2(msg, "bgr8")
        
        # Preprocessing
        img_resized = cv2.resize(cv_image, (640, 192))
        input_tensor = torch.from_numpy(img_resized / 255.0).permute(2,0,1).float().unsqueeze(0)
        
        # Inference
        with torch.no_grad():
            depth = self.model(input_tensor)[0].squeeze().cpu().numpy()
        
        # Normalizza la depth (0-10000 mm)
        depth_normalized = (depth * 10000).astype(np.uint16)
        
        # Converti in ROS Image
        depth_msg = self.bridge.cv2_to_imgmsg(depth_normalized, "16UC1")
        depth_msg.header = msg.header
        self.pub.publish(depth_msg)

def main():
    rclpy.init()
    node = DepthNode()
    rclpy.spin(node)
    rclpy.shutdown()

if __name__ == '__main__':
    main()