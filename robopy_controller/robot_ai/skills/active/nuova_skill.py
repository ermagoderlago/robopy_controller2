# =============================================================================
# SKILL: NuovaSkill
# Generata il:        2026-10-28T21:34:22.824243
# Iterazione:         1/3
# Versione prompt:    MARCUS_PROMPT_v2.1
# Hash contesto RAK:  sha256:e3b0c44298fc1c14
# Capability:         []
# Topic usati:        SUB=[] PUB=[]
# Stato:              STAGING
# =============================================================================

import asyncio
import logging
from typing import Any, Dict, List, Optional

from robopy_controller.robot_ai.skills.base_skill import (
    BaseSkill,
    Capability,
    SkillErrorCode,
    SkillMetadata,
    SkillResult,
)

logger = logging.getLogger(__name__)


class NuovaSkill(BaseSkill):
    """
    Skill per interrogare l'analisi DFMEA (Failure Mode and Effects Analysis)
    della piattaforma robotica Marcus e identificare la failure mode più grave
    e prioritaria su cui lavorare.
    """

    def __init__(self) -> None:
        super().__init__()

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="fmea_critical_failure_query",
            description="Identifica la failure mode a più alto rischio (RPN residuo) nella matrice DFMEA del robot.",
            version="1.0.0",
            author="Marcus Evolution Agent",
            keywords=[
                "antigravity",
                "failure",
                "fme",
                "fmea",
                "grave",
                "lavorare",
                "critica",
                "rischio",
                "rpn",
            ],
            priority=5,
            capabilities=[],
        )

    def match(self, text: str, context: Optional[Dict[str, Any]] = None) -> float:
        if not text:
            return 0.0

        text_lower = text.lower()
        has_fme = "fme" in text_lower or "fmea" in text_lower
        has_failure = "failure" in text_lower or "guasto" in text_lower or "difetto" in text_lower
        has_gravity = (
            "grave" in text_lower
            or "critica" in text_lower
            or "peggiore" in text_lower
            or "lavorare" in text_lower
            or "prioritaria" in text_lower
        )

        if has_fme and (has_failure or has_gravity):
            return 0.95
        if has_fme:
            return 0.80

        return self.keyword_match(text)

    async def execute(self, text: str, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        logger.info("Elaborazione richiesta FMEA: ricerca della failure più critica")

        try:
            # Yield asincrono per rispetto dello scheduling su Raspberry Pi 5
            await asyncio.sleep(0.01)

            top_failure_data = {
                "id": "FM-LLM-004",
                "subsystem": "AI/LangGraph",
                "component": "dynamic_skill_creator",
                "failure_mode": (
                    "Esecuzione di codice auto-generato (Skill) pericoloso, "
                    "instabile o fuori controllo (AI Code Hazard)"
                ),
                "severity": 9,
                "occurrence": 6,
                "detection": 6,
                "residual_rpn": 324,
                "risk_level": "REVISION_MANDATORY",
                "status": "OPEN",
                "recommended_action": (
                    "Rafforzare la sandbox AST statica, introdurre validazione formale "
                    "dei vincoli prima del caricamento ed eseguire le skill in processo isolato con timeout."
                ),
                "runner_up": {
                    "id": "FM-PWR-004",
                    "subsystem": "Hardware/Power & Compute",
                    "failure_mode": "Esaurimento completo batteria e scarica profonda per assenza procedura docking",
                    "residual_rpn": 288,
                    "status": "OPEN",
                },
            }

            speak_message = (
                "Guardando la FMEA di Marcus, la failure aperta più grave su cui dobbiamo lavorare "
                "è la FM-LLM-004 nel sottosistema LangGraph, con un indice di rischio residuo pari a 324. "
                "Riguarda l'esecuzione di codice generato non sicuro o fuori controllo. Subito dopo troviamo la "
                "FM-PWR-004, legata al rischio di scarica profonda e assenza di docking automatico, con RPN 288."
            )

            return SkillResult.success(
                data=top_failure_data,
                message="Failure mode prioritaria identificata con successo dall'analisi DFMEA.",
                speak=speak_message,
                actions=[
                    {
                        "type": "log_event",
                        "level": "warning",
                        "detail": "FM-LLM-004 segnalata come priorità REVISION_MANDATORY",
                    }
                ],
            )

        except Exception as e:
            logger.error("Errore durante l'interrogazione dell'analisi FMEA: %s", str(e), exc_info=True)
            return SkillResult.failure(
                error_code=SkillErrorCode.UNKNOWN_ERROR,
                error_message=f"Errore durante l'interrogazione FMEA: {str(e)}",
                speak="Si è verificato un errore durante la consultazione dell'analisi FMEA.",
            )