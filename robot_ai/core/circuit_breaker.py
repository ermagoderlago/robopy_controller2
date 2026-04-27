"""
Robot AI Core - Circuit Breaker
================================
Implements Circuit Breaker pattern for fault tolerance.
Prevents cascade failures when external services are unavailable.
"""

import threading
import time
from enum import Enum, auto
from typing import Any, Callable, Dict, Optional, TypeVar
from dataclasses import dataclass, field
from functools import wraps

from .exceptions import CircuitBreakerOpenError, ServiceUnavailableError


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = auto()     # Normal operation
    OPEN = auto()       # Failing, rejecting calls
    HALF_OPEN = auto()  # Testing if service recovered


@dataclass
class CircuitStats:
    """Statistics for a circuit breaker."""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    consecutive_failures: int = 0
    open_count: int = 0


class CircuitBreaker:
    """
    Circuit Breaker implementation for external services.
    
    States:
    - CLOSED: Normal operation, calls pass through
    - OPEN: Service failing, calls rejected immediately
    - HALF_OPEN: Testing recovery, limited calls allowed
    
    Usage:
        breaker = CircuitBreaker("gemini", failure_threshold=3)
        
        try:
            result = breaker.call(api_function, arg1, arg2)
        except CircuitBreakerOpenError:
            # Use fallback
            result = fallback_response()
    
    Or as decorator:
        @circuit_breaker("gemini")
        async def call_gemini(prompt):
            ...
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
        success_threshold: int = 1,
        excluded_exceptions: tuple = ()
    ):
        """
        Initialize circuit breaker.
        
        Args:
            name: Service name (for logging)
            failure_threshold: Failures before opening circuit
            recovery_timeout: Seconds before trying half-open
            half_open_max_calls: Max calls allowed in half-open state
            success_threshold: Successes needed to close from half-open
            excluded_exceptions: Exceptions that don't count as failures
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold
        self.excluded_exceptions = excluded_exceptions
        
        self._state = CircuitState.CLOSED
        self._lock = threading.RLock()
        self._stats = CircuitStats()
        self._last_state_change = time.time()
        self._half_open_calls = 0
        self._half_open_successes = 0
        
        # Callbacks
        self._on_state_change: list = []
        self._on_failure: list = []
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            # Auto-transition from OPEN to HALF_OPEN after timeout
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_state_change >= self.recovery_timeout:
                    self._transition_to(CircuitState.HALF_OPEN)
            return self._state
    
    @property
    def is_open(self) -> bool:
        """Check if circuit is open (blocking calls)."""
        return self.state == CircuitState.OPEN
    
    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self.state == CircuitState.CLOSED
    
    @property
    def stats(self) -> CircuitStats:
        """Get circuit statistics."""
        return self._stats
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker.
        
        Args:
            func: Function to call
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerOpenError: If circuit is open
            Exception: Any exception from the function
        """
        with self._lock:
            state = self.state
            
            # Check if call is allowed
            if state == CircuitState.OPEN:
                self._stats.rejected_calls += 1
                time_until_retry = self.recovery_timeout - (time.time() - self._last_state_change)
                raise CircuitBreakerOpenError(self.name, max(0, time_until_retry))
            
            if state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    self._stats.rejected_calls += 1
                    raise CircuitBreakerOpenError(self.name, 0)
                self._half_open_calls += 1
        
        # Execute the call
        self._stats.total_calls += 1
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.excluded_exceptions:
            # Don't count excluded exceptions as failures
            raise
        except Exception as e:
            self._on_failure_occurred(e)
            raise
    
    async def call_async(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute async function through circuit breaker.
        
        Args:
            func: Async function to call
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
        """
        with self._lock:
            state = self.state
            
            if state == CircuitState.OPEN:
                self._stats.rejected_calls += 1
                time_until_retry = self.recovery_timeout - (time.time() - self._last_state_change)
                raise CircuitBreakerOpenError(self.name, max(0, time_until_retry))
            
            if state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    self._stats.rejected_calls += 1
                    raise CircuitBreakerOpenError(self.name, 0)
                self._half_open_calls += 1
        
        self._stats.total_calls += 1
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except self.excluded_exceptions:
            raise
        except Exception as e:
            self._on_failure_occurred(e)
            raise
    
    def _on_success(self) -> None:
        """Handle successful call."""
        with self._lock:
            self._stats.successful_calls += 1
            self._stats.last_success_time = time.time()
            self._stats.consecutive_failures = 0
            
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
    
    def _on_failure_occurred(self, exception: Exception) -> None:
        """Handle failed call."""
        with self._lock:
            self._stats.failed_calls += 1
            self._stats.last_failure_time = time.time()
            self._stats.consecutive_failures += 1
            
            # Call failure callbacks
            for callback in self._on_failure:
                try:
                    callback(self.name, exception)
                except Exception:
                    pass
            
            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open goes back to open
                self._transition_to(CircuitState.OPEN)
            elif self._state == CircuitState.CLOSED:
                if self._stats.consecutive_failures >= self.failure_threshold:
                    self._transition_to(CircuitState.OPEN)
    
    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state."""
        old_state = self._state
        self._state = new_state
        self._last_state_change = time.time()
        
        if new_state == CircuitState.OPEN:
            self._stats.open_count += 1
        elif new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
            self._half_open_successes = 0
        
        # Call state change callbacks
        for callback in self._on_state_change:
            try:
                callback(self.name, old_state, new_state)
            except Exception:
                pass
    
    def force_open(self) -> None:
        """Force circuit to open state."""
        with self._lock:
            self._transition_to(CircuitState.OPEN)
    
    def force_close(self) -> None:
        """Force circuit to closed state."""
        with self._lock:
            self._stats.consecutive_failures = 0
            self._transition_to(CircuitState.CLOSED)
    
    def reset(self) -> None:
        """Reset circuit breaker to initial state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._stats = CircuitStats()
            self._last_state_change = time.time()
            self._half_open_calls = 0
            self._half_open_successes = 0
    
    def on_state_change(self, callback: Callable) -> None:
        """Register callback for state changes."""
        self._on_state_change.append(callback)
    
    def on_failure(self, callback: Callable) -> None:
        """Register callback for failures."""
        self._on_failure.append(callback)
    
    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status."""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.name,
                "is_open": self._state == CircuitState.OPEN,
                "stats": {
                    "total_calls": self._stats.total_calls,
                    "successful_calls": self._stats.successful_calls,
                    "failed_calls": self._stats.failed_calls,
                    "rejected_calls": self._stats.rejected_calls,
                    "consecutive_failures": self._stats.consecutive_failures,
                    "open_count": self._stats.open_count
                },
                "time_in_state": time.time() - self._last_state_change,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout
            }


class CircuitBreakerRegistry:
    """
    Registry for managing multiple circuit breakers.
    
    Usage:
        registry = CircuitBreakerRegistry()
        registry.register("gemini", failure_threshold=3)
        registry.register("tts", failure_threshold=5)
        
        breaker = registry.get("gemini")
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
            
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._breaker_lock = threading.RLock()
        self._global_callbacks: list = []
        self._initialized = True
    
    def register(self, name: str, **kwargs) -> CircuitBreaker:
        """
        Register a new circuit breaker.
        
        Args:
            name: Service name
            **kwargs: CircuitBreaker constructor arguments
            
        Returns:
            The circuit breaker instance
        """
        with self._breaker_lock:
            if name in self._breakers:
                return self._breakers[name]
            
            breaker = CircuitBreaker(name, **kwargs)
            
            # Add global callbacks
            for callback in self._global_callbacks:
                breaker.on_state_change(callback)
            
            self._breakers[name] = breaker
            return breaker
    
    def get(self, name: str) -> Optional[CircuitBreaker]:
        """Get circuit breaker by name."""
        return self._breakers.get(name)
    
    def get_or_create(self, name: str, **kwargs) -> CircuitBreaker:
        """Get existing or create new circuit breaker."""
        with self._breaker_lock:
            if name not in self._breakers:
                return self.register(name, **kwargs)
            return self._breakers[name]
    
    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all circuit breakers."""
        return {name: breaker.get_status() for name, breaker in self._breakers.items()}
    
    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        for breaker in self._breakers.values():
            breaker.reset()
    
    def on_any_state_change(self, callback: Callable) -> None:
        """Register callback for any circuit breaker state change."""
        self._global_callbacks.append(callback)
        for breaker in self._breakers.values():
            breaker.on_state_change(callback)


# Decorator for easy use
T = TypeVar('T')


def circuit_breaker(name: str, **kwargs):
    """
    Decorator to wrap function with circuit breaker.
    
    Usage:
        @circuit_breaker("gemini", failure_threshold=3)
        async def call_gemini(prompt):
            ...
    """
    registry = CircuitBreakerRegistry()
    breaker = registry.get_or_create(name, **kwargs)
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        import asyncio
        
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kw):
                return await breaker.call_async(func, *args, **kw)
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kw):
                return breaker.call(func, *args, **kw)
            return sync_wrapper
    
    return decorator
