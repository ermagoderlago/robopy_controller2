#!/usr/bin/env python3
"""
OAK-D Hybrid Odometry - FULLY CORRECTED VERSION
SuperPoint VO + Optional YOLO with proper coordinate handling and thread-safety
All bugs fixed, optimized for RPi5
"""

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image, CameraInfo, CompressedImage
from geometry_msgs.msg import TransformStamped, Quaternion
from nav_msgs.msg import Odometry
from vision_msgs.msg import Detection2DArray, Detection2D, ObjectHypothesisWithPose
from std_msgs.msg import Float32
import tf2_ros
from cv_bridge import CvBridge
from scipy.spatial.transform import Rotation as R_scipy
import threading
from collections import deque

import depthai as dai
import cv2
import numpy as np
import os
import time


class OakHybridOdometry(Node):
    def __init__(self):
        super().__init__('oak_hybrid_odometry')
        self.get_logger().info("🚀 OAK-D Hybrid: SuperPoint VO + Optional YOLO (CORRECTED)")

        # ============================================================================
        # PARAMETERS
        # ============================================================================
        self.declare_parameters("", [
            ('superpoint_blob_path', '/home/pi/superpoint.blob'),
            ('yolo_blob_path', '/home/pi/yolo.blob'),
            ('enable_yolo', True),
            ('yolo_frequency', 2.0),
            ('yolo_conf_thresh', 0.5),
            ('yolo_iou_thresh', 0.5),
            ('publish_tf', False),
            ('filter_alpha', 0.25),
            ('min_features', 20),
            ('min_inliers', 8),
            ('min_depth', 0.3),
            ('max_depth', 8.0),
            ('enable_clahe', True),
            ('use_bruteforce', True),  # BF better for low-texture indoor
        ])

        # Read parameters
        self.sp_blob = self.get_parameter('superpoint_blob_path').value
        self.yolo_blob = self.get_parameter('yolo_blob_path').value
        self.enable_yolo = self.get_parameter('enable_yolo').value
        self.yolo_freq = self.get_parameter('yolo_frequency').value
        self.yolo_conf_thresh = self.get_parameter('yolo_conf_thresh').value
        self.yolo_iou_thresh = self.get_parameter('yolo_iou_thresh').value
        self.publish_tf = self.get_parameter('publish_tf').value
        self.filter_alpha = self.get_parameter('filter_alpha').value
        self.min_features = self.get_parameter('min_features').value
        self.min_inliers = self.get_parameter('min_inliers').value
        self.min_depth = self.get_parameter('min_depth').value
        self.max_depth = self.get_parameter('max_depth').value
        self.enable_clahe = self.get_parameter('enable_clahe').value
        self.use_bruteforce = self.get_parameter('use_bruteforce').value

        # Resolutions
        self.SP_W, self.SP_H = 480, 360          # SuperPoint input
        self.DEPTH_W, self.DEPTH_H = 640, 400    # Depth resolution (400p)
        self.YOLO_W, self.YOLO_H = 320, 320      # YOLO input

        # ============================================================================
        # THREAD-SAFE STATE
        # ============================================================================
        self.pose = np.eye(4)
        self.pose_lock = threading.Lock()
        
        # VO state
        self.last_kpts = None
        self.last_descs = None
        self.last_pts3d = None
        self.vo_state_lock = threading.Lock()
        
        # Tracking quality
        self.inliers_history = deque(maxlen=10)
        self.tracking_quality = 1.0
        
        # YOLO state
        self.yolo_last_time = 0
        self.yolo_lock = threading.Lock()
        
        # EMA filter state
        self.f_pos = None
        self.f_quat = None
        
        # Utils
        self.bridge = CvBridge()
        
        if self.enable_clahe:
            self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        else:
            self.clahe = None
        
        # Matcher (BruteForce better for indoor low-texture)
        if self.use_bruteforce:
            self.matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
            self.get_logger().info("✅ Using BruteForce matcher (optimal for low-texture)")
        else:
            FLANN_INDEX_KDTREE = 1
            index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=10)
            search_params = dict(checks=100)
            self.matcher = cv2.FlannBasedMatcher(index_params, search_params)
            self.get_logger().info("✅ Using FLANN matcher")

        # Depth configuration
        self.declare_parameter('depth_fps', 30.0)
        self.declare_parameter('depth_resolution', '400p')
        self.declare_parameter('depth_pub_width', 320)  # Risoluzione pubblicazione
        self.declare_parameter('depth_pub_height', 200)
        
        # Cache parametri depth per publish
        self.depth_pub_w = self.get_parameter('depth_pub_width').value
        self.depth_pub_h = self.get_parameter('depth_pub_height').value

        # ============================================================================
        # ROS PUBLISHERS
        # ============================================================================
        qos_best_effort = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        qos_reliable = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        
        self.pub_odom = self.create_publisher(Odometry, '/vo/odom', qos_reliable)
        self.pub_depth = self.create_publisher(Image, '/camera/depth/image_raw', qos_reliable)
        self.pub_debug = self.create_publisher(CompressedImage, '/vo/debug/compressed', qos_best_effort)
        self.pub_quality = self.create_publisher(Float32, '/vo/quality', 5)
        self.pub_camera_info = self.create_publisher(CameraInfo, '/camera/camera_info', 10)
        self.tf_br = tf2_ros.TransformBroadcaster(self)
        
        # RGB sempre attivo per RTAB-Map
        self.pub_rgb = self.create_publisher(Image, '/camera/rgb/image_raw', qos_reliable)
        
        if self.enable_yolo:
            self.pub_detections = self.create_publisher(Detection2DArray, '/yolo/detections', qos_reliable)
            self.get_logger().info("✅ YOLO enabled")
        else:
            self.pub_detections = None
            self.get_logger().info("⚪ YOLO disabled (RGB still published from mono left)")

        # ============================================================================
        # INITIALIZE DEPTHAI
        # ============================================================================
        if not self.setup_pipeline():
            self.get_logger().error("❌ Failed to setup DepthAI pipeline")
            raise RuntimeError("DepthAI pipeline setup failed")
        
        # Processing thread
        self.running = True
        self.processing_thread = threading.Thread(target=self.process_frames, daemon=True)
        self.processing_thread.start()
        
        # ROS timers (main thread)
        self.create_timer(0.05, self.publish_odometry_callback)  # 20Hz
        self.create_timer(1.0, self.publish_camera_info_callback)
        
        self.get_logger().info("✅ OAK-D Hybrid Odometry initialized")

    # ============================================================================
    # DEPTHAI PIPELINE SETUP
    # ============================================================================
    
    def setup_pipeline(self):
        """Configure DepthAI pipeline with all fixes"""
        try:
            pipeline = dai.Pipeline()
            
            # ========================================================================
            # 1. MONO CAMERAS (Stereo)
            # ========================================================================
            mono_left = pipeline.create(dai.node.MonoCamera)
            mono_right = pipeline.create(dai.node.MonoCamera)
            
            # Get depth config
            depth_fps = self.get_parameter('depth_fps').value
            depth_res_str = self.get_parameter('depth_resolution').value
            
            # Map resolution
            res_map = {
                '400p': dai.MonoCameraProperties.SensorResolution.THE_400_P,
                '480p': dai.MonoCameraProperties.SensorResolution.THE_400_P, # Alias
                '720p': dai.MonoCameraProperties.SensorResolution.THE_720_P,
                '800p': dai.MonoCameraProperties.SensorResolution.THE_800_P
            }
            mono_res = res_map.get(depth_res_str, dai.MonoCameraProperties.SensorResolution.THE_400_P)
            self.get_logger().info(f"Depth config: FPS={depth_fps}, Resolution={depth_res_str}")

            mono_left.setResolution(mono_res)
            mono_left.setFps(depth_fps)
            mono_right.setResolution(mono_res)
            mono_right.setFps(depth_fps)
            
            # ✅ FIX: Use CAM_B/CAM_C instead of deprecated LEFT/RIGHT
            mono_left.setBoardSocket(dai.CameraBoardSocket.CAM_B)   # LEFT camera
            mono_right.setBoardSocket(dai.CameraBoardSocket.CAM_C)  # RIGHT camera
            
            # ========================================================================
            # 2. STEREO DEPTH
            # ========================================================================
            stereo = pipeline.create(dai.node.StereoDepth)
            
            # ✅ FIX: HIGH_ACCURACY better for VO
            stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.HIGH_ACCURACY)
            stereo.setDepthAlign(dai.CameraBoardSocket.CAM_B)
            stereo.setLeftRightCheck(True)
            stereo.setSubpixel(False)
            stereo.setExtendedDisparity(False)
            stereo.initialConfig.setMedianFilter(dai.MedianFilter.KERNEL_7x7)
            
            # Depth range
            config = stereo.initialConfig.get()
            config.postProcessing.thresholdFilter.minRange = int(self.min_depth * 1000)  # m to mm
            config.postProcessing.thresholdFilter.maxRange = int(self.max_depth * 1000)
            stereo.initialConfig.set(config)
            
            mono_left.out.link(stereo.left)
            mono_right.out.link(stereo.right)
            
            # ========================================================================
            # 3. SUPERPOINT NEURAL NETWORK
            # ========================================================================
            self.has_sp = False
            if os.path.exists(self.sp_blob):
                # Resize to SuperPoint input size
                manip_sp = pipeline.create(dai.node.ImageManip)
                manip_sp.initialConfig.setResize(self.SP_W, self.SP_H)
                manip_sp.initialConfig.setFrameType(dai.RawImgFrame.Type.GRAY8)
                manip_sp.setKeepAspectRatio(False)
                stereo.rectifiedLeft.link(manip_sp.inputImage)
                
                nn_sp = pipeline.create(dai.node.NeuralNetwork)
                nn_sp.setBlobPath(self.sp_blob)
                nn_sp.setNumInferenceThreads(2)
                nn_sp.input.setBlocking(False)
                manip_sp.out.link(nn_sp.input)
                
                xout_sp = pipeline.create(dai.node.XLinkOut)
                xout_sp.setStreamName("superpoint")
                nn_sp.out.link(xout_sp.input)
                
                self.has_sp = True
                self.get_logger().info(f"✅ SuperPoint loaded: {self.sp_blob}")
            else:
                self.get_logger().error(f"❌ SuperPoint blob not found: {self.sp_blob}")
            
            # ========================================================================
            # 4. YOLO (OPTIONAL)
            # ========================================================================
            self.has_yolo = False
            if self.enable_yolo and os.path.exists(self.yolo_blob):
                # RGB Camera
                cam_rgb = pipeline.create(dai.node.ColorCamera)
                
                # ✅ FIX: Use CAM_A for RGB, not deprecated RGB
                cam_rgb.setBoardSocket(dai.CameraBoardSocket.CAM_A)
                cam_rgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
                cam_rgb.setFps(self.get_parameter('depth_fps').value)
                cam_rgb.setPreviewSize(self.YOLO_W, self.YOLO_H)
                cam_rgb.setInterleaved(False)
                cam_rgb.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
                
                # YOLO Detection Network
                yolo_nn = pipeline.create(dai.node.YoloDetectionNetwork)
                yolo_nn.setBlobPath(self.yolo_blob)
                yolo_nn.setConfidenceThreshold(self.yolo_conf_thresh)
                yolo_nn.setNumClasses(80)  # COCO dataset
                yolo_nn.setCoordinateSize(4)
                yolo_nn.setIouThreshold(self.yolo_iou_thresh)
                
                # ✅ FIX: Add anchors and anchor masks
                yolo_nn.setAnchors([10,14, 23,27, 37,58, 81,82, 135,169, 344,319])
                yolo_nn.setAnchorMasks({
                    "side26": [1, 2, 3],
                    "side13": [3, 4, 5]
                })
                
                cam_rgb.preview.link(yolo_nn.input)
                
                xout_yolo = pipeline.create(dai.node.XLinkOut)
                xout_yolo.setStreamName("yolo")
                yolo_nn.out.link(xout_yolo.input)
                
                # RGB output for visualization
                xout_rgb = pipeline.create(dai.node.XLinkOut)
                xout_rgb.setStreamName("rgb")
                cam_rgb.video.link(xout_rgb.input)
                
                self.has_yolo = True
                self.get_logger().info(f"✅ YOLO loaded: {self.yolo_blob}")
            
            # ========================================================================
            # 5. OUTPUT STREAMS
            # ========================================================================
            xout_rect = pipeline.create(dai.node.XLinkOut)
            xout_rect.setStreamName("rect_left")
            stereo.rectifiedLeft.link(xout_rect.input)
            
            xout_depth = pipeline.create(dai.node.XLinkOut)
            xout_depth.setStreamName("depth")
            stereo.depth.link(xout_depth.input)
            
            # ========================================================================
            # 6. START DEVICE
            # ========================================================================
            self.device = dai.Device(pipeline)
            
            # Get calibration
            calib = self.device.readCalibration()
            
            # Intrinsics for depth camera (LEFT, 640x400)
            M = calib.getCameraIntrinsics(dai.CameraBoardSocket.CAM_B, self.DEPTH_W, self.DEPTH_H)
            self.K_depth = np.array(M, dtype=np.float64)
            self.fx_depth, self.fy_depth = M[0][0], M[1][1]
            self.cx_depth, self.cy_depth = M[0][2], M[1][2]
            
            # Intrinsics for SuperPoint (scaled from depth)
            scale_w = self.SP_W / self.DEPTH_W
            scale_h = self.SP_H / self.DEPTH_H
            self.K_sp = self.K_depth.copy()
            self.K_sp[0, 0] *= scale_w  # fx
            self.K_sp[1, 1] *= scale_h  # fy
            self.K_sp[0, 2] *= scale_w  # cx
            self.K_sp[1, 2] *= scale_h  # cy
            
            self.get_logger().info(
                f"📷 Calibration loaded: "
                f"fx={self.fx_depth:.1f}, fy={self.fy_depth:.1f}, "
                f"cx={self.cx_depth:.1f}, cy={self.cy_depth:.1f}"
            )
            
            return True
            
        except Exception as e:
            self.get_logger().error(f"❌ DepthAI setup failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    # ============================================================================
    # PROCESSING THREAD
    # ============================================================================
    
    def process_frames(self):
        """Main processing thread - non-blocking"""
        try:
            # Setup queues
            q_rect = self.device.getOutputQueue("rect_left", maxSize=4, blocking=False)
            q_depth = self.device.getOutputQueue("depth", maxSize=4, blocking=False)
            
            q_sp = None
            if self.has_sp:
                q_sp = self.device.getOutputQueue("superpoint", maxSize=4, blocking=False)
            
            q_yolo = None
            q_rgb = None
            if self.has_yolo:
                q_yolo = self.device.getOutputQueue("yolo", maxSize=4, blocking=False)
                q_rgb = self.device.getOutputQueue("rgb", maxSize=4, blocking=False)
            
            self.get_logger().info("✅ Processing thread started")
            
            while self.running:
                try:
                    # ✅ FIX: Use tryGet() instead of get() to avoid blocking
                    rect_frame = q_rect.tryGet()
                    depth_frame = q_depth.tryGet()
                    
                    if rect_frame is None or depth_frame is None:
                        time.sleep(0.001)  # Small sleep to avoid busy loop
                        continue
                    
                    timestamp = self.get_clock().now().to_msg()
                    
                    # Process SuperPoint VO
                    if q_sp is not None:
                        sp_packet = q_sp.tryGet()
                        if sp_packet is not None:
                            self.process_vo(
                                sp_packet,
                                rect_frame.getCvFrame(),
                                depth_frame.getFrame(),
                                timestamp
                            )
                    
                    # Process YOLO (throttled)
                    if q_yolo is not None and q_rgb is not None:
                        current_time = time.time()
                        if current_time - self.yolo_last_time >= 1.0 / self.yolo_freq:
                            yolo_packet = q_yolo.tryGet()
                            rgb_packet = q_rgb.tryGet()
                            
                            if yolo_packet is not None and rgb_packet is not None:
                                self.process_yolo(yolo_packet, rgb_packet.getCvFrame(), timestamp)
                                self.yolo_last_time = current_time
                    else:
                        # YOLO disabilitato: pubblica mono left come RGB (convertito in BGR e resized)
                        try:
                            gray = rect_frame.getCvFrame()
                            rgb_frame = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                            # Resize alla stessa risoluzione della depth
                            rgb_resized = cv2.resize(rgb_frame, (self.depth_pub_w, self.depth_pub_h))
                            rgb_msg = self.bridge.cv2_to_imgmsg(rgb_resized, "bgr8")
                            rgb_msg.header.stamp = timestamp
                            rgb_msg.header.frame_id = "left_optical_frame"
                            self.pub_rgb.publish(rgb_msg)
                        except Exception as e:
                            self.get_logger().debug(f"RGB publish error: {e}")
                    
                    # Publish depth (always)
                    self.publish_depth(depth_frame.getFrame(), timestamp)
                    
                except Exception as e:
                    self.get_logger().error(
                        f"Frame processing error: {e}",
                        throttle_duration_sec=1.0
                    )
                    
        except Exception as e:
            self.get_logger().error(f"Processing thread crashed: {e}")
            import traceback
            traceback.print_exc()

    # ============================================================================
    # VISUAL ODOMETRY
    # ============================================================================
    
    def process_vo(self, sp_packet, gray_frame, depth_map, timestamp):
        """Process Visual Odometry with SuperPoint"""
        try:
            # 1. Decode SuperPoint
            kpts, descs = self.decode_superpoint(sp_packet)
            
            if kpts is None or len(kpts) < self.min_features:
                self.get_logger().debug(
                    f"Insufficient keypoints: {len(kpts) if kpts is not None else 0}",
                    throttle_duration_sec=2.0
                )
                return
            
            # 2. Preprocess image (CLAHE if enabled)
            if self.clahe is not None:
                gray_frame = self.clahe.apply(gray_frame)
            
            # 3. Backproject to 3D
            pts3d, valid_idx = self.backproject_points(kpts, depth_map)
            
            if len(pts3d) < self.min_features:
                self.get_logger().debug(
                    f"Insufficient 3D points: {len(pts3d)}",
                    throttle_duration_sec=2.0
                )
                # ✅ FIX: Still update state for next frame
                with self.vo_state_lock:
                    if len(pts3d) > 0:
                        self.last_kpts = kpts[valid_idx].copy()
                        self.last_descs = descs[valid_idx].copy()
                        self.last_pts3d = pts3d.copy()
                return
            
            # 4. Matching and PnP (only if not first frame)
            with self.vo_state_lock:
                if self.last_kpts is not None and \
                   self.last_descs is not None and \
                   len(self.last_descs) >= self.min_features:
                    
                    # Match descriptors
                    matches = self.matcher.knnMatch(descs[valid_idx], self.last_descs, k=2)
                    
                    # Lowe's ratio test
                    good_matches = []
                    for match_pair in matches:
                        if len(match_pair) == 2:
                            m, n = match_pair
                            if m.distance < 0.75 * n.distance:
                                good_matches.append(m)
                    
                    if len(good_matches) >= self.min_inliers:
                        # Extract correspondences
                        src_pts = self.last_pts3d[[m.trainIdx for m in good_matches]]
                        dst_kpts = kpts[valid_idx][[m.queryIdx for m in good_matches]]
                        
                        # Solve PnP RANSAC
                        success, rvec, tvec, inliers = cv2.solvePnPRansac(
                            src_pts, dst_kpts, self.K_sp, None,
                            flags=cv2.SOLVEPNP_EPNP,
                            reprojectionError=3.0,
                            confidence=0.99,
                            iterationsCount=100
                        )
                        
                        if success and inliers is not None and len(inliers) >= self.min_inliers:
                            # Update pose
                            self.update_pose(rvec, tvec, len(inliers), len(good_matches))
                            
                            # Update inliers history
                            self.inliers_history.append(len(inliers))
                            self.tracking_quality = len(inliers) / len(good_matches)
                
                # ✅ FIX: Update state ALWAYS (even first frame)
                self.last_kpts = kpts[valid_idx].copy()
                self.last_descs = descs[valid_idx].copy()
                self.last_pts3d = pts3d.copy()
            
            # 5. Publish debug image
            self.publish_debug_image(gray_frame, kpts[valid_idx], timestamp)
                
        except Exception as e:
            self.get_logger().error(f"VO processing error: {e}", throttle_duration_sec=1.0)
            import traceback
            traceback.print_exc()
    
    def decode_superpoint(self, packet):
        """
        Decode SuperPoint output
        Note: Format depends on specific blob - this is for standard SuperPoint
        """
        try:
            # ✅ DEBUG: Log layer info (uncomment for debugging)
            # layer_names = packet.getAllLayerNames()
            # self.get_logger().debug(f"SP layers: {layer_names}")
            
            # Try to get heatmap and descriptors as separate layers
            heatmap = None
            desc_map = None
            
            for layer_name in packet.getAllLayerNames():
                data = np.array(packet.getLayerFp16(layer_name), dtype=np.float16)
                
                # ✅ DEBUG: Uncomment to see layer shapes
                # self.get_logger().debug(f"Layer {layer_name}: shape={data.shape}, size={data.size}")
                
                # Heuristic: larger array is descriptors
                if data.size > 500000:  # Descriptors (256 x H x W)
                    desc_map = data
                elif data.size > 100000:  # Heatmap (65 x H x W)
                    heatmap = data
            
            if heatmap is None or desc_map is None:
                self.get_logger().warn("Could not decode SuperPoint output")
                return None, None
            
            # Reshape
            GRID_W = self.SP_W // 8  # 60
            GRID_H = self.SP_H // 8  # 45
            
            try:
                heatmap = heatmap.reshape(65, GRID_H, GRID_W)
                desc_map = desc_map.reshape(256, GRID_H, GRID_W)
            except:
                self.get_logger().warn(f"Reshape failed: heatmap={heatmap.shape}, desc={desc_map.shape}")
                return None, None
            
            # Remove dustbin channel
            heatmap = heatmap[:-1, :, :]
            
            # Reshape to full resolution
            prob_map = heatmap.transpose(1, 2, 0)\
                .reshape(GRID_H, GRID_W, 8, 8)\
                .transpose(0, 2, 1, 3)\
                .reshape(self.SP_H, self.SP_W).astype(np.float32)
            
            # Threshold
            mask = prob_map > 0.015
            if not np.any(mask):
                return None, None
            
            # NMS
            kernel = np.ones((4, 4), dtype=np.uint8)
            dilated = cv2.dilate(prob_map, kernel)
            peaks = (prob_map == dilated) & mask
            ys, xs = np.where(peaks)
            
            if len(xs) == 0:
                return None, None
            
            # Keypoints
            kpts = np.column_stack((xs, ys)).astype(np.float32)
            
            # Extract descriptors
            ix = np.clip(xs // 8, 0, GRID_W - 1)
            iy = np.clip(ys // 8, 0, GRID_H - 1)
            descs = desc_map[:, iy, ix].T
            
            # Normalize
            norm = np.linalg.norm(descs, axis=1, keepdims=True)
            norm = np.maximum(norm, 1e-6)
            descs = (descs / norm).astype(np.float32)
            
            return kpts, descs
            
        except Exception as e:
            self.get_logger().warn(f"SuperPoint decode error: {e}")
            import traceback
            traceback.print_exc()
            return None, None
    
    def backproject_points(self, kpts, depth_map):
        """Backproject 2D keypoints to 3D using depth map"""
        pts3d = []
        valid_idx = []
        
        # ✅ FIX: Check depth map type
        if depth_map.dtype == np.uint16:
            depth_scale = 1000.0  # mm to meters
        else:
            depth_scale = 1.0  # already in meters
        
        # Scale from SuperPoint coords (480x360) to Depth coords (640x400)
        scale_w = self.DEPTH_W / self.SP_W
        scale_h = self.DEPTH_H / self.SP_H
        
        for i, kp in enumerate(kpts):
            # Convert SP coordinates to Depth map coordinates
            u_depth = int(kp[0] * scale_w)
            v_depth = int(kp[1] * scale_h)
            
            # Check bounds
            if 0 <= u_depth < self.DEPTH_W and 0 <= v_depth < self.DEPTH_H:
                z = float(depth_map[v_depth, u_depth]) / depth_scale
                
                if self.min_depth < z < self.max_depth:
                    # Backproject using depth camera intrinsics
                    X = (u_depth - self.cx_depth) * z / self.fx_depth
                    Y = (v_depth - self.cy_depth) * z / self.fy_depth
                    
                    pts3d.append([X, Y, z])
                    valid_idx.append(i)
        
        return np.array(pts3d, dtype=np.float64), valid_idx
    
    def update_pose(self, rvec, tvec, num_inliers, num_matches):
        """Update global pose from PnP result"""
        with self.pose_lock:
            # Convert rvec, tvec to transformation matrix
            R, _ = cv2.Rodrigues(rvec)
            
            # Transformation from previous frame to current frame
            T_delta = np.eye(4)
            T_delta[:3, :3] = R
            T_delta[:3, 3] = tvec.flatten()
            
            # Invert to get camera motion
            T_delta = np.linalg.inv(T_delta)
            
            # Convert from optical frame to base_link
            R_opt_to_base = np.array([
                [0, 0, 1],
                [-1, 0, 0],
                [0, -1, 0]
            ])
            
            T_robot = np.eye(4)
            T_robot[:3, :3] = R_opt_to_base @ T_delta[:3, :3] @ R_opt_to_base.T
            T_robot[:3, 3] = R_opt_to_base @ T_delta[:3, 3]
            
            # Update global pose
            self.pose = self.pose @ T_robot
            
            self.get_logger().debug(
                f"Pose updated: inliers={num_inliers}/{num_matches}",
                throttle_duration_sec=1.0
            )

    # ============================================================================
    # YOLO PROCESSING
    # ============================================================================
    
    def process_yolo(self, yolo_packet, rgb_frame, timestamp):
        """Process YOLO detections"""
        try:
            detections = yolo_packet.detections
            
            msg = Detection2DArray()
            msg.header.stamp = timestamp
            msg.header.frame_id = "camera_color_optical_frame"
            
            for det in detections:
                if det.confidence < self.yolo_conf_thresh:
                    continue
                
                # Create Detection2D
                d2d = Detection2D()
                
                # ✅ FIX: YOLO coordinates are normalized [0,1]
                # Scale to rgb_frame resolution
                d2d.bbox.center.position.x = (det.xmin + det.xmax) / 2.0 * rgb_frame.shape[1]
                d2d.bbox.center.position.y = (det.ymin + det.ymax) / 2.0 * rgb_frame.shape[0]
                d2d.bbox.size_x = (det.xmax - det.xmin) * rgb_frame.shape[1]
                d2d.bbox.size_y = (det.ymax - det.ymin) * rgb_frame.shape[0]
                
                # Add hypothesis
                obj = ObjectHypothesisWithPose()
                obj.hypothesis.class_id = str(det.label)
                obj.hypothesis.score = float(det.confidence)
                d2d.results.append(obj)
                
                msg.detections.append(d2d)
            
            # Publish detections
            if self.pub_detections:
                self.pub_detections.publish(msg)
            
            # Publish RGB for visualization
            if self.pub_rgb:
                rgb_msg = self.bridge.cv2_to_imgmsg(rgb_frame, "bgr8")
                rgb_msg.header.stamp = timestamp
                rgb_msg.header.frame_id = "camera_color_optical_frame"
                self.pub_rgb.publish(rgb_msg)
            
        except Exception as e:
            self.get_logger().error(
                f"YOLO processing error: {e}",
                throttle_duration_sec=1.0
            )

    # ============================================================================
    # ROS PUBLISHERS (Main Thread)
    # ============================================================================
    
    def publish_odometry_callback(self):
        """Publish odometry from main ROS thread"""
        with self.pose_lock:
            current_pose = self.pose.copy()
        
        # Extract position and orientation
        position = current_pose[:3, 3]
        rotation = R_scipy.from_matrix(current_pose[:3, :3]).as_quat()
        
        # Apply EMA filter
        if self.f_pos is None:
            self.f_pos = position
            self.f_quat = rotation
        else:
            self.f_pos = self.filter_alpha * position + (1 - self.filter_alpha) * self.f_pos
            
            # Quaternion filter with flip handling
            if np.dot(self.f_quat, rotation) < 0:
                rotation = -rotation
            
            self.f_quat = self.filter_alpha * rotation + (1 - self.filter_alpha) * self.f_quat
            norm = np.linalg.norm(self.f_quat)
            if norm > 1e-6:
                self.f_quat /= norm
            else:
                self.f_quat = np.array([0., 0., 0., 1.]) # Fallback to identity
        
        # Create Odometry message
        odom = Odometry()
        odom.header.stamp = self.get_clock().now().to_msg()
        odom.header.frame_id = "odom"
        odom.child_frame_id = "base_link"
        
        odom.pose.pose.position.x = float(self.f_pos[0])
        odom.pose.pose.position.y = float(self.f_pos[1])
        odom.pose.pose.position.z = float(self.f_pos[2])
        odom.pose.pose.orientation = Quaternion(
            x=float(self.f_quat[0]),
            y=float(self.f_quat[1]),
            z=float(self.f_quat[2]),
            w=float(self.f_quat[3])
        )
        
        # ✅ FIX: Adaptive covariance based on tracking quality
        avg_inliers = np.mean(self.inliers_history) if self.inliers_history else 10
        scale = 1.0 / max(avg_inliers, 1.0)
        
        pos_cov = 0.01 + scale * 0.1
        z_cov = pos_cov * 3  # Z less reliable
        rp_cov = 0.1 + scale * 0.3
        yaw_cov = 0.05 + scale * 0.2
        
        odom.pose.covariance = [
            pos_cov, 0, 0, 0, 0, 0,
            0, pos_cov, 0, 0, 0, 0,
            0, 0, z_cov, 0, 0, 0,
            0, 0, 0, rp_cov, 0, 0,
            0, 0, 0, 0, rp_cov, 0,
            0, 0, 0, 0, 0, yaw_cov
        ]
        
        self.pub_odom.publish(odom)
        
        # Publish quality
        self.pub_quality.publish(Float32(data=float(self.tracking_quality)))
        
        # Publish TF if requested
        if self.publish_tf:
            t = TransformStamped()
            t.header = odom.header
            t.child_frame_id = "base_link"
            t.transform.translation.x = odom.pose.pose.position.x
            t.transform.translation.y = odom.pose.pose.position.y
            t.transform.translation.z = odom.pose.pose.position.z
            t.transform.rotation = odom.pose.pose.orientation
            self.tf_br.sendTransform(t)
    
    def publish_depth(self, depth_frame, timestamp):
        """Publish depth map (resized to depth_pub_width x depth_pub_height)"""
        try:
            # Resize depth per performance
            if depth_frame.shape[1] != self.depth_pub_w or depth_frame.shape[0] != self.depth_pub_h:
                depth_resized = cv2.resize(depth_frame, (self.depth_pub_w, self.depth_pub_h), 
                                           interpolation=cv2.INTER_NEAREST)
            else:
                depth_resized = depth_frame
            
            depth_msg = self.bridge.cv2_to_imgmsg(depth_resized, "16UC1")
            depth_msg.header.stamp = timestamp
            depth_msg.header.frame_id = "left_optical_frame"
            self.pub_depth.publish(depth_msg)
        except Exception as e:
            self.get_logger().error(
                f"Depth publish error: {e}",
                throttle_duration_sec=1.0
            )
    
    def publish_debug_image(self, gray_frame, kpts, timestamp):
        """Publish debug image with keypoints"""
        try:
            # Convert to BGR and draw keypoints
            debug_img = cv2.cvtColor(gray_frame, cv2.COLOR_GRAY2BGR)
            
            for kp in kpts:
                cv2.circle(debug_img, (int(kp[0]), int(kp[1])), 2, (0, 255, 0), -1)
            
            # Add text
            cv2.putText(
                debug_img,
                f"Features: {len(kpts)} | Quality: {self.tracking_quality:.2f}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 255),
                2
            )
            
            # Compress
            _, buffer = cv2.imencode('.jpg', debug_img, [cv2.IMWRITE_JPEG_QUALITY, 70])
            
            # Create message
            msg = CompressedImage()
            msg.header.stamp = timestamp
            msg.header.frame_id = "left_optical_frame"
            msg.format = "jpeg"
            msg.data = buffer.tobytes()
            
            self.pub_debug.publish(msg)
            
        except Exception as e:
            self.get_logger().debug(f"Debug image error: {e}")
    
    def publish_camera_info_callback(self):
        """Publish camera info (scaled for depth_pub_width x depth_pub_height)"""
        try:
            info = CameraInfo()
            info.header.stamp = self.get_clock().now().to_msg()
            info.header.frame_id = "left_optical_frame"
            info.width = self.depth_pub_w
            info.height = self.depth_pub_h
            
            # Scala gli intrinsechi per la risoluzione di pubblicazione
            scale_x = self.depth_pub_w / self.DEPTH_W
            scale_y = self.depth_pub_h / self.DEPTH_H
            fx = self.fx_depth * scale_x
            fy = self.fy_depth * scale_y
            cx = self.cx_depth * scale_x
            cy = self.cy_depth * scale_y
            
            info.k = [
                float(fx), 0.0, float(cx),
                0.0, float(fy), float(cy),
                0.0, 0.0, 1.0
            ]
            
            info.p = [
                float(fx), 0.0, float(cx), 0.0,
                0.0, float(fy), float(cy), 0.0,
                0.0, 0.0, 1.0, 0.0
            ]
            
            info.distortion_model = "plumb_bob"
            info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
            
            self.pub_camera_info.publish(info)
            
        except Exception as e:
            self.get_logger().error(f"Camera info error: {e}")

    # ============================================================================
    # CLEANUP
    # ============================================================================
    
    def destroy_node(self):
        """Clean shutdown"""
        self.get_logger().info("Shutting down OAK-D Hybrid Odometry...")
        
        # Stop processing thread
        self.running = False
        
        # Wait for thread to finish
        if hasattr(self, 'processing_thread'):
            self.processing_thread.join(timeout=2.0)
            if self.processing_thread.is_alive():
                self.get_logger().warn("Processing thread did not terminate cleanly")
        
        # Close DepthAI device
        if hasattr(self, 'device'):
            try:
                self.device.close()
            except:
                pass
        
        super().destroy_node()


# ============================================================================
# MAIN
# ============================================================================

def main(args=None):
    rclpy.init(args=args)
    
    node = None
    try:
        node = OakHybridOdometry()
        rclpy.spin(node)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"Node error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
