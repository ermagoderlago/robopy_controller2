#!/usr/bin/env python3
"""
Speaker ID Node
===============
ROS 2 Python node that performs voice biometric verification using
ECAPA-TDNN model on the Hailo-10H NPU (or simulated).

Version: 02.00.00
"""

import os
import sys
import time
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String, Bool, Float32
from robopy_controller.msg import AudioData

try:
    from hailo_platform import (
        HEF, VDevice, ConfigureParams, HailoStreamInterface,
        InputVStreamParams, OutputVStreamParams, FormatType,
        InputVStreams, OutputVStreams
    )
    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

from robopy_controller.robot_ai.services.speaker_recognition_service import SpeakerRecognitionService


class SpeakerIdNode(Node):
    def __init__(self):
        super().__init__('speaker_id_node')
        self.get_logger().info("Inizializzazione speaker_id_node...")

        # Parameters
        self.declare_parameter('speaker_hef_path', '')
        self.declare_parameter('min_speaker_confidence', 0.75)
        self.declare_parameter('sim_mode', not HAILO_AVAILABLE)
        self.declare_parameter('known_speakers_dir', '/home/robopy/robopy/robopy_controller/known_speakers')
        self.declare_parameter('mock_speaker', '')

        self.hef_path = self.get_parameter('speaker_hef_path').value
        self.min_confidence = self.get_parameter('min_speaker_confidence').value
        self.sim_mode = self.get_parameter('sim_mode').value
        self.known_speakers_dir = self.get_parameter('known_speakers_dir').value

        # QoS Settings
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

        # Subscriptions
        self.sub_audio = self.create_subscription(
            AudioData, '/ai/input/audio_chunk', self.audio_callback, qos_best_effort
        )
        self.sub_trigger_enrollment = self.create_subscription(
            String, '/speaker/trigger_enrollment', self.trigger_enrollment_callback, qos_reliable
        )

        # Publishers
        self.pub_verified = self.create_publisher(
            Bool, '/speaker/verified', qos_reliable
        )
        self.pub_identity = self.create_publisher(
            String, '/speaker/identity', qos_reliable
        )
        self.pub_confidence = self.create_publisher(
            Float32, '/speaker/confidence', qos_reliable
        )

        # Initialize Speaker ID Service
        os.makedirs(self.known_speakers_dir, exist_ok=True)
        self.speaker_service = SpeakerRecognitionService(
            known_speakers_dir=self.known_speakers_dir,
            confidence_high=self.min_confidence
        )

        # Audio chunk buffer
        self._audio_buffer = []

        # NPU components
        self.vdevice = None
        self.hef = None
        self.network_group = None
        self.network_group_params = None
        self.input_vstreams_params = None
        self.output_vstreams_params = None
        self.input_name = None
        self.output_name = None

        # Init hardware if needed
        if not self.sim_mode:
            self.init_hardware()

        self.get_logger().info("speaker_id_node avviato.")

    def init_hardware(self):
        try:
            if not os.path.exists(self.hef_path):
                self.get_logger().warn(f"File HEF per Speaker ID non trovato: {self.hef_path}. Esecuzione simulata.")
                self.sim_mode = True
                return
                
            self.get_logger().info(f"Modello Speaker ID HEF caricato da: {self.hef_path}")
            self.vdevice = VDevice()
            self.hef = HEF(self.hef_path)
            
            self.configure_params = ConfigureParams.create_from_hef(self.hef, interface=HailoStreamInterface.PCIe)
            self.network_group = self.vdevice.configure(self.hef, self.configure_params)[0]
            self.network_group_params = self.network_group.create_params()
            
            self.input_vstreams_params = InputVStreamParams.make_from_network_group(self.network_group, FormatType.FLOAT32)
            self.output_vstreams_params = OutputVStreamParams.make_from_network_group(self.network_group, FormatType.FLOAT32)
            
            self.input_name = list(self.input_vstreams_params.keys())[0]
            self.output_name = list(self.output_vstreams_params.keys())[0]
            
            self.get_logger().info(f"NPU Speaker ID configurata: Input={self.input_name}, Output={self.output_name}")
        except Exception as e:
            self.get_logger().error(f"Inizializzazione hardware NPU Speaker ID fallita: {e}. Passaggio a SIMULATION.")
            self.sim_mode = True

    def audio_callback(self, msg):
        """Accumula i chunk audio e avvia il processamento alla fine della frase"""
        if len(msg.data) > 0:
            self._audio_buffer.append(msg.data)
        else:
            # Chunk vuoto indica End-of-Speech
            self.process_accumulated_audio()

    def trigger_enrollment_callback(self, msg):
        name = msg.data
        if name:
            self.get_logger().info(f"Avvio enrollment speaker per: {name}")
            self.speaker_service.start_enrollment(name)
        else:
            self.get_logger().info("Cancellazione enrollment speaker")
            self.speaker_service.cancel_enrollment()

    def process_accumulated_audio(self):
        if not self._audio_buffer:
            return
            
        raw_bytes = b''.join(self._audio_buffer)
        self._audio_buffer = []  # Resetta il buffer per la frase successiva
        
        # Converte in campioni int16
        audio_data = np.frombuffer(raw_bytes, dtype=np.int16)
        if len(audio_data) < 8000:  # Meno di 0.5s @ 16kHz
            self.get_logger().info("Segmento audio troppo corto per la biometria vocale, ignorato.")
            return

        self.get_logger().info(f"Processamento segmento audio vocale ({len(audio_data)} campioni)...")
        embedding = self.extract_embedding(audio_data)
        if embedding is None:
            self.get_logger().warn("Estrazione embedding vocale fallita.")
            return

        result = self.speaker_service.process_speaker_embedding(embedding)

        # Pubblica risultati
        verified_msg = Bool()
        verified_msg.data = result.recognized
        self.pub_verified.publish(verified_msg)

        identity_msg = String()
        identity_msg.data = result.name
        self.pub_identity.publish(identity_msg)

        conf_msg = Float32()
        conf_msg.data = result.confidence
        self.pub_confidence.publish(conf_msg)
        
        self.get_logger().info(f"Speaker ID: riconosciuto={result.recognized}, identità='{result.name}', confidenza={result.confidence:.3f}")

    def extract_embedding(self, audio_data: np.ndarray) -> Optional[List[float]]:
        if self.sim_mode:
            # Generazione embedding simulato e deterministico
            import hashlib
            hasher = hashlib.sha256(audio_data.tobytes())
            hash_val = int(hasher.hexdigest(), 16)
            np.random.seed(hash_val % (2**32))
            mock_emb = np.random.randn(192).astype(np.float32)

            # Verifica se è forzata una specifica identità per i test
            mock_speaker_param = self.get_parameter('mock_speaker').value
            if mock_speaker_param:
                known_emb = self.speaker_service._known_embeddings.get(mock_speaker_param.lower())
                if known_emb is not None:
                    # Ritorna l'embedding noto con rumore minimo per simulare variabilità
                    simulated_emb = known_emb + np.random.normal(0, 0.01, 192).astype(np.float32)
                    norm = np.linalg.norm(simulated_emb)
                    if norm > 0:
                        simulated_emb /= norm
                    return simulated_emb.tolist()

            # Ritorna embedding casuale normalizzato L2
            norm = np.linalg.norm(mock_emb)
            if norm > 0:
                mock_emb /= norm
            return mock_emb.tolist()

        try:
            # Converti in float32 tra -1.0 e 1.0
            float_audio = audio_data.astype(np.float32) / 32768.0
            
            # Pad o troncamento a 3s (48000 campioni)
            target_len = 48000
            if len(float_audio) < target_len:
                float_audio = np.pad(float_audio, (0, target_len - len(float_audio)), 'constant')
            else:
                float_audio = float_audio[:target_len]

            # Esegui inferenza NPU
            with self.network_group.activate(self.network_group_params):
                with InputVStreams(self.network_group, self.input_vstreams_params) as input_vstreams:
                    with OutputVStreams(self.network_group, self.output_vstreams_params) as output_vstreams:
                        input_vstreams[self.input_name].write(float_audio.reshape(1, -1))
                        output_buffer = np.zeros(self.output_vstreams_params[self.output_name].shape, dtype=np.float32)
                        output_vstreams[self.output_name].read(output_buffer)
                        
                        emb = output_buffer.flatten().tolist()
                        return emb
        except Exception as e:
            self.get_logger().error(f"Errore inferenza NPU Speaker ID: {e}")
            return None


def main(args=None):
    rclpy.init(args=args)
    node = SpeakerIdNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
