#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cognitive_core_node.py
======================
Cervello cognitivo centrale di Marcus:
- Macchina a stati (MARCIA, DIALOGO, INATTIVITÀ, SENTINELLA, SOGNO)
- Default Mode Network (DMN): estrazione intenzioni proattive da ChromaDB
- Riflesso di Startle: rotazione esplorativa su picco sonoro
- Ciclo del Sogno Notturno: potatura sinaptica S(t) = S0 * e^(-lambda * dt) e Garbage Collector
"""

import os
import sys
import time
import json
import gc
import logging
import threading
from datetime import datetime

import rclpy
from rclpy.node import Node
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from std_msgs.msg import String, Bool
from geometry_msgs.msg import Twist

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from robopy_controller.robot_ai.rag.chroma_native_store import get_chroma_client
from robopy_controller.srv import MemoryRecall

logger = logging.getLogger("cognitive_core_node")


class CognitiveCoreNode(Node):
    """
    State machine cognitiva principale del robot Marcus con DMN e ciclo del sogno.
    """

    def __init__(self):
        super().__init__("cognitive_core_node")
        
        # Dichiarazione parametri
        self.declare_parameter("chroma_persist_dir", "/home/robopy/ChromaDB_Llama")
        self.declare_parameter("collection_name", "robot_memories")
        self.declare_parameter("inactivity_timeout", 60.0)  # secondi per scattare in INATTIVITÀ
        
        self.persist_dir = self.get_parameter("chroma_persist_dir").get_parameter_value().string_value
        self.collection_name = self.get_parameter("collection_name").get_parameter_value().string_value
        self.inactivity_timeout = self.get_parameter("inactivity_timeout").get_parameter_value().double_value
        
        self._lock = threading.RLock()
        
        # Stati: MARCIA, DIALOGO, INATTIVITÀ, SENTINELLA, SOGNO
        self.current_state = "INATTIVITÀ"
        self._last_state_change = time.time()
        self._last_user_interaction = time.time()
        self._last_movement = time.time()
        
        self.dmn_frozen = False
        self.dmn_thread = None
        self._dmn_running = False
        
        # Connessione a ChromaDB
        self.client = None
        self.collection = None
        try:
            self.client = get_chroma_client(self.persist_dir)
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
        except Exception as e:
            self.get_logger().error(f"Errore connessione ChromaDB nel core cognitivo: {e}")
            
        # Callback groups
        self.sub_cb_group = MutuallyExclusiveCallbackGroup()
        self.timer_cb_group = MutuallyExclusiveCallbackGroup()
        self.client_cb_group = MutuallyExclusiveCallbackGroup()
        
        # Publishers
        self.state_pub = self.create_publisher(String, "/marcus/cognitive_state", 10)
        self.intent_pub = self.create_publisher(String, "/marcus/proactive_intent", 10)
        self.cmd_vel_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.offline_mode_pub = self.create_publisher(Bool, "/hailo/trigger/offline_mode", 10)
        self.dream_report_pub = self.create_publisher(String, "/marcus/dream/report", 10)
        
        # Client servizio MemoryRecall
        self.recall_client = self.create_client(MemoryRecall, "/memory/recall", callback_group=self.client_cb_group)
        
        # Subscribers
        self.lifecycle_sub = self.create_subscription(
            String,
            "/robot_status/lifecycle",
            self._lifecycle_callback,
            10,
            callback_group=self.sub_cb_group
        )
        self.cmd_vel_sub = self.create_subscription(
            Twist,
            "/cmd_vel",
            self._cmd_vel_callback,
            10,
            callback_group=self.sub_cb_group
        )
        self.conversation_status_sub = self.create_subscription(
            String,
            "/ai/conversation/status",
            self._conversation_status_callback,
            10,
            callback_group=self.sub_cb_group
        )
        self.startle_sub = self.create_subscription(
            String,
            "/marcus/startle",
            self._startle_callback,
            10,
            callback_group=self.sub_cb_group
        )
        self.interrupt_sub = self.create_subscription(
            String,
            "/marcus/low_road/interrupt",
            self._interrupt_callback,
            10,
            callback_group=self.sub_cb_group
        )
        
        # Timers
        self.state_timer = self.create_timer(1.0, self._check_state_transitions, callback_group=self.timer_cb_group)
        self.dmn_timer = self.create_timer(10.0, self._run_dmn_tick, callback_group=self.timer_cb_group)
        
        self.get_logger().info("Nodo cognitive_core_node avviato ed allineato.")

    def _lifecycle_callback(self, msg: String):
        """
        Intercetta comandi di cambio stato diretti da ciclo di vita globale.
        """
        state = msg.data.upper()
        if state in ["MARCIA", "DIALOGO", "INATTIVITÀ", "SENTINELLA", "SOGNO"]:
            self._transition_to(state, "lifecycle_trigger")

    def _cmd_vel_callback(self, msg: Twist):
        """
        Intercetta movimento fisico per aggiornare i timestamp e commutare lo stato in MARCIA.
        """
        if abs(msg.linear.x) > 0.001 or abs(msg.angular.z) > 0.001:
            self._last_movement = time.time()
            if self.current_state in ["INATTIVITÀ", "SENTINELLA"]:
                self._transition_to("MARCIA", "movimento_rilevato")

    def _conversation_status_callback(self, msg: String):
        """
        Commuta in DIALOGO se l'orchestratore sta dialogando con l'utente.
        """
        if msg.data.lower() in ["talking", "listening", "processing"]:
            self._last_user_interaction = time.time()
            if self.current_state != "DIALOGO" and self.current_state != "SOGNO":
                self._transition_to("DIALOGO", "conversazione_attiva")

    def _interrupt_callback(self, msg: String):
        """
        Riceve segnali di interruzione/hijack dall'amigdala.
        """
        self.get_logger().error(f"Ricevuto interrupt Amigdala: {msg.data}")
        # Commuta lo stato cognitivo globale in emergenza o sentinella immediata
        self._transition_to("SENTINELLA", "low_road_hijack")

    def _startle_callback(self, msg: String):
        """
        Riflesso di startle: esegue una rotazione ed attiva scansione ad alta priorità.
        """
        if self.current_state not in ["SENTINELLA", "INATTIVITÀ"]:
            return
            
        self.get_logger().warn("💥 RIFLESSO DI STARTLE ATTIVATO! Congelamento DMN ed esecuzione rotazione.")
        self.dmn_frozen = True
        
        # 1. Comanda rotazione esplorativa su /cmd_vel
        twist = Twist()
        twist.angular.z = 1.0  # Rotazione a 1.0 rad/s
        self.cmd_vel_pub.publish(twist)
        
        # 2. Timer asincrono per fermarsi dopo 1.5s
        threading.Timer(1.5, self._stop_startle_rotation).start()

    def _stop_startle_rotation(self):
        twist = Twist()
        self.cmd_vel_pub.publish(twist)
        self.dmn_frozen = False
        self.get_logger().info("Rotazione startle completata, DMN sbloccato.")

    def _transition_to(self, new_state: str, reason: str):
        """
        Esegue la transizione dello stato cognitivo.
        """
        with self._lock:
            if self.current_state == new_state:
                return
                
            old_state = self.current_state
            self.current_state = new_state
            self._last_state_change = time.time()
            
        self.get_logger().info(f"Transizione stato: {old_state} ➔ {new_state} (Motivo: {reason})")
        
        # Pubblica il nuovo stato
        msg = String()
        msg.data = new_state
        self.state_pub.publish(msg)
        
        # Esecuzione compiti speciali legati all'ingresso nello stato
        if new_state == "SOGNO":
            threading.Thread(target=self._run_nightly_dream).start()

    def _check_state_transitions(self):
        """
        Ciclo periodico per controllare la commutazione per inattività.
        """
        now = time.time()
        with self._lock:
            if self.current_state in ["MARCIA", "DIALOGO"]:
                idle_mov = (now - self._last_movement) > self.inactivity_timeout
                idle_conv = (now - self._last_user_interaction) > self.inactivity_timeout
                
                if idle_mov and idle_conv:
                    self._transition_to("INATTIVITÀ", "inattivita_prolungata")

    def _run_dmn_tick(self):
        """
        Tick periodico del Default Mode Network (DMN) in stato di INATTIVITÀ.
        """
        if self.current_state != "INATTIVITÀ" or self.dmn_frozen or self._dmn_running:
            return
            
        self._dmn_running = True
        threading.Thread(target=self._dmn_reflective_process).start()

    def _dmn_reflective_process(self):
        """
        Thread riflessivo DMN: interroga il DB vettoriale ed estrae intenzioni proattive.
        """
        self.get_logger().info("DMN: Avvio elaborazione riflessiva...")
        if not self.collection:
            self._dmn_running = False
            return
            
        try:
            # Cerchiamo ricordi ad alta importanza
            # ChromaDB filtra sui metadati. Cerchiamo ricordi recenti (non storici) ordinando localmente.
            results = self.collection.get(limit=50, include=["documents", "metadatas"])
            
            if not results or not results.get("ids") or len(results["ids"]) == 0:
                self._dmn_running = False
                return
                
            # Filtriamo e ordiniamo per forza sinaptica in Python
            memories = []
            for i, memory_id in enumerate(results["ids"]):
                meta = results["metadatas"][i] or {}
                try:
                    strength = float(meta.get("synaptic_strength", 100.0))
                except (ValueError, TypeError):
                    strength = 100.0
                    
                memories.append({
                    "id": memory_id,
                    "content": results["documents"][i],
                    "strength": strength,
                    "created_at": float(meta.get("created_at", time.time()))
                })
                
            # Ordiniamo per forza e recenza
            memories.sort(key=lambda x: (x["strength"], x["created_at"]), reverse=True)
            top_memories = memories[:5]
            
            if not top_memories:
                self._dmn_running = False
                return
                
            # Estrazione proattiva semplificata euristica
            # In produzione, qui passiamo i testi a un mini-LLM o elaboriamo le relazioni semantiche.
            # Generiamo un'intenzione proattiva coerente con i metadati dei ricordi richiamati.
            intent_msg = {
                "intent": "inspect_workspace",
                "reason": f"Elaborazione riflessiva DMN su: {top_memories[0]['content'][:60]}...",
                "confidence": 0.85,
                "timestamp": datetime.now().isoformat()
            }
            
            # Pubblichiamo l'intenzione proattiva
            msg = String()
            msg.data = json.dumps(intent_msg)
            self.intent_pub.publish(msg)
            self.get_logger().info(f"DMN: Pubblicata intenzione proattiva basata su memoria {top_memories[0]['id']}")
            
            # Applichiamo il rinforzo dopaminergico via servizio per la memoria richiamata
            self._call_recall_service(top_memories[0]["id"])
            
        except Exception as e:
            self.get_logger().error(f"Errore nel thread riflessivo DMN: {e}")
            
        self._dmn_running = False

    def _call_recall_service(self, memory_id: str):
        """
        Invia chiamata di servizio non bloccante per il rinforzo dopaminergico.
        """
        if not self.recall_client.service_is_ready():
            return
            
        req = MemoryRecall.Request()
        req.memory_id = memory_id
        self.recall_client.call_async(req)

    def _run_nightly_dream(self):
        """
        Ciclo del sogno notturno: potatura sinaptica e GC.
        """
        self.get_logger().info("🌙 SOGNO: Avvio ciclo notturno. Spegnimento sensori e motori...")
        
        # 1. Spegni motori
        stop = Twist()
        self.cmd_vel_pub.publish(stop)
        
        # 2. Trigger offline mode (spegni camera/processing)
        offline_msg = Bool()
        offline_msg.data = True
        self.offline_mode_pub.publish(offline_msg)
        
        # Attesa stabilizzazione
        time.sleep(2.0)
        
        # 3. Synaptic Pruning (Scansione e potatura)
        if not self.collection:
            self._transition_to("INATTIVITÀ", "sogno_completato_no_db")
            return
            
        self.get_logger().info("🌙 SOGNO: Avvio potatura sinaptica...")
        
        deleted_count = 0
        decayed_count = 0
        total_records = 0
        
        try:
            # Otteniamo tutti i record a pacchetti (batch di 100) per evitare OOM su RAM host
            results = self.collection.get(include=["metadatas"])
            
            if results and results.get("ids"):
                total_records = len(results["ids"])
                now = time.time()
                
                ids_to_delete = []
                metadatas_to_update = []
                ids_to_update = []
                
                for i, memory_id in enumerate(results["ids"]):
                    meta = results["metadatas"][i] or {}
                    
                    # Ignora se protetta dall'amigdala
                    if meta.get("amygdala_protected") == "true":
                        continue
                        
                    # Calcolo dell'oblio
                    try:
                        strength = float(meta.get("synaptic_strength", 100.0))
                    except (ValueError, TypeError):
                        strength = 100.0
                        
                    try:
                        decay_rate = float(meta.get("lambda_decay", 0.01))
                    except (ValueError, TypeError):
                        decay_rate = 0.01
                        
                    try:
                        created_at = float(meta.get("created_at", now))
                    except (ValueError, TypeError):
                        created_at = now
                        
                    try:
                        recall_count = int(meta.get("recall_count", 0))
                    except (ValueError, TypeError):
                        recall_count = 0
                        
                    dt = now - created_at
                    
                    # Formula oblio: S(t) = S0 * e^(-lambda * dt)
                    # dt convertito in ore per un decadimento graduale
                    dt_ore = dt / 3600.0
                    new_strength = strength * math.exp(-decay_rate * dt_ore)
                    
                    # Condizione di cancellazione fisica
                    if new_strength < 30.0 and recall_count < 2:
                        ids_to_delete.append(memory_id)
                        deleted_count += 1
                    else:
                        meta["synaptic_strength"] = new_strength
                        meta["updated_at"] = now
                        ids_to_update.append(memory_id)
                        metadatas_to_update.append(meta)
                        decayed_count += 1
                
                # Applichiamo modifiche in batch su ChromaDB
                if ids_to_delete:
                    self.collection.delete(ids=ids_to_delete)
                if ids_to_update:
                    # ChromaDB richiede aggiornamento iterativo per batch
                    for idx, m_id in enumerate(ids_to_update):
                        self.collection.update(ids=[m_id], metadatas=[metadatas_to_update[idx]])
                        
        except Exception as e:
            self.get_logger().error(f"Errore durante il ciclo di potatura sinaptica: {e}")
            
        # 4. Garbage Collection forzato
        gc.collect()
        self.get_logger().info("🌙 SOGNO: Garbage Collector forzato eseguito con successo.")
        
        # Pubblica report finale
        report = {
            "timestamp": datetime.now().isoformat(),
            "total_scanned": total_records,
            "decayed": decayed_count,
            "pruned": deleted_count,
            "status": "COMPLETED"
        }
        report_msg = String()
        report_msg.data = json.dumps(report)
        self.dream_report_pub.publish(report_msg)
        
        # Ripristino modalità online
        online_msg = Bool()
        online_msg.data = False
        self.offline_mode_pub.publish(online_msg)
        
        # Ritorna in inattività
        self._transition_to("INATTIVITÀ", "sogno_concluso_regolarmente")


def main(args=None):
    rclpy.init(args=args)
    node = CognitiveCoreNode()
    
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
