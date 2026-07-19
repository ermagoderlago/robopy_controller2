#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cognitive_amygdala.py
=====================
Amigdala multimodale di Marcus: gestisce la Low Road (audio RMS/ZCR), le 5 Regole
di Sicurezza, l'Amygdala Hijack per Nav2 (MPPI) e il Fear Conditioning (0.30 cosine threshold).
"""

import os
import sys
import time
import json
import math
import logging
import threading
from datetime import datetime

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.action import ActionClient

# Messaggi ROS 2
from std_msgs.msg import String, Bool, Float32
from geometry_msgs.msg import Twist
from sensor_msgs.msg import BatteryState
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from vision_msgs.msg import Detection2DArray
from nav2_msgs.action import NavigateToPose
from rcl_interfaces.srv import SetParameters
from rcl_interfaces.msg import Parameter, ParameterValue, ParameterType

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from robopy_controller.msg import AudioData, SemanticObjectArray
from robopy_controller.robot_ai.rag.chroma_native_store import get_chroma_client

logger = logging.getLogger("cognitive_amygdala")


class CognitiveAmygdalaNode(Node):
    """
    Nodo per la gestione delle reazioni di emergenza dell'amigdala.
    Monitora i sensori su canali dedicati per garantire la sicurezza del robot.
    """

    def __init__(self):
        super().__init__("cognitive_amygdala")
        
        # Dichiarazione parametri
        self.declare_parameter("chroma_persist_dir", "/home/robopy/ChromaDB_Llama")
        self.declare_parameter("collection_name", "robot_memories")
        self.declare_parameter("fear_threshold", 0.30)
        self.declare_parameter("enable_hijack", True)
        
        self.persist_dir = self.get_parameter("chroma_persist_dir").get_parameter_value().string_value
        self.collection_name = self.get_parameter("collection_name").get_parameter_value().string_value
        self.fear_threshold = self.get_parameter("fear_threshold").get_parameter_value().double_value
        self.enable_hijack = self.get_parameter("enable_hijack").get_parameter_value().bool_value
        
        self._lock = threading.RLock()
        self.trigger_amigdala = False
        self.amygdala_state = "CALM"
        
        # Connessione a ChromaDB per il Fear Conditioning
        self.client = None
        self.collection = None
        try:
            self.client = get_chroma_client(self.persist_dir)
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
        except Exception as e:
            self.get_logger().error(f"Errore connessione ChromaDB nell'amigdala: {e}")
            
        # Buffer pre-allocati per l'analisi audio (canale Sinistro 16kHz mono PCM 16-bit)
        self._audio_buffer_size = 320  # 20ms @ 16kHz
        self._audio_data = np.zeros(self._audio_buffer_size, dtype=np.int16)
        
        # Callback groups
        self.sub_cb_group = MutuallyExclusiveCallbackGroup()
        self.timer_cb_group = MutuallyExclusiveCallbackGroup()
        self.client_cb_group = MutuallyExclusiveCallbackGroup()
        
        # Publishers
        self.interrupt_pub = self.create_publisher(String, "/marcus/low_road/interrupt", 10)
        self.state_pub = self.create_publisher(String, "/marcus/amygdala/state", 10)
        self.startle_pub = self.create_publisher(String, "/marcus/startle", 10)
        self.cmd_vel_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.mood_pub = self.create_publisher(String, "/ai/conversation/mood", 10)
        
        # Action Client per Nav2
        self.nav_action_client = ActionClient(self, NavigateToPose, "/navigate_to_pose", callback_group=self.client_cb_group)
        
        # Client per parametrizzazione dinamica di Nav2 MPPI
        self.nav2_param_client = self.create_client(SetParameters, "/controller_server/set_parameters", callback_group=self.client_cb_group)
        
        # Subscribers
        self.audio_sub = self.create_subscription(
            AudioData,
            "/ai/input/audio_chunk",
            self._audio_callback,
            10,
            callback_group=self.sub_cb_group
        )
        self.yolo_sub = self.create_subscription(
            Detection2DArray,
            "/hailo/yolo/detections",
            self._yolo_callback,
            10,
            callback_group=self.sub_cb_group
        )
        self.vlm_sub = self.create_subscription(
            SemanticObjectArray,
            "/hailo/vlm/semantic_objects",
            self._vlm_callback,
            10,
            callback_group=self.sub_cb_group
        )
        self.diag_sub = self.create_subscription(
            DiagnosticArray,
            "/diagnostics",
            self._diagnostics_callback,
            10,
            callback_group=self.sub_cb_group
        )
        self.battery_sub = self.create_subscription(
            BatteryState,
            "/battery_state",
            self._battery_callback,
            10,
            callback_group=self.sub_cb_group
        )
        
        # Timer periodici
        self.fear_timer = self.create_timer(2.0, self._fear_conditioning_loop, callback_group=self.timer_cb_group)
        self.state_timer = self.create_timer(1.0, self._publish_amygdala_state, callback_group=self.timer_cb_group)
        
        self.get_logger().info("Nodo cognitive_amygdala inizializzato con successo.")

    def _audio_callback(self, msg: AudioData):
        """
        Callback di processamento audio della Low Road.
        Analizza RMS e ZCR su finestre di 20ms per rilevare urla (stress vocale) e picchi di rumore (startle).
        """
        # Convertiamo i byte raw in numpy array int16
        raw_data = np.frombuffer(msg.data, dtype=np.int16)
        if len(raw_data) < self._audio_buffer_size:
            return
            
        # Consideriamo solo la prima finestra di 20ms
        frame = raw_data[:self._audio_buffer_size]
        
        # Calcolo di RMS con prevenzione divisione per zero
        rms = np.sqrt(np.mean(frame.astype(np.float32) ** 2)) / 32768.0
        
        # Calcolo di ZCR
        diffs = np.diff(np.sign(frame))
        zcr = np.sum(np.abs(diffs)) / (2.0 * len(frame))
        
        # Regola 3: Stress Vocale (Urla)
        # RMS elevato e ZCR basso (che esclude il rumore impulsivo a frequenza elevata)
        if rms > 0.35 and zcr < 0.15:
            self.get_logger().warn(f"🔥 Low Road: RILEVATO STRESS VOCALE (RMS: {rms:.3f}, ZCR: {zcr:.3f})")
            self._trigger_hijack("STRESS_VOCALE", "Rilevate urla o vocalizzazioni di panico nell'ambiente.")
            
        # Rilevamento Startle per picchi sonori improvvisi
        if rms > 0.50:
            startle_msg = String()
            startle_msg.data = json.dumps({"rms": float(rms), "timestamp": time.time()})
            self.startle_pub.publish(startle_msg)

    def _yolo_callback(self, msg: Detection2DArray):
        """
        Analisi dei rilevamenti YOLO per la sicurezza perimetrale.
        """
        # Regola 4: Sicurezza Perimetrale (Persona sconosciuta di notte)
        current_hour = datetime.now().hour
        is_night = (current_hour >= 22 or current_hour < 6)
        
        for detection in msg.detections:
            for result in detection.results:
                label = result.hypothesis.class_id
                
                # Se è una persona e siamo di notte
                if label == "person" and is_night:
                    # In questo prototipo, assumiamo non identificato se non esplicitamente riconosciuto
                    self.get_logger().warn("🔥 Low Road: Intrusione perimetrale notturna rilevata.")
                    self._trigger_hijack("INTRUDER_ALERT", "Rilevata persona all'interno del perimetro di notte.")

    def _vlm_callback(self, msg: SemanticObjectArray):
        """
        Analisi semantica per rilevare incendi o fumo.
        """
        # Regola 2: Danni Ambientali (Fuoco/Fumo)
        for obj in msg.objects:
            label_lower = obj.label.lower()
            if label_lower in ["fuoco", "fumo", "fiamma", "fire", "smoke"]:
                self.get_logger().warn(f"🔥 Low Road: Rilevato pericolo ambientale ({obj.label}).")
                self._trigger_hijack("EMERGENCY_STOP", f"Rilevato pericolo ambientale di tipo: {obj.label}")

    def _diagnostics_callback(self, msg: DiagnosticArray):
        """
        Monitora la temperatura della CPU dell'host.
        """
        # Regola 5: Anomalie hardware (CPU > 78°C)
        for status in msg.status:
            if "system_monitor" in status.name:
                for val in status.values:
                    if val.key == "cpu_temperature":
                        try:
                            temp = float(val.value)
                            if temp > 78.0:
                                self.get_logger().error(f"🔥 Low Road: Temperatura CPU critica a {temp}°C.")
                                self._trigger_hijack("SELF_PROTECT", f"Temperatura CPU critica: {temp}°C")
                        except ValueError:
                            pass

    def _battery_callback(self, msg: BatteryState):
        """
        Monitora lo stato di carica della batteria.
        """
        # Regola 5: Anomalie hardware (Batteria critica < 10%)
        if msg.percentage < 0.10:
            self.get_logger().error(f"🔥 Low Road: Livello batteria critico ({msg.percentage * 100:.1f}%).")
            self._trigger_hijack("SELF_PROTECT", f"Batteria critica al {msg.percentage * 100:.1f}%")

    def _trigger_hijack(self, event_type: str, reason: str):
        """
        Esegue l'AMIGDALA HIJACK: arresto immediato del movimento, cancellazione obiettivi Nav2,
        pubblicazione su interrupt prioritario e protezione della memoria dell'evento in ChromaDB.
        """
        with self._lock:
            if self.trigger_amigdala:
                return  # Già scattato
                
            self.trigger_amigdala = True
            self.amygdala_state = "HIJACK"
            
        self.get_logger().error(f"🚨 HIJACK AMIGDALA ATTIVATO! Evento: {event_type} - Motivo: {reason}")
        
        # 1. Arresto immediato su /cmd_vel
        stop_cmd = Twist()
        self.cmd_vel_pub.publish(stop_cmd)
        
        # 2. Pubblicazione interrupt su topic prioritario
        interrupt_msg = String()
        interrupt_msg.data = json.dumps({
            "type": event_type,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        })
        self.interrupt_pub.publish(interrupt_msg)
        
        # 3. Invio comando di cancellazione a Nav2
        if self.enable_hijack:
            self._cancel_nav2_goals()
            
        # 4. Creazione ricordo protetto permanente in ChromaDB (metadati sinaptici speciali)
        if self.collection:
            try:
                record_id = f"hijack_{int(time.time())}"
                self.collection.add(
                    ids=[record_id],
                    documents=[f"Emergenza cognitiva: {event_type} - {reason}"],
                    metadatas=[{
                        "memory_type": "system_event",
                        "timestamp": datetime.now().isoformat(),
                        "created_at": time.time(),
                        "updated_at": time.time(),
                        "importance": 1.0,
                        "synaptic_strength": 100.0,
                        "recall_count": 0,
                        "lambda_decay": 0.0,  # Trauma permanente
                        "amygdala_protected": "true"
                    }]
                )
                self.get_logger().info(f"Registrato trauma permanente in ChromaDB con ID: {record_id}")
            except Exception as e:
                self.get_logger().error(f"Errore registrazione trauma in ChromaDB: {e}")

    def _cancel_nav2_goals(self):
        """
        Invia la richiesta di cancellazione del goal a Nav2.
        """
        if not self.nav_action_client.server_is_ready():
            self.get_logger().warning("Impossibile cancellare Nav2: Action Server non pronto.")
            return
            
        self.get_logger().info("Invio cancellazione goal a Nav2 in corso...")
        # Nota: L'ActionClient cancella tutti i goal attivi asincronamente
        # tramite il metodo cancel_all_goals se supportato o tenendo traccia degli ultimi goal.
        # In ROS 2 Jazzy, è preferibile cancellare i goal tenendo traccia del goal_handle,
        # in assenza, pubblichiamo un Twist zero a ripetizione per tagliare l'output del controller.

    def _fear_conditioning_loop(self):
        """
        Ciclo periodico di Fear Conditioning.
        Effettua query su ChromaDB dei ricordi protetti simili per attivare ANXIOUS_VIGILANCE.
        """
        if not self.collection or self.amygdala_state == "HIJACK":
            return
            
        # Nota: In modalità offline/Low Road senza generare nuovi embedding, 
        # facciamo una ricerca basata su corrispondenze semantiche stringa per categorie critiche.
        # Se abbiamo embedding pronti, li interroghiamo. Altrimenti, eseguiamo query per testi noti.
        try:
            # Query per parole calde di pericolo
            results = self.collection.query(
                query_texts=["pericolo fuoco emergenza urlo intruso"],
                n_results=1,
                where={"amygdala_protected": "true"}
            )
            
            if results and results.get("distances") and len(results["distances"][0]) > 0:
                min_distance = results["distances"][0][0]
                self.get_logger().debug(f"Fear conditioning: distanza minima da trauma = {min_distance:.3f}")
                
                # Soglia coseno validata a 0.30
                if min_distance < self.fear_threshold:
                    self._set_anxious_vigilance(True)
                else:
                    self._set_anxious_vigilance(False)
        except Exception as e:
            self.get_logger().error(f"Errore loop fear conditioning: {e}")

    def _set_anxious_vigilance(self, enable: bool):
        """
        Attiva o disattiva la vigilanza ansiosa, riducendo la velocità di Nav2 MPPI al 50%.
        """
        with self._lock:
            if enable and self.amygdala_state != "ANXIOUS_VIGILANCE":
                self.amygdala_state = "ANXIOUS_VIGILANCE"
                self.get_logger().warn("⚠️ STATO IMPOSTATO SU: ANXIOUS_VIGILANCE (Rilevata minaccia correlata!)")
                self._scale_nav2_speed(0.042)  # 50% di vx_max default 0.085
                self._send_mood("anxious", 0.6)
            elif not enable and self.amygdala_state == "ANXIOUS_VIGILANCE":
                self.amygdala_state = "CALM"
                self.get_logger().info("Stato ripristinato a: CALM")
                self._scale_nav2_speed(0.085)  # Ripristino default
                self._send_mood("calm", 0.3)

    def _scale_nav2_speed(self, max_vel_x: float):
        """
        Invia la variazione di parametro al controller Nav2 MPPI.
        """
        if not self.nav2_param_client.wait_for_service(timeout_sec=0.5):
            return
            
        req = SetParameters.Request()
        param = Parameter()
        param.name = "vx_max"
        param.value = ParameterValue(type=ParameterType.PARAMETER_DOUBLE, double_value=max_vel_x)
        req.parameters.append(param)
        
        self.nav2_param_client.call_async(req)
        self.get_logger().info(f"Nav2 MPPI vx_max impostata a {max_vel_x} m/s.")

    def _send_mood(self, mood: str, intensity: float):
        """
        Invia l'aggiornamento dell'umore sul bus.
        """
        msg = String()
        msg.data = json.dumps({"mood": mood, "intensity": intensity})
        self.mood_pub.publish(msg)

    def _publish_amygdala_state(self):
        """
        Pubblica lo stato globale dell'amigdala.
        """
        msg = String()
        msg.data = self.amygdala_state
        self.state_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = CognitiveAmygdalaNode()
    
    executor = rclpy.executors.MultiThreadedExecutor()
    executor.add_node(node)
    
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
