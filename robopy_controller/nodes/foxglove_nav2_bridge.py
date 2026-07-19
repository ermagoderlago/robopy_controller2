#!/usr/bin/env python3
# foxglove_nav2_bridge.py
# Bridges Foxglove Studio's /goal_pose topic (PoseStamped)
# to Nav2's /navigate_to_pose Action Server (NavigateToPose)
# because Jazzy Nav2 removed the native topic subscriber.

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose

class FoxgloveNav2Bridge(Node):
    def __init__(self):
        super().__init__('foxglove_nav2_bridge')
        
        # Subscribe to Foxglove's Nav Goal topic (ROS 2 standard)
        self.subscription = self.create_subscription(
            PoseStamped,
            '/goal_pose',
            self.goal_pose_callback,
            10
        )

        # Subscribe to Foxglove's Nav Goal topic (ROS 1 / Legacy standard)
        self.subscription_legacy = self.create_subscription(
            PoseStamped,
            '/move_base_simple/goal',
            self.goal_pose_callback,
            10
        )
        
        # Action client for Nav2
        self.action_client = ActionClient(self, NavigateToPose, '/navigate_to_pose')
        self.get_logger().info("Foxglove-to-Nav2 Bridge instantiated. Waiting for goals on /goal_pose...")

    def goal_pose_callback(self, msg: PoseStamped):
        self.get_logger().info(f"Received new 2D goal from Foxglove: x={msg.pose.position.x:.2f}, y={msg.pose.position.y:.2f}. Translating to Nav2 BT Action...")
        
        if not self.action_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error("Nav2 Action Server (/navigate_to_pose) not available! Is bt_navigator running?")
            return

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = msg
        goal_msg.behavior_tree = "" # Usa il default

        self.get_logger().info("Sending goal to Nav2 bt_navigator...")
        self.action_client.send_goal_async(goal_msg)

def main(args=None):
    rclpy.init(args=args)
    node = FoxgloveNav2Bridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
