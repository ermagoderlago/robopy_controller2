"""
Robot AI Skills - Base Skill
==============================
Classe base astratta per tutte le skill.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
import time


class SkillErrorCode(Enum):
    """Codici errore standard per le skill (Sprint 0 hardening)."""

    SUCCESS = 'SUCCESS'
    SKILL_NOT_FOUND = 'SKILL_NOT_FOUND'
    INVALID_PARAMETERS = 'INVALID_PARAMETERS'
    EXECUTION_TIMEOUT = 'EXECUTION_TIMEOUT'
    EXTERNAL_SERVICE_ERROR = 'EXTERNAL_SERVICE_ERROR'
    PERMISSION_DENIED = 'PERMISSION_DENIED'
    UNKNOWN_ERROR = 'UNKNOWN_ERROR'


@dataclass
class SkillResult:
    """
    Risultato dell'esecuzione di una skill.
    
    Campi:
        success: Se la skill è stata eseguita con successo.
        message: Messaggio leggibile (breve).
        data: Dati di output specifici della skill.
        actions: Lista di azioni da eseguire.
        speak: Testo da pronunciare all'utente.
        error_code: Codice errore standard (se fallimento).
        duration_ms: Tempo di esecuzione in millisecondi.
    """
    success: bool
    message: str = ''
    data: Dict[str, Any] = field(default_factory=dict)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    speak: Optional[str] = None
    error_code: Optional[SkillErrorCode] = None
    duration_ms: float = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Converte in dizionario serializzabile JSON."""
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data,
            "actions": self.actions,
            "speak": self.speak,
            "error_code": self.error_code.value if self.error_code else None,
            "duration_ms": self.duration_ms,
        }
    
    @staticmethod
    def success_result(
        message: str,
        data: Optional[Dict[str, Any]] = None,
        actions: Optional[List[Dict[str, Any]]] = None,
        speak: Optional[str] = None,
        duration_ms: float = 0.0,
    ) -> "SkillResult":
        """Factory: crea risultato di successo."""
        return SkillResult(
            success=True,
            message=message,
            data=data or {},
            actions=actions or [],
            speak=speak,
            error_code=None,
            duration_ms=duration_ms,
        )
    
    @staticmethod
    def failure_result(
        message: str,
        error_code: SkillErrorCode = SkillErrorCode.UNKNOWN_ERROR,
        speak: Optional[str] = None,
        duration_ms: float = 0.0,
    ) -> "SkillResult":
        """Factory: crea risultato di fallimento."""
        return SkillResult(
            success=False,
            message=message,
            data={},
            actions=[],
            speak=speak or f"Errore: {message}",
            error_code=error_code,
            duration_ms=duration_ms,
        )


@dataclass
class SkillMetadata:
    """Metadata for a skill."""
    name: str
    description: str
    version: str = '1.0.0'
    author: str = ''
    keywords: List[str] = field(default_factory=list)
    priority: int = 0
    enabled: bool = True
    requires_internet: bool = False
    requires_ha: bool = False
    requires_nav: bool = False
    requires_vision: bool = False


class BaseSkill(ABC):
    """
    Abstract base class for all skills.
    
    A skill is a self-contained capability that the robot can execute.
    Examples: controlling lights, setting timers, navigation commands.
    
    Usage:
        class LightSkill(BaseSkill):
            def get_metadata(self) -> SkillMetadata:
                return SkillMetadata(
                    name="lights",
                    description="Control lights",
                    keywords=["luce", "luci", "lampada"]
                )
            
            def match(self, text: str, context: Dict) -> float:
                # Return confidence score 0-1
                ...
            
            async def execute(self, text: str, context: Dict) -> SkillResult:
                # Execute the skill
                ...
    """
    
    def __init__(self):
        """Initialize stats and execution tracking."""
        self._stats = {
            'invocations': 0,
            'successes': 0,
            'failures': 0,
            'total_duration_ms': 0
        }
        self._last_execution: Optional[float] = None
    
    @abstractmethod
    def get_metadata(self) -> SkillMetadata:
        """Return skill metadata."""
        pass
    
    @abstractmethod
    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        """
        Calculate match score for input text.
        
        Args:
            text: User input text
            context: Additional context (location, time, etc.)
            
        Returns:
            Match confidence score 0-1. Return 0 if skill doesn't match.
        """
        pass
    
    @abstractmethod
    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        """
        Execute the skill.
        
        Args:
            text: User input text
            context: Additional context
            
        Returns:
            SkillResult with execution outcome
        """
        pass
    
    @property
    def name(self) -> str:
        """Get skill name."""
        return self.get_metadata().name
    
    @property
    def description(self) -> str:
        """Get skill description."""
        return self.get_metadata().description
    
    @property
    def keywords(self) -> List[str]:
        """Get skill keywords."""
        return self.get_metadata().keywords
    
    @property
    def enabled(self) -> bool:
        """Check if skill is enabled."""
        return self.get_metadata().enabled
    
    @property
    def priority(self) -> int:
        """Get skill priority (higher = matched first)."""
        return self.get_metadata().priority
    
    def keyword_match(self, text: str) -> float:
        """
        Calculate match score based on keywords.
        
        Args:
            text: Input text
            
        Returns:
            Score 0-1 based on keyword presence
        """
        text_lower = text.lower()
        keywords = self.keywords
        
        if not keywords:
            return 0.0
        
        matches = sum(1 for kw in keywords if kw.lower() in text_lower)
        return min(1.0, matches / len(keywords) * 2)  # 50% keywords = 1.0
    
    async def safe_execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        """
        Execute skill with error handling and statistics.
        
        Args:
            text: User input text
            context: Additional context
            
        Returns:
            SkillResult
        """
        start_time = time.perf_counter()
        self._stats["invocations"] += 1
        
        try:
            # Execute skill
            res = self.execute(text, context or {})
            
            # Helper to check for async generator without imports if possible, or use hasattr
            if hasattr(res, '__aiter__'):  # AsyncGenerator
                return res
            
            # Coroutine -> await it
            result = await res
            
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            result.duration_ms = duration_ms
            self._last_execution = time.time()
            self._stats["total_duration_ms"] += duration_ms
            
            if result.success:
                self._stats["successes"] += 1
            else:
                self._stats["failures"] += 1
            
            return result
            
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self._stats["failures"] += 1
            self._stats["total_duration_ms"] += duration_ms
            
            return SkillResult(
                success=False,
                message=f'Skill execution error: {str(e)}',
                duration_ms=duration_ms
            )
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get skill execution statistics."""
        avg_duration = (
            self._stats["total_duration_ms"] / self._stats["invocations"]
            if self._stats["invocations"] > 0 else 0
        )
        
        success_rate = (
            self._stats["successes"] / self._stats["invocations"] * 100
            if self._stats["invocations"] > 0 else 0
        )
        
        return {
            'name': self.name,
            'invocations': self._stats['invocations'],
            'successes': self._stats['successes'],
            'failures': self._stats['failures'],
            'success_rate': round(success_rate, 2),
            'avg_duration_ms': round(avg_duration, 2),
            'last_execution': self._last_execution
        }
    
    def reset_statistics(self) -> None:
        """Reset statistics counters."""
        self._stats = {
            "invocations": 0,
            "successes": 0,
            "failures": 0,
            "total_duration_ms": 0
        }
    
    def to_function_declaration(self) -> Dict[str, Any]:
        """
        Convert skill to LLM function declaration format.
        
        Returns:
            Function declaration dict for use with Gemini function calling
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.get_parameters_schema()
        }
    
    def get_parameters_schema(self) -> Dict[str, Any]:
        """
        Get JSON schema for skill parameters.
        Override in subclasses for specific parameters.
        
        Returns:
            JSON schema dict
        """
        return {
            "type": "object",
            "properties": {},
            "required": []
        }
    
    def __repr__(self) -> str:
        meta = self.get_metadata()
        return f"<Skill: {meta.name} v{meta.version}>"
