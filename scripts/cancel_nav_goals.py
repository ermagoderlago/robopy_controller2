#!/usr/bin/env python3
"""
Script per cancellare immediatamente tutti i goal attivi su Nav2 (BT Navigator e Controller)
e forzare cmd_vel a zero.
"""

import sys
import rclpy
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import Twist

def main():
    rclpy.init()
    node = rclpy.create_node("cancel_nav_goals")
    pub = node.create_publisher(Twist, "/cmd_vel", 10)
    
    stop_twist = Twist()
    for _ in range(5):
        pub.publish(stop_twist)

    action_cancel_services = [
        "/navigate_to_pose/_action/cancel_goal",
        "/navigate_through_poses/_action/cancel_goal",
        "/follow_path/_action/cancel_goal",
        "/compute_path_to_pose/_action/cancel_goal",
        "/compute_path_through_poses/_action/cancel_goal"
    ]

    for srv_name in action_cancel_services:
        client = node.create_client(CancelGoal, srv_name)
        if client.wait_for_service(timeout_sec=0.5):
            req = CancelGoal.Request()
            future = client.call_async(req)
            rclpy.spin_until_future_complete(node, future, timeout_sec=1.0)
            res = future.result()
            print(f"[CANCEL] {srv_name}: {res}")
        else:
            print(f"[SKIP] {srv_name}: non attivo")

    for _ in range(5):
        pub.publish(stop_twist)

    node.destroy_node()
    rclpy.shutdown()
    print("✅ Tutti i goal Nav2 sono stati cancellati.")

if __name__ == "__main__":
    main()
