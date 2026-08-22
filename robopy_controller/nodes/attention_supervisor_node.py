#!/usr/bin/env python3
"""
attention_supervisor_node.py - Marcus Context Switching & CPU Optimizer

Implementa la logica di "Attenzione Selettiva":
- Se il robot è in movimento (lettura da /cmd_vel):
  - Mette in pausa RTAB-Map per risparmiare CPU (niente loop closure o feature extraction su rtabmap)
  - Muta il microfono per evitare che il VAD scatti sui rumori dei motori
- Se il robot è fermo da più di N secondi:
  - Riattiva RTAB-Map (SLAM e loop closure attivi)
  - Riattiva il microfono
"""

import time
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool
from std_srvs.srv import Empty


class AttentionSupervisorNode(Node):
    def __init__(self):
        super().__init__('attention_supervisor_node')

        # Parametri configurabili
        self.declare_parameter('stop_timeout', 1.5)  # Secondi di fermo prima di riattivare SLAM/Mic
        self.declare_parameter('motion_threshold', 0.01)  # Tolleranza su Twist
        
        self.stop_timeout = self.get_parameter('stop_timeout').get_parameter_value().double_value
        self.motion_threshold = self.get_parameter('motion_threshold').get_parameter_value().double_value

        self.last_motion_time = 0.0
        # Iniziamo in uno stato sconosciuto, forzando un aggiornamento immediato al primo ciclo
        self.is_moving = None  

        # QoS
        reliable_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        # Publisher per mutare il microfono (True = Muto, False = Ascolto)
        self.pub_mic_mute = self.create_publisher(Bool, '/ai/input/mic_mute', reliable_qos)

        # Subscriber a cmd_vel (legge le intenzioni di movimento del Nav2 o teleop)
        self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_cb, 10)

        # Client per i servizi di RTAB-Map
        self.srv_pause = self.create_client(Empty, '/rtabmap/pause')
        self.srv_resume = self.create_client(Empty, '/rtabmap/resume')

        # Timer di monitoraggio stato (10 Hz)
        self.create_timer(0.1, self.check_state)
        
        self.get_logger().info("🎯 Attention Supervisor avviato: In attesa di comandi di movimento...")

    def cmd_vel_cb(self, msg: Twist):
        # Verifica se c'è un comando di movimento attivo
        linear_v = abs(msg.linear.x) + abs(msg.linear.y) + abs(msg.linear.z)
        angular_v = abs(msg.angular.x) + abs(msg.angular.y) + abs(msg.angular.z)
        
        if (linear_v + angular_v) > self.motion_threshold:
            self.last_motion_time = time.time()

    def check_state(self):
        now = time.time()
        # Se è passato meno del timeout dall'ultimo comando di movimento, stiamo camminando
        currently_moving = (now - self.last_motion_time) < self.stop_timeout

        # Se lo stato non è cambiato, non fare nulla
        if self.is_moving == currently_moving:
            return

        self.is_moving = currently_moving

        if self.is_moving:
            self.get_logger().info("🏃 Robot IN MOVIMENTO. [Azione: Pausa SLAM, Muto Microfono]")
            self.set_mic_mute(True)
            self.call_rtabmap_service(self.srv_pause, "PAUSE")
        else:
            self.get_logger().info("🛑 Robot FERMO. [Azione: Resume SLAM, Ascolto Microfono]")
            self.set_mic_mute(False)
            self.call_rtabmap_service(self.srv_resume, "RESUME")

    def set_mic_mute(self, mute: bool):
        msg = Bool()
        msg.data = mute
        self.pub_mic_mute.publish(msg)

    def call_rtabmap_service(self, client, action_name):
        if not client.wait_for_service(timeout_sec=0.5):
            self.get_logger().warning(f"⚠️ Servizio RTAB-Map ({action_name}) non disponibile. SLAM non è attivo?")
            return
            
        req = Empty.Request()
        future = client.call_async(req)
        future.add_done_callback(lambda future: self._service_callback(future, action_name))

    def _service_callback(self, future, action_name):
        try:
            future.result()
            self.get_logger().info(f"✅ RTAB-Map {action_name} applicato con successo.")
        except Exception as e:
            self.get_logger().error(f"❌ Fallita chiamata a RTAB-Map {action_name}: {e}")

def main(args=None):
    rclpy.init(args=args)
    node = AttentionSupervisorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
