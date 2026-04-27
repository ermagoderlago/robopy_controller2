"""
Robot AI Core - Event Bus
=========================
Thread-safe event bus for loose coupling between components.
Implements Observer pattern with async/sync handlers.
"""

import asyncio
import inspect
import weakref
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set
from enum import Enum
from dataclasses import dataclass, field
from collections import deque


class EventType(str, Enum):
    """Event types for the AI system."""
    
    # System lifecycle events
    SYSTEM_STARTING = "system_starting"
    SYSTEM_READY = "system_ready"
    SYSTEM_STOPPING = "system_stopping"
    STATE_CHANGED = "state_changed"
    
    # Error events
    ERROR_OCCURRED = "error_occurred"
    HEALTH_ISSUE = "health_issue"
    CIRCUIT_BREAKER_OPENED = "circuit_breaker_opened"
    CIRCUIT_BREAKER_CLOSED = "circuit_breaker_closed"
    
    # Configuration events
    CONFIG_CHANGED = "config_changed"
    CONFIG_RELOADED = "config_reloaded"
    
    # Memory/RAG events
    MEMORY_ADDED = "memory_added"
    MEMORY_UPDATED = "memory_updated"
    MEMORY_DELETED = "memory_deleted"
    MEMORY_CONSOLIDATED = "memory_consolidated"
    CONTEXT_UPDATED = "context_updated"

    # Task events
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    
    # Skill events
    SKILL_REGISTERED = "skill_registered"
    SKILL_UNREGISTERED = "skill_unregistered"
    SKILL_STARTED = "skill_started"
    SKILL_COMPLETED = "skill_completed"
    SKILL_FAILED = "skill_failed"
    
    # User interaction events
    USER_SPOKE = "user_spoke"
    SPEECH_STARTED = "speech_started"
    SPEECH_ENDED = "speech_ended"
    VOICE_COMMAND_RECOGNIZED = "voice_command_recognized"
    RESPONSE_GENERATED = "response_generated"
    RESPONSE_SPOKEN = "response_spoken"
    
    # Action events
    ACTION_REQUESTED = "action_requested"
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"
    
    # Sensor events
    IMAGE_CAPTURED = "image_captured"
    AUDIO_CAPTURED = "audio_captured"
    POSE_UPDATED = "pose_updated"
    DIAGNOSTIC_UPDATE = "diagnostic_update"
    FACE_RECOGNIZED = "face_recognized"
    
    # External service events
    LLM_REQUEST_STARTED = "llm_request_started"
    LLM_REQUEST_COMPLETED = "llm_request_completed"
    HA_CONNECTED = "ha_connected"
    HA_DISCONNECTED = "ha_disconnected"
    HA_EVENT_RECEIVED = "ha_event_received"
    
    # Navigation events
    NAVIGATION_STARTED = "navigation_started"
    NAVIGATION_COMPLETED = "navigation_completed"
    NAVIGATION_FAILED = "navigation_failed"
    OBSTACLE_DETECTED = "obstacle_detected"

    # TTS events
    TTS_STARTED = "tts_started"
    TTS_COMPLETED = "tts_completed"
    TTS_FAILED = "tts_failed"
    TTS_AUDIO_BUFFER = "tts_audio_buffer"
    LIVE_AUDIO_CHUNK = "live_audio_chunk"
    LIVE_TURN_COMPLETE = "live_turn_complete"  # [v5.9] Gemini ha completato un turno autonomo da audio streaming

    # ASR events
    ASR_STARTED = "asr_started"
    ASR_STOPPED = "asr_stopped"


@dataclass
class Event:
    """Event data structure."""
    event_type: EventType
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    source: str = "system"
    event_id: str = ""
    
    def __post_init__(self):
        if not self.event_id:
            import uuid
            self.event_id = str(uuid.uuid4())[:8]


class EventHandler:
    """
    Wrapper for event handlers with weak reference support.
    Prevents memory leaks from dangling references.
    """
    
    def __init__(self, callback: Callable, is_async: bool = False, weak_ref: bool = True):
        self._is_async = is_async
        self._is_method = False
        self._callback_ref = None
        self._func_name = None
        self._callback = None
        
        # Create weak reference if possible and requested
        if weak_ref and hasattr(callback, '__self__'):
            self._callback_ref = weakref.ref(callback.__self__)
            self._func_name = callback.__func__.__name__
            self._is_method = True
        else:
            self._callback = callback
    
    @property
    def is_async(self) -> bool:
        return self._is_async
    
    @property
    def callback(self) -> Optional[Callable]:
        """Get the callback, returning None if weak reference is dead."""
        if self._is_method:
            instance = self._callback_ref()
            if instance is None:
                return None
            return getattr(instance, self._func_name, None)
        return self._callback
    
    def __call__(self, event: Event) -> Any:
        """Call the handler if it's still alive."""
        cb = self.callback
        if cb is None:
            return None
        return cb(event)
    
    def __eq__(self, other):
        if isinstance(other, EventHandler):
            return self.callback == other.callback
        return self.callback == other


class EventBus:
    """
    Thread-safe event bus with support for async/sync handlers.
    
    Features:
    - Weak references to prevent memory leaks
    - Both sync and async handlers
    - Event history for debugging
    - Statistics tracking
    - Priority-based handler ordering
    
    Usage:
        bus = EventBus()
        
        # Subscribe
        bus.subscribe(EventType.USER_SPOKE, my_handler)
        
        # Publish
        bus.publish(EventType.USER_SPOKE, {"text": "Hello"})
        
        # Async publish
        await bus.publish_async(EventType.USER_SPOKE, {"text": "Hello"})
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, max_history: int = 1000):
        if hasattr(self, '_initialized') and self._initialized:
            return
            
        self._handlers: Dict[EventType, List[EventHandler]] = {}
        self._handler_lock = threading.RLock()
        self._event_history: deque = deque(maxlen=max_history)
        self._max_history = max_history
        
        # Statistics
        self._events_published = 0
        self._handlers_called = 0
        self._errors_count = 0
        
        self._initialized = True
    
    def subscribe(self, event_type: EventType, handler: Callable, 
                  is_async: bool = None, priority: int = 0) -> None:
        """
        Subscribe to an event type.
        
        Args:
            event_type: Type of event to subscribe to
            handler: Callback function (sync or async)
            is_async: Whether the handler is async (auto-detected if None)
            priority: Handler priority (higher = called first)
        """
        # Auto-detect async
        if is_async is None:
            is_async = asyncio.iscoroutinefunction(handler)
        
        with self._handler_lock:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            
            # Create handler wrapper
            handler_wrapper = EventHandler(handler, is_async, weak_ref=True)
            
            # Check for duplicates
            for existing in self._handlers[event_type]:
                if existing.callback == handler:
                    return
            
            self._handlers[event_type].append(handler_wrapper)
    
    def unsubscribe(self, event_type: EventType, handler: Callable) -> bool:
        """
        Unsubscribe a handler from an event type.
        
        Returns:
            True if handler was found and removed
        """
        with self._handler_lock:
            if event_type not in self._handlers:
                return False
            
            # Find and remove the handler
            handlers = self._handlers[event_type]
            for i, h in enumerate(handlers):
                if h.callback == handler:
                    handlers.pop(i)
                    
                    # Clean up empty lists
                    if not handlers:
                        del self._handlers[event_type]
                    return True
            
            return False
    
    def publish(self, event_type: EventType, data: Dict[str, Any] = None, 
                source: str = "system") -> Event:
        """
        Publish an event synchronously.
        
        Args:
            event_type: Type of event
            data: Event data dictionary
            source: Source of the event
            
        Returns:
            The published event
        """
        event = Event(
            event_type=event_type,
            data=data or {},
            source=source
        )
        
        # Store in history
        self._event_history.append(event)
        self._events_published += 1
        
        # Get handlers for this event type
        handlers = self._get_handlers(event_type)
        
        # Call handlers
        for handler in handlers:
            try:
                if handler.is_async:
                    # Schedule async handler in background
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self._call_async_handler(handler, event))
                    except RuntimeError:
                        # No running loop, run in new thread
                        threading.Thread(
                            target=self._run_async_in_thread,
                            args=(handler, event),
                            daemon=True
                        ).start()
                else:
                    # Call sync handler directly
                    handler(event)
                    self._handlers_called += 1
            except Exception as e:
                self._errors_count += 1
                print(f"Error in event handler for {event_type}: {str(e)}")
        
        return event
    
    async def publish_async(self, event_type: EventType, data: Dict[str, Any] = None,
                           source: str = "system") -> Event:
        """
        Publish an event asynchronously.
        
        Args:
            event_type: Type of event
            data: Event data dictionary
            source: Source of the event
            
        Returns:
            The published event
        """
        event = Event(
            event_type=event_type,
            data=data or {},
            source=source
        )
        
        # Store in history
        self._event_history.append(event)
        self._events_published += 1
        
        # Get handlers for this event type
        handlers = self._get_handlers(event_type)
        
        # Call handlers
        for handler in handlers:
            try:
                if handler.is_async:
                    result = handler(event)
                    if inspect.isawaitable(result):
                        await result
                else:
                    # Run sync handler in thread pool
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, handler, event)
                
                self._handlers_called += 1
            except Exception as e:
                self._errors_count += 1
                print(f"Error in event handler for {event_type}: {str(e)}")
        
        return event
    
    def _get_handlers(self, event_type: EventType) -> List[EventHandler]:
        """Get handlers for an event type, cleaning up dead references."""
        with self._handler_lock:
            if event_type not in self._handlers:
                return []
            
            # Filter out dead handlers
            live_handlers = []
            for handler in self._handlers[event_type]:
                if handler.callback is not None:
                    live_handlers.append(handler)
            
            self._handlers[event_type] = live_handlers
            return live_handlers.copy()
    
    async def _call_async_handler(self, handler: EventHandler, event: Event) -> None:
        """Call an async handler."""
        try:
            cb = handler.callback
            if cb is None:
                return
            
            result = cb(event)
            if inspect.isawaitable(result):
                await result
            
            self._handlers_called += 1
        except Exception as e:
            self._errors_count += 1
            print(f"Error in async event handler: {str(e)}")
    
    def _run_async_in_thread(self, handler: EventHandler, event: Event) -> None:
        """Run async handler in a new event loop in a thread."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._call_async_handler(handler, event))
            loop.close()
        except Exception as e:
            self._errors_count += 1
            print(f"Error running async handler in thread: {str(e)}")
    
    def get_subscribers(self, event_type: EventType) -> List[Callable]:
        """Get all live subscribers for an event type."""
        handlers = self._get_handlers(event_type)
        return [h.callback for h in handlers if h.callback is not None]
    
    def get_event_history(self, limit: int = 100, 
                         event_type: EventType = None) -> List[Event]:
        """
        Get recent event history.
        
        Args:
            limit: Maximum events to return
            event_type: Filter by event type (optional)
        """
        history = list(self._event_history)
        
        if event_type:
            history = [e for e in history if e.event_type == event_type]
        
        return history[-limit:]
    
    def clear_history(self) -> None:
        """Clear event history."""
        self._event_history.clear()
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get event bus statistics."""
        with self._handler_lock:
            handler_counts = {}
            for event_type, handlers in self._handlers.items():
                active = sum(1 for h in handlers if h.callback is not None)
                handler_counts[event_type.value] = active
            
            return {
                "events_published": self._events_published,
                "handlers_called": self._handlers_called,
                "errors_count": self._errors_count,
                "active_handlers_by_type": handler_counts,
                "event_history_size": len(self._event_history),
                "total_event_types_subscribed": len(self._handlers)
            }
    
    def cleanup(self) -> int:
        """
        Clean up dead references.
        
        Returns:
            Number of dead handlers removed
        """
        removed = 0
        with self._handler_lock:
            for event_type in list(self._handlers.keys()):
                original_count = len(self._handlers[event_type])
                self._handlers[event_type] = [
                    h for h in self._handlers[event_type]
                    if h.callback is not None
                ]
                removed += original_count - len(self._handlers[event_type])
                
                # Remove empty lists
                if not self._handlers[event_type]:
                    del self._handlers[event_type]
        
        return removed
    
    def reset(self) -> None:
        """Reset the event bus (useful for testing)."""
        with self._handler_lock:
            self._handlers.clear()
            self._event_history.clear()
            self._events_published = 0
            self._handlers_called = 0
            self._errors_count = 0
