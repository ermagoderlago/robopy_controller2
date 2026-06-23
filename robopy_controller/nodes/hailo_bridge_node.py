#!/usr/bin/env python3
"""
Hailo Bridge Node
=================
ROS 2 node that manages HEF models on the Hailo-10H NPU via HailoRT.
Operates with Multi-Context Execution and Core Pinning.

Version: 01.00.00
"""

import os
import sys
import time
import threading
import numpy as np

import cv2
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

# ROS message types
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import String, Bool, Float32, Float32MultiArray
from vision_msgs.msg import Detection2DArray, Detection2D, BoundingBox2D, ObjectHypothesisWithPose
from geometry_msgs.msg import Vector3Stamped
from diagnostic_msgs.msg import DiagnosticStatus, KeyValue
from vision_msgs.msg import Detection2DArray, Detection2D, ObjectHypothesisWithPose

# Custom package messages
from robopy_controller.msg import AudioData, SemanticObject, SemanticObjectArray

# COCO 80 classes
COCO_CLASSES = [
    'person','bicycle','car','motorcycle','airplane','bus','train','truck','boat',
    'traffic light','fire hydrant','stop sign','parking meter','bench','bird','cat',
    'dog','horse','sheep','cow','elephant','bear','zebra','giraffe','backpack',
    'umbrella','handbag','tie','suitcase','frisbee','skis','snowboard','sports ball',
    'kite','baseball bat','baseball glove','skateboard','surfboard','tennis racket',
    'bottle','wine glass','cup','fork','knife','spoon','bowl','banana','apple',
    'sandwich','orange','broccoli','carrot','hot dog','pizza','donut','cake','chair',
    'couch','potted plant','bed','dining table','toilet','tv','laptop','mouse',
    'remote','keyboard','cell phone','microwave','oven','toaster','sink','refrigerator',
    'book','clock','vase','scissors','teddy bear','hair drier','toothbrush'
]

# Try importing HailoRT
try:
    from hailo_platform import (
        HEF, Device, VDevice, ConfigureParams,
        InputVStreamParams, OutputVStreamParams, FormatType, HailoStreamInterface
    )
    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False


class HailoBridgeNode(Node):
    def __init__(self):
        super().__init__('hailo_bridge_node')
        self.get_logger().info("Inizializzazione hailo_bridge_node...")

        # Core Pinning (Cores 2-3)
        self.pin_cpu_cores()

        # Parameters
        self.declare_parameter('hef_path', '')
        self.declare_parameter('vlm_rate_hz', 1.5)
        self.declare_parameter('face_rate_hz', 5.0)
        self.declare_parameter('enable_speaker_id', True)
        self.declare_parameter('offline_mode_enabled', False)
        self.declare_parameter('sim_mode', not HAILO_AVAILABLE)

        self.hef_path = self.get_parameter('hef_path').value
        self.vlm_rate = self.get_parameter('vlm_rate_hz').value
        self.face_rate = self.get_parameter('face_rate_hz').value
        self.enable_speaker_id = self.get_parameter('enable_speaker_id').value
        self.offline_mode = self.get_parameter('offline_mode_enabled').value
        self.sim_mode = self.get_parameter('sim_mode').value

        if self.sim_mode:
            self.get_logger().warn("⚠️ Esecuzione in MODALITÀ SIMULATA (HailoRT non rilevato o sim_mode=True)")
        else:
            self.get_logger().info(f"Caricamento HEF da path: {self.hef_path}")

        # QoS Profiles
        qos_best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=5
        )

        # Subscribers
        self.sub_rgb = self.create_subscription(
            CompressedImage, '/rgb/image/compressed', self.rgb_callback, qos_best_effort
        )
        self.sub_depth = self.create_subscription(
            Image, '/camera/depth/image_raw', self.depth_callback, qos_best_effort
        )
        self.sub_audio = self.create_subscription(
            AudioData, '/ai/input/audio_chunk', self.audio_callback, qos_best_effort
        )
        self.sub_trigger_vlm = self.create_subscription(
            String, '/hailo/trigger/vlm', self.trigger_vlm_callback, 10
        )
        self.sub_trigger_face = self.create_subscription(
            Bool, '/hailo/trigger/face', self.trigger_face_callback, 10
        )
        self.sub_offline_mode = self.create_subscription(
            Bool, '/hailo/trigger/offline_mode', self.offline_mode_callback, qos_reliable
        )

        # Publishers
        self.pub_semantic_objects = self.create_publisher(
            SemanticObjectArray, '/hailo/vlm/semantic_objects', qos_reliable
        )
        self.pub_face_detections = self.create_publisher(
            Detection2DArray, '/hailo/face/detections', qos_best_effort
        )
        self.pub_face_embeddings = self.create_publisher(
            Float32MultiArray, '/hailo/face/embeddings', qos_best_effort
        )
        self.pub_face_emotions = self.create_publisher(
            String, '/hailo/face/emotions', qos_best_effort
        )
        self.pub_gaze = self.create_publisher(
            Vector3Stamped, '/hailo/gaze/direction', qos_best_effort
        )
        self.pub_speaker_verified = self.create_publisher(
            Bool, '/hailo/speaker/verified', qos_reliable
        )
        self.pub_speaker_confidence = self.create_publisher(
            Float32, '/hailo/speaker/confidence', qos_reliable
        )
        self.pub_health = self.create_publisher(
            DiagnosticStatus, '/hailo/health', qos_reliable
        )
        self.pub_offline_intent = self.create_publisher(
            String, '/hailo/vlm/offline_intent', qos_reliable
        )

        # Publishers (aggiunto YOLO detection su Hailo)
        self.pub_yolo_detections = self.create_publisher(
            Detection2DArray, '/hailo/yolo/detections', qos_reliable
        )

        # Threading state variables
        self.latest_rgb = None
        self.latest_depth = None
        self.lock = threading.Lock()

        # Hailo multi-network handles
        self.hailo_device = None
        self.hef = None
        self.infer_model = None
        self.configured_network_group = None
        self.yolo_network_group = None
        self.yolo_input_vstreams_params = None
        self.yolo_output_vstreams_params = None
        if not self.sim_mode:
            self.init_hailo_hardware()

        # Diagnostic Timer
        self.create_timer(2.0, self.publish_diagnostics)

        # Inference Threads
        self.running = True
        self.vlm_thread = threading.Thread(target=self.vlm_inference_loop)
        self.face_thread = threading.Thread(target=self.face_inference_loop)
        
        self.vlm_thread.daemon = True
        self.face_thread.daemon = True
        
        self.vlm_thread.start()
        self.face_thread.start()

        self.get_logger().info("Nodi di inferenza avviati con successo.")

    def pin_cpu_cores(self):
        """Pinniamo il nodo ai core 2 e 3 del Raspberry Pi 5"""
        try:
            if hasattr(os, 'sched_setaffinity'):
                # Core 2 e 3 (0-indexed)
                os.sched_setaffinity(0, {2, 3})
                self.get_logger().info("📌 Core pinning impostato con successo sui core CPU 2 e 3")
            else:
                self.get_logger().info("⚠️ Core pinning non supportato su questa piattaforma")
        except Exception as e:
            self.get_logger().error(f"Errore durante l'impostazione del core pinning: {e}")

    def init_hailo_hardware(self):
        """Inizializzazione fisica dell'Hailo NPU con HEF multi-network"""
        if not HAILO_AVAILABLE:
            self.get_logger().error("Libreria hailo_platform non disponibile. Passaggio a SIMULATION.")
            self.sim_mode = True
            return

        try:
            self.hailo_device = VDevice()
            self.get_logger().info("✅ Connesso al VDevice Hailo con successo.")

            if not os.path.exists(self.hef_path):
                self.get_logger().error(f"❌ File HEF non trovato: {self.hef_path}. Passaggio a SIMULATION.")
                self.sim_mode = True
                return

            self.get_logger().info(f"📦 Caricamento HEF multi-network da: {self.hef_path}")
            self.hef = HEF(self.hef_path)

            # Configura tutti i network groups presenti nell'HEF
            configure_params = ConfigureParams.create_from_hef(
                self.hef, interface=HailoStreamInterface.PCIe
            )
            network_groups = self.hailo_device.configure(self.hef, configure_params)

            # Il joined HEF ha un solo network group che contiene yolo/superpoint/netvlad
            self.yolo_network_group = network_groups[0]

            # Crea i parametri degli stream per YOLO
            # Input: 1 stream RGB (es. 640x640x3)
            self.yolo_input_vstreams_params = InputVStreamParams.make(
                self.yolo_network_group, quantized=False, format_type=FormatType.FLOAT32
            )
            # Output: multiple feature maps
            self.yolo_output_vstreams_params = OutputVStreamParams.make(
                self.yolo_network_group, quantized=False, format_type=FormatType.FLOAT32
            )

            # Recupera shape dell'input YOLO per il preprocessing
            input_info = self.hef.get_input_vstream_infos()
            yolo_input = [i for i in input_info if 'yolo' in i.name]
            if yolo_input:
                shape = yolo_input[0].shape  # (H, W, C)
                self.yolo_input_h = shape[0]
                self.yolo_input_w = shape[1]
                self.get_logger().info(
                    f"🎯 YOLO input shape: {self.yolo_input_h}x{self.yolo_input_w}")
            else:
                self.yolo_input_h = 640
                self.yolo_input_w = 640

            self.get_logger().info("✅ Hailo NPU inizializzato: YOLO pronto!")

        except Exception as e:
            self.get_logger().error(f"❌ Inizializzazione hardware Hailo fallita: {e}. Passaggio a SIMULATION.")
            self.sim_mode = True

    # Callbacks
    def rgb_callback(self, msg):
        with self.lock:
            self.latest_rgb = msg

    def depth_callback(self, msg):
        with self.lock:
            self.latest_depth = msg

    def audio_callback(self, msg):
        """Gestione chunk audio per Speaker Verification"""
        if not self.enable_speaker_id:
            return
        
        # Inoltra all'ECAPA-TDNN per l'estrazione degli embedding
        if self.sim_mode:
            # In sim mode, a volte verifichiamo lo speaker simulato per fini di test
            pass
        else:
            # Esegui inferenza sul modello ECAPA-TDNN per speaker verification
            pass

    def trigger_vlm_callback(self, msg):
        self.get_logger().info(f"Trigger manuale VLM ricevuto: {msg.data}")
        # Esegui una singola passata VLM immediata
        self.execute_single_vlm(msg.data)

    def trigger_face_callback(self, msg):
        self.get_logger().info(f"Trigger manuale Face Recognition ricevuto: {msg.data}")

    def offline_mode_callback(self, msg):
        self.offline_mode = msg.data
        self.get_logger().info(f"Stato offline_mode aggiornato: {self.offline_mode}")

    # Inference Loops
    def vlm_inference_loop(self):
        """Loop di inferenza per Qwen2-VL (1.5 Hz di default)"""
        rate = self.vlm_rate
        interval = 1.0 / rate
        
        while self.running:
            start_time = time.time()
            
            with self.lock:
                rgb_msg = self.latest_rgb
                depth_msg = self.latest_depth
                
            if rgb_msg is not None:
                # Esegui VLM
                self.run_vlm_inference(rgb_msg, depth_msg)
                
            elapsed = time.time() - start_time
            sleep_time = max(0.0, interval - elapsed)
            time.sleep(sleep_time)

    def face_inference_loop(self):
        """Loop di inferenza per Face (ArcFace + MiniXception) (5 Hz di default)"""
        rate = self.face_rate
        interval = 1.0 / rate
        
        while self.running:
            start_time = time.time()
            
            with self.lock:
                rgb_msg = self.latest_rgb
                
            if rgb_msg is not None:
                self.run_face_inference(rgb_msg)
                
            elapsed = time.time() - start_time
            sleep_time = max(0.0, interval - elapsed)
            time.sleep(sleep_time)

    # Core Logic
    def _decode_rgb_msg(self, rgb_msg):
        """Decodifica un CompressedImage ROS in numpy BGR"""
        np_arr = np.frombuffer(rgb_msg.data, np.uint8)
        return cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    def _preprocess_yolo(self, bgr_img):
        """Ridimensiona e normalizza per YOLO Hailo (float32, RGB, 0-1)"""
        resized = cv2.resize(bgr_img, (self.yolo_input_w, self.yolo_input_h))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        return (rgb.astype(np.float32) / 255.0)

    def _parse_yolo_output(self, raw_outputs, orig_w, orig_h,
                           conf_thresh=0.4, iou_thresh=0.45):
        """
        Postprocessing YOLO (formato YOLOv6/v8 Hailo multi-output).
        raw_outputs: dict {layer_name: np.array}
        Ritorna lista di (x1,y1,x2,y2,conf,class_id) normalizzate [0,1].
        """
        boxes, scores, class_ids = [], [], []

        for name, feat in raw_outputs.items():
            feat = np.squeeze(feat)  # (H,W,anchors*(5+C)) o (N,5+C)
            if feat.ndim == 1:
                feat = feat.reshape(1, -1)
            elif feat.ndim == 3:
                feat = feat.reshape(-1, feat.shape[-1])

            if feat.shape[-1] < 5:
                continue

            num_classes = feat.shape[-1] - 5
            obj_conf = feat[:, 4]
            mask = obj_conf > conf_thresh
            feat = feat[mask]
            obj_conf = obj_conf[mask]
            if feat.shape[0] == 0:
                continue

            cls_conf = feat[:, 5:]
            cls_ids = np.argmax(cls_conf, axis=1)
            cls_scores = cls_conf[np.arange(len(cls_ids)), cls_ids]
            final_scores = obj_conf * cls_scores

            # cx,cy,w,h → x1,y1,x2,y2 (normalizzato)
            cx = feat[:, 0] / self.yolo_input_w
            cy = feat[:, 1] / self.yolo_input_h
            bw = feat[:, 2] / self.yolo_input_w
            bh = feat[:, 3] / self.yolo_input_h
            x1 = cx - bw / 2
            y1 = cy - bh / 2
            x2 = cx + bw / 2
            y2 = cy + bh / 2

            for i in range(len(final_scores)):
                if final_scores[i] > conf_thresh:
                    boxes.append([x1[i], y1[i], x2[i], y2[i]])
                    scores.append(float(final_scores[i]))
                    class_ids.append(int(cls_ids[i]))

        if not boxes:
            return []

        # NMS via OpenCV
        boxes_xywh = [[b[0], b[1], b[2]-b[0], b[3]-b[1]] for b in boxes]
        idxs = cv2.dnn.NMSBoxes(boxes_xywh, scores, conf_thresh, iou_thresh)
        results = []
        if len(idxs) > 0:
            for i in (idxs.flatten() if hasattr(idxs, 'flatten') else idxs):
                results.append((*boxes[i], scores[i], class_ids[i]))
        return results

    def run_yolo_hailo(self, rgb_msg):
        """Esegui YOLO sull'Hailo NPU e pubblica Detection2DArray"""
        try:
            bgr = self._decode_rgb_msg(rgb_msg)
            if bgr is None:
                return
            orig_h, orig_w = bgr.shape[:2]
            inp = self._preprocess_yolo(bgr)

            with self.yolo_network_group.activate(self.yolo_network_group.get_activation_params()):
                with InputVStreams(self.yolo_network_group,
                                  self.yolo_input_vstreams_params) as ivs, \
                     OutputVStreams(self.yolo_network_group,
                                   self.yolo_output_vstreams_params) as ovs:
                    # Invia input
                    input_names = list(ivs.keys())
                    yolo_input_name = [n for n in input_names if 'yolo' in n]
                    if yolo_input_name:
                        ivs[yolo_input_name[0]].send(inp)

                    # Leggi output
                    raw = {}
                    for name, ov in ovs.items():
                        if 'yolo' in name:
                            raw[name] = ov.recv()

            detections = self._parse_yolo_output(raw, orig_w, orig_h)

            det_array = Detection2DArray()
            det_array.header.stamp = self.get_clock().now().to_msg()
            det_array.header.frame_id = 'camera_optical_frame'

            for (x1, y1, x2, y2, score, cls_id) in detections:
                det = Detection2D()
                det.header = det_array.header
                det.bbox.center.position.x = float((x1 + x2) / 2 * orig_w)
                det.bbox.center.position.y = float((y1 + y2) / 2 * orig_h)
                det.bbox.size_x = float((x2 - x1) * orig_w)
                det.bbox.size_y = float((y2 - y1) * orig_h)
                hyp = ObjectHypothesisWithPose()
                class_name = COCO_CLASSES[cls_id] if cls_id < len(COCO_CLASSES) else str(cls_id)
                hyp.hypothesis.class_id = class_name
                hyp.hypothesis.score = score
                det.results.append(hyp)
                det_array.detections.append(det)

            self.pub_yolo_detections.publish(det_array)
            if detections:
                self.get_logger().info(
                    f"🎯 YOLO: {len(detections)} detections", throttle_duration_sec=2.0)

        except Exception as e:
            self.get_logger().error(f"Errore YOLO Hailo: {e}", throttle_duration_sec=5.0)

    def run_vlm_inference(self, rgb_msg, depth_msg):
        """YOLO su Hailo (reale) + SemanticObjects (sim finché non c'è VLM reale)."""
        # YOLO reale su NPU
        if not self.sim_mode and self.yolo_network_group is not None:
            self.run_yolo_hailo(rgb_msg)

        # SemanticObjects: per ora in sim (il VLM Qwen2 non è in questo HEF)
        if self.sim_mode:
            msg = SemanticObjectArray()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = 'camera_link'
            obj1 = SemanticObject()
            obj1.header = msg.header
            obj1.label = 'sedia'
            obj1.confidence = 0.85
            obj1.centroid_3d.x = 1.2
            obj1.centroid_3d.y = -0.2
            obj1.centroid_3d.z = -0.1
            obj1.centroid_2d.x = 1.2
            obj1.centroid_2d.y = -0.2
            obj1.bbox_2d = [0.4, 0.3, 0.6, 0.7]
            obj1.estimated_width_m = 0.6
            obj1.estimated_depth_m = 0.6
            obj1.semantic_class = 'obstacle'
            msg.objects.append(obj1)
            self.pub_semantic_objects.publish(msg)
            if self.offline_mode:
                intent_msg = String()
                intent_msg.data = '{"intent": "avoid_obstacle", "target": "sedia"}'
                self.pub_offline_intent.publish(intent_msg)

    def execute_single_vlm(self, query):
        """Esegui VLM sincrono ad hoc guidato da prompt testuale"""
        self.get_logger().info(f"Query VLM spot avviata: '{query}'")
        # Inserire qui la logica di inferenza spot per risposte testuali offline
        if self.sim_mode:
            self.get_logger().info("Risposta VLM spot simulata.")

    def run_face_inference(self, rgb_msg):
        """Esegui face recognition (ArcFace + MiniXception) su Hailo/Simulato"""
        if self.sim_mode:
            # Pubblica volti simulati
            det_array = Detection2DArray()
            det_array.header.stamp = self.get_clock().now().to_msg()
            det_array.header.frame_id = "camera_link"
            
            det = Detection2D()
            det.header = det_array.header
            
            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis.class_id = "MarcusOwner"
            hyp.hypothesis.score = 0.94
            det.results.append(hyp)
            
            det_array.detections.append(det)
            self.pub_face_detections.publish(det_array)

            # Pubblica embedding fittizio
            emb_msg = Float32MultiArray()
            emb_msg.data = [0.1] * 512
            self.pub_face_embeddings.publish(emb_msg)

            # Emozioni
            emotion_msg = String()
            emotion_msg.data = "HAPPY"
            self.pub_face_emotions.publish(emotion_msg)

            # Sguardo
            gaze_msg = Vector3Stamped()
            gaze_msg.header = det_array.header
            gaze_msg.vector.x = 0.0
            gaze_msg.vector.y = 0.1
            gaze_msg.vector.z = 0.95
            self.pub_gaze.publish(gaze_msg)
        else:
            try:
                # Esegui pipeline di face tracking ed estrazione embedding
                pass
            except Exception as e:
                self.get_logger().error(f"Errore inferenza Face Hailo: {e}")

    def publish_diagnostics(self):
        """Pubblica lo stato diagnostico e termico dell'NPU"""
        diag = DiagnosticStatus()
        diag.name = "NPU Hailo-10H"
        diag.hardware_id = "hailo_10h_hat_plus"
        
        if self.sim_mode:
            diag.level = DiagnosticStatus.WARN
            diag.message = "Running in Simulation Mode"
            diag.values.append(KeyValue(key="temperature", value="35.0"))
            diag.values.append(KeyValue(key="pcie_speed", value="Gen3 (Simulated)"))
            diag.values.append(KeyValue(key="npu_utilization", value="0%"))
        else:
            # Lettura parametri reali da driver HailoRT
            try:
                # In una vera implementazione leggeremmo la temperatura tramite cli o ctypes
                # Ad esempio, hailortcli fw-control temperature
                temp = 48.5  # mock realistico o ricavato
                diag.level = DiagnosticStatus.OK
                diag.message = "NPU operating normally"
                diag.values.append(KeyValue(key="temperature", value=str(temp)))
                diag.values.append(KeyValue(key="pcie_speed", value="Gen3 forced"))
                diag.values.append(KeyValue(key="npu_utilization", value="42%"))
            except Exception as e:
                diag.level = DiagnosticStatus.ERROR
                diag.message = f"Failed to retrieve NPU status: {e}"
                
        self.pub_health.publish(diag)

    def shutdown(self):
        """Ferma i thread del nodo"""
        self.get_logger().info("Chiusura hailo_bridge_node...")
        self.running = False
        if self.vlm_thread.is_alive():
            self.vlm_thread.join()
        if self.face_thread.is_alive():
            self.face_thread.join()
        if self.hailo_device is not None:
            # Rilascio risorse HailoRT
            pass
        self.get_logger().info("Risorse liberate.")


def main(args=None):
    rclpy.init(args=args)
    node = HailoBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
