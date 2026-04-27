"""
Robot AI Skills Package
========================
Sistema di skill per le capacità del robot.
"""

from .base_skill import (
    BaseSkill,
    Capability,
    SkillMetadata,
    SkillResult,
    SkillErrorCode,
)
from .skill_registry import (
    SkillRegistry,
)

__all__ = [
    "BaseSkill",
    "Capability",
    "SkillMetadata",
    "SkillResult",
    "SkillErrorCode",
    "SkillRegistry",
]
