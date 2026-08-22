#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from message_filters import Subscriber, ApproximateTimeSynchronizer
from rclpy.qos import qos_profile_sensor_data

class SyncPublisher(Node):
    def __init__(self):
        super().__init__('sync_publisher')

        # Create synchronized subscribers
        self.rgb_sub = Subscriber(self, Image, '/rgb/image', qos_profile=qos_profile_sensor_data)
        #self.depth_sub = Subscriber(self, Image, '/rgb/depth', qos_profile=qos_profile_sensor_data)
        self.info_sub = Subscriber(self, CameraInfo, '/rgb/camera_info', qos_profile=qos_profile_sensor_data)

        self.sync = ApproximateTimeSynchronizer(
            [self.rgb_sub, self.depth_sub, self.info_sub],
            queue_size=10,
            slop=0.1  # tolleranza in secondi
        )
        self.sync.registerCallback(self.synced_callback)

        # Create publishers for RTAB-Map
        self.rgb_pub = self.create_publisher(Image, '/camera/rgb/image_rect_color', 10)
        #self.depth_pub = self.create_publisher(Image, '/camera/depth/image_rect_raw', 10)
        self.info_pub = self.create_publisher(CameraInfo, '/camera/rgb/camera_info', 10)

    def synced_callback(self, rgb_msg, depth_msg, info_msg):
        # Sincronizza header se serve
        stamp = rgb_msg.header.stamp
        rgb_msg.header.stamp = stamp
        #depth_msg.header.stamp = stamp
        info_msg.header.stamp = stamp

        self.rgb_pub.publish(rgb_msg)
        #self.depth_pub.publish(depth_msg)
        self.info_pub.publish(info_msg)

def main(args=None):
    rclpy.init(args=args)
    node = SyncPublisher()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
