#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import Header
import depthai as dai
import cv2
import numpy as np
from cv_bridge import CvBridge
import time

class OakDLiteNode(Node):
    def __init__(self):
        super().__init__('oak_d_lite_node')
        
        # Publishers per RTABMap
        self.rgb_pub = self.create_publisher(Image, '/oak_d/rgb', 10)
        self.depth_pub = self.create_publisher(Image, '/oak_d/depth', 10)
        self.camera_info_pub = self.create_publisher(CameraInfo, '/oak_d/camera_info', 10)
        
        self.bridge = CvBridge()
        self.device = None
        
        self.get_logger().info('OAK-D Lite Node inizializzato - Ricerca dispositivo...')

    def initialize_device(self):
        """Tenta di inizializzare il dispositivo con gestione errori"""
        try:
            self.get_logger().info('Ricerca dispositivi DepthAI...')
            available_devices = dai.Device.getAllAvailableDevices()
            self.get_logger().info(f'Dispositivi trovati: {len(available_devices)}')
            
            for device_info in available_devices:
                self.get_logger().info(f' - {device_info.getMxId()}')
            
            if len(available_devices) == 0:
                self.get_logger().error('Nessun dispositivo DepthAI trovato!')
                return False
                
            # Crea pipeline
            pipeline = dai.Pipeline()
            
            # Configura camera RGB semplificata
            cam_rgb = pipeline.create(dai.node.ColorCamera)
            cam_rgb.setPreviewSize(640, 400)
            cam_rgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
            cam_rgb.setInterleaved(False)
            cam_rgb.setColorOrder(dai.ColorCameraProperties.ColorOrder.RGB)
            
            # Configura stereo depth semplificata
            mono_left = pipeline.create(dai.node.MonoCamera)
            mono_right = pipeline.create(dai.node.MonoCamera)
            stereo = pipeline.create(dai.node.StereoDepth)
            
            mono_left.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
            mono_left.setBoardSocket(dai.CameraBoardSocket.LEFT)
            mono_right.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
            mono_right.setBoardSocket(dai.CameraBoardSocket.RIGHT)
            
            stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.HIGH_DENSITY)
            stereo.initialConfig.setMedianFilter(dai.MedianFilter.KERNEL_7x7)
            stereo.setLeftRightCheck(True)
            
            # Collegamenti
            mono_left.out.link(stereo.left)
            mono_right.out.link(stereo.right)
            
            # Output RGB
            xout_rgb = pipeline.create(dai.node.XLinkOut)
            xout_rgb.setStreamName("rgb")
            cam_rgb.preview.link(xout_rgb.input)
            
            # Output depth
            xout_depth = pipeline.create(dai.node.XLinkOut)
            xout_depth.setStreamName("depth")
            stereo.depth.link(xout_depth.input)
            
            # Prova a connettere al primo dispositivo disponibile
            self.device = dai.Device(pipeline)
            self.rgb_queue = self.device.getOutputQueue(name="rgb", maxSize=4, blocking=False)
            self.depth_queue = self.device.getOutputQueue(name="depth", maxSize=4, blocking=False)
            
            self.get_logger().info('OAK-D Lite connessa con successo!')
            return True
            
        except Exception as e:
            self.get_logger().error(f'Errore nella connessione: {str(e)}')
            self.device = None
            return False

    def publish_camera_info(self):
        """Pubblica le informazioni della camera per RTABMap"""
        if not hasattr(self, 'camera_info_pub'):
            return
            
        camera_info = CameraInfo()
        camera_info.header = Header()
        camera_info.header.stamp = self.get_clock().now().to_msg()
        camera_info.header.frame_id = "oak_d_camera"
        
        camera_info.width = 640
        camera_info.height = 400
        camera_info.distortion_model = "plumb_bob"
        camera_info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
        camera_info.k = [500.0, 0.0, 320.0, 0.0, 500.0, 200.0, 0.0, 0.0, 1.0]
        camera_info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        camera_info.p = [500.0, 0.0, 320.0, 0.0, 0.0, 500.0, 200.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        
        self.camera_info_pub.publish(camera_info)

    def run(self):
        """Loop principale con riconnessione automatica"""
        retry_count = 0
        max_retries = 10
        
        while rclpy.ok() and retry_count < max_retries:
            if self.device is None:
                self.get_logger().info(f'Tentativo di connessione {retry_count + 1}/{max_retries}')
                if self.initialize_device():
                    retry_count = 0  # Reset counter se connesso
                else:
                    retry_count += 1
                    if retry_count < max_retries:
                        self.get_logger().info('Attendo 5 secondi prima di ritentare...')
                        time.sleep(5)
                    continue
            
            try:
                # Ricevi e pubblica frame
                in_rgb = self.rgb_queue.tryGet()
                if in_rgb is not None:
                    frame_rgb = in_rgb.getCvFrame()
                    frame_rgb = cv2.cvtColor(frame_rgb, cv2.COLOR_BGR2RGB)
                    
                    ros_image = self.bridge.cv2_to_imgmsg(frame_rgb, "rgb8")
                    ros_image.header.stamp = self.get_clock().now().to_msg()
                    ros_image.header.frame_id = "oak_d_camera"
                    self.rgb_pub.publish(ros_image)
                
                in_depth = self.depth_queue.tryGet()
                if in_depth is not None:
                    frame_depth = in_depth.getFrame()
                    depth_uint16 = frame_depth.astype(np.uint16)
                    depth_image = self.bridge.cv2_to_imgmsg(depth_uint16, "16UC1")
                    depth_image.header.stamp = self.get_clock().now().to_msg()
                    depth_image.header.frame_id = "oak_d_camera"
                    self.depth_pub.publish(depth_image)
                
                self.publish_camera_info()
                rclpy.spin_once(self, timeout_sec=0.01)
                
            except Exception as e:
                self.get_logger().error(f'Errore durante l\'acquisizione: {str(e)}')
                self.device = None
                retry_count += 1
        
        if retry_count >= max_retries:
            self.get_logger().error('Numero massimo di tentativi di connessione raggiunto. Arresto del nodo.')

def main(args=None):
    rclpy.init(args=args)
    
    try:
        node = OakDLiteNode()
        node.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Errore nell'avvio del nodo: {e}")
    finally:
        rclpy.shutdown()

if __name__ == '__main__':
    main()