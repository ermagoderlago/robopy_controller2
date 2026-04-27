import rclpy
from rclpy.node import Node
from std_msgs.msg import ByteMultiArray, String
import os
import pygame
import threading
import io

class AudioPlayerNode(Node):
    def __init__(self):
        super().__init__('audio_player_node')
        self.subscription = self.create_subscription(
            ByteMultiArray,
            'audio_out',
            self.listener_callback,
            10)
        self.tts_subscription = self.create_subscription(
            String,
            'tts_out',
            self.tts_callback,
            10)
        
        # Inizializza Pygame mixer
        try:
            # Opzioni ottimizzate per Pi 5 / ALSA
            os.environ['SDL_AUDIODRIVER'] = 'alsa'
            # Se ReSpeaker non è ancora caricato, usiamo il default
            pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=2048)
            self.get_logger().info("🔊 [AUDIO PLAYER] Mixer inizializzato (24kHz, Mono)")
        except Exception as e:
            self.get_logger().error(f"❌ [AUDIO PLAYER] Errore inizializzazione mixer: {e}")

    def listener_callback(self, msg):
        """Riceve audio PCM (es. da Live API) e lo riproduce."""
        try:
            audio_data = bytes(msg.data)
            self.get_logger().info(f"🔊 [AUDIO PLAYER] Ricevuto chunk: {len(audio_data)} bytes")
            
            # Carica i dati audio in un buffer in memoria
            sound_buffer = io.BytesIO(audio_data)
            # Nota: pygame.mixer.Sound richiede un formato specifico o un file wav. 
            # In questo progetto assumiamo che i dati siano pronti per il mixer o usiamo il meccanismo di stream
            
            # Implementazione tipica Marcus:
            temp_sound = pygame.mixer.Sound(sound_buffer)
            temp_sound.play()
        except Exception as e:
            self.get_logger().error(f"❌ [AUDIO PLAYER] Errore riproduzione stream: {e}")

    def tts_callback(self, msg):
        """Riceve testo (da TTS legacy o debug) e lo logga o lo processa."""
        text = msg.data
        self.get_logger().info(f"🔊 [AUDIO PLAYER] Ricevuto TTS: {text}")

def main(args=None):
    rclpy.init(args=args)
    node = AudioPlayerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        pygame.mixer.quit()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
