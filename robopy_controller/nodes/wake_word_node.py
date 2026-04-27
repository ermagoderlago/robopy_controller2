#!/usr/bin/env python3
"""
Nodo Sentinella: Wake Word Detector
===================================
Versione ottimizzata:
- Riconoscimento "Marcus" via Porcupine personalizzato.
- Riproduzione beep.wav locale via jitter buffer dell'ESP32.
- Supporto per timestamp e AGC hardware.
"""

import os
import wave
import struct
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from robopy_controller.msg import AudioData
import pvporcupine
import numpy as np

# Carica le variabili d'ambiente (per la API Key)
def _load_keys():
    setup_keys_path = '/mnt/ssd/robopy_controller_host/setup_keys.sh'
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
            self.get_logger().error("PICOVOICE_API_KEY non trovata!")
            return

        # --- Inizializzazione Porcupine ---
        try:
            from ament_index_python.packages import get_package_share_directory
            pkg_share = get_package_share_directory('robopy_controller')
            keyword_path = os.path.join(pkg_share, 'config', 'wake_word', 'marcus.ppn')
            model_path = os.path.join(pkg_share, 'config', 'wake_word', 'porcupine_params_it.pv')
            
            # Caricamento Beep
            self._beep_data = None
            # Cerchiamo beep.wav in diverse posizioni comuni
            beep_paths = [
                os.path.join(pkg_share, 'resource', 'beep.wav'),
                os.path.join(os.getcwd(), 'beep.wav'),
                '/mnt/ssd/robopy_controller_host/beep.wav'
            ]
            for bp in beep_paths:
                if os.path.exists(bp):
                    self._beep_data = self._load_beep(bp)
                    if self._beep_data: 
                        self.get_logger().info(f"Beep caricato da: {bp}")
                        break

            if os.path.exists(keyword_path) and os.path.exists(model_path):
                self.get_logger().info(f"Caricamento 'Marcus' da: {keyword_path}")
                self.porcupine = pvporcupine.create(
                    access_key=access_key,
                    keyword_paths=[keyword_path],
                    model_path=model_path,
                    sensitivities=[0.8]
                )
            else:
                self.get_logger().warning("Keyword non trovata, uso default 'porcupine'.")
                self.porcupine = pvporcupine.create(access_key=access_key, keywords=['porcupine'])
        except Exception as e:
            self.get_logger().error(f"Errore Porcupine: {e}")
            return

        self.frame_length = self.porcupine.frame_length
        self.audio_buffer = bytes()

        # Publishers & Subscribers
        self.mute_pub   = self.create_publisher(Bool, '/ai/input/mic_mute', 10)
        self._speaker_pub = self.create_publisher(AudioData, '/respeaker/speaker_audio', 10)
        self.create_subscription(AudioData, '/audio/audio', self._audio_callback, 10)

        self.get_logger().info(f"👂 Sentinella attiva. Frame: {self.frame_length}")

    def _load_beep(self, path):
        try:
            with wave.open(path, 'rb') as wf:
                if wf.getnchannels() != 1 or wf.getframerate() != 16000:
                    self.get_logger().warning(f"Formato WAV non ottimale ({wf.getframerate()}Hz {wf.getnchannels()}ch)")
                return wf.readframes(wf.getnframes())
        except Exception as e:
            self.get_logger().error(f"Errore lettura WAV {path}: {e}")
            return None

    def _audio_callback(self, msg: AudioData):
        self.audio_buffer += bytes(msg.data)
        bytes_per_frame = self.frame_length * 2
        
        while len(self.audio_buffer) >= bytes_per_frame:
            frame_bytes = self.audio_buffer[:bytes_per_frame]
            self.audio_buffer = self.audio_buffer[bytes_per_frame:]
            
            pcm_raw = struct.unpack_from("h" * self.frame_length, frame_bytes)
            
            # RILEVAZIONE (Senza amplificazione software grazie all'AGC hardware)
            result = self.porcupine.process(pcm_raw)
            
            if result >= 0:
                self.get_logger().info("🔔 'MARCUS' RILEVATO!")
                
                # 1. Beep immediato via ROS 2 -> ESP32 Jitter Buffer
                if self._beep_data:
                    bmsg = AudioData()
                    bmsg.data = list(self._beep_data)
                    self._speaker_pub.publish(bmsg)
                
                # 2. Sblocca microfono AI
                unmute_msg = Bool()
                unmute_msg.data = False
                self.mute_pub.publish(unmute_msg)

    def destroy_node(self):
        if hasattr(self, 'porcupine'): self.porcupine.delete()
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
