"""
Robot AI Package
================
Comprehensive AI system for robot with Gemini, RAG memory, 
Home Assistant integration, and autonomous capabilities.
"""

__version__ = "1.0.0"
__author__ = "RoboPy Team"

# Lazy imports to avoid circular dependencies
def get_config_manager():
    from .core.config_manager import ConfigManager
    return ConfigManager

def get_event_bus():
    from .core.event_bus import EventBus
    return EventBus

def get_state_machine():
    from .core.state_machine import StateMachine
    return StateMachine
