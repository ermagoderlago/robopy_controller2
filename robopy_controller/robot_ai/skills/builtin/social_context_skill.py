#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Robot AI Skills - Social Context & Theory of Mind Skill (IMP-BIO-004)
====================================================================
Manages Theory of Mind awareness, workbench Do-Not-Disturb modes,
and 100% autonomous local Fall Emergency response.
"""

import time
import json
import re
from typing import Any, Dict, List, Optional

from ...utils.logging_utils import get_logger
from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode

logger = get_logger("SocialContextSkill")


class SocialContextSkill(BaseSkill):
    """
    Skill per la gestione della Teoria della Mente, Do-Not-Disturb e risposta locale alle cadute.
    """

    PATTERNS = [
        re.compile(r'\b(non disturbarmi|sono occupato|modalit[aà] lavoro|do not disturb|silenzioso|lasciami lavorare)\b', re.IGNORECASE),
        re.compile(r'\b(puoi parlare|torna normale|ho finito di lavorare|disattiva non disturbare)\b', re.IGNORECASE),
        re.compile(r'\b(come sto|stato contesto|cosa sto facendo|come mi vedi)\b', re.IGNORECASE)
    ]

    def __init__(self, ros_bridge: Optional[Any] = None):
        super().__init__()
        self.ros_bridge = ros_bridge
        self.manual_dnd: bool = False
        self.current_tom_state: str = "ENGAGED"
        self.last_fall_alert_time: float = 0.0

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="social_context_skill",
            description="Gestisce il contesto sociale, la modalità Non Disturbare e la sicurezza anticaduta locale.",
            version="1.0.0",
            author="Marcus Team",
            keywords=["non disturbarmi", "sono occupato", "modalità lavoro", "do not disturb", "puoi parlare", "come sto"],
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

    def update_tom_state(self, state_str: str):
        """Aggiorna lo stato dedotto da TheoryOfMindEngine."""
        self.current_tom_state = state_str

    async def execute(self, text_or_params: Any, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        if isinstance(text_or_params, dict):
            params = text_or_params
            text = params.get("text", "")
        else:
            text = str(text_or_params or "")
            params = dict(context or {})
            params["text"] = text
        
        # 1. Attivazione Non Disturbare
        if re.search(r'\b(non disturbarmi|sono occupato|modalit[aà] lavoro|do not disturb|silenzioso)\b', text, re.IGNORECASE):
            self.manual_dnd = True
            if self.ros_bridge and hasattr(self.ros_bridge, "set_tts_volume"):
                self.ros_bridge.set_tts_volume(0.30)
            return SkillResult.success(
                data={"dnd": True, "source": "manual"},
                message="Ricevuto. Ho attivato la modalità Non Disturbare. Non ti interromperò e parlerò a volume basso."
            )

        # 2. Disattivazione Non Disturbare
        if re.search(r'\b(puoi parlare|torna normale|ho finito|disattiva non disturbare)\b', text, re.IGNORECASE):
            self.manual_dnd = False
            if self.ros_bridge and hasattr(self.ros_bridge, "set_tts_volume"):
                self.ros_bridge.set_tts_volume(0.85)
            return SkillResult.success(
                data={"dnd": False, "source": "manual"},
                message="Modalità Non Disturbare disattivata. Sono di nuovo a piena disposizione per interagire!"
            )

        # 3. Query stato / Teoria della Mente
        effective_state = "DO_NOT_DISTURB" if self.manual_dnd else self.current_tom_state
        state_descriptions = {
            "DO_NOT_DISTURB": "Ti vedo concentrato al banco di lavoro o alla scrivania. Ho ridotto le notifiche per non deconcentrarti.",
            "ENGAGED": "Ti vedo pronto e disponibile ad interagire con me.",
            "RESTING": "Ti vedo rilassato o sdraiato in riposo.",
            "FALL_EMERGENCY": "Ho rilevato un allarme di possibile caduta a terra!"
        }
        desc = state_descriptions.get(effective_state, f"Stato corrente: {effective_state}")
        return SkillResult.success(
            data={"tom_state": effective_state, "manual_dnd": self.manual_dnd},
            message=desc
        )

    def handle_fall_emergency(self, person_name: Optional[str] = None) -> Dict[str, Any]:
        """
        [IMP-BIO-004] Procedura di emergenza anticaduta locale al 100% autonoma.
        (Nessuna dipendenza esterna da Home Assistant).
        """
        now = time.time()
        self.last_fall_alert_time = now
        name_str = f" {person_name}" if person_name else ""

        emergency_prompt = (
            f"Attenzione! Ho rilevato una possibile caduta a terra{name_str}. "
            f"Mi senti? Se stai bene dimmi chiaramente 'tutto bene'!"
        )

        logger.error(f"🚨 [FALL_EMERGENCY] Avviata procedura locale autonoma: '{emergency_prompt}'")

        # 1. Arresto immediato motricità ruote
        if self.ros_bridge and hasattr(self.ros_bridge, "publish_cmd_vel"):
            self.ros_bridge.publish_cmd_vel(0.0, 0.0)

        # 2. Flash LED Rosso di allarme
        if self.ros_bridge and hasattr(self.ros_bridge, "set_led"):
            self.ros_bridge.set_led("EMERGENCY_RED")

        # 3. Emissione vocale immediata ad alto volume
        if self.ros_bridge and hasattr(self.ros_bridge, "speak"):
            self.ros_bridge.speak(emergency_prompt, volume=1.0)

        return {
            "status": "emergency_triggered",
            "prompt": emergency_prompt,
            "timestamp": now
        }
