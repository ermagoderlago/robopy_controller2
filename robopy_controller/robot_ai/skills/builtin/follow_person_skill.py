#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Robot AI Skills - Intelligent Person Following Skill (IMP-BIO-002)
==================================================================
Fluid human-following combining:
1. Biometric Target Locking via ArcFace: Locks onto specific identity, preventing
   distraction/target-switching if another person crosses the camera field of view.
2. 3D Kinematic Pose Anticipation: Reads shoulder/torso torsion from Pose Estimation keypoints.
   When user begins turning towards Marcus, anticipates stop/slowdown before linear displacement occurs.
3. Prosodic Distance Regulation: Maintains optimal personal zone (1.2m nominal, min 0.8m, max 2.5m)
   adjusting velocity dynamically on /cmd_vel or Nav2 speed limits.
"""

import time
import math
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ...utils.logging_utils import get_logger
from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode

logger = get_logger("FollowPersonSkill")


class FollowPersonSkill(BaseSkill):
    """
    Skill di inseguimento biometrico intelligente con anticipazione cinematica della torsione.
    """

    PATTERNS = [
        re.compile(r'\b(seguimi|vieni con me|stai con me|segui|inseguimi|follow me)\b', re.IGNORECASE),
        re.compile(r'\b(smetti di seguire|fermati qui|non seguirmi pi[uù]|basta seguire)\b', re.IGNORECASE)
    ]

    def __init__(self, ros_bridge: Optional[Any] = None):
        super().__init__()
        self.ros_bridge = ros_bridge

        # Tracking state
        self.is_following: bool = False
        self.locked_identity: Optional[str] = None
        self.last_target_seen_time: float = 0.0
        self.last_target_distance_m: float = 1.5
        self.target_lost_timeout_s: float = 4.0

        # Kinematic anticipation
        self.last_shoulder_width_norm: float = 0.20
        self.last_torsion_ratio: float = 1.0

        # Prosodic tuning
        self.nominal_distance_m: float = 1.20
        self.min_distance_m: float = 0.80
        self.max_distance_m: float = 2.50
        self.max_linear_speed: float = 0.35  # Strict Red Zone limit <= 0.40 m/s
        self.max_angular_speed: float = 1.20 # Strict Red Zone limit <= 1.80 rad/s

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="follow_person_skill",
            description="Insegue fluidamente l'utente con blocco biometrico ArcFace e anticipazione torsione del busto.",
            version="1.0.0",
            author="Marcus Team",
            keywords=["seguimi", "vieni con me", "stai con me", "segui", "inseguimi", "follow me"],
            priority=7,
            requires_nav=True,
            requires_vision=True
        )

    @property
    def metadata(self) -> SkillMetadata:
        return self.get_metadata()

    def match(self, text: str, context: Optional[Dict[str, Any]] = None) -> float:
        if self.can_handle(text):
            return 0.95
        return 0.0

    def can_handle(self, text: str) -> bool:
        return any(p.search(text) for p in self.PATTERNS)

    async def execute(self, text_or_params: Any, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        if isinstance(text_or_params, dict):
            params = text_or_params
            text = params.get("text", "")
        else:
            text = str(text_or_params or "")
            params = dict(context or {})
            params["text"] = text

        stop_match = re.search(r'\b(smetti|fermati|basta|non seguirmi)\b', text, re.IGNORECASE)
        if stop_match:
            self.stop_following()
            return SkillResult.success(
                data={"following": False},
                message="Ho interrotto l'inseguimento. Rimango qui."
            )

        # Target biometric lock
        target_name = params.get("target_identity") or params.get("user_name")
        res = self.start_following(target_identity=target_name)
        if res:
            name_str = f" {target_name}" if target_name else ""
            return SkillResult.success(
                data={"following": True, "locked_identity": self.locked_identity},
                message=f"D'accordo, ti seguo{name_str}! Mantengo il passo con te."
            )
        else:
            return SkillResult.failure(
                error_code=SkillErrorCode.RESOURCE_BUSY,
                error_message="Impossibile avviare il following in questo momento."
            )

    def start_following(self, target_identity: Optional[str] = None) -> bool:
        self.is_following = True
        self.locked_identity = target_identity
        self.last_target_seen_time = time.time()
        logger.info(f"🚀 Iniziato FollowPerson con biometric lock su '{self.locked_identity}'")
        return True

    def stop_following(self):
        self.is_following = False
        self.locked_identity = None
        self._publish_cmd_vel(0.0, 0.0)
        logger.info("🛑 FollowPerson terminato.")

    def update_frame(
        self,
        keypoints: List[Tuple[float, float, float]],
        detected_identity: Optional[str] = None,
        estimated_distance_m: float = 1.5
    ) -> Dict[str, Any]:
        """
        Loop ad alta frequenza (10 Hz) invocato con i keypoint 3D della persona tracciata.
        Calcola l'anticipazione cinematica della torsione e pubblica /cmd_vel.
        """
        now = time.time()
        if not self.is_following:
            return {"status": "inactive"}

        # 1. Verifica identità biometrica (ArcFace Target Locking)
        if self.locked_identity and detected_identity:
            if detected_identity.lower() != "unknown" and detected_identity.lower() != self.locked_identity.lower():
                # Altra persona ha tagliato la traiettoria: ignora e attendi il target bloccato
                logger.info(f"Target locked '{self.locked_identity}' != rilevato '{detected_identity}'. Ignorato.")
                return {"status": "ignored_other_person"}

        if len(keypoints) < 17:
            # Target temporaneamente occluso
            if now - self.last_target_seen_time > self.target_lost_timeout_s:
                self._publish_cmd_vel(0.0, 0.0)
                return {"status": "target_lost", "target_lost": True}
            return {"status": "tracking_coasting"}

        self.last_target_seen_time = now
        self.last_target_distance_m = estimated_distance_m

        # 2. Anticipazione cinematica della torsione del busto
        # Keypoints: LEFT_SHOULDER = 5, RIGHT_SHOULDER = 6
        ls = keypoints[5]
        rs = keypoints[6]
        mid_x = (ls[0] + rs[0]) * 0.5  # Centro orizzontale della persona in immagine [0.0, 1.0]

        shoulder_width = abs(ls[0] - rs[0])
        # Torsione: quando la persona cammina di spalle la larghezza è estesa;
        # se comincia a girarsi verso il robot, il profilo delle spalle collassa transitoriamente
        torsion_ratio = shoulder_width / max(0.01, self.last_shoulder_width_norm)
        self.last_shoulder_width_norm = max(0.05, 0.9 * self.last_shoulder_width_norm + 0.1 * shoulder_width)

        # Se torsion_ratio < 0.70, la persona sta ruotando il corpo -> decelerazione preventiva
        torsion_decel_factor = 1.0
        if torsion_ratio < 0.75:
            torsion_decel_factor = max(0.2, torsion_ratio)

        # 3. Regolazione prossemica della velocità lineare
        distance_error = estimated_distance_m - self.nominal_distance_m
        if distance_error > 0.15:
            # Utente si allontana: accelera proporzionalmente
            v_cmd = min(self.max_linear_speed, distance_error * 0.6) * torsion_decel_factor
        elif distance_error < -0.15:
            # Troppo vicino: frena o arretra delicatamente
            v_cmd = max(-0.10, distance_error * 0.4)
        else:
            # Zona prossemica ottimale (1.2m +/- 0.15m)
            v_cmd = 0.0

        # 4. Inseguimento angolare (centratura persona nel campo visivo)
        angular_error = 0.5 - mid_x  # Errore rispetto al centro (0.5)
        w_cmd = max(-self.max_angular_speed, min(self.max_angular_speed, angular_error * 2.2))

        # Pubblica comandi di velocità
        self._publish_cmd_vel(v_cmd, w_cmd)

        return {
            "status": "following",
            "v_cmd": round(v_cmd, 3),
            "w_cmd": round(w_cmd, 3),
            "distance_m": round(estimated_distance_m, 2),
            "torsion_factor": round(torsion_decel_factor, 2)
        }

    def _publish_cmd_vel(self, linear_x: float, angular_z: float):
        if self.ros_bridge and hasattr(self.ros_bridge, "publish_cmd_vel"):
            self.ros_bridge.publish_cmd_vel(linear_x, angular_z)
