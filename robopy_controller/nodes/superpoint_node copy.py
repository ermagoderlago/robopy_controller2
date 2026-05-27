#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Nodo ROS 2 per OAK-D Lite - SuperPoint + FLANN Matcher per RTAB-Map
# superpoint_node.py

import os
import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import Image, Imu, CameraInfo
from std_msgs.msg import Header
from geometry_msgs.msg import Point, PoseArray, Pose, TransformStamped
import yaml
from scipy.ndimage import maximum_filter
import math
from tf2_ros import TransformBroadcaster

try:
    from depthai_ros_msgs.msg import TrackedFeatures, TrackedFeature
    HAS_TRACKED_MSG = True
except Exception as e:
    print(f"depthai_ros_msgs non disponibile: {e}")
    HAS_TRACKED_MSG = False

try:
    import depthai as dai
    DEPTHAI_AVAILABLE = True
except ImportError:
    DEPTHAI_AVAILABLE = False
    print("DepthAI non disponibile - simulazione attiva")

from ament_index_python.packages import get_package_share_directory


class OakSuperPointRTABMap(Node):
    def __init__(self):
        super().__init__('oak_superpoint_rtabmap')
        
        # -------------------------------------
        # Parametri configurabili via ROS
        # -------------------------------------

        self.camera_frame = 'oak_mono_camera_frame'
        self.camera_optical_frame = 'oak_mono_camera_optical_frame'
        self.depth_frame = 'oak_depth_frame'
        self.imu_frame = 'oak_imu_frame'


        # Per stabilizzazione temporale
        self.depth_history = []
        self.history_length = 5
        self.current_min = 100  # Valori di default
        self.current_max = 5000
        self.smoothing_alpha = 0.3  # Fattore di smoothing

        self.declare_parameter('publish_depth_normalized', True) # ← Aggiungi questa linea
        self.declare_parameter('fps', 15)                         # FPS della camera
        self.declare_parameter('imu_rate', 50)                    # Frequenza IMU
        self.declare_parameter('superpoint_side', 'left')         # Camera per SuperPoint
        self.declare_parameter('superpoint_blob', '')             # Percorso al modello
        self.declare_parameter('depth_out_size', '320x200')       # Dimensioni depth output
        self.declare_parameter('mono_out_size', '320x200')        # Dimensioni immagine monocamera
        self.declare_parameter('publish_depth', True)             # Pubblica depth map
        self.declare_parameter('publish_mono', True)              # Pubblica immagine monocamera
        self.declare_parameter('publish_features', True)          # Pubblica features SuperPoint
        self.declare_parameter('publish_camera_info', True)       # Pubblica info camera
        self.declare_parameter('feature_threshold', 0.015)        # Soglia per keypoint
        self.declare_parameter('max_features', 400)               # Numero max di feature
        self.declare_parameter('descriptor_dim', 256)             # Dimensione descrittori SuperPoint
        self.declare_parameter('camera_info_file', '')            # File calibrazione
        self.declare_parameter('use_imu', True)                   # Usa IMU
        self.declare_parameter('use_rtabmap_format', True)        # Formato compatibile RTAB-Map
        
        # NUOVI PARAMETRI PER FLANN MATCHING
        self.declare_parameter('use_flann_matching', True)
        self.declare_parameter('flann_match_ratio', 0.7)
        self.declare_parameter('min_matches_for_tracking', 10)
        self.declare_parameter('publish_visual_odom', False)
        self.declare_parameter('publish_matches_visualization', True)
        
        # Lettura parametri
        self.fps = int(self.get_parameter('fps').value)
        self.sp_side = self.get_parameter('superpoint_side').value.lower()
        self.publish_depth = bool(self.get_parameter('publish_depth').value)
        self.publish_mono = bool(self.get_parameter('publish_mono').value)
        self.publish_features = bool(self.get_parameter('publish_features').value)
        self.publish_camera_info = bool(self.get_parameter('publish_camera_info').value)
        self.feature_threshold = float(self.get_parameter('feature_threshold').value)
        self.max_features = int(self.get_parameter('max_features').value)
        self.descriptor_dim = int(self.get_parameter('descriptor_dim').value)
        self.use_imu = bool(self.get_parameter('use_imu').value)
        self.use_rtabmap_format = bool(self.get_parameter('use_rtabmap_format').value)
        
        # Nuovi parametri FLANN
        self.use_flann_matching = bool(self.get_parameter('use_flann_matching').value)
        self.flann_match_ratio = float(self.get_parameter('flann_match_ratio').value)
        self.min_matches_for_tracking = int(self.get_parameter('min_matches_for_tracking').value)
        self.publish_visual_odom = bool(self.get_parameter('publish_visual_odom').value)
        self.publish_matches_visualization = bool(self.get_parameter('publish_matches_visualization').value)

        self.publish_depth_normalized = bool(self.get_parameter('publish_depth_normalized').value)

        # Parse dimensioni
        out_size = self.get_parameter('depth_out_size').value
        self.depth_w, self.depth_h = map(int, out_size.split('x'))
        mono_size = self.get_parameter('mono_out_size').value
        self.mono_w, self.mono_h = map(int, mono_size.split('x'))
        
        # Percorso blob SuperPoint
        blob_param = self.get_parameter('superpoint_blob').value
        if blob_param and os.path.isfile(blob_param):
            self.sp_blob = blob_param
        else:
            share_dir = get_package_share_directory('robopy_controller')
            candidate = os.path.join(share_dir, 'models', 'superpoint.blob')
            if os.path.isfile(candidate):
                self.sp_blob = candidate
                self.get_logger().info(f"Usando blob: {self.sp_blob}")
            else:
                self.get_logger().error(f"Blob SuperPoint non trovato: {candidate}")
                self.sp_blob = None
                self.publish_features = False
        
        # Info camera
        self.camera_info = None
        camera_info_file = self.get_parameter('camera_info_file').value
        if camera_info_file and os.path.isfile(camera_info_file):
            self.load_camera_info(camera_info_file)
        else:
            self.generate_default_camera_info()
        
        if self.publish_depth_normalized:
            self.pub_depth_norm = self.create_publisher(
                Image, '/depth/image_normalized', 10
            )
            self.get_logger().info("Publisher depth normalizzata attivato")
        
        # -------------------------------------
        # Publisher ROS
        # -------------------------------------
        
        # Depth map
        if self.publish_depth:
            self.pub_depth = self.create_publisher(
                Image, '/depth/image_raw', 10
            )
            self.get_logger().info(f"Publisher depth attivato: {self.depth_w}x{self.depth_h}")
        
        # Immagine monocamera
        if self.publish_mono:
            self.pub_mono = self.create_publisher(
                Image, '/camera/image_raw', 10
            )
            self.get_logger().info(f"Publisher mono attivato: {self.mono_w}x{self.mono_h}")
        
        # Camera info
        if self.publish_camera_info and self.camera_info:
            self.pub_camera_info = self.create_publisher(
                CameraInfo, '/camera/camera_info', 10
            )
            self.get_logger().info("Publisher camera_info attivato")
        
        # Features SuperPoint
        if self.publish_features and HAS_TRACKED_MSG:
            self.pub_features = self.create_publisher(
                TrackedFeatures, '/superpoint/features', 10
            )
            self.get_logger().info("Publisher features SuperPoint attivato")
        
        # Features in formato RTAB-Map (opzionale)
        if self.use_rtabmap_format and self.publish_features:
            try:
                from rtabmap_msgs.msg import Feature
                self.pub_features_rtabmap = self.create_publisher(
                    Feature, '/rtabmap/features', 10
                )
                self.get_logger().info("Publisher features RTAB-Map attivato")
            except ImportError:
                self.get_logger().warn("rtabmap_msgs non disponibile - formato RTAB-Map disabilitato")
                self.use_rtabmap_format = False
        
        # IMU
        if self.use_imu:
            self.pub_imu = self.create_publisher(Imu, '/imu/raw', 100)
        
        # -------------------------------------
        # FLANN MATCHER SETUP
        # -------------------------------------
        if self.use_flann_matching:
            # Inizializzazione FLANN matcher
            FLANN_INDEX_KDTREE = 1
            index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
            search_params = dict(checks=50)
            self.flann = cv2.FlannBasedMatcher(index_params, search_params)
            
            # Publisher per matches visualization
            if self.publish_matches_visualization:
                self.pub_matches_viz = self.create_publisher(
                    PoseArray, '/flann/matches_viz', 10
                )
                self.get_logger().info("Publisher matches visualization attivato")
            
            # Publisher per odometria visuale (opzionale)
            if self.publish_visual_odom:
                self.tf_broadcaster = TransformBroadcaster(self)
                self.get_logger().info("TF broadcaster per odometria visuale attivato")
            
            # Buffer per feature precedenti
            self.prev_keypoints = None
            self.prev_descriptors = None
            self.prev_frame_id = 0
            self.accumulated_transform = np.eye(4)  # Matrice di trasformazione accumulata
            
            self.get_logger().info("FLANN matcher inizializzato")
        
        # Variabili di stato
        self.latest_keypoints = None
        self.latest_descriptors = None
        self.latest_scores = None
        self.frame_counter = 0
        self.sp_debug_done = False
        
        # Pipeline DepthAI
        if DEPTHAI_AVAILABLE:
            self.setup_depthai_pipeline()
        else:
            self.get_logger().warn("DepthAI non disponibile - modalità simulazione")
            self.setup_simulation()
        
        self.main_timer = self.create_timer(1.0 / self.fps, self.main_callback)
        
        # Buffer per tenere l'ultimo pacchetto di ogni tipo
        self.latest_mono_packet = None
        self.latest_depth_packet = None
        self.latest_sp_packet = None
        
        self.get_logger().info(f"Nodo SuperPoint per RTAB-Map avviato a {self.fps} FPS")
    
    def setup_depthai_pipeline(self):
        """Configura pipeline DepthAI"""
        try:
            pipeline = dai.Pipeline()
            
            # -------------------------------
            # Mono cameras
            # -------------------------------
            monoL = pipeline.create(dai.node.MonoCamera)
            monoR = pipeline.create(dai.node.MonoCamera)
            monoL.setBoardSocket(dai.CameraBoardSocket.LEFT)
            monoR.setBoardSocket(dai.CameraBoardSocket.RIGHT)
            monoL.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
            monoR.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
            monoL.setFps(self.fps)
            monoR.setFps(self.fps)
            
            # -------------------------------
            # StereoDepth
            # -------------------------------
            stereo = pipeline.create(dai.node.StereoDepth)
            #stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.HIGH_DENSITY)
            stereo.setConfidenceThreshold(200)
            #stereo.setLeftRightCheck(False)
            #stereo.setSubpixel(False)
            #stereo.setExtendedDisparity(False)
            monoL.out.link(stereo.left)
            monoR.out.link(stereo.right)

            stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.HIGH_ACCURACY)
            stereo.setLeftRightCheck(True)  # Abilita check sinistra-destra
            stereo.setSubpixel(True)        # Migliore precisione
            stereo.setExtendedDisparity(False)
            stereo.setRectifyEdgeFillColor(0)  # Nero per bordi
            
            #post processing
            stereo.initialConfig.setMedianFilter(dai.MedianFilter.KERNEL_7x7)
            stereo.setDepthAlign(dai.CameraBoardSocket.LEFT)  # Allinea alla camera sinistra
            stereo.setConfidenceThreshold(200)
            
            # -------------------------------
            # Depth output (resize on-device)
            # -------------------------------
            if self.publish_depth:
                manipDepth = pipeline.create(dai.node.ImageManip)
                manipDepth.initialConfig.setResize(self.depth_w, self.depth_h)
                manipDepth.setMaxOutputFrameSize(self.depth_w * self.depth_h * 2)
                stereo.depth.link(manipDepth.inputImage)
                
                xoutDepth = pipeline.create(dai.node.XLinkOut)
                xoutDepth.setStreamName('depth')
                manipDepth.out.link(xoutDepth.input)
            
            # -------------------------------
            # Monocamera output per SuperPoint
            # -------------------------------
            sp_camera = monoL if self.sp_side == 'left' else monoR
            
            # Output immagine monocamera (full resolution per RTAB-Map)
            if self.publish_mono:
                manipMono = pipeline.create(dai.node.ImageManip)
                manipMono.initialConfig.setResize(self.mono_w, self.mono_h)
                sp_camera.out.link(manipMono.inputImage)
                
                xoutMono = pipeline.create(dai.node.XLinkOut)
                xoutMono.setStreamName('mono')
                manipMono.out.link(xoutMono.input)
            
            # -------------------------------
            # SuperPoint Neural Network
            # -------------------------------
            if self.publish_features and self.sp_blob:
                manipSP = pipeline.create(dai.node.ImageManip)
                manipSP.initialConfig.setResize(320, 200)  # Dimensione fissa per SuperPoint
                sp_camera.out.link(manipSP.inputImage)
                
                nnSP = pipeline.create(dai.node.NeuralNetwork)
                nnSP.setBlobPath(self.sp_blob)
                manipSP.out.link(nnSP.input)
                
                xoutSP = pipeline.create(dai.node.XLinkOut)
                xoutSP.setStreamName('sp_out')
                nnSP.out.link(xoutSP.input)
            
            # -------------------------------
            # IMU
            # -------------------------------
            if self.use_imu:
                imu = pipeline.create(dai.node.IMU)
                try:
                    imu.enableIMUSensor([
                        dai.IMUSensor.ACCELEROMETER_RAW,
                        dai.IMUSensor.GYROSCOPE_RAW
                    ], 100)  # 100Hz
                    imu.setBatchReportThreshold(1)
                    imu.setMaxBatchReports(10)
                    
                    xoutImu = pipeline.create(dai.node.XLinkOut)
                    xoutImu.setStreamName('imu')
                    imu.out.link(xoutImu.input)
                except Exception as e:
                    self.get_logger().warn(f"IMU non configurable: {e}")
                    self.use_imu = False
            
            # -------------------------------
            # Device e code
            # -------------------------------
            self.device = dai.Device(pipeline)
            
            # Code di output
            if self.publish_depth:
                self.q_depth = self.device.getOutputQueue('depth', 8, False)
            if self.publish_mono:
                self.q_mono = self.device.getOutputQueue('mono', 8, False)
            if self.publish_features and self.sp_blob:
                self.q_sp = self.device.getOutputQueue('sp_out', 8, False)
            if self.use_imu:
                self.q_imu = self.device.getOutputQueue('imu', 50, False)
            
            self.get_logger().info("Pipeline DepthAI configurata con successo")
            
        except Exception as e:
            self.get_logger().error(f"Errore configurazione DepthAI: {e}")
            raise
    
    def setup_simulation(self):
        """Setup per modalità simulazione (senza hardware)"""
        self.get_logger().warn("Modalità simulazione - dati fittizi")
        self.q_depth = None
        self.q_mono = None
        self.q_sp = None
        self.q_imu = None
    
    def load_camera_info(self, filepath):
        """Carica info camera da file YAML"""
        try:
            with open(filepath, 'r') as f:
                calib_data = yaml.safe_load(f)
            
            if 'camera_matrix' in calib_data and 'distortion_coefficients' in calib_data:
                self.camera_info = CameraInfo()
                self.camera_info.width = calib_data.get('image_width', self.mono_w)
                self.camera_info.height = calib_data.get('image_height', self.mono_h)

                self.camera_info.header.frame_id = self.camera_optical_frame  # ← IMPORTANTE!
                
                # Matrice camera
                cam_matrix = calib_data['camera_matrix']['data']
                self.camera_info.k = cam_matrix
                
                # Matrice di distorsione
                dist_coeffs = calib_data['distortion_coefficients']['data']
                self.camera_info.d = dist_coeffs
                
                # Matrice di proiezione (3x4)
                if 'projection_matrix' in calib_data:
                    proj_matrix = calib_data['projection_matrix']['data']
                    self.camera_info.p = proj_matrix
                else:
                    # Costruisci matrice di proiezione di default
                    self.camera_info.p = [
                        cam_matrix[0], cam_matrix[1], cam_matrix[2], 0,
                        cam_matrix[3], cam_matrix[4], cam_matrix[5], 0,
                        cam_matrix[6], cam_matrix[7], cam_matrix[8], 0
                    ]
                
                # Matrice di rotazione (identity)
                self.camera_info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
                
                self.camera_info.distortion_model = calib_data.get('distortion_model', 'plumb_bob')
                self.get_logger().info(f"Info camera caricate da {filepath}")
            else:
                self.generate_default_camera_info()
                
        except Exception as e:
            self.get_logger().error(f"Errore caricamento info camera: {e}")
            self.generate_default_camera_info()
    
    def generate_default_camera_info(self):
        """Genera info camera di default"""
        self.camera_info = CameraInfo()
        self.camera_info.width = self.mono_w
        self.camera_info.height = self.mono_h

        self.camera_info.header.frame_id = self.camera_optical_frame  # ← IMPORTANTE!
        
        # Matrice intrinseca approssimativa per OAK-D Lite
        fx = fy = self.mono_w * 0.9  # Lunghezza focale approssimativa
        cx = self.mono_w / 2.0
        cy = self.mono_h / 2.0
        
        self.camera_info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        self.camera_info.d = [0.0, 0.0, 0.0, 0.0, 0.0]  # No distorsione
        
        # Matrice di proiezione
        self.camera_info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        
        # Matrice di rotazione (identity)
        self.camera_info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        
        self.camera_info.distortion_model = 'plumb_bob'
        self.get_logger().info("Info camera di default generate")

    def extract_superpoint_features(self, nndata):
        """Estrai keypoint e descrittori dai dati SuperPoint (oggetto NNData)"""
        try:
            # Estrai i dati dai layer dell'oggetto NNData
            semi_data = None
            desc_data = None
            
            for name in nndata.getAllLayerNames():
                if name == 'semi':
                    try:
                        semi_data = np.array(nndata.getLayerFp16('semi'))
                    except:
                        try:
                            semi_data = np.array(nndata.getLayerFp32('semi'))
                        except:
                            continue
                elif name == 'desc':
                    try:
                        desc_data = np.array(nndata.getLayerFp16('desc'))
                    except:
                        try:
                            desc_data = np.array(nndata.getLayerFp32('desc'))
                        except:
                            continue
            
            if semi_data is None:
                self.get_logger().warn("Nessun dato 'semi' trovato nel pacchetto NNData")
                return None, None, None
            
            # Ora semi_data è un array numpy, possiamo procedere
            # Formato: (65, H, W) per semi, (256, H, W) per desc
            # Per input 320x200: H=25, W=40
            
            # Controlla dimensione semi
            if semi_data.size != 65000:  # 65 * 25 * 40
                self.get_logger().warn(f"Dimensione semi inaspettata: {semi_data.size}")
                return None, None, None
            
            # Reshape semi
            semi_reshaped = semi_data.reshape(65, 25, 40)
            
            # Estrai heatmap (primi 64 canali)
            heatmap_channels = semi_reshaped[:64, :, :]
            
            # Softmax lungo i canali
            max_vals = np.max(heatmap_channels, axis=0, keepdims=True)
            exp_vals = np.exp(heatmap_channels - max_vals)
            heatmap = exp_vals / np.sum(exp_vals, axis=0, keepdims=True)
            
            # Ricostruisci heatmap ad alta risoluzione (200x320)
            heatmap_full = np.zeros((200, 320))
            for c in range(64):
                i = c % 8
                j = c // 8
                heatmap_full[j::8, i::8] = heatmap[c, :, :]
            
            # Trova massimi locali
            local_max = maximum_filter(heatmap_full, size=3) == heatmap_full
            candidate_points = np.where(local_max & (heatmap_full > self.feature_threshold))
            
            if len(candidate_points[0]) == 0:
                return np.array([]), np.array([]), desc_data
            
            # Ordina per score e prendi i migliori
            candidate_scores = heatmap_full[candidate_points]
            sorted_indices = np.argsort(candidate_scores)[::-1]  # Decrescente
            
            # Limita numero di feature
            n_features = min(self.max_features, len(sorted_indices))
            selected_indices = sorted_indices[:n_features]
            
            # Estrai coordinate
            y_coords = candidate_points[0][selected_indices]
            x_coords = candidate_points[1][selected_indices]
            scores_selected = candidate_scores[selected_indices]
            
            keypoints = np.column_stack([x_coords, y_coords])
            
            self.get_logger().debug(f"Keypoint estratti: {len(keypoints)}")
            return keypoints, scores_selected, desc_data
            
        except Exception as e:
            self.get_logger().error(f"Errore estrazione feature: {e}")
            return None, None, None

    def match_with_flann(self, desc_prev, desc_curr):
        """Matching con FLANN + Lowe's ratio test"""
        if len(desc_prev) < 2 or len(desc_curr) < 2:
            return []
        
        matches = self.flann.knnMatch(desc_prev, desc_curr, k=2)
        
        # Applica Lowe's ratio test
        good_matches = []
        
        for m, n in matches:
            if m.distance < self.flann_match_ratio * n.distance:
                good_matches.append(m)
        
        return good_matches
    
    def estimate_motion_from_matches(self, matches, prev_kpts, curr_kpts, depth_frame=None):
        """Stima movimento dai matches di feature"""
        if len(matches) < self.min_matches_for_tracking:
            return None
        
        # Estrai punti corrispondenti
        src_pts = np.float32([prev_kpts[m.queryIdx] for m in matches])
        dst_pts = np.float32([curr_kpts[m.trainIdx] for m in matches])
        
        try:
            # Stima matrice fondamentale con RANSAC
            F, mask = cv2.findFundamentalMat(src_pts, dst_pts, cv2.FM_RANSAC, 3.0, 0.99)
            
            # Usa solo inliers
            inlier_mask = mask.ravel() == 1
            src_inliers = src_pts[inlier_mask]
            dst_inliers = dst_pts[inlier_mask]
            
            if len(src_inliers) < self.min_matches_for_tracking:
                return None
            
            # Se abbiamo depth, possiamo stimare movimento 3D
            if depth_frame is not None and len(src_inliers) > 0:
                # Per semplicità, restituiamo la media dello spostamento 2D
                motion_2d = np.mean(dst_inliers - src_inliers, axis=0)
                return {
                    'translation': [motion_2d[0], motion_2d[1], 0.0],
                    'rotation': [0.0, 0.0, 0.0],  # Non stimiamo rotazione per ora
                    'num_inliers': len(src_inliers),
                    'avg_displacement': np.linalg.norm(motion_2d)
                }
            else:
                # Solo movimento 2D
                motion_2d = np.mean(dst_inliers - src_inliers, axis=0)
                return {
                    'translation': [motion_2d[0], motion_2d[1], 0.0],
                    'rotation': [0.0, 0.0, 0.0],
                    'num_inliers': len(src_inliers),
                    'avg_displacement': np.linalg.norm(motion_2d)
                }
                
        except Exception as e:
            self.get_logger().warn(f"Errore stima movimento: {e}")
            return None
    
    def publish_matches_visualization(self, matches, prev_kpts, curr_kpts, timestamp):
        """Pubblica matches come PoseArray per visualizzazione"""
        if not self.publish_matches_visualization or len(matches) == 0:
            return
        
        poses_msg = PoseArray()
        poses_msg.header = Header(
            stamp=timestamp.to_msg(),
            frame_id=self.camera_optical_frame
        )
        
        for match in matches:
            # Crea una "freccia" virtuale per il match
            pose = Pose()
            
            # Punto inizio (frame precedente)
            x0, y0 = prev_kpts[match.queryIdx]
            # Punto fine (frame corrente)
            x1, y1 = curr_kpts[match.trainIdx]
            
            # Calcola offset (normalizzato)
            dx = (x1 - x0) / 320.0
            dy = (y1 - y0) / 200.0
            
            # Usa la posizione media come posizione della freccia
            pose.position.x = (x0 + x1) / 2.0
            pose.position.y = (y0 + y1) / 2.0
            pose.position.z = 0.0  # Z = 0 per visualizzazione 2D
            
            # Orientamento della freccia (direzione del movimento)
            angle = math.atan2(dy, dx)
            
            # Converti a quaternione
            pose.orientation.z = math.sin(angle / 2.0)
            pose.orientation.w = math.cos(angle / 2.0)
            
            poses_msg.poses.append(pose)
        
        self.pub_matches_viz.publish(poses_msg)
    
    def publish_visual_odometry(self, motion, timestamp):
        """Pubblica odometria visuale come TF"""
        if not self.publish_visual_odom or motion is None:
            return
        
        # Aggiorna trasformazione accumulata
        # Per semplicità, assumiamo movimento piano
        tx, ty, tz = motion['translation']
        
        # Crea messaggio TF
        t = TransformStamped()
        t.header.stamp = timestamp.to_msg()
        t.header.frame_id = 'odom'
        t.child_frame_id = 'base_link'
        
        # Converti pixel a metri (approssimativo)
        # 1 pixel ≈ 0.001 metri per semplificare
        t.transform.translation.x = tx * 0.001
        t.transform.translation.y = ty * 0.001
        t.transform.translation.z = tz * 0.001
        
        # Rotazione (per ora identità)
        t.transform.rotation.x = 0.0
        t.transform.rotation.y = 0.0
        t.transform.rotation.z = 0.0
        t.transform.rotation.w = 1.0
        
        # Pubblica TF
        #self.tf_broadcaster.sendTransform(t)

    def publish_tracked_features(self, keypoints, scores, timestamp):
        """Pubblica feature in formato TrackedFeatures"""
        if not HAS_TRACKED_MSG or not hasattr(self, 'pub_features'):
            return
        
        feats_msg = TrackedFeatures()
        feats_msg.header = Header(
            stamp=timestamp.to_msg(),
            frame_id=self.camera_optical_frame
        )
        
        for i, (x, y) in enumerate(keypoints):
            tf = TrackedFeature()
            tf.id = i
            tf.position = Point(x=float(x), y=float(y), z=0.0)
            tf.age = 0
            tf.harris_score = float(scores[i]) if i < len(scores) else 1.0
            tf.tracking_error = 0.0
            feats_msg.features.append(tf)
        
        try:
            self.pub_features.publish(feats_msg)
            self.get_logger().debug(f"Pubblicate {len(keypoints)} feature")
        except Exception as e:
            self.get_logger().error(f"Errore pubblicazione feature: {e}")
    
    def publish_rtabmap_features(self, keypoints, descriptors, timestamp):
        """Pubblica feature in formato RTAB-Map (Feature msg)"""
        if not hasattr(self, 'pub_features_rtabmap'):
            return
        
        try:
            from rtabmap_msgs.msg import Feature
            
            feat_msg = Feature()
            feat_msg.header = Header(
                stamp=timestamp.to_msg(),
                frame_id='oak_mono_camera_frame'
            )
            
            # Converti keypoints a lista piatta [x1, y1, x2, y2, ...]
            feat_msg.keypoints = keypoints.flatten().astype(np.int32).tolist()
            
            # Prepara descrittori per RTAB-Map
            # Dobbiamo selezionare i descrittori corrispondenti ai keypoint
            n_keypoints = len(keypoints)
            
            # RTAB-Map si aspetta descrittori come immagine (N x D)
            # Per ora creiamo descrittori dummy (sarà migliorato)
            dummy_descriptors = np.random.randn(n_keypoints, self.descriptor_dim).astype(np.float32)
            
            # Converti in immagine
            descriptor_image = Image()
            descriptor_image.header = Header(stamp=timestamp.to_msg())
            descriptor_image.height = n_keypoints
            descriptor_image.width = self.descriptor_dim
            descriptor_image.encoding = '32FC1'
            descriptor_image.is_bigendian = False
            descriptor_image.step = self.descriptor_dim * 4  # 4 bytes per float32
            descriptor_image.data = dummy_descriptors.tobytes()
            
            feat_msg.descriptors = descriptor_image
            feat_msg.outlines = []  # Vuoto per ora
            
            self.pub_features_rtabmap.publish(feat_msg)
            
        except ImportError:
            self.get_logger().debug("rtabmap_msgs non disponibile")
        except Exception as e:
            self.get_logger().error(f"Errore pubblicazione feature RTAB-Map: {e}")

    def publish_mono_frame(self, frame, timestamp):
        """Pubblica frame mono con timestamp specifico"""
        if not self.publish_mono:
            return
        
        mono_msg = Image()
        mono_msg.header = Header(stamp=timestamp.to_msg(), frame_id=self.camera_optical_frame)
        mono_msg.height, mono_msg.width = frame.shape
        mono_msg.encoding = 'mono8'
        mono_msg.is_bigendian = False
        mono_msg.step = mono_msg.width
        mono_msg.data = frame.tobytes()
        
        self.pub_mono.publish(mono_msg)
    
    def publish_depth_frame(self, frame, timestamp):
        """Pubblica depth frame con filtraggio avanzato"""
        if not self.publish_depth:
            return
        
        import cv2
        
        # 1. Converti in float32 per il filtraggio
        frame_float = frame.astype(np.float32)
        
        # 2. Rimuovi valori zero (non validi) e outliers estremi
        valid_mask = (frame > 0) & (frame < 10000)  # 0-10 metri
        
        if np.any(valid_mask):
            # 3. Calcola percentili invece di min/max per robustezza
            valid_values = frame[valid_mask]
            new_min = np.percentile(valid_values, 2)
            new_max = np.percentile(valid_values, 98)

            # Smoothing esponenziale
            self.current_min = (self.smoothing_alpha * new_min + 
                            (1 - self.smoothing_alpha) * self.current_min)
            self.current_max = (self.smoothing_alpha * new_max + 
                            (1 - self.smoothing_alpha) * self.current_max)

            min_depth_mm = max(300, self.current_min)
            max_depth_mm = min(5000, self.current_max)
            
            # 4. Normalizza temporaneamente per il filtro bilaterale
            # Filtro bilaterale richiede 8-bit o 32-bit float
            # Normalizza a 0-1 range
            frame_normalized = np.clip(frame_float, min_depth_mm, max_depth_mm)
            frame_normalized = (frame_normalized - min_depth_mm) / (max_depth_mm - min_depth_mm + 1e-6)
            
            # 5. Applica filtro bilaterale (supporta float32)
            filtered_normalized = cv2.bilateralFilter(frame_normalized.astype(np.float32), 
                                                     d=5, sigmaColor=0.1, sigmaSpace=5)
            
            # 6. Riporta ai valori originali
            filtered = filtered_normalized * (max_depth_mm - min_depth_mm) + min_depth_mm
            
            # 7. Applica maschera di validità
            filtered = np.where(valid_mask, filtered, 0)
            
            # 8. Pubblica depth originale (filtrata)
            depth_msg = Image()
            depth_msg.header = Header(stamp=timestamp.to_msg(), frame_id='oak_depth_frame')
            depth_msg.height, depth_msg.width = filtered.shape
            depth_msg.encoding = 'mono16'
            depth_msg.is_bigendian = False
            depth_msg.step = depth_msg.width * 2
            # Converti a uint16 per la pubblicazione
            filtered_uint16 = np.clip(filtered, 0, 65535).astype(np.uint16)
            depth_msg.data = filtered_uint16.tobytes()
            self.pub_depth.publish(depth_msg)
            
            # 9. Pubblica versione normalizzata per visualizzazione
            if self.publish_depth_normalized:
                # Usa gamma correction per migliorare visibilità
                normalized = np.clip(filtered, min_depth_mm, max_depth_mm)
                normalized = (normalized - min_depth_mm) / (max_depth_mm - min_depth_mm + 1e-6)
                
                # Gamma correction (esponenziale per vedere meglio vicino)
                gamma = 0.6  # <1 per enfatizzare valori bassi
                normalized = np.power(normalized, gamma)
                
                # Scala a 0-255
                normalized = (normalized * 255).astype(np.uint8)
                
                # Filtro mediano finale per ridurre rumore
                normalized = cv2.medianBlur(normalized, 3)
                
                norm_msg = Image()
                norm_msg.header = Header(stamp=timestamp.to_msg(), frame_id='oak_depth_frame')
                norm_msg.height, norm_msg.width = normalized.shape
                norm_msg.encoding = 'mono8'
                norm_msg.is_bigendian = False
                norm_msg.step = norm_msg.width
                norm_msg.data = normalized.tobytes()
                self.pub_depth_norm.publish(norm_msg)
                
                # Log per debug
                self.get_logger().debug(
                    f"Depth range: {new_min:.1f}-{new_max:.1f} mm, "
                    f"Valid pixels: {np.sum(valid_mask)}/{valid_mask.size}"
                )
        else:
            # Se nessun pixel valido, pubblica zero
            self.get_logger().warn("Nessun pixel di depth valido!")

    def main_callback(self):
        """Callback principale: legge tutti i dati disponibili e pubblica in modo sincronizzato"""
        now = self.get_clock().now()
        
        # 1. LEGGI TUTTI I PACCHETTI DISPONIBILI
        if self.publish_mono and hasattr(self, 'q_mono'):
            mono_packets = self.q_mono.tryGetAll()
            if mono_packets:
                self.latest_mono_packet = mono_packets[-1]
        
        if self.publish_depth and hasattr(self, 'q_depth'):
            depth_packets = self.q_depth.tryGetAll()
            if depth_packets:
                self.latest_depth_packet = depth_packets[-1]
        
        if self.publish_features and hasattr(self, 'q_sp'):
            sp_packets = self.q_sp.tryGetAll()
            if sp_packets:
                self.latest_sp_packet = sp_packets[-1]
        
        # 2. PUBBLICA SOLO SE ABBIAMO TUTTI I DATI NECESSARI
        if self.latest_mono_packet and self.latest_depth_packet:
            # Estrai i frame
            mono_frame = self.latest_mono_packet.getFrame()
            depth_frame = self.latest_depth_packet.getFrame()
            
            # Usa timestamp comune
            ros_time = now
            
            # 3. PUBBLICA TUTTO CON LO STESSO TIMESTAMP
            self.publish_mono_frame(mono_frame, ros_time)
            self.publish_depth_frame(depth_frame, ros_time)
            
            # Pubblica camera_info
            if self.publish_camera_info and self.camera_info:
                # Assicurati che il frame_id sia impostato
                if not self.camera_info.header.frame_id:
                    self.get_logger().error(f"ERRORE: camera_info.header.frame_id è vuoto!")
                    self.camera_info.header.frame_id = self.camera_optical_frame
                
                self.camera_info.header.stamp = ros_time.to_msg()
                self.pub_camera_info.publish(self.camera_info)
            
            # 4. ELABORA E PUBBLICA FEATURE SE DISPONIBILI
            if self.latest_sp_packet:
                # Usa la nuova funzione che accetta NNData
                keypoints, scores, descriptors = self.extract_superpoint_features(self.latest_sp_packet)
                if keypoints is not None and len(keypoints) > 0:
                    self.publish_tracked_features(keypoints, scores, ros_time)
                    
                    if self.use_rtabmap_format:
                        # Ora descriptors potrebbe essere None, gestiscilo
                        self.publish_rtabmap_features(keypoints, descriptors, ros_time)
                    
                    # 5. FLANN MATCHING (se attivato)
                    if (self.use_flann_matching and 
                        self.prev_descriptors is not None and 
                        descriptors is not None and 
                        self.prev_keypoints is not None):
                        
                        # Prepara i descrittori per FLANN
                        # Assicurati che i descrittori siano in formato corretto
                        if descriptors.ndim > 2:
                            # Se descriptors ha dimensioni extra, rimodella
                            desc_curr = descriptors.reshape(-1, 256).astype(np.float32)
                        else:
                            desc_curr = descriptors.astype(np.float32)
                        
                        if self.prev_descriptors.ndim > 2:
                            desc_prev = self.prev_descriptors.reshape(-1, 256).astype(np.float32)
                        else:
                            desc_prev = self.prev_descriptors.astype(np.float32)
                        
                        # Controlla che ci siano abbastanza descrittori
                        if desc_prev.shape[0] > 1 and desc_curr.shape[0] > 1:
                            # Esegui matching FLANN
                            matches = self.match_with_flann(desc_prev, desc_curr)
                            
                            if matches:
                                self.get_logger().info(
                                    f"FLANN matches: {len(matches)}",
                                    throttle_duration_sec=2.0
                                )
                                
                                # Pubblica visualizzazione dei matches
                                if self.publish_matches_visualization:
                                    self.publish_matches_visualization(
                                        matches, self.prev_keypoints, keypoints, ros_time
                                    )
                                
                                # Stima movimento
                                motion = self.estimate_motion_from_matches(
                                    matches, self.prev_keypoints, keypoints, depth_frame
                                )
                                
                                if motion:
                                    self.get_logger().debug(
                                        f"Motion: Δx={motion['translation'][0]:.1f}, "
                                        f"Δy={motion['translation'][1]:.1f}, "
                                        f"inliers={motion['num_inliers']}"
                                    )
                                    
                                    # Pubblica odometria visuale
                                    self.publish_visual_odometry(motion, ros_time)
                    
                    # Memorizza feature per prossimo frame
                    self.prev_keypoints = keypoints.copy()
                    self.prev_descriptors = descriptors.copy() if descriptors is not None else None
                    self.frame_counter += 1
            
            # 6. RESETTA I BUFFER
            self.latest_mono_packet = None
            self.latest_depth_packet = None
            self.latest_sp_packet = None
    
    def destroy_node(self):
        """Cleanup"""
        if DEPTHAI_AVAILABLE and hasattr(self, 'device'):
            self.device.close()
        super().destroy_node()


def main():
    rclpy.init()
    node = OakSuperPointRTABMap()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("Interruzione da tastiera")
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()