"""
Robot AI Skills - Search Skill
==============================
Skill per la ricerca di oggetti nell'ambiente.
"""

import re
import asyncio
from typing import Any, Dict, List, Optional, Callable

from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode

class SearchSkill(BaseSkill):
    """
    Skill for finding objects.
    
    Handles commands like:
    - "Cerca le scarpe"
    - "Trova il mio telefono"
    - "Dove sono le chiavi?"
    """
    
    SEARCH_PATTERNS = [
        re.compile(r'\b(cerca|trova|scova|individua)\b', re.IGNORECASE),
        re.compile(r'\b(dove|dov\'è|dove sono)\b', re.IGNORECASE),
    ]
    
    def __init__(self, nav_client, llm_service, camera_provider: Callable[[], Optional[bytes]]):
        super().__init__()
        self.nav_client = nav_client
        self.llm_service = llm_service
        self.camera_provider = camera_provider
        self._is_searching = False
        
    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="search",
            description="Search for objects in known locations",
            version="1.0.0",
            keywords=["cerca", "trova", "dove"],
            priority=9, # Higher than simple nav
            requires_nav=True,
            requires_vision=True
        )
        
    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        """Match search commands."""
        # Check explicit keywords
        # Exclude common chat phrases
        text_lower = text.lower()
        if "dove abiti" in text_lower or \
           "dove sei" in text_lower or \
           "dove vivi" in text_lower or \
           "sai dove siamo" in text_lower:
            return 0.0

        if any(p.search(text) for p in self.SEARCH_PATTERNS):
            return 0.8
        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        """Execute search."""
        target = self._extract_target(text)
        if not target:
            yield SkillResult(False, "Cosa devo cercare?", "Cosa devo cercare?")
            return
            
        if not self.nav_client:
            yield SkillResult(False, "Navigazione non disponibile", "Non posso muovermi.")
            return
            
        locations = self.nav_client.get_all_waypoints()
        if not locations:
            yield SkillResult(False, "Non conosco nessuna stanza", "Non conosco stanze.")
            return
            
        self._is_searching = True
        
        # Announce start
        yield SkillResult(True, f"Cerco {target}...", f"Inizio a cercare {target}. Controllero in {len(locations)} stanze.")
        
        for loc in locations:
            if not self._is_searching:
                break
                
            # Go to location
            yield SkillResult(True, f"Vado in {loc.name}...", f"Vado in {loc.name}.")
            
            try:
                success = await self.nav_client.navigate_to_pose(loc.x, loc.y, loc.theta, loc.frame_id)
                if not success:
                    continue
            except Exception:
                continue
                
            # Look
            yield SkillResult(True, f"Guardo in {loc.name}...", f"Sono in {loc.name}. Guardo...")
            await asyncio.sleep(1.0) # Wait for camera to settle
            
            img = self.camera_provider()
            if img:
                # Ask Vision
                prompt = f"Vedi {target} in questa immagine? Rispondi solo SÌ o NO. Sii molto sicuro."
                # We need to construct a specific vision query
                # This depends on how llm_service is exposed. 
                # Assuming simple generate call.
                try:
                    response = await self.llm_service.generate(prompt, images=[img], max_tokens=10)
                    answer = response.text.strip().lower()
                    
                    if "sì" in answer or "si" in answer or "yes" in answer:
                        self._is_searching = False
                        yield SkillResult(True, f"Trovato {target} in {loc.name}!", f"Ho trovato {target} in {loc.name}!")
                        return
                    else:
                        yield SkillResult(True, f"Non c'è in {loc.name}", f"Qui non c'è.")
                except Exception as e:
                    pass
            else:
                yield SkillResult(True, "Camera non disponibile", "Non vedo nulla.")
                
        self._is_searching = False
        yield SkillResult(False, f"Non ho trovato {target}", f"Non ho trovato {target} da nessuna parte.")
        return

    def _extract_target(self, text: str) -> Optional[str]:
        # Simple extraction logic (heuristic)
        # Remove keywords
        clean = text
        for p in self.SEARCH_PATTERNS:
            clean = p.sub('', clean)
        
        clean = clean.strip()
        # Remove common articles
        for art in ["il ", "la ", "lo ", "i ", "gli ", "le ", "un ", "una ", "uno "]:
            if clean.startswith(art):
                clean = clean[len(art):]
        
        return clean if clean else None
        
    def cancel(self):
        self._is_searching = False
        if self.nav_client:
            asyncio.create_task(self.nav_client.cancel_navigation())
