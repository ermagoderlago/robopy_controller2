"""
Robot AI Skills - Nightly Dream Skill
=====================================
Skill to manually trigger nightly analysis.
"""

from typing import Any, Dict

from ..base_skill import BaseSkill, SkillMetadata, SkillResult
from ...services.nightly_dream_service import NightlyDreamService

class NightlyDreamSkill(BaseSkill):
    """
    Skill for triggering nightly dream analysis.
    """
    
    def __init__(self, dream_service: NightlyDreamService):
        super().__init__()
        self.dream_service = dream_service
    
    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="nightly_dream",
            description="Manually triggers nightly memory analysis.",
            version="1.0.0",
            keywords=["analisi", "notturna", "sogno", "dream", "giornata"],
            priority=5
        )
    
    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        text_lower = text.lower()
        if "avvia analisi notturna" in text_lower:
            return 0.95
        if "analizza giornata" in text_lower or "analizza la giornata" in text_lower:
            return 0.95
        if "sogna ora" in text_lower:
            return 0.95
        if "start nightly dream" in text_lower:
            return 0.95
            
        return 0.0
    
    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        # Yield start message
        yield SkillResult.success_result(
            message="Sto iniziando l'analisi notturna...",
            speak="Certamente. Inizio ad analizzare le memorie della giornata. Richiederà qualche secondo."
        )
        
        # Run analysis
        result = await self.dream_service.run_analysis()
        
        status = result.get("status")
        if status == "success":
            count = result.get("memories_analyzed", 0)
            yield SkillResult.success_result(
                message=f"Analisi completata. Analizzate {count} memorie.",
                speak=f"Ho finito l'analisi. Ho esaminato {count} conversazioni e ho aggiornato il mio diario di miglioramento."
            )
        elif status == "skipped":
            reason = result.get("reason")
            yield SkillResult.success_result(
                message=f"Analisi saltata: {reason}",
                speak="Ho controllato, ma non ho abbastanza nuove memorie da analizzare per le ultime 24 ore."
            )
        else:
            reason = result.get("reason", "unknown")
            yield SkillResult.failure_result(
                message=f"Analisi fallita: {reason}",
                speak="Purtroppo c'è stato un problema durante l'analisi. Controlla i log per i dettagli."
            )
