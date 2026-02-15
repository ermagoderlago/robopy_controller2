import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import time
import sys

class TestNode(Node):
    def __init__(self):
        super().__init__('test_live_api')
        self.pub = self.create_publisher(String, '/ai/input/text', 10)
        self.sub = self.create_subscription(String, '/ai/conversation/response', self.callback, 10)
        self.queries = [
            "Chi sei?",
            "Che ore sono?",
            "Raccontami una barzelletta breve.",
            "Dove ti trovi?",
            "Analisi sistema.",
            "Cosa vedi?",
            "Ripeti 'ABC'.",
            "Capitale d'Italia?",
            "Colore del cielo?",
            "Bye bye."
        ]
        self.current_idx = 0
        self.waiting = False
        self.response_received = False
        self.responses = []
        
    def callback(self, msg):
        self.get_logger().info(f"Received response: {msg.data}")
        self.response_received = True
        self.responses.append(msg.data)

    def run_tests(self):
        time.sleep(5)  # Wait for node connection
        
        for q in self.queries:
            self.get_logger().info(f"Sending Q{self.current_idx+1}: {q}")
            self.pub.publish(String(data=q))
            
            # Wait for response (up to 40s - live api can be slow if connection setup)
            start_wait = time.time()
            self.response_received = False
            while not self.response_received:
                rclpy.spin_once(self, timeout_sec=0.1)
                if time.time() - start_wait > 40:
                    self.get_logger().error(f"Timeout waiting for response to: {q}")
                    break
            
            if self.response_received:
                self.get_logger().info(f"✅ Test {self.current_idx+1} PASSED")
            else:
                self.get_logger().error(f"❌ Test {self.current_idx+1} FAILED")
                
            self.current_idx += 1
            time.sleep(2) # Grace period

def main():
    rclpy.init()
    node = TestNode()
    try:
        node.run_tests()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
