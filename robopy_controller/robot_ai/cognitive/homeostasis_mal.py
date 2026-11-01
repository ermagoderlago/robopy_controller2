#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Homeostatic Drive Engine with Mutual Activation Limit (MAL) & Active Inference.
Governed by SPEC-05, SPEC-04, and Milestone 4 architecture.

Implements a 3-drive homeostatic vector:
  D = [d_curiosity, d_sociality, d_autonomy]^T in [0.0, 1.0]^3

Features:
- Mutual Activation Limit (MAL): Cross-inhibitory matrix W_MAL preventing drive saturation
- Dynamic Suppression: Theory of Mind (ToM) "DO_NOT_DISTURB" reduces sociality and curiosity by 85%
- Spatial Surprise Injection: Receives S(O_i) from marcus_semantic_mapper_cpp to feed curiosity
- Biometric & Loneliness Boost: ArcFace familiar identity and inactivity timer feed sociality
- Battery Governor Integration: Voltage and SOC feed autonomy/rest
- Proactive Gating Bypass Trigger: When max(D) >= 0.38 outside refractory cooldown, emits ProactiveEvent
"""

import time
import math
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger("HomeostasisMAL")


@dataclass
class ProactiveEvent:
    drive_name: str
    drive_intensity: float
    trigger_reason: str
    target_identity: Optional[str] = None
    target_object: Optional[str] = None
    target_position: Optional[Tuple[float, float, float]] = None
    suggested_utterance: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class HomeostaticDriveEngine:
    """
    Engine omeostatico a 3 pulsioni concorrenti con dinamica MAL (Mutual Activation Limit).
    """

    CRITICAL_THRESHOLD: float = 0.38
    REFRACTORY_PERIOD_SEC: float = 90.0

    def __init__(self, refractory_period_s: float = 90.0):
        self.refractory_period_s = refractory_period_s

        # Drive baseline values [0.0, 1.0]
        self.curiosity: float = 0.05
        self.sociality: float = 0.05
        self.autonomy: float = 0.05

        # External state inputs
        self.last_user_interaction_time: float = time.time()
        self.last_proactive_trigger_time: float = 0.0
        self.current_battery_voltage: float = 12.2
        self.current_battery_soc: float = 100.0
        self.tom_state: str = "ENGAGED"  # "DO_NOT_DISTURB", "ENGAGED", "IDLE", "FALL_EMERGENCY"
        self.recognized_person_name: Optional[str] = None
        self.last_spatial_anomaly: Optional[Dict[str, Any]] = None

        # Cross-inhibition weights (MAL Matrix W)
        # Row i inhibited by column j: W_ij
        self.w_mal = {
            ("curiosity", "sociality"): 0.35,
            ("curiosity", "autonomy"): 0.60,
            ("sociality", "curiosity"): 0.30,
            ("sociality", "autonomy"): 0.50,
            ("autonomy", "curiosity"): 0.70,
            ("autonomy", "sociality"): 0.70,
        }

    def update_telemetry(
        self,
        battery_voltage: float,
        battery_soc: float = 100.0,
        tom_state: str = "ENGAGED",
        recognized_person: Optional[str] = None
    ):
        """Aggiorna lo stato contestuale del robot."""
        self.current_battery_voltage = float(battery_voltage)
        self.current_battery_soc = float(battery_soc)
        self.tom_state = str(tom_state)
        self.recognized_person_name = recognized_person

    def inject_spatial_anomaly(self, anomaly_data: Dict[str, Any]):
        """Inietta una sorpresa spaziale da marcus_semantic_mapper_cpp (S(O_i) >= 0.38)."""
        self.last_spatial_anomaly = anomaly_data
        surprise_score = float(anomaly_data.get("surprise_score", 0.5))
        # Salto immediato della curiosità sopra la soglia critica
        self.curiosity = min(1.0, max(self.curiosity, 0.10) + 0.50 * surprise_score)

    def notify_interaction(self):
        """Notifica che l'utente ha interagito con Marcus (reset socialità e timer inattività)."""
        self.last_user_interaction_time = time.time()
        self.sociality = max(0.05, self.sociality - 0.45)

    def step(self, dt: float = 0.5) -> Tuple[Dict[str, float], Optional[ProactiveEvent]]:
        """
        Esegue un passo di integrazione temporale MAL e Active Inference.
        Restituisce (drives_dict, eventuale_proactive_event).
        """
        now = time.time()
        idle_duration = now - self.last_user_interaction_time

        # 1. Calcolo afferenze grezze (Feedforward Drives)
        # Sociality cresce con inattività verso LONELY (>120s) e presenza di familiari noti
        social_growth = 0.002 * (idle_duration / 60.0)
        if self.recognized_person_name and self.recognized_person_name.lower() not in ("unknown", "none"):
            # Presenza di familiare riconosciuto da ArcFace: forte spinta sociale
            social_growth += 0.08

        # Autonomy cresce se la batteria scende sotto 10.8V
        if self.current_battery_voltage < 10.40:
            autonomy_target = 0.95
        elif self.current_battery_voltage < 10.80:
            autonomy_target = 0.65
        elif self.current_battery_voltage < 11.20:
            autonomy_target = 0.30
        else:
            autonomy_target = 0.05
        autonomy_growth = (autonomy_target - self.autonomy) * 0.10

        # Curiosity decade naturalmente nel tempo se non rifornita da anomalie
        curiosity_decay = -0.015 * dt

        # Integrazione pre-MAL
        raw_curiosity = max(0.0, min(1.0, self.curiosity + curiosity_decay))
        raw_sociality = max(0.0, min(1.0, self.sociality + social_growth * dt))
        raw_autonomy = max(0.0, min(1.0, self.autonomy + autonomy_growth * dt))

        # 2. MAL (Mutual Activation Limit) Cross-Inhibition
        # d'_i = raw_i - sum_j (W_ij * raw_j)
        mal_curiosity = raw_curiosity - (
            self.w_mal[("curiosity", "sociality")] * raw_sociality +
            self.w_mal[("curiosity", "autonomy")] * raw_autonomy
        ) * dt
        mal_sociality = raw_sociality - (
            self.w_mal[("sociality", "curiosity")] * raw_curiosity +
            self.w_mal[("sociality", "autonomy")] * raw_autonomy
        ) * dt
        mal_autonomy = raw_autonomy - (
            self.w_mal[("autonomy", "curiosity")] * raw_curiosity +
            self.w_mal[("autonomy", "sociality")] * raw_sociality
        ) * dt

        # 3. Dynamic Theory of Mind (ToM) Modulation
        # Se l'utente è al banco di lavoro in DO_NOT_DISTURB, sopprimi curiosità e socialità
        if self.tom_state == "DO_NOT_DISTURB":
            mal_curiosity *= 0.15
            mal_sociality *= 0.15

        # Clamping finale [0.0, 1.0]
        self.curiosity = max(0.0, min(1.0, mal_curiosity))
        self.sociality = max(0.0, min(1.0, mal_sociality))
        self.autonomy = max(0.0, min(1.0, mal_autonomy))

        drives = {
            "curiosity": round(self.curiosity, 3),
            "sociality": round(self.sociality, 3),
            "autonomy": round(self.autonomy, 3)
        }

        # 4. Proactive Event Evaluation
        proactive_event = None
        in_refractory = (now - self.last_proactive_trigger_time) < self.refractory_period_s

        if not in_refractory and self.tom_state != "DO_NOT_DISTURB":
            # Trova la pulsione dominante
            dominant_drive = max(drives, key=drives.get)
            dominant_val = drives[dominant_drive]

            if dominant_val >= self.CRITICAL_THRESHOLD:
                self.last_proactive_trigger_time = now

                if dominant_drive == "autonomy":
                    proactive_event = ProactiveEvent(
                        drive_name="autonomy",
                        drive_intensity=dominant_val,
                        trigger_reason="low_battery_rest_needed",
                        suggested_utterance="Sento la batteria un po' scarica. Se non hai bisogno di me, torno alla cuccia a ricaricarmi."
                    )
                    self.autonomy = 0.10

                elif dominant_drive == "curiosity" and self.last_spatial_anomaly:
                    lbl = self.last_spatial_anomaly.get("label", "oggetto")
                    x = self.last_spatial_anomaly.get("x", 0.0)
                    y = self.last_spatial_anomaly.get("y", 0.0)
                    z = self.last_spatial_anomaly.get("z", 1.0)
                    proactive_event = ProactiveEvent(
                        drive_name="curiosity",
                        drive_intensity=dominant_val,
                        trigger_reason="spatial_surprise_exploration",
                        target_object=lbl,
                        target_position=(x, y, z),
                        suggested_utterance=f"Ho notato che c'è un oggetto insolito qui vicino, sembra un {lbl}. Lo hai spostato tu?"
                    )
                    self.curiosity = 0.10
                    self.last_spatial_anomaly = None

                elif dominant_drive == "sociality":
                    if self.recognized_person_name and self.recognized_person_name.lower() not in ("unknown", "none"):
                        p_name = self.recognized_person_name.capitalize()
                        proactive_event = ProactiveEvent(
                            drive_name="sociality",
                            drive_intensity=dominant_val,
                            trigger_reason="familiar_person_detected",
                            target_identity=p_name,
                            suggested_utterance=f"Ciao {p_name}! Ti ho riconosciuto, hai bisogno di una mano o posso farti compagnia?"
                        )
                    else:
                        proactive_event = ProactiveEvent(
                            drive_name="sociality",
                            drive_intensity=dominant_val,
                            trigger_reason="lonely_inactivity_checkin",
                            suggested_utterance="Ciao! Sono qui attivo se hai bisogno di me."
                        )
                    self.sociality = 0.10

        return drives, proactive_event
