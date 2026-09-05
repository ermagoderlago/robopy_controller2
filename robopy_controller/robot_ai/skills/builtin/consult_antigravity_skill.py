"""
Robot AI Skills - Consult Antigravity Skill
===========================================
Skill che permette a Marcus di dialogare on-demand con l'Agente Antigravity residente su Raspberry Pi 5
per richiedere consulenze architetturali, analisi di codice o spiegazioni tecniche approfondite.
"""

import logging
from typing import Any, Dict, Optional
from ..base_skill import BaseSkill, SkillMetadata, SkillResult, Capability
from ...services.antigravity_agent_service import AntigravityAgentService

logger = logging.getLogger("robot_ai.skills.consult_antigravity")


class ConsultAntigravitySkill(BaseSkill):
    """
    Skill per interagire direttamente con l'Agente Antigravity (Gemini 3.8 con thinking avanzato).
    """

    def __init__(self, antigravity_service: Optional[AntigravityAgentService] = None):
        super().__init__()
        self.antigravity_service = antigravity_service or AntigravityAgentService()

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="consult_antigravity_skill",
            description="Dialoga con l'Agente Antigravity (Gemini 3.8) per consulenze su codice, architettura, e problemi complessi.",
            version="1.0.0",
            keywords=[
                "antigravity", "chiedi ad antigravity", "consulta antigravity",
                "parere antigravity", "cosa pensa antigravity", "aiuto codice"
            ],
            priority=8,
            capabilities=[Capability.HA_READ]
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        text_lower = text.lower()
        patterns = [
            "chiedi ad antigravity", "consulta antigravity", "cosa pensa antigravity",
            "parla con antigravity", "parere di antigravity", "fatti consigliare da antigravity",
            "chiedi all'agente antigravity"
        ]
        if any(p in text_lower for p in patterns):
            return 0.95
        if "antigravity" in text_lower and any(w in text_lower for w in ["pensa", "consiglio", "parere", "spiega", "codice"]):
            return 0.85
        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        context = context or {}
        question = context.get("question") or text
        code_or_context = context.get("code_or_context") or context.get("context", "")

        # Pulisci trigger iniziali da text se question è il raw input
        for prefix in ["chiedi ad antigravity", "consulta antigravity", "cosa pensa antigravity di", "chiedi ad antigravity:"]:
            if question.lower().startswith(prefix):
                question = question[len(prefix):].strip()

        logger.info(f"ConsultAntigravitySkill: invio quesito ad Antigravity: '{question[:100]}'...")

        try:
            advice = await self.antigravity_service.consult_antigravity_dialogue(
                question=question,
                context=code_or_context
            )

            # Estrai frase naturale per il parlato vocale
            first_sentence = advice.split(".")[0] if "." in advice else advice[:120]
            speak_msg = f"Ho parlato con Antigravity. {first_sentence.strip()}."

            return SkillResult(
                success=True,
                message=advice,
                speak=speak_msg,
                data={"question": question, "antigravity_response": advice}
            )

        except Exception as e:
            logger.error(f"Errore ConsultAntigravitySkill: {e}", exc_info=True)
            return SkillResult(
                success=False,
                message=f"Errore durante il dialogo con Antigravity: {str(e)}",
                speak="Ho provato a consultare Antigravity, ma si è verificato un errore di elaborazione."
            )

    def to_function_declaration(self) -> Dict[str, Any]:
        return {
            "name": "consult_antigravity",
            "description": (
                "Chiede una consulenza specialistica all'Agente Antigravity (Gemini 3.8 con extended thinking). "
                "Usa questo tool quando hai bisogno di un parere tecnico approfondito, revisione di codice ROS 2, "
                "o comprensione di un'anomalia di sistema."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "La domanda o problema tecnico da sottoporre ad Antigravity"
                    },
                    "code_or_context": {
                        "type": "string",
                        "description": "Frammento di codice, errore o contesto specifico da analizzare (opzionale)"
                    }
                },
                "required": ["question"]
            }
        }
