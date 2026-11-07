#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
virtual_human_actors_node.py
============================
Simulatore di Attori Umani Virtuali Interattivi per l'ambiente Gazebo di Marcus.

Funzionalità:
1. Movimento autonomo tra le stanze della casa (Salotto, Cucina, Studio, Ingresso)
   e posture contestuali:
   - `STANDING` / `WALKING`: Interazione libera e Person Following (FM-BIO-002)
   - `DO_NOT_DISTURB_DESK`: Lavoro concentrato allo Studio -> inibisce dell'85%
     le pulsioni di Curiosità e Socialità di Marcus (Theory of Mind - IMP-BIO-004)
   - `WAVING` / `STOP_GESTURE`: Gesti visivi a 17 keypoints per la NPU Hailo
   - `FALL_EMERGENCY`: Simulazione caduta per testare il soccorso attivo
2. Interazione Vocale & Dialogo Bidirezionale con Marcus (SPEC-04):
   - Gli umani virtuali parlano a Marcus (comandi vocali, lodi, risposte)
   - Quando Marcus fa una domanda proattiva spinto dalla Curiosità o dalla Socialità
     (su `/ai/conversation/proactive_speech` o `/robopy/conversation_tx`), l'umano
     virtuale risponde in modo coerente ed emette un segnale di ricompensa sociale
     che stimola il rilascio di Dopamina (+RPE) nel cervello di Marcus!
   - Generatore PCM 16000 Hz Mono 16-bit (SPEC-04 Red Zone) per testare:
     * Parlato normale
     * Picco acustico improvviso (Startle Reflex, RMS > 0.50)
     * Urlo / Stress vocale di panico (Amigdala Low-Road Hijack, RMS > 0.35 & ZCR < 0.15)
"""

from __future__ import annotations

import math
import time
import json
from dataclasses import dataclass, field
from typing import Dict, Any, List, Tuple, Optional
import numpy as np

try:
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String
    try:
        from robopy_controller.msg import AudioData
    except ImportError:
        AudioData = None
    HAS_ROS2 = True
except ImportError:
    HAS_ROS2 = False
    Node = object
    AudioData = None


# Coordinate stanze di marcus_smart_home.sdf
SMART_HOME_ROOMS: Dict[str, Tuple[float, float]] = {
    "salotto": (1.8, 0.5),
    "cucina": (-2.0, 1.2),
    "studio": (1.8, -2.0),
    "ingresso": (0.5, -1.5),
}


@dataclass
class VirtualHumanActor:
    actor_id: str
    identity: str  # es. "luca" (familiare riconosciuto da ArcFace) o "unknown" (ospite)
    x: float
    y: float
    target_x: float
    target_y: float
    speed_mps: float = 0.45
    posture: str = "STANDING"
    tom_state: str = "ENGAGED"  # ENGAGED, IDLE, DO_NOT_DISTURB, FALL_EMERGENCY
    current_room: str = "salotto"
    last_utterance: str = ""
    mood: str = "friendly"


class VirtualHumanActorsEngine:
    """
    Motore deterministico di comportamento, dialogo e sintesi PCM 16kHz degli umani virtuali.
    """

    SAMPLE_RATE_HZ: int = 16000  # SPEC-04 Zona Rossa: 16000 Hz Mono PCM 16-bit

    def __init__(self):
        self.actors: Dict[str, VirtualHumanActor] = {
            "human_luca": VirtualHumanActor(
                actor_id="human_luca",
                identity="luca",
                x=1.8,
                y=0.5,
                target_x=1.8,
                target_y=0.5,
                posture="STANDING",
                tom_state="ENGAGED",
                current_room="salotto",
            ),
            "human_guest": VirtualHumanActor(
                actor_id="human_guest",
                identity="unknown",
                x=-2.0,
                y=1.2,
                target_x=-2.0,
                target_y=1.2,
                posture="STANDING",
                tom_state="IDLE",
                current_room="cucina",
            ),
        }
        self.dialogue_history: List[Dict[str, Any]] = []

    def move_actor_to_room(self, actor_id: str, room_name: str, posture: str = "STANDING") -> VirtualHumanActor:
        actor = self.actors[actor_id]
        room_key = room_name.strip().lower()
        if room_key in SMART_HOME_ROOMS:
            tx, ty = SMART_HOME_ROOMS[room_key]
            actor.target_x = tx
            actor.target_y = ty
            actor.current_room = room_key

        actor.posture = posture
        if posture == "DO_NOT_DISTURB_DESK":
            actor.tom_state = "DO_NOT_DISTURB"
        elif posture == "FALL_EMERGENCY":
            actor.tom_state = "FALL_EMERGENCY"
        else:
            actor.tom_state = "ENGAGED"
        return actor

    def step(self, dt: float) -> Dict[str, VirtualHumanActor]:
        """Aggiorna la cinematica degli umani virtuali verso i rispettivi waypoint."""
        for actor in self.actors.values():
            dx = actor.target_x - actor.x
            dy = actor.target_y - actor.y
            dist = math.hypot(dx, dy)
            if dist > 0.05:
                step_d = min(dist, actor.speed_mps * dt)
                actor.x += (dx / dist) * step_d
                actor.y += (dy / dist) * step_d
                if actor.posture not in ("DO_NOT_DISTURB_DESK", "FALL_EMERGENCY"):
                    actor.posture = "WALKING"
            else:
                if actor.posture == "WALKING":
                    actor.posture = "STANDING"
        return self.actors

    def respond_to_marcus_proactive_speech(
        self,
        marcus_utterance: str,
        actor_id: str = "human_luca"
    ) -> Dict[str, Any]:
        """
        Genera una risposta umana naturale quando Marcus inizia una conversazione
        spinto da Curiosità, Socialità o Autonomia, calcolando la valenza sociale
        (ricompensa dopaminergica per Marcus).
        """
        actor = self.actors.get(actor_id, self.actors["human_luca"])
        text_low = marcus_utterance.lower()

        # Caso 1: L'umano è in modalità DO_NOT_DISTURB al banco di lavoro
        if actor.tom_state == "DO_NOT_DISTURB":
            reply = "Marcus, adesso sto lavorando concentrato, parliamo più tardi per favore."
            social_reward = -0.25
            sentiment = "annoyed_busy"
        # Caso 2: Marcus fa una domanda curiosa su un oggetto insolito / sorpresa spaziale
        elif any(k in text_low for k in ("oggetto insolito", "spostato", "notato", "sembra un", "cos'è")):
            reply = "Bravo Marcus! Che bella osservazione: l'ho appoggiato io lì poco fa, grazie per avermelo segnalato!"
            social_reward = +0.48
            sentiment = "praise_curiosity"
        # Caso 3: Marcus saluta un familiare per pulsione di socialità
        elif any(k in text_low for k in ("ciao", "riconosciuto", "compagnia", "mano")):
            reply = f"Ciao Marcus! Che piacere vederti, sei proprio un ottimo compagno!"
            social_reward = +0.42
            sentiment = "warm_affection"
        # Caso 4: Marcus avvisa che torna alla cuccia per batteria scarica
        elif any(k in text_low for k in ("batteria", "cuccia", "ricaricarmi")):
            reply = "Certamente Marcus, vai pure alla cuccia a ricaricarti, a dopo!"
            social_reward = +0.35
            sentiment = "supportive"
        else:
            reply = "Ti ascolto Marcus, dimmi pure!"
            social_reward = +0.20
            sentiment = "neutral_positive"

        actor.last_utterance = reply
        event = {
            "actor_id": actor.actor_id,
            "identity": actor.identity,
            "marcus_said": marcus_utterance,
            "human_reply": reply,
            "social_reward": social_reward,
            "sentiment": sentiment,
            "timestamp": time.time(),
        }
        self.dialogue_history.append(event)
        return event

    @classmethod
    def synthesize_pcm16_audio_chunk(
        cls,
        mode: str = "normal_speech",
        duration_ms: int = 20
    ) -> bytes:
        """
        Genera un frame audio PCM a 16000 Hz Mono 16-bit little-endian (SPEC-04 Red Zone):
        - `normal_speech`: parlato umano modulato (RMS ~ 0.12)
        - `vocal_stress_scream`: urlo/panico ad alta energia e basso ZCR (RMS > 0.35, ZCR < 0.15)
          per testare la Regola 3 della Low Road in `CognitiveAmygdalaNode`!
        - `startle_bang`: picco sonoro impulsivo forte (RMS > 0.55) per testare lo Startle Reflex!
        """
        num_samples = int((cls.SAMPLE_RATE_HZ * duration_ms) / 1000)
        t = np.arange(num_samples, dtype=np.float32) / float(cls.SAMPLE_RATE_HZ)

        if mode == "vocal_stress_scream":
            # Onda vocale fondamentale a 220 Hz a bassa frequenza di zero-crossing (ZCR < 0.15) e RMS ~ 0.48
            wave = 0.68 * np.sin(2.0 * np.pi * 220.0 * t)
        elif mode == "startle_bang":
            # Picco forte con RMS > 0.55
            wave = 0.85 * np.sin(2.0 * np.pi * 180.0 * t)
        else:
            # Parlato normale moderato
            wave = 0.18 * np.sin(2.0 * np.pi * 160.0 * t) + 0.05 * np.sin(2.0 * np.pi * 320.0 * t)

        pcm16 = np.clip(wave * 32767.0, -32768, 32767).astype(np.int16)
        return pcm16.tobytes()


class VirtualHumanActorsNode(Node):
    """
    Nodo ROS 2 per la gestione degli attori umani virtuali e dell'interazione vocale/sociale
    con Marcus in Gazebo.
    """

    def __init__(self):
        super().__init__("virtual_human_actors_node")
        self.engine = VirtualHumanActorsEngine()
        self._last_time = time.time()

        # Subscriptions
        self.create_subscription(String, "/ai/conversation/proactive_speech", self._on_marcus_proactive_speech, 10)
        self.create_subscription(String, "/robopy/conversation_tx", self._on_marcus_proactive_speech, 10)
        self.create_subscription(String, "/sim/human_actor/command", self._on_actor_command, 10)

        # Publishers
        self.pub_actor_state = self.create_publisher(String, "/sim/human_actor/state", 10)
        self.pub_human_speech = self.create_publisher(String, "/sim/human_actor/speech", 10)
        self.pub_ai_text = self.create_publisher(String, "/ai/input/text", 10)
        self.pub_tom_state = self.create_publisher(String, "/marcus/social_context/tom_state", 10)
        self.pub_reward = self.create_publisher(String, "/marcus/reward/social_feedback", 10)

        if AudioData is not None:
            self.pub_audio_chunk = self.create_publisher(AudioData, "/ai/input/audio_chunk", 10)
        else:
            self.pub_audio_chunk = None

        self.create_timer(0.10, self._update_actors_10hz)
        self.get_logger().info("👥 [VirtualHumanActorsNode] Attori umani virtuali (Luca, Ospite) attivi nel mondo.")

    def _on_actor_command(self, msg: String):
        """
        Comandi JSON per muovere o far parlare gli umani virtuali:
        es. {"actor_id": "human_luca", "room": "studio", "posture": "DO_NOT_DISTURB_DESK"}
        oppure {"actor_id": "human_luca", "say": "Marcus, vieni in cucina", "audio_mode": "normal_speech"}
        """
        try:
            cmd = json.loads(msg.data)
            actor_id = cmd.get("actor_id", "human_luca")
            if "room" in cmd or "posture" in cmd:
                actor = self.engine.move_actor_to_room(
                    actor_id,
                    cmd.get("room", self.engine.actors[actor_id].current_room),
                    cmd.get("posture", "STANDING"),
                )
                tom_msg = String()
                tom_msg.data = actor.tom_state
                self.pub_tom_state.publish(tom_msg)

            if "say" in cmd:
                utterance = cmd["say"]
                t_msg = String()
                t_msg.data = utterance
                self.pub_ai_text.publish(t_msg)
                self.pub_human_speech.publish(t_msg)

            if "audio_mode" in cmd and self.pub_audio_chunk is not None:
                pcm_bytes = self.engine.synthesize_pcm16_audio_chunk(mode=cmd["audio_mode"])
                a_msg = AudioData()
                a_msg.data = list(pcm_bytes)
                self.pub_audio_chunk.publish(a_msg)
        except Exception as e:
            self.get_logger().error(f"Errore comando attore umano: {e}")

    def _on_marcus_proactive_speech(self, msg: String):
        if not msg.data:
            return
        interaction = self.engine.respond_to_marcus_proactive_speech(msg.data)
        self.get_logger().info(
            f"🗣️ [Umano Virtuale '{interaction['identity']}'] Risponde a Marcus: \"{interaction['human_reply']}\" "
            f"(Ricompensa Sociale: {interaction['social_reward']:+.2f})"
        )

        s_msg = String()
        s_msg.data = interaction["human_reply"]
        self.pub_human_speech.publish(s_msg)

        r_msg = String()
        r_msg.data = json.dumps(interaction)
        self.pub_reward.publish(r_msg)

    def _update_actors_10hz(self):
        now = time.time()
        dt = max(0.01, min(0.5, now - self._last_time))
        self._last_time = now

        actors = self.engine.step(dt)
        for actor in actors.values():
            st_msg = String()
            st_msg.data = json.dumps({
                "actor_id": actor.actor_id,
                "identity": actor.identity,
                "x": round(actor.x, 3),
                "y": round(actor.y, 3),
                "posture": actor.posture,
                "tom_state": actor.tom_state,
                "room": actor.current_room,
            })
            self.pub_actor_state.publish(st_msg)


def main(args=None):
    if not HAS_ROS2:
        return
    rclpy.init(args=args)
    node = VirtualHumanActorsNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
