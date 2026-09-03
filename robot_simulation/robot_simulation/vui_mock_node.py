#!/usr/bin/env python3
import os
import json
import urllib.request
import urllib.error

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

class VuiMockNode(Node):
    def __init__(self):
        super().__init__('vui_mock_node')
        
        self.declare_parameter('use_sim_time', True)
        
        # Subscriptions
        self.sub_ai = self.create_subscription(
            String,
            '/ai/input/text',
            self.input_callback,
            10
        )
        
        # Publisher
        self.pub_conv = self.create_publisher(
            String,
            '/robopy/conversation_tx',
            10
        )
        
        self.api_key = os.environ.get('GEMINI_API_KEY', '')
        self.api_url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.api_key}'
        
        self.system_prompt = "Sei Marcus, un robot mobile autonomo in simulazione Gazebo. Rispondi in italiano in modo sintetico e amichevole."
        
        self.get_logger().info('VUI Mock Node started.')

    def input_callback(self, msg):
        user_text = msg.data
        self.get_logger().info(f'Ricevuto input: {user_text}')
        
        payload = {
            "system_instruction": {
                "parts": [{"text": self.system_prompt}]
            },
            "contents": [
                {"parts": [{"text": user_text}]}
            ]
        }
        
        req = urllib.request.Request(
            self.api_url, 
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                result = json.loads(response.read().decode('utf-8'))
                reply_text = result['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            self.get_logger().error(f"Errore Gemini API: {e}")
            reply_text = f"Errore simulato di connessione. Hai detto: {user_text}"
            
        self.get_logger().info(f'Risposta Gemini: {reply_text}')
        
        reply_msg = String()
        reply_msg.data = reply_text
        self.pub_conv.publish(reply_msg)


def main(args=None):
    rclpy.init(args=args)
    node = VuiMockNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()
