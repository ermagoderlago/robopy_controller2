#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intrinsic_motivation_and_reward_engine.py
=========================================
Sistema Biomimetico di Curiosità Epistemica, Ricompense Dopaminergiche (RPE)
e Istinti di Sopravvivenza/Socialità per Marcus AI (SPEC-05, FM-COG-001, FM-SIM-001).

Architettura a 4 Pilastri Umani:
1. Pulsioni Omeostatiche Concorrenti (HomeostaticDriveEngine - MAL):
   - Curiosità (Sorpresa spaziale 3D + incertezza stanze non visitate)
   - Socialità (Solitudine + riconoscimento biometrico ArcFace + inibizione ToM DO_NOT_DISTURB)
   - Autonomia / Istinto di Sopravvivenza Energetica (Tensione batteria 3S2P -> Homing alla Cuccia)
2. Sistema Dopaminergico con Riassorbimento Omeostatico & Efficacy Audit (FM-COG-001):
   - Calcolo Reward Prediction Error: delta_RPE = R_actual - R_expected
   - Decadimento esponenziale (Synaptic Reuptake) verso baseline D_0 = 0.30 per prevenire
     saturazione dopaminergica e loop ossessivi (Behavioral Entrapment).
   - Monitor di Entropia Comportamentale di Shannon H(A): penalizza per abituazione
     la ripetizione stereotipata della stessa azione.
3. Apprendimento Spaziale & Istinto di Autoconservazione (Amigdala + Bottleneck Map):
   - Ricorda dove ha incontrato ostacoli negativi (scale >15cm), stalli o traumi (Fear Map)
     e dove ha avuto difficoltà a passare (Bottleneck Map).
4. Deliberazione Cognitiva Validata da Pre-flight Kinematic Sandbox (SPEC-05 Red Zone):
   - Ogni azione autonoma deliberata dal cervello passa obbligatoriamente per
     `TrinityKinematicSandbox.validate_action()` ed è isolata in `ShadowMemorySandbox` (FM-SIM-001).
"""

import math
import time
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Any, List, Tuple, Optional

from robopy_controller.robot_ai.cognitive.homeostasis_mal import (
    HomeostaticDriveEngine,
    ProactiveEvent,
)
from robopy_controller.robot_ai.trinity.kinematic_sandbox import (
    TrinityKinematicSandbox,
    RobotSafetyContext,
    SandboxValidationResult,
)
from robopy_controller.robot_ai.trinity.shadow_memory_sandbox import (
    ShadowMemorySandbox,
)

logger = logging.getLogger("IntrinsicMotivationEngine")


@dataclass
class DopamineRPEEvent:
    event_type: str  # "epistemic_discovery", "social_praise", "homeostatic_dock", "navigation_failure", "tom_rejection"
    r_actual: float
    r_expected: float
    rpe_delta: float
    dopamine_level: float
    behavioral_entropy: float
    tail_angle_deg: float
    tail_freq_hz: float
    mood: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class RoomExperienceState:
    room_name: str
    center_xy: Tuple[float, float]
    visit_count: int = 0
    goal_failures: int = 0
    novelty_score: float = 0.85
    fear_level: float = 0.0
    expected_reward: float = 0.25
    last_visited_ts: float = 0.0


class DopamineRewardAndInstinctEngine:
    """
    Motore di motivazione intrinseca, ricompensa dopaminergica (FM-COG-001) e istinti
    per l'evoluzione autonoma di Marcus in Gazebo e nel mondo reale.
    """

    BASELINE_DOPAMINE: float = 0.30
    REUPTAKE_LAMBDA: float = 0.12  # Fattore di riassorbimento sinaptico esponenziale (FM-COG-001)
    MAX_TAIL_FREQ_HZ: float = 3.0  # Vincolo SPEC-01 Green Zone: frequenza <= 3 Hz

    def __init__(
        self,
        shadow_sandbox: Optional[ShadowMemorySandbox] = None,
        refractory_period_s: float = 15.0
    ):
        self.drive_engine = HomeostaticDriveEngine(refractory_period_s=refractory_period_s)
        self.kinematic_sandbox = TrinityKinematicSandbox()
        self.shadow_sandbox = shadow_sandbox or ShadowMemorySandbox()
        self.shadow_sandbox.activate()

        # Stato neurochimico
        self.dopamine_level: float = self.BASELINE_DOPAMINE
        self.last_rpe_delta: float = 0.0
        self.amygdala_state: str = "CALM"  # CALM, ANXIOUS_VIGILANCE, HIJACK
        self.current_mood: str = "calm_attentive"
        self.tail_angle_deg: float = 0.0
        self.tail_freq_hz: float = 0.0

        # Storico decisioni per calcolo Entropia Comportamentale H(A) (FM-COG-001)
        self.action_history: List[str] = []
        self.expected_action_rewards: Dict[str, float] = {
            "explore_frontier": 0.20,
            "inspect_anomaly": 0.25,
            "social_interaction": 0.25,
            "homing_recharge": 0.30,
        }

        # Mappa esperienziale delle stanze (Curiosità Epistemica + Bottleneck + Fear Map)
        now = time.time()
        self.rooms: Dict[str, RoomExperienceState] = {
            "salotto": RoomExperienceState("salotto", (1.8, 0.5), visit_count=1, novelty_score=0.30, last_visited_ts=now),
            "cucina": RoomExperienceState("cucina", (-2.0, 1.2), visit_count=0, novelty_score=0.90, last_visited_ts=now - 300),
            "studio": RoomExperienceState("studio", (1.8, -2.0), visit_count=0, novelty_score=0.85, last_visited_ts=now - 240),
            "ingresso": RoomExperienceState("ingresso", (0.5, -1.5), visit_count=0, novelty_score=0.80, last_visited_ts=now - 180),
            "base": RoomExperienceState("base", (0.0, 0.0), visit_count=1, novelty_score=0.10, last_visited_ts=now),
        }
        self.rpe_history: List[DopamineRPEEvent] = []

    def compute_behavioral_entropy(self) -> float:
        """
        Calcola l'Entropia di Shannon H(A) sulle ultime 12 decisioni per prevenire
        saturazione dopaminergica ed entrapment comportamentale (FM-COG-001).
        """
        window = self.action_history[-12:]
        if len(window) <= 1:
            return 1.0
        counts = Counter(window)
        n = float(len(window))
        entropy = 0.0
        for c in counts.values():
            p = c / n
            if p > 0:
                entropy -= p * math.log2(p)
        return round(entropy, 4)

    def apply_dopamine_reuptake(self, dt: float) -> float:
        """
        Riassorbimento sinaptico esponenziale (Homeostatic Dopamine Reuptake - FM-COG-001):
        D(t + dt) = D_0 + (D(t) - D_0) * e^(-lambda * dt)
        """
        decay = math.exp(-self.REUPTAKE_LAMBDA * max(0.0, dt))
        self.dopamine_level = self.BASELINE_DOPAMINE + (self.dopamine_level - self.BASELINE_DOPAMINE) * decay
        self.dopamine_level = max(0.0, min(1.0, self.dopamine_level))
        return self.dopamine_level

    def process_reward_outcome(
        self,
        action_category: str,
        r_actual: float,
        room_name: str = "salotto",
        description: str = ""
    ) -> DopamineRPEEvent:
        """
        Calcola il Reward Prediction Error (delta_RPE = R_actual - R_expected),
        applica l'habituation penalty se l'entropia comportamentale è troppo bassa,
        aggiorna il livello di Dopamina, la memoria sinaptica Shadow e l'espressività della coda.
        """
        self.action_history.append(action_category)
        entropy = self.compute_behavioral_entropy()

        # Se Marcus ripete ossessivamente la stessa azione (bassa entropia < 0.45 dopo >=4 azioni),
        # applica smorzamento per abituazione (FM-COG-001 Efficacy Audit)
        habituation_factor = 1.0
        if len(self.action_history) >= 4 and entropy < 0.45 and r_actual > 0:
            habituation_factor = 0.45

        effective_r = r_actual * habituation_factor
        r_exp = self.expected_action_rewards.get(action_category, 0.20)
        rpe_delta = effective_r - r_exp

        # Aggiornamento aspettativa con learning rate alpha = 0.25 (Rescorla-Wagner)
        self.expected_action_rewards[action_category] = max(
            -0.50, min(0.80, r_exp + 0.25 * rpe_delta)
        )

        # Aggiornamento fasico del livello di Dopamina con saturazione morbida tanh
        self.last_rpe_delta = round(rpe_delta, 4)
        self.dopamine_level = max(0.0, min(1.0, self.dopamine_level + 0.65 * math.tanh(rpe_delta)))

        # Aggiornamento stato stanza (Novelty, Bottleneck, Reward)
        if room_name in self.rooms:
            rm = self.rooms[room_name]
            if rpe_delta > 0:
                rm.visit_count += 1
                rm.novelty_score = max(0.05, rm.novelty_score * 0.45)  # Curiosità soddisfatta in questa stanza
                rm.last_visited_ts = time.time()
            elif rpe_delta < -0.15:
                rm.goal_failures += 1

        # Mapping neuro-espressivo (Coda SPEC-01 [-45°, +45°], <= 3Hz & Mood)
        if self.amygdala_state == "HIJACK":
            self.tail_angle_deg = 0.0
            self.tail_freq_hz = 0.0
            self.current_mood = "amygdala_fear_hijack"
        elif rpe_delta >= 0.15:
            self.tail_angle_deg = min(45.0, 20.0 + 40.0 * rpe_delta)
            self.tail_freq_hz = min(self.MAX_TAIL_FREQ_HZ, 1.5 + 2.0 * rpe_delta)
            self.current_mood = "joyful_rewarded"
        elif rpe_delta <= -0.15:
            self.tail_angle_deg = -15.0
            self.tail_freq_hz = 0.5
            self.current_mood = "frustrated_adapting"
        else:
            self.tail_angle_deg = 10.0
            self.tail_freq_hz = 1.0
            self.current_mood = "curious_attentive"

        # Persistenza nella Shadow Memory Sandbox (FM-SIM-001)
        synaptic_strength = max(10.0, min(200.0, 100.0 + 80.0 * rpe_delta))
        self.shadow_sandbox.record_Memory_event(
            memory_id=f"rpe_{action_category}_{int(time.time() * 1000)}",
            content=description or f"Outcome {action_category} in {room_name}: RPE={rpe_delta:+.2f}",
            memory_type="dopamine_rl",
            synaptic_strength=synaptic_strength,
            dopamine_rpe=rpe_delta,
            amygdala_protected=False,
            room_name=room_name,
        )

        ev = DopamineRPEEvent(
            event_type=action_category,
            r_actual=round(effective_r, 4),
            r_expected=round(r_exp, 4),
            rpe_delta=round(rpe_delta, 4),
            dopamine_level=round(self.dopamine_level, 4),
            behavioral_entropy=entropy,
            tail_angle_deg=round(self.tail_angle_deg, 2),
            tail_freq_hz=round(self.tail_freq_hz, 2),
            mood=self.current_mood,
        )
        self.rpe_history.append(ev)
        return ev

    def register_amygdala_fear_event(self, event_type: str, reason: str, room_name: str = "salotto") -> None:
        """
        Istinto di Autoconservazione (Low-Road Hijack): registra il trauma nella Fear Map
        e nella Shadow Sandbox (amygdala_protected=True, lambda_decay=0.0).
        """
        self.amygdala_state = "HIJACK"
        self.tail_angle_deg = 0.0
        self.tail_freq_hz = 0.0
        self.current_mood = "amygdala_fear_hijack"

        if room_name in self.rooms:
            self.rooms[room_name].fear_level = min(1.0, self.rooms[room_name].fear_level + 0.65)

        self.shadow_sandbox.record_Memory_event(
            memory_id=f"trauma_{event_type}_{int(time.time() * 1000)}",
            content=f"AMYGDALA HIJACK [{event_type}]: {reason}",
            memory_type="amygdala_trauma",
            synaptic_strength=100.0,
            dopamine_rpe=-0.50,
            amygdala_protected=True,
            room_name=room_name,
        )

    def select_most_curious_safe_room(self) -> str:
        """
        Seleziona la stanza con il massimo valore epistemico (alta novità, basso pericolo
        e bassi fallimenti/bottleneck) per l'esplorazione autonoma guidata da curiosità.
        """
        best_room = "cucina"
        best_score = -999.0
        now = time.time()

        for name, rm in self.rooms.items():
            if name == "base":
                continue
            staleness_bonus = min(0.40, (now - rm.last_visited_ts) / 600.0)
            bottleneck_penalty = 0.25 * rm.goal_failures
            fear_penalty = 0.80 * rm.fear_level
            score = rm.novelty_score + staleness_bonus - bottleneck_penalty - fear_penalty
            if score > best_score:
                best_score = score
                best_room = name
        return best_room

    def step_autonomous_cycle(
        self,
        dt: float,
        safety_context: RobotSafetyContext,
        spatial_anomaly: Optional[Dict[str, Any]] = None
    ) -> Tuple[Dict[str, float], Optional[ProactiveEvent], Optional[SandboxValidationResult], Optional[Dict[str, Any]]]:
        """
        Esegue un ciclo completo Percezione -> Pulsioni MAL -> Deliberazione Cognitiva ->
        Pre-flight Kinematic Sandbox:
        1. Aggiorna il riassorbimento dopaminergico e la telemetria in HomeostaticDriveEngine.
        2. Se c'è un'anomalia spaziale 3D, la inietta nella Curiosità.
        3. Se l'Amigdala è in HIJACK o c'è un ostacolo negativo (scale >= 15cm), forza `stop_motion`.
        4. Altrimenti, se una pulsione supera la soglia (0.38) o la batteria è in riserva (<= 10.15V),
           delibera la chiamata di funzione strutturata e la valida con `TrinityKinematicSandbox`.
        """
        self.apply_dopamine_reuptake(dt)
        self.amygdala_state = safety_context.amygdala_state

        # 1. Istinto Primordiale di Sopravvivenza Immediata (Amigdala Hijack o Scale/Drop-Off >= 15cm)
        if safety_context.amygdala_state == "HIJACK" or safety_context.max_depth_drop >= 0.15:
            stop_cmd = {"function": "stop_motion", "arguments": {}}
            val_res = self.kinematic_sandbox.validate_action(stop_cmd, safety_context)
            return (
                {"curiosity": self.drive_engine.curiosity, "sociality": self.drive_engine.sociality, "autonomy": self.drive_engine.autonomy},
                None,
                val_res,
                stop_cmd,
            )

        # 2. Iniezione eventuale sorpresa spaziale 3D
        if spatial_anomaly is not None:
            self.drive_engine.inject_spatial_anomaly(spatial_anomaly)

        # 3. Passo di integrazione MAL delle 3 pulsioni
        drives, proactive_event = self.drive_engine.step(dt=dt)

        # 4. Istinto Energetico Hard-Trigger se tensione <= 10.15V (SPEC-06)
        if safety_context.battery_voltage <= 10.15 and not safety_context.is_charging:
            dock_cmd = {"function": "navigate_to", "arguments": {"destination": "base"}}
            val_res = self.kinematic_sandbox.validate_action(dock_cmd, safety_context)
            return drives, proactive_event, val_res, dock_cmd

        # 5. Deliberazione autonoma su evento proattivo (Curiosità, Socialità, Autonomia)
        if proactive_event is not None:
            if proactive_event.drive_name == "autonomy":
                cmd = {"function": "navigate_to", "arguments": {"destination": "base"}}
            elif proactive_event.drive_name == "curiosity":
                target_room = self.select_most_curious_safe_room()
                cmd = {"function": "navigate_to", "arguments": {"destination": target_room}}
            elif proactive_event.drive_name == "sociality":
                cmd = {"function": "navigate_to", "arguments": {"destination": "salotto"}}
            else:
                cmd = {"function": "start_frontier_exploration", "arguments": {}}

            val_res = self.kinematic_sandbox.validate_action(cmd, safety_context)
            return drives, proactive_event, val_res, cmd

        return drives, None, None, None
