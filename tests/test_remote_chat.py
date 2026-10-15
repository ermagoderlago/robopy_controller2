import sys
import time
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

class ChatTester(Node):
    def __init__(self, prompt):
        super().__init__('chat_tester')
        self.prompt = prompt
        self.pub = self.create_publisher(String, '/robopy/conversation_rx', 10)
        self.sub = self.create_subscription(String, '/ai/conversation/response', self.on_response, 10)
        self.received = False
        self.response_text = ""
        self.t0 = None
        self.latency = None

    def send(self):
        msg = String()
        msg.data = self.prompt
        self.t0 = time.perf_counter()
        self.pub.publish(msg)
        print(">>> [TEST] Sent prompt:", self.prompt)

    def on_response(self, msg):
        self.latency = time.perf_counter() - self.t0
        self.response_text = msg.data
        self.received = True
        print(f">>> [TEST] Received response in {self.latency*1000:.1f}ms: {self.response_text}")

def main():
    rclpy.init()
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Ciao Marcus, puoi parlare?"
    tester = ChatTester(prompt)
    
    # Wait for discovery
    start_discovery = time.time()
    while time.time() - start_discovery < 2.0:
        rclpy.spin_once(tester, timeout_sec=0.1)

    tester.send()
    
    wait_start = time.time()
    while not tester.received and (time.time() - wait_start < 15.0):
        rclpy.spin_once(tester, timeout_sec=0.1)

    if not tester.received:
        print(">>> [TEST] FAILED: No response received within 15s!")
        sys.exit(1)
    else:
        print(f">>> [TEST] SUCCESS! Response: '{tester.response_text}' (Latency: {tester.latency*1000:.1f}ms)")
        sys.exit(0)

if __name__ == '__main__':
    main()
