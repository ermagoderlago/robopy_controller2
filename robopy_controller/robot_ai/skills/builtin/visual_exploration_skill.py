"""
Robot AI Skills - Visual Exploration Skill
==========================================
Esplorazione visiva guidata dall'LLM.
"""

import re
import asyncio
import json
from typing import Any, Dict, Callable, Optional

from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode
from ...core.image_handler import Image

class VisualExplorationSkill(BaseSkill):
    """
    Skill for semantic visual exploration using Gemini Cloud.
    """
    
    EXPLORE_PATTERNS = [
        re.compile(r'\b(esplora\s+con\s+la\s+vista|guarda\s+in\s+giro|mappa\s+guardando|esplorazione\s+visiva)\b', re.IGNORECASE)
    ]
    
    def __init__(self, nav_client=None, llm_service=None, camera_provider=None, move_handler=None):
        super().__init__()
        self.nav_client = nav_client
        self.llm_service = llm_service
        self.camera_provider = camera_provider
        self.move_handler = move_handler
    
    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="visual_exploration",
            description="Esplora la stanza analizzando le immagini con Gemini per decidere dove muoversi in sicurezza",
            version="1.0.0",
            keywords=["esplora", "vista", "guarda", "mappa"],
            priority=10, # Higher priority than generic explore from NavigationSkill
            requires_nav=True
        )
        
    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        score = 0.0
        if any(p.search(text) for p in self.EXPLORE_PATTERNS):
            score = 0.95
        return score
        
    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        if not self.llm_service or not self.camera_provider or not self.move_handler:
            return SkillResult.failure_result("Servizi mancanti per esplorazione visiva", SkillErrorCode.EXTERNAL_SERVICE_ERROR)
            
        frame_bytes = self.camera_provider()
        if not frame_bytes:
            return SkillResult.failure_result(
                "Nessuna immagine dalla videocamera", 
                SkillErrorCode.TOOL_EXECUTION_FAILED, 
                speak="Non riesco a vedere nulla, assicurati che la videocamera sia accesa!"
            )
            
        return await self._run_exploration_loop(frame_bytes)
        
    async def _run_exploration_loop(self, initial_frame: bytes) -> SkillResult:
        # Esegue un singolo step di esplorazione visiva
        prompt = (
            "Sei il robot MARCUS. Analizza questa immagine della stanza. "
            "Dove posso muovermi in sicurezza per mappare nuove zone ed evitare ostacoli? "
            "Rispondi SOLO in formato JSON valido con due chiavi: 'distanza_metri' (float, massimo 1.0 metri) e 'angolo_gradi' (float, da -90.0 a 90.0). "
            "NON AGGIUNGERE TESTO, SOLO JSON. MANTIENI LA RISPOSTA PULITA. "
            "ESEMPIO: {\"distanza_metri\": 0.5, \"angolo_gradi\": 30.0}"
        )
        
        # Decodifica da base64 a bytes se necessario
        if isinstance(initial_frame, str):
            import base64
            initial_frame = base64.b64decode(initial_frame)
            
        image_obj = Image.from_compressed(initial_frame)
        
        try:
            response = await self.llm_service.generate(prompt=prompt, images=[image_obj])
            
            # Extract JSON block
            json_str = response.text
            match = re.search(r'```json\s*(.*?)\s*```', json_str, re.DOTALL)
            if match:
                json_str = match.group(1)
            else:
                match = re.search(r'\{.*\}', json_str, re.DOTALL)
                if match:
                    json_str = match.group(0)
            
            try:
                data = json.loads(json_str)
            except Exception:
                return SkillResult.failure_result(
                    "Il cloud non ha risposto con coordinate valide.",
                    SkillErrorCode.EXTERNAL_SERVICE_ERROR,
                    speak="Ho analizzato l'immagine ma non ho capito bene dove muovermi."
                )
            
            dist = float(data.get("distanza_metri", 0.1))
            ang = float(data.get("angolo_gradi", 0.0))
            
            # Constrain values strictly for indoor space safety
            dist = max(-0.5, min(1.0, dist))
            ang = max(-90.0, min(90.0, ang))
            
            msg = f"Ottima vista! Mi muovo di {dist:.2f} metri e ruoto di {ang:.1f} gradi."
            
            # Execute movement relative
            if ang != 0.0:
                direction = "sinistra" if ang > 0 else "destra"
                self.move_handler(direction, 0.3, 1.0, abs(ang))
                await asyncio.sleep(1.5)
                
            if dist != 0.0:
                direction = "avanti" if dist > 0 else "indietro"
                duration = abs(dist) / 0.15
                self.move_handler(direction, 0.15, duration, None)
                
            return SkillResult(
                success=True, 
                message=f"Esplorazione parziale eseguita (dist: {dist}, ang: {ang})", 
                speak=msg
            )
            
        except Exception as e:
            return SkillResult.failure_result(
                f"Errore esplorazione visiva: {e}", 
                SkillErrorCode.EXTERNAL_SERVICE_ERROR, 
                speak="Ho avuto un problema tecnico durante l'analisi visiva."
            )
