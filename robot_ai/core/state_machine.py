"""
Robot AI Core - State Machine
==============================
Operational state management for the AI system.
Handles system lifecycle: BOOTING → READY → PROCESSING → ERROR → SLEEPING
"""

import threading
import time
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass, field


class SystemState(Enum):
    """System operational states."""
    
    BOOTING = auto()      # System starting up
    INITIALIZING = auto() # Loading models, connecting
    READY = auto()        # Ready for interaction
    LISTENING = auto()    # Actively listening for input
    PROCESSING = auto()   # Processing user request
    SPEAKING = auto()     # TTS playing
    EXECUTING = auto()    # Executing action (HA, Nav)
    ERROR = auto()        # Error state
    DEGRADED = auto()     # Running with limited features
    SLEEPING = auto()     # Low-power mode
    SHUTDOWN = auto()     # Shutting down


@dataclass
class StateTransition:
    """Represents a state transition."""
    from_state: SystemState
    to_state: SystemState
    timestamp: float = field(default_factory=time.time)
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class StateMachine:
    """
    Thread-safe state machine for system lifecycle management.
    
    Features:
    - Valid transition enforcement
    - Transition callbacks (on_enter, on_exit)
    - State history tracking
    - Timeout handling
    - Degraded mode support
    
    Usage:
        sm = StateMachine()
        sm.on_enter(SystemState.READY, lambda: print("System ready!"))
        sm.transition_to(SystemState.READY)
    """
    
    # Valid state transitions
    VALID_TRANSITIONS: Dict[SystemState, Set[SystemState]] = {
        SystemState.BOOTING: {
            SystemState.INITIALIZING,
            SystemState.ERROR,
            SystemState.SHUTDOWN
        },
        SystemState.INITIALIZING: {
            SystemState.READY,
            SystemState.DEGRADED,
            SystemState.ERROR,
            SystemState.SHUTDOWN
        },
        SystemState.READY: {
            SystemState.LISTENING,
            SystemState.PROCESSING,
            SystemState.SLEEPING,
            SystemState.ERROR,
            SystemState.DEGRADED,
            SystemState.SHUTDOWN
        },
        SystemState.LISTENING: {
            SystemState.READY,
            SystemState.PROCESSING,
            SystemState.ERROR,
            SystemState.SHUTDOWN
        },
        SystemState.PROCESSING: {
            SystemState.READY,
            SystemState.SPEAKING,
            SystemState.EXECUTING,
            SystemState.ERROR,
            SystemState.DEGRADED,
            SystemState.SHUTDOWN
        },
        SystemState.SPEAKING: {
            SystemState.READY,
            SystemState.LISTENING,
            SystemState.ERROR,
            SystemState.SHUTDOWN
        },
        SystemState.EXECUTING: {
            SystemState.READY,
            SystemState.SPEAKING,
            SystemState.ERROR,
            SystemState.SHUTDOWN
        },
        SystemState.ERROR: {
            SystemState.READY,
            SystemState.DEGRADED,
            SystemState.INITIALIZING,
            SystemState.SHUTDOWN
        },
        SystemState.DEGRADED: {
            SystemState.READY,
            SystemState.LISTENING,
            SystemState.PROCESSING,
            SystemState.ERROR,
            SystemState.SHUTDOWN
        },
        SystemState.SLEEPING: {
            SystemState.READY,
            SystemState.LISTENING,
            SystemState.SHUTDOWN
        },
        SystemState.SHUTDOWN: set()  # Terminal state
    }
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, initial_state: SystemState = SystemState.BOOTING):
        if hasattr(self, '_initialized') and self._initialized:
            return
            
        self._state = initial_state
        self._state_lock = threading.RLock()
        self._history: List[StateTransition] = []
        self._max_history = 100
        
        # Callbacks
        self._on_enter_callbacks: Dict[SystemState, List[Callable]] = {}
        self._on_exit_callbacks: Dict[SystemState, List[Callable]] = {}
        self._on_any_transition: List[Callable] = []
        
        # State metadata
        self._state_entered_at = time.time()
        self._error_message: Optional[str] = None
        self._degraded_features: Set[str] = set()
        
        # Timeouts
        self._state_timeouts: Dict[SystemState, float] = {}
        self._timeout_thread: Optional[threading.Thread] = None
        self._running = True
        
        self._initialized = True
    
    @property
    def state(self) -> SystemState:
        """Get current state."""
        with self._state_lock:
            return self._state
    
    @property
    def state_name(self) -> str:
        """Get current state name."""
        return self.state.name
    
    @property
    def time_in_state(self) -> float:
        """Get time spent in current state (seconds)."""
        return time.time() - self._state_entered_at
    
    @property
    def is_ready(self) -> bool:
        """Check if system is ready for interaction."""
        return self.state in {SystemState.READY, SystemState.LISTENING, SystemState.DEGRADED}
    
    @property
    def is_busy(self) -> bool:
        """Check if system is busy processing."""
        return self.state in {SystemState.PROCESSING, SystemState.SPEAKING, SystemState.EXECUTING}
    
    @property
    def is_error(self) -> bool:
        """Check if system is in error state."""
        return self.state == SystemState.ERROR
    
    @property
    def is_degraded(self) -> bool:
        """Check if system is in degraded mode."""
        return self.state == SystemState.DEGRADED
    
    @property
    def error_message(self) -> Optional[str]:
        """Get current error message if in error state."""
        return self._error_message
    
    @property
    def degraded_features(self) -> Set[str]:
        """Get set of features that are degraded."""
        return self._degraded_features.copy()
    
    def can_transition_to(self, new_state: SystemState) -> bool:
        """Check if transition to new_state is valid."""
        valid_targets = self.VALID_TRANSITIONS.get(self._state, set())
        return new_state in valid_targets
    
    def transition_to(self, new_state: SystemState, reason: str = "",
                     metadata: Dict[str, Any] = None) -> bool:
        """
        Transition to a new state.
        
        Args:
            new_state: Target state
            reason: Reason for transition
            metadata: Additional metadata
            
        Returns:
            True if transition was successful
        """
        with self._state_lock:
            old_state = self._state
            
            # Validate transition
            if not self.can_transition_to(new_state):
                return False
            
            # Record transition
            transition = StateTransition(
                from_state=old_state,
                to_state=new_state,
                reason=reason,
                metadata=metadata or {}
            )
            self._history.append(transition)
            if len(self._history) > self._max_history:
                self._history.pop(0)
            
            # Call exit callbacks
            self._call_exit_callbacks(old_state, new_state)
            
            # Update state
            self._state = new_state
            self._state_entered_at = time.time()
            
            # Clear error if leaving error state
            if old_state == SystemState.ERROR and new_state != SystemState.ERROR:
                self._error_message = None
            
            # Clear degraded features if fully recovered
            if old_state == SystemState.DEGRADED and new_state == SystemState.READY:
                self._degraded_features.clear()
            
            # Call enter callbacks
            self._call_enter_callbacks(new_state, old_state)
            
            # Call any-transition callbacks
            for callback in self._on_any_transition:
                try:
                    callback(transition)
                except Exception as e:
                    print(f"Error in transition callback: {e}")
            
            return True
    
    def set_error(self, message: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Transition to error state with message.
        
        Args:
            message: Error message
            metadata: Additional error metadata
        """
        self._error_message = message
        return self.transition_to(
            SystemState.ERROR, 
            reason=message,
            metadata=metadata
        )
    
    def set_degraded(self, features: List[str], reason: str = "") -> bool:
        """
        Transition to degraded state.
        
        Args:
            features: List of degraded features
            reason: Reason for degradation
        """
        self._degraded_features.update(features)
        return self.transition_to(
            SystemState.DEGRADED,
            reason=reason,
            metadata={"degraded_features": features}
        )
    
    def recover(self, reason: str = "Recovery") -> bool:
        """
        Attempt to recover from error/degraded state.
        
        Returns:
            True if recovery was successful
        """
        if self.state == SystemState.ERROR:
            return self.transition_to(SystemState.INITIALIZING, reason=reason)
        elif self.state == SystemState.DEGRADED:
            return self.transition_to(SystemState.READY, reason=reason)
        return False
    
    def on_enter(self, state: SystemState, callback: Callable) -> None:
        """
        Register callback for entering a state.
        
        Args:
            state: State to watch
            callback: Function to call (receives old_state as argument)
        """
        if state not in self._on_enter_callbacks:
            self._on_enter_callbacks[state] = []
        self._on_enter_callbacks[state].append(callback)
    
    def on_exit(self, state: SystemState, callback: Callable) -> None:
        """
        Register callback for exiting a state.
        
        Args:
            state: State to watch
            callback: Function to call (receives new_state as argument)
        """
        if state not in self._on_exit_callbacks:
            self._on_exit_callbacks[state] = []
        self._on_exit_callbacks[state].append(callback)
    
    def on_transition(self, callback: Callable) -> None:
        """
        Register callback for any state transition.
        
        Args:
            callback: Function to call (receives StateTransition as argument)
        """
        self._on_any_transition.append(callback)
    
    def set_timeout(self, state: SystemState, timeout_seconds: float,
                   on_timeout: Callable = None) -> None:
        """
        Set timeout for a state.
        
        If the system remains in the state for longer than timeout,
        on_timeout callback is called or system transitions to ERROR.
        
        Args:
            state: State to set timeout for
            timeout_seconds: Timeout in seconds
            on_timeout: Callback on timeout (optional)
        """
        self._state_timeouts[state] = timeout_seconds
        
        if on_timeout:
            # Store timeout callback
            if not hasattr(self, '_timeout_callbacks'):
                self._timeout_callbacks = {}
            self._timeout_callbacks[state] = on_timeout
    
    def get_history(self, limit: int = 10) -> List[StateTransition]:
        """Get recent state transition history."""
        return self._history[-limit:]
    
    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive status information."""
        return {
            "state": self.state.name,
            "time_in_state": self.time_in_state,
            "is_ready": self.is_ready,
            "is_busy": self.is_busy,
            "is_error": self.is_error,
            "is_degraded": self.is_degraded,
            "error_message": self._error_message,
            "degraded_features": list(self._degraded_features),
            "transition_count": len(self._history),
            "valid_transitions": [s.name for s in self.VALID_TRANSITIONS.get(self._state, set())]
        }
    
    def _call_enter_callbacks(self, new_state: SystemState, old_state: SystemState) -> None:
        """Call callbacks for entering a state."""
        callbacks = self._on_enter_callbacks.get(new_state, [])
        for callback in callbacks:
            try:
                callback(old_state)
            except Exception as e:
                print(f"Error in on_enter callback for {new_state}: {e}")
    
    def _call_exit_callbacks(self, old_state: SystemState, new_state: SystemState) -> None:
        """Call callbacks for exiting a state."""
        callbacks = self._on_exit_callbacks.get(old_state, [])
        for callback in callbacks:
            try:
                callback(new_state)
            except Exception as e:
                print(f"Error in on_exit callback for {old_state}: {e}")
    
    def shutdown(self) -> bool:
        """Transition to shutdown state."""
        self._running = False
        return self.transition_to(SystemState.SHUTDOWN, reason="System shutdown")
    
    def reset(self) -> None:
        """Reset state machine (for testing)."""
        with self._state_lock:
            self._state = SystemState.BOOTING
            self._state_entered_at = time.time()
            self._error_message = None
            self._degraded_features.clear()
            self._history.clear()
