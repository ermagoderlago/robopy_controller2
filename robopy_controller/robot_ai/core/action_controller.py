"""
ActionController: Punto di ingresso unico per l'esecuzione di azioni/skill.

Garantisce type safety, validazione parametri, gestione errori.
Sprint 0 Hardening.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, TYPE_CHECKING

from ..skills.base_skill import SkillErrorCode, SkillResult

if TYPE_CHECKING:
    from ..skills.skill_registry import SkillRegistry

logger = logging.getLogger(__name__)


@dataclass
class ActionRequest:
    """Richiesta tipizzata per l'esecuzione di un'azione."""
    skill_name: str
    parameters: Dict[str, Any]
    timeout_seconds: float = 30.0
    user_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


class ActionController:
    """
    Controller unificato per l'esecuzione delle azioni.

    Wrappa SkillRegistry con validazione, timeout e storico esecuzioni.
    Ogni azione passa per execute_action() — mai direttamente alle skill.
    """
    
    def __init__(self, skill_registry: Optional["SkillRegistry"] = None):
        """
        Inizializza il controller.
        
        Args:
            skill_registry: Registro skill da usare (default: singleton).
        """
        if skill_registry:
            self.registry = skill_registry
        else:
            # Import differito per evitare ciclo core <-> skills
            from ..skills.skill_registry import SkillRegistry
            self.registry = SkillRegistry()
            
        self.execution_history: list = []
    
    async def execute_action(self, request: ActionRequest) -> SkillResult:
        """
        Esegue un'azione (skill).
        
        Args:
            request: Richiesta tipizzata.
        
        Returns:
            SkillResult (sempre successo o fallimento, mai eccezioni).
        """
        start_time = time.time()
        result = None
        
        try:
            # Step 1: Valida la richiesta
            validation_error = self._validate_request(request)
            if validation_error:
                result = self._failure_with_timing(
                    validation_error,
                    SkillErrorCode.INVALID_PARAMETERS,
                    start_time,
                )
            
            # Step 2: Cerca la skill nel registro (se non già fallito)
            if not result:
                skill = self.registry.get(request.skill_name)
                if not skill:
                    logger.warning(f"Skill sconosciuta: {request.skill_name}")
                    available = [s.name for s in self.registry.get_all()]
                    result = self._failure_with_timing(
                        f"Skill '{request.skill_name}' non trovata. "
                        f"Disponibili: {', '.join(available)}",
                        SkillErrorCode.SKILL_NOT_FOUND,
                        start_time,
                    )
            
            # Step 3: Verifica abilitazione
            if not result and not skill.enabled:
                result = self._failure_with_timing(
                    f"Skill '{request.skill_name}' disabilitata",
                    SkillErrorCode.PERMISSION_DENIED,
                    start_time,
                )
            
            # Step 4-5: Esecuzione
            if not result:
                # Costruisci contesto
                text = request.parameters.get("text", request.skill_name)
                context = request.context or {}
                context.update(request.parameters)
                if request.user_id:
                    context["user_id"] = request.user_id
                
                logger.info(f"Esecuzione skill: {request.skill_name} con params: {request.parameters}")
                
                result = await self._execute_with_timeout(
                    skill, text, context, request.timeout_seconds
                )
            
            # Step 6: Valida contratto risultato
            if not isinstance(result, SkillResult):
                logger.error(f"Skill {request.skill_name} ha restituito tipo non valido: {type(result)}")
                result = SkillResult.failure_result(
                    f"Skill ha restituito tipo non valido: {type(result).__name__}",
                    SkillErrorCode.UNKNOWN_ERROR,
                    duration_ms=(time.time() - start_time) * 1000,
                )
            
            return result
        
        except Exception as e:
            # Gestione errori graceful
            logger.exception(f"Errore inatteso in execute_action: {e}")
            result = SkillResult.failure_result(
                f"Errore inatteso: {str(e)[:100]}",
                SkillErrorCode.UNKNOWN_ERROR,
                duration_ms=(time.time() - start_time) * 1000,
            )
            return result
            
        finally:
            if result:
                # Step 7: Registra nello storico (sempre)
                self.execution_history.append({
                    "skill": request.skill_name,
                    "success": result.success,
                    "duration_ms": result.duration_ms,
                    "timestamp": time.time(),
                })
                
                if result.success:
                    logger.info(
                        f"✅ Skill eseguita: {request.skill_name}, "
                        f"durata={result.duration_ms:.0f}ms"
                    )
    
    def _validate_request(self, request: ActionRequest) -> Optional[str]:
        """Valida la struttura della richiesta. Restituisce messaggio errore se invalida."""
        if not request.skill_name:
            return "skill_name è vuoto"
        if not isinstance(request.parameters, dict):
            return "parameters deve essere un dizionario"
        if request.timeout_seconds <= 0:
            return "timeout_seconds deve essere > 0"
        return None
    
    async def _execute_with_timeout(
        self,
        skill,
        text: str,
        context: Dict[str, Any],
        timeout_seconds: float,
    ) -> SkillResult:
        """Esegue la skill con protezione timeout (asyncio)."""
        try:
            result = await asyncio.wait_for(
                skill.safe_execute(text, context),
                timeout=timeout_seconds
            )
            return result
        except asyncio.TimeoutError:
            logger.error(f"Skill timeout: esecuzione superata {timeout_seconds}s")
            return SkillResult.failure_result(
                f"Esecuzione scaduta dopo {timeout_seconds}s",
                SkillErrorCode.EXECUTION_TIMEOUT,
                duration_ms=timeout_seconds * 1000,
            )
    
    def _failure_with_timing(
        self,
        message: str,
        error_code: SkillErrorCode,
        start_time: float,
    ) -> SkillResult:
        """Crea risultato di fallimento con timing."""
        return SkillResult.failure_result(
            message,
            error_code,
            duration_ms=(time.time() - start_time) * 1000,
        )
    
    def get_available_skills(self) -> list:
        """Restituisce lista nomi skill disponibili."""
        return [s.name for s in self.registry.get_all()]
    
    def get_execution_history(self, limit: int = 100) -> list:
        """Restituisce le ultime N esecuzioni."""
        return self.execution_history[-limit:]
