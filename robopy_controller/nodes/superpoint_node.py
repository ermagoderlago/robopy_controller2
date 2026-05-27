#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Nodo ROS 2 per OAK-D Lite - SuperPoint + FLANN Matcher + Odometria
# superpoint_node.py

import os
import signal
import threading
import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.time import Time
from rclpy.executors import MultiThreadedExecutor
from sensor_msgs.msg import Image, Imu, CameraInfo
from nav_msgs.msg import Odometry
from std_msgs.msg import Header
from geometry_msgs.msg import Point, PoseArray, Pose, TransformStamped, Quaternion
import yaml
from scipy.ndimage import maximum_filter
import math
from tf2_ros import TransformBroadcaster
import time
import struct
from visualization_msgs.msg import Marker, MarkerArray
from geometry_msgs.msg import Point
from std_msgs.msg import ColorRGBA
from rclpy.executors import MultiThreadedExecutor, ExternalShutdownException # Aggiunta questa
from sensor_msgs.msg import Imu
from scipy.ndimage import maximum_filter

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

# Aggiungi questi import per visualizzazione
from sensor_msgs.msg import PointCloud2, PointField
from visualization_msgs.msg import MarkerArray, Marker
from geometry_msgs.msg import PoseStamped
from std_msgs.msg import String
from sensor_msgs.msg import CompressedImage
import sensor_msgs_py.point_cloud2 as pc2

from robopy_controller.features.superpoint import EnhancedSuperPointExtractor
from robopy_controller.features.tracker import KeypointTracker
from robopy_controller.pose.odometry import HybridOdometrySystem
from robopy_controller.viz.debug import OdometryDebugSystem
from robopy_controller.features.optical_flow import KLTTracker

# Driver Mode Imports
from robopy_controller.msg import OAKSyncFrame
from cv_bridge import CvBridge


try:
    import blobconverter
    HAS_BLOBCONVERTER = True
except ImportError:
    HAS_BLOBCONVERTER = False

try:
    from vision_msgs.msg import Detection2DArray, Detection2D, BoundingBox2D, ObjectHypothesisWithPose
    VISION_MSGS_AVAILABLE = True
except ImportError:
    VISION_MSGS_AVAILABLE = False


# COCO class names per YOLO (Italian labels from oakd_camera_publisher_node)
COCO_CLASS_NAMES = [
    "persona", "bicicletta", "automobile", "motocicletta", "aereo", "autobus", "treno", "camion", "barca",
    "semaforo", "idrante", "cartello stop", "parcometro", "panchina", "uccello", "gatto",
    "cane", "cavallo", "pecora", "mucca", "elefante", "orso", "zebra", "giraffa", "zaino",
    "ombrello", "borsetta", "cravatta", "valigia", "frisbee", "sci", "snowboard", "palla sportiva",
    "aquilone", "mazza da baseball", "guantone da baseball", "skateboard", "tavola da surf", "racchetta da tennis",
    "bottiglia", "bicchiere da vino", "tazza", "forchetta", "coltello", "cucchiaio", "ciotola", "banana", "mela",
    "panino", "arancia", "broccoli", "carota", "hot dog", "pizza", "ciambella", "torta", "sedia",
    "divano", "pianta in vaso", "letto", "tavolo da pranzo", "toilette", "televisione", "laptop", "mouse",
    "telecomando", "tastiera", "cellulare", "forno a microonde", "forno", "tostapane", "lavandino", "frigorifero",
    "libro", "orologio", "vaso", "forbici", "orsacchiotto", "asciugacapelli", "spazzolino da denti"
]


class OakSuperPointOdometry(Node):
    def __init__(self):
        super().__init__('oak_superpoint_odometry')

        # FIX LOGGING: Inizializza l'attributo logger richiesto dal tuo codice
        self.logger = self.get_logger()
        self.logger.info("Inizializzazione OakSuperPointOdometry...")

        # --- PARAMETRI ---
        # Pitch della camera (se punta in ALTO usa lo stesso segno dell'URDF, es: -0.1535)
        self.camera_pitch = -0.1535 
        self.publish_superpoint_debug = True # Forza debug image

        # --- PUBLISHERS ---
        self.pub_odom = self.create_publisher(Odometry, '/odom', 10)
        self.pub_debug_image = self.create_publisher(Image, '~/debug_image', 10)
        self.pub_debug_compressed = self.create_publisher(CompressedImage, '~/debug_image/compressed', 10)
        self.tf_broadcaster = TransformBroadcaster(self)
        
        self.logger = self.get_logger()

        # Flag per shutdown controllato
        self.shutdown_requested = False
        self.shutdown_lock = threading.Lock()
        self.shutdown_complete = threading.Event()
        self.prev_mono_frame = None  # Per debug matching
        self.last_num_matches = 0    # Per covarianza adattiva
        
        # -------------------------------------
        # Parametri configurabili via ROS
        # Standard ROS frame hierarchy:
        # odom -> base_link -> camera_link -> camera_optical_frame
        # - camera_link: frame fisico della camera (assi robotica: X avanti, Y sinistra, Z alto)
        # - camera_optical_frame: frame ottico (assi visione: X destra, Y basso, Z avanti)
        # -------------------------------------

        self.base_frame = 'base_link'                         # Frame principale robot
        self.camera_frame = 'camera_link'                     # Frame fisico camera
        self.camera_optical_frame = 'camera_optical_frame'    # Frame ottico (per visione)
        self.depth_frame = 'camera_optical_frame'             # Depth usa frame ottico
        self.imu_frame = 'imu_link'                           # Frame IMU standard

        self.current_track_ids = None

        # Per stabilizzazione temporale
        self.depth_history = []
        self.history_length = 5
        self.current_min = 100  # Valori di default
        self.current_max = 5000
        self.smoothing_alpha = 0.3  # Fattore di smoothing

        self.declare_parameter('publish_depth_normalized', False)
        self.declare_parameter('fps', 15)
        self.declare_parameter('imu_rate', 50)
        self.declare_parameter('superpoint_side', 'left')
        self.declare_parameter('superpoint_blob', '')
        self.declare_parameter('depth_out_size', '320x200')
        self.declare_parameter('mono_out_size', '320x200')
        self.declare_parameter('publish_depth', True)
        self.declare_parameter('publish_mono', True)
        self.declare_parameter('publish_features', True)
        self.declare_parameter('publish_camera_info', True)
        self.declare_parameter('feature_threshold', 0.015)
        self.declare_parameter('max_features', 500)
        self.declare_parameter('descriptor_dim', 256)
        self.declare_parameter('camera_info_file', '')
        self.declare_parameter('use_imu', True)
        self.declare_parameter('use_rtabmap_format', True)
        
        # NUOVI PARAMETRI PER FLANN MATCHING
        self.declare_parameter('use_flann_matching', True)
        self.declare_parameter('flann_match_ratio', 0.7)
        self.declare_parameter('min_matches_for_tracking', 10)
        self.declare_parameter('publish_visual_odom', True)
        self.declare_parameter('publish_matches_visualization', True)
        
        # PARAMETRI PER VISUALIZZAZIONE SUPERPOINT
        self.declare_parameter('publish_superpoint_debug', True)
        
        # PARAMETRO PER BUNDLE ADJUSTMENT
        self.declare_parameter('use_bundle_adjustment', True)
        self.declare_parameter('publish_keypoints_cloud', True)
        self.declare_parameter('publish_matched_cloud', True)
        
        # PARAMETRI PER YOLO (opzionale)
        self.declare_parameter('yolo_blob', '')
        self.declare_parameter('use_yolo_segmentation', False)
        self.declare_parameter('yolo_confidence_threshold', 0.5)
        self.declare_parameter('yolo_input_width', 512)
        self.declare_parameter('yolo_input_height', 288)
        self.declare_parameter('execution_provider', 'VPU') # VPU (default) o CPU
        self.declare_parameter('filter_floor', True)        # Filtra pavimento (geometrico/semantico)
        self.declare_parameter('filter_people', True)       # Filtra persone/animali
        self.declare_parameter('camera_pitch', -0.1535)    # Inclinazione camera (rad)
        
        # Variabili per throttling del logging
        self.last_flann_log_time = 0
        self.flann_log_interval = 0.5  # secondi - ridotto per debug
        self.last_depth_log_time = 0
        self.depth_log_interval = 2.0  # secondi
        self.current_track_ids_prev = None # Per logging cambiamenti ID


        # Inizializza tracker keypoints
        self.keypoint_tracker = KeypointTracker(max_distance=20, max_age=3)
        self.frame_buffer = []  # Ultimi N frame per stabilizzazione
        self.keypoint_buffer = []  # Ultimi N set di keypoints
        self.buffer_size = 3
        self.last_good_keypoints = None
        
        # Lettura parametri
        self.fps = int(self.get_parameter('fps').value)
        self.fps = int(self.get_parameter('fps').value)
        self.sp_side = self.get_parameter('superpoint_side').value.lower()
        if self.sp_side in ['center', 'rgb']:
             self.sp_side = 'rgb' # Normalize
        
        self.publish_depth = bool(self.get_parameter('publish_depth').value)
        self.publish_mono = bool(self.get_parameter('publish_mono').value)
        self.publish_features = bool(self.get_parameter('publish_features').value)
        self.publish_camera_info = bool(self.get_parameter('publish_camera_info').value)
        self.feature_threshold = float(self.get_parameter('feature_threshold').value)
        self.max_features = int(self.get_parameter('max_features').value)
        self.descriptor_dim = int(self.get_parameter('descriptor_dim').value)
        self.use_imu = bool(self.get_parameter('use_imu').value)
        self.use_rtabmap_format = bool(self.get_parameter('use_rtabmap_format').value)
        self.publish_depth_normalized = bool(self.get_parameter('publish_depth_normalized').value)
        
        # Nuovi parametri FLANN
        self.use_flann_matching = bool(self.get_parameter('use_flann_matching').value)
        self.flann_match_ratio = float(self.get_parameter('flann_match_ratio').value)
        self.min_matches_for_tracking = int(self.get_parameter('min_matches_for_tracking').value)
        self.publish_visual_odom_flag = bool(self.get_parameter('publish_visual_odom').value)
        self.publish_matches_viz_flag = bool(self.get_parameter('publish_matches_visualization').value)

        # Nuovi parametri visualizzazione SuperPoint
        self.publish_superpoint_debug = bool(self.get_parameter('publish_superpoint_debug').value)
        self.publish_keypoints_cloud = bool(self.get_parameter('publish_keypoints_cloud').value)
        self.publish_matched_cloud = bool(self.get_parameter('publish_matched_cloud').value)

        # Debug abilitato
        self.declare_parameter('debug_images', True)
        self.debug_images = bool(self.get_parameter('debug_images').value)

        if self.debug_images:
            self.get_logger().info("🔍 DEBUG IMMAGINI ATTIVATO")
            self.get_logger().info(f"📊 Parametri pubblicazione: mono={self.publish_mono}, depth={self.publish_depth}")

        # HYBRID TRACKING CONFIGURATION
        self.declare_parameter('enable_hybrid_tracking', True)
        self.declare_parameter('superpoint_interval', 3)
        self.declare_parameter('processing_width', 640)
        self.declare_parameter('processing_height', 400)
        
        # CRITICAL: Parse ALL dimension parameters FIRST before any logic
        # This ensures we have the correct values from launch file
        depth_size = self.get_parameter('depth_out_size').value
        self.depth_w, self.depth_h = map(int, depth_size.split('x'))
        mono_size = self.get_parameter('mono_out_size').value
        self.mono_w, self.mono_h = map(int, mono_size.split('x'))
        
        # Processing resolution (for SuperPoint/KLT) - separate from publishing resolution
        self.enable_hybrid = bool(self.get_parameter('enable_hybrid_tracking').value)
        self.sp_interval = int(self.get_parameter('superpoint_interval').value)
        self.proc_w = int(self.get_parameter('processing_width').value)
        self.proc_h = int(self.get_parameter('processing_height').value)
        
        if self.enable_hybrid:
            self.logger.info(f"🚀 Hybrid Tracking ENABLED. Processing: {self.proc_w}x{self.proc_h}, Mono pub: {self.mono_w}x{self.mono_h}")
        else:
            # If hybrid disabled, processing resolution follows mono
            self.proc_w = self.mono_w
            self.proc_h = self.mono_h
            self.logger.info(f"Hybrid disabled. Using mono resolution: {self.mono_w}x{self.mono_h}")

        # Initialize KLT Tracker
        self.klt_tracker = KLTTracker(logger=self.logger)
        self.frame_count = 0
        self.prev_gray_full = None # Full resolution previous frame for KLT

        
        if self.publish_camera_info:
            # Creiamo il publisher subito, self.camera_info sarà popolato dopo
            self.pub_camera_info = self.create_publisher(
                CameraInfo, '/camera/camera_info', 10
            )
            self.get_logger().info("Publisher camera_info attivato (attenderà calibrazione)")

        # NUOVI PARAMETRI PER SISTEMI POTENZIATI
        self.declare_parameter('use_enhanced_extraction', True)
        self.declare_parameter('use_hybrid_matching', True)
        self.declare_parameter('debug_level', 'info')  # 'none', 'info', 'debug'

        # Parametro per bundle adjustment
        self.use_bundle_adjustment = bool(self.get_parameter('use_bundle_adjustment').value)
        
        # Leggi i nuovi parametri
        self.use_enhanced_extraction = bool(self.get_parameter('use_enhanced_extraction').value)
        self.use_hybrid_matching = bool(self.get_parameter('use_hybrid_matching').value)
        self.debug_level = self.get_parameter('debug_level').value

        #self.publish_depth_normalized = bool(self.get_parameter('publish_depth_normalized').value)

        # Parametri YOLO
        # Parametri YOLO
        self.use_yolo_segmentation = bool(self.get_parameter('use_yolo_segmentation').value)
        self.yolo_confidence_threshold = float(self.get_parameter('yolo_confidence_threshold').value)
        self.yolo_w = int(self.get_parameter('yolo_input_width').value)
        self.yolo_h = int(self.get_parameter('yolo_input_height').value)
        
        # Parametri Filtri Semantici/Geometrici
        self.execution_provider = self.get_parameter('execution_provider').value.upper()
        self.filter_floor = bool(self.get_parameter('filter_floor').value)
        self.filter_people = bool(self.get_parameter('filter_people').value)
        self.camera_pitch = float(self.get_parameter('camera_pitch').value)


        # Matrice di rotazione per correggere assi OAK-D Lite
        # Camera: Z=avanti, X=destra, Y=basso
        # Robot: X=avanti, Y=sinistra, Z=alto
        self.R_camera_to_robot = np.array([
            [0.0, 0.0, 1.0],   # Camera Z (avanti) -> Robot X (avanti)
            [-1.0, 0.0, 0.0],  # Camera -X (sinistra) -> Robot Y (sinistra)
            [0.0, -1.0, 0.0]   # Camera -Y (alto) -> Robot Z (alto)
        ])

        self.get_logger().info("✅ Assi OAK-D Lite configurati: Camera(Z=avanti) -> Robot(X=avanti)")

        # NOTE: depth_w/h and mono_w/h already parsed above (lines 247-250)
        # Removed duplicate parsing to avoid confusion
        
        # Percorso blob SuperPoint
        blob_param = self.get_parameter('superpoint_blob').value
        if blob_param and os.path.isfile(blob_param):
            self.sp_blob = blob_param
        else:
            share_dir = get_package_share_directory('robopy_controller')
            candidate = os.path.join(share_dir, 'models', 'superpoint.blob')
            if os.path.isfile(candidate):
                self.sp_blob = candidate
                self.get_logger().info(f"Usando blob SuperPoint: {self.sp_blob}")
            else:
                self.get_logger().error(f"Blob SuperPoint non trovato: {candidate}")
                self.sp_blob = None
                self.publish_features = False
        
        # Percorso blob YOLO (opzionale)
        yolo_blob_param = self.get_parameter('yolo_blob').value
        if yolo_blob_param:
            if os.path.isfile(yolo_blob_param):
                self.yolo_blob_path = yolo_blob_param
                self.get_logger().info(f"✅ YOLO blob trovato: {self.yolo_blob_path}")
            else:
                self.get_logger().warn(f"⚠️ YOLO blob non trovato al percorso: {yolo_blob_param}")
                # Fallback: cerca nella cartella models del package
                share_dir = get_package_share_directory('robopy_controller')
                basename = os.path.basename(yolo_blob_param)
                candidate = os.path.join(share_dir, 'models', basename)
                if os.path.isfile(candidate):
                    self.yolo_blob_path = candidate
                    self.get_logger().info(f"✅ YOLO blob trovato nel package: {self.yolo_blob_path}")
                else:
                    self.get_logger().error(f"❌ YOLO blob non trovato neanche nel package: {candidate}")
                    self.use_yolo_segmentation = False
        elif self.use_yolo_segmentation:
            # Cerca il default se abilitato ma non specificato
            share_dir = get_package_share_directory('robopy_controller')
            candidate = os.path.join(share_dir, 'models', 'yolov6nr1_coco_640x352.blob')
            if os.path.isfile(candidate):
                self.yolo_blob_path = candidate
                self.get_logger().info(f"✅ Usando YOLO blob di default: {self.yolo_blob_path}")
            elif HAS_BLOBCONVERTER:
                try:
                    self.get_logger().info(f"YOLO blob non trovato. Scaricamento YOLOv8n-seg ({self.yolo_w}x{self.yolo_h}) via blobconverter...")
                    
                    # Versione Nano Segmentation
                    # Nota: blobconverter spesso usa nomi come 'yolov8n_seg_coco_640x640'
                    # ma se vogliamo una risoluzione specifica e non è nel zoo, servirebbero compile_params.
                    # Procediamo con il tentativo di scaricare la versione standard o quella richiesta.
                    model_name = "yolov8n_seg_coco_640x640" # Fallback standard
                    if self.yolo_w == 640 and self.yolo_h == 640:
                        model_name = "yolov8n_seg_coco_640x640"
                    
                    # Se l'utente vuole 512x288, proviamo a scaricare la versione nano generica
                    # o usiamo quella disponibile. Per ora scarichiamo quella del zoo.
                    self.yolo_blob_path = blobconverter.from_zoo(
                        name="yolov8n_seg_coco_640x640", 
                        shaves=8
                    )
                    self.get_logger().info(f"✅ YOLO blob scaricato: {self.yolo_blob_path}")
                except Exception as e:
                    self.get_logger().error(f"❌ Errore scaricamento YOLO: {e}")
                    self.use_yolo_segmentation = False
            else:
                self.get_logger().warn(f"YOLO blob non trovato e blobconverter non disponibile: {candidate}")
                self.use_yolo_segmentation = False
        
        # Info camera
        self.camera_info = None
        self.camera_matrix = None
        self.dist_coeffs = None
        camera_info_file = self.get_parameter('camera_info_file').value
        if camera_info_file and os.path.isfile(camera_info_file):
            self.load_camera_info(camera_info_file)
        else:
            self.generate_default_camera_info()
        
        # Variabili per odometria
        self.accumulated_transform = np.eye(4)
        self.last_pose = np.eye(4)
        self.scale_factor = 1.0
        self.keyframe_buffer = []
        self.max_keyframes = 20
        
        # Buffer per tenere l'ultimo pacchetto di ogni tipo
        self.latest_mono_packet = None
        self.latest_depth_packet = None
        self.latest_sp_packet = None
        self.latest_yolo_packet = None
        self.prev_keypoints = None
        self.prev_descriptors = None
        self.prev_depth_frame = None
        self.prev_stamp = None
        
        # -------------------------------------
        # Publisher ROS
        # -------------------------------------
        
        # Depth map
        if self.publish_depth:
            self.pub_depth = self.create_publisher(
                Image, '/depth/image_raw', 10  # Formato 16UC1 per compressione
            )
            self.get_logger().info(f"Publisher depth attivato: {self.depth_w}x{self.depth_h}")

        #if self.publish_depth_normalized:
        #    self.pub_depth_norm = self.create_publisher(
        #        Image, '/depth/visualization', 10  # Formato bgr8 SOLO per visualizzazione
        #    )
        #    self.get_logger().info("Publisher depth visualizzazione attivato")
        
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
        
        # Features in formato RTAB-Map
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
        
        # Odometria
        if self.publish_visual_odom_flag:
            self.pub_odom = self.create_publisher(Odometry, '/superpoint/odometry', 10)
            self.tf_broadcaster = TransformBroadcaster(self)
            self.get_logger().info("Publisher odometry e TF attivati")
            
            # Pubblica TF statica tra base_link e camera_frame
            self.publish_static_tf()
        
        # Matches visualization
        if self.publish_matches_viz_flag:
            self.pub_matches_viz = self.create_publisher(
                PoseArray, '/flann/matches_viz', 10
            )
            self.get_logger().info("Publisher matches visualization attivato")
        
        #if self.publish_depth_normalized:
        #    self.pub_depth_norm = self.create_publisher(
        #        Image, '/depth/image_normalized', 10
        #    )
        #    self.get_logger().info("Publisher depth normalizzata attivato")
        
        # Publisher per debug SuperPoint
        if self.publish_superpoint_debug:
            self.pub_debug_image = self.create_publisher(
                Image, '/superpoint/debug_image', 10
            )
            self.get_logger().info("Publisher debug image attivato")
        
        if self.publish_keypoints_cloud:
            self.pub_keypoints_cloud = self.create_publisher(
                PointCloud2, '/superpoint/keypoints_3d', 10
            )
            self.get_logger().info("Publisher keypoints 3D cloud attivato")
        
        if self.publish_matched_cloud:
            self.pub_matches_cloud = self.create_publisher(
                PointCloud2, '/superpoint/matches_3d', 10
            )
            self.get_logger().info("Publisher matches 3D cloud attivato")
        
        # Marker per visualizzare keypoints in RViz
        self.pub_markers = self.create_publisher(
            MarkerArray, '/superpoint/markers', 10
        )
        
        # Publisher YOLO (opzionale)
        if self.use_yolo_segmentation and self.yolo_blob_path:
            try:
                from vision_msgs.msg import Detection2DArray
                self.pub_yolo = self.create_publisher(Detection2DArray, '/yolo/detections', 10)
                self.get_logger().info("Publisher YOLO segmentation attivato")
            except ImportError:
                self.get_logger().warn("vision_msgs non disponibile - YOLO disabilitato")
                self.use_yolo_segmentation = False

        
        
        # -------------------------------------
        # FLANN MATCHER SETUP
        # -------------------------------------
        if self.use_flann_matching:
            # Inizializzazione FLANN matcher
            FLANN_INDEX_KDTREE = 1
            index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
            search_params = dict(checks=50)
            self.flann = cv2.FlannBasedMatcher(index_params, search_params)
            
            self.get_logger().info("FLANN matcher inizializzato")
        
        # Variabili di stato
        self.latest_keypoints = None
        self.latest_descriptors = None
        self.latest_scores = None
        self.frame_counter = 0
        
        # Driver Mode Parameter
        self.declare_parameter('use_driver_topic', False)
        self.use_driver_topic = bool(self.get_parameter('use_driver_topic').value)
        self.bridge = CvBridge()

        # Pipeline DepthAI
        if self.use_driver_topic:
            self.get_logger().info("🚀 Running in DRIVER MODE (Subscribing to /oak/sync_frame)")
            self.create_subscription(OAKSyncFrame, '/oak/sync_frame', self.driver_callback, 10)
            # Skip Device Setup
        elif DEPTHAI_AVAILABLE:
            self.setup_depthai_pipeline()

        else:
            self.get_logger().warn("DepthAI non disponibile - modalità simulazione")
            self.setup_simulation()
        
        # ============================================================================
        # FIX 1: Correggi CameraInfo
        # ============================================================================
        self.fix_camera_info_issue()
        
        # ============================================================================
        # FIX 2: Inizializza sistemi potenziati
        # ============================================================================
        self.enhanced_extractor = EnhancedSuperPointExtractor(
            config={
                'feature_threshold': self.feature_threshold,
                'max_features': self.max_features
            },
            logger=self.get_logger()
        )
        
        # ============================================================================
        # FIX 3: Inizializza sistema odometria ibrido
        # ============================================================================
        self.odometry_system = HybridOdometrySystem(
            config={
                'min_matches': self.min_matches_for_tracking,
                'min_matches_for_tracking': 8
            },
            camera_matrix=self.camera_matrix,
            dist_coeffs=self.dist_coeffs,
            logger=self.get_logger()
        )
        
        # ============================================================================
        # FIX 4: Inizializza sistema debug
        # ============================================================================
        self.debug_system = OdometryDebugSystem(self)
        
        self.get_logger().info("✅ Sistema potenziato inizializzato con successo!")
        
        if not self.use_driver_topic:
            self.main_timer = self.create_timer(1.0 / self.fps, self.main_callback)

        
        self.get_logger().info(f"Nodo SuperPoint Odometry avviato a {self.fps} FPS")
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)


    def _convert_optical_to_robot(self, T_optical):
            """
            Converte dal frame Ottico al frame Robot compensando l'inclinazione (Pitch).
            """
            if T_optical is None:
                return None

            # 1. Estrai Rotazione (R) e Traslazione (t) dal movimento ottico
            R_opt = T_optical[:3, :3]
            t_opt = T_optical[:3, 3]

            # 2. Step A: Converti da Ottico (Z-forward) a Camera Link geometrico (X-forward)
            #    Assi ottici: Z->X, X->-Y, Y->-Z
            R_opt_to_cam = np.array([
                [0, 0, 1],
                [-1, 0, 0],
                [0, -1, 0]
            ], dtype=np.float32)

            # Applichiamo il cambio base: T_cam = R_adj * T_opt * R_adj.T
            R_cam = R_opt_to_cam @ R_opt @ R_opt_to_cam.T
            t_cam = R_opt_to_cam @ t_opt

            # 3. Step B: Compensa l'inclinazione (Pitch) della camera rispetto al robot
            #    Se la camera punta in ALTO (pitch negativo nell'URDF, es: -0.1535),
            #    dobbiamo ruotare il vettore movimento verso il BASSO per allinearlo al pavimento.
            
            # Angolo di montaggio (DEVE COMBACIARE CON L'URDF/LAUNCH)
            # Se URDF = -0.1535 (punta in alto), qui usiamo lo stesso segno per costruire la rotazione
            pitch_angle = self.camera_pitch 
            
            c = np.cos(pitch_angle)
            s = np.sin(pitch_angle)
            
            # Matrice di rotazione attorno a Y (Pitch)
            # R_base_to_cam
            R_pitch = np.array([
                [ c, 0, s],
                [ 0, 1, 0],
                [-s, 0, c]
            ], dtype=np.float32)

            # Vogliamo portare il movimento DAL frame camera AL frame base.
            # T_base = R_pitch * T_cam * R_pitch.T
            
            R_robot = R_pitch @ R_cam @ R_pitch.T
            t_robot = R_pitch @ t_cam

            # 4. Ricostruisci la matrice 4x4 finale
            T_robot = np.eye(4, dtype=np.float32)
            T_robot[:3, :3] = R_robot
            T_robot[:3, 3] = t_robot
            
            return T_robot



    def publish_odometry(self, current_pose, timestamp, inlier_ratio):
        """Pubblica l'odometria SOLO se valida e sopra soglia"""
        
        # ⚠️ CONTROLLO CRITICO 1: Se inlier ratio è troppo basso, NON pubblicare
        MIN_INLIER_RATIO = 0.35  # 35% inliers minimi
        if inlier_ratio < MIN_INLIER_RATIO:
            self.logger.debug(f"📉 Inlier ratio troppo basso: {inlier_ratio:.2f} < {MIN_INLIER_RATIO:.2f}")
            return
        
        if current_pose is None:
            self.get_logger().warn("⚠️ current_pose è None")
            return
        
        # ⚠️ CONTROLLO CRITICO 2: Valida la trasformazione corrente
        try:
            # Estrai movimento dalla trasformazione corrente (è già nel frame robot)
            t_norm = np.linalg.norm(current_pose[:3, 3])
            R = current_pose[:3, :3]
            rvec, _ = cv2.Rodrigues(R)
            r_norm = np.linalg.norm(rvec)
            
            # SOGLIE MOLTO STRETTE per robot fermo
            MAX_TRANSLATION_STATIC = 0.002  # 2mm - se è meno, è rumore
            MAX_ROTATION_STATIC = np.deg2rad(0.1)  # 0.1 gradi
            
            # ⚠️ Se il robot è FERMO, non pubblicare NIENTE
            if t_norm < MAX_TRANSLATION_STATIC and r_norm < MAX_ROTATION_STATIC:
                self.logger.debug(f"🔒 ROBOT FERMO: t={t_norm*1000:.1f}mm, r={np.degrees(r_norm):.1f}°")
                return
            
            # Soglie per movimento realistico (a 15fps)
            MAX_TRANSLATION_DYNAMIC = 0.15  # 15cm/frame (2.25m/s)
            MAX_ROTATION_DYNAMIC = np.deg2rad(30)  # 30 gradi/frame
            
            if t_norm > MAX_TRANSLATION_DYNAMIC:
                self.get_logger().warn(f"🚫 Traslazione troppo grande: {t_norm:.3f}m")
                return
            
            if r_norm > MAX_ROTATION_DYNAMIC:
                self.get_logger().warn(f"🚫 Rotazione troppo grande: {np.degrees(r_norm):.1f}°")
                return
                
        except Exception as e:
            self.get_logger().error(f"Errore validazione trasformazione: {e}")
            return
        
        # ⚠️ CONTROLLO CRITICO 3: Smoothing della trasformazione
        if hasattr(self.odometry_system, '_smooth_transform'):
            current_pose_smoothed = self.odometry_system._smooth_transform(current_pose)
        else:
            current_pose_smoothed = current_pose
        
        # ⚠️ CONTROLLO CRITICO 4: Aggiorna odometria accumulata SOLO se valida
        success = False
        try:
            success = self.odometry_system.update_odometry(current_pose_smoothed, inlier_ratio)
        except Exception as e:
            self.get_logger().error(f"Errore update_odometry: {e}")
            return
        
        if not success:
            self.logger.debug("❌ Fallito aggiornamento odometria")
            return
        
        # ⚠️ CONTROLLO CRITICO 5: Ottieni la posa accumulata DOPO l'update
        try:
            accumulated_pose = self.odometry_system.get_current_pose()
            if accumulated_pose is None:
                self.get_logger().warn("accumulated_pose è None")
                return
        except Exception as e:
            self.get_logger().error(f"Errore get_current_pose: {e}")
            return
        
        # 6. PUBBLICAZIONE ODOMETRIA ROS
        try:
            odom_msg = Odometry()
            odom_msg.header.stamp = timestamp.to_msg()
            odom_msg.header.frame_id = "odom"
            odom_msg.child_frame_id = self.base_frame
            
            # Posizione dall'accumulato
            position = accumulated_pose[:3, 3]
            odom_msg.pose.pose.position.x = float(position[0])
            odom_msg.pose.pose.position.y = float(position[1])
            odom_msg.pose.pose.position.z = float(position[2])
            
            # Orientamento dall'accumulato
            R_accum = accumulated_pose[:3, :3]
            qx, qy, qz, qw = self.matrix_to_quaternion(R_accum)
            
            odom_msg.pose.pose.orientation.x = qx
            odom_msg.pose.pose.orientation.y = qy
            odom_msg.pose.pose.orientation.z = qz
            odom_msg.pose.pose.orientation.w = qw
            
            # ⚠️ Covarianza ADATTIVA basata sulla qualità della stima
            # Più alto inlier_ratio → più bassa covarianza (più fiducia)
            base_pos_cov = 0.1
            base_ang_cov = 0.2
            
            # Fattore di qualità (0.0 = pessimo, 1.0 = ottimo)
            quality_factor = max(0.0, min(1.0, (inlier_ratio - 0.3) / 0.7))
            
            pos_cov = base_pos_cov * (1.0 - quality_factor) + 0.01
            ang_cov = base_ang_cov * (1.0 - quality_factor) + 0.01
            
            # Matrice di covarianza 6x6 (posizione + orientamento)
            # [x, y, z, roll, pitch, yaw]
            odom_msg.pose.covariance = [
                pos_cov, 0.0,    0.0,    0.0,    0.0,    0.0,
                0.0,    pos_cov, 0.0,    0.0,    0.0,    0.0,
                0.0,    0.0,    pos_cov, 0.0,    0.0,    0.0,
                0.0,    0.0,    0.0,    ang_cov, 0.0,    0.0,
                0.0,    0.0,    0.0,    0.0,    ang_cov, 0.0,
                0.0,    0.0,    0.0,    0.0,    0.0,    ang_cov
            ]
            
            # Velocità (opzionale, puoi stimarla dalla trasformazione)
            dt = 1.0 / self.fps if hasattr(self, 'fps') else 0.066
            odom_msg.twist.twist.linear.x = float(current_pose[0, 3] / dt)
            odom_msg.twist.twist.linear.y = float(current_pose[1, 3] / dt)
            odom_msg.twist.twist.linear.z = float(current_pose[2, 3] / dt)
            
            # Covarianza velocità (alta perché stimata visivamente)
            vel_cov = 0.5
            odom_msg.twist.covariance = [vel_cov] * 36
            
            # Pubblica
            if hasattr(self, 'pub_odom'):
                self.pub_odom.publish(odom_msg)
                
                self.logger.debug(
                    f"📤 Odom pubblicata: "
                    f"pos=[{position[0]:.3f}, {position[1]:.3f}, {position[2]:.3f}] | "
                    f"Δ=[{current_pose[0, 3]:.3f}, {current_pose[1, 3]:.3f}, {current_pose[2, 3]:.3f}]m | "
                    f"inlier={inlier_ratio:.2f} | "
                    f"cov={pos_cov:.3f}"
                )
            else:
                self.get_logger().error("❌ pub_odom non esiste!")
                
        except Exception as e:
            self.get_logger().error(f"❌ Errore pubblicazione odometria: {e}")
            import traceback
            self.get_logger().error(traceback.format_exc())
            return
        
        # 7. PUBBLICAZIONE TF
        try:
            self.publish_odometry_tf(accumulated_pose, timestamp)
        except Exception as e:
            self.get_logger().error(f"❌ Errore pubblicazione TF: {e}")


    def publish_odometry_tf(self, transform, timestamp):
        """Pubblica la trasformazione TF per l'odometria"""
        if transform is None:
            return
            
        try:
            # Crea messaggio TransformStamped
            t = TransformStamped()
            t.header.stamp = timestamp.to_msg()
            t.header.frame_id = "odom"  # Frame fisso del mondo
            t.child_frame_id = self.base_frame  # Frame del robot (base_link)
            
            # Posizione
            position = transform[:3, 3]
            t.transform.translation.x = float(position[0])
            t.transform.translation.y = float(position[1])
            t.transform.translation.z = float(position[2])
            
            # Rotazione (da matrice a quaternione)
            R = transform[:3, :3]
            qx, qy, qz, qw = self.matrix_to_quaternion(R)
            
            t.transform.rotation.x = qx
            t.transform.rotation.y = qy
            t.transform.rotation.z = qz
            t.transform.rotation.w = qw
            
            # Invia TF
            self.tf_broadcaster.sendTransform(t)
            
            # Debug
            self.logger.debug(
                f"📤 TF pubblicato: odom → {self.base_frame} | "
                f"Pos: [{position[0]:.2f}, {position[1]:.2f}, {position[2]:.2f}]"
            )
            
        except Exception as e:
            self.get_logger().error(f"Errore pubblicazione TF: {e}")



    def visualize_matches(self, prev_frame, curr_frame, prev_kpts, curr_kpts, matches, max_matches=50):
        """Crea immagine con match visualizzati per debug"""
        if prev_frame is None or curr_frame is None:
            return None
        
        try:
            # Converti in BGR se necessario
            if len(prev_frame.shape) == 2:
                prev_rgb = cv2.cvtColor(prev_frame, cv2.COLOR_GRAY2BGR)
            else:
                prev_rgb = prev_frame.copy()
                
            if len(curr_frame.shape) == 2:
                curr_rgb = cv2.cvtColor(curr_frame, cv2.COLOR_GRAY2BGR)
            else:
                curr_rgb = curr_frame.copy()
            
            # Disegna keypoints
            for kp in prev_kpts[:100]:  # Limita a 100 per chiarezza
                x, y = int(kp[0]), int(kp[1])
                cv2.circle(prev_rgb, (x, y), 3, (0, 255, 0), -1)  # Verde per prev
                cv2.circle(prev_rgb, (x, y), 4, (0, 0, 0), 1)     # Bordo nero
            
            for kp in curr_kpts[:100]:
                x, y = int(kp[0]), int(kp[1])
                cv2.circle(curr_rgb, (x, y), 3, (0, 0, 255), -1)  # Rosso per curr
                cv2.circle(curr_rgb, (x, y), 4, (255, 255, 255), 1)  # Bordo bianco
            
            # Crea immagine concatenata
            h, w = prev_rgb.shape[:2]
            vis = np.zeros((h, w*2, 3), dtype=np.uint8)
            vis[:, :w] = prev_rgb
            vis[:, w:] = curr_rgb
            
            # Disegna linee per i match
            for i, match in enumerate(matches[:max_matches]):
                try:
                    # Controlla indici validi
                    if (match.queryIdx >= len(prev_kpts) or 
                        match.trainIdx >= len(curr_kpts)):
                        continue
                        
                    pt1 = (int(prev_kpts[match.queryIdx][0]), 
                        int(prev_kpts[match.queryIdx][1]))
                    pt2 = (int(curr_kpts[match.trainIdx][0]) + w, 
                        int(curr_kpts[match.trainIdx][1]))
                    
                    # Colore in base alla qualità del match
                    if match.distance < 0.5:
                        color = (0, 255, 255)  # Giallo per match buoni
                    elif match.distance < 0.7:
                        color = (255, 255, 0)  # Ciano per match medi
                    else:
                        color = (255, 0, 255)  # Magenta per match deboli
                    
                    cv2.line(vis, pt1, pt2, color, 1, cv2.LINE_AA)
                    
                    # Punti terminali
                    cv2.circle(vis, pt1, 2, (255, 255, 255), -1)
                    cv2.circle(vis, pt2, 2, (255, 255, 255), -1)
                    
                except (IndexError, TypeError) as e:
                    continue
            
            # Aggiungi testo informativo
            cv2.putText(vis, f"Matches: {len(matches)}", 
                    (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.putText(vis, f"Prev: {len(prev_kpts)}", 
                    (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.putText(vis, f"Curr: {len(curr_kpts)}", 
                    (w+10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            
            # Linea divisoria
            cv2.line(vis, (w, 0), (w, h), (255, 255, 255), 1)
            
            return vis
            
        except Exception as e:
            self.get_logger().error(f"Errore in visualize_matches: {e}")
            return None
        
    def debug_blob_layers(self, sp_pkt):
        """Debug per identificare i layer nel blob SuperPoint"""
        if sp_pkt is None:
            self.get_logger().error("❌ sp_pkt è None")
            return
        
        try:
            # Ottieni tutti i nomi dei layer
            layer_names = sp_pkt.getAllLayerNames()
            self.get_logger().info(f"✅ Layer trovati nel blob: {layer_names}")
            
            # Per ogni layer, mostra dimensione e tipo
            for name in layer_names:
                if sp_pkt.hasLayer(name):
                    try:
                        # Prova TUTTI i tipi di dati - ora concentriamoci su FP16
                        fp16_data = sp_pkt.getLayerFp16(name)
                        if fp16_data is not None:
                            self.get_logger().info(f"  • {name}: FP16, shape={len(fp16_data)} elementi")
                            
                            # Stampa i primi 10 valori per debug
                            if len(fp16_data) > 0:
                                self.get_logger().info(f"     Primi 10 valori: {fp16_data[:10]}")
                                self.get_logger().info(f"     Min: {min(fp16_data):.6f}, Max: {max(fp16_data):.6f}")
                                
                        # Controlla anche altri formati per sicurezza
                        int32_data = sp_pkt.getLayerInt32(name)
                        if int32_data is not None and len(int32_data) > 0:
                            self.get_logger().info(f"  • {name}: Int32, shape={len(int32_data)} elementi")
                            
                    except Exception as e:
                        self.get_logger().info(f"  • {name}: Errore lettura - {e}")
        except Exception as e:
            self.get_logger().error(f"❌ Errore debug layer: {e}")

    def decode_yolo_segmentation(self, nndata):
        """
        Decodifica l'output di YOLOv8n-seg.
        """
        if nndata is None:
            return []
        try:
            # YOLOv8n-seg standard output names
            # output0: detections [1, 116, 8400]
            # output1: prototypes [1, 32, 160, 160]
            out0 = np.array(nndata.getLayerFp16("output0")).reshape(116, -1)
            
            detections = []
            conf_thresh = self.yolo_confidence_threshold
            
            # 80 classi COCO + 4 bbox + 32 mask coeffs
            scores = out0[4:84, :]
            max_scores = np.max(scores, axis=0)
            class_ids = np.argmax(scores, axis=0)
            
            indices = np.where(max_scores > conf_thresh)[0]
            for idx in indices:
                # YOLOv8-seg detections: [x_center, y_center, width, height, conf, class, mask_coeffs...]
                # The coordinates are normalized to model input size (e.g., 640 for 640x640 model)
                # If the model is exported with dynamic shapes, it might vary.
                # Assuming standard YOLOv8 behavior where coordinates are relative to input size.
                detections.append({
                    'box': out0[:4, idx],
                    'conf': max_scores[idx],
                    'class': int(class_ids[idx])
                })
            return detections
        except Exception as e:
            self.get_logger().error(f"Errore YOLO: {e}")
            return []

    def decode_yolo_v6(self, pkt):
        """
        Decodifica l'output di YoloDetectionNetwork (YOLOv6).
        """
        dets = []
        try:
            if not pkt or not hasattr(pkt, "detections"): return dets
            for d in pkt.detections:
                # YoloDetectionNetwork returns normalized coordinates [0, 1]
                # We normalize them to the yolo input size first, then _publish_yolo_detections scales them to mono
                # Actually, _publish_yolo_detections expects coordinates relative to self.yolo_w/h
                
                # DepthAI Detection object has xmin, ymin, xmax, ymax
                bw = (d.xmax - d.xmin) * self.yolo_w
                bh = (d.ymax - d.ymin) * self.yolo_h
                xc = (d.xmin + d.xmax) / 2.0 * self.yolo_w
                yc = (d.ymin + d.ymax) / 2.0 * self.yolo_h
                
                dets.append({
                    'box': [xc, yc, bw, bh],
                    'conf': float(d.confidence),
                    'class': int(d.label)
                })
        except Exception as e:
            self.get_logger().error(f"Errore decodifica YOLOv6: {e}")
        return dets

    def _publish_yolo_detections(self, detections, stamp):
        """
        Pubblica le detection YOLO in formato Detection2DArray.
        """
        if not self.use_yolo_segmentation or not hasattr(self, 'pub_yolo'):
            return
            
        try:
            det_array = Detection2DArray()
            det_array.header.stamp = stamp.to_msg()
            det_array.header.frame_id = self.camera_optical_frame

            for d in detections:
                detection = Detection2D()
                detection.header = det_array.header
                
                # YOLO coordinates from decode_yolo_segmentation are xc, yc, bw, bh
                # We need to scale them to the publishing resolution (not input resolution)
                # But typically Detection2D expects absolute pixels?
                # Actually, the convention in ROS2 vision_msgs usually follows pixels.
                
                xc, yc, bw, bh = d['box']
                # Scaling based on model input (self.yolo_w, self.yolo_h) to mono_w, mono_h
                scalex = self.mono_w / float(self.yolo_w)
                scaley = self.mono_h / float(self.yolo_h)
                
                bbox = BoundingBox2D()
                bbox.center.position.x = float(xc * scalex)
                bbox.center.position.y = float(yc * scaley)
                bbox.size_x = float(bw * scalex)
                bbox.size_y = float(bh * scaley)
                detection.bbox = bbox
                
                hyp = ObjectHypothesisWithPose()
                lbl = d['class']
                hyp.hypothesis.class_id = str(lbl)
                hyp.hypothesis.score = float(d['conf'])
                
                # Use Italian label if available
                if lbl < len(COCO_CLASS_NAMES):
                    # We can use the description field or just a custom mapping if needed
                    # ROS2 Detection2D doesn't have a direct 'class_name' but we can put it in metadata if needed
                    pass

                detection.results.append(hyp)
                det_array.detections.append(detection)

            self.pub_yolo.publish(det_array)
            
        except Exception as e:
            self.get_logger().error(f"Errore pubblicazione YOLO: {e}")

    def generate_semantic_mask(self, detections, shape):
        """
        Genera maschera 1=scarta.
        """
        h, w = shape
        mask = np.zeros((h, w), dtype=np.uint8)
        
        # 0: person, 14-23: animals
        DYNAMIC = [0, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
        
        for det in detections:
            if det['class'] in DYNAMIC and self.filter_people:
                xc, yc, bw, bh = det['box']
                # Scaling based on configured YOLO input resolution
                mx = int((xc - bw/2) * w / float(self.yolo_w))
                my = int((yc - bh/2) * h / float(self.yolo_h))
                mw = int(bw * w / float(self.yolo_w))
                mh = int(bh * h / float(self.yolo_h))
                cv2.rectangle(mask, (mx, my), (mx+mw, my+mh), 1, -1)
                
        if self.filter_floor:
            mask[int(h*0.75):, :] = 1
            
        return mask

    def extract_stable_superpoint_features(self, nndata, depth_frame, mono_frame, prev_keypoints=None, semantic_mask=None):
        """Estrazione con FILTRI DI STABILITÀ e MASCHERA SEMANTICA"""


        
        
        # 1. Estrazione base
        kpts_raw, scores_raw, desc_raw = self.enhanced_extractor.extract_enhanced_features(
            nndata, depth_frame
        )
        
        if kpts_raw is None or len(kpts_raw) < 5:
            return None, None, None

        # 1.5 ✅ SCALING: Adatta i punti alla risoluzione di mono_frame (es. 640x400)
        shape = mono_frame.shape
        h_mono, w_mono = shape[:2]
        if w_mono != 320 or h_mono != 200:
            scale_x = w_mono / 320.0
            scale_y = h_mono / 200.0
            kpts_raw = kpts_raw * np.array([scale_x, scale_y], dtype=np.float32)
        
        if kpts_raw is None or len(kpts_raw) < 5:
            return None, None, None
        
        # 2. ✅ Ensure grayscale for filters
        if len(shape) == 3:
            mono_proc = cv2.cvtColor(mono_frame, cv2.COLOR_BGR2GRAY)
        else:
            mono_proc = mono_frame

        # 2. ✅ NUOVO: Filtro Anti-Edge (rimuove bordi degli oggetti)
        kpts_raw, desc_raw, scores_raw = self.enhanced_extractor.filter_edge_features(
            kpts_raw, desc_raw, scores_raw, mono_proc, threshold=80
        )
        
        # 3. ✅ NUOVO: Filtro Semantico
        if semantic_mask is not None:
             # semantic_mask è (mono_h, mono_w), i punti sono già scalati a mono_w/h?
             # No, kpts_raw sono ancora 320x200 qui.
             h_mask, w_mask = semantic_mask.shape
             k_mask = np.zeros(len(kpts_raw), dtype=bool)
             for i, (kx, ky) in enumerate(kpts_raw):
                  # Scala coordinate SP (320x200) a Maschera
                  mx = int(kx * w_mask / 320.0)
                  my = int(ky * h_mask / 200.0)
                  if 0 <= mx < w_mask and 0 <= my < h_mask:
                       if semantic_mask[my, mx] > 0:
                            k_mask[i] = True # To reject
             
             # Mantieni solo quelli FUORI dalla maschera
             keep = ~k_mask
             kpts_raw = kpts_raw[keep]
             desc_raw = desc_raw[keep]
             scores_raw = scores_raw[keep]
             
        if len(kpts_raw) == 0:
            return None, None, None

        # 4. ✅ NUOVO: Grid filter con priorità centrale
        kpts_raw, desc_raw, scores_raw = self.enhanced_extractor.grid_filter_keypoints_enhanced(
            kpts_raw, desc_raw, scores_raw, grid_size=24, max_per_cell=1
        )
        
        # 4. Tracking (già presente)
        t_kpts, t_desc, t_ids = self.keypoint_tracker.update(kpts_raw, desc_raw)
        
        if t_kpts is None or len(t_kpts) < 5:
            tracked_kpts = kpts_raw
            tracked_desc = desc_raw
            track_ids = list(range(len(kpts_raw)))
        else:
            tracked_kpts = t_kpts
            tracked_desc = t_desc
            track_ids = t_ids
        
        # 5. ✅ NUOVO: Filtro Consistenza Temporale
        if prev_keypoints is not None and len(prev_keypoints) > 0:
            # Mantieni solo punti vicini a quelli del frame precedente
            consistent = []
            
            for i, kp in enumerate(tracked_kpts):
                # Trova il punto più vicino nel frame precedente
                distances = np.linalg.norm(prev_keypoints - kp, axis=1)
                min_dist = np.min(distances)
                
                # Se il punto ha un "vicino" stabile, mantienilo
                if min_dist < 40:  # Max 40 pixel di movimento
                    consistent.append(i)
            
            if consistent:
                tracked_kpts = tracked_kpts[consistent]
                if tracked_desc is not None:
                    tracked_desc = tracked_desc[consistent]
                track_ids = [track_ids[i] for i in consistent]
        
        # 6. Limite finale
        max_points = 300
        if len(tracked_kpts) > max_points:
            if scores_raw is not None:
                idx = np.argsort(scores_raw)[::-1][:max_points]
            else:
                idx = np.arange(max_points)
            
            tracked_kpts = tracked_kpts[idx]
            if tracked_desc is not None:
                tracked_desc = tracked_desc[idx]
            track_ids = [track_ids[i] for i in idx]
        
        return tracked_kpts, scores_raw, tracked_desc

    def timestamp_to_ros_time(self, dai_timestamp):
        """
        Converte un oggetto timedelta di DepthAI (monotono) in rclpy.time.Time.
        """
        # dai_timestamp è un oggetto datetime.timedelta restituito da getTimestamp()
        seconds = dai_timestamp.total_seconds()
        return rclpy.time.Time(seconds=seconds)

    def publish_camera_info(self, timestamp):
        """Pubblica i messaggi CameraInfo sincronizzati"""
        # Controlla se abbiamo il publisher E camera_info è stato inizializzato
        if hasattr(self, 'pub_camera_info') and self.camera_info is not None:
            self.camera_info.header.stamp = timestamp.to_msg()
            self.camera_info.header.frame_id = self.camera_optical_frame
            self.pub_camera_info.publish(self.camera_info)
        elif hasattr(self, 'pub_camera_info'):
            # Log solo la prima volta per debug
            if not hasattr(self, '_camera_info_warned'):
                self._camera_info_warned = True
                self.get_logger().warn("CameraInfo non ancora inizializzato, attendi calibrazione")


    def signal_handler(self, signum, frame):
        """Gestisce i segnali di terminazione (Ctrl+C)"""
        try:
            signame = signal.Signals(signum).name
        except ValueError:
            signame = signum
        self.get_logger().info(f"Ricevuto segnale {signame}, avvio shutdown...")
        self.request_shutdown()
    
    def request_shutdown(self):
        """Richiede uno shutdown controllato"""
        with self.shutdown_lock:
            if not self.shutdown_requested:
                self.shutdown_requested = True
                self.get_logger().info("Shutdown richiesto, interrompendo operazioni...")
                
                # Avvia shutdown in un thread separato
                shutdown_thread = threading.Thread(target=self.perform_shutdown, daemon=True)
                shutdown_thread.start()
    
    def perform_shutdown(self):
        """Esegue lo shutdown controllato"""
        try:
            self.get_logger().info("Inizio shutdown controllato...")
            
            # 1. Ferma il timer principale
            if hasattr(self, 'main_timer') and self.main_timer:
                try:
                    self.main_timer.cancel()
                    self.logger.debug("Timer principale fermato")
                except Exception as e:
                    self.get_logger().error(f"Errore fermando timer: {e}")
            
            # 2. Chiudi il dispositivo DepthAI
            if DEPTHAI_AVAILABLE and hasattr(self, 'device'):
                try:
                    self.device.close()
                    self.logger.debug("Dispositivo DepthAI chiuso")
                except Exception as e:
                    self.get_logger().error(f"Errore chiusura dispositivo DepthAI: {e}")
            
            # 3. Pulisci buffer
            self.latest_mono_packet = None
            self.latest_depth_packet = None
            self.latest_sp_packet = None
            self.prev_keypoints = None
            self.prev_descriptors = None
            
            # 4. Distruggi il nodo
            self.destroy_node()
            
            self.get_logger().info("Shutdown completato con successo")
            
        except Exception as e:
            self.get_logger().error(f"Errore durante shutdown: {e}")
        finally:
            self.shutdown_complete.set()
    
    def setup_depthai_pipeline(self):
            """Configura pipeline DepthAI - VERSIONE OTTIMIZZATA: Allineamento + Median Filter + YOLO"""
            try:
                pipeline = dai.Pipeline()
                
                # -------------------------------------------------------------------------
                # 1. CAMERAS
                # -------------------------------------------------------------------------
                # Mono Cameras (Left/Right) for Depth
                monoL = pipeline.create(dai.node.MonoCamera)
                monoR = pipeline.create(dai.node.MonoCamera)
                monoL.setBoardSocket(dai.CameraBoardSocket.CAM_B)
                monoR.setBoardSocket(dai.CameraBoardSocket.CAM_C)
                
                # Dynamic Resolution Selection for Mono
                if self.mono_w >= 640:
                    res = dai.MonoCameraProperties.SensorResolution.THE_400_P # 640x400
                else:
                    res = dai.MonoCameraProperties.SensorResolution.THE_400_P
                    
                monoL.setResolution(res)
                monoR.setResolution(res)
                monoL.setFps(self.fps)
                monoR.setFps(self.fps)
                
                # RGB Camera (Center) - Only create if needed or if sp_side is rgb
                rgbCam = None
                if self.sp_side == 'rgb' or self.use_yolo_segmentation:
                    rgbCam = pipeline.create(dai.node.ColorCamera)
                    rgbCam.setBoardSocket(dai.CameraBoardSocket.CAM_A)
                    rgbCam.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
                    rgbCam.setInterleaved(True) # Interleaved for Host preview/processing
                    rgbCam.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
                    rgbCam.setFps(self.fps)
                    # Video size matches processing size (or close to it)
                    # rgbCam.setVideoSize(640, 400) # Aspect ratio match might be needed
                    # rgbCam.setPreviewSize(320, 320) # Standard YOLO input size often squared

                # -------------------------------------------------------------------------
                # 2. STEREO DEPTH
                # -------------------------------------------------------------------------
                stereo = pipeline.create(dai.node.StereoDepth)
                stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.DEFAULT)
                
                # Alignment
                if self.sp_side == 'rgb':
                     stereo.setDepthAlign(dai.CameraBoardSocket.CAM_A)
                     self.get_logger().info("✅ StereoDepth allineato alla camera: RGB (Center)")
                elif self.sp_side == 'left':
                    stereo.setDepthAlign(dai.CameraBoardSocket.CAM_B)
                else:
                    stereo.setDepthAlign(dai.CameraBoardSocket.CAM_C)

                stereo.initialConfig.setMedianFilter(dai.MedianFilter.KERNEL_7x7)
                
                config = stereo.initialConfig.get()
                config.algorithmControl.enableLeftRightCheck = True
                config.algorithmControl.enableSubpixel = False 
                config.costMatching.confidenceThreshold = 200
                config.postProcessing.thresholdFilter.minRange = 200
                config.postProcessing.thresholdFilter.maxRange = 10000
                stereo.initialConfig.set(config)

                monoL.out.link(stereo.left)
                monoR.out.link(stereo.right)

                # -------------------------------------------------------------------------
                # 3. SOURCE SELECTION FOR SUPERPOINT
                # -------------------------------------------------------------------------
                sp_input_source = None # This will be the node output we feed to SP/Manip
                
                if self.sp_side == 'rgb':
                     # RGB is Color, SP needs Grayscale. 
                     # We use the 'video' output (640x400) or 'isp'.
                     sp_input_source = rgbCam.video
                elif self.sp_side == 'left':
                     sp_input_source = stereo.rectifiedLeft
                else:
                     sp_input_source = stereo.rectifiedRight
                     
                # -------------------------------------------------------------------------
                # 4. YOLO INTEGRATION
                # -------------------------------------------------------------------------
                if self.use_yolo_segmentation and self.yolo_blob_path:
                    # Resize for YOLO (typically 640x640 or 320x320 depending on model)
                    # yolov8n-seg is usually 640x640. Ideally we check blob config.
                    # Assuming 320x320 or 640x640. Using 640x640 is safer for accuracy but heavier.
                    # Let's assume standard 640x640 for V8.
                    
                    manipYolo = pipeline.create(dai.node.ImageManip)
                    manipYolo.initialConfig.setResize(self.yolo_w, self.yolo_h)
                    manipYolo.initialConfig.setKeepAspectRatio(False) # Squash
                    manipYolo.initialConfig.setFrameType(dai.RawImgFrame.Type.BGR888p) # Planar needed for NN? Usually yes.
                    
                    # Link source (RGB preferred if available, else Mono converted)
                    if rgbCam:
                         rgbCam.video.link(manipYolo.inputImage) # Use full video stream to avoid preview crop
                    else:
                         # Use Mono converted to frame if no RGB? Standard YOLO needs color usually.
                         # If running YOLO on Mono, accuracy drops significantly. 
                         # We forced RGB creation if usage is enabled.
                         pass

                    yoloNN = pipeline.create(dai.node.YoloDetectionNetwork)
                    yoloNN.setBlobPath(self.yolo_blob_path)
                    
                    # YOLOv6 Configuration
                    yoloNN.setNumClasses(80)
                    yoloNN.setCoordinateSize(4)
                    yoloNN.setIouThreshold(0.5)
                    yoloNN.setConfidenceThreshold(self.yolo_confidence_threshold)
                    
                    # YOLOv6 (r1/r2) uses different output formats. 
                    # For DepthAI YoloDetectionNetwork, we specify the subtype if possible.
                    # Note: 2.31.0 supports YOLOv6 decoding in the node.
                    try:
                        # Some versions of depthai use setAnchorMasks/setAnchors, 
                        # but anchorless models like YOLOv6 often don't need them if the blob is correct.
                        pass
                    except: pass
                    
                    yoloNN.setAnchors([])
                    yoloNN.setAnchorMasks({})
                    
                    manipYolo.out.link(yoloNN.input)
                    
                    xoutYolo = pipeline.create(dai.node.XLinkOut)
                    xoutYolo.setStreamName('yolo_out')
                    yoloNN.out.link(xoutYolo.input)
                    self.get_logger().info(f"✅ Pipeline YOLO conf: {self.yolo_blob_path}")

                # -------------------------------------------------------------------------
                # 5. OUTPUTS SETUP
                # -------------------------------------------------------------------------
                
                # Depth Output
                if self.publish_depth or self.publish_depth_normalized:
                    manipDepth = pipeline.create(dai.node.ImageManip)
                    # If RGB is used, depth is aligned to RGB (Video size). 
                    # We resize to our processing size (320x200 or custom)
                    manipDepth.initialConfig.setResize(self.depth_w, self.depth_h)
                    manipDepth.initialConfig.setFrameType(dai.RawImgFrame.Type.RAW16)
                    stereo.depth.link(manipDepth.inputImage)
                    
                    xoutDepth = pipeline.create(dai.node.XLinkOut)
                    xoutDepth.setStreamName('depth')
                    manipDepth.out.link(xoutDepth.input)

                # Mono/Color Output (Visual Odometry & Tracking Input)
                if self.publish_mono:
                    manipMono = pipeline.create(dai.node.ImageManip)
                    manipMono.initialConfig.setResize(self.mono_w, self.mono_h)
                    
                    # If source is RGB, we want Grayscale for VO?
                    # Or we publish Color and convert internally?
                    # Let's publish what the Camera is. 
                    # BUT SuperPoint needs Grayscale.
                    
                    # Stream 1: Visualization (Color if RGB, Gray if Mono)
                    if self.sp_side == 'rgb':
                        # Convert to BGR for publishing/display
                        manipMono.initialConfig.setFrameType(dai.RawImgFrame.Type.BGR888p) 
                    else:
                        manipMono.initialConfig.setFrameType(dai.RawImgFrame.Type.RAW8)

                    sp_input_source.link(manipMono.inputImage)
                    
                    xoutMono = pipeline.create(dai.node.XLinkOut)
                    xoutMono.setStreamName('mono')
                    manipMono.out.link(xoutMono.input)


                # 6. SUPERPOINT PIPELINE (VPU)
                if self.execution_provider == 'VPU' and self.publish_features and self.sp_blob:
                    
                    manipSP = pipeline.create(dai.node.ImageManip)
                    manipSP.initialConfig.setResize(self.proc_w, self.proc_h)
                    manipSP.initialConfig.setKeepAspectRatio(False)
                    
                    # SuperPoint expects Gray 320x200
                    # If input is RGB, ImageManip can convert? 
                    # Yes, FrameType RAW8 should act as luminance/gray extraction.
                    manipSP.initialConfig.setFrameType(dai.RawImgFrame.Type.RAW8)
                    
                    sp_input_source.link(manipSP.inputImage)
                    
                    nnSP = pipeline.create(dai.node.NeuralNetwork)
                    nnSP.setBlobPath(self.sp_blob)
                    manipSP.out.link(nnSP.input)
                    
                    xoutSP = pipeline.create(dai.node.XLinkOut)
                    xoutSP.setStreamName('sp_out')
                    nnSP.out.link(xoutSP.input)

                # IMU
                if self.use_imu:
                    imu = pipeline.create(dai.node.IMU)
                    imu.enableIMUSensor([dai.IMUSensor.ACCELEROMETER_RAW, dai.IMUSensor.GYROSCOPE_RAW], 100)
                    imu.setBatchReportThreshold(1)
                    imu.setMaxBatchReports(10)
                    
                    xoutImu = pipeline.create(dai.node.XLinkOut)
                    xoutImu.setStreamName('imu')
                    imu.out.link(xoutImu.input)

                # 7. Avvio Device
                self.device = dai.Device(pipeline)
                self.setup_real_calibration(self.device)
                
                # Output Queues
                if self.publish_depth:
                    self.q_depth = self.device.getOutputQueue('depth', 8, False)
                if self.publish_mono:
                    self.q_mono = self.device.getOutputQueue('mono', 8, False)
                if self.execution_provider == 'VPU' and self.publish_features and self.sp_blob:
                    self.q_sp = self.device.getOutputQueue('sp_out', 8, False)
                if self.use_yolo_segmentation:
                    self.q_yolo = self.device.getOutputQueue('yolo_out', 4, False)
                if self.use_imu:
                    self.q_imu = self.device.getOutputQueue('imu', 50, False)
                
                self.get_logger().info(f"✅ Pipeline avviata. Camera: {self.sp_side}, Provider: {self.execution_provider}")
                
            except Exception as e:
                self.get_logger().error(f"Errore configurazione DepthAI: {e}")
                raise
    
    def setup_simulation(self):
            """Setup per modalità simulazione con inizializzazione stati per evitare crash"""
            self.get_logger().warn("🚀 MODALITÀ SIMULAZIONE ATTIVA - Generazione dati fittizi")
            
            # Inizializziamo i pacchetti a None per evitare AttributeError
            self.latest_mono_packet = None
            self.latest_depth_packet = None
            self.latest_sp_packet = None
            
            # Se il codice usa le code per i check, le mettiamo a None
            self.q_depth = self.q_mono = self.q_sp = self.q_imu = None
            
            # Generiamo una camera matrix di default per permettere l'avvio del PnP (anche se non farà nulla)
            self.generate_default_camera_info()
            
            # Esempio: potresti caricare un'immagine statica qui se volessi testare il PnP in loop
    
    def load_camera_info(self, filepath):
        """Carica info camera da file YAML con auto-scaling per diverse risoluzioni"""
        try:
            with open(filepath, 'r') as f:
                calib_data = yaml.safe_load(f)
            
            if 'camera_matrix' in calib_data:
                # 1. Leggi risoluzione originale dal file
                orig_w = calib_data.get('image_width', self.mono_w)
                orig_h = calib_data.get('image_height', self.mono_h)
                
                # 2. Calcola i fattori di scala (es. se file è 640 e noi siamo a 320, scale = 0.5)
                scale_x = self.mono_w / orig_w
                scale_y = self.mono_h / orig_h

                self.camera_info = CameraInfo()
                self.camera_info.width = self.mono_w
                self.camera_info.height = self.mono_h
                self.camera_info.header.frame_id = self.camera_optical_frame
                
                # 3. Scala la Matrice K (focali e centri ottici)
                k = calib_data['camera_matrix']['data']
                scaled_k = [
                    k[0] * scale_x, k[1],           k[2] * scale_x,
                    k[3],           k[4] * scale_y, k[5] * scale_y,
                    k[6],           k[7],           k[8]
                ]
                self.camera_info.k = scaled_k
                self.camera_matrix = np.array(scaled_k).reshape(3, 3)
                
                # 4. Distorsione (non cambia con la scala per il modello plumb_bob)
                self.camera_info.d = calib_data['distortion_coefficients']['data']
                self.dist_coeffs = np.array(self.camera_info.d)
                
                # 5. Scala la Matrice P (Proiezione)
                if 'projection_matrix' in calib_data:
                    p = calib_data['projection_matrix']['data']
                    self.camera_info.p = [
                        p[0] * scale_x, p[1],           p[2] * scale_x, p[3] * scale_x,
                        p[4],           p[5] * scale_y, p[6] * scale_y, p[7] * scale_y,
                        p[8],           p[9],           p[10],          p[11]
                    ]
                
                self.camera_info.r = calib_data.get('rectification_matrix', {'data': [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0]})['data']
                self.camera_info.distortion_model = calib_data.get('distortion_model', 'plumb_bob')
                
                self.get_logger().info(f"✅ Info camera scalate ({orig_w}x{orig_h} -> {self.mono_w}x{self.mono_h})")
            else:
                self.generate_default_camera_info()
                
        except Exception as e:
            self.get_logger().error(f"❌ Errore caricamento camera: {e}")
            self.generate_default_camera_info()
    
    def generate_default_camera_info(self):
        """
        Genera CameraInfo per OAK-D Lite a 320x200 (Landscape).
        Calcola i parametri ottici scalati dai 400P (640x400) nativi.
        """
        self.camera_info = CameraInfo()
        
        # 1. FORZATURA DIMENSIONI
        # Se i valori arrivano invertiti (es. 200x320), li sistemiamo qui
        width = max(self.mono_w, self.mono_h)
        height = min(self.mono_w, self.mono_h)
        
        self.camera_info.width = width
        self.camera_info.height = height
        self.camera_info.header.frame_id = self.camera_optical_frame
        
        # 2. CALCOLO FOCALE (Basato su calibrazione tipica OAK-D Lite)
        # FOV Orizzontale ~71.9°. Formula: fx = (W/2) / tan(HFOV/2)
        # Per 640x400 (400P), fx/fy tipici sono ~441.25
        ref_w, ref_h = 640.0, 400.0
        ref_f = 441.25
        
        # Scaling coerente con la risoluzione attuale
        scale_x = width / ref_w
        scale_y = height / ref_h
        
        fx = ref_f * scale_x
        fy = ref_f * scale_y
        
        # Centro ottico (assunto a metà immagine per il default)
        cx = width / 2.0
        cy = height / 2.0
        
        # 3. ASSEGNAZIONE MATRICI ROS 2
        # K: Matrice Intrinseca (3x3)
        self.camera_info.k = [
            fx,  0.0, cx, 
            0.0, fy,  cy, 
            0.0, 0.0, 1.0
        ]
        
        # D: Distorsione (0.0 perché usiamo output rettificato dalla OAK-D)
        self.camera_info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
        self.camera_info.distortion_model = 'plumb_bob'
        
        # R: Rettificazione (Identità)
        self.camera_info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]

        # P: Proiezione (3x4)
        self.camera_info.p = [
            fx,  0.0, cx,  0.0, 
            0.0, fy,  cy,  0.0, 
            0.0, 0.0, 1.0, 0.0
        ]

        # 4. CACHE PER USO INTERNO (Evitiamo conversioni continue nel loop)
        self.camera_matrix = np.array(self.camera_info.k).reshape(3, 3).astype(np.float32)
        self.dist_coeffs = np.array(self.camera_info.d).astype(np.float32)

        self.get_logger().info(
            f"📸 Camera Info Configurata: {width}x{height} | "
            f"fx:{fx:.1f} cx:{cx:.1f} cy:{cy:.1f}"
        )
    
    def fix_camera_info_issue(self):
        """Verifica consistenza della configurazione CameraInfo"""
        
        # Frame ID sono ora corretti e standard ROS (definiti in __init__)
        # Non modificarli qui per mantenere coerenza globale
        
        # Assicurati che CameraInfo sia correttamente inizializzato
        if not hasattr(self, 'camera_info') or self.camera_info is None:
            self.generate_default_camera_info()
            self.get_logger().warn("CameraInfo non inizializzato, generato default")
        
        # 3. Imposta il frame_id corretto
        self.camera_info.header.frame_id = self.camera_optical_frame
        
        # 4. Crea un publisher se non esiste
        if not hasattr(self, 'pub_camera_info') and self.publish_camera_info:
            self.pub_camera_info = self.create_publisher(
                CameraInfo, '/camera/camera_info', 10
            )
            self.get_logger().info("✅ Publisher CameraInfo creato")

    def publish_camera_info_fixed(self, timestamp):
        """Versione corretta per pubblicare CameraInfo"""
        if not hasattr(self, 'pub_camera_info') or self.pub_camera_info is None:
            self.get_logger().error("Publisher CameraInfo non disponibile!")
            return
        
        if self.camera_info is None:
            self.get_logger().error("CameraInfo non inizializzato!")
            return
        
        # Copia il messaggio per evitare modifiche condivise
        camera_info_msg = CameraInfo()
        # Corretto: usa il tempo attuale di ROS 2 (System Time) anziché timestamp della camera
        camera_info_msg.header.stamp = self.get_clock().now().to_msg()
        camera_info_msg.header.frame_id = self.camera_optical_frame
        
        # Copia tutti i campi
        camera_info_msg.width = self.camera_info.width
        camera_info_msg.height = self.camera_info.height
        camera_info_msg.distortion_model = self.camera_info.distortion_model
        camera_info_msg.d = list(self.camera_info.d)  # Converti in lista
        camera_info_msg.k = list(self.camera_info.k)  # Converti in lista
        camera_info_msg.r = list(self.camera_info.r)  # Converti in lista
        camera_info_msg.p = list(self.camera_info.p)  # Converti in lista
        
        # Debug
        self.logger.debug(
            f"📷 Pubblico CameraInfo: "
            f"frame_id={camera_info_msg.header.frame_id}, "
            f"size={camera_info_msg.width}x{camera_info_msg.height}, "
            f"K[0]={camera_info_msg.k[0]:.1f}"
        )
        
        self.pub_camera_info.publish(camera_info_msg)

    def debug_image_dimensions(self, frame, name):
        """Debug per verificare dimensioni immagini"""
        if frame is None:
            self.get_logger().error(f"{name}: frame è None")
            return
        
        self.get_logger().info(f"📏 {name}: shape={frame.shape}, dtype={frame.dtype}")
        
        # Controlla orientamento
        height, width = frame.shape[0], frame.shape[1]
        
        if width == self.mono_w and height == self.mono_h:
            self.get_logger().info(f"   ✅ Dimensioni CORRETTE: {width}x{height} (width x height)")
        elif width == self.mono_h and height == self.mono_w:
            self.get_logger().error(f"   ❌ Dimensioni INVERTITE: {width}x{height} invece di {self.mono_w}x{self.mono_h}!")
            # Ruota l'immagine
            rotated = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            self.get_logger().info(f"   🔄 Immagine ruotata a {rotated.shape[1]}x{rotated.shape[0]}")
            return rotated
        else:
            self.get_logger().warn(f"   ⚠️  Dimensioni inattese: {width}x{height} (Atteso: {self.mono_w}x{self.mono_h})")
        
        return frame



    
    def extract_superpoint_features(self, nndata, depth_frame=None):
        """Estrai keypoint e descrittori con tutti i filtri applicati"""
        """Estrai keypoint e descrittori con tutti i filtri applicati"""
        """Estrazione SuperPoint con stabilizzazione temporale"""
        """Estrai keypoint e descrittori con tutti i filtri applicati"""
        try:
            # CORREZIONE: usa l'estrattore enhanced invece di chiamare se stesso ricorsivamente
            keypoints, scores, descriptors = self.enhanced_extractor.extract_enhanced_features(
                nndata, depth_frame
            )
            
            if keypoints is None or len(keypoints) == 0:
                return None, None, None
            
            # Applica filtri leggeri
            keypoints, descriptors, scores = self.filter_border_keypoints(
                keypoints, descriptors, scores, border=12
            )
            
            if len(keypoints) == 0:
                return None, None, None
            
            # Grid filter per distribuzione uniforme
            keypoints, descriptors, scores = self.grid_filter_keypoints(
                keypoints, descriptors, scores, grid_size=16, max_per_cell=4
            )
            
            if len(keypoints) == 0:
                return None, None, None
            
            # Filtro depth se disponibile
            if depth_frame is not None:
                keypoints, descriptors, scores = self.filter_keypoints_by_depth(
                    keypoints, descriptors, scores, depth_frame
                )
            
            return keypoints, scores, descriptors
            
        except Exception as e:
            self.get_logger().error(f"Errore estrazione features: {e}")
            import traceback
            self.get_logger().error(traceback.format_exc())
            return None, None, None



    def filter_border_keypoints(self, keypoints, descriptors, scores, border=12):
        """
        🥇 FIX 1 — Soppressione sui bordi (OBBLIGATORIO)
        Elimina keypoint vicini ai bordi dell'immagine
        """
        """
        Filtro bordi rilassato per mantenere più punti.
        """
        w = self.proc_w
        h = self.proc_h
        
        mask = (
            (keypoints[:, 0] >= border) &
            (keypoints[:, 0] < w - border) &
            (keypoints[:, 1] >= border) &
            (keypoints[:, 1] < h - border)
        )
        
        if np.any(mask):
            self.logger.debug(f"Filtro bordi ({border}px): {len(keypoints)} -> {np.sum(mask)} keypoints")
            return keypoints[mask], descriptors[mask], scores[mask]
        else:
            return np.array([]), np.array([]), np.array([])

    def grid_filter_keypoints(self, keypoints, descriptors, scores, grid_size=16, max_per_cell=4):
        """
        🥈 FIX 2 — Grid-based keypoint selection (FONDAMENTALE)
        Distribuisce i keypoint uniformemente nell'immagine
        """
        # Più celle, più punti
        """
        Grid filter rilassato.
        """
        w = self.proc_w
        h = self.proc_h
        
        if len(keypoints) == 0:
            return keypoints, descriptors, scores
        
        # Calcola dimensioni cella
        cell_w = w / grid_size
        cell_h = h / grid_size
        
        keep_indices = []
        
        for gx in range(grid_size):
            for gy in range(grid_size):
                # Trova keypoints in questa cella
                in_cell = np.where(
                    (keypoints[:, 0] >= gx * cell_w) &
                    (keypoints[:, 0] < (gx + 1) * cell_w) &
                    (keypoints[:, 1] >= gy * cell_h) &
                    (keypoints[:, 1] < (gy + 1) * cell_h)
                )[0]
                
                if len(in_cell) == 0:
                    continue
                
                # Prendi i migliori punti per score
                if len(in_cell) > max_per_cell:
                    # Ordina per score (decrescente) e prendi i primi max_per_cell
                    sorted_indices = in_cell[np.argsort(scores[in_cell])[::-1]]
                    best = sorted_indices[:max_per_cell]
                    keep_indices.extend(best)
                else:
                    keep_indices.extend(in_cell)
        
        if keep_indices:
            keep_indices = np.array(keep_indices)
            self.logger.debug(f"Grid filter ({grid_size}x{grid_size}): {len(keypoints)} -> {len(keep_indices)} keypoints")
            return keypoints[keep_indices], descriptors[keep_indices], scores[keep_indices]
        else:
            return np.array([]), np.array([]), np.array([])

    def sample_depth_at_coords(self, depth_frame, x, y):
        """
        Campiona la profondità scalando le coordinate se le risoluzioni differiscono.
        """
        try:
            h_d, w_d = depth_frame.shape[:2]
            
            # Scale coordinates from processing resolution (SP) to depth resolution
            # self.proc_w/h are the SP input dimensions (e.g. 480x360)
            sx = w_d / self.proc_w
            sy = h_d / self.proc_h
            
            dx = int(round(x * sx))
            dy = int(round(y * sy))
            
            if 0 <= dx < w_d and 0 <= dy < h_d:
                return depth_frame[dy, dx]
            return 0
        except:
            return 0

    def filter_keypoints_by_depth(self, keypoints, descriptors, scores, depth_frame):
        """
        🥉 FIX 4 — Rimuovi keypoint senza depth valida
        Filtra punti su superfici lucide, pareti lontane, zone senza depth affidabile
        """
        if len(keypoints) == 0 or depth_frame is None:
            return keypoints, descriptors, scores
        
        valid_indices = []
        
        for i, (x, y) in enumerate(keypoints):
            # Leggi depth (in mm) con scaling corretto
            d = self.sample_depth_at_coords(depth_frame, x, y)
            
            # Controlla se depth è valida (20cm - 8m)
            if 300 < d < 8000:  # mm
                # Controllo aggiuntivo: variazione nell'area attorno al punto scalato
                h_d, w_d = depth_frame.shape[:2]
                sx, sy = w_d / self.proc_w, h_d / self.proc_h
                dx, dy = int(round(x * sx)), int(round(y * sy))
                
                y_min = max(0, dy - 1)
                y_max = min(h_d, dy + 2)
                x_min = max(0, dx - 1)
                x_max = min(w_d, dx + 2)
                
                patch = depth_frame[y_min:y_max, x_min:x_max]
                if patch.size > 0:
                    # Rimuovi zeri dal patch
                    patch_nonzero = patch[patch > 0]
                    if patch_nonzero.size > 0:
                        std_depth = np.std(patch_nonzero)
                        # Se la profondità è troppo variabile (probabile bordo), scarta
                        if std_depth < 150:  # 15cm di deviazione standard max
                            valid_indices.append(i)
        
        if valid_indices:
            valid_indices = np.array(valid_indices)
            self.logger.debug(f"Depth filter: {len(keypoints)} -> {len(valid_indices)} keypoints")
            return keypoints[valid_indices], descriptors[valid_indices], scores[valid_indices]
        else:
            return np.array([]), np.array([]), np.array([])




    def match_with_flann(self, desc_prev, desc_curr):
            """
            Matching ottimizzato per SuperPoint con Cross-Check simulato e Ratio Test dinamico.
            """
            """
            Matching ottimizzato per SuperPoint con Fallback Robusto.
            """
            """
            Matching robusto con BFMatcher e controlli di qualità migliorati.
            Ritorna matches validi per odometria visuale.
            """
            # 1. Controlli di sicurezza
            if desc_prev is None or desc_curr is None:
                self.get_logger().warn("Descrittori None in match_with_flann")
                return []
            
            # Controlla che i descrittori abbiano la forma corretta
            if len(desc_prev.shape) != 2 or len(desc_curr.shape) != 2:
                self.get_logger().warn(f"Forma descrittori errata: prev={desc_prev.shape}, curr={desc_curr.shape}")
                return []
            
            # Controlla numero minimo di punti
            if len(desc_prev) < 5 or len(desc_curr) < 5:
                self.logger.debug(f"Troppi pochi descrittori: prev={len(desc_prev)}, curr={len(desc_curr)}")
                return []
            
            # 2. Conversione a float32 (obbligatoria per OpenCV)
            try:
                d1 = desc_prev.astype(np.float32)
                d2 = desc_curr.astype(np.float32)
            except Exception as e:
                self.get_logger().error(f"Errore conversione descrittori: {e}")
                return []
            
            # 3. Normalizzazione L2 (SuperPoint dovrebbe già essere normalizzato, ma rifacciamolo)
            norm1 = np.linalg.norm(d1, axis=1, keepdims=True)
            norm2 = np.linalg.norm(d2, axis=1, keepdims=True)
            
            # Evita divisioni per zero
            norm1[norm1 < 1e-6] = 1.0
            norm2[norm2 < 1e-6] = 1.0
            
            d1 = d1 / norm1
            d2 = d2 / norm2
            
            # 4. BFMatcher con Cross-Check (massima robustezza)
            try:
                # Usa NORM_L2 per descrittori SuperPoint (floating point)
                bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=True)
                
                # Esegui il matching
                raw_matches = bf.match(d1, d2)
                
                if len(raw_matches) == 0:
                    self.logger.debug("BFMatcher: zero matches trovati")
                    return []
                
                # 5. Filtraggio qualità
                good_matches = []
                
                # Calcola statistiche distanze
                distances = [m.distance for m in raw_matches]
                avg_dist = np.mean(distances)
                std_dist = np.std(distances)
                
                # Filtra per distanza ragionevole (per vettori L2 normalizzati)
                # Una distanza tipica per match buoni è < 0.7
                distance_threshold = min(0.8, avg_dist + std_dist)
                
                for m in raw_matches:
                    if m.distance < distance_threshold:
                        good_matches.append(m)
                
                # 6. Ordina per qualità (distanza minore = migliore)
                if good_matches:
                    good_matches = sorted(good_matches, key=lambda x: x.distance)
                    
                    # Limita il numero massimo di match per performance
                    max_good_matches = 200
                    if len(good_matches) > max_good_matches:
                        good_matches = good_matches[:max_good_matches]
                
                # 7. Log diagnostico
                self.logger.debug(
                    f"Matching: {len(desc_prev)}->{len(desc_curr)} "
                    f"| Raw: {len(raw_matches)} | Good: {len(good_matches)} "
                    f"| AvgDist: {avg_dist:.3f} | Thr: {distance_threshold:.3f}"
                )
                
                return good_matches
                
            except cv2.error as e:
                self.get_logger().error(f"Errore OpenCV in BFMatcher: {e}")
                return []
            except Exception as e:
                self.get_logger().error(f"Errore inatteso in matching: {e}")
                return []
    
    
    def try_alternative_matching(self, keypoints, descriptors):
        """Prova metodi alternativi di matching per debug"""
        if self.prev_descriptors is None or descriptors is None:
            return
        
        try:
            # Metodo 1: BFMatcher senza cross-check ma con ratio test
            bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
            knn_matches = bf.knnMatch(self.prev_descriptors, descriptors, k=2)
            
            # Lowe's ratio test
            good_matches = []
            for match_pair in knn_matches:
                if len(match_pair) < 2:
                    continue
                m, n = match_pair
                if m.distance < 0.7 * n.distance:
                    good_matches.append(m)
            
            self.get_logger().info(f"ALTERNATIVE: BF KNN ratio test trovato {len(good_matches)} matches")
            
            # Metodo 2: Matching semplice per distanza
            if len(good_matches) == 0:
                # Calcola distanze tra tutti i descrittori
                distances = np.linalg.norm(
                    self.prev_descriptors[:, np.newaxis] - descriptors[np.newaxis, :], 
                    axis=2
                )
                
                # Trova i match più vicini
                min_distances = np.min(distances, axis=1)
                match_indices = np.argmin(distances, axis=1)
                
                # Filtra per distanza massima
                max_distance = 0.5  # Soglia per SuperPoint normalizzato
                valid_matches = min_distances < max_distance
                
                self.get_logger().info(f"ALTERNATIVE: Matching diretto trovato {np.sum(valid_matches)} matches")
                self.get_logger().info(f"ALTERNATIVE: Distanze minime: media={np.mean(min_distances):.3f}, min={np.min(min_distances):.3f}, max={np.max(min_distances):.3f}")
                
        except Exception as e:
            self.get_logger().error(f"Errore in alternative matching: {e}")
    
    def debug_matching_issue(self, desc_prev, desc_curr):
        """Funzione di debug per investigare problemi di matching"""
        if desc_prev is None or desc_curr is None:
            return "Descrittori None"
        
        info = []
        info.append(f"prev_shape: {desc_prev.shape}, curr_shape: {desc_curr.shape}")
        info.append(f"prev_dtype: {desc_prev.dtype}, curr_dtype: {desc_curr.dtype}")
        
        # Controlla valori NaN o infiniti
        if np.any(np.isnan(desc_prev)):
            info.append("NaN in desc_prev")
        if np.any(np.isnan(desc_curr)):
            info.append("NaN in desc_curr")
        
        if np.any(np.isinf(desc_prev)):
            info.append("Inf in desc_prev")
        if np.any(np.isinf(desc_curr)):
            info.append("Inf in desc_curr")
        
        # Controlla norma
        if len(desc_prev) > 0:
            norm_sample = np.linalg.norm(desc_prev[0])
            info.append(f"norma primo desc_prev: {norm_sample:.3f}")
        
        if len(desc_curr) > 0:
            norm_sample = np.linalg.norm(desc_curr[0])
            info.append(f"norma primo desc_curr: {norm_sample:.3f}")
        
        return " | ".join(info)

    def matrix_to_quaternion(self, matrix):
            """
            Converte una matrice di trasformazione 4x4 o rotazione 3x3 in quaternione (x, y, z, w).
            Ottimizzata per stabilità numerica e performance.
            """
            # Estrai la rotazione 3x3 (funziona sia con matrici 3x3 che 4x4)
            R = matrix[:3, :3]
            
            # Algoritmo di Shepperd robusto
            tr = np.trace(R)
            
            if tr > 0:
                S = np.sqrt(tr + 1.0) * 2 # S = 4 * qw
                qw = 0.25 * S
                qx = (R[2, 1] - R[1, 2]) / S
                qy = (R[0, 2] - R[2, 0]) / S
                qz = (R[1, 0] - R[0, 1]) / S
            elif (R[0, 0] > R[1, 1]) and (R[0, 0] > R[2, 2]):
                S = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2 # S = 4 * qx
                qw = (R[2, 1] - R[1, 2]) / S
                qx = 0.25 * S
                qy = (R[0, 1] + R[1, 0]) / S
                qz = (R[0, 2] + R[2, 0]) / S
            elif R[1, 1] > R[2, 2]:
                S = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2 # S = 4 * qy
                qw = (R[0, 2] - R[2, 0]) / S
                qx = (R[0, 1] + R[1, 0]) / S
                qy = 0.25 * S
                qz = (R[1, 2] + R[2, 1]) / S
            else:
                S = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2 # S = 4 * qz
                qw = (R[1, 0] - R[0, 1]) / S
                qx = (R[0, 2] + R[2, 0]) / S
                qy = (R[1, 2] + R[2, 1]) / S
                qz = 0.25 * S

            # Normalizzazione finale (Indispensabile per ROS 2 / RViz)
            q = np.array([qx, qy, qz, qw])
            norm = np.linalg.norm(q)
            
            if norm < 1e-6:
                # Fallback in caso di matrice degenere
                return 0.0, 0.0, 0.0, 1.0
                
            return tuple(q / norm)
    


    def publish_odometry(self, current_pose, timestamp, inlier_ratio):
        """Pubblica l'odometria"""
        # Usa current_pose invece di transform
        if current_pose is None:
            self.get_logger().warn("⚠️ Nessuna trasformazione valida da pubblicare")
            return
        
        # 1. Validazione della trasformazione
        # Estrai rvec e tvec dalla matrice per la validazione
        R = current_pose[:3, :3]
        tvec = current_pose[:3, 3]
        
        # Converti R in rvec (Rodrigues)
        rvec, _ = cv2.Rodrigues(R)
        
        # Valida la trasformazione - USANDO odometry_system
        if not self.odometry_system._validate_transformation(rvec, tvec, inlier_ratio):
            self.get_logger().warn(f"❌ Trasformazione non valida, skip odometria")
            return
        
        # 2. Smoothing della trasformazione (se necessario)
        if hasattr(self.odometry_system, '_smooth_transform'):
            current_pose_smoothed = self.odometry_system._smooth_transform(current_pose)
        else:
            current_pose_smoothed = current_pose
        
        # 3. Aggiorna odometria accumulata - USANDO odometry_system
        if not self.odometry_system.update_odometry(current_pose_smoothed, inlier_ratio):
            self.get_logger().warn("⚠️ Fallito aggiornamento odometria")
            return
        
        # 4. Pubblica messaggio Odometry
        try:
            odom_msg = Odometry()
            odom_msg.header.stamp = timestamp.to_msg()
            odom_msg.header.frame_id = "odom"
            odom_msg.child_frame_id = self.base_frame
            
            # Posizione dall'accumulato - USANDO odometry_system
            position = self.odometry_system.transform_accumulated[:3, 3]
            odom_msg.pose.pose.position.x = float(position[0])
            odom_msg.pose.pose.position.y = float(position[1])
            odom_msg.pose.pose.position.z = float(position[2])
            
            # Orientamento dall'accumulato - USANDO odometry_system
            R_accum = self.odometry_system.transform_accumulated[:3, :3]
            qx, qy, qz, qw = self.matrix_to_quaternion(R_accum)
            
            odom_msg.pose.pose.orientation.x = qx
            odom_msg.pose.pose.orientation.y = qy
            odom_msg.pose.pose.orientation.z = qz
            odom_msg.pose.pose.orientation.w = qw
            
            # Covarianza (puoi regolarla in base alla confidenza)
            covariance = 0.1 * (1.0 - inlier_ratio) + 0.01
            odom_msg.pose.covariance = [covariance] * 36
            
            # Pubblica
            self.pub_odom.publish(odom_msg)
            
            self.logger.debug(
                f"📤 Odom pubblicata: pos [{position[0]:.2f}, {position[1]:.2f}, {position[2]:.2f}] | "
                f"Inlier: {inlier_ratio:.2f}"
            )
            
        except Exception as e:
            self.get_logger().error(f"Errore pubblicazione odometria: {e}")
            # Fallback critico: se non esiste, crealo al volo (ma e' un bug logico)
            if not hasattr(self, 'pub_odom'):
                 self.pub_odom = self.create_publisher(Odometry, '/odom', 10)
            return
        
        # 5. Pubblica TF - USANDO odometry_system
        self.publish_odometry_tf(self.odometry_system.transform_accumulated, timestamp)



    def rotation_matrix_to_euler(self, R):
            """
            Converti matrice di rotazione in angoli di Eulero (Roll, Pitch, Yaw).
            Sequenza: ZYX (standard ROS).
            """
            # Calcoliamo la norma della prima colonna/riga per gestire la singolarità
            sy = np.sqrt(R[0, 0]**2 + R[1, 0]**2)
            
            # Tolleranza per Gimbal Lock
            singular = sy < 1e-6
            
            if not singular:
                # Caso normale
                roll  = np.arctan2(R[2, 1], R[2, 2]) # Rotazione attorno a X
                pitch = np.arctan2(-R[2, 0], sy)      # Rotazione attorno a Y
                yaw   = np.arctan2(R[1, 0], R[0, 0]) # Rotazione attorno a Z
            else:
                # Caso Gimbal Lock (pitch è +/- 90 gradi)
                roll  = np.arctan2(-R[1, 2], R[1, 1])
                pitch = np.arctan2(-R[2, 0], sy)
                yaw   = 0
                
            # Restituiamo in gradi per facilità di lettura nel logger
            return np.degrees(roll), np.degrees(pitch), np.degrees(yaw)
    
    def publish_matches_visualization(self, matches, prev_kpts, curr_kpts, timestamp):
            """Visualizza i vettori di movimento in RViz proiettandoli in metri"""
            if not hasattr(self, 'pub_matches_viz') or len(matches) == 0:
                return
            
            poses_msg = PoseArray()
            poses_msg.header.stamp = self.get_clock().now().to_msg()
            poses_msg.header.frame_id = self.camera_optical_frame # 'oak_depth_optical_frame'

            # Parametri per la proiezione visiva
            # Mettiamo i punti a 1.0m di distanza per renderli visibili
            z_plane = 1.0 
            fx = self.camera_matrix[0, 0]
            fy = self.camera_matrix[1, 1]
            cx = self.camera_matrix[0, 2]
            cy = self.camera_matrix[1, 2]

            # Limita il numero di frecce per non saturare RViz (max 100)
            step = max(1, len(matches) // 100)
            
            for i in range(0, len(matches), step):
                match = matches[i]
                if match.queryIdx >= len(prev_kpts) or match.trainIdx >= len(curr_kpts):
                    continue

                # Coordinate pixel
                u0, v0 = prev_kpts[match.queryIdx]
                u1, v1 = curr_kpts[match.trainIdx]

                # Converti Pixel -> Metri (proiezione su piano z=1)
                x0 = (u0 - cx) * z_plane / fx
                y0 = (v0 - cy) * z_plane / fy
                x1 = (u1 - cx) * z_plane / fx
                y1 = (v1 - cy) * z_plane / fy

                pose = Pose()
                # Posizione di partenza della freccia
                pose.position.x = float(x0)
                pose.position.y = float(y0)
                pose.position.z = float(z_plane)

                # Calcolo orientamento verso il punto di arrivo
                dx = x1 - x0
                dy = y1 - y0
                # atan2(y, x) per l'angolo nel piano XY della camera
                angle = math.atan2(dy, dx)
                
                # Quaternione per rotazione attorno all'asse Z della camera
                pose.orientation.z = math.sin(angle / 2.0)
                pose.orientation.w = math.cos(angle / 2.0)

                poses_msg.poses.append(pose)

            self.pub_matches_viz.publish(poses_msg)
    
    def publish_tracked_features(self, keypoints, scores, timestamp):
            """Pubblica feature in formato TrackedFeatures (compatibile con depthai_ros_msgs)"""
            if not HAS_TRACKED_MSG or not hasattr(self, 'pub_features'):
                return
            
            if keypoints is None or len(keypoints) == 0:
                return

            # Header unico per tutto il messaggio
            feats_msg = TrackedFeatures()
            feats_msg.header.stamp = self.get_clock().now().to_msg()
            feats_msg.header.frame_id = self.camera_optical_frame # 'oak_depth_optical_frame'
            
            # Preparazione scores (ottimizzata)
            if scores is None or len(scores) != len(keypoints):
                scores = np.ones(len(keypoints), dtype=np.float32)
            
            # Iterazione efficiente
            for i, ((x, y), score) in enumerate(zip(keypoints, scores)):
                tf = TrackedFeature()
                
                # ID: In un tracker vero questo dovrebbe persistere tra i frame.
                # Qui usiamo l'indice per indicare la posizione nel buffer corrente.
                tf.id = i 
                
                # NOTA: Pubblichiamo in coordinate PIXEL. 
                # Assicurarsi che i nodi a valle conoscano la risoluzione 320x200.
                tf.position.x = float(x)
                tf.position.y = float(y)
                tf.position.z = 0.0
                
                tf.age = 0 # Feature appena estratta
                tf.harris_score = float(score) # Score di confidenza SuperPoint
                tf.tracking_error = 0.0
                
                feats_msg.features.append(tf)
            
            try:
                self.pub_features.publish(feats_msg)
            except Exception as e:
                self.get_logger().error(f"Errore pubblicazione feature: {e}")
    
    def publish_rtabmap_features(self, keypoints, descriptors, timestamp):
            """
            Invia feature SuperPoint a RTAB-Map per Loop Closure detection avanzato.
            """
            if not hasattr(self, 'pub_features_rtabmap') or keypoints is None:
                return
            
            try:
                # Import locale per evitare dipendenze se RTAB-Map non è installato
                from rtabmap_msgs.msg import Feature
                from rtabmap_conversions import cv_keypoints_to_ros_features # Se disponibile
            except ImportError:
                return

            feat_msg = Feature()
            feat_msg.header.stamp = self.get_clock().now().to_msg()
            feat_msg.header.frame_id = self.camera_optical_frame

            # 1. KEYPOINTS: Conversione in lista piatta
            # Nota: RTAB-Map preferisce float per sub-pixel accuracy
            feat_msg.keypoints = keypoints.flatten().astype(np.float32).tolist()

            # 2. DESCRITTORI: Conversione in formato 'Image' (Matrix format)
            if descriptors is not None and len(descriptors) > 0:
                # Assicuriamoci che siano Float32 e normalizzati
                desc_float = descriptors.astype(np.float32)
                
                desc_img = Image()
                desc_img.header = feat_msg.header
                desc_img.height = desc_float.shape[0] # Numero di feature (N)
                desc_img.width = desc_float.shape[1]  # Dimensione descrittore (256)
                desc_img.encoding = '32FC1'
                desc_img.step = desc_float.shape[1] * 4
                desc_img.data = desc_float.tobytes()
                
                feat_msg.descriptors = desc_img
            
            # 3. PUBBLICAZIONE
            try:
                self.pub_features_rtabmap.publish(feat_msg)
            except Exception as e:
                self.get_logger().error(f"Errore RTAB-Map Feature Pub: {e}")
    


    def publish_mono_frame(self, frame, timestamp):
        """Pubblica l'immagine monocromatica - VERSIONE ROBUSTA"""
        if frame is None or not hasattr(self, 'pub_mono'):
            return
        
        try:
            # Assicura che l'immagine sia in formato corretto
            shape = frame.shape
            h, w = shape[:2]
            
            # Debug dimensioni
            if h != self.mono_h or w != self.mono_w:
                if h == self.mono_w and w == self.mono_h:
                    frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
                    h, w = frame.shape[:2]
                else:
                    self.get_logger().debug(f"⚠️ Mono frame dimensioni custom: {w}x{h}")
            
            # Crea messaggio ROS
            msg = Image()
            msg.header.stamp = timestamp.to_msg()
            msg.header.frame_id = self.camera_optical_frame
            msg.height = h
            msg.width = w
            
            if len(frame.shape) == 3:
                msg.encoding = 'bgr8'
                msg.step = w * 3
            else:
                msg.encoding = 'mono8'
                msg.step = w
            
            
            # Assicura che i dati siano nel formato corretto
            if frame.dtype != np.uint8:
                frame = frame.astype(np.uint8)
            
            msg.data = frame.tobytes()
            
            # Pubblica
            self.pub_mono.publish(msg)
            self.get_logger().debug(f"✅ Mono pubblicata: {w}x{h}")
            
        except Exception as e:
            self.get_logger().error(f"❌ Errore pubblicazione mono: {e}")

    def publish_depth_frame(self, depth_frame, timestamp):
        """Pubblica depth RAW - VERSIONE ROBUSTA"""
        if depth_frame is None or not hasattr(self, 'pub_depth'):
            return
        
        try:
            # Converti in uint16
            depth_mm = depth_frame.astype(np.uint16)
            h, w = depth_mm.shape
            
            # Debug dimensioni - check con depth_w/h, non mono_w/h!
            if h != self.depth_h or w != self.depth_w:
                self.get_logger().warn(f"⚠️ Depth frame dimensioni inattese: {w}x{h} (Atteso: {self.depth_w}x{self.depth_h})")
                # Ridimensiona se necessario
                if h == self.depth_w and w == self.depth_h:
                    depth_mm = cv2.rotate(depth_mm, cv2.ROTATE_90_CLOCKWISE)
                    h, w = depth_mm.shape
                    self.get_logger().info(f"🔄 Depth ruotata a {w}x{h}")
            
            # Crea messaggio ROS
            msg = Image()
            msg.header.stamp = timestamp.to_msg()
            msg.header.frame_id = self.camera_optical_frame
            msg.height = h
            msg.width = w
            msg.encoding = '16UC1'
            msg.is_bigendian = False
            msg.step = w * 2  # 2 byte per pixel uint16
            
            msg.data = depth_mm.tobytes()
            
            # Pubblica
            self.pub_depth.publish(msg)
            self.get_logger().debug(f"✅ Depth pubblicata: {w}x{h}")
            
        except Exception as e:
            self.get_logger().error(f"❌ Errore pubblicazione depth: {e}")


    def publish_depth_normalized_frame(self, depth_frame, now):
        """
        Pubblica depth visualizzabile con Colormap (JET) per un debug immediato in RViz.
        """
        """
        Pubblica depth visualizzabile con Colormap (JET) per un debug immediato in RViz.
        RIMANE in formato bgr8 - NON usare compressed_depth_image_transport su questo topic!
        """
        if not hasattr(self, 'pub_depth_norm') or depth_frame is None:
            return

        try:
            # 1. Pulizia dati e normalizzazione rapida
            valid_mask = (depth_frame > 0)
            if not np.any(valid_mask):
                normalized = np.zeros(depth_frame.shape, dtype=np.uint8)
            else:
                # Normalizzazione dinamica robusta
                min_d = np.percentile(depth_frame[valid_mask], 5)
                max_d = np.percentile(depth_frame[valid_mask], 95)
                
                diff = max_d - min_d if max_d > min_d else 1.0
                
                normalized = np.clip((depth_frame - min_d) / diff * 255, 0, 255).astype(np.uint8)
                normalized[~valid_mask] = 0

            # 2. APPLICA COLORMAP
            color_depth = cv2.applyColorMap(255 - normalized, cv2.COLORMAP_JET)
            color_depth[normalized == 0] = [0, 0, 0]

            # 3. CREAZIONE MESSAGGIO ROS 2
            msg = Image()
            msg.header.stamp = now.to_msg()
            msg.header.frame_id = self.camera_optical_frame
            msg.height, msg.width = color_depth.shape[:2]
            msg.encoding = 'bgr8'  # FORMATO NON COMPATIBILE CON compressed_depth
            msg.step = msg.width * 3
            msg.data = color_depth.tobytes()

            self.pub_depth_norm.publish(msg)

        except Exception as e:
            self.get_logger().error(f"Errore visualizzazione depth: {e}")

    def bundle_adjustment_light(self, object_points, image_points, rvec, tvec):
            """
            Raffina la stima della posa minimizzando l'errore di riproiezione.
            Utilizza i parametri della camera già presenti nel nodo.
            """
            # Se abbiamo troppi pochi punti, l'ottimizzazione è instabile
            if len(object_points) < 6:
                return rvec, tvec

            try:
                # Usiamo le matrici già convertite in float32 e memorizzate nel nodo
                # Evitiamo di ricreare la matrice ad ogni chiamata
                success, rvec_refined, tvec_refined = cv2.solvePnP(
                    object_points.astype(np.float32),
                    image_points.astype(np.float32),
                    self.camera_matrix,  # Pre-calcolata in generate_default_camera_info
                    self.dist_coeffs,    # Pre-calcolata (solitamente array di zeri)
                    rvec,
                    tvec,
                    useExtrinsicGuess=True,
                    flags=cv2.SOLVEPNP_ITERATIVE
                )
                
                if success:
                    # Opzionale: calcola l'errore di riproiezione finale per debug
                    return rvec_refined, tvec_refined
                
            except Exception as e:
                self.logger.debug(f"Bundle adjustment fallito (probabile instabilità): {e}")
            
            return rvec, tvec

    def create_superpoint_debug_image(self, frame, keypoints, matches, prev_keypoints, yolo_detections=None):
        """Crea un'immagine di debug con keypoints (punti) e matches (linee di flusso)."""
        """Crea immagine debug con colori per stabilità"""
        """Crea immagine debug semplificata con keypoints e matches"""
        """Crea immagine debug semplificata con keypoints e matches"""
        try:
            if frame is None:
                self.get_logger().warn("Frame mono è None per debug image")
                return np.zeros((200, 320, 3), dtype=np.uint8)
            
            # Converti in BGR se necessario
            if len(frame.shape) == 2:
                debug_img = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            else:
                debug_img = frame.copy()
            
            h, w = debug_img.shape[:2]
            
            # Calculate scaling factor if processing resolution != mono resolution
            # Keypoints are in proc_w/h coordinates, need to scale to frame w/h
            scale_x = w / self.proc_w
            scale_y = h / self.proc_h
            
            # Disegna i keypoints
            if keypoints is not None and len(keypoints) > 0:
                for kp in keypoints[:200]:  # Limita a 200 per performance
                    # Scale coordinates from processing resolution to frame resolution
                    x = int(kp[0] * scale_x)
                    y = int(kp[1] * scale_y)
                    if 0 <= x < w and 0 <= y < h:
                        cv2.circle(debug_img, (x, y), 4, (0, 0, 0), -1)  # Bordo nero
                        cv2.circle(debug_img, (x, y), 3, (0, 255, 0), -1)  # Centro verde
            
            # Disegna matches (linee gialle)
            if (matches is not None and prev_keypoints is not None and 
                len(matches) > 0 and keypoints is not None):
                for match in matches[:50]:  # Limita a 50 per performance
                    try:
                        p1 = prev_keypoints[match.queryIdx]
                        p2 = keypoints[match.trainIdx]
                        
                        # Scale coordinates
                        pt1 = (int(p1[0] * scale_x), int(p1[1] * scale_y))
                        pt2 = (int(p2[0] * scale_x), int(p2[1] * scale_y))
                        
                        # Disegna linea
                        cv2.line(debug_img, pt1, pt2, (0, 255, 255), 1, cv2.LINE_AA)
                        
                        # Disegna piccoli cerchi agli endpoint
                        cv2.circle(debug_img, pt1, 2, (255, 0, 0), -1)  # Blu per prev
                        cv2.circle(debug_img, pt2, 2, (0, 0, 255), -1)  # Rosso per curr
                    except (IndexError, AttributeError, TypeError) as e:
                        continue
            
            # Disegna YOLO Detections
            if yolo_detections:
                 for det in yolo_detections:
                      xc, yc, bw, bh = det['box']
                      mx = int((xc - bw/2) * w / 640.0)
                      my = int((yc - bh/2) * h / 640.0)
                      mw = int(bw * w / 640.0)
                      mh = int(bh * h / 640.0)
                      
                      # Colore in base alla classe
                      color = (0, 0, 255) # Red for dynamic
                      if det['class'] == 0: color = (0, 0, 255) # Person
                      elif det['class'] in [15, 16]: color = (0, 165, 255) # Animals
                      
                      cv2.rectangle(debug_img, (mx, my), (mx+mw, my+mh), color, 2)
                      cv2.putText(debug_img, f"Class {det['class']}", (mx, my-5), 
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

            # Aggiungi testo overlay
            overlay = debug_img.copy()
            cv2.rectangle(overlay, (0, 0), (w, 50), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.5, debug_img, 0.5, 0, debug_img)
            
            # Testo statistiche
            keypoint_count = len(keypoints) if keypoints is not None else 0
            match_count = len(matches) if matches is not None else 0
            
            cv2.putText(debug_img, f"Keypoints: {keypoint_count}", 
                        (10, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            
            cv2.putText(debug_img, f"Matches: {match_count}", 
                        (10, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            
            return debug_img
            
        except Exception as e:
            self.get_logger().error(f"Errore creazione debug image: {e}")
            import traceback
            self.get_logger().error(traceback.format_exc())
            
            # Fallback
            if frame is not None and len(frame.shape) == 2:
                return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            elif frame is not None:
                return frame.copy()
            else:
                return np.zeros((200, 320, 3), dtype=np.uint8)

    def publish_keypoints_3d(self, keypoints, depth_frame, timestamp):
            """Pubblica i keypoints 3D estratti come PointCloud2"""
            if keypoints is None or len(keypoints) == 0 or depth_frame is None:
                return

            # 1. Recupero parametri intrinseci
            fx = self.camera_matrix[0, 0]
            fy = self.camera_matrix[1, 1]
            cx = self.camera_matrix[0, 2]
            cy = self.camera_matrix[1, 2]

            points_3d = []
            
            # 2. Estrazione coordinate e profondità
            # Arrotondiamo i keypoints per indicizzare la matrice di profondità
            kp_int = keypoints.astype(np.int32)
            u = kp_int[:, 0]
            v = kp_int[:, 1]

            # Filtro per rimanere nei bordi dell'immagine di processing (proc_w/h)
            h_p, w_p = self.proc_h, self.proc_w
            valid_idx = (u >= 0) & (u < w_p) & (v >= 0) & (v < h_p)
            u, v = u[valid_idx], v[valid_idx]
            kp_filtered = keypoints[valid_idx]

            # 3. Calcolo coordinate 3D con scaling depth
            h_d, w_d = depth_frame.shape
            sx, sy = w_d / w_p, h_d / h_p
            u_scaled = (u * sx).astype(np.int32)
            v_scaled = (v * sy).astype(np.int32)
            
            # Clipping per sicurezza
            u_scaled = np.clip(u_scaled, 0, w_d - 1)
            v_scaled = np.clip(v_scaled, 0, h_d - 1)

            depths = depth_frame[v_scaled, u_scaled].astype(np.float32)
            
            # Filtro profondità valida (es. tra 30cm e 5m)
            valid_depth = (depths > 300) & (depths < 5000)
            
            if not np.any(valid_depth):
                return

            z = depths[valid_depth] / 1000.0  # mm -> metri
            u_v = kp_filtered[valid_depth]
            
            # Proiezione inversa
            x = (u_v[:, 0] - cx) * z / fx
            y = (u_v[:, 1] - cy) * z / fy

            # Creazione array [N, 3]
            points = np.stack((x, y, z), axis=-1)

            # 4. Creazione Messaggio PointCloud2
            header = Header()
            header.stamp = timestamp.to_msg()
            header.frame_id = self.camera_optical_frame

            # PointField definisce la struttura di ogni punto
            fields = [
                PointField(name='x', offset=0, datatype=PointField.FLOAT32, count=1),
                PointField(name='y', offset=4, datatype=PointField.FLOAT32, count=1),
                PointField(name='z', offset=8, datatype=PointField.FLOAT32, count=1),
            ]

            cloud = pc2.create_cloud(header, fields, points.tolist())
            self.pub_keypoints_cloud.publish(cloud)

    import struct

    def publish_matched_points_3d(self, matches, prev_keypoints, curr_keypoints, prev_depth, curr_depth, timestamp):
        """
        Pubblica i match in 3D: Punti precedenti (Verdi) e Punti correnti (Ciano).
        Permette di vedere il 'flusso 3D' delle feature in RViz.
        """
        if not hasattr(self, 'pub_matches_cloud') or not matches:
            return
        if prev_depth is None or curr_depth is None:
            return

        try:
            buffer = bytearray()
            point_step = 16  # x, y, z (float32 * 3) + rgb (uint32)
            valid_points = 0

            # Parametri intrinseci pre-calcolati
            fx = self.camera_matrix[0, 0]
            fy = self.camera_matrix[1, 1]
            cx = self.camera_matrix[0, 2]
            cy = self.camera_matrix[1, 2]

            for match in matches:
                # Estrazione indici
                qi, ti = match.queryIdx, match.trainIdx
                
                # 1. PUNTO PRECEDENTE (Verde)
                u0, v0 = map(int, prev_keypoints[qi])
                if 0 <= u0 < prev_depth.shape[1] and 0 <= v0 < prev_depth.shape[0]:
                    d0 = prev_depth[v0, u0]
                    if 300 < d0 < 5000:
                        z0 = float(d0) / 1000.0
                        x0 = (u0 - cx) * z0 / fx
                        y0 = (v0 - cy) * z0 / fy
                        # Colore Verde: 00FF00
                        rgb_prev = struct.unpack('I', struct.pack('BBBB', 0, 255, 0, 255))[0]
                        buffer.extend(struct.pack('fffI', x0, y0, z0, rgb_prev))
                        valid_points += 1

                # 2. PUNTO CORRENTE (Ciano)
                u1, v1 = map(int, curr_keypoints[ti])
                if 0 <= u1 < curr_depth.shape[1] and 0 <= v1 < curr_depth.shape[0]:
                    d1 = curr_depth[v1, u1]
                    if 300 < d1 < 5000:
                        z1 = float(d1) / 1000.0
                        x1 = (u1 - cx) * z1 / fx
                        y1 = (v1 - cy) * z1 / fy
                        # Colore Ciano: 00FFFF
                        rgb_curr = struct.unpack('I', struct.pack('BBBB', 255, 255, 0, 255))[0]
                        buffer.extend(struct.pack('fffI', x1, y1, z1, rgb_curr))
                        valid_points += 1

            if valid_points == 0:
                return

            # Creazione Messaggio PointCloud2
            cloud = PointCloud2()
            cloud.header.stamp = timestamp.to_msg()
            cloud.header.frame_id = self.camera_optical_frame
            cloud.height = 1
            cloud.width = valid_points
            cloud.is_dense = False
            cloud.is_bigendian = False
            cloud.point_step = point_step
            cloud.row_step = point_step * valid_points
            
            cloud.fields = [
                PointField(name='x', offset=0, datatype=PointField.FLOAT32, count=1),
                PointField(name='y', offset=4, datatype=PointField.FLOAT32, count=1),
                PointField(name='z', offset=8, datatype=PointField.FLOAT32, count=1),
                PointField(name='rgb', offset=12, datatype=PointField.UINT32, count=1),
            ]
            cloud.data = bytes(buffer)

            self.pub_matches_cloud.publish(cloud)

        except Exception as e:
            self.get_logger().error(f"Errore PointCloud Match: {e}")




    def create_superpoint_markers(self, keypoints, matches, prev_keypoints, timestamp):
        """Crea markers RViz efficienti per visualizzare il tracking 2D nello spazio."""
        """Crea markers RViz efficienti per visualizzare il tracking 2D nello spazio."""
        """Crea markers RViz con protezione totale contro variabili non inizializzate."""
        """Crea markers RViz con logica di protezione granulare."""
        try:
            if not hasattr(self, 'pub_markers') or self.pub_markers is None:
                return
            
            # 1. Verifica Matrice di Calibrazione
            if self.camera_matrix is None:
                return
                
            # 2. Verifica Keypoints correnti
            if keypoints is None or len(keypoints) == 0:
                return

            # Estrazione sicura parametri
            cx = float(self.camera_matrix[0, 2])
            cy = float(self.camera_matrix[1, 2])
            fx = float(self.camera_matrix[0, 0])
            fy = float(self.camera_matrix[1, 1])

            marker_array = MarkerArray()
            msg_header = self.get_clock().now().to_msg() # Usiamo tempo fresco per RViz

            # --- 3. Marker PUNTI (Sempre) ---
            kp_marker = Marker()
            kp_marker.header.stamp = timestamp.to_msg()
            kp_marker.header.frame_id = self.camera_optical_frame  # Pubblica direttamente in frame ottico
            kp_marker.ns = "keypoints"
            kp_marker.id = 0
            kp_marker.type = Marker.POINTS
            kp_marker.action = Marker.ADD
            kp_marker.scale.x = kp_marker.scale.y = 0.01
            kp_marker.color.r, kp_marker.color.a = 1.0, 1.0
            kp_marker.lifetime.nanosec = 100000000 # 0.1s

            for kp in keypoints[:100]:
                if kp is not None:
                    # Coordinate in frame ottico - nessuna trasformazione
                    p = Point()
                    p.x = (float(kp[0]) - cx) / fx
                    p.y = (float(kp[1]) - cy) / fy
                    p.z = 1.0
                    kp_marker.points.append(p)
            
            marker_array.markers.append(kp_marker)

            # --- 4. Marker MATCHES (Solo se abbiamo storia precedente) ---
            # Verifichiamo TUTTO prima di usare le parentesi []
            if matches is not None and prev_keypoints is not None:
                line_marker = Marker()
                line_marker.header = kp_marker.header
                line_marker.ns = "matches"
                line_marker.id = 1
                line_marker.type = Marker.LINE_LIST
                line_marker.action = Marker.ADD
                line_marker.scale.x = 0.005
                line_marker.color.g, line_marker.color.a = 1.0, 0.6
                line_marker.lifetime.nanosec = 100000000

                # Iteriamo sui matches solo se è una lista/array valido
                try:
                    max_m = min(50, len(matches))
                    for i in range(max_m):
                        m = matches[i]
                        # Verifica che queryIdx e trainIdx esistano e siano validi
                        if m is not None and hasattr(m, 'queryIdx'):
                            if m.queryIdx < len(prev_keypoints) and m.trainIdx < len(keypoints):
                                # Estrazione coordinate
                                curr = keypoints[m.trainIdx]
                                prev = prev_keypoints[m.queryIdx]
                                
                                if curr is not None and prev is not None:
                                    for coords in [prev, curr]:
                                        # Coordinate in frame ottico - nessuna trasformazione
                                        p = Point()
                                        p.x = (float(coords[0]) - cx) / fx
                                        p.y = (float(coords[1]) - cy) / fy
                                        p.z = 1.0
                                        line_marker.points.append(p)
                except Exception:
                    pass # Se i matches non sono ancora indicizzabili, pazienza

                if len(line_marker.points) > 0:
                    marker_array.markers.append(line_marker)

            # 5. Invio finale
            self.pub_markers.publish(marker_array)
                    
        except Exception as e:
            # Questo catturerà se camera_matrix[0,2] fallisce perché camera_matrix è diventato None
            pass

    
    def bundle_adjustment_light(self, object_points, image_points, rvec, tvec):
        """Bundle Adjustment leggero per raffinare la stima PnP"""
        try:
            # Parametri della camera (da DepthAI)
            camera_matrix = self.camera_matrix
            dist_coeffs = self.dist_coeffs if hasattr(self, 'dist_coeffs') else np.zeros(5, dtype=np.float32)
            
            # Ottimizza con solvePnP iterativo
            success, rvec_refined, tvec_refined = cv2.solvePnP(
                object_points,
                image_points,
                camera_matrix,
                dist_coeffs,
                rvec,
                tvec,
                useExtrinsicGuess=True,
                flags=cv2.SOLVEPNP_ITERATIVE
            )
            
            if success:
                return rvec_refined, tvec_refined
            else:
                # Ritorna i valori originali se l'ottimizzazione fallisce
                return rvec, tvec
                
        except Exception as e:
            self.get_logger().warn(f"Bundle adjustment fallito: {e}")
            return rvec, tvec
    
    
    
    def estimate_motion_pnp(self, prev_kpts, curr_kpts, depth_frame, matches):
            """Stima del moto camera con PnP RANSAC + depth robusta"""
            """Stima del moto camera con PnP RANSAC + depth robusta"""
            """Stima del moto camera con PnP RANSAC + depth robusta e validazione"""
                
            if len(matches) < self.min_matches_for_tracking:
                self.logger.debug(f"❌ Troppi pochi matches: {len(matches)}")
                return None, 0.0
            
            try:
                camera_matrix = self.camera_matrix
                dist_coeffs = getattr(self, 'dist_coeffs', np.zeros(5, dtype=np.float32))
                
                fx, fy = camera_matrix[0, 0], camera_matrix[1, 1]
                cx, cy = camera_matrix[0, 2], camera_matrix[1, 2]
                
                object_points = []
                image_points = []
                match_indices = []
                
                # Pre-calcola dimensioni depth
                h_depth, w_depth = depth_frame.shape
                
                # 1. Raccogli punti 3D-2D con campionamento robusto
                for i, m in enumerate(matches[:100]):  # Limita a 100 matches per performance
                    u_prev, v_prev = prev_kpts[m.queryIdx]
                    u_curr, v_curr = curr_kpts[m.trainIdx]
                    
                    # Campionamento depth robusto con scaling automatico
                    d_mm = self.sample_depth_at_coords(depth_frame, u_prev, v_prev)
                    
                    if d_mm == 0:
                        # Fallback ROI attorno al punto scalato
                        h_d, w_d = depth_frame.shape[:2]
                        sx, sy = w_d / self.proc_w, h_d / self.proc_h
                        dx, dy = int(round(u_prev * sx)), int(round(v_prev * sy))
                        
                        roi = depth_frame[max(0, dy-1):min(h_d, dy+2), 
                                        max(0, dx-1):min(w_d, dx+2)]
                        valid_depths = roi[roi > 0]
                        if len(valid_depths) < 3:
                            continue
                        d_mm = np.median(valid_depths)
                    
                    if d_mm is None:
                        continue
                    
                    # Validazione range depth (20cm - 7m per OAK-D Lite)
                    if not (200 < d_mm < 7000):
                        continue
                    
                    # Conversione a metri e proiezione 3D
                    z = d_mm / 1000.0
                    X = (u_prev - cx) * z / fx
                    Y = (v_prev - cy) * z / fy
                    
                    object_points.append([X, Y, z])
                    image_points.append([u_curr, v_curr])
                    match_indices.append(i)
                
                # 2. Validazione numero punti
                if len(object_points) < 8:
                    self.logger.debug(f"❌ Punti 3D insufficienti: {len(object_points)}")
                    return None, 0.0
                
                obj_pts = np.asarray(object_points, dtype=np.float32)
                img_pts = np.asarray(image_points, dtype=np.float32)
                
                # 3. PnP RANSAC con parametri robusti
                try:
                    success, rvec, tvec, inliers = cv2.solvePnPRansac(
                        obj_pts,
                        img_pts,
                        camera_matrix,
                        dist_coeffs,
                        flags=cv2.SOLVEPNP_EPNP,  # Più stabile per punti sparsi
                        iterationsCount=200,
                        reprojectionError=2.0,    # Più stringente (2 pixel)
                        confidence=0.99,
                        #reprojectionError=3.0     # Massimo errore in pixel
                    )
                    
                    if not success or inliers is None or len(inliers) < 6:
                        self.logger.debug("PnP fallito o troppi pochi inliers")
                        return None, 0.0
                    
                    # 4. Calcola rapporto inlier
                    inlier_ratio = len(inliers) / len(obj_pts)
                    
                    # 5. Validazione fisica della trasformazione
                    # Norma traslazione
                    t_norm = np.linalg.norm(tvec)
                    
                    # Validazione movimenti realistici (max 30cm/frame a 15fps = 4.5m/s)
                    if t_norm > 0.3:
                        self.get_logger().warn(f"Traslazione troppo grande: {t_norm:.2f}m")
                        return None, inlier_ratio
                    
                    # Validazione rotazione (matrice ortogonale)
                    R, _ = cv2.Rodrigues(rvec)
                    det = np.linalg.det(R)
                    if abs(det - 1.0) > 0.01:
                        self.get_logger().warn(f"Matrice rotazione non ortogonale: det={det:.3f}")
                        return None, inlier_ratio
                    
                    # 6. Costruzione matrice trasformazione 4x4
                    T = np.eye(4, dtype=np.float32)
                    T[:3, :3] = R
                    T[:3, 3] = tvec.flatten()
                    
                    # 7. Applica Bundle Adjustment leggero se abilitato
                    if self.use_bundle_adjustment and inlier_ratio > 0.4:
                        try:
                            rvec_refined, tvec_refined = self.bundle_adjustment_light(
                                obj_pts[inliers[:, 0]],
                                img_pts[inliers[:, 0]],
                                rvec,
                                tvec
                            )
                            R_refined, _ = cv2.Rodrigues(rvec_refined)
                            T[:3, :3] = R_refined
                            T[:3, 3] = tvec_refined.flatten()
                            self.logger.debug("Bundle adjustment applicato")
                        except Exception as e:
                            self.logger.debug(f"Bundle adjustment fallito: {e}")
                    
                    # 8. Log di debug
                    self.logger.debug(
                        f"✅ PnP successo: {len(inliers)}/{len(obj_pts)} inliers "
                        f"(ratio: {inlier_ratio:.2f}), traslazione: {t_norm:.3f}m"
                    )
                    
                    return T, inlier_ratio
                    
                except cv2.error as e:
                    self.get_logger().error(f"Errore OpenCV in PnP: {e}")
                    return None, 0.0
                except Exception as e:
                    self.get_logger().error(f"Errore inatteso in PnP: {e}")
                    return None, 0.0
                    
            except Exception as e:
                self.get_logger().error(f"Errore generale in estimate_motion_pnp: {e}")
                return None, 0.0




    def get_realistic_camera_matrix(self):
            """
            Restituisce la matrice intrinseca corrente.
            Usa i valori calcolati dinamicamente per evitare discrepanze tra 
            le immagini pubblicate e il calcolo della posa PnP.
            """
            if hasattr(self, 'camera_matrix') and self.camera_matrix is not None:
                return self.camera_matrix
            
            # Fallback di emergenza se la matrice non è ancora stata inizializzata
            self.get_logger().warn("Matrice camera non inizializzata, uso valori di fallback")
            return np.array([
                [220.6, 0.0,   160.0],
                [0.0,   220.6, 100.0],
                [0.0,   0.0,   1.0]
            ], dtype=np.float32)

    def setup_real_calibration(self, device):
        """Estrae la calibrazione reale dalla EEPROM della OAK-D Lite"""
        calibData = device.readCalibration()
        # Ottieni la risoluzione nativa del sensore usato
        # Supponendo 400P (640x400)
        native_w, native_h = 640, 400 
        
        # Matrice intrinseca per la camera scelta
        socket = dai.CameraBoardSocket.CAM_B if self.sp_side == 'left' else dai.CameraBoardSocket.CAM_C
        M_native = np.array(calibData.getCameraIntrinsics(socket, native_w, native_h))
        
        # SCALATURA: calcola i fattori di scala
        scale_x = self.mono_w / native_w
        scale_y = self.mono_h / native_h
        
        
        # Applica la scala alla matrice intrinseca
        self.camera_matrix = M_native.copy()
        self.camera_matrix[0, 0] *= scale_x  # fx
        self.camera_matrix[1, 1] *= scale_y  # fy
        self.camera_matrix[0, 2] *= scale_x  # cx
        self.camera_matrix[1, 2] *= scale_y  # cy
        
        self.dist_coeffs = np.array(calibData.getDistortionCoefficients(socket))
        self.get_logger().info(f"Calibrazione scalata a {self.mono_w}x{self.mono_h}")

    def debug_feature_matching(self, keypoints, prev_keypoints, matches, depth_frame):
            """Monitora la qualità del matching e la coerenza della profondità."""
            if matches is None or len(matches) == 0:
                self.logger.debug("Nessun match trovato")
                return
            
            num_to_show = min(5, len(matches))
            self.logger.debug(f"--- Debug Matching ({len(matches)} matches totali) ---")

            for i in range(num_to_show):
                match = matches[i]
                prev_kp = prev_keypoints[match.queryIdx]
                curr_kp = keypoints[match.trainIdx]
                
                # Calcolo spostamento (Optical Flow)
                flow = np.linalg.norm(curr_kp - prev_kp)
                
                # Accesso alla depth con scaling
                depth = self.sample_depth_at_coords(depth_frame, curr_kp[0], curr_kp[1])
                depth_str = f"{depth}mm" if depth > 0 else "Invalid"

                self.logger.debug(
                    f"Match {i}: Prev[{int(prev_kp[0])},{int(prev_kp[1])}] -> "
                    f"Curr[{int(curr_kp[0])},{int(curr_kp[1])}] | Flow: {flow:.1f}px | Depth: {depth_str}"
                )

    def publish_imu_packet(self, packet, timestamp):
        """Pubblica i dati IMU se presenti nella pipeline"""
        imu_msg = Imu()
        imu_msg.header.stamp = self.get_clock().now().to_msg()
        imu_msg.header.frame_id = self.imu_frame  # frame IMU standard ROS
        
        # Esempio semplificato:
        accel = packet.acceleroMeter
        gyro = packet.gyroscope
        
        imu_msg.linear_acceleration.x = accel.x
        imu_msg.linear_acceleration.y = accel.y
        imu_msg.linear_acceleration.z = accel.z
        imu_msg.angular_velocity.x = gyro.x
        imu_msg.angular_velocity.y = gyro.y
        imu_msg.angular_velocity.z = gyro.z
        
        # Nota: servirebbe anche l'orientamento se vuoi l'IMU completa
        if hasattr(self, 'pub_imu'):
            self.pub_imu.publish(imu_msg)

        # Salva orientamento IMU per la VO
        dt = 0.01  # oppure calcolato dal timestamp
        roll, pitch, yaw = self.update_imu_orientation(
            (accel.x, accel.y, accel.z),
            (gyro.x, gyro.y, gyro.z),
            dt
        )

        self.last_imu_orientation = self.imu_orientation_to_matrix(
            roll, pitch, yaw
        )


    def debug_depth_alignment(self, keypoints, depth_frame):
            """Verifica se i keypoints SuperPoint cadono su zone con profondità valida."""
            if keypoints is None or depth_frame is None or len(keypoints) == 0:
                return
            
            self.get_logger().info("--- Analisi Allineamento Depth ---")
            
            h, w = depth_frame.shape
            num_kpts = len(keypoints)
            # Scegliamo indici equidistanti invece di random per stabilità visiva nei log
            sample_indices = np.linspace(0, num_kpts - 1, min(10, num_kpts), dtype=int)
            
            invalid_count = 0

            for idx in sample_indices:
                kp = keypoints[idx]
                x, y = int(kp[0]), int(kp[1])
                
                # Accesso alla depth con scaling
                depth = self.sample_depth_at_coords(depth_frame, x, y)
                status = "OK" if depth > 0 else "INVALID (0)"
                if depth == 0: invalid_count += 1

                self.get_logger().info(
                    f"KP {idx:3}: [{x:3}, {y:3}] -> Depth: {depth:4}mm | {status}"
                )
                
                # Analisi area attorno al punto scalato
                h_d, w_d = depth_frame.shape[:2]
                sx, sy = w_d / self.proc_w, h_d / self.proc_h
                dx, dy = int(round(x * sx)), int(round(y * sy))
                
                y_s, y_e = max(0, dy-1), min(h_d, dy+2)
                x_s, x_e = max(0, dx-1), min(w_d, dx+2)
                area = depth_frame[y_s:y_e, x_s:x_e]
                
                if area.size > 0 and depth > 0:
                    diff = area.max() - area.min()
                    if diff > 100: # Se c'è un salto di >10cm in 3x3 pixel
                        self.get_logger().warn(f"   ⚠️ Possibile bordo: variazione area {diff}mm")

            if invalid_count > 5:
                self.get_logger().warn(f"❗ Attenzione: {invalid_count}/10 campioni hanno depth nulla!")


    def _convert_optical_to_robot(self, T_optical):
            """Converte il movimento da Ottico a Robot compensando il Pitch."""
            if T_optical is None: return None

            R_opt = T_optical[:3, :3]
            t_opt = T_optical[:3, 3]

            # 1. Da Ottico (Z-avanti) a Camera Link (X-avanti)
            R_opt_to_cam = np.array([[0,0,1], [-1,0,0], [0,-1,0]], dtype=np.float32)
            R_cam = R_opt_to_cam @ R_opt @ R_opt_to_cam.T
            t_cam = R_opt_to_cam @ t_opt

            # 2. Compensa Pitch (Inclinazione camera)
            p = self.camera_pitch
            R_pitch = np.array([[np.cos(p), 0, np.sin(p)], [0, 1, 0], [-np.sin(p), 0, np.cos(p)]], dtype=np.float32)
            
            R_robot = R_pitch @ R_cam @ R_pitch.T
            t_robot = R_pitch @ t_cam

            T_robot = np.eye(4, dtype=np.float32)
            T_robot[:3, :3] = R_robot
            T_robot[:3, 3] = t_robot
            return T_robot

    def main_callback(self):
        """Versione corretta con pubblicazione garantita delle immagini"""
        
        # 0. Controllo shutdown
        with self.shutdown_lock:
            if self.shutdown_requested: 
                return
        
        # 1. Verifica disponibilità code
        if not all(hasattr(self, q) for q in ['q_mono', 'q_depth', 'q_sp']): 
            return
            
        mono_pkts = self.q_mono.tryGetAll()
        depth_pkts = self.q_depth.tryGetAll()
        sp_pkts = self.q_sp.tryGetAll()
        
        yolo_detections = []
        semantic_mask = None
        
        if self.use_yolo_segmentation and hasattr(self, 'q_yolo'):
            yolo_pkts = self.q_yolo.tryGetAll()
            if yolo_pkts:
                # Use decode_yolo_v6 for YoloDetectionNetwork output
                yolo_detections = self.decode_yolo_v6(yolo_pkts[-1])
                semantic_mask = self.generate_semantic_mask(yolo_detections, (self.mono_h, self.mono_w))
                
                # Pubblica le detection ROS
                msg_ts_yolo = self.get_clock().now() # O usa timestamp hardware se preferibile
                self._publish_yolo_detections(yolo_detections, msg_ts_yolo)

        # 2. Se non ci sono pacchetti, esci senza pubblicare
        if not (mono_pkts and depth_pkts and (sp_pkts or self.execution_provider == 'CPU')): 
            return
        
        try:
            # 3. Estrazione frame corrente
            mono_frame = mono_pkts[-1].getFrame()
            
            # 🔄 FIX: Handle Planar format from DepthAI (C, H, W) -> (H, W, C)
            # scn is 480 error fix: if channels are first, transpose.
            if mono_frame.ndim == 3 and mono_frame.shape[0] <= 4 and mono_frame.shape[2] > 4:
                mono_frame = mono_frame.transpose(1, 2, 0)
            
            depth_frame = depth_pkts[-1].getFrame()
            sp_pkt = sp_pkts[-1]
            
            # 4. TIMESTAMP CRITICO: usa tempo ROS corrente
            msg_ts = self.get_clock().now()
            
            # 5. ✅ PUBBLICAZIONE IMMAGINI (SEMPRE, indipendentemente dai keypoints)
            if hasattr(self, 'pub_mono') and mono_frame is not None:
                self.publish_mono_frame(mono_frame, msg_ts)
            
            if hasattr(self, 'pub_depth') and depth_frame is not None:
                self.publish_depth_frame(depth_frame, msg_ts)
            
            if hasattr(self, 'publish_depth_normalized') and self.publish_depth_normalized:
                if hasattr(self, 'pub_depth_norm') and depth_frame is not None:
                    self.publish_depth_normalized_frame(depth_frame, msg_ts)
            
            # 6. ✅ PUBBLICAZIONE CAMERA INFO
            if hasattr(self, 'publish_camera_info') and self.publish_camera_info:
                self.publish_camera_info_fixed(msg_ts)
            
            # 7. Estrazione feature (Hybrid Selection)
            # tracked_kpts e matches devono essere definiti per il debug
            tracked_kpts = []
            matches = []
            
            kpts_raw = None
            desc_raw = None
            
            self.frame_count += 1
            do_superpoint = True
            
            # Logica Ibrida: Se abilitata, usa KLT per i frame intermedi
            if self.enable_hybrid and self.klt_tracker.prev_gray is not None:
                if self.frame_count % self.sp_interval != 0:
                    do_superpoint = False

            if do_superpoint:
                # --- A. SUPERPOINT KEYFRAME ---
                # Check provider
                if self.execution_provider == 'VPU' and sp_pkts:
                    kpts_raw, scores_raw, desc_raw = self.extract_stable_superpoint_features(
                        sp_pkts[-1], depth_frame, mono_frame, self.last_good_keypoints, semantic_mask
                    )
                else:
                    # CPU Fallback - HARRIS/ORB
                    # This is a placeholder for actual CPU fallback implementation
                    kpts_raw = None 
                
                # Inizializza KLT se abbiamo buoni punti
                if self.enable_hybrid and kpts_raw is not None and len(kpts_raw) > 10:
                    self.klt_tracker.init_tracks(mono_frame, kpts_raw, desc_raw)
                    
            else:
                # --- B. KLT TRACKING (Intermediate) ---
                kpts_raw, ids, desc_raw, status = self.klt_tracker.track(mono_frame)
                
                if not status or len(kpts_raw) < 10:
                    # Logga solo ogni tanto per evitare spam
                    if not hasattr(self, '_last_klt_warn') or (self.get_clock().now() - self._last_klt_warn).nanoseconds > 2e9: # 2 secondi
                        self.get_logger().warn(f"⚠️ KLT ha perso il tracking - Forza SuperPoint al prossimo frame (Tracking: {len(kpts_raw) if kpts_raw is not None else 0} pts)")
                        self._last_klt_warn = self.get_clock().now()
                    
                    # FIX LOOP: Imposta frame_count in modo che il prossimo sia un frame SuperPoint
                    # frame_count viene incrementato all'inizio del loop (linea 2916 circa)
                    # Vogliamo che (frame_count + 1) % sp_interval == 0
                    if self.sp_interval > 0:
                        self.frame_count = self.sp_interval - 1
                    else:
                        self.frame_count = 0 
                        
                    return
            
            # 8. Se l'estrazione fallisce, esci ma le immagini sono già state pubblicate
            num_extracted = len(kpts_raw) if kpts_raw is not None else 0
            if kpts_raw is None or num_extracted < 5:
                self.get_logger().debug(f"Pochi keypoints: {num_extracted}, skip odometria")
                return                                  

            # Call shared logic
            self.process_odometry(tracked_kpts, tracked_desc, mono_frame, depth_frame, msg_ts, do_superpoint, yolo_detections)


    def driver_callback(self, msg):
        """Processa frame ricevuti dal driver esterno (OAKSyncFrame)"""
        try:
            # 1. Unpack Images
            mono_frame = self.bridge.imgmsg_to_cv2(msg.mono, desired_encoding="mono8")
            depth_frame = self.bridge.imgmsg_to_cv2(msg.depth, desired_encoding="16UC1")
            
            # Timestamp corretto dal messaggio
            ts_msg = Time.from_msg(msg.header.stamp)
            
            # Pubblica (relay) per visualizzazione
            if hasattr(self, 'pub_mono'): self.publish_mono_frame(mono_frame, ts_msg)
            if hasattr(self, 'pub_depth'): self.publish_depth_frame(depth_frame, ts_msg)
            
            # 2. Unpack Features
            # Kpts
            kpts_raw = []
            if msg.keypoints.data:
                 kpts_raw = np.array(msg.keypoints.data, dtype=np.float32).reshape(-1, 2)
            
            # Desc
            # TODO: Handle compressed descriptors (delta). 
            # For now assume driver sends raw or empty.
            # If empty, Flann will fail if used.
            # Assuming KLT fallback for now if desc missing.
            desc_raw = np.array([]) 
            
            # Detections
            yolo_detections = [] # Decode from msg.detections if needed
            
            # 3. Logic Injection
            self.frame_count += 1
            
            # Logic expects tracked_kpts to be defined
            tracked_kpts = kpts_raw
            tracked_desc = desc_raw
            
            if len(tracked_kpts) > 0:
                 self.process_odometry(tracked_kpts, tracked_desc, mono_frame, depth_frame, ts_msg, do_superpoint=True, yolo_detections=yolo_detections)

        except Exception as e:
            self.get_logger().error(f"Driver callback error: {e}")


    def process_odometry(self, tracked_kpts, tracked_desc, mono_frame, depth_frame, msg_ts, do_superpoint, yolo_detections=None):
            matches = []

            # 9. Matching & Odometry
            if self.prev_descriptors is not None and len(self.prev_descriptors) > 0:
                current_pose = None
                inlier_ratio = 0.0
                
                # A. Matching
                # Se siamo in modalità KLT, i match sono IMPLICITI (stesso indice)
                if not do_superpoint and self.enable_hybrid:
                     matches = []
                     # Implict match for KLT
                     # Assumption: tracked_kpts indices align with prev_keypoints
                     min_len = min(len(self.prev_keypoints), len(tracked_kpts))
                     for i in range(min_len):
                         matches.append(cv2.DMatch(i, i, 0.0))
                else:
                    # Caso SuperPoint classico o Driver Mode (Global Match)
                    # Se non abbiamo descrittori (es. driver non li manda), questo fallirà per FLANN.
                    # Ma se stiamo usando KLT su feature SP, dovremmo gestirlo.
                    # Per ora assumiamo che il driver mandi feature complete o che si usi logica ibrida.
                    if tracked_desc is not None and len(tracked_desc) > 0:
                        matches = self.odometry_system.match_features_hybrid(
                            self.prev_descriptors, tracked_desc,
                            self.prev_keypoints, tracked_kpts
                        )
                
                # B. Stima Posa
                if len(matches) >= self.min_matches_for_tracking:
                    current_pose, calculated_inlier_ratio = self.odometry_system.estimate_pose_robust(
                        self.prev_keypoints, tracked_kpts, depth_frame, matches
                    )
                    
                    # Usa inlier ratio calcolato
                    inlier_ratio = calculated_inlier_ratio
                    
                    # C. Pubblica Odometria
                    if current_pose is not None:
                        self.publish_odometry(current_pose, msg_ts, inlier_ratio)

                        
            # Aggiorna stato precedente
            self.prev_keypoints = tracked_kpts
            self.prev_descriptors = tracked_desc
            self.last_good_keypoints = tracked_kpts # Per tracking frame-to-frame

            # 10. Pubblicazioni standard (Visualizzazione)
            self.publish_mono_frame(mono_frame, msg_ts)
            self.publish_depth_frame(depth_frame, msg_ts)
            
            if getattr(self, 'publish_superpoint_debug', False):
                debug_img = self.create_superpoint_debug_image(
                    mono_frame, tracked_kpts, matches, self.prev_keypoints, yolo_detections
                )
                if debug_img is not None and hasattr(self, 'pub_debug_image'):
                    try:
                        # Log di debug per confermare la pubblicazione
                        if self.frame_count % 30 == 0:
                            self.get_logger().info(f"📸 Pubblando debug image: {debug_img.shape}")
                        
                        debug_rgb = cv2.cvtColor(debug_img, cv2.COLOR_BGR2RGB)
                        msg = Image()
                        msg.header.stamp = msg_ts.to_msg()
                        msg.header.frame_id = self.camera_optical_frame
                        msg.height, msg.width, _ = debug_rgb.shape
                        msg.encoding = 'rgb8'
                        msg.step = msg.width * 3
                        msg.data = debug_rgb.tobytes()
                        self.pub_debug_image.publish(msg)
                        
                        # Pubblica anche compresso per Foxglove remoto
                        if hasattr(self, 'pub_debug_compressed'):
                            msg_c = CompressedImage()
                            msg_c.header = msg.header
                            msg_c.format = "jpeg"
                            # Compressione JPEG qualità 80
                            success, encoded_img = cv2.imencode('.jpg', debug_rgb, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                            if success:
                                msg_c.data = encoded_img.tobytes()
                                self.pub_debug_compressed.publish(msg_c)
                                
                    except Exception: pass

        except Exception as e:
            self.get_logger().error(f"Errore in main_callback: {e}")
            import traceback
            self.get_logger().error(traceback.format_exc())


    def apply_motion_damping(self, T_delta, alpha_t=0.6, alpha_r=0.5):
        """
        Applica damping esponenziale a traslazione e rotazione
        per ridurre jitter frame-to-frame.
        """

        # Decomposizione
        R = T_delta[:3, :3]
        t = T_delta[:3, 3]

        # --- Traslazione ---
        t_damped = alpha_t * t

        # --- Rotazione (Rodrigues) ---
        rvec, _ = cv2.Rodrigues(R)
        rvec_damped = alpha_r * rvec
        R_damped, _ = cv2.Rodrigues(rvec_damped)

        T_out = np.eye(4, dtype=np.float32)
        T_out[:3, :3] = R_damped
        T_out[:3, 3] = t_damped

        return T_out

    def update_global_pose(self, T_delta):
        """
        Accumula la posa globale camera/world.
        """

        if not hasattr(self, 'T_world'):
            self.T_world = np.eye(4, dtype=np.float32)

        # Composizione: T_world = T_world * T_delta
        self.T_world = self.T_world @ T_delta

        return self.T_world

    def update_imu_orientation(self, accel, gyro, dt):
        """
        Stima orientamento IMU light:
        - Roll/Pitch da accelerometro
        - Yaw integrato dal giroscopio Z
        """

        ax, ay, az = accel
        gx, gy, gz = gyro

        # Roll & Pitch dalla gravità
        roll = np.arctan2(ay, az)
        pitch = np.arctan2(-ax, np.sqrt(ay*ay + az*az))

        # Inizializzazione yaw
        if not hasattr(self, 'imu_yaw'):
            self.imu_yaw = 0.0

        # Integrazione yaw (rad/s * s)
        self.imu_yaw += gz * dt

        return roll, pitch, self.imu_yaw

    def imu_orientation_to_matrix(self, roll, pitch, yaw):
        """
        Converte roll, pitch, yaw in matrice di rotazione 3x3
        """
        cr, sr = np.cos(roll), np.sin(roll)
        cp, sp = np.cos(pitch), np.sin(pitch)
        cy, sy = np.cos(yaw), np.sin(yaw)

        R = np.array([
            [cy*cp, cy*sp*sr - sy*cr, cy*sp*cr + sy*sr],
            [sy*cp, sy*sp*sr + cy*cr, sy*sp*cr - cy*sr],
            [-sp,   cp*sr,            cp*cr]
        ], dtype=np.float32)

        return R


    def apply_kinematic_gating(self, T_delta):
        """
        Rifiuta movimenti fisicamente impossibili per il robot.
        """

        # Traslazione
        t = T_delta[:3, 3]
        trans_norm = np.linalg.norm(t)

        # Rotazione (angolo asse)
        R = T_delta[:3, :3]
        angle = np.arccos(np.clip((np.trace(R) - 1) / 2, -1.0, 1.0))

        # Limiti realistici (per ~30 FPS)
        MAX_TRANSLATION = 0.15   # metri / frame
        MAX_ROTATION = np.deg2rad(8)  # rad / frame (~240°/s)

        if trans_norm > MAX_TRANSLATION:
            self.get_logger().warn(
                f"🚫 Gating: traslazione {trans_norm:.2f}m > limite {MAX_TRANSLATION}m"
            )
            return None

        if angle > MAX_ROTATION:
            deg = np.rad2deg(angle)
            self.get_logger().warn(
                f"🚫 Gating: rotazione {deg:.1f}° > limite {np.rad2deg(MAX_ROTATION):.1f}°"
            )
            return None

        return T_delta



    def compute_adaptive_covariance(self, inlier_ratio, num_matches=None):
        """
        Calcola covarianza adattiva in base alla qualità della stima
        """
        if num_matches is None:
            num_matches = getattr(self, 'last_num_matches', 20)
        
        # Covarianza base (pessimistica)
        base_cov_xy = 0.5    # m^2
        base_cov_z = 1.0     # m^2  
        base_cov_yaw = 0.8   # rad^2
        
        # Fattore qualità basato su inlier ratio
        quality_factor = 1.0
        if inlier_ratio > 0.7 and num_matches > 50:
            quality_factor = 0.1  # Molto buono
        elif inlier_ratio > 0.5 and num_matches > 30:
            quality_factor = 0.3  # Buono
        elif inlier_ratio > 0.3:
            quality_factor = 0.6  # Medio
        else:
            quality_factor = 1.2  # Scadente
        
        # Applica fattore qualità
        cov_xy = base_cov_xy * quality_factor
        cov_z = base_cov_z * quality_factor
        cov_yaw = base_cov_yaw * quality_factor
        
        # Limita valori minimi e massimi
        cov_xy = max(0.01, min(cov_xy, 5.0))
        cov_z = max(0.02, min(cov_z, 10.0))
        cov_yaw = max(0.02, min(cov_yaw, 3.0))
        
        return cov_xy, cov_z, cov_yaw


    def publish_cam_info(self, timestamp):
        if hasattr(self, 'pub_camera_info') and self.camera_info is not None:
            self.camera_info.header.stamp = self.get_clock().now().to_msg()  # ✅ CORRETTO
            self.camera_info.header.frame_id = self.camera_optical_frame
            self.pub_camera_info.publish(self.camera_info)

    def publish_static_tf(self):
        """Disabilitato: TF statiche vanno nel launch file per modularità.
        Vedi test_odometry_launch.py per la configurazione dei nodi static_transform_publisher.
        """
        pass


    def destroy_node(self):
        """Cleanup garantito"""
        try:
            self.logger.debug("Inizio destroy_node...")
            
            # 1. Ferma il timer
            if hasattr(self, 'main_timer') and self.main_timer:
                try:
                    self.main_timer.cancel()
                    self.main_timer.destroy()
                    self.logger.debug("Timer principale fermato")
                except Exception as e:
                    self.get_logger().error(f"Errore fermando timer: {e}")
            
            # 2. Chiudi dispositivo DepthAI
            if DEPTHAI_AVAILABLE and hasattr(self, 'device'):
                try:
                    self.device.close()
                    self.logger.debug("Dispositivo DepthAI chiuso")
                except Exception as e:
                    self.get_logger().error(f"Errore chiusura dispositivo DepthAI: {e}")
            
            # 3. Pulisci buffer
            self.latest_mono_packet = None
            self.latest_depth_packet = None
            self.latest_sp_packet = None
            self.prev_keypoints = None
            self.prev_descriptors = None
            
            # 4. Chiama il destroy_node della classe base
            super().destroy_node()
            
            self.logger.debug("destroy_node completato")
            
        except Exception as e:
            self.get_logger().error(f"Errore in destroy_node: {e}")
            try:
                super().destroy_node()
            except:
                pass


def main(args=None):
    """EntryPoint con gestione del ciclo di vita hardware e ROS 2"""
    rclpy.init(args=args)
    
    node = None
    executor = None
    
    try:
        # Inizializzazione Nodo
        node = OakSuperPointOdometry()
        
        # MultiThreadedExecutor: Thread 1 per i callback ROS, Thread 2 per il loop OAK
        executor = MultiThreadedExecutor(num_threads=2)
        executor.add_node(node)
        
        node.get_logger().info("--- Sistema di Odometria SuperPoint Attivo ---")
        
        
        # Gestione segnali (Ctrl+C)
        def signal_handler(sig, frame):
            node.get_logger().info("Segnale di interruzione rilevato, arresto in corso...")
            node.shutdown_requested = True
            # Non chiamare rclpy.shutdown() qui, lascia che il main loop esca
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Esecuzione
        try:
            executor.spin()
        except (KeyboardInterrupt, ExternalShutdownException):
            node.get_logger().info("Segnale di arresto ricevuto (Ctrl+C)")
        except Exception as e:
            node.get_logger().error(f"Errore fatale durante l'esecuzione: {e}")
            import traceback
            traceback.print_exc()
        
    finally:
        # --- Procedura di Shutdown Pulita ---
        if node is not None:
            # 1. Fermiamo prima la cattura hardware (OAK-D)
            node.request_shutdown() 
            
            # 2. Attendiamo che il thread OAK-D chiuda i thread interni
            if hasattr(node, 'shutdown_complete'):
                success = node.shutdown_complete.wait(timeout=3.0)
                if not success:
                    node.get_logger().warn("Hardware shutdown timed out - forzatura...")

            # 3. Pulizia ROS
            node.destroy_node()
        
        if executor is not None:
            executor.shutdown()
            
        if rclpy.ok():
            rclpy.shutdown()
            
        print("Spegni completato. Robot in sicurezza.")

if __name__ == '__main__':
    main()