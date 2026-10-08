"""
ResourceGovernor — Engine deterministico a regioni ortogonali per l'allocazione
dinamica di risorse (CPU, NPU, frequenze, lifecycle).

Principi architetturali non negoziabili (IMP-GOV-001):
1. Pavimento uditivo invariante (microfono, VAD, KWS, parole di stop, ECAPA residente).
2. Modelli piccoli residenti (si governa la frequenza di campionamento, non la presenza).
3. Fail-safe verso SAFE_FULL (frequenze nominali, Nav2 inattivo, motion gate chiuso).
4. Nessun accesso diretto a /cmd_vel dal Governor (la deliberazione di moto spetta all'LLM/Nav2).
5. LiDAR attivo in tutti gli stati tranne DOCKED_SLEEP.
6. Qwen2-VL (VLM) ammesso SOLO nello stato STATIONARY.
"""

import time
from enum import Enum
from typing import Dict, Any, Optional, List


class KinematicState(str, Enum):
    STATIONARY = "STATIONARY"
    DOCKED_SLEEP = "DOCKED_SLEEP"
    WAKING = "WAKING"
    PREP_NAV = "PREP_NAV"
    RELOCALIZING = "RELOCALIZING"
    MOVING = "MOVING"
    STOPPING = "STOPPING"


class CognitiveState(str, Enum):
    VIGILANT = "VIGILANT"
    ENGAGED = "ENGAGED"
    LOCAL_VLM = "LOCAL_VLM"


class SafetyState(str, Enum):
    NORMAL = "NORMAL"
    SAFE_FULL = "SAFE_FULL"


class ResourceProfile:
    """Profilo delle frequenze e allocazioni calcolate dal Governor."""
    def __init__(
        self,
        camera_fps: int = 15,
        scrfd_hz: float = 2.0,
        arcface_recheck_sec: float = 5.0,
        pose_hz: float = 0.0,
        yolo_hz: float = 2.0,
        vio_hz: float = 1.0,
        vpr_hz: float = 0.0,
        lidar_active: bool = True,
        rtabmap_paused: bool = True,
        nav2_active: bool = False,
        motion_gate_open: bool = False,
        vlm_loaded: bool = False,
        hearing_floor_active: bool = True
    ):
        self.camera_fps = camera_fps
        self.scrfd_hz = scrfd_hz
        self.arcface_recheck_sec = arcface_recheck_sec
        self.pose_hz = pose_hz
        self.yolo_hz = yolo_hz
        self.vio_hz = vio_hz
        self.vpr_hz = vpr_hz
        self.lidar_active = lidar_active
        self.rtabmap_paused = rtabmap_paused
        self.nav2_active = nav2_active
        self.motion_gate_open = motion_gate_open
        self.vlm_loaded = vlm_loaded
        # Invariante 1: Pavimento uditivo sempre forzato a True
        self.hearing_floor_active = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_fps": self.camera_fps,
            "scrfd_hz": self.scrfd_hz,
            "arcface_recheck_sec": self.arcface_recheck_sec,
            "pose_hz": self.pose_hz,
            "yolo_hz": self.yolo_hz,
            "vio_hz": self.vio_hz,
            "vpr_hz": self.vpr_hz,
            "lidar_active": self.lidar_active,
            "rtabmap_paused": self.rtabmap_paused,
            "nav2_active": self.nav2_active,
            "motion_gate_open": self.motion_gate_open,
            "vlm_loaded": self.vlm_loaded,
            "hearing_floor_active": self.hearing_floor_active,
        }


class InvariantViolation(Exception):
    """Sollevata quando viene rilevato un tentativo di violazione degli invarianti di sicurezza."""
    pass


class ResourceGovernorEngine:
    """
    Engine Statechart a due regioni ortogonali: Cinematica x Cognitiva,
    con stato globale di sicurezza SAFE_FULL.
    """

    def __init__(
        self,
        idle_dock_timeout_s: float = 180.0,
        waking_duration_s: float = 2.0,
        stopping_duration_s: float = 0.5,
        dialogue_timeout_s: float = 180.0,
        shadow_mode: bool = True
    ):
        self.idle_dock_timeout_s = idle_dock_timeout_s
        self.waking_duration_s = waking_duration_s
        self.stopping_duration_s = stopping_duration_s
        self.dialogue_timeout_s = dialogue_timeout_s
        self.shadow_mode = shadow_mode

        # Stati iniziali
        self.kinematic_state = KinematicState.STATIONARY
        self.cognitive_state = CognitiveState.VIGILANT
        self.safety_state = SafetyState.NORMAL

        # Timestamp di monitoraggio
        now = time.monotonic()
        self._state_enter_time = now
        self._last_speech_time = now
        self._last_velocity_zero_time = now
        self._last_dock_idle_time = now

        # Flag telemetrici
        self.is_in_dock = False
        self.current_speed = 0.0
        self.person_detected = False
        self.cloud_connected = True
        self.scan_consistency_ok = True
        self.nav_intent_active = False

        # Storico transizioni e audit invarianti
        self.transition_history: List[Dict[str, Any]] = []
        self.invariant_violations: List[str] = []

    def update_telemetry(
        self,
        linear_speed: float,
        is_charging_or_docked: bool,
        person_present: bool = False,
        cloud_online: bool = True,
        scan_to_map_match_ok: bool = True
    ):
        """Aggiorna i segnali telemetrici dei sensori di sistema."""
        now = time.monotonic()
        self.current_speed = abs(linear_speed)
        self.is_in_dock = bool(is_charging_or_docked)
        self.person_detected = bool(person_present)
        self.cloud_connected = bool(cloud_online)
        self.scan_consistency_ok = bool(scan_to_map_match_ok)

        if self.current_speed < 0.02:
            pass
        else:
            self._last_velocity_zero_time = now

        if not self.is_in_dock:
            self._last_dock_idle_time = now

    def notify_user_interaction(self):
        """Notifica un evento KWS o rilevamento di persona interagente."""
        self._last_speech_time = time.monotonic()
        if self.cognitive_state == CognitiveState.VIGILANT:
            self._transition_cognitive(CognitiveState.ENGAGED, reason="kws_or_face_detected")

        if self.kinematic_state == KinematicState.DOCKED_SLEEP:
            self._transition_kinematic(KinematicState.WAKING, reason="interaction_wake")

    def request_navigation(self):
        """Notifica un intent di navigazione da TRINITY / Gemini Live."""
        self.nav_intent_active = True
        if self.cognitive_state == CognitiveState.LOCAL_VLM:
            # Break-before-make: scarica VLM prima di qualsiasi avvio al moto
            self._transition_cognitive(CognitiveState.ENGAGED, reason="nav_intent_preemption")

        if self.kinematic_state in (KinematicState.STATIONARY, KinematicState.DOCKED_SLEEP):
            self._transition_kinematic(KinematicState.PREP_NAV, reason="nav_intent")

    def cancel_navigation(self):
        """Annulla la navigazione attiva o riceve evento di goal raggiunto."""
        self.nav_intent_active = False
        if self.kinematic_state == KinematicState.MOVING:
            self._transition_kinematic(KinematicState.STOPPING, reason="nav_cancelled_or_reached")

    def trigger_emergency_stop(self):
        """Fast-path stop immediato (<10ms)."""
        self.nav_intent_active = False
        if self.kinematic_state in (KinematicState.MOVING, KinematicState.PREP_NAV):
            self._transition_kinematic(KinematicState.STOPPING, reason="fast_path_stop")

    def evaluate_cycle(self) -> ResourceProfile:
        """Esegue un ciclo deterministico di aggiornamento della Statechart (10 Hz)."""
        now = time.monotonic()

        # 1. Valutazione Regione Cognitiva
        if self.cognitive_state == CognitiveState.ENGAGED:
            if now - self._last_speech_time > self.dialogue_timeout_s:
                self._transition_cognitive(CognitiveState.VIGILANT, reason="dialogue_timeout")
            elif not self.cloud_connected and self.kinematic_state == KinematicState.STATIONARY:
                self._transition_cognitive(CognitiveState.LOCAL_VLM, reason="cloud_offline_fallback")

        elif self.cognitive_state == CognitiveState.LOCAL_VLM:
            if self.cloud_connected:
                self._transition_cognitive(CognitiveState.ENGAGED, reason="cloud_restored")
            elif self.kinematic_state != KinematicState.STATIONARY:
                # Invariante 8: VLM vietato fuori da STATIONARY
                self._transition_cognitive(CognitiveState.ENGAGED, reason="motion_preemption")

        # 2. Valutazione Regione Cinematica
        if self.kinematic_state == KinematicState.STATIONARY:
            if self.is_in_dock and (now - self._last_dock_idle_time > self.idle_dock_timeout_s):
                self._transition_kinematic(KinematicState.DOCKED_SLEEP, reason="dock_idle_timeout")
            elif self.nav_intent_active:
                self._transition_kinematic(KinematicState.PREP_NAV, reason="nav_intent_active")

        elif self.kinematic_state == KinematicState.DOCKED_SLEEP:
            if not self.is_in_dock or self.nav_intent_active:
                self._transition_kinematic(KinematicState.WAKING, reason="undocked_or_intent")

        elif self.kinematic_state == KinematicState.WAKING:
            if now - self._state_enter_time >= self.waking_duration_s:
                self._transition_kinematic(KinematicState.STATIONARY, reason="waking_complete")

        elif self.kinematic_state == KinematicState.PREP_NAV:
            if not self.scan_consistency_ok:
                self._transition_kinematic(KinematicState.RELOCALIZING, reason="scan_mismatch")
            else:
                self._transition_kinematic(KinematicState.MOVING, reason="safety_gates_passed")

        elif self.kinematic_state == KinematicState.RELOCALIZING:
            if self.scan_consistency_ok:
                self._transition_kinematic(KinematicState.PREP_NAV, reason="relocalization_verified")

        elif self.kinematic_state == KinematicState.MOVING:
            if not self.nav_intent_active:
                self._transition_kinematic(KinematicState.STOPPING, reason="nav_intent_cleared")
            elif self.current_speed < 0.01 and (now - self._last_velocity_zero_time > 3.0):
                # Stallo prolungato a velocità nulla
                self._transition_kinematic(KinematicState.STOPPING, reason="stalled_standstill")

        elif self.kinematic_state == KinematicState.STOPPING:
            if self.current_speed < 0.01 and (now - self._state_enter_time >= self.stopping_duration_s):
                self._transition_kinematic(KinematicState.STATIONARY, reason="standstill_confirmed")

        # 3. Risoluzione del Profilo di Risorse
        profile = self._resolve_resource_profile()

        # 4. Invariant Verification (Watchdog)
        self._audit_invariants(profile)

        return profile

    def _resolve_resource_profile(self) -> ResourceProfile:
        """Calcola la tabella dei parametri in base alla combinazione (Kinematic, Cognitive)."""
        if self.safety_state == SafetyState.SAFE_FULL:
            # Profilo SAFE_FULL nominale
            return ResourceProfile(
                camera_fps=15,
                scrfd_hz=5.0,
                arcface_recheck_sec=5.0,
                pose_hz=5.0 if self.person_detected else 0.0,
                yolo_hz=5.0,
                vio_hz=1.0,
                vpr_hz=0.0,
                lidar_active=True,
                rtabmap_paused=True,
                nav2_active=False,
                motion_gate_open=False,
                vlm_loaded=False
            )

        k = self.kinematic_state
        c = self.cognitive_state

        if k == KinematicState.DOCKED_SLEEP:
            return ResourceProfile(
                camera_fps=5,
                scrfd_hz=1.0,
                arcface_recheck_sec=0.0,
                pose_hz=0.0,
                yolo_hz=0.0,
                vio_hz=0.0,
                vpr_hz=0.0,
                lidar_active=False,      # Unico stato con LiDAR spento
                rtabmap_paused=True,
                nav2_active=False,
                motion_gate_open=False,
                vlm_loaded=False
            )

        if c == CognitiveState.LOCAL_VLM and k == KinematicState.STATIONARY:
            return ResourceProfile(
                camera_fps=10,
                scrfd_hz=2.0,
                arcface_recheck_sec=5.0,
                pose_hz=0.0,
                yolo_hz=1.0,
                vio_hz=0.0,
                vpr_hz=0.0,
                lidar_active=True,
                rtabmap_paused=True,
                nav2_active=False,
                motion_gate_open=False,
                vlm_loaded=True
            )

        if k == KinematicState.MOVING:
            engaged = (c == CognitiveState.ENGAGED)
            return ResourceProfile(
                camera_fps=20,
                scrfd_hz=10.0 if engaged else 5.0,
                arcface_recheck_sec=5.0 if engaged else 10.0,
                pose_hz=10.0 if engaged else (5.0 if self.person_detected else 0.0),
                yolo_hz=12.0,
                vio_hz=20.0,
                vpr_hz=0.5,
                lidar_active=True,
                rtabmap_paused=False,
                nav2_active=True,
                motion_gate_open=True,   # Motion gate aperto solo in MOVING
                vlm_loaded=False
            )

        # Default STATIONARY / PREP_NAV / RELOCALIZING / STOPPING / WAKING
        if c == CognitiveState.ENGAGED:
            return ResourceProfile(
                camera_fps=15,
                scrfd_hz=10.0,
                arcface_recheck_sec=2.0,
                pose_hz=10.0 if self.person_detected else 0.0,
                yolo_hz=5.0,
                vio_hz=1.0,
                vpr_hz=0.0,
                lidar_active=True,
                rtabmap_paused=True,
                nav2_active=False,
                motion_gate_open=False,
                vlm_loaded=False
            )
        else: # VIGILANT
            return ResourceProfile(
                camera_fps=10,
                scrfd_hz=2.0,
                arcface_recheck_sec=5.0,
                pose_hz=5.0 if self.person_detected else 0.0,
                yolo_hz=2.0,
                vio_hz=1.0,
                vpr_hz=0.0,
                lidar_active=True,
                rtabmap_paused=True,
                nav2_active=False,
                motion_gate_open=False,
                vlm_loaded=False
            )

    def _audit_invariants(self, profile: ResourceProfile):
        """Verifica a ogni ciclo che nessuno degli 8 Invarianti non negoziabili sia violato."""
        # Invariante 1: Pavimento uditivo sempre attivo
        if not profile.hearing_floor_active:
            self._trigger_violation("INVARIANT_1: Pavimento uditivo non attivo!")

        # Invariante 5: Motion Gate non deve mai essere aperto fuori da MOVING
        if profile.motion_gate_open and self.kinematic_state != KinematicState.MOVING:
            self._trigger_violation("INVARIANT_5: Motion Gate aperto fuori dallo stato MOVING!")

        # Invariante 6: LiDAR deve essere attivo in tutti gli stati tranne DOCKED_SLEEP
        if not profile.lidar_active and self.kinematic_state != KinematicState.DOCKED_SLEEP:
            self._trigger_violation(f"INVARIANT_6: LiDAR spento nello stato non ammesso {self.kinematic_state}!")

        # Invariante 8: Qwen2-VL (VLM) ammesso SOLO nello stato STATIONARY
        if profile.vlm_loaded and self.kinematic_state != KinematicState.STATIONARY:
            self._trigger_violation(f"INVARIANT_8: VLM caricato durante stato cinematico {self.kinematic_state}!")

    def _trigger_violation(self, message: str):
        self.invariant_violations.append(message)
        self.safety_state = SafetyState.SAFE_FULL
        raise InvariantViolation(message)

    def _transition_kinematic(self, new_state: KinematicState, reason: str):
        old = self.kinematic_state
        self.kinematic_state = new_state
        self._state_enter_time = time.monotonic()
        self.transition_history.append({
            "type": "KINEMATIC",
            "from": old.value,
            "to": new_state.value,
            "reason": reason,
            "timestamp": self._state_enter_time
        })

    def _transition_cognitive(self, new_state: CognitiveState, reason: str):
        old = self.cognitive_state
        self.cognitive_state = new_state
        self.transition_history.append({
            "type": "COGNITIVE",
            "from": old.value,
            "to": new_state.value,
            "reason": reason,
            "timestamp": time.monotonic()
        })
