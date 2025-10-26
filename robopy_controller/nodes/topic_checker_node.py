#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from rclpy.task import Future

class TopicChecker(Node):
    def __init__(self):
        super().__init__('topic_checker')
        
        self.declare_parameter('required_topics', [])
        self.required_topics = self.get_parameter('required_topics').value
        
        self.found_topics = set()
        self.timer = self.create_timer(1.0, self.check_topics)
        self.get_logger().info("Checking for required topics...")
        
    def check_topics(self):
        available_topics = self.get_topic_names_and_types()
        available_topic_names = [topic[0] for topic in available_topics]
        
        for topic in self.required_topics:
            if topic in available_topic_names:
                if topic not in self.found_topics:
                    self.get_logger().info(f"✓ Topic found: {topic}")
                    self.found_topics.add(topic)
            else:
                self.get_logger().warning(f"✗ Topic missing: {topic}")
        
        if len(self.found_topics) == len(self.required_topics):
            self.get_logger().info("All required topics are available!")
            self.timer.cancel()

def main(args=None):
    rclpy.init(args=args)
    node = TopicChecker()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()