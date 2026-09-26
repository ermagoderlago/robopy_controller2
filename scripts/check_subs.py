import rclpy, time
from rclpy.node import Node
from geometry_msgs.msg import Twist
from robopy_controller.msg import AudioData

rclpy.init()
node = Node('diag_checker')
p_cmd = node.create_publisher(Twist, '/cmd_vel', 10)
p_spk = node.create_publisher(AudioData, '/respeaker/speaker_audio', 10)
p_chunk = node.create_publisher(AudioData, '/ai/conversation/audio_chunk', 10)

for i in range(15):
    rclpy.spin_once(node, timeout_sec=0.1)
    time.sleep(0.1)

print('cmd_vel subs:', p_cmd.get_subscription_count())
print('speaker_audio subs:', p_spk.get_subscription_count())
print('audio_chunk subs:', p_chunk.get_subscription_count())
node.destroy_node()
rclpy.shutdown()
