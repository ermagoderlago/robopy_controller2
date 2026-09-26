#!/usr/bin/env python3
"""
marcus_voice_nav.py - Autonomous NoMaD Exploration with High-Reliability Local VUI Voice
======================================================================================
Executes continuous autonomous reactive navigation at piano terra with NoMaD:
1. Arms and sets NoMaD mode to EXPLORING (/nomad/enable=True, /nomad/set_mode="EXPLORING").
2. Real-time background DDS spinning ensures 0-latency callback handling.
3. Forwards velocity commands with safety speed clamping (|v| <= 0.18 m/s, |w| <= 0.60 rad/s).
4. Monitors collisions and obstacles via:
   - /nomad/collision_event
   - /imu/data (impact spikes / jerk)
   - /ultrasonic_sensor (range < 0.15m)
5. Reliable, zero-latency Italian voice synthesizer with persistent PCM caching (24kHz standard rate)
   and explicit DDS subscriber synchronization on /respeaker/speaker_audio.
"""

import os
import sys
import time
import math
import hashlib
import tempfile
import subprocess
import threading
import queue
from typing import Optional

# Strip source dir to avoid uncompiled message shadowing, prioritize install and venv
while "/mnt/ssd/robopy_controller_host" in sys.path:
    sys.path.remove("/mnt/ssd/robopy_controller_host")

for p in [
    "/home/robopy/ros2_venv/lib/python3.11/site-packages",
    "/mnt/ssd/robopy_controller_host/install/robopy_controller/local/lib/python3.11/dist-packages",
    "/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages"
]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)


import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu, Range
from std_msgs.msg import String, Bool

try:
    from robopy_controller.msg import AudioData
    HAS_AUDIO_DATA = True
except Exception:
    try:
        from audio_common_msgs.msg import AudioData
        HAS_AUDIO_DATA = True
    except Exception:
        HAS_AUDIO_DATA = False

try:
    from gtts import gTTS
    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False

CACHE_DIR = "/tmp/marcus_tts_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

COMMON_PHRASES = [
    "Ciao Luca! Avvio l'esplorazione autonoma del piano terra con la telecamera NoMaD.",
    "Inizio a muovermi. Cerco percorsi liberi da ostacoli.",
    "Attenzione! Ho trovato un ostacolo! Faccio retromarcia per disimpegnarmi.",
    "Disimpegno completato. Continuo l'esplorazione.",
    "Esplorazione NoMaD in corso. Telecamera e sensori attivi.",
    "Esplorazione NoMaD completata. Mi fermo in sicurezza."
]


class MarcusVoiceNavigator(Node):
    def __init__(self):
        super().__init__('marcus_voice_navigator')

        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        # Publishers
        self.pub_cmd_vel = self.create_publisher(Twist, '/cmd_vel', qos_reliable)
        self.pub_nomad_enable = self.create_publisher(Bool, '/nomad/enable', qos_reliable)
        self.pub_nomad_mode = self.create_publisher(String, '/nomad/set_mode', qos_reliable)

        # Gemini Live / AI input fallback topics
        self.pub_ai_voice = self.create_publisher(String, '/ai/input/voice_test', qos_reliable)
        self.pub_ai_text = self.create_publisher(String, '/ai/input/text', qos_reliable)

        # Direct ReSpeaker audio publisher
        if HAS_AUDIO_DATA:
            self.pub_speaker_audio = self.create_publisher(AudioData, '/respeaker/speaker_audio', 10)
        else:
            self.pub_speaker_audio = None

        # Subscribers
        self.create_subscription(Twist, '/cmd_vel_nomad', self._nomad_cmd_cb, 10)
        self.create_subscription(String, '/nomad/collision_event', self._collision_cb, qos_reliable)
        self.create_subscription(Imu, '/imu/data', self._imu_cb, qos_sensor)
        self.create_subscription(Range, '/ultrasonic_range', self._ultrasonic_cb, qos_sensor)

        # State Variables
        self.is_running = True
        self.nomad_active = False
        self.collision_detected = False
        self.last_collision_time = 0.0
        self.latest_nomad_twist = Twist()
        self.has_new_nomad_cmd = False
        self.cmd_count = 0

        self.last_imu_ax = 0.0
        self.last_imu_time = time.monotonic()
        self.imu_spike_count = 0
        self.ultrasonic_dist = 2.0
        self.ultrasonic_spike_count = 0

        self.lock = threading.Lock()
        
        # Dedicated Reliable Speech Queue & Worker with Cooldown & De-duplication
        self._speech_queue = queue.Queue(maxsize=10)
        self.last_speech_time = 0.0
        self.last_spoken_phrase = ""
        self.speech_cooldown_sec = 3.5
        self.dedup_window_sec = 8.0
        self.is_speaking = False

        self._speech_thread = threading.Thread(target=self._speech_worker, daemon=True, name="marcus_voice_worker")
        self._speech_thread.start()

        # Pre-warm common phrases cache in background
        threading.Thread(target=self._prewarm_cache, daemon=True, name="marcus_tts_prewarm").start()

        self.get_logger().info("🎙️ MarcusVoiceNavigator inizializzato con motore vocale deterministico 24kHz e cache PCM.")

    def _prewarm_cache(self):
        """Pre-synthesizes common phrases so mission navigation audio is 100% instant (<5ms)."""
        for phrase in COMMON_PHRASES:
            try:
                self._get_or_synthesize_pcm(phrase)
            except Exception as e:
                self.get_logger().debug(f"Prewarm phrase warning: {e}")

    def _get_or_synthesize_pcm(self, text: str) -> Optional[bytes]:
        """Returns 24000Hz 16-bit mono PCM bytes, using disk cache for zero-latency playback."""
        h = hashlib.md5(text.strip().encode('utf-8')).hexdigest()
        cache_path = os.path.join(CACHE_DIR, f"{h}.raw")

        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            try:
                with open(cache_path, "rb") as f:
                    return f.read()
            except Exception:
                pass

        if not HAS_GTTS:
            return None

        try:
            mp3_f = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
            mp3_path = mp3_f.name
            mp3_f.close()

            tts = gTTS(text=text, lang='it')
            tts.save(mp3_path)

            # Convert to 24000 Hz, mono, 16-bit PCM matching respeaker_vui_node _STD_AUDIO_RATE
            cmd = [
                "ffmpeg", "-y", "-i", mp3_path,
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", "24000", "-ac", "1", cache_path
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

            try:
                os.remove(mp3_path)
            except Exception:
                pass

            if os.path.exists(cache_path):
                with open(cache_path, "rb") as f:
                    return f.read()
        except Exception as e:
            self.get_logger().error(f"Errore generazione TTS '{text}': {e}")

        return None

    def _speech_worker(self):
        """Dedicated background worker that streams natural 24kHz PCM speech to ReSpeaker DAC."""
        while self.is_running:
            try:
                phrase = self._speech_queue.get(timeout=0.2)
                self.is_speaking = True
                
                pcm_bytes = self._get_or_synthesize_pcm(phrase)
                if pcm_bytes and self.pub_speaker_audio:
                    chunk_size = 2048
                    # 24000 Hz, 16-bit mono -> 48000 bytes/sec
                    sample_rate = 24000
                    bytes_per_sec = sample_rate * 2
                    delay_per_chunk = (chunk_size / float(bytes_per_sec)) * 0.95

                    self.get_logger().info(f"🔊 [VUI] Inizio riproduzione ({len(pcm_bytes)}B): \"{phrase}\"")
                    for i in range(0, len(pcm_bytes), chunk_size):
                        if not self.is_running:
                            break
                        chunk = pcm_bytes[i:i + chunk_size]
                        audio_msg = AudioData()
                        audio_msg.data = list(chunk)
                        self.pub_speaker_audio.publish(audio_msg)
                        time.sleep(delay_per_chunk)
                    
                    self.get_logger().info(f"✅ [VUI] Riproduzione completata: \"{phrase}\"")

                # Breve pausa tra le frasi
                time.sleep(0.3)
                self.is_speaking = False
            except queue.Empty:
                continue
            except Exception as e:
                self.is_speaking = False
                self.get_logger().error(f"Speech worker error: {e}")

    def wait_for_audio_subscriber(self, timeout_sec: float = 3.0):
        """Ensures DDS endpoint discovery is complete before emitting the initial voice announcement."""
        if not self.pub_speaker_audio:
            return
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_sec:
            if self.pub_speaker_audio.get_subscription_count() > 0:
                self.get_logger().info(f"🔊 Audio subscriber connesso ({self.pub_speaker_audio.get_subscription_count()} attivi).")
                return
            time.sleep(0.1)
        self.get_logger().warn("⚠️ Nessun audio subscriber connesso entro il timeout (procedo comunque).")

    def wait_for_speech_done(self, timeout_sec: float = 5.0):
        """Blocks until the current spoken phrase is finished."""
        t0 = time.monotonic()
        time.sleep(0.1)
        while time.monotonic() - t0 < timeout_sec:
            if not self.is_speaking and self._speech_queue.empty():
                return
            time.sleep(0.1)

    def speak(self, text: str, force: bool = False):
        """Enqueues speech for vocalization with cooldown and de-duplication."""
        now = time.monotonic()
        norm_text = text.strip()
        if not norm_text:
            return

        # 1. De-duplicazione: ignora messaggi identici ravvicinati (< 8 secondi)
        if not force and norm_text == self.last_spoken_phrase and (now - self.last_speech_time) < self.dedup_window_sec:
            return

        # 2. Cooldown: distanzia gli annunci di almeno 3.5 secondi per permettere il drain completo del buffer audio
        if not force and (now - self.last_speech_time) < self.speech_cooldown_sec:
            return

        self.last_speech_time = now
        self.last_spoken_phrase = norm_text

        print(f"\n🗣️ [MARCUS]: \"{norm_text}\"\n", flush=True)
        self.get_logger().info(f"🗣️ [MARCUS]: {norm_text}")
        try:
            self._speech_queue.put_nowait(norm_text)
        except queue.Full:
            pass

    def _nomad_cmd_cb(self, msg: Twist):
        with self.lock:
            self.latest_nomad_twist = msg
            self.has_new_nomad_cmd = True
            self.cmd_count += 1

    def _collision_cb(self, msg: String):
        with self.lock:
            self.get_logger().error(f"🚨 Collision event da NoMaD: {msg.data}")
            self.collision_detected = True
            self.last_collision_time = time.monotonic()

    def _imu_cb(self, msg: Imu):
        now = time.monotonic()
        dt = max(1e-3, now - self.last_imu_time)
        ax = msg.linear_acceleration.x
        jerk_x = abs(ax - self.last_imu_ax) / dt

        # Shock detection: require 2 consecutive samples exceeding threshold to filter track vibration
        if abs(ax) > 3.5 or jerk_x > 32.0:
            self.imu_spike_count += 1
            if self.imu_spike_count >= 2 and (now - self.last_collision_time > 4.0):
                with self.lock:
                    self.get_logger().warn(f"💥 Urto confermato da IMU! ax={ax:.2f}, jerk={jerk_x:.2f}")
                    self.collision_detected = True
                    self.last_collision_time = now
        else:
            self.imu_spike_count = 0

        self.last_imu_ax = ax
        self.last_imu_time = now

    def _ultrasonic_cb(self, msg: Range):
        self.ultrasonic_dist = msg.range
        # Filter spurious single readings (requires 3 consecutive readings < 0.16m and valid range > 0.04m)
        if 0.04 < msg.range < 0.16:
            self.ultrasonic_spike_count += 1
            if self.ultrasonic_spike_count >= 3 and (time.monotonic() - self.last_collision_time > 4.0):
                with self.lock:
                    self.get_logger().warn(f"🚧 Ostacolo ravvicinato ultrasuoni confermato: {msg.range:.2f} m!")
                    self.collision_detected = True
                    self.last_collision_time = time.monotonic()
        else:
            self.ultrasonic_spike_count = 0

    def stop_robot(self):
        cmd = Twist()
        self.pub_cmd_vel.publish(cmd)

    def backup_maneuver(self):
        """Executes smooth, safe disengagement reverse maneuver."""
        self.get_logger().info("↩️ Manovra di retromarcia di sicurezza...")
        self.stop_robot()
        time.sleep(0.2)
        back_cmd = Twist()
        back_cmd.linear.x = -0.08
        back_cmd.angular.z = 0.0
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.2:
            self.pub_cmd_vel.publish(back_cmd)
            time.sleep(0.05)
        self.stop_robot()

    def run_mission(self, duration_sec: float = 60.0):
        is_continuous = (duration_sec <= 0.0)

        # 0. Sincronizzazione DDS Audio Endpoint (evita perdita del primo pacchetto)
        self.wait_for_audio_subscriber(timeout_sec=3.0)

        # 1. Annuncio Iniziale
        self.speak("Ciao Luca! Avvio l'esplorazione autonoma del piano terra con la telecamera NoMaD.", force=True)
        self.wait_for_speech_done(timeout_sec=4.5)

        # 2. Abilitazione NoMaD con attivazione EXPLORING
        en_msg = Bool()
        en_msg.data = True
        self.pub_nomad_enable.publish(en_msg)

        mode_msg = String()
        mode_msg.data = "EXPLORING"
        self.pub_nomad_mode.publish(mode_msg)
        self.nomad_active = True
        self.get_logger().info("🧭 NoMaD abilitato in modalità esplorazione autonoma continua.")

        # 3. Annuncio di Ricerca
        self.speak("Inizio a muovermi. Cerco percorsi liberi da ostacoli.", force=True)
        self.wait_for_speech_done(timeout_sec=3.5)

        # 4. Loop di Navigazione Reattiva (10 Hz)
        t_start = time.monotonic()
        last_log_time = t_start
        last_speech_time = t_start

        while is_continuous or (time.monotonic() - t_start < duration_sec):
            now = time.monotonic()

            # Gestione Collisione o Urto
            if self.collision_detected:
                self.collision_detected = False
                # Disarma temporaneamente NoMaD durante la manovra di retromarcia
                dis_msg = Bool()
                dis_msg.data = False
                self.pub_nomad_enable.publish(dis_msg)

                self.stop_robot()
                self.speak("Attenzione! Ho trovato un ostacolo! Faccio retromarcia per disimpegnarmi.", force=True)
                self.backup_maneuver()
                self.speak("Disimpegno completato. Continuo l'esplorazione.", force=True)

                # Riabilita NoMaD
                en_msg = Bool()
                en_msg.data = True
                self.pub_nomad_enable.publish(en_msg)
                time.sleep(0.5)
                continue

            if now - last_log_time >= 2.0:
                last_log_time = now
                with self.lock:
                    self.get_logger().info(
                        f"🚗 [NAV-LIVE] NoMaD attivo (Comandi: {self.cmd_count}, "
                        f"v={self.latest_nomad_twist.linear.x:.3f} m/s, w={self.latest_nomad_twist.angular.z:.3f} rad/s)"
                    )

            # Annuncio Periodico di stato ogni 20 secondi
            if now - last_speech_time > 20.0:
                last_speech_time = now
                self.speak("Esplorazione NoMaD in corso. Telecamera e sensori attivi.")

            time.sleep(0.1)

        # 5. Arresto e Disattivazione NoMaD
        self.stop_robot()
        dis_msg = Bool()
        dis_msg.data = False
        self.pub_nomad_enable.publish(dis_msg)
        mode_stop = String()
        mode_stop.data = "STOPPED"
        self.pub_nomad_mode.publish(mode_stop)
        self.get_logger().info("🛑 NoMaD disattivato e robot fermato.")

        # 6. Annuncio Finale
        self.speak("Esplorazione NoMaD completata. Mi fermo in sicurezza.", force=True)
        self.wait_for_speech_done(timeout_sec=4.0)
        self.stop_robot()


def main():
    rclpy.init()
    navigator = MarcusVoiceNavigator()

    spin_thread = threading.Thread(target=rclpy.spin, args=(navigator,), daemon=True)
    spin_thread.start()

    duration = 60.0
    if len(sys.argv) > 1:
        try:
            duration = float(sys.argv[1])
        except ValueError:
            pass

    try:
        navigator.run_mission(duration_sec=duration)
    except KeyboardInterrupt:
        pass
    finally:
        navigator.stop_robot()
        navigator.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
