import time
import threading
from typing import Dict, Any, Optional
from robot_ai.utils import get_logger

from .cag_hardware_collector import HardwareStateCollector
from .cag_ros_inspector import ROSTopologyInspector
from .cag_error_tracker import ErrorContextTracker
from .cag_environment import EnvironmentSnapshot

class ContextAggregator:
    """
    Coordinates all context collectors with a TTL cache.
    Thread-safe.
    """
    
    def __init__(self, ros_node=None, ttl_seconds: float = 5.0):
        self.logger = get_logger("ContextAggregator")
        self.ttl = ttl_seconds
        
        self.hardware = HardwareStateCollector()
        self.ros = ROSTopologyInspector(node=ros_node)
        self.errors = ErrorContextTracker()
        self.environment = EnvironmentSnapshot()
        
        self._lock = threading.RLock()
        self._cache = {}
        self._last_update_time = 0.0

    def _refresh_cache_if_needed(self):
        now = time.time()
        if now - self._last_update_time > self.ttl:
            self._cache["hardware"] = self.hardware.get_hardware_summary()
            self._cache["ros"] = self.ros.get_topology_summary()
            self._cache["last_error"] = self.errors.get_last_error_context()
            
            # Text representations
            self._cache["hardware_text"] = self.hardware.to_text()
            self._cache["ros_text"] = self.ros.to_text()
            self._cache["error_text"] = self.errors.to_text()
            self._cache["env_text"] = self.environment.to_text()
            
            self._last_update_time = now

    def get_snapshot(self) -> Dict[str, Any]:
        """Returns the full context snapshot."""
        with self._lock:
            self._refresh_cache_if_needed()
            return dict(self._cache)

    def to_prompt_section(self, max_tokens: int = 400) -> str:
        """
        Returns a formatted string suitable for appending to an LLM prompt.
        """
        with self._lock:
            self._refresh_cache_if_needed()
            
            lines = [
                "--- CURRENT SYSTEM CONTEXT ---",
                self._cache["hardware_text"],
                self._cache["ros_text"],
                self._cache["env_text"],
                self._cache["error_text"],
                "------------------------------"
            ]
            
            text = "\n".join(lines)
            
            # Very rough token approximation (1 token ~= 4 chars)
            if len(text) > max_tokens * 4:
                text = text[:max_tokens * 4] + "... [TRUNCATED]"
                
            return text
