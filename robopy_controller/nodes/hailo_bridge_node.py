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
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from cv_bridge import CvBridge

# ROS message types
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import String, Bool, Float32, Float32MultiArray
from vision_msgs.msg import Detection2DArray, Detection2D, BoundingBox2D, ObjectHypothesisWithPose
from geometry_msgs.msg import Vector3Stamped
from diagnostic_msgs.msg import DiagnosticStatus, KeyValue

# Custom package messages
from robopy_controller.msg import AudioData, SemanticObject, SemanticObjectArray

import math

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

COCO_TO_ITALIAN = {
    'person': 'persona',
    'chair': 'sedia',
    'couch': 'divano',
    'bed': 'letto',
    'dining table': 'tavolo',
    'bench': 'panchina',
    'backpack': 'zaino',
    'suitcase': 'valigia',
    'handbag': 'borsa',
    'bottle': 'bottiglia',
    'cup': 'tazza',
    'tv': 'televisore',
    'laptop': 'computer'
}

# Try importing HailoRT
try:
    from hailo_platform import (
        HEF, Device, VDevice, ConfigureParams,
        InputVStreamParams, OutputVStreamParams, FormatType, HailoStreamInterface,
        InputVStreams, OutputVStreams
    )
    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

# Import face alignment
try:
    from robopy_controller.robot_ai.utils.face_alignment import align_face
except ImportError:
    align_face = None


class NetVLADPooledCPU:
    def __init__(self, num_clusters=32, dim=128, seed=42):
        self.num_clusters = num_clusters
        self.dim = dim
        
        # Inizializza i pesi pseudo-casuali stabili per coerenza tra i riavvii del robot
        rng = np.random.default_rng(seed)
        self.centroids = rng.standard_normal((num_clusters, dim), dtype=np.float32)
        # Normalizzazione L2 dei centroidi
        self.centroids /= np.linalg.norm(self.centroids, axis=1, keepdims=True)
        
        # Convoluzione 1x1 equivalente a combinazione lineare conv_weights * x + bias
        self.conv_weights = rng.standard_normal((num_clusters, dim), dtype=np.float32)
        self.conv_bias = rng.standard_normal(num_clusters, dtype=np.float32)

    def pool(self, feature_map):
        if feature_map.ndim == 4:
            feature_map = feature_map[0]  # Rimuove dimensione batch -> (128, H, W) o (H, W, 128)
        
        # Se i canali (128) si trovano nell'ultimo asse, trasponiamo a (C, H, W)
        if feature_map.ndim == 3 and feature_map.shape[-1] == self.dim:
            feature_map = feature_map.transpose(2, 0, 1)
            
        C, H, W = feature_map.shape
        x = feature_map.reshape(C, -1)  # Flatten spaziale -> (128, H * W)
        
        # 1. Normalizzazione L2 dell'input lungo i canali
        norms = np.linalg.norm(x, axis=0, keepdims=True)
        norms[norms == 0] = 1.0
        x_norm = x / norms
        
        # 2. Soft-assignment (proiezione lineare + bias)
        soft_assign = np.matmul(self.conv_weights, x_norm) + self.conv_bias[:, np.newaxis]
        
        # Softmax lungo l'asse dei cluster (asse 0)
        soft_assign_exp = np.exp(soft_assign - np.max(soft_assign, axis=0, keepdims=True))
        soft_assign = soft_assign_exp / np.sum(soft_assign_exp, axis=0, keepdims=True)
        
        # 3. VLAD core: accumulo residui pesati
        vlad = np.zeros((self.num_clusters, self.dim), dtype=np.float32)
        for k in range(self.num_clusters):
            # residuo: (dim, H * W)
            res = x_norm - self.centroids[k][:, np.newaxis]
            # residuo pesato dalla soft assignment
            vlad[k] = np.sum(res * soft_assign[k], axis=1)
            
        # 4. Flattening e Normalizzazione L2 finale del descrittore globale
        vlad_flat = vlad.flatten()
        vlad_norm = np.linalg.norm(vlad_flat)
        if vlad_norm > 0:
            vlad_flat /= vlad_norm
            
        return vlad_flat


class FaceDatabase:
    """
    Database dei volti noti per il Face Recognition.
    
    Carica all'avvio gli embedding ArcFace (vettori 512-dim normalizzati L2)
    dai file .npy presenti in known_faces/<nome>/embedding.npy.
    Il confronto avviene tramite prodotto scalare (similarità del coseno),
    possibile perché i vettori sono già normalizzati L2.
    """

    def __init__(self, known_faces_dir: str, logger=None):
        self.logger = logger
        self.known_faces_dir = known_faces_dir
        # dict: nome_persona -> np.array(512,) normalizzato L2
        self._embeddings: dict = {}
        self.load()

    def load(self):
        """Scansiona known_faces_dir e carica tutti gli embedding.npy disponibili."""
        self._embeddings = {}
        if not os.path.exists(self.known_faces_dir):
            if self.logger:
                self.logger.warn(f"known_faces_dir non trovata: {self.known_faces_dir}")
            return
        
        count = 0
        for person_name in os.listdir(self.known_faces_dir):
            person_dir = os.path.join(self.known_faces_dir, person_name)
            if not os.path.isdir(person_dir):
                continue
            
            npy_path = os.path.join(person_dir, 'embedding.npy')
            if not os.path.exists(npy_path):
                if self.logger:
                    self.logger.warn(f"Nessun embedding.npy per '{person_name}'. Enrollment necessario.")
                continue
            
            try:
                emb = np.load(npy_path).astype(np.float32)
                # Verifica e normalizza L2 (per sicurezza)
                norm = np.linalg.norm(emb)
                if norm < 1e-6:
                    if self.logger:
                        self.logger.warn(f"Embedding nullo per '{person_name}', saltato.")
                    continue
                emb /= norm
                self._embeddings[person_name.lower()] = emb
                count += 1
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Errore caricamento embedding {npy_path}: {e}")
        
        if self.logger:
            self.logger.info(f"👥 FaceDatabase: {count} identità caricate → {list(self._embeddings.keys())}")

    def identify(self, embedding: np.ndarray, threshold: float = 0.45):
        """
        Confronta un embedding con tutti i volti noti.
        
        Args:
            embedding: vettore float32 (N,) — verrà normalizzato L2 internamente
            threshold: soglia minima di similarità coseno (0-1, default 0.45)
            
        Returns:
            (name: str, score: float) — name='unknown' se sotto soglia o DB vuoto
        """
        if len(self._embeddings) == 0:
            return "unknown", 0.0
        
        emb = np.array(embedding, dtype=np.float32).flatten()
        norm = np.linalg.norm(emb)
        if norm < 1e-6:
            return "unknown", 0.0
        emb /= norm
        
        best_name = "unknown"
        best_score = -1.0
        for name, ref_emb in self._embeddings.items():
            # Prodotto scalare = similarità coseno (vettori già normalizzati)
            score = float(np.dot(emb, ref_emb))
            if score > best_score:
                best_score = score
                best_name = name
        
        if best_score < threshold:
            return "unknown", best_score
        return best_name, best_score

    def add_face(self, name: str, embedding: np.ndarray):
        """
        Aggiunge o aggiorna un'identità nel DB in memoria e su disco.
        
        Args:
            name: nome persona (lowercase)
            embedding: vettore float32 512-dim (normalizzazione L2 applicata internamente)
        """
        emb = np.array(embedding, dtype=np.float32).flatten()
        norm = np.linalg.norm(emb)
        if norm < 1e-6:
            return
        emb /= norm
        
        self._embeddings[name.lower()] = emb
        
        # Salvataggio persistente su disco
        person_dir = os.path.join(self.known_faces_dir, name.lower())
        os.makedirs(person_dir, exist_ok=True)
        npy_path = os.path.join(person_dir, 'embedding.npy')
        np.save(npy_path, emb)
        if self.logger:
            self.logger.info(f"💾 Embedding salvato per '{name}' → {npy_path}")

    def get_count(self) -> int:
        return len(self._embeddings)

    def get_names(self) -> list:
        return list(self._embeddings.keys())


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
        self.declare_parameter('known_faces_dir', '/home/robopy/robopy/robopy_controller/known_faces')
        self.declare_parameter('publish_sim_sedia', False)
        self.declare_parameter('annotated_image_topic', '/hailo/annotated_image/compressed')
        self.declare_parameter('face_identity_threshold', 0.45)

        self.hef_path = self.get_parameter('hef_path').value
        self.vlm_rate = self.get_parameter('vlm_rate_hz').value
        self.face_rate = self.get_parameter('face_rate_hz').value
        self.enable_speaker_id = self.get_parameter('enable_speaker_id').value
        self.offline_mode = self.get_parameter('offline_mode_enabled').value
        self.sim_mode = self.get_parameter('sim_mode').value
        self.known_faces_dir = self.get_parameter('known_faces_dir').value
        self.publish_sim_sedia = self.get_parameter('publish_sim_sedia').value
        self.annotated_image_topic = self.get_parameter('annotated_image_topic').value
        self.face_identity_threshold = self.get_parameter('face_identity_threshold').value

        self.has_yolo = False
        self.has_scrfd = False
        self.has_arcface = False
        self.use_infer_model_api = False

        # State cache for image annotation
        self.latest_yolo_detections = []
        self.latest_face_detections = []
        self.latest_semantic_objects = []

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
            Image, '/rgb/image', self.rgb_callback, qos_best_effort
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
        # Enrollment runtime: pubblica il nome persona mentre è inquadrata per aggiungere/aggiornare identità
        self.sub_enroll_face = self.create_subscription(
            String, '/hailo/face/enroll', self.enroll_face_callback, qos_reliable
        )

        # Publishers
        self.pub_semantic_objects = self.create_publisher(
            SemanticObjectArray, '/hailo/vlm/semantic_objects', qos_reliable
        )
        self.pub_annotated_image = self.create_publisher(
            CompressedImage, self.annotated_image_topic, qos_best_effort
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
        # Publisher identità riconosciuta (nome persona o 'unknown')
        self.pub_face_identity = self.create_publisher(
            String, '/hailo/face/identity', qos_reliable
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
        
        # Publisher VPR NetVLAD
        self.pub_vpr_descriptor = self.create_publisher(
            Float32MultiArray, '/hailo/vpr/descriptor', qos_best_effort
        )

        # NetVLAD Pooler per calcolo su host CPU
        self.netvlad_pooler = NetVLADPooledCPU(num_clusters=32, dim=128, seed=42)
        self.has_netvlad = False

        # Threading state variables
        self.latest_rgb = None
        self.latest_depth = None
        self.lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self.bridge = CvBridge()
        
        # Enrollment runtime: accumulatore embedding per person name (dict: name -> list of np.arrays)
        self._enroll_buffer: dict = {}
        self._enroll_target_samples = 10  # Numero di campioni da accumulare prima di salvare
        
        # Face Database: caricamento embedding noti
        self.face_db = FaceDatabase(
            known_faces_dir=self._get_known_faces_dir(),
            logger=self.get_logger()
        )

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
        self.vpr_thread = threading.Thread(target=self.vpr_inference_loop)
        
        self.vlm_thread.daemon = True
        self.face_thread.daemon = True
        self.vpr_thread.daemon = True
        
        self.vlm_thread.start()
        self.face_thread.start()
        self.vpr_thread.start()

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
            
            # Prova a caricare con InferModel API (moderna API per Hailo-10H)
            try:
                self.infer_model = self.hailo_device.create_infer_model(self.hef_path)
                
                # Configura gli output stream su FormatType.FLOAT32 per dequantizzazione automatica
                for outp in self.infer_model.outputs:
                    try:
                        outp.set_format_type(FormatType.FLOAT32)
                    except Exception:
                        pass

                self.configured_infer_model = self.infer_model.configure()
                self.bindings = self.configured_infer_model.create_bindings()
                self.use_infer_model_api = True
                self.get_logger().info("✅ Modello configurato con successo via InferModel API (Output FLOAT32 dequantizzato)")
                
                # Identifica i model streams
                self.has_yolo = any('yolo' in inp.name.lower() for inp in self.infer_model.inputs)
                self.has_scrfd = any('scrfd' in inp.name.lower() for inp in self.infer_model.inputs)
                self.has_arcface = any('arcface' in inp.name.lower() or 'resnet50' in inp.name.lower() for inp in self.infer_model.inputs)
                self.has_netvlad = any('netvlad' in inp.name.lower() or 'mobilenet' in inp.name.lower() or 'vpr' in inp.name.lower() or 'features' in inp.name.lower() for inp in self.infer_model.inputs)
                
                # Preallochiamo i buffer: input (uint8) e output (float32 dequantizzato)
                self.npu_buffers = {}
                for inp in self.infer_model.inputs:
                    buf = np.zeros(inp.shape, dtype=np.uint8)
                    self.npu_buffers[inp.name] = buf
                    self.bindings.input(inp.name).set_buffer(buf)
                for outp in self.infer_model.outputs:
                    buf = np.empty(outp.shape, dtype=np.float32)
                    self.npu_buffers[outp.name] = buf
                    self.bindings.output(outp.name).set_buffer(buf)

                # Trova shape di input per YOLO
                yolo_input = [inp for inp in self.infer_model.inputs if 'yolo' in inp.name.lower()]
                if yolo_input:
                    shape = yolo_input[0].shape  # (H, W, C)
                    self.yolo_input_h = shape[0]
                    self.yolo_input_w = shape[1]
                else:
                    self.yolo_input_h = 640
                    self.yolo_input_w = 640
                    
            except Exception as e_infer:
                self.get_logger().warn(f"⚠️ InferModel API fallita ({e_infer}). Fallback a legacy VStream API...")
                self.use_infer_model_api = False
                self.hef = HEF(self.hef_path)

                # Configura tutti i network groups presenti nell'HEF (legacy)
                configure_params = ConfigureParams.create_from_hef(
                    self.hef, interface=HailoStreamInterface.PCIe
                )
                network_groups = self.hailo_device.configure(self.hef, configure_params)
                self.yolo_network_group = network_groups[0]

                # Identifica gli stream (legacy)
                input_infos = self.hef.get_input_stream_infos()
                self.has_yolo = any('yolo' in info.name.lower() for info in input_infos)
                self.has_scrfd = any('scrfd' in info.name.lower() for info in input_infos)
                self.has_arcface = any('arcface' in info.name.lower() or 'resnet50' in info.name.lower() for info in input_infos)
                self.has_netvlad = any('netvlad' in info.name.lower() or 'mobilenet' in info.name.lower() or 'vpr' in info.name.lower() or 'features' in info.name.lower() for info in input_infos)

                if self.has_yolo:
                    self.yolo_input_vstreams_params = InputVStreamParams.make(
                        self.yolo_network_group, quantized=False, format_type=FormatType.FLOAT32
                    )
                    self.yolo_output_vstreams_params = OutputVStreamParams.make(
                        self.yolo_network_group, quantized=False, format_type=FormatType.FLOAT32
                    )

                # Recupera shape dell'input YOLO
                yolo_input = [i for i in input_infos if 'yolo' in i.name.lower()]
                if yolo_input:
                    shape = yolo_input[0].shape  # (H, W, C)
                    self.yolo_input_h = shape[0]
                    self.yolo_input_w = shape[1]
                else:
                    self.yolo_input_h = 640
                    self.yolo_input_w = 640

            self.get_logger().info(f"✅ NPU Hailo inizializzato: YOLO={self.has_yolo}, SCRFD={self.has_scrfd}, ArcFace={self.has_arcface}, NetVLAD={self.has_netvlad}")

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

    def enroll_face_callback(self, msg):
        """
        Avvia o annulla l'enrollment di un nuovo volto.
        
        Pubblica il nome persona su /hailo/face/enroll per avviare l'enrollment.
        Il nodo accumula self._enroll_target_samples embedding ArcFace reali,
        calcola la media, normalizza L2 e salva il file .npy nella known_faces_dir.
        
        Formato msg.data:
          - 'luca'        → avvia enrollment per 'luca'
          - 'cancel:luca' → annulla enrollment in corso per 'luca'
          - 'reload'      → ricarica il FaceDatabase da disco
        """
        cmd = msg.data.strip().lower()
        
        if cmd == 'reload':
            self.face_db.load()
            self.get_logger().info(
                f"🔄 FaceDatabase ricaricato: {self.face_db.get_count()} identità → {self.face_db.get_names()}"
            )
            return
        
        if cmd.startswith('cancel:'):
            cancel_name = cmd.split(':', 1)[1].strip()
            with self.lock:
                removed = self._enroll_buffer.pop(cancel_name, None)
            if removed is not None:
                self.get_logger().info(f"❌ Enrollment annullato per '{cancel_name}'.")
            else:
                self.get_logger().warn(f"Nessun enrollment in corso per '{cancel_name}'.")
            return
        
        # Avvio enrollment
        person_name = cmd
        if not person_name:
            self.get_logger().warn("enroll_face_callback: nome persona vuoto, ignorato.")
            return
        
        with self.lock:
            if person_name in self._enroll_buffer:
                self.get_logger().warn(
                    f"⚠️ Enrollment per '{person_name}' già in corso "
                    f"({len(self._enroll_buffer[person_name])}/{self._enroll_target_samples} campioni). "
                    "Usa 'cancel:<nome>' per annullare."
                )
                return
            self._enroll_buffer[person_name] = []
        
        self.get_logger().info(
            f"📸 Enrollment avviato per '{person_name}'. "
            f"Mantieni il volto visibile alla telecamera per {self._enroll_target_samples} frame..."
        )

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
        """Decodifica un Image ROS raw in numpy BGR"""
        try:
            return self.bridge.imgmsg_to_cv2(rgb_msg, desired_encoding='bgr8')
        except Exception as e:
            self.get_logger().error(f"Errore cv_bridge decodifica immagine: {e}", throttle_duration_sec=5.0)
            return None

    def _preprocess_yolo(self, bgr_img):
        """Ridimensiona e normalizza per YOLO Hailo (float32, RGB, 0-1)"""
        resized = cv2.resize(bgr_img, (self.yolo_input_w, self.yolo_input_h))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        return (rgb.astype(np.float32) / 255.0)

    def _parse_yolo_output(self, raw_outputs, orig_w, orig_h,
                           conf_thresh=0.4, iou_thresh=0.45):
        """
        Postprocessing YOLO (formato YOLOv8 DFL multi-scale Hailo).
        raw_outputs: dict {layer_name: np.array}
        Ritorna lista di (x1,y1,x2,y2,conf,class_id) normalizzate [0,1].
        """
        boxes, scores, class_ids = [], [], []

        # Controlliamo se sono presenti gli head separati di YOLOv8 (bbox, cls per 3 scale)
        scales = [
            {'stride': 8,  'bbox': 'yolo/conv44', 'cls': 'yolo/conv45'},
            {'stride': 16, 'bbox': 'yolo/conv60', 'cls': 'yolo/conv61'},
            {'stride': 32, 'bbox': 'yolo/conv73', 'cls': 'yolo/conv74'},
        ]

        has_yolov8_heads = all(s['bbox'] in raw_outputs and s['cls'] in raw_outputs for s in scales)

        if has_yolov8_heads:
            def sigmoid_fn(x):
                return 1.0 / (1.0 + np.exp(-np.clip(x, -10.0, 10.0)))

            def decode_dfl_fn(reg_dist, reg_max=16):
                N = reg_dist.shape[0]
                reg_dist = reg_dist.reshape(N, 4, reg_max)
                prob = np.exp(reg_dist - np.max(reg_dist, axis=-1, keepdims=True))
                prob /= np.sum(prob, axis=-1, keepdims=True)
                weights = np.arange(reg_max, dtype=np.float32)
                return np.sum(prob * weights, axis=-1)  # (N, 4)

            for s in scales:
                cls_head = np.squeeze(raw_outputs[s['cls']]).astype(np.float32)
                bbox_head = np.squeeze(raw_outputs[s['bbox']]).astype(np.float32)
                
                H, W, C = cls_head.shape
                cls_prob = sigmoid_fn(cls_head.reshape(-1, C))
                
                max_scores = np.max(cls_prob, axis=-1)
                max_classes = np.argmax(cls_prob, axis=-1)
                
                mask = max_scores > conf_thresh
                if not np.any(mask):
                    continue
                    
                valid_scores = max_scores[mask]
                valid_classes = max_classes[mask]
                valid_bbox_raw = bbox_head.reshape(-1, 64)[mask]
                
                grid_y, grid_x = np.meshgrid(np.arange(H), np.arange(W), indexing='ij')
                grid_x = grid_x.reshape(-1)[mask]
                grid_y = grid_y.reshape(-1)[mask]
                
                dfl_offsets = decode_dfl_fn(valid_bbox_raw)
                
                cx = (grid_x + 0.5 + (dfl_offsets[:, 2] - dfl_offsets[:, 0]) / 2.0) * s['stride']
                cy = (grid_y + 0.5 + (dfl_offsets[:, 3] - dfl_offsets[:, 1]) / 2.0) * s['stride']
                w_px = (dfl_offsets[:, 0] + dfl_offsets[:, 2]) * s['stride']
                h_px = (dfl_offsets[:, 1] + dfl_offsets[:, 3]) * s['stride']
                
                x1 = np.clip((cx - w_px / 2.0) / self.yolo_input_w, 0.0, 1.0)
                y1 = np.clip((cy - h_px / 2.0) / self.yolo_input_h, 0.0, 1.0)
                x2 = np.clip((cx + w_px / 2.0) / self.yolo_input_w, 0.0, 1.0)
                y2 = np.clip((cy + h_px / 2.0) / self.yolo_input_h, 0.0, 1.0)
                
                for i in range(len(valid_scores)):
                    boxes.append([float(x1[i]), float(y1[i]), float(x2[i]), float(y2[i])])
                    scores.append(float(valid_scores[i]))
                    class_ids.append(int(valid_classes[i]))
        else:
            # Fallback legacy per output concatenati singoli (es. 85+ canali)
            for name, feat in raw_outputs.items():
                feat = np.squeeze(feat).astype(np.float32)
                if feat.ndim == 1:
                    feat = feat.reshape(1, -1)
                elif feat.ndim == 3:
                    feat = feat.reshape(-1, feat.shape[-1])

                if feat.shape[-1] < 85:
                    continue

                obj_conf = feat[:, 4]
                if np.any(obj_conf > 1.0) or np.any(obj_conf < 0.0):
                    obj_conf = 1.0 / (1.0 + np.exp(-np.clip(obj_conf, -10.0, 10.0)))
                    
                mask = obj_conf > conf_thresh
                feat = feat[mask]
                obj_conf = obj_conf[mask]
                if feat.shape[0] == 0:
                    continue

                cls_conf = feat[:, 5:]
                if np.any(cls_conf > 1.0) or np.any(cls_conf < 0.0):
                    cls_conf = 1.0 / (1.0 + np.exp(-np.clip(cls_conf, -10.0, 10.0)))
                    
                cls_ids = np.argmax(cls_conf, axis=1)
                cls_scores = cls_conf[np.arange(len(cls_ids)), cls_ids]
                final_scores = obj_conf * cls_scores

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
                        boxes.append([float(x1[i]), float(y1[i]), float(x2[i]), float(y2[i])])
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
            
            raw = {}
            if self.use_infer_model_api:
                yolo_input_name = [inp.name for inp in self.infer_model.inputs if 'yolo' in inp.name.lower()]
                yolo_output_names = [outp.name for outp in self.infer_model.outputs if 'yolo' in outp.name.lower()]
                
                if yolo_input_name and yolo_output_names:
                    # Preprocess per YOLO
                    resized = cv2.resize(bgr, (self.yolo_input_w, self.yolo_input_h))
                    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
                    if self.npu_buffers[yolo_input_name[0]].dtype == np.float32:
                        rgb = (rgb.astype(np.float32) / 255.0)
                    
                    with self._infer_lock:
                        self.bindings.input(yolo_input_name[0]).set_buffer(rgb)
                        for name in yolo_output_names:
                            self.bindings.output(name).set_buffer(self.npu_buffers[name])
                            
                        self.configured_infer_model.run([self.bindings], 1000)
                    raw = {name: self.npu_buffers[name] for name in yolo_output_names}
            else:
                inp = self._preprocess_yolo(bgr)
                with self.yolo_network_group.activate(self.yolo_network_group.get_activation_params()):
                    with InputVStreams(self.yolo_network_group,
                                      self.yolo_input_vstreams_params) as ivs, \
                         OutputVStreams(self.yolo_network_group,
                                       self.yolo_output_vstreams_params) as ovs:
                        input_names = list(ivs.keys())
                        yolo_input_name = [n for n in input_names if 'yolo' in n.lower()]
                        if yolo_input_name:
                            ivs[yolo_input_name[0]].send(inp)

                        for name, ov in ovs.items():
                            if 'yolo' in name.lower():
                                raw[name] = ov.recv()

            if not raw:
                return

            detections = self._parse_yolo_output(raw, orig_w, orig_h)
            with self.lock:
                self.latest_yolo_detections = detections

            det_array = Detection2DArray()
            det_array.header.stamp = rgb_msg.header.stamp
            det_array.header.frame_id = 'camera_optical_frame'

            sem_array = SemanticObjectArray()
            sem_array.header = det_array.header

            sem_list = []
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

                # Map to SemanticObject if relevant obstacle
                if class_name in COCO_TO_ITALIAN or class_name in ['person', 'chair', 'couch', 'bed', 'dining table', 'bench', 'backpack', 'suitcase', 'handbag', 'laptop', 'tv']:
                    obj = SemanticObject()
                    obj.header = sem_array.header
                    obj.label = COCO_TO_ITALIAN.get(class_name, class_name)
                    obj.confidence = float(score)
                    obj.bbox_2d = [float(x1), float(y1), float(x2), float(y2)]
                    
                    if class_name == 'person':
                        obj.semantic_class = 'person'
                    elif class_name in ['chair', 'couch', 'bed', 'dining table', 'bench']:
                        obj.semantic_class = 'furniture'
                    else:
                        obj.semantic_class = 'obstacle'
                        
                    # Estimate 3D position using the latest depth image
                    with self.lock:
                        depth_msg_copy = self.latest_depth
                    pos_3d = self.estimate_3d_position(x1, y1, x2, y2, depth_msg_copy)
                    if pos_3d is not None:
                        obj.centroid_3d.x = float(pos_3d[0])
                        obj.centroid_3d.y = float(pos_3d[1])
                        obj.centroid_3d.z = float(pos_3d[2])
                        obj.centroid_2d.x = float((x1 + x2) / 2.0)
                        obj.centroid_2d.y = float((y1 + y2) / 2.0)
                        obj.centroid_2d.z = 0.0
                    else:
                        obj.centroid_3d.x = 0.0
                        obj.centroid_3d.y = 0.0
                        obj.centroid_3d.z = 2.0
                        obj.centroid_2d.x = float((x1 + x2) / 2.0)
                        obj.centroid_2d.y = float((y1 + y2) / 2.0)
                        obj.centroid_2d.z = 0.0
                        
                    z_depth = obj.centroid_3d.z
                    obj.estimated_width_m = float((x2 - x1) * z_depth * 1.37)
                    obj.estimated_depth_m = float((y2 - y1) * z_depth * 1.02)
                    
                    sem_array.objects.append(obj)
                    sem_list.append(obj)

            self.pub_yolo_detections.publish(det_array)
            if sem_array.objects:
                self.pub_semantic_objects.publish(sem_array)
            with self.lock:
                self.latest_semantic_objects = sem_list

            if detections:
                self.get_logger().info(
                    f"🎯 YOLO: {len(detections)} detections", throttle_duration_sec=2.0)

        except Exception as e:
            self.get_logger().error(f"Errore YOLO Hailo: {e}", throttle_duration_sec=5.0)


    def run_vlm_inference(self, rgb_msg, depth_msg):
        """YOLO su Hailo (reale) + SemanticObjects (sim finché non c'è VLM reale)."""
        # YOLO reale su NPU
        if not self.sim_mode and (self.yolo_network_group is not None or (self.use_infer_model_api and self.has_yolo)):
            self.run_yolo_hailo(rgb_msg)

        # SemanticObjects: per ora in sim (il VLM Qwen2 non è in questo HEF)
        if self.sim_mode:
            if self.publish_sim_sedia:
                msg = SemanticObjectArray()
                msg.header.stamp = rgb_msg.header.stamp
                msg.header.frame_id = 'camera_optical_frame'
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

                with self.lock:
                    self.latest_semantic_objects = [obj1]
                    self.latest_yolo_detections = [(0.4, 0.3, 0.6, 0.7, 0.85, 56)]  # 56 is chair

                if self.offline_mode:
                    intent_msg = String()
                    intent_msg.data = '{"intent": "avoid_obstacle", "target": "sedia"}'
                    self.pub_offline_intent.publish(intent_msg)
            else:
                with self.lock:
                    self.latest_semantic_objects = []
                    self.latest_yolo_detections = []

        # Annotate and publish image (both in simulation and real mode)
        self.annotate_and_publish_image(rgb_msg, depth_msg)

    def execute_single_vlm(self, query):
        """Esegui VLM sincrono ad hoc guidato da prompt testuale"""
        self.get_logger().info(f"Query VLM spot avviata: '{query}'")
        # Inserire qui la logica di inferenza spot per risposte testuali offline
        if self.sim_mode:
            self.get_logger().info("Risposta VLM spot simulata.")

    def _get_known_faces_dir(self) -> str:
        if hasattr(self, 'known_faces_dir') and self.known_faces_dir and os.path.exists(self.known_faces_dir):
            return self.known_faces_dir
        script_dir = os.path.dirname(os.path.abspath(__file__))
        rel_path = os.path.join(os.path.dirname(script_dir), 'known_faces')
        if os.path.exists(rel_path):
            return rel_path
        return '/home/robopy/robopy/robopy_controller/known_faces'

    def run_face_inference_sim(self):
        """Simula il riconoscimento facciale caricando un file .npy noto per i test"""
        faces_dir = self._get_known_faces_dir()
        
        npy_files = []
        if os.path.exists(faces_dir):
            for root, dirs, files in os.walk(faces_dir):
                for file in files:
                    if file.endswith('.npy'):
                        name = os.path.splitext(file)[0]
                        npy_files.append((name, os.path.join(root, file)))

        if npy_files:
            idx = int(time.time() / 15) % len(npy_files)
            name, npy_path = npy_files[idx]
            try:
                emb = np.load(npy_path)
                if emb.shape == (512,):
                    det_array = Detection2DArray()
                    det_array.header.stamp = self.get_clock().now().to_msg()
                    det_array.header.frame_id = "camera_optical_frame"
                    
                    det = Detection2D()
                    det.header = det_array.header
                    det.bbox.center.position.x = 320.0
                    det.bbox.center.position.y = 240.0
                    det.bbox.size_x = 120.0
                    det.bbox.size_y = 120.0
                    
                    hyp = ObjectHypothesisWithPose()
                    hyp.hypothesis.class_id = name.lower()
                    hyp.hypothesis.score = 0.92
                    det.results.append(hyp)
                    det_array.detections.append(det)
                    
                    self.pub_face_detections.publish(det_array)
                    
                    with self.lock:
                        self.latest_face_detections = [(name.lower(), 0.92, (0.40625, 0.375, 0.59375, 0.625))]
                    
                    emb_msg = Float32MultiArray()
                    emb_msg.data = emb.tolist()
                    self.pub_face_embeddings.publish(emb_msg)
                    
                    emotion_msg = String()
                    emotion_msg.data = "HAPPY"
                    self.pub_face_emotions.publish(emotion_msg)
                    
                    gaze_msg = Vector3Stamped()
                    gaze_msg.header = det_array.header
                    gaze_msg.vector.x = 0.0
                    gaze_msg.vector.y = 0.0
                    gaze_msg.vector.z = 1.0
                    self.pub_gaze.publish(gaze_msg)
                    return
            except Exception as e:
                self.get_logger().error(f"Errore caricamento embedding simulato {npy_path}: {e}")

        det_array = Detection2DArray()
        det_array.header.stamp = self.get_clock().now().to_msg()
        det_array.header.frame_id = "camera_optical_frame"
        
        det = Detection2D()
        det.header = det_array.header
        det.bbox.center.position.x = 320.0
        det.bbox.center.position.y = 240.0
        det.bbox.size_x = 100.0
        det.bbox.size_y = 100.0
        
        hyp = ObjectHypothesisWithPose()
        hyp.hypothesis.class_id = "luca"
        hyp.hypothesis.score = 0.95
        det.results.append(hyp)
        det_array.detections.append(det)
        self.pub_face_detections.publish(det_array)
        
        with self.lock:
            self.latest_face_detections = [("luca", 0.95, (0.4375, 0.395, 0.5625, 0.604))]
        
        emb_msg = Float32MultiArray()
        emb_msg.data = [0.05] * 512
        self.pub_face_embeddings.publish(emb_msg)
        
        emotion_msg = String()
        emotion_msg.data = "NEUTRAL"
        self.pub_face_emotions.publish(emotion_msg)
        
        gaze_msg = Vector3Stamped()
        gaze_msg.header = det_array.header
        gaze_msg.vector.z = 1.0
        self.pub_gaze.publish(gaze_msg)

    def _parse_scrfd_output(self, raw_outputs, img_w, img_h, conf_thresh=0.5):
        """Parsa output SCRFD per estrarre bbox e 5 landmark"""
        faces = []
        try:
            nms_keys = [k for k in raw_outputs.keys() if 'nms' in k.lower()]
            if nms_keys:
                data = raw_outputs[nms_keys[0]]
                return faces

            scores_maps = {}
            bboxes_maps = {}
            kps_maps = {}
            
            for name, val in raw_outputs.items():
                if 'scrfd' not in name.lower():
                    continue
                val = np.squeeze(val)
                if val.shape[-1] == 1 or 'score' in name.lower() or 'cls' in name.lower():
                    scores_maps[name] = val
                elif val.shape[-1] == 4 or 'bbox' in name.lower() or 'reg' in name.lower():
                    bboxes_maps[name] = val
                elif val.shape[-1] == 10 or 'kps' in name.lower() or 'pts' in name.lower() or 'landmark' in name.lower():
                    kps_maps[name] = val

            if not scores_maps:
                return faces

            for name, score_map in scores_maps.items():
                h, w = score_map.shape[:2]
                stride = 640 // h if h > 0 else 8
                
                scores = 1.0 / (1.0 + np.exp(-score_map)) if np.min(score_map) < 0 else score_map
                y_indices, x_indices = np.where(scores > conf_thresh)[:2]
                
                bbox_map = None
                for b_name, b_val in bboxes_maps.items():
                    if b_val.shape[:2] == (h, w):
                        bbox_map = b_val
                        break
                        
                kps_map = None
                for k_name, k_val in kps_maps.items():
                    if k_val.shape[:2] == (h, w):
                        kps_map = k_val
                        break
                        
                if bbox_map is None:
                    continue
                    
                for y, x in zip(y_indices, x_indices):
                    score = float(scores[y, x])
                    
                    anchor_x = x * stride
                    anchor_y = y * stride
                    
                    dist = bbox_map[y, x] * stride
                    x1 = anchor_x - dist[0]
                    y1 = anchor_y - dist[1]
                    x2 = anchor_x + dist[2]
                    y2 = anchor_y + dist[3]
                    
                    x1_norm = max(0.0, x1 / 640.0)
                    y1_norm = max(0.0, y1 / 640.0)
                    x2_norm = min(1.0, x2 / 640.0)
                    y2_norm = min(1.0, y2 / 640.0)
                    
                    bbox_orig = [
                        int(x1_norm * img_w),
                        int(y1_norm * img_h),
                        int(x2_norm * img_w),
                        int(y2_norm * img_h)
                    ]
                    
                    landmarks = np.zeros((5, 2), dtype=np.float32)
                    if kps_map is not None:
                        kps_vals = kps_map[y, x] * stride
                        for i in range(5):
                            lm_x = anchor_x + kps_vals[i * 2]
                            lm_y = anchor_y + kps_vals[i * 2 + 1]
                            landmarks[i] = [
                                float(max(0.0, min(1.0, lm_x / 640.0)) * img_w),
                                float(max(0.0, min(1.0, lm_y / 640.0)) * img_h)
                            ]
                    else:
                        w_box = bbox_orig[2] - bbox_orig[0]
                        h_box = bbox_orig[3] - bbox_orig[1]
                        landmarks = np.array([
                            [bbox_orig[0] + w_box * 0.3, bbox_orig[1] + h_box * 0.45],
                            [bbox_orig[0] + w_box * 0.7, bbox_orig[1] + h_box * 0.45],
                            [bbox_orig[0] + w_box * 0.5, bbox_orig[1] + h_box * 0.6],
                            [bbox_orig[0] + w_box * 0.35, bbox_orig[1] + h_box * 0.8],
                            [bbox_orig[0] + w_box * 0.65, bbox_orig[1] + h_box * 0.8]
                        ], dtype=np.float32)
                        
                    faces.append({
                        "bbox": bbox_orig,
                        "landmarks": landmarks,
                        "score": score
                    })

            if len(faces) > 1:
                faces = sorted(faces, key=lambda f: f["score"], reverse=True)
                keep_faces = []
                for f in faces:
                    overlap = False
                    for kf in keep_faces:
                        b1 = f["bbox"]
                        b2 = kf["bbox"]
                        xi1 = max(b1[0], b2[0])
                        yi1 = max(b1[1], b2[1])
                        xi2 = min(b1[2], b2[2])
                        yi2 = min(b1[3], b2[3])
                        inter_area = max(0, xi2 - xi1) * max(0, yi2 - yi1)
                        union_area = (b1[2]-b1[0])*(b1[3]-b1[1]) + (b2[2]-b2[0])*(b2[3]-b2[1]) - inter_area
                        iou = inter_area / float(union_area) if union_area > 0 else 0
                        if iou > 0.4:
                            overlap = True
                            break
                    if not overlap:
                        keep_faces.append(f)
                faces = keep_faces

        except Exception as ex:
            self.get_logger().error(f"Errore decodifica SCRFD: {ex}")
        return faces

    def run_face_inference(self, rgb_msg):
        """Esegui face recognition (ArcFace) su Hailo/Simulato"""
        if self.sim_mode or not hasattr(self, 'has_scrfd') or not hasattr(self, 'has_arcface') or not self.has_scrfd or not self.has_arcface:
            self.run_face_inference_sim()
            return

        try:
            bgr = self._decode_rgb_msg(rgb_msg)
            if bgr is None:
                return
            orig_h, orig_w = bgr.shape[:2]

            scrfd_input_name = [inp.name for inp in self.infer_model.inputs if 'scrfd' in inp.name.lower()]
            scrfd_output_names = [outp.name for outp in self.infer_model.outputs if 'scrfd' in outp.name.lower()]
            
            if not scrfd_input_name or not scrfd_output_names:
                self.run_face_inference_sim()
                return
                
            scrfd_input_name = scrfd_input_name[0]
            inp_info = [inp for inp in self.infer_model.inputs if inp.name == scrfd_input_name][0]
            scrfd_h, scrfd_w = inp_info.shape[0], inp_info.shape[1]
            
            resized_scrfd = cv2.resize(bgr, (scrfd_w, scrfd_h))
            rgb_scrfd = cv2.cvtColor(resized_scrfd, cv2.COLOR_BGR2RGB)
            
            with self._infer_lock:
                self.bindings.input(scrfd_input_name).set_buffer(rgb_scrfd)
                for name in scrfd_output_names:
                    self.bindings.output(name).set_buffer(self.npu_buffers[name])
                    
                self.configured_infer_model.run([self.bindings], 1000)
            
            raw_scrfd_outputs = {name: self.npu_buffers[name] for name in scrfd_output_names}
            faces = self._parse_scrfd_output(raw_scrfd_outputs, orig_w, orig_h)
            
            if not faces:
                with self.lock:
                    self.latest_face_detections = []
                return
                
            arcface_input_name = [inp.name for inp in self.infer_model.inputs if 'arcface' in inp.name.lower() or 'resnet50' in inp.name.lower()]
            arcface_output_name = [outp.name for outp in self.infer_model.outputs if 'arcface' in outp.name.lower() or 'resnet50' in outp.name.lower()]
            
            if not arcface_input_name or not arcface_output_name:
                self.get_logger().error("ArcFace non configurato nell'NPU")
                return
                
            arcface_input_name = arcface_input_name[0]
            arcface_output_name = arcface_output_name[0]

            det_array = Detection2DArray()
            det_array.header.stamp = self.get_clock().now().to_msg()
            det_array.header.frame_id = "camera_optical_frame"
            
            face_list = []
            recognized_names = []
            for face in faces[:3]:
                bbox = face["bbox"]
                landmarks = face["landmarks"]
                score = face["score"]
                
                aligned_crop = align_face(bgr, landmarks, output_size=112) if align_face else None
                if aligned_crop is None:
                    x1, y1, x2, y2 = max(0, bbox[0]), max(0, bbox[1]), min(orig_w, bbox[2]), min(orig_h, bbox[3])
                    if x2 <= x1 or y2 <= y1:
                        continue
                    aligned_crop = cv2.resize(bgr[y1:y2, x1:x2], (112, 112))
                    
                rgb_crop = cv2.cvtColor(aligned_crop, cv2.COLOR_BGR2RGB)
                
                with self._infer_lock:
                    self.bindings.input(arcface_input_name).set_buffer(rgb_crop)
                    self.bindings.output(arcface_output_name).set_buffer(self.npu_buffers[arcface_output_name])
                    self.configured_infer_model.run([self.bindings], 1000)
                
                raw_embedding = self.npu_buffers[arcface_output_name].flatten().astype(np.float32)

                # ── Enrollment runtime: se in corso, accumula il campione ──
                with self.lock:
                    active_enroll = dict(self._enroll_buffer)
                for enroll_name, enroll_samples in active_enroll.items():
                    enroll_samples.append(raw_embedding.copy())
                    if len(enroll_samples) >= self._enroll_target_samples:
                        # Calcola embedding medio e normalizza L2
                        mean_emb = np.mean(np.stack(enroll_samples, axis=0), axis=0)
                        self.face_db.add_face(enroll_name, mean_emb)
                        with self.lock:
                            self._enroll_buffer.pop(enroll_name, None)
                        self.get_logger().info(
                            f"✅ Enrollment completato per '{enroll_name}' "
                            f"({self._enroll_target_samples} campioni salvati)."
                        )
                        identity_msg = String()
                        identity_msg.data = f"enrolled:{enroll_name}"
                        self.pub_face_identity.publish(identity_msg)

                # ── Matching coseno con FaceDatabase ──
                identity_name, identity_score = self.face_db.identify(
                    raw_embedding, threshold=self.face_identity_threshold
                )
                
                face_list.append((
                    identity_name,
                    float(identity_score),
                    (bbox[0]/orig_w, bbox[1]/orig_h, bbox[2]/orig_w, bbox[3]/orig_h)
                ))
                recognized_names.append(identity_name)
                
                # Pubblica embedding grezzo per eventuali nodi downstream
                emb_msg = Float32MultiArray()
                emb_msg.data = raw_embedding.tolist()
                self.pub_face_embeddings.publish(emb_msg)
                
                det = Detection2D()
                det.header = det_array.header
                det.bbox.center.position.x = float((bbox[0] + bbox[2]) / 2)
                det.bbox.center.position.y = float((bbox[1] + bbox[3]) / 2)
                det.bbox.size_x = float(bbox[2] - bbox[0])
                det.bbox.size_y = float(bbox[3] - bbox[1])
                
                hyp = ObjectHypothesisWithPose()
                hyp.hypothesis.class_id = identity_name
                hyp.hypothesis.score = float(identity_score)
                det.results.append(hyp)
                det_array.detections.append(det)
                
                self.get_logger().info(
                    f"👤 Volto riconosciuto: {identity_name} (score={identity_score:.3f})",
                    throttle_duration_sec=2.0
                )
                
            with self.lock:
                self.latest_face_detections = face_list

            if det_array.detections:
                self.pub_face_detections.publish(det_array)
                
                # Pubblica identità primaria (volto con score più alto)
                if recognized_names:
                    primary_name = recognized_names[0]
                    identity_msg = String()
                    identity_msg.data = primary_name
                    self.pub_face_identity.publish(identity_msg)
                
                emotion_msg = String()
                emotion_msg.data = "HAPPY"
                self.pub_face_emotions.publish(emotion_msg)
                
                first_face = faces[0]["bbox"]
                gaze_msg = Vector3Stamped()
                gaze_msg.header = det_array.header
                gaze_msg.vector.x = float((first_face[0] + first_face[2]) / 2 / orig_w - 0.5)
                gaze_msg.vector.y = float((first_face[1] + first_face[3]) / 2 / orig_h - 0.5)
                gaze_msg.vector.z = 1.0
                self.pub_gaze.publish(gaze_msg)

        except Exception as e:
            self.get_logger().error(f"Errore inferenza Face Hailo: {e}", throttle_duration_sec=5.0)



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
            # TODO: Leggere parametri reali da driver HailoRT tramite API C/C++ o utility CLI in futuro.
            # Al momento usiamo valori mock realistici per evitare dipendenze esterne bloccanti.
            try:
                temp = 48.5  # mock realistico o ricavato
                diag.level = DiagnosticStatus.OK
                diag.message = "NPU operating normally (Mocked diagnostics)"
                diag.values.append(KeyValue(key="temperature", value=str(temp)))
                diag.values.append(KeyValue(key="pcie_speed", value="Gen3 forced"))
                diag.values.append(KeyValue(key="npu_utilization", value="42%"))
            except Exception as e:
                diag.level = DiagnosticStatus.ERROR
                diag.message = f"Failed to retrieve NPU status: {e}"
                
        self.pub_health.publish(diag)

    def vpr_inference_loop(self):
        """Loop di inferenza per NetVLAD VPR (1.0 Hz di default)"""
        rate = 1.0
        interval = 1.0 / rate
        
        while self.running:
            start_time = time.time()
            
            with self.lock:
                rgb_msg = self.latest_rgb
                
            if rgb_msg is not None:
                self.run_netvlad_inference(rgb_msg)
                
            elapsed = time.time() - start_time
            sleep_time = max(0.0, interval - elapsed)
            time.sleep(sleep_time)

    def run_netvlad_inference(self, rgb_msg):
        """Esegui NetVLAD VPR backbone su NPU + pooling su CPU"""
        try:
            bgr = self._decode_rgb_msg(rgb_msg)
            if bgr is None:
                return

            if self.sim_mode:
                # Generazione descrittore simulato coerente basato sul colore medio dell'immagine
                mean_val = np.mean(bgr)
                rng = np.random.default_rng(int(mean_val * 100))
                vlad_descriptor = rng.standard_normal(4096, dtype=np.float32)
                vlad_descriptor /= np.linalg.norm(vlad_descriptor)
                
                msg = Float32MultiArray()
                msg.data = vlad_descriptor.tolist()
                self.pub_vpr_descriptor.publish(msg)
                return

            if not self.has_netvlad:
                return

            netvlad_input_name = [inp.name for inp in self.infer_model.inputs if 'netvlad' in inp.name.lower() or 'mobilenet' in inp.name.lower() or 'vpr' in inp.name.lower() or 'features' in inp.name.lower()]
            netvlad_output_names = [outp.name for outp in self.infer_model.outputs if 'netvlad' in outp.name.lower() or 'mobilenet' in outp.name.lower() or 'vpr' in outp.name.lower() or 'features' in outp.name.lower()]
            
            if not netvlad_input_name or not netvlad_output_names:
                return
                
            netvlad_input_name = netvlad_input_name[0]
            netvlad_output_name = netvlad_output_names[0]
            
            inp_info = [inp for inp in self.infer_model.inputs if inp.name == netvlad_input_name][0]
            netvlad_h, netvlad_w = inp_info.shape[0], inp_info.shape[1]
            
            resized = cv2.resize(bgr, (netvlad_w, netvlad_h))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            
            with self._infer_lock:
                self.bindings.input(netvlad_input_name).set_buffer(rgb)
                self.bindings.output(netvlad_output_name).set_buffer(self.npu_buffers[netvlad_output_name])
                
                self.configured_infer_model.run([self.bindings], 1000)
            
            feature_map = self.npu_buffers[netvlad_output_name].astype(np.float32)
            vlad_descriptor = self.netvlad_pooler.pool(feature_map)
            
            msg = Float32MultiArray()
            msg.data = vlad_descriptor.tolist()
            self.pub_vpr_descriptor.publish(msg)
            
        except Exception as e:
            self.get_logger().error(f"Errore NetVLAD VPR: {e}", throttle_duration_sec=5.0)

    def estimate_3d_position(self, x1, y1, x2, y2, depth_msg):
        if depth_msg is None:
            return None
        
        try:
            # Parse depth image safely
            is_32f = "32F" in depth_msg.encoding
            is_16bit = "16" in depth_msg.encoding or depth_msg.encoding == "mono16"
            
            if is_32f:
                dtype = np.float32
            elif is_16bit:
                dtype = np.uint16
            else:
                dtype = np.uint8
                
            depth_data = np.frombuffer(depth_msg.data, dtype=dtype)
            h, w = depth_msg.height, depth_msg.width
            if len(depth_data) != h * w:
                return None
                
            depth_img = depth_data.reshape(h, w)
            if is_32f:
                depth_img = depth_img.astype(np.float32)  # already in meters
            elif is_16bit:
                depth_img = depth_img.astype(np.float32) / 1000.0  # mm to meters
            else:
                depth_img = depth_img.astype(np.float32)
                
            # Get pixel coordinates of bounding box center
            cx_pixel = int((x1 + x2) / 2 * w)
            cy_pixel = int((y1 + y2) / 2 * h)
            
            cx_pixel = max(0, min(w - 1, cx_pixel))
            cy_pixel = max(0, min(h - 1, cy_pixel))
            
            # Read depth at center with 5x5 window filtering
            x_start = max(0, cx_pixel - 2)
            x_end = min(w, cx_pixel + 3)
            y_start = max(0, cy_pixel - 2)
            y_end = min(h, cy_pixel + 3)
            
            depth_window = depth_img[y_start:y_end, x_start:x_end]
            valid_depths = depth_window[~np.isnan(depth_window) & (depth_window > 0.1) & (depth_window < 10.0)]
            
            if len(valid_depths) == 0:
                depth = depth_img[cy_pixel, cx_pixel]
                if np.isnan(depth) or depth <= 0.1 or depth > 10.0:
                    return None
            else:
                depth = float(np.median(valid_depths))
                
            # Project 2D center to 3D camera coordinates using FOV approximation (OAK-D Lite: HFOV=69, VFOV=54)
            cx_norm = (x1 + x2) / 2
            cy_norm = (y1 + y2) / 2
            
            z = depth
            x = (cx_norm - 0.5) * depth * 1.37
            y = (cy_norm - 0.5) * depth * 1.02
            
            return (x, y, z)
        except Exception as e:
            self.get_logger().error(f"Error estimating 3D position: {e}", throttle_duration_sec=5.0)
            return None

    def annotate_and_publish_image(self, rgb_msg, depth_msg):
        try:
            bgr = self._decode_rgb_msg(rgb_msg)
            if bgr is None:
                return
            h, w = bgr.shape[:2]

            with self.lock:
                yolo_dets = list(self.latest_yolo_detections)
                face_dets = list(self.latest_face_detections)
                sem_objs = list(self.latest_semantic_objects)

            # Draw YOLO detections (Green boxes)
            for det in yolo_dets:
                x1, y1, x2, y2, score, cls_id = det
                px1, py1 = int(x1 * w), int(y1 * h)
                px2, py2 = int(x2 * w), int(y2 * h)
                
                class_name = COCO_CLASSES[cls_id] if cls_id < len(COCO_CLASSES) else str(cls_id)
                italian_name = COCO_TO_ITALIAN.get(class_name, class_name)
                label = f"{italian_name} {score:.2f}"
                
                cv2.rectangle(bgr, (px1, py1), (px2, py2), (0, 255, 0), 2)
                cv2.putText(bgr, label, (px1, max(15, py1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

            # Draw face detections (Blue/Orange boxes)
            for face in face_dets:
                name, score, bbox = face
                x1, y1, x2, y2 = bbox
                px1, py1 = int(x1 * w), int(y1 * h)
                px2, py2 = int(x2 * w), int(y2 * h)
                
                label = f"{name} {score:.2f}"
                cv2.rectangle(bgr, (px1, py1), (px2, py2), (255, 100, 0), 2)
                cv2.putText(bgr, label, (px1, max(15, py1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 100, 0), 1)

            # Draw semantic objects (Purple circles & labels)
            for obj in sem_objs:
                if len(obj.bbox_2d) == 4 and any(v != 0.0 for v in obj.bbox_2d):
                    px1 = int(obj.bbox_2d[0] * w)
                    py1 = int(obj.bbox_2d[1] * h)
                    px2 = int(obj.bbox_2d[2] * w)
                    py2 = int(obj.bbox_2d[3] * h)
                    
                    label = f"SEM: {obj.label} (conf:{obj.confidence:.2f})"
                    cv2.rectangle(bgr, (px1, py1), (px2, py2), (255, 0, 255), 2)
                    cv2.putText(bgr, label, (px1, max(15, py1 - 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)
                
                cx = int(obj.centroid_2d.x * w) if (0.0 <= obj.centroid_2d.x <= 1.0) else int((obj.bbox_2d[0] + obj.bbox_2d[2])/2 * w) if len(obj.bbox_2d) == 4 else w // 2
                cy = int(obj.centroid_2d.y * h) if (0.0 <= obj.centroid_2d.y <= 1.0) else int((obj.bbox_2d[1] + obj.bbox_2d[3])/2 * h) if len(obj.bbox_2d) == 4 else h // 2
                
                cv2.circle(bgr, (cx, cy), 5, (255, 0, 255), -1)
                pos_str = f"3D: [{obj.centroid_3d.x:.2f}, {obj.centroid_3d.y:.2f}, {obj.centroid_3d.z:.2f}]m"
                cv2.putText(bgr, pos_str, (cx + 8, cy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 255), 1)

            # Compress and publish
            success, encoded_img = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if success:
                comp_msg = CompressedImage()
                comp_msg.header = rgb_msg.header
                comp_msg.format = "jpeg"
                comp_msg.data = encoded_img.tobytes()
                self.pub_annotated_image.publish(comp_msg)
        except Exception as e:
            self.get_logger().error(f"Error drawing or publishing annotated image: {e}", throttle_duration_sec=5.0)

    def shutdown(self):
        """Ferma i thread del nodo"""
        self.get_logger().info("Chiusura hailo_bridge_node...")
        self.running = False
        if self.vlm_thread.is_alive():
            self.vlm_thread.join()
        if self.face_thread.is_alive():
            self.face_thread.join()
        if hasattr(self, 'vpr_thread') and self.vpr_thread.is_alive():
            self.vpr_thread.join()
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
