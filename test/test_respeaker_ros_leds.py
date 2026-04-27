#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import time

class RespeakerLedTest(Node):
    def __init__(self):
        super().__init__('respeaker_led_test')
        self.publisher_ = self.create_publisher(String, '/respeaker/led_command', 10)
        self.get_logger().info('ROS 2 ReSpeaker LED Test Node Started')
        
    def send_effect(self, effect):
        msg = String()
        msg.data = f"LED_EFFECT:{effect}"
        self.publisher_.publish(msg)
        self.get_logger().info(f'Published: {msg.data}')

def main():
    rclpy.init()
    node = RespeakerLedTest()
    
    effects = ['LISTENING', 'THINKING', 'SUCCESS', 'ERROR', 'IDLE', 'WAKE_WORD']
    
    try:
        print("Iniziando il test dei LED tramite ROS 2...")
        print("Assicurati che 'respeaker_interface_node' sia in esecuzione.")
        
        for effect in effects:
            print(f"Test effetto: {effect}")
            node.send_effect(effect)
            time.sleep(2.0)
            
        print("Test completato.")
        node.send_effect('IDLE')
        
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
