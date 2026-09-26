import sys
import time
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

text = sys.argv[1] if len(sys.argv) > 1 else "Ciao! Sono Marcus."
rclpy.init()
node = Node('voice_trigger_node')
pub = node.create_publisher(String, '/ai/input/voice_test', 10)

for _ in range(10):
    rclpy.spin_once(node, timeout_sec=0.1)
    if pub.get_subscription_count() > 0:
        break
    time.sleep(0.1)

msg = String()
msg.data = text
pub.publish(msg)
print(f"Triggered voice_test with: '{text}' (Subs: {pub.get_subscription_count()})")

for _ in range(10):
    rclpy.spin_once(node, timeout_sec=0.1)
    time.sleep(0.1)

node.destroy_node()
rclpy.shutdown()
