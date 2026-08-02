"""
Robot AI Skills - Enroll Speaker Skill
======================================
Skill per registrare una nuova identità vocale sull'NPU Hailo.
"""

import asyncio
import logging
import subprocess
from typing import Any, Dict

from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability

logger = logging.getLogger("robot_ai.skills.enroll_speaker_skill")

class EnrollSpeakerSkill(BaseSkill):
    """
    Skill per registrare il timbro vocale di un utente.
    """
    
    def __init__(self):
        super().__init__()

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="enroll_speaker",
            description="Avvia la procedura di registrazione vocale (enrollment) per associare la voce a un nome utente specifico.",
            version="1.0.0",
            keywords=["impara", "voce", "timbro", "registrami"],
            priority=8,
            capabilities=[]
        )

    async def execute(self, text: str, context: Dict[str, Any] = None) -> Any:
        """
        Esegue la skill ricevendo i parametri da Gemini.
        Essendo chiamata via Tool Call, `context['args']` contiene i parametri.
        """
        args = context.get('args', {}) if context else {}
        name = args.get("name")
        
        if not name:
            yield SkillResult.failure_result(
                "Non ho capito come ti chiami.",
                SkillErrorCode.INVALID_PARAMETERS,
                speak="Per imparare la tua voce ho bisogno di sapere come ti chiami. Puoi ripetere il tuo nome?"
            )
            return

        name = name.lower().strip()
        
        # Publish ROS 2 message via subprocess to avoid ROS node complexity in isolated skill thread
        try:
            cmd = [
                'ros2', 'topic', 'pub', '--once', 
                '/speaker/trigger_enrollment', 
                'std_msgs/msg/String', 
                f'{{data: "{name}"}}'
            ]
            subprocess.Popen(cmd)
            
            yield SkillResult.success_result(
                message=f"Enrollment avviato per {name}.",
                speak=f"Perfetto {name}, ho avviato la registrazione della tua voce. Da ora in poi cercherò di riconoscerti quando mi parli."
            )
        except Exception as e:
            logger.error(f"Errore trigger enrollment: {e}")
            yield SkillResult.failure_result(
                f"Errore di sistema: {e}",
                SkillErrorCode.SYSTEM_ERROR,
                speak="Scusa, ho avuto un problema tecnico nell'attivare la registrazione della tua voce."
            )

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Il nome della persona che sta parlando e di cui si vuole registrare la voce."
                }
            },
            "required": ["name"]
        }
