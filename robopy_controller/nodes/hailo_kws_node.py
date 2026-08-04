#!/usr/bin/env python3
"""
Nodo ROS 2: hailo_kws_node
Esegue l'ascolto continuo della Wake Word ("Marcus") tramite Hailo-10H NPU.
Pubblica gli eventi di rilevamento sul topic ROS 2 `/hailo/wakeword_trigger`.
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Header, Float32

from robopy_controller.robot_ai.services.hailo_kws_service import HailoKWSService


class HailoKWSNode(Node):
    """
    Nodo ROS 2 per l'offloading del Keyword Spotting su Hailo-10H NPU.
    """

    def __init__(self):
        super().__init__('hailo_kws_node')
        
        self.declare_parameter('hef_path', '/mnt/ssd/robopy_controller_host/models/marcus_kws.hef')
        self.declare_parameter('threshold', 0.85)
        
        hef_path = self.get_parameter('hef_path').get_parameter_value().string_value
        threshold = self.get_parameter('threshold').get_parameter_value().double_value

        self.publisher_trigger = self.create_publisher(Header, '/hailo/wakeword_trigger', 10)
        self.publisher_confidence = self.create_publisher(Float32, '/hailo/wakeword_confidence', 10)

        self.get_logger().info("🔥 Inizializzazione hailo_kws_node (Offloading Wake Word NPU)...")
        
        self.kws_service = HailoKWSService(
            hef_path=hef_path,
            threshold=threshold,
            on_wakeword_cb=self._on_wakeword_detected
        )

    def _on_wakeword_detected(self, confidence: float):
        self.get_logger().info(f"🔥 [HailoKWS] Wake Word 'Marcus' rilevata! Confidenza: {confidence:.2%}")
        
        # Pubblica l'header di trigger
        hdr = Header()
        hdr.stamp = self.get_clock().now().to_msg()
        hdr.frame_id = "marcus_kws"
        self.publisher_trigger.publish(hdr)

        # Pubblica la confidenza
        msg_conf = Float32()
        msg_conf.data = float(confidence)
        self.publisher_confidence.publish(msg_conf)


def main(args=None):
    rclpy.init(args=args)
    node = HailoKWSNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
