# IMPLEMENTAZIONE MARCUS SPRINT 0
## Codice Production-Grade: Hardening & Esecuzione Azioni Unificata

---

# FILE 1: `marcus/core/skill_result.py`
## Contratto SkillResult (Dataclass Immutabile)

```python
"""
SkillResult: Contratto immutabile per gli esiti dell'esecuzione delle skill.
Ogni skill DEVE restituire questo tipo.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
import time
from enum import Enum


class SkillErrorCode(Enum):
    """Codici errore standard per le skill."""
    SUCCESS = "SUCCESS"
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
    INVALID_PARAMETERS = "INVALID_PARAMETERS"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


@dataclass(frozen=True)  # Immutabile
class SkillResult:
    """
    Contratto risultato immutabile per tutte le esecuzioni skill.
    
    Campi:
        success: Se la skill è stata eseguita con successo.
        message: Messaggio leggibile (breve).
        data: Dati specifici della skill (se applicabile).
        speak: Testo da dire all'utente (se applicabile).
        error_code: Codice errore standard (se fallimento).
        duration_ms: Tempo esecuzione in millisecondi.
    """
    
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    speak: Optional[str] = None
    error_code: Optional[SkillErrorCode] = None
    duration_ms: float = 0.0
    
    def __post_init__(self):
        """Valida invarianti contratto."""
        # Se success=True, error_code deve essere None
        if self.success and self.error_code is not None:
            raise ValueError("success=True ma error_code è impostato")
        
        # Se success=False, error_code deve essere impostato
        if not self.success and self.error_code is None:
            raise ValueError("success=False ma error_code è None")
        
        # Messaggio deve essere non vuoto
        if not self.message or not isinstance(self.message, str):
            raise ValueError("message deve essere stringa non vuota")
        
        # Durata deve essere non negativa
        if self.duration_ms < 0:
            raise ValueError("duration_ms deve essere >= 0")
    
    def to_dict(self) -> Dict[str, Any]:
        """Converte in dict serializzabile JSON."""
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data,
            "speak": self.speak,
            "error_code": self.error_code.value if self.error_code else None,
            "duration_ms": self.duration_ms,
        }
    
    @staticmethod
    def success_result(
        message: str,
        data: Optional[Dict[str, Any]] = None,
        speak: Optional[str] = None,
        duration_ms: float = 0.0,
    ) -> "SkillResult":
        """Factory: crea risultato successo."""
        return SkillResult(
            success=True,
            message=message,
            data=data,
            speak=speak,
            error_code=None,
            duration_ms=duration_ms,
        )
    
    @staticmethod
    def failure_result(
        message: str,
        error_code: SkillErrorCode = SkillErrorCode.UNKNOWN_ERROR,
        duration_ms: float = 0.0,
    ) -> "SkillResult":
        """Factory: crea risultato fallimento."""
        return SkillResult(
            success=False,
            message=message,
            data=None,
            speak=f"Spiacente, {message.lower()}",
            error_code=error_code,
            duration_ms=duration_ms,
        )
```

---

# FILE 2: `marcus/core/action_controller.py`
## Motore Esecuzione Azioni Unificato

```python
"""
ActionController: Punto di ingresso singolo per tutte le esecuzioni azioni/skill.
Impone type safety, validazione parametri, gestione errori.
"""

import logging
import time
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass
import inspect

from marcus.core.skill_result import SkillResult, SkillErrorCode
from marcus.skills import (
    # Importa tutte le skill
    TurnOnLightSkill,
    TurnOffLightSkill,
    AdjustBlindsSkill,
    # ... (15 skill totali)
)

logger = logging.getLogger(__name__)


@dataclass
class ActionRequest:
    """Richiesta azione tipizzata."""
    skill_name: str
    parameters: Dict[str, Any]
    timeout_seconds: float = 30.0
    user_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


class ActionController:
    """Controller esecuzione azioni unificato."""
    
    def __init__(self):
        """Inizializza registro skill."""
        self.skills: Dict[str, Callable] = self._register_skills()
        self.execution_history: list = []
    
    def _register_skills(self) -> Dict[str, Callable]:
        """Registra tutte le skill disponibili."""
        return {
            "turn_on_light": TurnOnLightSkill().execute,
            "turn_off_light": TurnOffLightSkill().execute,
            "adjust_blinds": AdjustBlindsSkill().execute,
            # ... (aggiungi rimanenti 15 skill)
        }
    
    def execute_action(self, request: ActionRequest) -> SkillResult:
        """
        Esegue azione (skill).
        
        Args:
            request: Richiesta azione tipizzata.
        
        Returns:
            SkillResult (ha sempre successo o restituisce risultato fallimento, non solleva mai eccezioni).
        """
        start_time = time.time()
        
        try:
            # Passo 1: Valida richiesta
            validation_error = self._validate_request(request)
            if validation_error:
                return self._failure_with_timing(
                    validation_error,
                    SkillErrorCode.INVALID_PARAMETERS,
                    start_time,
                )
            
            # Passo 2: Cerca skill
            skill_func = self.skills.get(request.skill_name.lower())
            if not skill_func:
                logger.warning(f"Skill sconosciuta: {request.skill_name}")
                return self._failure_with_timing(
                    f"Skill '{request.skill_name}' non trovata. "
                    f"Disponibili: {', '.join(self.skills.keys())}",
                    SkillErrorCode.SKILL_NOT_FOUND,
                    start_time,
                )
            
            # Passo 3: Valida parametri contro firma skill
            param_error = self._validate_parameters(skill_func, request.parameters)
            if param_error:
                return self._failure_with_timing(
                    param_error,
                    SkillErrorCode.INVALID_PARAMETERS,
                    start_time,
                )
            
            # Passo 4: Esegui skill con timeout
            logger.info(f"Esecuzione skill: {request.skill_name} con params: {request.parameters}")
            
            result = self._execute_with_timeout(
                skill_func,
                request.parameters,
                request.timeout_seconds,
            )
            
            # Passo 5: Valida contratto risultato
            if not isinstance(result, SkillResult):
                logger.error(f"Skill {request.skill_name} ha restituito non-SkillResult: {type(result)}")
                return SkillResult.failure_result(
                    f"Skill ha restituito tipo non valido: {type(result).__name__}",
                    SkillErrorCode.UNKNOWN_ERROR,
                    time.time() - start_time,
                )
            
            # Passo 6: Log risultato
            self.execution_history.append({
                "skill": request.skill_name,
                "success": result.success,
                "duration_ms": result.duration_ms,
                "timestamp": time.time(),
            })
            
            logger.info(f"✅ Skill eseguita: {request.skill_name}, success={result.success}, duration={result.duration_ms}ms")
            
            return result
        
        except Exception as e:
            # Gestione errori graceful
            logger.exception(f"Errore inatteso in execute_action: {e}")
            return SkillResult.failure_result(
                f"Errore inatteso: {str(e)[:100]}",
                SkillErrorCode.UNKNOWN_ERROR,
                time.time() - start_time,
            )
    
    def _validate_request(self, request: ActionRequest) -> Optional[str]:
        """Valida struttura richiesta. Restituisce messaggio errore se invalida."""
        if not request.skill_name:
            return "skill_name è vuoto"
        if not isinstance(request.parameters, dict):
            return "parameters deve essere dict"
        if request.timeout_seconds <= 0:
            return "timeout_seconds deve essere > 0"
        return None
    
    def _validate_parameters(self, skill_func: Callable, params: Dict[str, Any]) -> Optional[str]:
        """Valida che i parametri corrispondano alla firma della skill."""
        try:
            sig = inspect.signature(skill_func)
            bound = sig.bind(**params)
            bound.apply_defaults()
            return None
        except TypeError as e:
            return f"Validazione parametri fallita: {str(e)}"
    
    def _execute_with_timeout(
        self,
        skill_func: Callable,
        params: Dict[str, Any],
        timeout_seconds: float,
    ) -> SkillResult:
        """Esegue skill con protezione timeout."""
        import signal
        
        def timeout_handler(signum, frame):
            raise TimeoutError(f"Esecuzione skill ha superato {timeout_seconds}s")
        
        # Imposta signal handler
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(int(timeout_seconds))
        
        try:
            result = skill_func(**params)
            signal.alarm(0)  # Cancella allarme
            return result
        except TimeoutError as e:
            logger.error(f"Timeout skill: {e}")
            return SkillResult.failure_result(
                str(e),
                SkillErrorCode.EXECUTION_TIMEOUT,
                timeout_seconds * 1000,
            )
        finally:
            signal.signal(signal.SIGALRM, old_handler)
    
    def _failure_with_timing(
        self,
        message: str,
        error_code: SkillErrorCode,
        start_time: float,
    ) -> SkillResult:
        """Crea risultato fallimento con timing."""
        return SkillResult.failure_result(
            message,
            error_code,
            (time.time() - start_time) * 1000,
        )
    
    def get_available_skills(self) -> list:
        """Restituisce lista nomi skill disponibili."""
        return list(self.skills.keys())
    
    def get_execution_history(self, limit: int = 100) -> list:
        """Restituisce ultime N esecuzioni."""
        return self.execution_history[-limit:]


# Istanza Singleton
_action_controller = ActionController()


def execute_action(request: ActionRequest) -> SkillResult:
    """Funzione globale per esecuzione azioni."""
    return _action_controller.execute_action(request)
```

---

# FILE 3: `marcus/core/image_handler.py`
## Standardizzazione Formato Immagine

```python
"""
ImageHandler: Standard interno = bytes (raw).
Conversione a base64 SOLO al confine API.
"""

import base64
import logging
from typing import Union, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Image:
    """Rappresentazione immagine interna (sempre bytes)."""
    data: bytes  # Raw image bytes
    format: str  # "jpeg", "png", "rgb", etc.
    width: int
    height: int
    metadata: Optional[dict] = None
    
    def to_base64(self) -> str:
        """Converte a base64 SOLO per export API."""
        return base64.b64encode(self.data).decode('utf-8')
    
    @staticmethod
    def from_base64(base64_str: str, format: str, width: int, height: int) -> "Image":
        """Crea da base64 (es. da input API)."""
        try:
            raw_bytes = base64.b64decode(base64_str)
            return Image(data=raw_bytes, format=format, width=width, height=height)
        except Exception as e:
            logger.error(f"Fallita decodifica base64: {e}")
            raise ValueError(f"Dati base64 non validi: {str(e)}")
    
    @staticmethod
    def from_file(filepath: str) -> "Image":
        """Carica immagine da file."""
        with open(filepath, 'rb') as f:
            raw_bytes = f.read()
        
        format = filepath.split('.')[-1].lower()
        # Estrazione semplice dimensioni (in produzione, usa PIL/OpenCV)
        width, height = 640, 480  # placeholder
        
        return Image(data=raw_bytes, format=format, width=width, height=height)


class ImageValidator:
    """Valida immagini prima di storage/processing."""
    
    MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
    ALLOWED_FORMATS = {"jpeg", "png", "jpg", "rgb"}
    
    @staticmethod
    def validate(image: Image) -> Optional[str]:
        """Restituisce messaggio errore se invalida, None se OK."""
        if not isinstance(image.data, bytes):
            return f"image.data deve essere bytes, ottenuto {type(image.data)}"
        
        if len(image.data) > ImageValidator.MAX_SIZE_BYTES:
            return f"Dimensione immagine {len(image.data)} eccede max {ImageValidator.MAX_SIZE_BYTES}"
        
        if image.format.lower() not in ImageValidator.ALLOWED_FORMATS:
            return f"Formato {image.format} non consentito. Consentiti: {ImageValidator.ALLOWED_FORMATS}"
        
        if image.width <= 0 or image.height <= 0:
            return "Dimensioni immagine devono essere positive"
        
        return None
```

---

# FILE 4: `marcus/core/tool_declarations.py`
## Schema & Validazione Tool Gemini

```python
"""
Dichiarazioni tool per function calling Gemini.
Deve corrispondere esattamente allo schema SDK Gemini.
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class ToolParameter:
    """Definizione parametro per tool Gemini."""
    name: str
    type: str  # "string", "number", "boolean", "object", "array"
    description: str
    required: bool = False
    enum: Optional[List[str]] = None


@dataclass
class ToolDefinition:
    """Definizione tool compatibile con SDK Gemini."""
    name: str
    description: str
    parameters: List[ToolParameter]
    
    def to_gemini_schema(self) -> Dict[str, Any]:
        """Converte a schema API Gemini."""
        properties = {}
        required = []
        
        for param in self.parameters:
            prop = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum:
                prop["enum"] = param.enum
            properties[param.name] = prop
            
            if param.required:
                required.append(param.name)
        
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }


class ToolRegistry:
    """Registro centrale di tutti i tool disponibili per Gemini."""
    
    def __init__(self):
        self.tools: Dict[str, ToolDefinition] = {}
        self._register_default_tools()
    
    def _register_default_tools(self):
        """Registra tool che Gemini può chiamare."""
        
        # Tool 1: Esegui Skill
        self.register(ToolDefinition(
            name="execute_skill",
            description="Esegui una skill (azione) nel sistema robot.",
            parameters=[
                ToolParameter(
                    name="skill_name",
                    type="string",
                    description="Nome della skill da eseguire (es. 'turn_on_light')",
                    required=True,
                ),
                ToolParameter(
                    name="parameters",
                    type="object",
                    description="Parametri per la skill (formato dipende dalla skill)",
                    required=True,
                ),
                ToolParameter(
                    name="timeout_seconds",
                    type="number",
                    description="Tempo massimo esecuzione in secondi",
                    required=False,
                ),
            ]
        ))
        
        # Tool 2: Interroga Memoria
        self.register(ToolDefinition(
            name="query_memory",
            description="Cerca nella memoria del robot interazioni passate.",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Query di ricerca (es. 'preferenza caffè')",
                    required=True,
                ),
                ToolParameter(
                    name="limit",
                    type="number",
                    description="Max risultati da restituire (default 5)",
                    required=False,
                ),
                ToolParameter(
                    name="type",
                    type="string",
                    description="Filtra per tipo memoria",
                    enum=["episodic", "semantic"],
                    required=False,
                ),
            ]
        ))
        
        # Tool 3: Ottieni Stato Home Assistant
        self.register(ToolDefinition(
            name="get_ha_state",
            description="Interroga stati dispositivi Home Assistant.",
            parameters=[
                ToolParameter(
                    name="device_type",
                    type="string",
                    description="Tipo dispositivo (es. 'light', 'blinds')",
                    required=True,
                ),
                ToolParameter(
                    name="location",
                    type="string",
                    description="Stanza/posizione (es. 'living_room')",
                    required=False,
                ),
            ]
        ))
    
    def register(self, tool: ToolDefinition) -> None:
        """Registra un tool."""
        if tool.name in self.tools:
            raise ValueError(f"Tool {tool.name} già registrato")
        self.tools[tool.name] = tool
    
    def get_tools_for_gemini(self) -> List[Dict[str, Any]]:
        """Esporta tutti i tool in formato SDK Gemini."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.to_gemini_schema(),
            }
            for tool in self.tools.values()
        ]
    
    def validate_tool_call(self, tool_name: str, parameters: Dict[str, Any]) -> Optional[str]:
        """Valida parametri chiamata tool. Restituisce messaggio errore se invalida."""
        tool = self.tools.get(tool_name)
        if not tool:
            return f"Tool sconosciuto: {tool_name}"
        
        # Controlla params richiesti
        for param in tool.parameters:
            if param.required and param.name not in parameters:
                return f"Parametro richiesto mancante: {param.name}"
        
        # Valida tipi param (base)
        for param in tool.parameters:
            if param.name in parameters:
                value = parameters[param.name]
                if param.type == "string" and not isinstance(value, str):
                    return f"Parametro {param.name} deve essere stringa, ottenuto {type(value)}"
                elif param.type == "number" and not isinstance(value, (int, float)):
                    return f"Parametro {param.name} deve essere numero, ottenuto {type(value)}"
        
        return None


# Singleton
_tool_registry = ToolRegistry()


def get_tools_for_gemini() -> List[Dict[str, Any]]:
    """Ottieni tutti i tool in formato Gemini."""
    return _tool_registry.get_tools_for_gemini()
```

---

# FILE 5: `tests/unit/test_sprint_0_hardening.py`
## Unit Test per Sprint 0

```python
"""
Unit test per Sprint 0 hardening (funzionalità hardening).
"""

import pytest
import time
from marcus.core.skill_result import SkillResult, SkillErrorCode
from marcus.core.action_controller import ActionRequest, ActionController
from marcus.core.image_handler import Image, ImageValidator
from marcus.core.tool_declarations import ToolRegistry


class TestSkillResult:
    """Test per contratto SkillResult."""
    
    def test_skill_result_success_creation(self):
        """Test creazione risultato successo."""
        result = SkillResult.success_result(
            "Luce accesa",
            speak="Ho acceso la luce"
        )
        assert result.success is True
        assert result.message == "Luce accesa"
        assert result.error_code is None
    
    def test_skill_result_failure_creation(self):
        """Test creazione risultato fallimento."""
        result = SkillResult.failure_result(
            "Dispositivo non trovato",
            SkillErrorCode.SKILL_NOT_FOUND
        )
        assert result.success is False
        assert result.error_code == SkillErrorCode.SKILL_NOT_FOUND
    
    def test_skill_result_immutable(self):
        """Test SkillResult è immutabile (frozen dataclass)."""
        result = SkillResult.success_result("test")
        with pytest.raises(AttributeError):
            result.success = False
    
    def test_skill_result_contract_violation_success_with_error(self):
        """Test contratto: success=True ma error_code impostato."""
        with pytest.raises(ValueError):
            SkillResult(
                success=True,
                message="test",
                error_code=SkillErrorCode.UNKNOWN_ERROR,
            )
    
    def test_skill_result_to_dict(self):
        """Test serializzazione a dict."""
        result = SkillResult.success_result("Messaggio test", speak="test")
        d = result.to_dict()
        assert d["success"] is True
        assert d["message"] == "Messaggio test"
        assert d["speak"] == "test"


class TestActionController:
    """Test per esecuzione azioni unificata."""
    
    @pytest.fixture
    def controller(self):
        return ActionController()
    
    def test_execute_action_unknown_skill(self, controller):
        """Test esecuzione skill sconosciuta."""
        request = ActionRequest(
            skill_name="skill_inesistente",
            parameters={},
        )
        result = controller.execute_action(request)
        
        assert result.success is False
        assert result.error_code == SkillErrorCode.SKILL_NOT_FOUND
    
    def test_execute_action_invalid_parameters(self, controller):
        """Test skill con parametri invalidi."""
        request = ActionRequest(
            skill_name="turn_on_light",
            parameters={},  # Manca param richiesto 'room'
        )
        result = controller.execute_action(request)
        
        assert result.success is False
        assert result.error_code == SkillErrorCode.INVALID_PARAMETERS
    
    def test_get_available_skills(self, controller):
        """Test lista skill disponibili."""
        skills = controller.get_available_skills()
        assert "turn_on_light" in skills
        assert len(skills) >= 10  # Almeno 10 skills attese
    
    def test_execution_history_tracking(self, controller):
        """Test che le esecuzioni vengano loggate."""
        # Esegui una skill (fallirà ma va bene)
        controller.execute_action(ActionRequest(
            skill_name="unknown",
            parameters={},
        ))
        
        history = controller.get_execution_history()
        assert len(history) > 0
        assert history[-1]["skill"] == "unknown"
        assert history[-1]["success"] is False


class TestImageHandler:
    """Test per standardizzazione formato immagine."""
    
    def test_image_creation_raw_bytes(self):
        """Test creazione immagine da bytes raw."""
        raw = b"\x89PNG\r\n\x1a\n..."  # Byte magic PNG
        img = Image(data=raw, format="png", width=640, height=480)
        
        assert isinstance(img.data, bytes)
        assert img.format == "png"
    
    def test_image_to_base64(self):
        """Test conversione a base64 (solo last-mile)."""
        raw = b"test image data"
        img = Image(data=raw, format="jpeg", width=100, height=100)
        
        base64_str = img.to_base64()
        assert isinstance(base64_str, str)
        assert len(base64_str) > 0
    
    def test_image_from_base64(self):
        """Test creazione immagine da base64."""
        original = b"test image data"
        base64_str = Image(
            data=original,
            format="jpeg",
            width=100,
            height=100
        ).to_base64()
        
        restored = Image.from_base64(base64_str, "jpeg", 100, 100)
        assert restored.data == original
    
    def test_image_validator_size(self):
        """Test validazione dimensione immagine."""
        # Troppo grande
        large_data = b"x" * (11 * 1024 * 1024)  # 11 MB
        img = Image(data=large_data, format="jpeg", width=640, height=480)
        error = ImageValidator.validate(img)
        assert error is not None
    
    def test_image_validator_format(self):
        """Test validazione formato immagine."""
        img = Image(
            data=b"test",
            format="bmp",  # Non consentito
            width=640,
            height=480
        )
        error = ImageValidator.validate(img)
        assert error is not None


class TestToolDeclarations:
    """Test per schema tool Gemini."""
    
    def test_tool_registry_default_tools(self):
        """Test che i tool di default siano registrati."""
        registry = ToolRegistry()
        tools = registry.get_tools_for_gemini()
        
        tool_names = [t["name"] for t in tools]
        assert "execute_skill" in tool_names
        assert "query_memory" in tool_names
    
    def test_tool_schema_format(self):
        """Test schema tool compatibile con Gemini."""
        registry = ToolRegistry()
        tools = registry.get_tools_for_gemini()
        
        # Ogni tool deve avere campi richiesti
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "parameters" in tool
            assert tool["parameters"]["type"] == "object"
    
    def test_tool_call_validation(self):
        """Test validazione chiamate tool."""
        registry = ToolRegistry()
        
        # Chiamata valida
        error = registry.validate_tool_call(
            "execute_skill",
            {"skill_name": "turn_on_light", "parameters": {}}
        )
        assert error is None