import time
from collections import deque
from typing import Dict, Any, Optional
from robot_ai.utils import get_logger

class ErrorContextTracker:
    """
    Tracks recent errors and exceptions for the CAG module.
    """
    
    def __init__(self, maxlen: int = 10):
        self.logger = get_logger("ErrorContextTracker")
        self.errors = deque(maxlen=maxlen)

    def record_error(self, source: str, error_msg: str, traceback_str: Optional[str] = None, severity: str = "ERROR"):
        """Records a new error."""
        error_entry = {
            "timestamp": time.time(),
            "source": source,
            "message": error_msg,
            "traceback": traceback_str,
            "severity": severity
        }
        self.errors.append(error_entry)
        self.logger.debug(f"Recorded error from {source}: {error_msg}")

    def get_last_error_context(self) -> Optional[Dict[str, Any]]:
        """Returns the most recent error context."""
        if not self.errors:
            return None
        return self.errors[-1]

    def to_text(self) -> str:
        """Returns a compact string representation of the last error."""
        last_error = self.get_last_error_context()
        if not last_error:
            return "[ERR] No recent errors."
        
        source = last_error["source"]
        msg = last_error["message"]
        severity = last_error["severity"]
        
        # Keep it short, truncate message if needed
        short_msg = msg[:80] + "..." if len(msg) > 80 else msg
        return f"[ERR] {severity} in {source}: {short_msg}"
