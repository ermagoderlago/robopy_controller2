#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chroma_synaptic_manager.py
==========================
Interfaccia Vettoriale e Dinamica Sinaptica per la memoria episodica di Marcus.
Gestisce l'aggiornamento dei metadati sinaptici sul database vettoriale persistente
ChromaDB ed espone il servizio ROS 2 /memory/recall.
"""

import os
import sys
import time
import json
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime

import rclpy
from rclpy.node import Node
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup

# Importiamo i moduli di ChromaDB e dello store esistente
try:
    import chromadb
except ImportError:
    pass

# Aggiungiamo il path per importare i moduli locali se necessario
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from robopy_controller.robot_ai.rag.chroma_native_store import get_chroma_client
from robopy_controller.srv import MemoryRecall

logger = logging.getLogger("chroma_synaptic_manager")


class ChromaSynapticManagerNode(Node):
    """
    Nodo ROS 2 che gestisce la dinamica sinaptica (forza, decadimento, protezione)
    dei ricordi archiviati all'interno di ChromaDB.
    """

    def __init__(self):
        super().__init__("chroma_synaptic_manager")
        
        # Dichiarazione parametri ROS 2
        self.declare_parameter("chroma_persist_dir", "/home/robopy/ChromaDB_Llama")
        self.declare_parameter("collection_name", "robot_memories")
        
        self.persist_dir = self.get_parameter("chroma_persist_dir").get_parameter_value().string_value
        self.collection_name = self.get_parameter("collection_name").get_parameter_value().string_value
        
        self.get_logger().info(f"Avvio ChromaSynapticManagerNode. Persistenza: {self.persist_dir}")
        
        self._lock = threading.RLock()
        self._initialized = False
        
        # Connessione a ChromaDB
        try:
            self.client = get_chroma_client(self.persist_dir)
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            self._initialized = True
            self.get_logger().info(f"Connessione a ChromaDB completata. Record totali: {self.collection.count()}")
        except Exception as e:
            self.get_logger().error(f"Errore inizializzazione ChromaDB: {e}")
            
        # Callback Group mutuamente esclusivo per la thread-safety di ROS 2
        self.service_callback_group = MutuallyExclusiveCallbackGroup()
        
        # Servizio ROS 2 per il rinforzo dei ricordi
        self.recall_service = self.create_service(
            MemoryRecall,
            "/memory/recall",
            self._handle_memory_recall,
            callback_group=self.service_callback_group
        )
        self.get_logger().info("Servizio /memory/recall inizializzato.")

    def _handle_memory_recall(self, request: MemoryRecall.Request, response: MemoryRecall.Response) -> MemoryRecall.Response:
        """
        Rinforza la memoria specificata incrementando recall_count e impostando
        la forza sinaptica (synaptic_strength) al valore massimo iniziale (100.0).
        """
        if not self._initialized:
            response.success = False
            response.message = "Database vettoriale ChromaDB non inizializzato."
            return response
            
        memory_id = request.memory_id
        self.get_logger().info(f"Richiesta di richiamo dopaminergico per la memoria: {memory_id}")
        
        with self._lock:
            try:
                # Recuperiamo il record corrente da ChromaDB
                result = self.collection.get(ids=[memory_id], include=["metadatas"])
                
                if not result or not result.get("ids") or len(result["ids"]) == 0:
                    response.success = False
                    response.message = f"Memoria {memory_id} non trovata nel database."
                    self.get_logger().warning(response.message)
                    return response
                
                # Otteniamo i metadati preesistenti
                metadata = result["metadatas"][0] if result["metadatas"] else {}
                
                # Calcolo dei nuovi valori con controlli difensivi sui tipi
                try:
                    current_recall = int(metadata.get("recall_count", 0))
                except (ValueError, TypeError):
                    current_recall = 0
                
                new_recall = current_recall + 1
                new_strength = 100.0
                
                # Aggiorniamo i metadati conservando il resto dei campi
                updated_metadata = metadata.copy()
                updated_metadata["synaptic_strength"] = new_strength
                updated_metadata["recall_count"] = new_recall
                updated_metadata["updated_at"] = time.time()
                updated_metadata["timestamp"] = datetime.now().isoformat()
                
                # Scrittura su ChromaDB
                self.collection.update(
                    ids=[memory_id],
                    metadatas=[updated_metadata]
                )
                
                response.success = True
                response.new_synaptic_strength = new_strength
                response.new_recall_count = new_recall
                response.message = f"Memoria {memory_id} rinforzata con successo."
                self.get_logger().info(f"Rinforzo completato: recall_count={new_recall}")
                
            except Exception as e:
                response.success = False
                response.message = f"Errore interno durante il richiamo: {str(e)}"
                self.get_logger().error(response.message)
                
        return response

    def write_synaptic_metadata(self, memory_id: str, updates: Dict[str, Any]) -> bool:
        """
        Aggiorna selettivamente i metadati di un record esistente in ChromaDB.
        """
        if not self._initialized:
            return False
            
        with self._lock:
            try:
                result = self.collection.get(ids=[memory_id], include=["metadatas"])
                if not result or not result.get("ids") or len(result["ids"]) == 0:
                    return False
                    
                metadata = result["metadatas"][0] or {}
                updated_metadata = metadata.copy()
                
                for k, v in updates.items():
                    # ChromaDB supporta solo tipi primitivi string, int, float, bool
                    if isinstance(v, bool):
                        updated_metadata[k] = "true" if v else "false"
                    else:
                        updated_metadata[k] = v
                        
                updated_metadata["updated_at"] = time.time()
                self.collection.update(ids=[memory_id], metadatas=[updated_metadata])
                return True
            except Exception as e:
                self.get_logger().error(f"Errore nell'aggiornamento metadati sinaptici per {memory_id}: {e}")
                return False


def main(args=None):
    rclpy.init(args=args)
    node = ChromaSynapticManagerNode()
    
    # Utilizzo di MultiThreadedExecutor per supportare callback asincrone e servizi paralleli
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
