#!/usr/bin/env python3
import time
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

def main():
    rclpy.init()
    node = Node('nomad_test_injector')
    pub_text = node.create_publisher(String, '/ai/input/text', 10)
    pub_rx = node.create_publisher(String, '/robopy/conversation_rx', 10)
    
    msg = String()
    msg.data = "esplora con Nomad"
    
    print(f"Injecting: '{msg.data}'...")
    time.sleep(1.0)
    for i in range(3):
        pub_text.publish(msg)
        pub_rx.publish(msg)
        print(f"Published iteration {i+1}")
        rclpy.spin_once(node, timeout_sec=0.5)
        time.sleep(0.5)
        
    print("Done injecting.")
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
