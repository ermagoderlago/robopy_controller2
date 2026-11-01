#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Robot AI Skills - Visual Pointing Skill ("Guarda Lì")
=====================================================
Interprets user 3D pointing gestures, aligns the robot camera/chassis to the
pointed target, crops the visual ROI, and queries Qwen2-VL or Gemini to describe
the indicated object or scene.
"""

import math
import time
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ...utils.logging_utils import get_logger
from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode

logger = get_logger("VisualPointingSkill")


class VisualPointingSkill(BaseSkill):
    """
    Skill per l'interpretazione del gesto di puntamento 3D ("Guarda lì").
    """

    PATTERNS = [
        re.compile(r'\b(guarda\s+l[iì]|guarda\s+l[aà]|cosa\s+c\'[eè]\s+l[iì]|cosa\s+sto\s+indicando|vedi\s+l[iì]|look\s+there)\b', re.IGNORECASE),
        re.compile(r'\b(dimmi\s+cos\'[eè]\s+questo|cos\'[eè]\s+quello|guarda\s+dove\s+indico)\b', re.IGNORECASE)
    ]

    def __init__(self, ros_bridge: Optional[Any] = None, vlm_service: Optional[Any] = None):
        super().__init__()
        self.ros_bridge = ros_bridge
        self.vlm_service = vlm_service
        self.last_pointing_target: Optional[Dict[str, Any]] = None
        self.last_pointing_time: float = 0.0

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="visual_pointing_skill",
            description="Interpreta il vettore di puntamento 3D del braccio dell'utente e descrive l'oggetto indicato.",
            version="1.0.0",
            author="Marcus Team",
            keywords=["guarda lì", "cosa c'è lì", "cosa sto indicando", "vedi lì", "look there", "guarda la"],
            priority=8,
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

    def update_pointing_target(self, target_data: Dict[str, Any]):
        """Aggiorna le coordinate del bersaglio 3D ricevuto da /vision/pointing_vector."""
        self.last_pointing_target = target_data
        self.last_pointing_time = time.time()

    async def execute(self, text_or_params: Any, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        if isinstance(text_or_params, dict):
            params = text_or_params
        else:
            params = dict(context or {})
            params["text"] = str(text_or_params or "")

        now = time.time()
        # Verifica se un puntamento recente è disponibile (< 5.0 secondi)
        if not self.last_pointing_target or (now - self.last_pointing_time > 5.0):
            return SkillResult.failure(
                error_code=SkillErrorCode.INVALID_STATE,
                error_message="Non ho visto dove stai indicando. Puoi indicare di nuovo con il braccio disteso?"
            )

        target_3d = self.last_pointing_target.get("target_3d", [0.0, 0.0, 1.5])
        tx, ty, tz = target_3d[0], target_3d[1], target_3d[2]
        hand = self.last_pointing_target.get("hand", "destro")
        hand_it = "destro" if hand == "RIGHT" else "sinistro"

        logger.info(f"👉 Esecuzione VisualPointing verso coordinate 3D: [{tx:.2f}, {ty:.2f}, {tz:.2f}] (Braccio {hand_it})")

        # 1. Calcolo angolo di rotazione relativo dello chassis per allinearsi al bersaglio
        yaw_angle = math.atan2(tx, tz)
        if abs(yaw_angle) > 0.15:
            if self.ros_bridge and hasattr(self.ros_bridge, "rotate_relative"):
                self.ros_bridge.rotate_relative(yaw_angle)

        # 2. Query VLM o descrizione della direzione
        description = f"Sto guardando dove hai indicato con il braccio {hand_it}."
        if self.vlm_service and hasattr(self.vlm_service, "query_target_region"):
            try:
                vlm_resp = await self.vlm_service.query_target_region(target_3d=target_3d)
                if vlm_resp:
                    description = vlm_resp
            except Exception as e:
                logger.error(f"Errore query VLM su puntamento: {e}")

        return SkillResult.success(
            data={"target_3d": target_3d, "yaw_angle_rad": yaw_angle, "hand": hand},
            message=description
        )
