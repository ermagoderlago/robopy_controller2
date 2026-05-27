#!/usr/bin/env python3
# topic_checker_node.py

import rclpy
from rclpy.node import Node
import time

class TopicChecker(Node):
    def __init__(self):
        super().__init__('topic_checker')
        
        # Lista di default come stringa
        default_topics = "/oak/rgb/image_raw,/oak/rgb/camera_info,/oak/stereo/image_raw,/oak/imu/data,/odometry/filtered"
        self.declare_parameter('required_topics', default_topics)
        
        # Leggi e splitta
        topics_str = self.get_parameter('required_topics').value
        self.required_topics = [t.strip() for t in topics_str.split(',') if t.strip()]
        
        self.get_logger().info(f'Topic Checker started. Required topics: {self.required_topics}')
        self.timer = self.create_timer(5.0, self.check_topics)
    
    def check_topics(self):
        try:
            active_topics = self.get_topic_names_and_types()
            active_names = [topic[0] for topic in active_topics]
            
            available = []
            missing = []
            
            for req in self.required_topics:
                if req in active_names:
                    available.append(req)
                else:
                    missing.append(req)
            
            if missing:
                self.get_logger().warning(f'Missing topics: {missing}')
                self.get_logger().info(f'Available topics: {available}')
            else:
                self.get_logger().info('All required topics available!')
                
        except Exception as e:
            self.get_logger().error(f'Error checking topics: {e}')

def main(args=None):
    rclpy.init(args=args)
    node = TopicChecker()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()