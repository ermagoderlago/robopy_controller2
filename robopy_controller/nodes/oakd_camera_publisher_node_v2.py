#!/usr/bin/env python3
# oakd_camera_publisher_node_v2.py - Nodo ROS2 DepthAI v2 completo

import os
import time
import threading
from typing import Optional

os.environ['RCUTILS_CONSOLE_OUTPUT_FORMAT'] = '[{severity} {time}] [{name}]: {message}'
os.environ['RCUTILS_LOGGING_SEVERITY'] = 'INFO'

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image, CameraInfo
from cv_bridge import CvBridge

import depthai as dai
import numpy as np
import cv2

class OakDCameraNodeV2(Node):
    def __init__(self):
        super().__init__('oakd_camera_node_v2')
        
        # Parametri configurazione
        self.declare_parameter('frame_width', 640)
        self.declare_parameter('frame_height', 480)
        self.declare_parameter('fps', 30.0)
        self.declare_parameter('optical_frame_id', 'camera_optical_frame')
        
        self.width = self.get_parameter('frame_width').value
        self.height = self.get_parameter('frame_height').value
        self.fps = self.get_parameter('fps').value
        self.optical_frame_id = self.get_parameter('optical_frame_id').value
        
        # Publishers
        qos_profile = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST
        )
        
        self.pub_rgb = self.create_publisher(Image, '/oakd/rgb/image_raw', qos_profile)
        self.pub_info = self.create_publisher(CameraInfo, '/oakd/rgb/camera_info', qos_profile)
        self.pub_depth = self.create_publisher(Image, '/oakd/depth/image_raw', qos_profile)
        self.pub_depth_info = self.create_publisher(CameraInfo, '/oakd/depth/camera_info', qos_profile)
        
        # Utilities
        self.bridge = CvBridge()
        
        # Inizializza info camera
        self._init_camera_info()
        
        # Variabili DepthAI
        self.pipeline = None
        self.device = None
        self.q_rgb = None
        self.q_depth = None
        self.running = False
        self.loop_thread = None
        
        self.get_logger().info(f'Starting OAK-D node v2: {self.width}x{self.height} @ {self.fps}FPS')
        
        # Costruisci e avvia pipeline
        if self._build_pipeline_v2():
            self.running = True
            self.loop_thread = threading.Thread(target=self._process_frames, daemon=True)
            self.loop_thread.start()
            self.get_logger().info('✅ Node started successfully')
        else:
            self.get_logger().error('❌ Failed to start pipeline')
    
    def _init_camera_info(self):
        """Inizializza i messaggi CameraInfo"""
        # RGB camera info
        self.camera_info = CameraInfo()
        self.camera_info.header.frame_id = self.optical_frame_id
        self.camera_info.width = self.width
        self.camera_info.height = self.height
        
        # Parametri intrinseci approssimati per OAK-D
        fx = fy = 0.8 * float(self.width)
        cx = float(self.width) / 2.0
        cy = float(self.height) / 2.0
        
        self.camera_info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        self.camera_info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        self.camera_info.distortion_model = 'plumb_bob'
        self.camera_info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
        self.camera_info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        
        # Depth camera info (simile a RGB)
        self.depth_info = CameraInfo()
        self.depth_info.header.frame_id = self.optical_frame_id
        self.depth_info.width = 640  # Risoluzione tipica depth
        self.depth_info.height = 400
        self.depth_info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        self.depth_info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        self.depth_info.distortion_model = 'plumb_bob'
        self.depth_info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
    
    def _build_pipeline_v2(self):
        """Costruisce la pipeline DepthAI v2"""
        try:
            self.get_logger().info('Building DepthAI v2 pipeline...')
            
            # Crea pipeline
            self.pipeline = dai.Pipeline()
            
            # ============================================
            # 1. RGB CAMERA (ColorCamera)
            # ============================================
            cam_rgb = self.pipeline.create(dai.node.ColorCamera)
            cam_rgb.setPreviewSize(self.width, self.height)
            cam_rgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
            cam_rgb.setInterleaved(False)
            cam_rgb.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
            cam_rgb.setFps(self.fps)
            
            # Crea output per RGB
            xout_rgb = self.pipeline.create(dai.node.XLinkOut)
            xout_rgb.setStreamName("rgb")
            cam_rgb.preview.link(xout_rgb.input)
            
            # ============================================
            # 2. DEPTH (Stereo)
            # ============================================
            # Left mono camera
            mono_left = self.pipeline.create(dai.node.MonoCamera)
            mono_left.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
            mono_left.setBoardSocket(dai.CameraBoardSocket.LEFT)
            mono_left.setFps(self.fps)
            
            # Right mono camera
            mono_right = self.pipeline.create(dai.node.MonoCamera)
            mono_right.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
            mono_right.setBoardSocket(dai.CameraBoardSocket.RIGHT)
            mono_right.setFps(self.fps)
            
            # Stereo depth
            stereo = self.pipeline.create(dai.node.StereoDepth)
            stereo.setConfidenceThreshold(200)
            stereo.setLeftRightCheck(True)
            stereo.setSubpixel(False)
            stereo.setExtendedDisparity(False)
            
            # Link mono cameras to stereo
            mono_left.out.link(stereo.left)
            mono_right.out.link(stereo.right)
            
            # Crea output per depth
            xout_depth = self.pipeline.create(dai.node.XLinkOut)
            xout_depth.setStreamName("depth")
            stereo.depth.link(xout_depth.input)
            
            # ============================================
            # Connetti al dispositivo
            # ============================================
            self.get_logger().info('Connecting to OAK-D device...')
            
            try:
                self.device = dai.Device(self.pipeline)
                self.get_logger().info(f'✅ Connected to device')
                
                # Ottieni le code
                self.q_rgb = self.device.getOutputQueue(name="rgb", maxSize=4, blocking=False)
                self.q_depth = self.device.getOutputQueue(name="depth", maxSize=4, blocking=False)
                
                return True
                
            except Exception as e:
                self.get_logger().error(f'Failed to connect to device: {e}')
                
                # Prova a vedere i dispositivi disponibili
                available_devices = dai.Device.getAllAvailableDevices()
                if available_devices:
                    self.get_logger().info(f'Available devices: {[d.getMxId() for d in available_devices]}')
                else:
                    self.get_logger().error('No devices found!')
                
                return False
            
        except Exception as e:
            self.get_logger().error(f'Pipeline build error: {e}')
            import traceback
            self.get_logger().error(traceback.format_exc())
            return False
    
    def _process_frames(self):
        """Processa i frame dalla camera"""
        self.get_logger().info('Starting frame processing...')
        
        frame_count = 0
        last_log_time = time.time()
        
        while self.running and rclpy.ok():
            try:
                # 1. Processa frame RGB
                if self.q_rgb is not None:
                    in_rgb = self.q_rgb.tryGet()
                    if in_rgb is not None:
                        frame = in_rgb.getCvFrame()
                        stamp = self.get_clock().now().to_msg()
                        
                        # Pubblica immagine RGB
                        img_msg = self.bridge.cv2_to_imgmsg(frame, encoding="bgr8")
                        img_msg.header.stamp = stamp
                        img_msg.header.frame_id = self.optical_frame_id
                        self.pub_rgb.publish(img_msg)
                        
                        # Pubblica camera info
                        self.camera_info.header.stamp = stamp
                        self.pub_info.publish(self.camera_info)
                        
                        frame_count += 1
                
                # 2. Processa depth
                if self.q_depth is not None:
                    in_depth = self.q_depth.tryGet()
                    if in_depth is not None:
                        depth_frame = in_depth.getFrame()
                        stamp = self.get_clock().now().to_msg()
                        
                        # Converti in metri e float32
                        depth_frame = depth_frame.astype(np.float32) / 1000.0  # mm -> m
                        
                        # Pubblica depth
                        depth_msg = self.bridge.cv2_to_imgmsg(depth_frame, encoding="32FC1")
                        depth_msg.header.stamp = stamp
                        depth_msg.header.frame_id = self.optical_frame_id
                        self.pub_depth.publish(depth_msg)
                        
                        # Pubblica depth info
                        self.depth_info.header.stamp = stamp
                        self.pub_depth_info.publish(self.depth_info)
                
                # Log ogni 2 secondi
                current_time = time.time()
                if current_time - last_log_time > 2.0:
                    self.get_logger().info(f'📊 Processed {frame_count} frames')
                    last_log_time = current_time
                    frame_count = 0
                
                # Piccola pausa
                time.sleep(0.001)
                
            except Exception as e:
                self.get_logger().error(f'Frame processing error: {e}')
                time.sleep(0.1)
        
        self.get_logger().info('Frame processing stopped')
    
    def destroy_node(self):
        """Clean shutdown"""
        self.get_logger().info('Shutting down node...')
        self.running = False
        
        if self.loop_thread:
            self.loop_thread.join(timeout=2.0)
        
        if self.device:
            self.device.close()
            self.get_logger().info('Device closed')
        
        super().destroy_node()

def main(args=None):
    """Main function per ROS2"""
    rclpy.init(args=args)
    
    try:
        node = OakDCameraNodeV2()
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info('Keyboard interrupt received')
    except Exception as e:
        node.get_logger().error(f'Node error: {e}')
    finally:
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()