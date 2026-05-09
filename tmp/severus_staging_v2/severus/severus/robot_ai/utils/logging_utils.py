"""
Robot AI Utils - Logging
========================
Structured JSON logging with ROS 2 integration.
"""

import sys
import json
import time
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime
from logging.handlers import RotatingFileHandler


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""
    
    def __init__(self, include_timestamp: bool = True, include_extra: bool = True):
        super().__init__()
        self.include_timestamp = include_timestamp
        self.include_extra = include_extra
    
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        if self.include_timestamp:
            log_entry["timestamp"] = datetime.utcnow().isoformat() + "Z"
        
        # Add extra fields
        if self.include_extra:
            for key in ["event", "component", "action", "duration_ms", 
                       "error_type", "user_id", "session_id"]:
                if hasattr(record, key):
                    log_entry[key] = getattr(record, key)
        
        # Add exception info
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_entry, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Colored console formatter for development."""
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Add component prefix if available
        component = getattr(record, 'component', '')
        component_str = f"[{component}] " if component else ""
        
        return f"{color}{timestamp} {record.levelname:8}{self.RESET} {component_str}{record.getMessage()}"


class AILogger:
    """
    AI system logger with JSON output and ROS integration.
    
    Features:
    - Structured JSON logging
    - Rotating file handler
    - Console output with colors
    - Component tagging
    - Event tracking
    
    Usage:
        logger = AILogger("llm_service")
        logger.info("Request completed", duration_ms=150, tokens=500)
    """
    
    _loggers: Dict[str, 'AILogger'] = {}
    _lock = threading.RLock()
    _configured = False
    
    @classmethod
    def configure(
        cls,
        log_dir: str = str(Path.home() / ".robot_ai" / "logs"),
        level: str = "INFO",
        max_size_mb: int = 100,
        backup_count: int = 5,
        json_console: bool = False
    ):
        """Configure global logging settings."""
        with cls._lock:
            if cls._configured:
                return
            
            cls._log_dir = Path(log_dir)
            cls._log_dir.mkdir(parents=True, exist_ok=True)
            cls._level = getattr(logging, level.upper(), logging.INFO)
            cls._max_bytes = max_size_mb * 1024 * 1024
            cls._backup_count = backup_count
            cls._json_console = json_console
            cls._configured = True
    
    def __init__(self, component: str):
        self.component = component
        self._logger = logging.getLogger(f"robot_ai.{component}")
        
        # Configure if not done
        if not AILogger._configured:
            AILogger.configure()
        
        # Prevent duplicate handlers
        if not self._logger.handlers:
            self._setup_handlers()
    
    def _setup_handlers(self):
        """Set up log handlers."""
        self._logger.setLevel(AILogger._level)
        
        # Console handler
        console = logging.StreamHandler(sys.stdout)
        if AILogger._json_console:
            console.setFormatter(JSONFormatter())
        else:
            console.setFormatter(ConsoleFormatter())
        console.setLevel(AILogger._level)
        self._logger.addHandler(console)
        
        # File handler (JSON)
        try:
            log_file = AILogger._log_dir / f"{self.component}.log"
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=AILogger._max_bytes,
                backupCount=AILogger._backup_count
            )
            file_handler.setFormatter(JSONFormatter())
            file_handler.setLevel(logging.DEBUG)
            self._logger.addHandler(file_handler)
        except Exception:
            pass  # Skip file logging if directory not writable
    
    def _log(self, level: int, message: str, **kwargs):
        """Internal log method with extra fields."""
        kwargs['component'] = self.component
        self._logger.log(level, message, extra=kwargs)
    
    def debug(self, message: str, **kwargs):
        """Log debug message."""
        self._log(logging.DEBUG, message, **kwargs)
    
    def info(self, message: str, **kwargs):
        """Log info message."""
        self._log(logging.INFO, message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """Log warning message."""
        self._log(logging.WARNING, message, **kwargs)
    
    def error(self, message: str, exc_info: bool = False, **kwargs):
        """Log error message."""
        self._log(logging.ERROR, message, **kwargs)
        if exc_info:
            self._logger.exception(message, extra={'component': self.component})
    
    def critical(self, message: str, **kwargs):
        """Log critical message."""
        self._log(logging.CRITICAL, message, **kwargs)
    
    def event(self, event_name: str, **kwargs):
        """Log a named event with metadata."""
        kwargs['event'] = event_name
        self._log(logging.INFO, f"Event: {event_name}", **kwargs)
    
    def action(self, action_name: str, success: bool, duration_ms: float = None, **kwargs):
        """Log an action with result."""
        kwargs['action'] = action_name
        kwargs['success'] = success
        if duration_ms is not None:
            kwargs['duration_ms'] = duration_ms
        
        status = "completed" if success else "failed"
        self._log(logging.INFO if success else logging.WARNING, 
                 f"Action {action_name} {status}", **kwargs)


class TimedOperation:
    """
    Context manager for timing operations.
    
    Usage:
        logger = AILogger("llm")
        with TimedOperation(logger, "generate_response") as op:
            result = llm.generate(prompt)
            op.set_metadata(tokens=result.tokens)
    """
    
    def __init__(self, logger: AILogger, operation: str, log_level: str = "info"):
        self.logger = logger
        self.operation = operation
        self.log_level = log_level
        self.start_time = None
        self.metadata: Dict[str, Any] = {}
        self.success = True
    
    def __enter__(self) -> 'TimedOperation':
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (time.perf_counter() - self.start_time) * 1000
        self.success = exc_type is None
        
        log_method = getattr(self.logger, self.log_level if self.success else "error")
        log_method(
            f"Operation '{self.operation}' {'completed' if self.success else 'failed'}",
            duration_ms=round(duration_ms, 2),
            success=self.success,
            **self.metadata
        )
        
        if exc_val:
            self.metadata['error'] = str(exc_val)
        
        return False  # Don't suppress exceptions
    
    def set_metadata(self, **kwargs):
        """Add metadata to the operation log."""
        self.metadata.update(kwargs)


def get_logger(component: str) -> AILogger:
    """Get or create a logger for a component."""
    with AILogger._lock:
        if component not in AILogger._loggers:
            AILogger._loggers[component] = AILogger(component)
        return AILogger._loggers[component]
