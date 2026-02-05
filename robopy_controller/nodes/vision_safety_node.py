#!/usr/bin/env python3
# vision_safety_node.py
import rclpy
from rclpy.node import Node
from rtabmap_msgs.msg import Info
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool
import time

class VisionSafetyNode(Node):
    def __init__(self):
        super().__init__('vision_safety_node')
        
        # Parameters
        self.declare_parameter('min_inliers', 15)
        self.declare_parameter('timeout_sec', 1.0)
        self.declare_parameter('enable_auto_stop', True)
        
        self.min_inliers = self.get_parameter('min_inliers').value
        self.timeout = self.get_parameter('timeout_sec').value
        self.auto_stop = self.get_parameter('enable_auto_stop').value
        
        # State
        self.last_good_time = time.time()
        self.vision_ok = False
        self.inliers = 0
        
        # Subscribers
        self.sub_info = self.create_subscription(
            Info, '/rtabmap/info', self.info_callback, 10)
        
        # Publishers
        self.pub_cmd_vel = self.create_publisher(Twist, '/cmd_vel_safe', 10)
        self.pub_status = self.create_publisher(Bool, '/vision/status', 10)
        
        # Subscription to original cmd_vel
        self.sub_cmd_vel = self.create_subscription(
            Twist, '/cmd_vel', self.cmd_vel_callback, 10)
        
        # Timer for safety check
        self.timer = self.create_timer(0.1, self.safety_check)
        
        self.get_logger().info("Vision safety node started")
    
    def info_callback(self, msg):
        # Extract inliers from RTAB-Map info
        if hasattr(msg, 'stats') and 'Inliers' in msg.stats:
            self.inliers = msg.stats['Inliers']
            
            # Check if vision is good
            if self.inliers >= self.min_inliers:
                self.vision_ok = True
                self.last_good_time = time.time()
            else:
                self.vision_ok = False
        
        # Publish status
        status_msg = Bool()
        status_msg.data = self.vision_ok
        self.pub_status.publish(status_msg)
    
    def cmd_vel_callback(self, msg):
        # Forward cmd_vel only if vision is OK or timeout not expired
        current_time = time.time()
        
        if self.auto_stop:
            # If vision lost for too long, STOP the robot
            if current_time - self.last_good_time > self.timeout:
                stop_msg = Twist()
                stop_msg.linear.x = 0.0
                stop_msg.angular.z = 0.0
                self.pub_cmd_vel.publish(stop_msg)
                self.get_logger().warn("VISION LOST - STOPPING ROBOT")
                return
        
        # Otherwise, forward the command
        self.pub_cmd_vel.publish(msg)
    
    def safety_check(self):
        # Periodic check
        current_time = time.time()
        
        if self.vision_ok:
            self.get_logger().debug(f"Vision OK - Inliers: {self.inliers}")
        else:
            if current_time - self.last_good_time > self.timeout:
                self.get_logger().warn(f"Vision LOST for {self.timeout}s - Inliers: {self.inliers}")

def main():
    rclpy.init()
    node = VisionSafetyNode()
    rclpy.spin(node)
    rclpy.shutdown()

if __name__ == '__main__':
    main()