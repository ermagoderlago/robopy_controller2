#!/usr/bin/env python3
# oakd_camera_publisher_node_super.py - Nodo completo con YOLO + Stereo + IMU + SuperPoint FUNZIONANTE

import os
import time
import threading
import json
from typing import Optional, List

os.environ['RCUTILS_CONSOLE_OUTPUT_FORMAT'] = '[{severity} {time}] [{name}]: {message}'
os.environ['RCUTILS_LOGGING_SEVERITY'] = 'ERROR'

import rclpy
from rclpy.node import Node
from rclpy.time import Time
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
from sensor_msgs.msg import Image, CameraInfo, Imu
from vision_msgs.msg import Detection2DArray, Detection2D, BoundingBox2D, ObjectHypothesisWithPose
from std_msgs.msg import Float32MultiArray, MultiArrayDimension
from cv_bridge import CvBridge

import depthai as dai
import numpy as np
import cv2
import torch

# Default model YOLO
DEFAULT_MODEL = "luxonis/yolov6-nano:r2-coco-512x384"

# COCO class names per YOLO
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

class OakDSuperPointNode(Node):
    def __init__(self):
        super().__init__("oakd_superpoint_node")

        # ================================================================
        # PARAMETRI DI CONFIGURAZIONE
        # ================================================================
        
        # Parametri originali YOLO
        self.declare_parameter("optical_frame_id", "camera_optical_frame")
        self.declare_parameter("imu_frame_id", "imu_link")
        self.declare_parameter("enable_nn", False)
        self.declare_parameter("model", DEFAULT_MODEL)
        self.declare_parameter("nn_width", 512)
        self.declare_parameter("nn_height", 384)
        self.declare_parameter("nn_fps", 15.0)
        self.declare_parameter("low_w", 320)
        self.declare_parameter("low_h", 240)
        self.declare_parameter("low_fps", 15.0)
        self.declare_parameter("depth_fps", 10.0)
        self.declare_parameter("draw_detections", True)
        self.declare_parameter("debug", True)
        self.declare_parameter("enhance_images", True)
        self.declare_parameter("use_coco_names", True)
        
        # Parametri SuperPoint
        self.declare_parameter("blob_path", "")
        self.declare_parameter("width", 160)
        self.declare_parameter("height", 100)
        self.declare_parameter("conf_thresh", 0.015)
        self.declare_parameter("nms_dist", 1)
        # Nuovo parametro: usa SuperPoint su CPU (True) o su OAK-D (False)
        self.declare_parameter("superpoint_cpu", True)
        self.superpoint_cpu = bool(self.get_parameter("superpoint_cpu").value)

        # Carica parametri YOLO
        self.optical_frame_id = self.get_parameter("optical_frame_id").value
        self.imu_frame_id = self.get_parameter("imu_frame_id").value
        self.enable_nn = bool(self.get_parameter("enable_nn").value)
        self.model = str(self.get_parameter("model").value)
        self.nn_w = int(self.get_parameter("nn_width").value)
        self.nn_h = int(self.get_parameter("nn_height").value)
        self.nn_fps = float(self.get_parameter("nn_fps").value)
        self.low_w = int(self.get_parameter("low_w").value)
        self.low_h = int(self.get_parameter("low_h").value)
        self.low_fps = float(self.get_parameter("low_fps").value)
        self.depth_fps = float(self.get_parameter("depth_fps").value)
        self.draw_detections = bool(self.get_parameter("draw_detections").value)
        self.debug = bool(self.get_parameter("debug").value)
        self.enhance_images = bool(self.get_parameter("enhance_images").value)
        self.use_coco_names = bool(self.get_parameter("use_coco_names").value)
        
        # Carica parametri SuperPoint
        self.blob_path = str(self.get_parameter("blob_path").value)
        self.W = int(self.get_parameter("width").value)
        self.H = int(self.get_parameter("height").value)
        self.conf_thresh = float(self.get_parameter("conf_thresh").value)
        self.nms_dist = int(self.get_parameter("nms_dist").value)

        if not os.path.exists(self.blob_path):
            raise RuntimeError(f"❌ Blob non trovato: {self.blob_path}")

        self.get_logger().info(f"SuperPoint input: {self.W}x{self.H}")
        if self.superpoint_cpu:
            self.get_logger().info("SuperPoint verrà eseguito su CPU (Raspberry Pi)")
        
        # LOGICA RIDUZIONE CARICO
        # Alterna Depth e SuperPoint per risparmiare memoria e poter usare risoluzioni/FPS più alti
        self.alternate_pipelines = True  # Abilita alternanza pipeline
        self.alternate_interval = 30     # Numero di cicli tra uno switch (es: ogni ~0.5s se loop ~60Hz)
        self.active_pipeline = "depth"   # Inizia con Depth
        self.last_switch = 0

        if self.depth_fps > 0:
            self.get_logger().info(
                "Alternanza pipeline Depth/SuperPoint attiva: risoluzioni e FPS più alti possibili"
            )
            self.nn_fps = 0.0  # YOLO disabilitato
            self.low_fps = 5.0
            self.depth_fps = 5.0
            self.low_w = 320
            self.low_h = 240
            # SuperPoint sempre alla risoluzione del blob
            self.superpoint_skip = 1
        else:
            self.get_logger().info("Stereo Depth disattivato (depth_fps=0)")
            self.superpoint_skip = 1

        # ================================================================
        # PUBLISHERS
        # ================================================================
        
        qos_best_effort = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        qos_reliable = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)

        # Publishers originali YOLO
        self.pub_rgb = self.create_publisher(Image, "/oak/rgb/image_raw", qos_reliable)
        self.pub_info = self.create_publisher(CameraInfo, "/oak/rgb/camera_info", qos_reliable)
        self.pub_annot = self.create_publisher(Image, "/oak/rgb/image_annotated", qos_best_effort)
        self.pub_depth = self.create_publisher(Image, "/oak/stereo/image_raw", qos_reliable)
        self.pub_imu = self.create_publisher(Imu, "/oak/imu/data", qos_reliable)
        self.pub_detections = self.create_publisher(Detection2DArray, "/oak/detections", 10)
        self.pub_stereo_info = self.create_publisher(CameraInfo, "/oak/stereo/camera_info", qos_reliable)

        # Publishers SuperPoint (come nodo funzionante)
        self.pub_gray = self.create_publisher(Image, "/oak/superpoint/gray", 10)
        self.pub_overlay = self.create_publisher(Image, "/oak/superpoint/overlay", 10)
        self.pub_keypoints = self.create_publisher(Float32MultiArray, "/oak/superpoint/keypoints", 10)

        # ================================================================
        # INIZIALIZZAZIONE
        # ================================================================
        
        self.bridge = CvBridge()
        self._init_camera_info()
        self.class_names = COCO_CLASS_NAMES if self.use_coco_names else None
        
        # Variabili di stato
        self.pipeline = None
        self.device = None
        self._running = False
        self._loop_thread = None
        self.base_time_offset = 0.0
        
        # Code DepthAI ORIGINALI (del primo nodo)
        self.q_low = None          # RGB a bassa risoluzione
        self.q_preview = None      # Preview per YOLO
        self.q_nn = None           # Output YOLO
        self.q_depth = None        # Depth
        self.q_imu = None          # IMU
        
        # Code SuperPoint SEPARATE (del secondo nodo funzionante)
        self.q_superpoint_gray = None
        self.q_superpoint_out = None

        self.get_logger().info("Starting pipeline construction...")
        
        try:
            if self._build_pipeline_correct():
                self._running = True
                
                # Calcolo offset temporale
                ros_now = self.get_clock().now().nanoseconds / 1e9
                dai_now = dai.Clock.now().total_seconds()
                self.base_time_offset = ros_now - dai_now
                self.get_logger().info(f"Time offset initialized: {self.base_time_offset:.4f}s")

                self._loop_thread = threading.Thread(target=self._loop_integrated, daemon=True)
                self._loop_thread.start()
                self.get_logger().info("Node initialization complete!")
            else:
                self.get_logger().error("Failed to initialize pipeline")
        except Exception as e:
            self.get_logger().error(f"Initialization failed: {e}")

    # ================================================================
    # METODI DI INIZIALIZZAZIONE (PRIMO NODO)
    # ================================================================
    
    def _init_camera_info(self):
        """Inizializza i messaggi CameraInfo"""
        self.cam_info = CameraInfo()
        self.cam_info.header.frame_id = self.optical_frame_id
        self.cam_info.width = self.low_w
        self.cam_info.height = self.low_h
        fx = fy = 0.8 * float(self.low_w)
        cx = float(self.low_w) / 2.0
        cy = float(self.low_h) / 2.0
        self.cam_info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        self.cam_info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        self.cam_info.distortion_model = "plumb_bob"
        self.cam_info.d = [0.0, 0.0, 0.0, 0.0, 0.0]

    # ================================================================
    # PIPELINE CORRETTA - COMBO PERFETTA
    # ================================================================
    
    def _build_pipeline_correct(self) -> bool:
        """Costruisce la pipeline corretta combinando entrambi i nodi"""
        try:
            p = dai.Pipeline()
            
            # ============================================================
            # 1. MONO CAMERA PRINCIPALE (PER TUTTO)
            # ============================================================
            cam = p.create(dai.node.Camera).build()
            
            # ============================================================
            # 2. OUTPUT PRINCIPALE RGB (bassa risoluzione) - PRIMO NODO
            # ============================================================
            low_out = cam.requestOutput(
                (self.low_w, self.low_h),
                type=dai.ImgFrame.Type.GRAY8
            )
            
            # ============================================================
            # 3. YOLO DETECTION NETWORK - PRIMO NODO
            # ============================================================
            if self.enable_nn and self.model:
                try:
                    preview_out = cam.requestOutput(
                        (self.nn_w, self.nn_h),
                        type=dai.ImgFrame.Type.BGR888p
                    )
                    det = p.create(dai.node.DetectionNetwork)
                    det = det.build(cam, self.model, self.nn_fps)
                    self.q_nn = det.out.createOutputQueue(maxSize=2, blocking=False)
                    self.q_preview = preview_out.createOutputQueue(maxSize=2, blocking=False)
                    self.get_logger().info(f"DetectionNetwork built: {self.model}")
                except Exception as e:
                    self.get_logger().warning(f"Failed to create NN: {e}")
                    self.enable_nn = False
            
            # ============================================================
            # 4. SUPERPOINT PIPELINE - SOLO SU OAK-D SE superpoint_cpu=False
            # ============================================================
            if not self.superpoint_cpu and os.path.exists(self.blob_path):
                try:
                    self.get_logger().info(f"Costruzione pipeline SuperPoint: {self.W}x{self.H}")
                    superpoint_out = cam.requestOutput(
                        (self.W, self.H),
                        type=dai.ImgFrame.Type.GRAY8
                    )
                    superpoint_nn = p.create(dai.node.NeuralNetwork)
                    superpoint_nn.setBlobPath(self.blob_path)
                    superpoint_nn.setNumInferenceThreads(1)
                    superpoint_nn.input.setBlocking(False)
                    superpoint_out.link(superpoint_nn.input)
                    self.q_superpoint_gray = superpoint_out.createOutputQueue(
                        maxSize=2, blocking=False
                    )
                    self.q_superpoint_out = superpoint_nn.out.createOutputQueue(
                        maxSize=1, blocking=False
                    )
                    # Debug: stima memoria SuperPoint
                    sp_input_bytes = self.W * self.H
                    sp_output_bytes = 65 * (self.H // 8) * (self.W // 8) * 4  # float32
                    self.get_logger().info(
                        f"[DEBUG] SuperPoint input: {sp_input_bytes/1024:.1f} KB, output: {sp_output_bytes/1024:.1f} KB per frame"
                    )
                    self.get_logger().info("✅ Pipeline SuperPoint OAK-D costruita")
                except Exception as e:
                    self.get_logger().error(f"❌ Errore nella pipeline SuperPoint: {e}")
                    import traceback
                    self.get_logger().error(traceback.format_exc())
            else:
                # Solo output GRAY per SuperPoint su CPU
                self.q_superpoint_gray = cam.requestOutput(
                    (self.W, self.H),
                    type=dai.ImgFrame.Type.GRAY8
                ).createOutputQueue(maxSize=2, blocking=False)

            # 5. STEREO DEPTH - PRIMO NODO (RISOLUZIONE RIDOTTA)
            if self.depth_fps > 0:
                mono_l = p.create(dai.node.MonoCamera)
                mono_r = p.create(dai.node.MonoCamera)
                mono_l.setBoardSocket(dai.CameraBoardSocket.CAM_B)
                mono_r.setBoardSocket(dai.CameraBoardSocket.CAM_C)
                mono_l.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
                mono_r.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
                mono_l.setFps(self.depth_fps)
                mono_r.setFps(self.depth_fps)

                stereo = p.create(dai.node.StereoDepth)
                try:
                    stereo.setConfidenceThreshold(200)
                    stereo.setLeftRightCheck(False)
                    stereo.setSubpixel(False)
                except: 
                    pass
                stereo.setOutputSize(160, 120)  # Riduci output depth
                stereo.setDepthAlign(dai.CameraBoardSocket.CAM_A)
                stereo.setLeftRightCheck(True)
                stereo.setSubpixel(False)

                mono_l.out.link(stereo.left)
                mono_r.out.link(stereo.right)
                self.q_depth = stereo.depth.createOutputQueue(maxSize=2, blocking=False)
                # Debug: stima memoria Depth
                depth_bytes = 160 * 120 * 2  # uint16 per pixel
                self.get_logger().info(
                    f"[DEBUG] StereoDepth output: {depth_bytes/1024:.1f} KB per frame"
                )
                self.get_logger().info(f"Stereo Depth attivo a {self.depth_fps} FPS (ridotto)")
            else:
                self.q_depth = None
                self.get_logger().info("Stereo Depth disattivato")
            
            # ============================================================
            # 6. IMU - PRIMO NODO
            # ============================================================
            imu = p.create(dai.node.IMU)
            imu.enableIMUSensor(dai.IMUSensor.ACCELEROMETER_RAW, 50)
            imu.enableIMUSensor(dai.IMUSensor.GYROSCOPE_RAW, 50)
            imu.setBatchReportThreshold(1)
            imu.setMaxBatchReports(5)

            # ============================================================
            # 7. CREATE QUEUES RIMANENTI
            # ============================================================
            self.q_low = low_out.createOutputQueue(maxSize=2, blocking=False)
            self.q_imu = imu.out.createOutputQueue(maxSize=50, blocking=False)
            
            # ============================================================
            # 8. START DEVICE
            # ============================================================
            self.device = p.start()
            self.pipeline = p
            
            return True
            
        except Exception as e:
            self.get_logger().error(f"Pipeline build error: {e}")
            import traceback
            self.get_logger().error(traceback.format_exc())
            return False

    # ================================================================
    # UTILITY FUNCTIONS - PRIMO NODO
    # ================================================================
    
    def get_ros_time_from_dai(self, dai_ts):
        """Converte timestamp DepthAI a ROS Time"""
        ts_abs = self.base_time_offset + dai_ts.total_seconds()
        seconds = int(ts_abs)
        nanoseconds = int((ts_abs - seconds) * 1e9)
        return Time(seconds=seconds, nanoseconds=nanoseconds).to_msg()

    def _create_stereo_camera_info(self, width, height, timestamp):
        """Crea CameraInfo per camera stereo"""
        stereo_info = CameraInfo()
        stereo_info.header.stamp = timestamp
        stereo_info.header.frame_id = self.optical_frame_id
        stereo_info.height = height
        stereo_info.width = width
        
        fx = 400.0 * (width / 320.0)
        fy = 400.0 * (height / 240.0)
        cx = width / 2.0
        cy = height / 2.0
        
        stereo_info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        stereo_info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        stereo_info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
        stereo_info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        stereo_info.distortion_model = 'plumb_bob'
        stereo_info.binning_x = 0
        stereo_info.binning_y = 0
        
        return stereo_info

    # ================================================================
    # YOLO FUNCTIONS - PRIMO NODO (INTATTE)
    # ================================================================
    
    def _decode_detections(self, pkt, w, h):
        """Decodifica detection YOLO"""
        dets = []
        try:
            if not pkt or not hasattr(pkt, "detections"): 
                return dets
            for d in pkt.detections:
                x1 = int(max(0, d.xmin * w))
                y1 = int(max(0, d.ymin * h))
                x2 = int(min(w-1, d.xmax * w))
                y2 = int(min(h-1, d.ymax * h))
                lbl = int(d.label)
                conf = float(d.confidence)
                
                name = f"cls_{lbl}"
                if self.class_names and lbl < len(self.class_names):
                    name = self.class_names[lbl]
                elif "yolov8" in self.model.lower() and lbl < len(COCO_CLASS_NAMES):
                    name = COCO_CLASS_NAMES[lbl]

                dets.append({
                    'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                    'label': lbl, 'confidence': conf, 'class_name': name
                })
        except Exception as e:
            if self.debug:
                self.get_logger().debug(f"Detection decode error: {e}")
        return dets

    def enhance_image(self, image):
        """Migliora contrasto immagine"""
        if not self.enhance_images: 
            return image
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            mean_brightness = np.mean(gray)
            TOO_DARK, TOO_BRIGHT = 40, 80
            
            if mean_brightness > TOO_BRIGHT: 
                return image
            
            if mean_brightness > TOO_DARK:
                gamma = 1.3
                inv_gamma = 1.0 / gamma
                table = np.array([(i / 255.0) ** inv_gamma * 255 for i in range(256)]).astype("uint8")
                return cv2.LUT(image, table)
            else:
                denoised = cv2.medianBlur(image, 3)
                lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
                l, a, b = cv2.split(lab)
                clip_limit = max(1.0, 2.0 * (TOO_DARK - mean_brightness) / TOO_DARK)
                clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(12,12))
                l_eq = clahe.apply(l)
                
                darkness_factor = (TOO_DARK - mean_brightness) / TOO_DARK
                blend_strength = 0.3 + 0.5 * darkness_factor
                l_blended = cv2.addWeighted(l, 1.0 - blend_strength, l_eq, blend_strength, 0)
                
                lab_eq = cv2.merge([l_blended, a, b])
                enhanced = cv2.cvtColor(lab_eq, cv2.COLOR_LAB2BGR)
                
                gamma = 1.1
                inv_gamma = 1.0 / gamma
                table = np.array([(i / 255.0) ** inv_gamma * 255 for i in range(256)]).astype("uint8")
                return cv2.LUT(enhanced, table)
        except:
            return image

    def _draw_and_publish(self, frame, dets, stamp):
        """Disegna e pubblica detection YOLO"""
        try:
            img = frame.copy()
            if dets and self.draw_detections:
                for d in dets:
                    cv2.rectangle(img, (d['x1'], d['y1']), (d['x2'], d['y2']), (0, 255, 0), 2)
                    txt = f"{d.get('class_name', 'unk')}: {d.get('confidence', 0.0):.2f}"
                    ytxt = max(0, d['y1'] - 6)
                    cv2.putText(img, txt, (d['x1'], ytxt-1), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            msg = self.bridge.cv2_to_imgmsg(img, encoding="bgr8")
            msg.header.stamp = stamp
            msg.header.frame_id = self.optical_frame_id
            self.pub_annot.publish(msg)
        except Exception as e:
            if self.debug:
                self.get_logger().debug(f"Draw error: {e}")

    def _publish_detections(self, dets, stamp):
        """Pubblica detection YOLO come messaggio ROS2"""
        try:
            det_array = Detection2DArray()
            det_array.header.stamp = stamp
            det_array.header.frame_id = self.optical_frame_id
            for d in dets:
                detection = Detection2D()
                detection.header = det_array.header
                bbox = BoundingBox2D()
                bbox.center.position.x = (d['x1'] + d['x2']) / 2.0
                bbox.center.position.y = (d['y1'] + d['y2']) / 2.0
                bbox.size_x = float(d['x2'] - d['x1'])
                bbox.size_y = float(d['y2'] - d['y1'])
                detection.bbox = bbox
                hyp = ObjectHypothesisWithPose()
                hyp.hypothesis.class_id = str(d['label'])
                hyp.hypothesis.score = float(d['confidence'])
                detection.results.append(hyp)
                det_array.detections.append(detection)
            self.pub_detections.publish(det_array)
        except Exception as e:
            if self.debug:
                self.get_logger().debug(f"Publish detections error: {e}")

    # ================================================================
    # SUPERPOINT PROCESSING - CPU (PyTorch)
    # ================================================================
    def _process_superpoint_cpu(self):
        """Esegue SuperPoint su CPU (PyTorch)"""
        try:
            pkt = self.q_superpoint_gray.tryGet()
            if pkt is None:
                return
            gray = pkt.getCvFrame()
            msg = self.bridge.cv2_to_imgmsg(gray, encoding="mono8")
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = self.optical_frame_id
            self.pub_gray.publish(msg)

            # Carica modello SuperPoint PyTorch solo la prima volta
            if not hasattr(self, "superpoint_model"):
                try:
                    from superpoint import SuperPoint  # Assicurati di avere superpoint.py nel PYTHONPATH
                except ImportError as e:
                    self.get_logger().error(
                        "SuperPoint CPU error: modulo 'superpoint' non trovato. "
                        "Assicurati di avere superpoint.py nella stessa cartella o nel PYTHONPATH."
                    )
                    return
                self.superpoint_model = SuperPoint()
                self.superpoint_model.eval()
                self.get_logger().info("Modello SuperPoint PyTorch caricato su CPU")

            # Preprocessing
            img = gray.astype(np.float32) / 255.0
            inp = torch.from_numpy(img).unsqueeze(0).unsqueeze(0)  # [1,1,H,W]
            with torch.no_grad():
                out = self.superpoint_model({'image': inp})
            kpts = out['keypoints'][0].cpu().numpy()  # Nx2
            scores = out['scores'][0].cpu().numpy()   # N

            # Pubblica overlay
            overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            for (x, y), s in zip(kpts, scores):
                if s > self.conf_thresh:
                    cv2.circle(overlay, (int(x), int(y)), 2, (0, 255, 0), -1)
            ov_msg = self.bridge.cv2_to_imgmsg(overlay, encoding="bgr8")
            ov_msg.header.stamp = self.get_clock().now().to_msg()
            ov_msg.header.frame_id = self.optical_frame_id
            self.pub_overlay.publish(ov_msg)

            # Pubblica keypoints
            if len(kpts) > 0:
                arr = np.hstack([kpts, scores[:, None]]).astype(np.float32)
                msg = Float32MultiArray()
                msg.data = arr.flatten().tolist()
                dim0 = MultiArrayDimension(label="points", size=arr.shape[0], stride=arr.shape[0]*3)
                dim1 = MultiArrayDimension(label="data", size=3, stride=3)
                msg.layout.dim = [dim0, dim1]
                self.pub_keypoints.publish(msg)
        except Exception as e:
            self.get_logger().error(f"SuperPoint CPU error: {e}")

    # ================================================================
    # SUPERPOINT PROCESSING - ESATTAMENTE COME SECONDO NODO
    # ================================================================
    
    def _process_superpoint(self):
        """Processa SuperPoint - COPIA ESATTA DEL SECONDO NODO"""
        try:
            # Usa self.H e self.W invece di self.superpoint_h e self.superpoint_w
            H8 = self.H // 8
            W8 = self.W // 8
            hw = H8 * W8

            # GRAY IMAGE
            pkt = self.q_superpoint_gray.tryGet()
            if pkt is not None:
                gray = pkt.getCvFrame()
                msg = self.bridge.cv2_to_imgmsg(gray, encoding="mono8")
                msg.header.stamp = self.get_clock().now().to_msg()
                msg.header.frame_id = self.optical_frame_id
                self.pub_gray.publish(msg)

            # SUPERPOINT OUTPUT
            pkt_sp = self.q_superpoint_out.tryGet()
            if pkt_sp is not None:

                layer_names = pkt_sp.getAllLayerNames()
                self.get_logger().info(f"[SuperPoint] layers: {layer_names}")

                heatmap = None
                desc = None
                desc_ch = None

                for name in layer_names:
                    data = np.asarray(pkt_sp.getTensor(name), dtype=np.float32)
                    size = data.size

                    if size == 65 * hw:
                        heatmap = data
                        self.get_logger().info(f"  heatmap ← {name}")
                    elif size % hw == 0:
                        ch = size // hw
                        if ch in (128, 256):
                            desc = data
                            desc_ch = ch
                            self.get_logger().info(f"  descriptors ← {name} ({ch}D)")

                if heatmap is None or desc is None:
                    self.get_logger().error("❌ Output SuperPoint NON valido")
                    return

                heatmap = heatmap.reshape(1, 65, H8, W8)
                desc = desc.reshape(1, desc_ch, H8, W8)

                self.get_logger().info(f"✔ heatmap {heatmap.shape}, desc {desc.shape}")

                # HEATMAP IMAGE
                hm = np.max(heatmap[0, :-1], axis=0)
                hm = (hm - hm.min()) / (hm.max() - hm.min() + 1e-6)
                hm = (hm * 255).astype(np.uint8)

                hm_msg = self.bridge.cv2_to_imgmsg(hm, encoding="mono8")
                hm_msg.header.stamp = self.get_clock().now().to_msg()
                hm_msg.header.frame_id = self.optical_frame_id
                self.pub_overlay.publish(hm_msg)  # Pubblica su overlay

                # DESCRIPTORS
                msg = Float32MultiArray()
                msg.data = desc.flatten().tolist()

                dim0 = MultiArrayDimension()
                dim0.label = "batch"
                dim0.size = 1
                dim0.stride = desc.size

                dim1 = MultiArrayDimension()
                dim1.label = "channels"
                dim1.size = desc_ch
                dim1.stride = desc_ch * H8 * W8

                dim2 = MultiArrayDimension()
                dim2.label = "height"
                dim2.size = H8
                dim2.stride = H8 * W8

                dim3 = MultiArrayDimension()
                dim3.label = "width"
                dim3.size = W8
                dim3.stride = W8

                msg.layout.dim = [dim0, dim1, dim2, dim3]

                self.pub_keypoints.publish(msg)  # Pubblica su keypoints

        except Exception as e:
            self.get_logger().error(f"SuperPoint processing error: {e}")

    # ================================================================
    # MAIN LOOP INTEGRATO - FUNZIONANTE
    # ================================================================
    
    def _loop_integrated(self):
        """Loop principale integrato - VERSIONE ALTERNANZA PIPELINE"""
        self.get_logger().info("MAIN LOOP STARTED - Publishing data...")
        frame_counter = 0
        last_log_time = time.time()
        superpoint_counter = 0
        cycle_counter = 0

        while self._running and rclpy.ok():
            try:
                # Log ogni 2 secondi
                now = time.time()
                if now - last_log_time > 2.0:
                    self.get_logger().info(f"Loop running - Frames processed: {frame_counter}")
                    last_log_time = now
                
                # ----------------------------------------------------
                # 1. RGB FRAME - PRIMO NODO
                # ----------------------------------------------------
                rgb_stamp = None
                if self.q_low:
                    pkt = self.q_low.tryGet()
                    if pkt:
                        try:
                            frame = pkt.getFrame()  # uint8 mono
                            if self.enhance_images:
                                # Converti a BGR per enhance_image, poi torna a GRAY
                                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                                enhanced = self.enhance_image(frame_bgr)
                                frame = cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY)
                            
                            stamp = self.get_clock().now().to_msg()
                            rgb_stamp = stamp

                            rgb_msg = self.bridge.cv2_to_imgmsg(frame, encoding="mono8")
                            rgb_msg.header.stamp = stamp
                            rgb_msg.header.frame_id = self.optical_frame_id
                            self.pub_rgb.publish(rgb_msg)

                            self.cam_info.header.stamp = stamp
                            self.pub_info.publish(self.cam_info)
                            
                            frame_counter += 1
                        except Exception as e:
                            self.get_logger().error(f"RGB publish error: {e}")

                # Alternanza pipeline Depth/SuperPoint
                if self.alternate_pipelines:
                    cycle_counter += 1
                    if cycle_counter - self.last_switch >= self.alternate_interval:
                        if self.active_pipeline == "depth":
                            self.active_pipeline = "superpoint"
                        else:
                            self.active_pipeline = "depth"
                        self.last_switch = cycle_counter

                # 2. DEPTH FRAME - SOLO SE PIPELINE ATTIVA
                if self.q_depth and (not self.alternate_pipelines or self.active_pipeline == "depth"):
                    dp = self.q_depth.tryGet()
                    if dp:
                        try:
                            depth = dp.getFrame().astype(np.float32) / 1000.0  # mm -> m

                            if rgb_stamp is not None:
                                stamp = rgb_stamp
                            else:
                                stamp = self.get_clock().now().to_msg()

                            depth_msg = self.bridge.cv2_to_imgmsg(depth, encoding="32FC1")
                            depth_msg.header.stamp = stamp
                            depth_msg.header.frame_id = self.optical_frame_id
                            self.pub_depth.publish(depth_msg)

                            stereo_info_msg = self._create_stereo_camera_info(
                                width=depth.shape[1],
                                height=depth.shape[0],
                                timestamp=stamp
                            )
                            self.pub_stereo_info.publish(stereo_info_msg)
                            
                        except Exception as e:
                            self.get_logger().error(f"Depth publish error: {e}")

                # ----------------------------------------------------
                # 3. YOLO DETECTIONS - PRIMO NODO
                # ----------------------------------------------------
                det_stamp = rgb_stamp if rgb_stamp is not None else self.get_clock().now().to_msg()
                
                frame_preview = None
                if self.q_preview:
                    pktp = self.q_preview.tryGet()
                    if pktp:
                        frame_preview = pktp.getCvFrame()
                        if rgb_stamp is None:
                            det_stamp = self.get_ros_time_from_dai(pktp.getTimestamp())
                        else:
                            det_stamp = rgb_stamp

                dets = []
                if self.q_nn and self.enable_nn:
                    nn_pkt = self.q_nn.tryGet()
                    if nn_pkt:
                        dets = self._decode_detections(nn_pkt, self.nn_w, self.nn_h)
                        if dets:
                            self._publish_detections(dets, det_stamp)

                if frame_preview is not None:
                    self._draw_and_publish(frame_preview, dets, det_stamp)

                # ----------------------------------------------------
                # 4. SUPERPOINT PROCESSING
                # ----------------------------------------------------
                if self.superpoint_cpu:
                    self._process_superpoint_cpu()
                elif not self.alternate_pipelines or self.active_pipeline == "superpoint":
                    superpoint_counter += 1
                    if superpoint_counter % self.superpoint_skip == 0:
                        self._process_superpoint()

                # ----------------------------------------------------
                # 5. IMU DATA - PRIMO NODO
                # ----------------------------------------------------
                if self.q_imu:
                    pkts = self.q_imu.tryGetAll()
                    if pkts:
                        for pkt in pkts:
                            samples = getattr(pkt, "packets", [pkt])
                            for s in samples:
                                try:
                                    imu_ts = self.get_ros_time_from_dai(pkt.getTimestamp())
                                    
                                    imu_msg = Imu()
                                    imu_msg.header.stamp = imu_ts
                                    imu_msg.header.frame_id = self.imu_frame_id
                                    
                                    accel = getattr(s, "acceleroMeter", None)
                                    gyro = getattr(s, "gyroscope", None)
                                    
                                    if accel:
                                        imu_msg.linear_acceleration.x = float(accel.x)
                                        imu_msg.linear_acceleration.y = float(accel.y)
                                        imu_msg.linear_acceleration.z = -float(accel.z)
                                    if gyro:
                                        imu_msg.angular_velocity.x = float(gyro.x)
                                        imu_msg.angular_velocity.y = float(gyro.y)
                                        imu_msg.angular_velocity.z = -float(gyro.z)
                                        
                                    imu_msg.orientation_covariance = [-1.0] * 9
                                    self.pub_imu.publish(imu_msg)
                                except Exception as e:
                                    self.get_logger().error(f"IMU publish error: {e}")

                # Breve pausa per non saturare CPU
                time.sleep(0.001)

            except Exception as e:
                self.get_logger().error(f"Loop error: {e}")
                time.sleep(0.1)

    # ================================================================
    # CLEANUP
    # ================================================================
    
    def destroy_node(self):
        """Clean shutdown"""
        self._running = False
        if self._loop_thread:
            self._loop_thread.join(timeout=2.0)
        if self.device:
            self.device.close()
        super().destroy_node()

def main():
    rclpy.init()
    node = OakDSuperPointNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()