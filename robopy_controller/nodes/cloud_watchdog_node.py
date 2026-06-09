#!/usr/bin/env python3
"""
Cloud Watchdog Node
===================
ROS 2 Python node that monitors latency and connection status to the Gemini Cloud
backend, signaling offline fallback mode and survival state when needed.

Version: 01.00.00
"""

import time
import socket
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String, Bool, Float32


class CloudWatchdogNode(Node):
    def __init__(self):
        super().__init__('cloud_watchdog_node')
        self.get_logger().info("Inizializzazione cloud_watchdog_node...")

        # Parameters
        self.declare_parameter('heartbeat_interval_sec', 5.0)
        self.declare_parameter('timeout_offline_sec', 15.0)
        self.declare_parameter('timeout_degraded_sec', 8.0)
        self.declare_parameter('gemini_host', 'generativelanguage.googleapis.com')

        self.interval = self.get_parameter('heartbeat_interval_sec').value
        self.offline_timeout = self.get_parameter('timeout_offline_sec').value
        self.degraded_timeout = self.get_parameter('timeout_degraded_sec').value
        self.gemini_host = self.get_parameter('gemini_host').value

        # QoS Settings
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )
        qos_best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # Publishers
        self.pub_status = self.create_publisher(
            String, '/cloud/status', qos_reliable
        )
        self.pub_latency = self.create_publisher(
            Float32, '/cloud/latency_ms', qos_best_effort
        )
        self.pub_offline_mode = self.create_publisher(
            Bool, '/hailo/trigger/offline_mode', qos_reliable
        )

        # State
        self.cloud_status = "ONLINE"
        self.last_successful_ping = time.time()
        self.is_offline = False

        # Start monitoring timer
        self.create_timer(self.interval, self.check_connection)

        self.get_logger().info("cloud_watchdog_node avviato.")

    def check_connection(self):
        """Effettua il ping socket HTTPS verso l'host Gemini per valutare la latenza"""
        # Creiamo un thread per evitare di bloccare l'executor ROS con la socket bloccante
        threading.Thread(target=self._ping_worker).start()

    def _ping_worker(self):
        start_time = time.time()
        success = False
        latency = 0.0

        try:
            # Forziamo IPv4 come descritto in lesson_learned.md per prevenire timeout IPv6
            # Apriamo una semplice connessione TCP a porta 443 (HTTPS) senza SSL handshake
            # per ridurre l'overhead mantenendo l'accuratezza di connessione.
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0) # 3 secondi di timeout per il ping
            
            # DNS resolution + TCP connection
            sock.connect((self.gemini_host, 443))
            sock.close()
            
            latency = (time.time() - start_time) * 1000.0 # convert dynamic latency to ms
            success = True
        except Exception as e:
            # Log degli errori silenziato se siamo già offline per non sporcare i log
            if not self.is_offline:
                self.get_logger().warn(f"Connessione a Gemini fallita: {e}")

        now = time.time()
        if success:
            self.last_successful_ping = now
            
            # Pubblica la latenza reale
            lat_msg = Float32()
            lat_msg.data = float(latency)
            self.pub_latency.publish(lat_msg)
            
            # Determina lo stato: ONLINE o DEGRADED basato sul timeout_degraded
            # o se la latenza singola è superiore alla soglia
            if latency > (self.degraded_timeout * 100.0): # E.g. se degraded_timeout è 8s, usiamo 800ms come soglia latenza
                new_status = "DEGRADED"
            else:
                new_status = "ONLINE"
        else:
            time_since_last_success = now - self.last_successful_ping
            if time_since_last_success >= self.offline_timeout:
                new_status = "OFFLINE"
            else:
                new_status = "DEGRADED"

        # Gestione transizione di stato
        if new_status != self.cloud_status:
            self.get_logger().info(f"Stato connessione Cloud cambiato: {self.cloud_status} -> {new_status}")
            self.cloud_status = new_status
            
            # Pubblica stato
            status_msg = String()
            status_msg.data = self.cloud_status
            self.pub_status.publish(status_msg)

            # Se siamo OFFLINE, attiviamo offline_mode su hailo_bridge per abilitare Qwen locale
            offline_trigger = Bool()
            if self.cloud_status == "OFFLINE":
                self.is_offline = True
                offline_trigger.data = True
            else:
                self.is_offline = False
                offline_trigger.data = False
            self.pub_offline_mode.publish(offline_trigger)
        else:
            # Pubblicazione periodica dello stato corrente
            status_msg = String()
            status_msg.data = self.cloud_status
            self.pub_status.publish(status_msg)


def main(args=None):
    rclpy.init(args=args)
    node = CloudWatchdogNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
