#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import Image, CameraInfo
from cv_bridge import CvBridge
from picamera2 import Picamera2
import time
import numpy as np

class UltimateCameraPublisher(Node):
    def __init__(self):
        super().__init__('ultimate_camera_publisher')
        
        qos = QoSProfile(reliability=QoSReliabilityPolicy.BEST_EFFORT, depth=3)
        self.publisher_image = self.create_publisher(Image, '/camera/image_raw', qos)
        self.publisher_info = self.create_publisher(CameraInfo, '/camera/camera_info', qos)
        self.bridge = CvBridge()

        # Parametri ottimizzati
        self.declare_parameter('width', 320)
        self.declare_parameter('height', 240)
        self.declare_parameter('fps', 2)  # Bilanciato tra fluidità e carico CPU
        self.declare_parameter('frame_id', 'camera_frame')
        self.declare_parameter('use_low_latency', True)

        width = self.get_parameter('width').value
        height = self.get_parameter('height').value
        fps = self.get_parameter('fps').value
        self.frame_id = self.get_parameter('frame_id').value
        low_latency = self.get_parameter('use_low_latency').value

        self.frame_count = 0

        self.get_logger().info(f"Starting Ultimate Camera: {width}x{height} @ {fps}fps")

        try:
            self.picam2 = Picamera2()
            
            if low_latency:
                # Configurazione a bassa latenza
                config = self.picam2.create_video_configuration(
                    main={"size": (width, height), "format": "RGB888"},
                    controls={
                        "FrameRate": fps,
                        "NoiseReductionMode": 2,  # Fast noise reduction
                        "AwbEnable": True,
                        "AeEnable": True,
                    },
                    queue=False,  # Critico per ridurre latenza
                    buffer_count=2  # Minimo numero di buffer
                )
            else:
                # Configurazione bilanciata
                config = self.picam2.create_preview_configuration(
                    main={"size": (width, height), "format": "RGB888"},
                    controls={"FrameRate": fps}
                )
            
            self.picam2.configure(config)
            
            # Pre-alloca un buffer per ridurre allocazioni
            self.frame_buffer = np.zeros((height, width, 3), dtype=np.uint8)
            
            self.picam2.start()
            time.sleep(2.5)  # Più tempo per warm-up (AWB, AE)

            self.timer = self.create_timer(1.0 / fps, self.timer_callback)
            self.get_logger().info("Ultimate camera started successfully")
            
        except Exception as e:
            self.get_logger().error(f"Camera init failed: {e}")
            raise

    def timer_callback(self):
        try:
            # Acquisisci frame
            array = self.picam2.capture_array()
            
            stamp = self.get_clock().now().to_msg()
            
            # Pubblica direttamente senza copie aggiuntive
            msg = self.bridge.cv2_to_imgmsg(array, "rgb8")
            msg.header.stamp = stamp
            msg.header.frame_id = self.frame_id
            self.publisher_image.publish(msg)
            
            # Pubblica camera_info meno frequentemente
            self.frame_count += 1
            if self.frame_count % 25 == 0:  # Solo ogni 25 frame
                cam_info = self.get_camera_info(stamp)
                self.publisher_info.publish(cam_info)
                
        except Exception as e:
            self.get_logger().error(f"Capture error: {e}")

    def get_camera_info(self, stamp):
        cam_info = CameraInfo()
        cam_info.header.stamp = stamp
        cam_info.header.frame_id = self.frame_id
        cam_info.width = self.get_parameter('width').value
        cam_info.height = self.get_parameter('height').value
        
        # Calibrazione base - considera di calibrarla per valori precisi
        fx = 500.0
        fy = 500.0
        cx = cam_info.width / 2
        cy = cam_info.height / 2
        
        cam_info.k = [fx, 0, cx, 0, fy, cy, 0, 0, 1]
        cam_info.d = [0, 0, 0, 0, 0]
        cam_info.distortion_model = "plumb_bob"
        
        return cam_info

    def destroy_node(self):
        if hasattr(self, 'picam2'):
            try:
                self.picam2.stop()
            except:
                pass
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = UltimateCameraPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()