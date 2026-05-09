#!/usr/bin/env python3
"""
respeaker_interface_node.py
===========================
Bridge bidirezionale tra il ReSpeaker Lite (XIAO ESP32S3 via ESPHome)
e il sistema ROS 2 del robot.

Comunicazione:
  /dev/ttyACM0 (o configurabile) @ 115200 baud  ←→  ROS 2

Messaggi ricevuti dalla UART (ESP32S3 → Pi):
  TRIGGER_JARVIS\\n  → pubblica False su /ai/input/mic_mute (sblocca il microfono)
  HEARTBEAT\\n       → pubblica True su /respeaker/heartbeat
  STATO:ww=1,led=0:0:0\\n → log informativo
  AUDIO_LEVEL:N\\n   → pubblica su /respeaker/audio_level

Comandi inviati sulla UART (Pi → ESP32S3):
  /respeaker/led_command (String) → viene inviato direttamente (es. "LED_EFFECT:THINKING")
  /ai/tts/speaking (Bool)         → True = LED_EFFECT:THINKING, False = LED_EFFECT:IDLE
"""

import os
import serial
import serial.tools.list_ports
import threading
import queue
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String, Int32

UART_PORT_DEFAULT = '/dev/ttyACM0'
UART_BAUD_DEFAULT = 115200
RECONNECT_DELAY_S = 3.0
HEARTBEAT_TIMEOUT_S = 15.0  # Se non riceviamo heartbeat per N secondi → warning


class ReSpeakerInterfaceNode(Node):
    """
    Nodo ROS 2 che gestisce la comunicazione seriale con il ReSpeaker Lite.
    Thread-safe tramite una queue di comandi TX.
    """

    def __init__(self):
        super().__init__('respeaker_interface_node')

        # Parametri configurabili via ROS
        self.declare_parameter('uart_port', UART_PORT_DEFAULT)
        self.declare_parameter('uart_baud', UART_BAUD_DEFAULT)
        self.declare_parameter('enabled', True)

        self._port = self.get_parameter('uart_port').get_parameter_value().string_value
        self._baud = self.get_parameter('uart_baud').get_parameter_value().integer_value
        self._enabled = self.get_parameter('enabled').get_parameter_value().bool_value

        if not self._enabled:
            self.get_logger().info("respeaker_interface_node disabilitato via parametro.")
            return

        self._serial: serial.Serial | None = None
        self._tx_queue: queue.Queue = queue.Queue()
        self._shutdown = False
        self._last_heartbeat = self.get_clock().now()

        # ── Publishers ─────────────────────────────────────────
        self._mic_mute_pub = self.create_publisher(
            Bool, '/ai/input/mic_mute', 10)
        self._heartbeat_pub = self.create_publisher(
            Bool, '/respeaker/heartbeat', 10)
        self._audio_level_pub = self.create_publisher(
            Int32, '/respeaker/audio_level', 10)
        self._status_pub = self.create_publisher(
            String, '/respeaker/status', 10)

        # ── Subscribers ────────────────────────────────────────
        self.create_subscription(
            String, '/respeaker/led_command', self._led_command_cb, 10)
        self.create_subscription(
            Bool, '/ai/tts/speaking', self._tts_speaking_cb, 10)

        # ── Serial threads ─────────────────────────────────────
        self._rx_thread = threading.Thread(
            target=self._rx_loop, name='respeaker_rx', daemon=True)
        self._tx_thread = threading.Thread(
            target=self._tx_loop, name='respeaker_tx', daemon=True)

        self._connect()
        self._rx_thread.start()
        self._tx_thread.start()

        # Timer per monitorare heartbeat
        self.create_timer(10.0, self._check_heartbeat)

        self.get_logger().info(
            f"🎤 ReSpeaker Interface attivo — porta: {self._port} @ {self._baud} baud")

    # ── Connessione seriale ─────────────────────────────────────
    def _connect(self) -> bool:
        try:
            if self._serial and self._serial.is_open:
                self._serial.close()
            self._serial = serial.Serial(
                port=self._port,
                baudrate=self._baud,
                timeout=1.0,
                write_timeout=1.0
            )
            self.get_logger().info(f"✅ Porta seriale aperta: {self._port}")
            # Invia LED idle all'avvio
            self._enqueue_command("LED_EFFECT:IDLE\n")
            return True
        except serial.SerialException as e:
            self.get_logger().warning(f"⚠️ Impossibile aprire {self._port}: {e}")
            self._serial = None
            return False

    # ── RX loop (thread) ───────────────────────────────────────
    def _rx_loop(self):
        import time
        while not self._shutdown:
            if not self._serial or not self._serial.is_open:
                time.sleep(RECONNECT_DELAY_S)
                self._connect()
                continue
            try:
                raw = self._serial.readline()
                if not raw:
                    continue
                line = raw.decode('utf-8', errors='replace').strip()
                if line:
                    self._handle_rx(line)
            except serial.SerialException as e:
                self.get_logger().warning(f"❌ Errore seriale RX: {e}")
                self._serial = None
            except Exception as e:
                self.get_logger().warning(f"RX eccezione: {e}")

    def _handle_rx(self, line: str):
        """Processa una riga ricevuta dall'ESP32S3."""
        self.get_logger().debug(f"← UART: {line}")

        if line == "TRIGGER_JARVIS":
            self.get_logger().info("🔔 Wake Word rilevato dal ReSpeaker! Apro microfono...")
            msg = Bool()
            msg.data = False  # False = microfono APERTO
            self._mic_mute_pub.publish(msg)
            # Feedback LED: stiamo ascoltando
            self._enqueue_command("LED_EFFECT:LISTENING\n")

        elif line == "HEARTBEAT":
            self._last_heartbeat = self.get_clock().now()
            hb_msg = Bool()
            hb_msg.data = True
            self._heartbeat_pub.publish(hb_msg)

        elif line.startswith("STATO:"):
            status_msg = String()
            status_msg.data = line
            self._status_pub.publish(status_msg)
            self.get_logger().debug(f"Stato ReSpeaker: {line}")

        elif line.startswith("AUDIO_LEVEL:"):
            try:
                level = int(line.split(":")[1])
                level_msg = Int32()
                level_msg.data = level
                self._audio_level_pub.publish(level_msg)
            except ValueError:
                pass

        elif line.startswith("WIFI_ON") or line.startswith("WIFI_OFF"):
            self.get_logger().info(f"WiFi ESP32: {line}")

        else:
            self.get_logger().warning(f"⚠️ Messaggio UART non gestito: '{line}'")

    # ── TX loop (thread) ───────────────────────────────────────
    def _tx_loop(self):
        import time
        while not self._shutdown:
            try:
                cmd = self._tx_queue.get(timeout=1.0)
                if self._serial and self._serial.is_open:
                    self._serial.write(cmd.encode('utf-8'))
                    self.get_logger().debug(f"→ UART: {cmd.strip()}")
                else:
                    self.get_logger().warning("TX: porta non aperta, scarto comando.")
            except queue.Empty:
                pass
            except serial.SerialException as e:
                self.get_logger().warning(f"❌ Errore seriale TX: {e}")

    def _enqueue_command(self, cmd: str):
        """Accoda un comando da inviare all'ESP32S3."""
        self._tx_queue.put_nowait(cmd)

    # ── Callbacks subscribers ───────────────────────────────────
    def _led_command_cb(self, msg: String):
        """Invia direttamente il comando LED ricevuto dal topic."""
        cmd = msg.data.strip()
        if not cmd.endswith('\n'):
            cmd += '\n'
        self._enqueue_command(cmd)

    def _tts_speaking_cb(self, msg: Bool):
        """
        Quando l'AI sta parlando → LED cyan pulsante.
        Quando finisce → torna IDLE.
        """
        if msg.data:
            self._enqueue_command("LED_EFFECT:THINKING\n")
        else:
            self._enqueue_command("LED_EFFECT:IDLE\n")

    # ── Heartbeat monitor ──────────────────────────────────────
    def _check_heartbeat(self):
        elapsed = (self.get_clock().now() - self._last_heartbeat).nanoseconds / 1e9
        if elapsed > HEARTBEAT_TIMEOUT_S:
            self.get_logger().warning(
                f"⚠️ Nessun heartbeat dal ReSpeaker da {elapsed:.0f}s! "
                "Verifica connessione USB."
            )

    # ── Shutdown ───────────────────────────────────────────────
    def destroy_node(self):
        self._shutdown = True
        self._enqueue_command("LED_OFF\n")
        import time; time.sleep(0.2)
        if self._serial and self._serial.is_open:
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
