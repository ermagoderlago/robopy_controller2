import pytest
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist
import time

class KinematicsTestNode(Node):
    def __init__(self):
        super().__init__('kinematics_test_node')
        self.subscription = self.create_subscription(Odometry, '/odom', self.odom_callback, 10)
        self.publisher = self.create_publisher(Twist, '/cmd_vel', 10)
        self.initial_x = None
        self.current_x = None

    def odom_callback(self, msg):
        if self.initial_x is None:
            self.initial_x = msg.pose.pose.position.x
        self.current_x = msg.pose.pose.position.x

def test_kinematics_sim():
    rclpy.init()
    node = KinematicsTestNode()
    
    # Send cmd_vel 0.2 m/s for 3 seconds -> expected 0.6m
    twist = Twist()
    twist.linear.x = 0.2
    
    start_time = time.time()
    while time.time() - start_time < 3.0:
        node.publisher.publish(twist)
        rclpy.spin_once(node, timeout_sec=0.1)

    twist.linear.x = 0.0
    node.publisher.publish(twist)
    
    time.sleep(0.5)
    rclpy.spin_once(node, timeout_sec=0.5)
    
    if node.initial_x is not None and node.current_x is not None:
        distance = node.current_x - node.initial_x
        assert 0.5 < distance < 0.7, f"Expected ~0.6m, got {distance}m"
    else:
        pytest.fail("Did not receive odometry")

    node.destroy_node()
    rclpy.shutdown()
