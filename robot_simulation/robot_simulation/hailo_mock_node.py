#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from vision_msgs.msg import Detection2DArray, Detection2D, ObjectHypothesisWithPose
import time
import random

class HailoMockNode(Node):
    def __init__(self):
        super().__init__('hailo_mock_node')
        
        self.declare_parameter('inference_latency_ms', 50)
        self.latency_ms = self.get_parameter('inference_latency_ms').value
        
        self.subscription = self.create_subscription(
            Image,
            '/camera/color/image_raw',
            self.image_callback,
            10
        )
        self.publisher_ = self.create_publisher(
            Detection2DArray,
            '/hailo_detections',
            10
        )
        self.get_logger().info(f'Hailo Mock Node started with {self.latency_ms}ms simulated latency.')

    def image_callback(self, msg):
        # Simulate inference time
        time.sleep(self.latency_ms / 1000.0)
        
        # Create dummy detection array
        detection_array = Detection2DArray()
        detection_array.header = msg.header
        
        # Simulate a random object detection
        if random.random() > 0.5:
            det = Detection2D()
            det.header = msg.header
            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis.class_id = 'person'
            hyp.hypothesis.score = random.uniform(0.7, 0.99)
            det.results.append(hyp)
            
            # Dummy bounding box
            det.bbox.center.position.x = float(msg.width / 2)
            det.bbox.center.position.y = float(msg.height / 2)
            det.bbox.size_x = 100.0
            det.bbox.size_y = 200.0
            
            detection_array.detections.append(det)
            
        self.publisher_.publish(detection_array)

def main(args=None):
    rclpy.init(args=args)
    node = HailoMockNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
