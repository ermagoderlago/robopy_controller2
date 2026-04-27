"""
Robot AI Core - Custom Exceptions
=================================
Custom exception classes for the AI system.
"""


class AIError(Exception):
    """Base exception for AI system errors."""
    
    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
    
    def __str__(self):
        if self.details:
            return f"{self.message} | Details: {self.details}"
        return self.message


class LLMError(AIError):
    """Exception raised for LLM-related errors."""
    pass


class ValidationError(AIError):
    """Exception raised for validation errors."""
    pass


class CircuitBreakerOpenError(AIError):
    """Exception raised when circuit breaker is open."""
    
    def __init__(self, service_name: str, recovery_time: float):
        super().__init__(
            f"Circuit breaker open for {service_name}",
            {"service": service_name, "recovery_in_seconds": recovery_time}
        )
        self.service_name = service_name
        self.recovery_time = recovery_time


class ServiceUnavailableError(AIError):
    """Exception raised when an external service is unavailable."""
    
    def __init__(self, service_name: str, reason: str = None):
        super().__init__(
            f"Service unavailable: {service_name}",
            {"service": service_name, "reason": reason}
        )
        self.service_name = service_name


class ConfigurationError(AIError):
    """Exception raised for configuration errors."""
    pass


class SecurityError(AIError):
    """Exception raised for security violations."""
    pass


class MemoryError(AIError):
    """Exception raised for RAG memory-related errors."""
    pass


class NavigationError(AIError):
    """Exception raised for navigation errors."""
    pass


class HomeAssistantError(AIError):
    """Exception raised for Home Assistant errors."""
    pass


class ASRError(AIError):
    """Exception raised for speech recognition errors."""
    pass


class TTSError(AIError):
    """Exception raised for text-to-speech errors."""
    pass


class SkillError(AIError):
    """Exception raised for skill execution errors."""
    pass


class TimeoutError(AIError):
    """Exception raised when an operation times out."""
    
    def __init__(self, operation: str, timeout_seconds: float):
        super().__init__(
            f"Operation timed out: {operation}",
            {"operation": operation, "timeout": timeout_seconds}
        )
        self.operation = operation
        self.timeout_seconds = timeout_seconds
