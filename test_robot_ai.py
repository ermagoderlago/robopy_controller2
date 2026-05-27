#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import time
import threading

class RobotAITester(Node):
    def __init__(self):
        super().__init__('robot_ai_tester')
        self.response_sub = self.create_subscription(String, '/ai/conversation/response', self._response_callback, 10)
        self.text_pub = self.create_publisher(String, 'ai/input/text', 10)
        self.voice_pub = self.create_publisher(String, 'ai/input/voice_test', 10)
        
        self.responses = []
        self.response_event = threading.Event()
        
        self.questions = [
            # 5 Text Questions
            ("text", "Ciao Marcus, come stai oggi?"),
            ("text", "Cosa puoi dirmi del tuo hardware?"),
            ("text", "Qual è la tua missione principale?"),
            ("text", "Come ti senti riguardo al fatto di essere un robot?"),
            ("text", "Qual è il tuo sensore preferito?"),
            # 5 "Voice" Questions (tested via voice_test interface)
            ("voice", "Marcus, chi ti ha creato?"),
            ("voice", "Qual è la tua data di nascita?"),
            ("voice", "Puoi descrivere la stanza?"),
            ("voice", "Sei pronto per una missione?"),
            ("voice", "Dimmi una curiosità sulla robotica.")
        ]

    def _response_callback(self, msg):
        self.get_logger().info(f"Received Response: {msg.data}")
        self.responses.append(msg.data)
        self.response_event.set()

    def run_tests(self):
        self.get_logger().info("Starting Robot AI Node Verification...")
        time.sleep(2) # Wait for connections
        
        results = []
        
        for i, (q_type, q_text) in enumerate(self.questions):
            self.get_logger().info(f"--- Test {i+1}/10 ({q_type}) ---")
            self.get_logger().info(f"Question: {q_text}")
            
            self.response_event.clear()
            msg = String()
            msg.data = q_text
            
            if q_type == "text":
                self.text_pub.publish(msg)
            else:
                self.voice_pub.publish(msg)
            
            # Wait for response (long timeout for Gemini)
            if self.response_event.wait(timeout=30.0):
                results.append(True)
                self.get_logger().info(f"Success! Response received.")
            else:
                results.append(False)
                self.get_logger().error(f"Timeout waiting for response to: {q_text}")
            
            time.sleep(1) # Gap between questions
            
        self.get_logger().info("====================================")
        self.get_logger().info(f"Tests Completed: {sum(results)}/10 passed.")
        self.get_logger().info("====================================")
        
        if all(results):
            self.get_logger().info("ALL TESTS PASSED SUCCESSFULLY!")
        else:
            self.get_logger().error("SOME TESTS FAILED.")

def main(args=None):
    rclpy.init(args=args)
    tester = RobotAITester()
    
    # Run tests in a separate thread so spin() can handle callbacks
    test_thread = threading.Thread(target=tester.run_tests)
    test_thread.start()
    
    try:
        rclpy.spin(tester)
    except KeyboardInterrupt:
        pass
    finally:
        test_thread.join()
        tester.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
