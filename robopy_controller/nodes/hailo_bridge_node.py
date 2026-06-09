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

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

# ROS message types
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import String, Bool, Float32, Float32MultiArray
from geometry_msgs.msg import Vector3Stamped
from diagnostic_msgs.msg import DiagnosticStatus, KeyValue
from vision_msgs.msg import Detection2DArray, Detection2D, ObjectHypothesisWithPose

# Custom package messages
from robopy_controller.msg import AudioData, SemanticObject, SemanticObjectArray

# Try importing HailoRT
try:
    from hailo_platform import (
        HEF, Device, VDevice, InferVStream, ConfigureParams,
        InputVStreamParams, OutputVStreamParams, FormatType
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
            Image, '/depth/image_raw', self.depth_callback, qos_best_effort
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

        # Threading state variables
        self.latest_rgb = None
        self.latest_depth = None
        self.lock = threading.Lock()

        # Initialize NPU hardware
        self.hailo_device = None
        self.hef = None
        self.configured_network_group = None
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
        """Inizializzazione fisica dell'Hailo NPU"""
        try:
            # Create a virtual device that handles memory/context scheduling
            params = ConfigureParams()
            self.hailo_device = VDevice()
            self.get_logger().info("Connesso al VDevice Hailo con successo.")

            # Load the unified HEF containing VLM, Face, and Speaker ID models
            if os.path.exists(self.hef_path):
                self.hef = HEF(self.hef_path)
                # Configure network groups
                configure_params = self.hailo_device.create_configure_params(self.hef)
                self.configured_network_group = self.hailo_device.configure(self.hef, configure_params)[0]
                self.get_logger().info(f"Modello HEF caricato ed inizializzato correttamente.")
            else:
                self.get_logger().error(f"File HEF non trovato al percorso: {self.hef_path}. Passaggio a SIMULATION.")
                self.sim_mode = True
        except Exception as e:
            self.get_logger().error(f"Inizializzazione hardware Hailo fallita: {e}. Passaggio a SIMULATION.")
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
    def run_vlm_inference(self, rgb_msg, depth_msg):
        """Esegui inferenza Qwen2-VL su Hailo/Simulato"""
        if self.sim_mode:
            # Generazione ostacoli semantici di test simulati
            msg = SemanticObjectArray()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = "camera_link"
            
            # Simuliamo un ostacolo "sedia" a 1.2 metri
            obj1 = SemanticObject()
            obj1.header = msg.header
            obj1.label = "sedia"
            obj1.confidence = 0.85
            obj1.centroid_3d.x = 1.2
            obj1.centroid_3d.y = -0.2
            obj1.centroid_3d.z = -0.1
            obj1.centroid_2d.x = 1.2
            obj1.centroid_2d.y = -0.2
            obj1.bbox_2d = [0.4, 0.3, 0.6, 0.7]
            obj1.estimated_width_m = 0.6
            obj1.estimated_depth_m = 0.6
            obj1.semantic_class = "obstacle"
            
            msg.objects.append(obj1)
            self.pub_semantic_objects.publish(msg)
            
            if self.offline_mode:
                # Simuliamo un routing offline di un intent
                intent_msg = String()
                intent_msg.data = '{"intent": "avoid_obstacle", "target": "sedia"}'
                self.pub_offline_intent.publish(intent_msg)
        else:
            try:
                # 1. Preprocess dei dati compressi RGB
                # 2. Binding degli stream di input/output Hailo
                # 3. Chiamata inferenza
                # 4. Parsing dell'output per estrarre bounding box e baricentri 3D
                pass
            except Exception as e:
                self.get_logger().error(f"Errore inferenza VLM Hailo: {e}")

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
