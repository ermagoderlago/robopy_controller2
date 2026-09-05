"""
Robot AI Builtin Skills Package
================================
Built-in skill implementations.
"""

try:
    from .ha_skill import HomeAssistantSkill
except ImportError:
    HomeAssistantSkill = None

try:
    from .navigation_skill import NavigationSkill
except ImportError:
    NavigationSkill = None

try:
    from .nightly_dream_skill import NightlyDreamSkill
except ImportError:
    NightlyDreamSkill = None

try:
    from .nomad_exploration_skill import NomadExplorationSkill
except ImportError:
    NomadExplorationSkill = None

__all__ = [
    "HomeAssistantSkill",
    "NavigationSkill",
    "NightlyDreamSkill",
    "NomadExplorationSkill",
]
