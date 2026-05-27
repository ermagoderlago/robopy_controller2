#!/usr/bin/env python3
"""respeaker_interface_node.py
===========================
Bridge bidirezionale tra il ReSpeaker Lite (XIAO ESP32S3 via ESPHome)
e il sistema ROS 2 del robot.

Comunicazione:
  /dev/ttyACM0 (USB JTAG/serial debug unit) ←→ ROS 2

Protocollo firmware (respeaker_lite_firmware.yaml v11.0):
  Comandi accettati dal firmware:
    HEARTBEAT_REQ\n    → firmware risponde HEARTBEAT\n
    AUDIO_START\n      → firmware risponde STREAM:ON\n
    AUDIO_STOP\n       → firmware risponde STREAM:OFF\n
    DIAG_ON/OFF\n      → firmware risponde DIAG:ON/OFF\n
    SPEAKER_STOP\n     → firmware risponde SPEAKER:OFF\n
    LED_EFFECT:X\n     → X = IDLE|LISTENING|THINKING|SUCCESS|ERROR|OFF
    LED_RGB:R,G,B\n    → colore diretto (0-255)
    LED_OFF\n          → spegni LED
    AUDIO_OUT:<size>\n → firmware riceve <size> bytes PCM per speaker

  Messaggi inviati dal firmware:
    READY\n, HEARTBEAT\n, STREAM:ON/OFF\n, AUDIO_LEVEL:N\n
    LED:OK\n, SPEAKER:CHUNK_DONE\n
"""

import serial
import threading
import queue
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String, Int32

UART_PORT_DEFAULT = '/dev/ttyACM0'
UART_BAUD_DEFAULT = 115200
RECONNECT_DELAY_S = 3.0
HEARTBEAT_TIMEOUT_S = 15.0


class ReSpeakerInterfaceNode(Node):
    """
    Nodo ROS 2 che gestisce la comunicazione seriale con il ReSpeaker Lite.
    Thread-safe tramite Lock sulla porta seriale e queue di comandi TX.
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
        self._serial_lock = threading.Lock()
        self._tx_queue: queue.Queue = queue.Queue()
        self._shutdown = False
        self._last_heartbeat = self.get_clock().now()
        self._firmware_ready = False

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
        with self._serial_lock:
            try:
                if self._serial and self._serial.is_open:
                    self._serial.close()
                self._serial = serial.Serial(
                    port=self._port,
                    baudrate=self._baud,
                    timeout=1.0,
                    write_timeout=2.0
                )
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
                self._firmware_ready = False
                self.get_logger().info(f"✅ Porta seriale aperta: {self._port}")
                return True
            except serial.SerialException as e:
                self.get_logger().warning(f"⚠️ Impossibile aprire {self._port}: {e}")
                self._serial = None
                return False

    # ── RX loop (thread) ───────────────────────────────────────
    def _rx_loop(self):
        import time
        while not self._shutdown:
            with self._serial_lock:
                ser = self._serial
                is_open = ser is not None and ser.is_open

            if not is_open:
                time.sleep(RECONNECT_DELAY_S)
                self._connect()
                continue

            try:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode('utf-8', errors='replace').strip()
                if line:
                    self._handle_rx(line)
            except serial.SerialException as e:
                self.get_logger().warning(f"❌ Errore seriale RX: {e}")
                with self._serial_lock:
                    if self._serial is ser:
                        try:
                            self._serial.close()
                        except Exception:
                            pass
                        self._serial = None
                        self._firmware_ready = False
            except Exception as e:
                self.get_logger().warning(f"RX eccezione: {e}")

    def _handle_rx(self, line: str):
        """Processa una riga ricevuta dall'ESP32S3."""
        self.get_logger().debug(f"← UART: {line}")

        if line == "READY":
            self._firmware_ready = True
            self._last_heartbeat = self.get_clock().now()
            self.get_logger().info("🟢 Firmware ESP32 READY — comunicazione USB attiva")

        elif line == "TRIGGER_JARVIS":
            self.get_logger().info("🔔 Wake Word rilevato dal ReSpeaker! Apro microfono...")
            msg = Bool()
            msg.data = False  # False = microfono APERTO
            self._mic_mute_pub.publish(msg)

        elif line == "HEARTBEAT" or line == "PONG":
            self._last_heartbeat = self.get_clock().now()
            if not self._firmware_ready:
                self._firmware_ready = True
                self.get_logger().info("🟢 Firmware ESP32 attivo (primo heartbeat)")
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

        elif line.startswith("STREAM:") or line.startswith("DIAG:") or line.startswith("SPEAKER:"):
            self.get_logger().info(f"ESP32 ack: {line}")

        elif line == "LED:OK":
            self.get_logger().debug("LED comando confermato")

        elif line.startswith("WIFI_ON") or line.startswith("WIFI_OFF"):
            self.get_logger().info(f"WiFi ESP32: {line}")

        else:
            self.get_logger().debug(f"UART non gestito: '{line}'")

    # ── TX loop (thread) ───────────────────────────────────────
    def _tx_loop(self):
        import time
        while not self._shutdown:
            try:
                cmd = self._tx_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            with self._serial_lock:
                ser = self._serial
                is_open = ser is not None and ser.is_open

            if not is_open:
                self.get_logger().debug("TX: porta non aperta, scarto comando.")
                continue

            try:
                ser.write(cmd.encode('utf-8'))
                self.get_logger().debug(f"→ UART: {cmd.strip()}")
            except serial.SerialTimeoutException:
                # Write timeout è transitorio — NON chiudere la seriale
                self.get_logger().debug(
                    f"TX timeout (comando: {cmd.strip()}) — firmware potrebbe non essere pronto")
            except serial.SerialException as e:
                self.get_logger().warning(f"❌ Errore seriale TX: {e}")
                with self._serial_lock:
                    if self._serial is ser:
                        try:
                            self._serial.close()
                        except Exception:
                            pass
                        self._serial = None
                        self._firmware_ready = False

    def _enqueue_command(self, cmd: str):
        """Accoda un comando da inviare all'ESP32S3."""
        self._tx_queue.put_nowait(cmd)

    # ── Callbacks subscribers ───────────────────────────────────
    def _led_command_cb(self, msg: String):
        """Invia comando LED al firmware via USB serial."""
        cmd = msg.data.strip()
        if not cmd.endswith('\n'):
            cmd += '\n'
        self._enqueue_command(cmd)

    def _tts_speaking_cb(self, msg: Bool):
        """Quando l'AI sta parlando → LED blu, quando finisce → IDLE."""
        if msg.data:
            self._enqueue_command("LED_EFFECT:THINKING\n")
        else:
            self._enqueue_command("LED_EFFECT:IDLE\n")

    # ── Heartbeat monitor ──────────────────────────────────────
    def _check_heartbeat(self):
        """Monitora se il firmware sta ancora rispondendo."""
        now = self.get_clock().now()
        elapsed = (now - self._last_heartbeat).nanoseconds / 1e9

        if elapsed > HEARTBEAT_TIMEOUT_S:
            self.get_logger().warning(
                f"⚠️ Nessun heartbeat dal ReSpeaker da {int(elapsed)}s! "
                "Verifica connessione USB."
            )
            # Usa il comando corretto del firmware
            self._enqueue_command("HEARTBEAT_REQ\n")

            # Hard reset dopo 60s senza risposta
            if elapsed > 60.0:
                self.get_logger().error(
                    "Heartbeat perso da troppo tempo. Forzo riapertura porta seriale...")
                with self._serial_lock:
                    if self._serial:
                        try:
                            self._serial.close()
                        except Exception:
                            pass
                        self._serial = None
                        self._firmware_ready = False
        else:
            # Sollecita heartbeat con il comando corretto
            self._enqueue_command("HEARTBEAT_REQ\n")

    # ── Shutdown ───────────────────────────────────────────────
    def destroy_node(self):
        self._shutdown = True
        import time; time.sleep(0.2)
        with self._serial_lock:
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
