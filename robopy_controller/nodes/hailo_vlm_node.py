#!/usr/bin/env python3
"""
Hailo VLM Node
==============
ROS 2 node that runs the Qwen2-VL-1.5B Vision-Language Model (VLM)
on the Hailo-10H NPU via Hailo GenAI Suite.
Includes a shared VDevice configuration and robust simulation fallback.

Version: 01.00.00
"""

import os
import sys
import time
import threading
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

import cv2
import numpy as np
from sensor_msgs.msg import CompressedImage, Image
from cv_bridge import CvBridge
from robopy_controller.srv import AskVisualQuestion

# Try importing Hailo GenAI packages
try:
    # Qwen2-VL wrapper via Hailo GenAI Suite
    # Note: These imports reflect the Hailo GenAI SDK structure for edge VLM
    from hailo_genai import VDevice as GenAIVDevice, Qwen2VL
    from hailo_apps.python.core.common.defines import SHARED_VDEVICE_GROUP_ID
    HAILO_GENAI_AVAILABLE = True
except ImportError:
    HAILO_GENAI_AVAILABLE = False


class HailoVlmNode(Node):
    def __init__(self):
        super().__init__('hailo_vlm_node')
        self.get_logger().info("Inizializzazione hailo_vlm_node...")

        # Parameters
        self.declare_parameter('model_path', '/mnt/ssd/models/qwen2_vl_1.5b.hef')
        self.declare_parameter('sim_mode', not HAILO_GENAI_AVAILABLE)
        self.declare_parameter('vdevice_group_id', 42) # default group for sharing NPU

        self.model_path = self.get_parameter('model_path').value
        self.sim_mode = self.get_parameter('sim_mode').value
        self.vdevice_group_id = self.get_parameter('vdevice_group_id').value

        self.bridge = CvBridge()
        self.latest_bgr = None
        self.lock = threading.Lock()

        if self.sim_mode:
            self.get_logger().warn("⚠️ Esecuzione in MODALITÀ SIMULATA (Hailo GenAI non disponibile o sim_mode=True)")
        else:
            self.get_logger().info(f"Caricamento modello VLM da: {self.model_path}")
            self.init_hailo_vlm()

        # QoS for camera topic
        qos_best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # Subscriber to camera stream
        self.sub_rgb = self.create_subscription(
            CompressedImage, '/rgb/image/compressed', self.rgb_callback, qos_best_effort
        )

        # Service Server
        self.srv_ask_question = self.create_service(
            AskVisualQuestion, '/hailo/vlm/ask_question', self.handle_ask_question
        )

        self.get_logger().info("✅ Node Hailo VLM pronto.")

    def init_hailo_vlm(self):
        """Inizializzazione del dispositivo Hailo NPU e del modello Qwen2-VL"""
        try:
            # Condividiamo il VDevice usando SHARED_VDEVICE_GROUP_ID o un ID gruppo specifico
            # per consentire la coesistenza con hailo_bridge_node
            self.get_logger().info("🔌 Creazione VDevice NPU condiviso...")
            
            # Usiamo SHARED_VDEVICE_GROUP_ID se disponibile, altrimenti il parametro configurato
            group_id = SHARED_VDEVICE_GROUP_ID if 'SHARED_VDEVICE_GROUP_ID' in globals() else self.vdevice_group_id
            
            self.vdevice = GenAIVDevice(group_id=group_id)
            self.vlm = Qwen2VL(self.vdevice, self.model_path)
            
            self.get_logger().info("✅ Qwen2-VL caricato con successo su Hailo NPU.")
        except Exception as e:
            self.get_logger().error(f"❌ Inizializzazione Qwen2-VL su NPU fallita: {e}. Passaggio a SIMULATION.")
            self.sim_mode = True

    def rgb_callback(self, msg):
        """Salva l'ultimo frame ricevuto dalla fotocamera"""
        try:
            np_arr = np.frombuffer(msg.data, np.uint8)
            bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            with self.lock:
                self.latest_bgr = bgr
        except Exception as e:
            self.get_logger().error(f"Errore decodifica immagine VLM callback: {e}")

    def handle_ask_question(self, request, response):
        """Gestisce le domande VQA locali inviate tramite il servizio ROS 2"""
        question = request.question
        self.get_logger().info(f"❓ Domanda VLM ricevuta: '{question}'")

        # Recupera l'ultimo frame
        with self.lock:
            img = self.latest_bgr.copy() if self.latest_bgr is not None else None

        if img is None:
            response.success = False
            response.answer = "Errore: Frame fotocamera non disponibile."
            return response

        if self.sim_mode:
            # Fallback di simulazione intelligente: risponde in base a keyword comuni
            response.success = True
            response.answer = self.generate_simulated_vqa_response(question, img)
            return response

        try:
            # Esegui inferenza Qwen2-VL locale su Hailo NPU
            # Convertiamo BGR a RGB per il modello
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            
            # Chiamata all'API locale Qwen2VL
            result = self.vlm.generate(prompt=question, image=rgb)
            
            response.answer = str(result)
            response.success = True
        except Exception as e:
            self.get_logger().error(f"Errore durante l'inferenza Qwen2-VL locale: {e}")
            response.success = False
            response.answer = f"Errore locale NPU: {e}"

        return response

    def generate_simulated_vqa_response(self, question, img):
        """Genera una risposta simulata deterministica per i test di VQA"""
        q_lower = question.lower()
        
        # Analizziamo la luminosità media per simulare variazioni di contesto reale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mean_brightness = np.mean(gray)
        brightness_desc = "luminoso" if mean_brightness > 120 else "piuttosto buio"
        
        # Risposte simulate coerenti
        if "cucina" in q_lower or "kitchen" in q_lower:
            return "Vedo una cucina con un bancone in legno, un forno e una sedia posizionata sul lato sinistro."
        elif "soggiorno" in q_lower or "salotto" in q_lower:
            return f"Vedo un soggiorno {brightness_desc} con un divano blu e un tavolino da caffè al centro."
        elif "camera" in q_lower or "letto" in q_lower:
            return "Vedo una camera da letto con un letto matrimoniale rifatto e un comodino in metallo."
        elif "ostacolo" in q_lower or "davanti" in q_lower:
            return "Sì, c'è una sedia posizionata a circa 1.2 metri direttamente davanti a me."
        else:
            return f"Analisi visiva completata nell'ambiente ({brightness_desc}). Rilevato un contesto domestico standard."


def main(args=None):
    rclpy.init(args=args)
    node = HailoVlmNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
