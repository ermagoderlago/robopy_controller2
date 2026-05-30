"""
Robot AI Skills - Timer Skill
==============================
Skill per la gestione di timer e promemoria temporali.
"""

import re
import asyncio
import logging
from typing import Any, Dict, List, Optional

from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability

logger = logging.getLogger("robot_ai.skills.timer_skill")

class TimerSkill(BaseSkill):
    """
    Skill per impostare timer.
    
    Supporta:
    - "Imposta un timer di 5 minuti"
    - "Avvisami tra 10 secondi"
    - "Metti un timer di un'ora"
    """
    
    # Pattern per estrarre il tempo
    TIME_PATTERNS = [
        re.compile(r'(\d+)\s*(minut[io]|min)', re.IGNORECASE),
        re.compile(r'(\d+)\s*(second[io]|sec)', re.IGNORECASE),
        re.compile(r'(\d+)\s*(or[ae]|h)', re.IGNORECASE),
        re.compile(r'\bun\b\'?\s*(ora|minuto|secondo)', re.IGNORECASE),
    ]
    
    # Pattern per il matching della skill
    MATCH_PATTERNS = [
        re.compile(r'\btimer\b', re.IGNORECASE),
        re.compile(r'\bavvisami\b', re.IGNORECASE),
        re.compile(r'\bsveglia\b', re.IGNORECASE),
        re.compile(r'\bconta\b', re.IGNORECASE),
    ]

    def __init__(self):
        super().__init__()
        self._active_timers = []

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="timer",
            description="Imposta un timer con avviso sonoro al termine.",
            version="1.0.0",
            keywords=["timer", "sveglia", "avviso", "tempo"],
            priority=8,
            capabilities=[Capability.AUDIO_PLAY]
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        """Determina se l'input riguarda un timer."""
        text_lower = text.lower()
        
        # Se c'è la parola "timer", score alto
        if any(p.search(text_lower) for p in self.MATCH_PATTERNS):
            # Verifica se c'è anche un'indicazione temporale
            if any(p.search(text_lower) for p in self.TIME_PATTERNS):
                return 0.9
            return 0.6
        
        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> Any:
        """
        Esegue il timer. 
        Essendo una skill che richiede attesa, usiamo un generatore async 
        per dare feedback immediato e poi finale.
        """
        seconds = self._extract_seconds(text)
        
        if seconds <= 0:
            yield SkillResult.failure_result(
                "Non ho capito per quanto tempo devo impostare il timer.",
                SkillErrorCode.INVALID_PARAMETERS,
                speak="Scusa, non ho capito la durata del timer. Puoi ripetere?"
            )
            return

        time_desc = self._format_duration(seconds)
        
        # Feedback iniziale
        yield SkillResult.success_result(
            message=f"Timer di {time_desc} avviato.",
            speak=f"Va bene, imposto un timer di {time_desc}. Ti avviserò allo scadere."
        )
        
        # Attesa
        try:
            await asyncio.sleep(seconds)
            
            # Feedback finale
            yield SkillResult.success_result(
                message=f"Timer di {time_desc} completato!",
                speak=f"Din don! Il tempo per il timer di {time_desc} è scaduto!",
                actions=[{"type": "led_alert", "color": "orange", "duration": 3}]
            )
        except asyncio.CancelledError:
            logger.info("Timer cancellato")
            yield SkillResult.failure_result("Timer annullato")

    def _extract_seconds(self, text: str) -> int:
        """Estrae il tempo totale in secondi dal testo."""
        total_seconds = 0
        text_lower = text.lower()
        
        # Gestione numeri testuali semplici
        text_lower = text_lower.replace("un minuto", "1 minuto")
        text_lower = text_lower.replace("un'ora", "1 ora")
        text_lower = text_lower.replace("un secondo", "1 secondo")
        
        # Estrazione ore
        h_match = re.search(r'(\d+)\s*(or[ae]|h)', text_lower)
        if h_match:
            total_seconds += int(h_match.group(1)) * 3600
            
        # Estrazione minuti
        m_match = re.search(r'(\d+)\s*(minut[io]|min)', text_lower)
        if m_match:
            total_seconds += int(m_match.group(1)) * 60
            
        # Estrazione secondi
        s_match = re.search(r'(\d+)\s*(second[io]|sec)', text_lower)
        if s_match:
            total_seconds += int(s_match.group(1))
            
        return total_seconds

    def _format_duration(self, seconds: int) -> str:
        """Formatta la durata in modo leggibile."""
        if seconds < 60:
            return f"{seconds} secondi"
        
        minutes = seconds // 60
        rem_seconds = seconds % 60
        
        if minutes < 60:
            res = f"{minutes} minut{'o' if minutes == 1 else 'i'}"
            if rem_seconds > 0:
                res += f" e {rem_seconds} secondi"
            return res
            
        hours = minutes // 60
        rem_minutes = minutes % 60
        res = f"{hours} or{'a' if hours == 1 else 'e'}"
        if rem_minutes > 0:
            res += f" e {rem_minutes} minuti"
        return res

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "duration_seconds": {
                    "type": "integer",
                    "description": "Durata del timer in secondi"
                }
            },
            "required": ["duration_seconds"]
        }
