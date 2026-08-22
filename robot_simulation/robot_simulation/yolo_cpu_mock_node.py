import rclpy
from rclpy.node import Node
import math
from sensor_msgs.msg import Image
from nav_msgs.msg import Odometry
from vision_msgs.msg import Detection2D, Detection2DArray, ObjectHypothesisWithPose

class YoloCpuMockNode(Node):
    def __init__(self):
        super().__init__('yolo_cpu_mock_node')
        
        self.image_sub = self.create_subscription(
            Image,
            '/rgb/image',
            self.image_callback,
            10
        )
        
        self.odom_sub = self.create_subscription(
            Odometry,
            '/odom',
            self.odom_callback,
            10
        )
        
        self.det_pub = self.create_publisher(
            Detection2DArray,
            '/hailo/detections',
            10
        )
        
        self.timer = self.create_timer(0.2, self.timer_callback)
        
        self.robot_x = 0.0
        self.robot_y = 0.0
        self.robot_yaw = 0.0
        self.last_image_header = None
        
        self.objects = [
            {"id": "box", "x": 2.0, "y": 0.5},
            {"id": "table", "x": -1.0, "y": 1.5},
            {"id": "column", "x": 1.5, "y": -1.5},
            {"id": "obstacle", "x": -1.5, "y": -1.0}
        ]

    def image_callback(self, msg):
        self.last_image_header = msg.header

    def odom_callback(self, msg):
        self.robot_x = msg.pose.pose.position.x
        self.robot_y = msg.pose.pose.position.y
        
        q = msg.pose.pose.orientation
        siny_cosp = 2 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1 - 2 * (q.y * q.y + q.z * q.z)
        self.robot_yaw = math.atan2(siny_cosp, cosy_cosp)

    def timer_callback(self):
        det_array = Detection2DArray()
        if self.last_image_header:
            det_array.header = self.last_image_header
        else:
            det_array.header.stamp = self.get_clock().now().to_msg()
            det_array.header.frame_id = "camera_depth_optical_frame"
            
        for obj in self.objects:
            dx = obj["x"] - self.robot_x
            dy = obj["y"] - self.robot_y
            
            dist = math.hypot(dx, dy)
            if dist > 3.0:
                continue
                
            angle_to_obj = math.atan2(dy, dx)
            rel_angle = angle_to_obj - self.robot_yaw
            
            # Normalize angle to [-pi, pi]
            while rel_angle > math.pi:
                rel_angle -= 2 * math.pi
            while rel_angle < -math.pi:
                rel_angle += 2 * math.pi
                
            if abs(rel_angle) <= math.radians(60):
                det = Detection2D()
                
                # Mock bounding box
                # FOV is ~120 degrees (-60 to +60), map to 0-640 image width
                center_x = 320.0 - (rel_angle / math.radians(60)) * 320.0
                center_y = 240.0
                
                box_size = max(20.0, 150.0 / dist)
                
                det.bbox.center.position.x = center_x
                det.bbox.center.position.y = center_y
                det.bbox.size_x = box_size
                det.bbox.size_y = box_size
                
                hyp = ObjectHypothesisWithPose()
                hyp.hypothesis.class_id = obj["id"]
                hyp.hypothesis.score = 0.85
                
                det.results.append(hyp)
                det_array.detections.append(det)
                
        self.det_pub.publish(det_array)

def main(args=None):
    rclpy.init(args=args)
    node = YoloCpuMockNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
