#!/usr/bin/env python3
# oakd_camera_publisher_node.py - Versione Sincronizzata per RTAB-Map
# ROS2 node using DepthAI v3 API. 
# Fix: Usa timestamp hardware per sincronizzazione corretta RGB-Depth.

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
from cv_bridge import CvBridge

import depthai as dai
import numpy as np
import cv2

# Default model
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

class OakDv3Node(Node):
    def __init__(self):
        super().__init__("oakd_v3_node")

        self.rgb_buffer = {}
        self.MAX_SYNC_DT = 0.005


        # Parameters
        self.declare_parameter("optical_frame_id", "camera_optical_frame")
        self.declare_parameter("imu_frame_id", "imu_link")
        self.declare_parameter("enable_nn", True)
        self.declare_parameter("model", DEFAULT_MODEL)
        self.declare_parameter("nn_width", 512)
        self.declare_parameter("nn_height", 384)
        self.declare_parameter("nn_fps", 15.0)
        self.declare_parameter("low_w", 320)
        self.declare_parameter("low_h", 240)
        self.declare_parameter("low_fps", 10.0)
        self.declare_parameter("depth_fps", 10.0)
        self.declare_parameter("draw_detections", True)
        self.declare_parameter("debug", True)
        self.declare_parameter("enhance_images", True)
        self.declare_parameter("use_coco_names", True)

        # Load params
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

        # Publishers
        qos_best_effort = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        qos_reliable = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)

        self.pub_rgb = self.create_publisher(Image, "/oak/rgb/image_raw", qos_reliable)
        self.pub_info = self.create_publisher(CameraInfo, "/oak/rgb/camera_info", qos_reliable)
        self.pub_annot = self.create_publisher(Image, "/oak/rgb/image_annotated", qos_best_effort)
        self.pub_depth = self.create_publisher(Image, "/oak/stereo/image_raw", qos_reliable)
        self.pub_imu = self.create_publisher(Imu, "/oak/imu/data", qos_reliable)
        self.pub_detections = self.create_publisher(Detection2DArray, "/oak/detections", 10)

        # Bridge + camera info
        self.bridge = CvBridge()
        self._init_camera_info()

        # DepthAI pipeline state
        self.pipeline = None
        self.device = None
        self.q_low = None
        self.q_preview = None
        self.q_nn = None
        self.q_depth = None
        self.q_imu = None

        # Labels
        self.class_names = None
        self._try_load_labels_from_model_path()

        # Variabili di sincronizzazione
        self._running = False
        self._loop_thread = None
        
        # TIME SYNC: Calcoliamo l'offset tra ROS time e DepthAI device time
        # Questo verrà raffinato quando avviamo il device
        self.base_time_offset = 0.0

        self.get_logger().info("Starting pipeline construction...")
        
        try:
            if self._build_pipeline_v3():
                self._running = True
                
                # Calcolo offset temporale iniziale
                # Tempo sistema ROS (sec) - Tempo Device OAK (sec)
                # Questo ci permette di convertire i timestamp hardware in timestamp ROS
                ros_now = self.get_clock().now().nanoseconds / 1e9
                dai_now = dai.Clock.now().total_seconds()
                self.base_time_offset = ros_now - dai_now
                self.get_logger().info(f"Time offset initialized: {self.base_time_offset:.4f}s")

                self._loop_thread = threading.Thread(target=self._loop, daemon=True)
                self._loop_thread.start()
                self.get_logger().info("Node initialization complete!")
            else:
                self.get_logger().error("Failed to initialize pipeline")
        except Exception as e:
            self.get_logger().error(f"Initialization failed: {e}")

    def _init_camera_info(self):
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

    def _try_load_labels_from_model_path(self):
        try:
            if os.path.exists(self.model) and os.path.isfile(self.model):
                d = os.path.dirname(self.model)
                cfg = os.path.join(d, "config.json")
                if os.path.exists(cfg):
                    with open(cfg, "r") as f:
                        j = json.load(f)
                    if isinstance(j, dict):
                        if "labels" in j:
                            self.class_names = j["labels"]
                        elif "model" in j and "labels" in j["model"]:
                            self.class_names = j["model"]["labels"]
            
            if self.class_names is None and self.use_coco_names:
                self.class_names = COCO_CLASS_NAMES
                if self.debug:
                    self.get_logger().info(f"Using COCO class names ({len(self.class_names)} classes)")
        except Exception as e:
            if self.debug: self.get_logger().warning(f"Failed to load labels: {e}")
            if self.use_coco_names: self.class_names = COCO_CLASS_NAMES

    def get_ros_time_from_dai(self, dai_ts):
        """Converte il timestamp hardware di DepthAI in ROS Time msg"""
        ts_abs = self.base_time_offset + dai_ts.total_seconds()
        seconds = int(ts_abs)
        nanoseconds = int((ts_abs - seconds) * 1e9)
        return Time(seconds=seconds, nanoseconds=nanoseconds).to_msg()

    def _build_pipeline_v3(self) -> bool:
        try:
            p = dai.Pipeline()

            # 1. RGB Camera
            cam = p.create(dai.node.Camera)
            cam.setBoardSocket(dai.CameraBoardSocket.CAM_A)
            cam.setFps(self.fps)
            cam.setPreviewSize(self.nn_w, self.nn_h)  # Preview output for NN
            cam.setVideoSize(self.low_w, self.low_h)   # Video output for low res

            # 2. Neural Network (disabled for now - needs proper setup)
            self.enable_nn = False  # Disable NN until properly configured

            # 3. Stereo Depth
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
                stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.HIGH_DENSITY)
            except: pass
            stereo.setOutputSize(self.low_w, self.low_h)
            stereo.setDepthAlign(dai.CameraBoardSocket.CAM_A)
            stereo.setLeftRightCheck(True)
            stereo.setSubpixel(False)

            mono_l.out.link(stereo.left)
            mono_r.out.link(stereo.right)

            # 4. IMU
            imu = p.create(dai.node.IMU)
            imu.enableIMUSensor(dai.IMUSensor.ACCELEROMETER_RAW, 50)
            imu.enableIMUSensor(dai.IMUSensor.GYROSCOPE_RAW, 50)
            imu.setBatchReportThreshold(1)
            imu.setMaxBatchReports(5)

            # 5. Create Queues
            self.q_low = cam.video.createOutputQueue(maxSize=4, blocking=False)      # Low res video
            self.q_preview = cam.preview.createOutputQueue(maxSize=4, blocking=False) # NN preview
            self.q_depth = stereo.depth.createOutputQueue(maxSize=4, blocking=False)
            self.q_imu = imu.out.createOutputQueue(maxSize=50, blocking=False)

            # 6. Start device
            self.device = dai.Device(p)
            return True
        except Exception as e:
            self.get_logger().error(f"Pipeline build error: {e}")
            import traceback
            traceback.print_exc()
            return False
            self.get_logger().error(traceback.format_exc())
            return False

    def _decode_detections(self, pkt, w, h):
        dets = []
        try:
            if not pkt or not hasattr(pkt, "detections"): return dets
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
            pass
        return dets

    def enhance_image(self, image):
        if not self.enhance_images: return image
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            mean_brightness = np.mean(gray)
            TOO_DARK, TOO_BRIGHT = 40, 80
            
            if mean_brightness > TOO_BRIGHT: return image
            
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
                
                # Gamma finale
                gamma = 1.1
                inv_gamma = 1.0 / gamma
                table = np.array([(i / 255.0) ** inv_gamma * 255 for i in range(256)]).astype("uint8")
                return cv2.LUT(enhanced, table)
        except:
            return image

    def _draw_and_publish(self, frame, dets, stamp):
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
        except Exception: pass

    def _publish_detections(self, dets, stamp):
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
        except Exception: pass

    def _loop(self):
        self.get_logger().info("MAIN LOOP STARTED - Publishing data...")
        frame_counter = 0
        last_log_time = time.time()
        
        while self._running and rclpy.ok():
            try:
                rgb_stamp = None   # <<< FIX QUI
                now = time.time()
                if now - last_log_time > 2.0:
                    self.get_logger().info(f"Loop running - Frames processed: {frame_counter}")
                    last_log_time = now
                
                # --- 1. RGB ---
                # ===============================
                # RGB + DEPTH (SYNC RTAB-MAP SAFE)
                # ===============================

                # =========================
                # RGB
                # =========================
                if self.q_low:
                    pkt = self.q_low.tryGet()
                    if pkt:
                        try:
                            frame = pkt.getCvFrame()

                            # ROS time UNICO (NO timestamp hardware)
                            stamp = self.get_clock().now().to_msg()

                            rgb_msg = self.bridge.cv2_to_imgmsg(frame, encoding="bgr8")
                            rgb_msg.header.stamp = stamp
                            rgb_msg.header.frame_id = self.optical_frame_id
                            self.pub_rgb.publish(rgb_msg)

                            # CameraInfo ASSOCIATA ALLO STESSO STAMP
                            self.cam_info.header.stamp = stamp
                            self.pub_info.publish(self.cam_info)

                        except Exception as e:
                            self.get_logger().error(f"RGB publish error: {e}")


                # =========================
                # DEPTH
                # =========================
                if self.q_depth:
                    dp = self.q_depth.tryGet()
                    if dp:
                        try:
                            depth = dp.getFrame().astype(np.float32) / 1000.0  # mm -> m

                            # STESSO CLOCK ROS (non importa se non coincide perfettamente)
                            stamp = self.get_clock().now().to_msg()

                            depth_msg = self.bridge.cv2_to_imgmsg(depth, encoding="32FC1")
                            depth_msg.header.stamp = stamp
                            depth_msg.header.frame_id = self.optical_frame_id
                            self.pub_depth.publish(depth_msg)

                        except Exception as e:
                            self.get_logger().error(f"Depth publish error: {e}")



                # --- 2. PREVIEW & NN ---
                det_stamp = rgb_stamp if rgb_stamp else self.get_ros_time_from_dai(dai.Clock.now())
                
                # Preview frame
                frame_preview = None
                if self.q_preview:
                    pktp = self.q_preview.tryGet()
                    if pktp:
                        frame_preview = pktp.getCvFrame()
                        # Se abbiamo una preview ma non RGB, usiamo il timestamp della preview
                        if not rgb_stamp:
                            det_stamp = self.get_ros_time_from_dai(pktp.getTimestamp())

                # Detections
                dets = []
                if self.q_nn and self.enable_nn:
                    nn_pkt = self.q_nn.tryGet()
                    if nn_pkt:
                        dets = self._decode_detections(nn_pkt, self.nn_w, self.nn_h)
                        if hasattr(nn_pkt, 'getTimestamp'):
                             det_stamp = self.get_ros_time_from_dai(nn_pkt.getTimestamp())
                        if dets:
                            self._publish_detections(dets, det_stamp)

                # Annotazioni (debug view)
                if frame_preview is not None:
                    self._draw_and_publish(frame_preview, dets, det_stamp)

                

                # --- 4. IMU ---
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
                                        
                                    imu_msg.orientation_covariance = [-1.0]*9
                                    self.pub_imu.publish(imu_msg)
                                except Exception: pass

                time.sleep(0.001)

            except Exception as e:
                self.get_logger().error(f"Loop error: {e}")
                time.sleep(0.1)

    def destroy_node(self):
        self.get_logger().info("Stopping node...")
        self._running = False
        if self._loop_thread:
            self._loop_thread.join(timeout=2.0)
        try:
            if self.device: self.device.close()
        except: pass
        super().destroy_node()

def main():
    rclpy.init()
    node = OakDv3Node()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()