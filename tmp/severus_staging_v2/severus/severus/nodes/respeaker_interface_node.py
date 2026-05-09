#!/usr/bin/env python3
"""
respeaker_interface_node.py
===========================
Bridge bidirezionale tra il ReSpeaker Lite (XIAO ESP32S3 via ESPHome v7)
e il sistema ROS 2 del robot.

Comunicazione:
  /dev/ttyACM0 (o configurabile) @ 921600 baud  <-->  ROS 2

Comandi supportati via /respeaker/audio_control (String):
  AUDIO_START, AUDIO_STOP, DIAG_ON, DIAG_OFF, PLAY_BEEP,
  PAUSA, RIPRENDI, STATO, SPEAKER_STOP, HEARTBEAT_REQ

Messaggi ricevuti dalla UART (ESP32S3 → Pi):
  READY               → log info, pubblica su /respeaker/status
  TRIGGER_JARVIS      → pubblica False su /ai/input/mic_mute
  HEARTBEAT           → pubblica su /respeaker/heartbeat
  STATO:ww=1,led=0:0:0 → pubblica su /respeaker/status
  AUDIO_LEVEL:N       → pubblica su /respeaker/audio_level
  AUDIO_PCM:<N>\n + N byte raw  → pubblica PCM su /audio/audio
  AUDIO_STATS:d:p     → pubblica su /respeaker/audio_stats
  STREAM:ON/OFF       → pubblica su /respeaker/streaming
  DIAG:ON/OFF         → log info
  WIFI_ON/OFF         → log info
  SPEAKER:CHUNK_DONE  → sblocca invio chunk successivo
  SPEAKER:OFF         → conferma stop speaker

Comandi inviati sulla UART (Pi → ESP32S3):
  /respeaker/led_command (String)     → inviato direttamente (es. "LED_EFFECT:THINKING")
  /respeaker/audio_control (String)   → AUDIO_START / AUDIO_STOP / DIAG_ON / DIAG_OFF
  /ai/tts/speaking (Bool)             → True = THINKING, False = IDLE
  /respeaker/speaker_audio (AudioData) → PCM int16 LE 16kHz da inviare allo speaker
"""

import threading
import queue
import time
import array

import serial

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from std_msgs.msg import Bool, String, Int32
from severus.msg import AudioData

UART_PORT_DEFAULT    = '/dev/ttyACM0'
UART_BAUD_DEFAULT    = 115200
RECONNECT_DELAY_S    = 3.0
HEARTBEAT_TIMEOUT_S  = 15.0


class ReSpeakerInterfaceNode(Node):
    """
    Nodo ROS 2 per la comunicazione seriale con il ReSpeaker Lite.
    Thread-safe tramite una queue di comandi TX.
    """

    def __init__(self):
        super().__init__('respeaker_interface_node')

        self._shutdown = False
        self._serial: serial.Serial | None = None
        
        self.declare_parameter('uart_port', UART_PORT_DEFAULT)
        self.declare_parameter('uart_baud', UART_BAUD_DEFAULT)
        self.declare_parameter('enabled', True)
        self.declare_parameter('enable_aec', True)
        self.declare_parameter('enable_agc', True)
        self.declare_parameter('enable_ns', True)
        self.declare_parameter('default_volume', 3)

        self._port    = self.get_parameter('uart_port').get_parameter_value().string_value
        self._baud    = self.get_parameter('uart_baud').get_parameter_value().integer_value
        self._enabled = self.get_parameter('enabled').get_parameter_value().bool_value
        
        self._init_aec = self.get_parameter('enable_aec').get_parameter_value().bool_value
        self._init_agc = self.get_parameter('enable_agc').get_parameter_value().bool_value
        self._init_ns  = self.get_parameter('enable_ns').get_parameter_value().bool_value
        self._init_vol = self.get_parameter('default_volume').get_parameter_value().integer_value

        self._serial_lock = threading.Lock()   # protegge _serial.write
        self._tx_queue: queue.Queue = queue.Queue()
        self._last_heartbeat = self.get_clock().now()
        self._boot_time_offset_ns = 0   # Usato per mappare ts ESP32 -> ROS

        if not self._enabled:
            self.get_logger().info("respeaker_interface_node disabilitato via parametro.")
            return

        # ── Publishers ──────────────────────────────────────────────────
        # QoS affidabile per audio stream (compatibile con orchestrator)
        qos_audio = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        self._mic_mute_pub   = self.create_publisher(Bool,      '/ai/input/mic_mute',    10)
        self._heartbeat_pub  = self.create_publisher(Bool,      '/respeaker/heartbeat',  10)
        self._audio_level_pub = self.create_publisher(Int32,    '/respeaker/audio_level', 10)
        self._status_pub     = self.create_publisher(String,    '/respeaker/status',      10)
        self._streaming_pub  = self.create_publisher(Bool,      '/respeaker/streaming',   10)
        self._audio_stats_pub = self.create_publisher(String,   '/respeaker/audio_stats', 10)

        # ── Subscribers ─────────────────────────────────────────────────
        self.create_subscription(
            String,    '/respeaker/led_command',   self._led_command_cb,   10)
        self.create_subscription(
            String,    '/respeaker/audio_control', self._audio_control_cb, 10)
        self.create_subscription(
            Bool,      '/ai/tts/speaking',         self._tts_speaking_cb,  10)
        self.create_subscription(
            Int32,     '/respeaker/volume',        self._volume_cb,        10)
        self.create_subscription(
            Bool,      '/respeaker/aec',           self._aec_cb,           10)
        self.create_subscription(
            Bool,      '/respeaker/agc',           self._agc_cb,           10)
        self.create_subscription(
            Bool,      '/respeaker/ns',            self._ns_cb,            10)

        # ── Thread seriale ──────────────────────────────────────────────
        self._rx_thread = threading.Thread(
            target=self._rx_loop, name='respeaker_rx', daemon=True)
        self._tx_thread = threading.Thread(
            target=self._tx_loop, name='respeaker_tx', daemon=True)

        self._connect()
        self._rx_thread.start()
        self._tx_thread.start()

        self.create_timer(10.0, self._check_heartbeat)

        self.get_logger().info(
            f"ReSpeaker Interface attivo — {self._port} @ {self._baud} baud")

    # ── Connessione seriale ─────────────────────────────────────────────

    def _connect(self) -> bool:
        try:
            if self._serial and self._serial.is_open:
                self._serial.close()
            self.get_logger().info(f"Porta seriale aperta: {self._port} @ {self._baud}")
            self._enqueue_command("PING\n")
            self._enqueue_command("LED_EFFECT:IDLE\n")
            
            # Attivazione funzioni DSP avanzate (Hardware)
            self.get_logger().info(f"DSP Init: AEC={self._init_aec}, AGC={self._init_agc}, NS={self._init_ns}")
            self._enqueue_command(f"DSP_AEC:{1 if self._init_aec else 0}\n")
            self._enqueue_command(f"DSP_AGC:{1 if self._init_agc else 0}\n")
            self._enqueue_command(f"DSP_NS:{1 if self._init_ns else 0}\n")
            
            # Impostazione volume iniziale (es. 3% per la notte)
            self.get_logger().info(f"Impostazione volume iniziale: {self._init_vol}%")
            self._enqueue_command(f"VOL:{self._init_vol}\n")
            
            return True
        except serial.SerialException as e:
            self.get_logger().warning(f"Impossibile aprire {self._port}: {e}")
            self._serial = None
            return False

    # ── RX loop ─────────────────────────────────────────────────────────

    def _rx_loop(self):
        while not self._shutdown:
            if not self._serial or not self._serial.is_open:
                time.sleep(RECONNECT_DELAY_S)
                self._connect()
                continue
            try:
                waiting = self._serial.in_waiting
                if not waiting:          # handles 0... and None
                    time.sleep(0.005)
                    continue

                line_bytes = self._serial.readline()
                if not line_bytes:
                    continue

                # ── Messaggi di controllo ASCII ──────────────────────────
                try:
                    line = line_bytes.decode('utf-8', errors='replace').strip()
                    # Rimuovi caratteri non stampabili (es. byte corrotti)
                    if line:
                        self._handle_rx(line)
                except Exception as e:
                    self.get_logger().warning(f"Errore elaborazione riga: {e}")

            except (serial.SerialException, TypeError, OSError) as e:
                if "returned no data" not in str(e):
                    self.get_logger().warning(f"Errore seriale RX: {e}")
                self._serial = None

    # (Rimosso _handle_audio_raw poiché l'audio è ora USB nativo)

    def _handle_rx(self, line: str):
        self.get_logger().debug(f"<- UART: {line}")

        # Boot completato
        if line == "READY":
            self.get_logger().info("ReSpeaker Lite pronto (boot OK)")
            msg = String(); msg.data = line
            self._status_pub.publish(msg)
            # Re-init: ripristina LED idle e forza heartbeat con PING
            self._enqueue_command("LED_EFFECT:IDLE\n")
            self._enqueue_command("PING\n")

        # Wake word
        elif line == "TRIGGER_JARVIS":
            self.get_logger().info("Wake word: JARVIS — apro microfono")
            msg = Bool(); msg.data = False   # False = microfono APERTO
            self._mic_mute_pub.publish(msg)
            self._enqueue_command("LED_EFFECT:LISTENING\n")

        # Heartbeat response
        elif line == "PONG":
            self._last_heartbeat = self.get_clock().now()
            msg = Bool(); msg.data = True
            self._heartbeat_pub.publish(msg)

        # Conferma comando
        elif line == "OK":
            self.get_logger().debug("ReSpeaker: OK")

        # Stato
        elif line.startswith("STATO:"):
            msg = String(); msg.data = line
            self._status_pub.publish(msg)

        # Livello audio (modalita' DIAG)
        elif line.startswith("AUDIO_LEVEL:"):
            try:
                level = int(line.split(":", 1)[1])
                msg = Int32(); msg.data = level
                self._audio_level_pub.publish(msg)
            except (ValueError, IndexError):
                pass

        # Statistiche backpressure audio (ogni 5s durante streaming)
        # Formato: AUDIO_STATS:<drops>:<partials>
        elif line.startswith("AUDIO_STATS:"):
            msg = String(); msg.data = line
            self._audio_stats_pub.publish(msg)
            try:
                parts = line[12:].split(":")
                drops, partials = int(parts[0]), int(parts[1])
                if drops > 0 or partials > 0:
                    self.get_logger().warning(
                        f"Audio backpressure: {drops} drop, {partials} write parziali — "
                        "considera AUDIO_STOP se persiste"
                    )
            except (ValueError, IndexError):
                pass

        # Stato streaming
        elif line == "STREAM:ON":
            msg = Bool(); msg.data = True
            self._streaming_pub.publish(msg)
            self.get_logger().info("Streaming audio ON")

        elif line == "STREAM:OFF":
            msg = Bool(); msg.data = False
            self._streaming_pub.publish(msg)
            self.get_logger().info("Streaming audio OFF")

        # (Rimosso SPEAKER:CHUNK_DONE poiché l'audio è ora USB nativo)

        elif line == "SPEAKER:OFF":
            self.get_logger().info("Speaker fermato")

        else:
            self.get_logger().debug(f"Messaggio UART: '{line}'")

    # ── TX loop ─────────────────────────────────────────────────────────

    def _tx_loop(self):
        while not self._shutdown:
            try:
                item = self._tx_queue.get(timeout=1.0)

                if not self._serial or not self._serial.is_open:
                    self.get_logger().warning("TX: porta non aperta, scarto comando.")
                    continue

                # item puo' essere str (comando ASCII) o bytes (payload binario)
                if isinstance(item, str):
                    with self._serial_lock:
                        self._serial.write(item.encode('utf-8'))
                    self.get_logger().debug(f"-> UART: {item.strip()}")
                elif isinstance(item, (bytes, bytearray)):
                    with self._serial_lock:
                        self._serial.write(item)
                    self.get_logger().debug(f"-> UART: [{len(item)} byte raw]")

            except queue.Empty:
                pass
            except serial.SerialException as e:
                self.get_logger().warning(f"Errore seriale TX: {e}")

    def _enqueue_command(self, cmd: str):
        """Accoda un comando ASCII."""
        self._tx_queue.put_nowait(cmd)

    def _enqueue_raw(self, data: bytes):
        """Accoda payload binario (es. PCM per speaker)."""
        self._tx_queue.put_nowait(data)

    # ── Speaker output (Pi → ESP → altoparlante) ────────────────────────

    # (Rimosso _speaker_audio_cb poiché l'audio è ora USB nativo)

    # ── Callbacks subscribers ASCII ─────────────────────────────────────

    def _led_command_cb(self, msg: String):
        """Invia direttamente il comando LED ricevuto dal topic."""
        cmd = msg.data.strip()
        # Supporta sia "IDLE" che "LED_EFFECT:IDLE" che "LED_RGB:255,0,0"
        if ":" not in cmd:
            cmd = f"LED_EFFECT:{cmd}"
        if not cmd.endswith('\n'):
            cmd += '\n'
        self._enqueue_command(cmd)

    def _volume_cb(self, msg: Int32):
        """Imposta il volume (0-100)."""
        self._enqueue_command(f"VOL:{msg.data}\n")

    def _aec_cb(self, msg: Bool):
        """Attiva/Disattiva AEC (Echo Cancellation)."""
        val = 1 if msg.data else 0
        self._enqueue_command(f"DSP_AEC:{val}\n")

    def _agc_cb(self, msg: Bool):
        """Attiva/Disattiva AGC (Automatic Gain Control)."""
        val = 1 if msg.data else 0
        self._enqueue_command(f"DSP_AGC:{val}\n")

    def _ns_cb(self, msg: Bool):
        """Attiva/Disattiva NS (Noise Suppression)."""
        val = 1 if msg.data else 0
        self._enqueue_command(f"DSP_NS:{val}\n")

    def _tts_speaking_cb(self, msg: Bool):
        """LED cyan pulsante mentre l'AI parla, IDLE quando finisce."""
        self._enqueue_command(
            "LED_EFFECT:THINKING\n" if msg.data else "LED_EFFECT:IDLE\n"
        )

    def _audio_control_cb(self, msg: String):
        """
        Comandi firmware inviati come stringa sul topic /respeaker/audio_control.
        Supportati: AUDIO_START, AUDIO_STOP, DIAG_ON, DIAG_OFF, PLAY_BEEP,
                    PAUSA, RIPRENDI, STATO, SPEAKER_STOP, HEARTBEAT_REQ.
        """
        cmd = msg.data.strip()
        if not cmd:
            return
        if not cmd.endswith('\n'):
            cmd += '\n'
        self._enqueue_command(cmd)

    # ── Heartbeat monitor ───────────────────────────────────────────────

        # Forza una risposta heartbeat dal firmware con PING
        self._enqueue_command("PING\n")

    # ── Shutdown ─────────────────────────────────────────────────────────

    def destroy_node(self):
        self._shutdown = True
        if getattr(self, '_enabled', False) and hasattr(self, '_tx_queue'):
            self._enqueue_command("LED_RGB:0,0,0\n")
        time.sleep(0.2)
        if getattr(self, '_serial', None) and self._serial.is_open:
            self._serial.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = ReSpeakerInterfaceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()