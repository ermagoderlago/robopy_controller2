#!/usr/bin/env python3
"""
Nodo Sentinella: Wake Word Detector
===================================
Ascolta il topic audio ROS 2 in background e, quando rileva la parola chiave,
pubblica 'False' sul topic /ai/input/mic_mute per svegliare l'Orchestrator.
"""

import os
import struct
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from audio_common_msgs.msg import AudioData
import pvporcupine

# Carica le variabili d'ambiente (per la API Key)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def _load_keys():
    setup_keys_path = '/home/robopy/robopy/robopi_controller/robopy_controller_host/setup_keys.sh'
    if os.path.exists(setup_keys_path):
        with open(setup_keys_path, 'r') as f:
            for line in f:
                if line.startswith('export PICOVOICE_API_KEY='):
                    val = line.split('=', 1)[1].strip().strip('"').strip("'")
                    os.environ['PICOVOICE_API_KEY'] = val

class WakeWordSentinel(Node):
    def __init__(self):
        super().__init__('wake_word_sentinel')
        
        _load_keys()
        access_key = os.environ.get('PICOVOICE_API_KEY')
        if not access_key:
            self.get_logger().error("PICOVOICE_API_KEY non trovata! Nodo interrotto.")
            return

        self.get_logger().info("Inizializzazione motore Wake Word...")
        
        try:
            # Percorso del file personalizzato "marcus.ppn" installato nel pacchetto
            # In ROS 2, cerchiamo in share/robopy_controller/config/wake_word/marcus.ppn
            from ament_index_python.packages import get_package_share_directory
            try:
                pkg_share = get_package_share_directory('robopy_controller')
                keyword_path = os.path.join(pkg_share, 'config', 'wake_word', 'marcus.ppn')
                model_path = os.path.join(pkg_share, 'config', 'wake_word', 'porcupine_params_it.pv')
                
                if os.path.exists(keyword_path) and os.path.exists(model_path):
                    self.get_logger().info(f"Caricamento parola chiave personalizzata: {keyword_path}")
                    self.porcupine = pvporcupine.create(
                        access_key=access_key,
                        keyword_paths=[keyword_path],
                        model_path=model_path
                    )
                else:
                    self.get_logger().warning(f"Keyword/Model file non trovati in {pkg_share}. Uso default 'porcupine'.")
                    self.porcupine = pvporcupine.create(
                        access_key=access_key,
                        keywords=['porcupine']
                    )
            except Exception as e:
                self.get_logger().error(f"Errore ricerca share directory: {e}. Uso default 'porcupine'.")
                self.porcupine = pvporcupine.create(
                    access_key=access_key,
                    keywords=['porcupine']
                )

        except Exception as e:
            self.get_logger().error(f"Errore caricamento Porcupine: {e}")
            return

        # ---------- Campi runtime necessari al callback ----------
        self.frame_length = self.porcupine.frame_length
        self.audio_buffer = bytes()

        # Publisher per sbloccare il microfono dell'orchestratore
        self.mute_pub = self.create_publisher(Bool, '/ai/input/mic_mute', 10)

        # Subscription al topic audio (registrata SOLO se Porcupine è pronto)
        self.create_subscription(AudioData, '/audio/audio', self._audio_callback, 10)

        self.get_logger().info(
            f"👂 Sentinella attiva. frame_length={self.frame_length}, "
            f"In attesa della parola magica..."
        )

    def _audio_callback(self, msg: AudioData):
        """Accumula l'audio di ROS 2 e lo analizza in cerca della Wake Word."""
        if not hasattr(self, 'porcupine'):
            return
        self.audio_buffer += msg.data
        
        # L'audio a 16-bit richiede 2 bytes per ogni campione (frame_length * 2)
        bytes_per_frame = self.frame_length * 2
        
        while len(self.audio_buffer) >= bytes_per_frame:
            # Estrai un chunk esatto per Porcupine
            frame_bytes = self.audio_buffer[:bytes_per_frame]
            self.audio_buffer = self.audio_buffer[bytes_per_frame:]
            
            # Converti i bytes raw in una lista di interi a 16-bit
            pcm = struct.unpack_from("h" * self.frame_length, frame_bytes)
            
            # Analizza
            result = self.porcupine.process(pcm)
            
            # Se result >= 0, la parola chiave è stata riconosciuta!
            if result >= 0:
                self.get_logger().info("🔔 WAKE WORD RILEVATA! Apro il canale per Gemini...")
                
                # Pubblichiamo False su mic_mute (che significa: APRI IL MICROFONO)
                unmute_msg = Bool()
                unmute_msg.data = False
                self.mute_pub.publish(unmute_msg)

    def destroy_node(self):
        if hasattr(self, 'porcupine'):
            self.porcupine.delete()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = WakeWordSentinel()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
