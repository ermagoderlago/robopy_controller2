#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
import cv2
import numpy as np

class USBCameraNode(Node):
    def __init__(self):
        super().__init__('usb_camera_node')
        
        self.declare_parameter('device', 0)
        self.declare_parameter('width', 640)
        self.declare_parameter('height', 480)
        self.declare_parameter('fps', 30)
        
        self.image_pub = self.create_publisher(Image, 'camera/image_raw', 10)
        
        device = self.get_parameter('device').value
        width = self.get_parameter('width').value
        height = self.get_parameter('height').value
        fps = self.get_parameter('fps').value
        
        self.get_logger().info(f'Starting USB camera: device {device}, {width}x{height} @ {fps}fps')
        
        # Cerca una camera USB funzionante
        self.cap = None
        for dev in [device, 1, 2, 3]:
            self.cap = cv2.VideoCapture(dev)
            if self.cap.isOpened():
                self.get_logger().info(f'Found USB camera on /dev/video{dev}')
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                self.cap.set(cv2.CAP_PROP_FPS, fps)
                break
            else:
                self.get_logger().warn(f'No camera on /dev/video{dev}')
        
        if self.cap and self.cap.isOpened():
            self.timer = self.create_timer(1.0/fps, self.capture_frame)
        else:
            self.get_logger().error('No USB camera found!')
    
    def capture_frame(self):
        try:
            ret, frame = self.cap.read()
            if ret:
                # Converti BGR to RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
                # Crea messaggio ROS2
                msg = Image()
                msg.header.stamp = self.get_clock().now().to_msg()
                msg.header.frame_id = 'usb_camera'
                msg.height = frame_rgb.shape[0]
                msg.width = frame_rgb.shape[1]
                msg.encoding = 'rgb8'
                msg.step = 3 * frame_rgb.shape[1]  # 3 bytes per pixel
                msg.data = frame_rgb.tobytes()
                
                self.image_pub.publish(msg)
                self.get_logger().info('Published USB camera frame', throttle_duration_sec=5.0)
            else:
                self.get_logger().warn('Failed to capture frame from USB camera')
        except Exception as e:
            self.get_logger().error(f'USB camera error: {str(e)}')
    
    def destroy_node(self):
        if hasattr(self, 'cap') and self.cap:
            self.cap.release()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = USBCameraNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()