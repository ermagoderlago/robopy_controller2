#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Situational Awareness Skill.
============================
Module: robopy_controller.robot_ai.skills.builtin.situational_awareness_skill
Architecture:
  - SituationalAwarenessSkill: Builtin Skill inheriting from BaseSkill (priority 95)
  - Provides sub-50ms deterministic local fast-path execution for situational queries
    ("Dove ti trovi?", "In quale mappa navighi?", "Cosa vedi?")
  - Integrates and coordinates DestructiveConfirmationGate for destructive voice commands
  - Synchronizes with TRINITY CAG (EnvironmentSnapshot) under <2200-2500 token budget

Conforms to:
  - TC7 / R4: Voice situational queries and destructive confirmation gates
  - SPEC-05: TRINITY triadic context fusion and fast-path skill execution
  - marcus_core_rules.md: Zero BOM UTF-8
"""

import time
from typing import Any, Dict, List, Optional

from ...skills.base_skill import BaseSkill, SkillMetadata, SkillResult, Capability
from ...vui.vui_dialogue_engine import VUIDialogueEngine
from ...core.destructive_confirmation_gate import DestructiveConfirmationGate


class SituationalAwarenessSkill(BaseSkill):
    """
    Builtin Skill for robot situational awareness questions and destructive command gating.
    Intercepts:
      - "Dove ti trovi?" / "Dove sei?"
      - "In quale mappa stai navigando?" / "In che mappa navighi?"
      - "Cosa vedi?" / "Cosa c'è davanti?"
      - "Mappa nuova stanza <room>", "Aggiorna mappa <room>", "Sovrascrivi mappa <room>", "Cancella mappa <room>"
      - Pending destructive gate confirmations: "sì, confermo" / "non confermo"
    """

    def __init__(
        self,
        dialogue_engine: Optional[VUIDialogueEngine] = None,
        destructive_gate: Optional[DestructiveConfirmationGate] = None,
        cag_environment: Optional[Any] = None,
    ) -> None:
        super().__init__()
        self.cag_environment = cag_environment
        self.dialogue_engine = dialogue_engine or VUIDialogueEngine(cag_environment=cag_environment)
        self.destructive_gate = destructive_gate or DestructiveConfirmationGate(cag_environment=cag_environment)

    def get_metadata(self) -> SkillMetadata:
        """Returns metadata configuration for the skill."""
        return SkillMetadata(
            name="situational_awareness",
            description=(
                "Fornisce risposte vocali deterministiche e veloci sullo stato situazionale "
                "del robot (posizione, mappa, AMCL, visione) e gestisce i gate di sicurezza "
                "per comandi distruttivi di mappatura."
            ),
            version="1.0.0",
            author="Marcus Team",
            keywords=[
                "dove ti trovi", "dove sei", "in che stanza", "in quale stanza",
                "in quale mappa", "in che mappa", "quale mappa", "che mappa",
                "cosa vedi", "cosa stai vedendo", "cosa c'è davanti", "cosa c e davanti",
                "mappa nuova stanza", "aggiorna mappa", "sovrascrivi mappa",
                "cancella mappa", "elimina mappa", "confermo", "annulla"
            ],
            priority=95,  # High priority for sub-50ms local fast-path interception
            enabled=True,
            requires_internet=False,
            requires_ha=False,
            requires_nav=False,
            requires_vision=False,
            capabilities=[Capability.AUDIO_PLAY],
        )

    def match(self, text: str, context: Optional[Dict[str, Any]] = None) -> float:
        """
        Calculates match score.
        Returns 1.0 if destructive gate is pending (to intercept user confirmation).
        Returns 0.98 for new destructive commands.
        Returns 1.0 for situational awareness queries.
        Returns 0.0 otherwise.
        """
        if not text or not text.strip():
            return 0.0

        # 1. If a destructive confirmation gate is pending, intercept with highest confidence
        if self.destructive_gate.pending:
            return 1.0

        # 2. Check for destructive command trigger
        if self.destructive_gate.parse_spoken_command(text) is not None:
            return 0.98

        # 3. Check for situational awareness queries
        cleaned = self.dialogue_engine._normalize_text(text)
        if (
            self.dialogue_engine.RE_LOCATION.search(cleaned)
            or self.dialogue_engine.RE_MAP.search(cleaned)
            or self.dialogue_engine.RE_VISION.search(cleaned)
        ):
            return 1.0

        return 0.0

    async def execute(self, text: str, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        """
        Executes the skill deterministically.
        """
        t0 = time.time()
        self._stats["invocations"] += 1

        # Case 1: Destructive Gate is pending -> evaluate user response
        if self.destructive_gate.pending:
            reply, confirmed = self.destructive_gate.process_voice_input(text)
            duration_ms = (time.time() - t0) * 1000.0
            self._stats["successes"] += 1
            self._stats["total_duration_ms"] += duration_ms

            return SkillResult.success_result(
                message="Risposta gate distruttivo elaborata",
                data={
                    "confirmed": confirmed,
                    "pending": self.destructive_gate.pending,
                    "target_room": self.destructive_gate.target_room,
                    "status": self.destructive_gate.get_status_dict(),
                },
                speak=reply,
                duration_ms=duration_ms,
            )

        # Case 2: New Destructive Command Trigger
        cmd = self.destructive_gate.parse_spoken_command(text)
        if cmd is not None:
            cmd_type, room = cmd
            prompt = self.destructive_gate.request_action(room, cmd_type)
            duration_ms = (time.time() - t0) * 1000.0
            self._stats["successes"] += 1
            self._stats["total_duration_ms"] += duration_ms

            return SkillResult.success_result(
                message=f"Gate distruttivo attivato per '{room}'",
                data={
                    "command_type": cmd_type,
                    "target_room": room,
                    "status": self.destructive_gate.get_status_dict(),
                },
                speak=prompt,
                duration_ms=duration_ms,
            )

        # Case 3: Situational Awareness Query
        reply = self.dialogue_engine.handle_query(text)
        duration_ms = (time.time() - t0) * 1000.0
        self._stats["successes"] += 1
        self._stats["total_duration_ms"] += duration_ms

        return SkillResult.success_result(
            message="Query situazionale elaborata con successo",
            data={
                "query": text,
                "reply": reply,
                "cag_snapshot": dict(self.dialogue_engine.cag_snapshot),
            },
            speak=reply,
            duration_ms=duration_ms,
        )
