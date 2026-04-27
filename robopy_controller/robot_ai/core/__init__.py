"""
Robot AI Core Package
=====================
Infrastruttura core per il sistema AI del robot.
"""

from .exceptions import (
    AIError,
    LLMError,
    ValidationError,
    CircuitBreakerOpenError,
    ServiceUnavailableError,
    ConfigurationError,
    SecurityError,
    MemoryError,
    NavigationError,
    HomeAssistantError,
    ASRError,
    TTSError,
    SkillError,
    TimeoutError,
)
from .config_manager import (
    ConfigManager,
    AIConfig,
)
from .event_bus import (
    EventBus,
    EventType,
    Event,
)
from .state_machine import (
    StateMachine,
    SystemState,
)
from .circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitState,
    circuit_breaker,
)
from .action_controller import (
    ActionController,
    ActionRequest,
)
from .image_handler import (
    Image,
    ImageValidator,
)
from .tool_declarations import (
    ToolRegistry,
    ToolDefinition,
    ToolParameter,
)

__all__ = [
    # Eccezioni
    "AIError",
    "LLMError",
    "ValidationError",
    "CircuitBreakerOpenError",
    "ServiceUnavailableError",
    "ConfigurationError",
    "SecurityError",
    "MemoryError",
    "NavigationError",
    "HomeAssistantError",
    "ASRError",
    "TTSError",
    "SkillError",
    "TimeoutError",
    # Config
    "ConfigManager",
    "AIConfig",
    # Event Bus
    "EventBus",
    "EventType",
    "Event",
    # State Machine
    "StateMachine",
    "SystemState",
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitState",
    "circuit_breaker",
    # Sprint 0: Action Controller
    "ActionController",
    "ActionRequest",
    # Sprint 0: Image Handler
    "Image",
    "ImageValidator",
    # Sprint 0: Tool Declarations
    "ToolRegistry",
    "ToolDefinition",
    "ToolParameter",
]
