"""
Robot AI Skills - Skill Generator Wrapper (Meta-Skill)
=====================================================
Skill che permette a Marcus di generare nuove skill usando la pipeline skill_generator.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from robot_ai.skills.base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability
from ..skill_generator import SkillGeneratorPipeline, SkillRequest

logger = logging.getLogger("robot_ai.skills.crea_skill")

class CreaSkill(BaseSkill):
    """
    Skill per la generazione di nuove capacità per il robot.
    
    Permette a Marcus di:
    1. Ricevere una richiesta di una nuova funzionalità.
    2. Usare la pipeline interna per generare il codice Python.
    3. Validare la skill tramite sandbox e Quality Gate.
    4. Proporre la skill per l'approvazione.
    """

    def __init__(self, llm_service):
        super().__init__()
        self.llm_service = llm_service
        self.pipeline = SkillGeneratorPipeline()
        
    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="crea_skill",
            description="Genera una nuova skill ROS 2 per aggiungere capacità al robot.",
            version="1.0.0",
            keywords=["crea skill", "genera abilità", "nuova funzione", "impara a"],
            priority=10, # Alta priorità per meta-comandi
            capabilities=[Capability.HA_READ] # Esempio di capability minima richiesta
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        """Individua richieste di creazione nuove skill."""
        text_lower = text.lower()
        patterns = [
            "crea una skill",
            "genera una skill",
            "impara a",
            "crea una nuova abilità",
            "costruisci una skill"
        ]
        
        if any(p in text_lower for p in patterns):
            return 0.9
        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        """
        Esegue il processo di generazione della skill.
        """
        # 1. Estrazione parametri base (Marcus LLM dovrebbe averli già estratti via Tool Calling if enabled,
        # ma se viene chiamato via match() dobbiamo estrarli noi o chiedere all'utente).
        
        # Se siamo in Tool Calling, Marcus passerà i parametri. 
        # Se siamo in match(), dobbiamo dedurre nome e descrizione.
        
        # Nota: In Marcus AI, le skill builtin sono spesso usate come Tool nel LLMService.
        # Se Marcus parla con Gemini Live API, userà il tool definition.
        
        yield SkillResult(True, "Inizio generazione...", "Certamente. Sto analizzando la tua richiesta per creare una nuova capacità.")
        
        try:
            # Funzione helper che Marcus (l'LLM) userà per generare il codice nel prompt della pipeline
            async def code_provider(prompt: str) -> str:
                # Chiamata a Gemini per generare il codice puro (testo tra <SKILL_CODE> tags)
                response = await self.llm_service.generate(prompt)
                return response.text

            # Tentativo di estrazione nome e descrizione semplificato (fellback)
            name_suggestion = "NuovaSkill"
            description_suggestion = text
            
            # TODO: In una implementazione più avanzata, potremmo fare un passaggio LLM 
            # preventivo per definire bene la SkillRequest.
            
            request = SkillRequest(
                name=name_suggestion,
                description=description_suggestion,
                test_utterances=[text]
            )

            # Esecuzione pipeline
            result = await self.pipeline.run_full_pipeline(request, code_provider)

            if result.success:
                msg = f"Skill '{result.skill_name}' generata con successo e messa in staging. Vuoi che la approvi?"
                yield SkillResult(True, "Generazione completata", msg)
            else:
                msg = f"Non sono riuscito a generare la skill: {result.failure_report}"
                yield SkillResult(False, "Fallimento generazione", msg)

        except Exception as e:
            logger.error(f"Errore durante CreaSkill: {e}", exc_info=True)
            yield SkillResult(False, "Errore interno", f"Si è verificato un errore durante la creazione: {str(e)}")

    def to_function_declaration(self) -> Dict[str, Any]:
        """Dichiarazione per il Tool Calling del LLM."""
        return {
            "name": "crea_skill",
            "description": "Genera una nuova skill (capacità) per il robot Marcus. Usa questo tool quando l'utente ti chiede di imparare a fare qualcosa di nuovo o di creare una nuova funzionalità.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nome della skill in PascalCase (es. AccendiLuceSkill)"
                    },
                    "description": {
                        "type": "string",
                        "description": "Descrizione dettagliata di cosa deve fare la skill"
                    },
                    "capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Elenco delle capability richieste (es. ha.write, nav.move)"
                    }
                },
                "required": ["name", "description"]
            }
        }
