#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
neuro_vegetative_bridge.py
==========================
Ponte neuro-vegetativo per il mimetismo fisico e la visualizzazione dello stato emotivo.
Sottoscrive lo stato dell'Amigdala e del Core Cognitivo per pubblicare:
1. Comandi LED su /respeaker/led_command
2. Stato emotivo su /ai/conversation/mood
"""

import os
import sys
import json
import logging
import threading

import rclpy
from rclpy.node import Node
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from std_msgs.msg import String

logger = logging.getLogger("neuro_vegetative_bridge")


class NeuroVegetativeBridgeNode(Node):
    """
    Nodo che funge da ponte tra gli stati emotivi interni e gli attuatori fisici
    (LED e coda) per garantire mimetismo ed espressività.
    """

    def __init__(self):
        super().__init__("neuro_vegetative_bridge")
        
        self._lock = threading.RLock()
        
        # Inizializziamo lo stato emozionale e cognitivo corrente
        self.current_amygdala_state = "CALM"
        self.current_cognitive_state = "INATTIVITÀ"
        
        # Callback groups
        self.sub_cb_group = MutuallyExclusiveCallbackGroup()
        self.timer_cb_group = MutuallyExclusiveCallbackGroup()
        
        # Publishers
        self.mood_pub = self.create_publisher(String, "/ai/conversation/mood", 10)
        self.led_command_pub = self.create_publisher(String, "/respeaker/led_command", 10)
        
        # Subscribers
        self.amygdala_state_sub = self.create_subscription(
            String,
            "/marcus/amygdala/state",
            self._amygdala_state_callback,
            10,
            callback_group=self.sub_cb_group
        )
        self.cognitive_state_sub = self.create_subscription(
            String,
            "/marcus/cognitive_state",
            self._cognitive_state_callback,
            10,
            callback_group=self.sub_cb_group
        )
        
        # Timer periodico per inviare aggiornamenti dei LED ed evitare sovrascrizioni
        self.update_timer = self.create_timer(2.0, self._publish_states, callback_group=self.timer_cb_group)
        
        self.get_logger().info("Nodo neuro_vegetative_bridge inizializzato correttamente.")

    def _amygdala_state_callback(self, msg: String):
        """
        Riceve l'aggiornamento dello stato emotivo dall'amigdala.
        """
        with self._lock:
            state = msg.data.upper()
            if self.current_amygdala_state != state:
                self.current_amygdala_state = state
                self.get_logger().info(f"Stato Amigdala aggiornato: {state}")
                self._update_actuators()

    def _cognitive_state_callback(self, msg: String):
        """
        Riceve l'aggiornamento dello stato cognitivo globale.
        """
        with self._lock:
            state = msg.data.upper()
            if self.current_cognitive_state != state:
                self.current_cognitive_state = state
                self.get_logger().info(f"Stato Cognitivo aggiornato: {state}")
                self._update_actuators()

    def _update_actuators(self):
        """
        Calcola l'uscita per i LED ed il mood in base alla combinazione di stati.
        """
        with self._lock:
            amygdala = self.current_amygdala_state
            cognitive = self.current_cognitive_state
            
        mood_data = {"mood": "calm", "intensity": 0.3}
        led_command = "LED_RGB:255,180,50"  # Oro caldo di default
        
        # Priorità 1: Sogno notturno (Cognitivo in SOGNO)
        if cognitive == "SOGNO":
            mood_data = {"mood": "dreaming", "intensity": 0.1}
            led_command = "LED_EFFECT:OFF"
            
        # Priorità 2: Hijack dell'Amigdala (Emergenza totale)
        elif amygdala == "HIJACK":
            mood_data = {"mood": "emergency", "intensity": 1.0}
            led_command = "LED_EFFECT:ERROR"  # Rosso lampeggiante
            
        # Priorità 3: Vigilanza ansiosa (Fear Conditioning)
        elif amygdala == "ANXIOUS_VIGILANCE":
            mood_data = {"mood": "anxious", "intensity": 0.6}
            led_command = "LED_RGB:120,0,200"  # Viola rapido
            
        # Priorità 4: Alert
        elif amygdala == "ALERT":
            mood_data = {"mood": "alert", "intensity": 0.8}
            led_command = "LED_RGB:100,0,180"  # Viola indaco
            
        # Priorità 5: Stato calmo generico
        else:
            mood_data = {"mood": "calm", "intensity": 0.3}
            led_command = "LED_RGB:255,180,50"  # Oro caldo
            
        # Pubblicazione immediata del mood
        mood_msg = String()
        mood_msg.data = json.dumps(mood_data)
        self.mood_pub.publish(mood_msg)
        
        # Pubblicazione immediata del LED command
        led_msg = String()
        led_msg.data = led_command
        self.led_command_pub.publish(led_msg)

    def _publish_states(self):
        """
        Pubblicazione periodica di salvaguardia dello stato dei LED ed emotivo.
        """
        self._update_actuators()


def main(args=None):
    rclpy.init(args=args)
    node = NeuroVegetativeBridgeNode()
    
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
