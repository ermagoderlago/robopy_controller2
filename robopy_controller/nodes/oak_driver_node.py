import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy

# Custom messages
from robopy_controller.msg import OAKSyncFrame  # Requires rebuild
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from sensor_msgs.msg import Image, CameraInfo, Imu
from geometry_msgs.msg import Polygon, Point32

import depthai as dai
import numpy as np
import cv2
import time

# OAK Logic Classes
from robopy_controller.oak_logic.spatial_bucketing import bucket_keypoints
from robopy_controller.oak_logic.delta_encoder import DeltaEncoder
from robopy_controller.oak_logic.adaptive_roi import adaptive_depth_roi
from robopy_controller.oak_logic.motion_trigger import MotionTriggeredYOLO
from robopy_controller.oak_logic.sync_buffer import TemporalSyncBuffer
from robopy_controller.oak_logic.health_monitor import SystemHealthMonitor
from robopy_controller.oak_logic.rate_controller import AdaptiveRateController

class OAKDriverNode(Node):
    def __init__(self):
        super().__init__('oak_driver')
        
        # QoS Settings
        self.sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            durability=DurabilityPolicy.VOLATILE
        )
        
        self.diagnostics_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=5
        )
        
        # Publishers
        self.sync_pub = self.create_publisher(
            OAKSyncFrame,
            '/oak/sync_frame',
            self.sensor_qos
        )
        
        self.diag_pub = self.create_publisher(
            DiagnosticArray,
            '/oak/diagnostics',
            self.diagnostics_qos
        )
        
        # State
        self.running = True
        
        # Logic Modules
        self.delta_encoder = DeltaEncoder()
        self.motion_trigger = MotionTriggeredYOLO()
        self.sync_buffer = TemporalSyncBuffer(max_age_ms=50) # Strict sync
        self.health_monitor = SystemHealthMonitor()
        self.rate_controller = AdaptiveRateController()
        
        # Init Device
        self.pipeline = self.create_pipeline()
        self.device = dai.Device(self.pipeline)
        
        # Queues
        # setBlocking(False) is critical
        self.q_depth = self.device.getOutputQueue("depth", maxSize=1, blocking=False)
        self.q_conf = self.device.getOutputQueue("confidence", maxSize=1, blocking=False)
        # Note: Neural networks (YOLO, SuperPoint, Pose) decoupled to Hailo-10H NPU (SPEC-03, FM-VIS-001)
        self.q_imu = self.device.getOutputQueue("imu", maxSize=2, blocking=False)

        # Timers
        self.diag_timer = self.create_timer(1.0, self.publish_diagnostics)
        self.processing_timer = self.create_timer(0.001, self.process_loop) # Fast loop

        self.get_logger().info('OAK Driver Node initialized')

    def create_pipeline(self):
        pipeline = dai.Pipeline()
        
        # --- Config Params (Default) ---
        depth_conf = 220
        # TODO: update these via rate controller later.
        
        # --- CAMERAS ---
        monoLeft = pipeline.create(dai.node.MonoCamera)
        monoRight = pipeline.create(dai.node.MonoCamera)
        monoLeft.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
        monoRight.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
        monoLeft.setBoardSocket(dai.CameraBoardSocket.LEFT)
        monoRight.setBoardSocket(dai.CameraBoardSocket.RIGHT)
        monoLeft.setFps(30)
        monoRight.setFps(30)
        
        # RGB
        camRgb = pipeline.create(dai.node.ColorCamera)
        camRgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
        camRgb.setInterleaved(False)
        camRgb.setColorOrder(dai.ColorCameraProperties.ColorOrder.RGB)
        camRgb.setFps(30)
        camRgb.setIspScale(1, 3) # 1080p -> 360p
        camRgb.setPreviewSize(320, 320) # YOLO input
        
        # --- DEPTH ---
        stereo = pipeline.create(dai.node.StereoDepth)
        stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.HIGH_DENSITY)
        stereo.initialConfig.setMedianFilter(dai.MedianFilter.KERNEL_7x7)
        stereo.setLeftRightCheck(False)
        stereo.setExtendedDisparity(False)
        stereo.setSubpixel(False)
        stereo.initialConfig.setConfidenceThreshold(depth_conf)
        # stereo.setDepthAlign(dai.CameraBoardSocket.RGB) # Optional, disabled for perf as per doc
        
        monoLeft.out.link(stereo.left)
        monoRight.out.link(stereo.right)
        
        xoutDepth = pipeline.create(dai.node.XLinkOut)
        xoutDepth.setStreamName("depth")
        xoutDepth.input.setBlocking(False)
        xoutDepth.input.setQueueSize(1)
        stereo.depth.link(xoutDepth.input)
        
        xoutConf = pipeline.create(dai.node.XLinkOut)
        xoutConf.setStreamName("confidence")
        xoutConf.input.setBlocking(False)
        xoutConf.input.setQueueSize(1)
        stereo.confidenceMap.link(xoutConf.input)
        
        # --- IMU ---
        imu = pipeline.create(dai.node.IMU)
        imu.enableIMUSensor(dai.IMUSensor.ACCELEROMETER_RAW, 200)
        imu.enableIMUSensor(dai.IMUSensor.GYROSCOPE_RAW, 200)
        imu.setBatchReportThreshold(1)
        imu.setMaxBatchReports(10)
        
        xoutImu = pipeline.create(dai.node.XLinkOut)
        xoutImu.setStreamName("imu")
        xoutImu.input.setBlocking(False)
        xoutImu.input.setQueueSize(2)
        imu.out.link(xoutImu.input)

        return pipeline

    def process_loop(self):
        if not self.running: return

        # 1. READ ALL QUEUES (Non-blocking)
        
        # Depth
        depth_frame = None
        conf_frame = None
        ts_depth = None
        if self.q_depth.has() and self.q_conf.has():
            d_pkt = self.q_depth.get()
            c_pkt = self.q_conf.get()
            depth_frame = d_pkt.getCvFrame()
            conf_frame = c_pkt.getCvFrame()
            ts_depth = d_pkt.getTimestamp().total_seconds()
            
            # --- LOGIC: ADAPTIVE ROI ---
            d_roi, roi_coords = adaptive_depth_roi(depth_frame, conf_frame)
            
            if d_roi is None:
                # Fallback for bad depth -> Keep sync alive
                d_roi = depth_frame
                h, w = depth_frame.shape
                roi_coords = (0, 0, w, h)
                valid_ratio = 0.0
            else:
                valid_ratio = (d_roi > 0).sum() / d_roi.size
            
            # Add to sync buffer
            self.sync_buffer.add_depth(d_roi, roi_coords, valid_ratio, ts_depth)
        
        # IMU (Direct publish, no sync needed for frame usually, or sync separately)
        if self.q_imu.has():
            imu_pkt = self.q_imu.get()
            # Publish standard IMU msg immediately
            # ...
        
        # 2. ATTEMPT SYNC
        synced = self.sync_buffer.get_synced_frame()

        # DEBUG: Log status periodically
        if self.health_monitor.frame_count % 30 == 0:
             self.get_logger().info(f"Queues - Depth: {self.q_depth.has()}, IMU: {self.q_imu.has()}")
             if not synced:
                 t_depth = self.sync_buffer.depth_buf[-1]['ts'] if self.sync_buffer.depth_buf else 0
                 self.get_logger().warn(f"Sync pending. TS Depth: {t_depth:.3f}")
        
        if synced:
            # 3. HEALTH MONITOR
            self.health_monitor.update_metrics(synced, self.device.getChipTemperature())
            adj, mode = self.health_monitor.get_pipeline_adjustments()
            
            # 4. RATE CONTROLLER
            # Apply adjustments (e.g. set device config) if possible via dynamic reconfig or XLinkIn
            # For now just log
            
            # 5. PUBLISH MSG
            msg = OAKSyncFrame()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = "oak_rgb_camera_optical_frame"
            
            # Pack fields...
            # Note: converting numpy depth to image msg
            # msg.depth = ...
            
            self.sync_pub.publish(msg)

    def publish_diagnostics(self):
        diag_array = DiagnosticArray()
        diag_array.header.stamp = self.get_clock().now().to_msg()
        
        status = DiagnosticStatus()
        status.name = 'OAK-D Lite'
        status.hardware_id = 'oak_d_lite'
        
        mode = self.health_monitor.degradation_mode
        if mode == 'minimal':
            status.level = DiagnosticStatus.ERROR
        elif mode == 'degraded':
            status.level = DiagnosticStatus.WARN
        else:
            status.level = DiagnosticStatus.OK
            
        status.values = [
            KeyValue(key='degradation_mode', value=mode),
            KeyValue(key='temperature', value=f"{self.health_monitor.health_metrics['oak_temperature']:.1f}"),
            KeyValue(key='depth_valid', value=f"{self.health_monitor.health_metrics['depth_valid_ratio']:.2f}")
        ]
        
        diag_array.status.append(status)
        self.diag_pub.publish(diag_array)

def main(args=None):
    rclpy.init(args=args)
    node = OAKDriverNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
