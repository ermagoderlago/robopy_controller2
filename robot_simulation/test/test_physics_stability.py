import pytest
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
import time

class StabilityTestNode(Node):
    def __init__(self):
        super().__init__('stability_test_node')
        self.subscription = self.create_subscription(Odometry, '/odom', self.odom_callback, 10)
        self.initial_pose = None
        self.drifted = False

    def odom_callback(self, msg):
        if self.initial_pose is None:
            self.initial_pose = msg.pose.pose
        else:
            dx = abs(msg.pose.pose.position.x - self.initial_pose.position.x)
            dy = abs(msg.pose.pose.position.y - self.initial_pose.position.y)
            if dx > 0.01 or dy > 0.01:
                self.drifted = True

def test_physics_stability():
    rclpy.init()
    node = StabilityTestNode()
    
    start_time = time.time()
    while time.time() - start_time < 5.0:
        rclpy.spin_once(node, timeout_sec=0.1)
        if node.drifted:
            pytest.fail("Robot drifted without commands (Physics instability)")
            break

    node.destroy_node()
    rclpy.shutdown()
