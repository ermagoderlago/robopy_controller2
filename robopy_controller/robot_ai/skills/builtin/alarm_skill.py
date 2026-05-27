"""
Robot AI Skills - Alarm Skill
==============================
Skill for managing alarms and wake-up calls.
"""

import re
import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional

from robopy_controller.robot_ai.skills.base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode

logger = logging.getLogger("robot_ai.skills.alarm_skill")

class AlarmSkill(BaseSkill):
    """
    Skill for setting absolute alarms.
    
    Supports:
    - "Svegliami alle 7:00"
    - "Imposta una sveglia per le sette e mezza"
    - "Metti la sveglia alle 8 di domani"
    """
    
    # Pattern per estrarre l'orario (es: 7:00, 7.30, 7 e mezza, 19:15)
    TIME_PATTERN = re.compile(r'(\d{1,2})[:\.]?(\d{2})?\s*(am|pm)?', re.IGNORECASE)
    WORD_TIME_PATTERN = re.compile(r'\b(una|due|tre|quattro|cinque|sei|sette|otto|nove|dieci|undici|dodici)\b', re.IGNORECASE)
    
    # Pattern per il matching della skill
    MATCH_PATTERNS = [
        re.compile(r'\bsveglia\b', re.IGNORECASE),
        re.compile(r'\bsvegliami\b', re.IGNORECASE),
        re.compile(r'\balarm\b', re.IGNORECASE),
    ]

    def __init__(self, scheduler=None):
        super().__init__()
        self.scheduler = scheduler  # Will be injected from AIOrchestrator
        self._active_alarms = {}

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="alarm",
            description="Imposta una sveglia ad un orario specifico.",
            version="1.0.0",
            keywords=["sveglia", "alarm", "svegliami", "ora", "appuntamento"],
            priority=9
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        """Determina se l'input riguarda una sveglia."""
        text_lower = text.lower()
        
        if any(p.search(text_lower) for p in self.MATCH_PATTERNS):
            # Se c'è anche un'indicazione di orario, score alto
            if self.TIME_PATTERN.search(text_lower) or self.WORD_TIME_PATTERN.search(text_lower):
                return 0.95
            return 0.7
        
        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> Any:
        """Esegue l'impostazione della sveglia."""
        if not self.scheduler:
             yield SkillResult.failure_result(
                 "Il sistema di pianificazione non è attivo.",
                 SkillErrorCode.SYSTEM_ERROR,
                 speak="Scusa, il mio sistema di pianificazione non è attivo, non posso impostare la sveglia."
             )
             return

        target_time = self._extract_time(text)
        
        if not target_time:
            yield SkillResult.failure_result(
                "Non ho capito a che ora devo impostare la sveglia.",
                SkillErrorCode.INVALID_PARAMETERS,
                speak="Scusa, non ho capito a che ora vuoi la sveglia. Puoi dirmelo chiaramente? Ad esempio: 'svegliami alle sette e trenta'."
            )
            return

        # Calcola quando deve suonare
        now = datetime.now() # Questo ora rifletterà il timezone del sistema (Europe/Rome dopo il fix)
        # Se l'ora è già passata oggi, impostala per domani
        alarm_dt = datetime.combine(now.date(), target_time)
        if alarm_dt <= now:
            alarm_dt += timedelta(days=1)

        job_id = f"alarm_{alarm_dt.strftime('%Y%m%d_%H%M%S')}"
        
        # Schedula il job
        try:
            self.scheduler.add_job(
                self._trigger_alarm, 
                'date', 
                run_date=alarm_dt,
                args=[alarm_dt.strftime("%H:%M")],
                id=job_id
            )
            self._active_alarms[job_id] = alarm_dt
            
            time_str = alarm_dt.strftime("%H:%M")
            day_str = "domani" if alarm_dt.date() > now.date() else "oggi"
            
            yield SkillResult.success_result(
                message=f"Sveglia impostata per {day_str} alle {time_str}.",
                speak=f"Va bene, sveglia impostata per {day_str} alle {time_str}. Ti sveglierò io!"
            )
        except Exception as e:
            logger.error(f"Errore scheduling sveglia: {e}")
            yield SkillResult.failure_result(
                f"Errore interno: {e}",
                SkillErrorCode.SYSTEM_ERROR,
                speak="C'è stato un errore nel salvare la sveglia. Riprova tra un attimo."
            )

    async def _trigger_alarm(self, time_str: str):
        """Metodo chiamato quando la sveglia suona."""
        logger.info(f"SVEGLIA! Sono le {time_str}")
        # Qui potremmo inviare un evento al bus o parlare direttamente
        # Per ora usiamo il TTS se possibile (tramite evento)
        from robot_ai.core.event_bus import EventBus, EventType
        EventBus().publish(EventType.SKILL_COMPLETED, {
            "name": "alarm",
            "message": f"SVEGLIA! Sono le {time_str}",
            "speak": f"Buongiorno! Sono le {time_str}, è ora di svegliarsi!"
        })

    def _extract_time(self, text: str) -> Optional[time]:
        """Estrae l'oggetto time dal testo."""
        text_lower = text.lower()
        
        # Mapping parole -> numeri
        words_to_num = {
            "una": 1, "un'ora": 1, "due": 2, "tre": 3, "quattro": 4, 
            "cinque": 5, "sei": 6, "sette": 7, "otto": 8, "nove": 9, 
            "dieci": 10, "undici": 11, "dodici": 12, "mezza": 30, "un quarto": 15
        }
        
        # Prova pattern numerico (7:00, 19.30)
        match = self.TIME_PATTERN.search(text_lower)
        if match:
            h = int(match.group(1))
            m = int(match.group(2)) if match.group(2) else 0
            am_pm = match.group(3)
            
            if am_pm and am_pm.lower() == "pm" and h < 12:
                h += 12
            elif am_pm and am_pm.lower() == "am" and h == 12:
                h = 0
            
            if 0 <= h < 24 and 0 <= m < 60:
                return time(h, m)

        # Prova parole (sette e mezza)
        for word, val in words_to_num.items():
            if word in text_lower:
                # Logica semplificata: cerca la prima parola che indica l'ora
                h_match = self.WORD_TIME_PATTERN.search(text_lower)
                if h_match:
                    h = words_to_num[h_match.group(1).lower()]
                    m = 0
                    if "e mezza" in text_lower or "e trenta" in text_lower:
                        m = 30
                    elif "e un quarto" in text_lower:
                        m = 15
                    elif "e quarantacinque" in text_lower or "meno un quarto" in text_lower:
                        if "meno" in text_lower:
                             h -= 1
                             m = 45
                        else:
                             m = 45
                    
                    # Se dice "di sera" o "pomeriggio"
                    if any(x in text_lower for x in ["sera", "pomeriggio", "notte"]):
                        if h < 12: h += 12
                        
                    return time(h, m)

        return None

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "time_string": {
                    "type": "string",
                    "description": "Orario della sveglia (es. 07:00)"
                }
            },
            "required": ["time_string"]
        }
