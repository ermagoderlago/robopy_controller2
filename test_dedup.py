import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import time

class SpammerNode(Node):
    def __init__(self):
        super().__init__('spammer_node')
        self.publisher_ = self.create_publisher(String, '/robopy/conversation_rx', 10)
        
    def run(self):
        msg = String()
        msg.data = "Test deduplica: messaggio identico"
        self.get_logger().info("Inizio spam di 50 messaggi identici...")
        for i in range(50):
            self.publisher_.publish(msg)
            time.sleep(0.01) # 10ms
        
        time.sleep(1.0) # Aspettiamo che il robot processi
        msg.data = "Test deduplica: messaggio DIVERSO"
        self.get_logger().info("Invio messaggio finale unico.")
        self.publisher_.publish(msg)
        self.get_logger().info("Test completato.")

def main(args=None):
    rclpy.init(args=args)
    node = SpammerNode()
    node.run()
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
