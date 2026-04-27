# =============================================================================
# SKILL: WebSearchSkill
# Generata il:        2026-03-30T21:38:28.299219
# Iterazione:         1/3
# Versione prompt:    MARCUS_PROMPT_v2.1
# Hash contesto RAK:  sha256:00b65734f12f5e4e
# Capability:         [<Capability.WEB_SEARCH: 'web.search'>]
# Topic usati:        SUB=[] PUB=[]
# Stato:              STAGING
# =============================================================================

from robopy_controller.robot_ai.skills.base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability
from typing import Any, Dict, List, Optional
import asyncio
import logging

logger = logging.getLogger(__name__)

class WebSearchSkill(BaseSkill):
    """Skill per la ricerca di informazioni sul web via OpenCrawl e Qwen 3.6."""

    def __init__(self, crawl_service=None, qwen_service=None):
        super().__init__()
        self.crawl_service = crawl_service
        self.qwen_service = qwen_service

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="web_search_skill",
            description="Cerca news, prezzi e informazioni sul web.",
            version="2.0.0",
            priority=5,
            capabilities=[Capability.WEB_SEARCH],
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        text_lower = text.lower()
        patterns = ["cerca", "prezzo", "quanto costa", "notizie", "mondo", "chi è", "cosa è", "dimmi di", "google"]
        if any(p in text_lower for p in patterns):
            return 0.85
        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        logger.info(f"Avvio ricerca web per: {text}")
        
        if not self.crawl_service or not self.qwen_service:
            yield SkillResult(
                success=False,
                speak="I servizi di ricerca web non sono configurati correttamente.",
                error_code=SkillErrorCode.DEPENDENCY_MISSING
            )
            return

        # 1. Estrarre la query di ricerca pulita
        query = self._clean_query(text)
        
        yield SkillResult(True, f"Cerco '{query}' sul web...", f"Sto cercando '{query}' sul web...")

        # 2. Eseguire il crawl/scrape
        search_results = await self.crawl_service.crawl(query)
        
        if not search_results:
            yield SkillResult(
                success=False,
                speak=f"Uhm, non ho trovato risultati pertinenti per {query}.",
                error_code=SkillErrorCode.EXECUTION_FAILED
            )
            return

        yield SkillResult(True, "Analizzo i risultati con Qwen...", "Analizzo i risultati con Qwen 3.6...")

        # 3. Analizzare i risultati con Qwen
        answer = await self.qwen_service.analyze_search_results(query, search_results)
        
        if not answer:
            answer = "Ho trovato dei risultati ma non sono riuscito a riassumerli correttamente."

        yield SkillResult(
            success=True,
            speak=answer,
            error_code=SkillErrorCode.SUCCESS
        )

    def _clean_query(self, text: str) -> str:
        """Rimuove trigger words per isolare la query."""
        clean = text.lower()
        for word in ["cerca", "prezzo", "notizie", "chi è", "cosa è", "dimmi di", "su google", "su internet"]:
            clean = clean.replace(word, "")
        return clean.strip()