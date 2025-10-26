import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import numpy as np
import cv2
import ncnn
import threading

class MiDaSDepthNodeNCNN(Node):
    def __init__(self):
        super().__init__('midas_depth_node_ncnn')

        self.sub = self.create_subscription(Image, '/rgb/image', self.image_callback, 10)
        self.pub = self.create_publisher(Image, '/depth/image', 10)

        self.bridge = CvBridge()

        # Load NCNN model
        self.net = ncnn.Net()
        self.net.opt.use_vulkan_compute = False
        self.net.load_param("/opt/midas/midas_small-opt.param")
        self.net.load_model("/opt/midas/midas_small-opt.bin")

        # Preprocessing config
        self.target_size = (256, 256)  # expected input size
        self.lock = threading.Lock()

        self.get_logger().info("MiDaS NCNN Node initialized.")

    def image_callback(self, msg):
        with self.lock:
            frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            input_blob = self.preprocess(frame)
            depth_map = self.run_ncnn(input_blob)
            depth_vis = self.postprocess(depth_map, frame.shape[:2])

            out_msg = self.bridge.cv2_to_imgmsg(depth_vis, encoding='mono8')
            out_msg.header = msg.header
            self.pub.publish(out_msg)

    def preprocess(self, img):
        img_resized = cv2.resize(img, self.target_size)
        img_rgb = cv2.cvtColor(img_resized, cv2.COLOR_BGR2RGB)
        img_norm = img_rgb.astype(np.float32) / 255.0

        mat_in = ncnn.Mat.from_pixels(img_norm, ncnn.Mat.PixelType.PIXEL_RGB, *self.target_size)
        mean_vals = (0.485, 0.456, 0.406)
        norm_vals = (0.229, 0.224, 0.225)
        mat_in.substract_mean_normalize(mean_vals, norm_vals)
        return mat_in

    def run_ncnn(self, mat_in):
        ex = self.net.create_extractor()
        ex.input("input", mat_in)

        ret, mat_out = ex.extract("output")
        if ret != 0:
            self.get_logger().error("Failed to extract from MiDaS NCNN")
            return np.zeros((256, 256), dtype=np.uint8)

        return np.array(mat_out).reshape(256, 256)

    def postprocess(self, depth, original_shape):
        depth_norm = cv2.normalize(depth, None, 0, 255, cv2.NORM_MINMAX)
        depth_u8 = depth_norm.astype(np.uint8)
        return cv2.resize(depth_u8, (original_shape[1], original_shape[0]))

def main(args=None):
    rclpy.init(args=args)
    node = MiDaSDepthNodeNCNN()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()

