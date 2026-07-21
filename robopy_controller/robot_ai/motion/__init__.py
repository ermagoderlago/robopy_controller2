"""
Robot AI Motion Package
======================
Framework per la gestione del movimento relativo, primitivi di movimento e sequenze cinematiche.
"""

from .motion_manager import MotionPrimitive, MotionSequence, MotionManager, MotionType

__all__ = [
    "MotionPrimitive",
    "MotionSequence",
    "MotionManager",
    "MotionType",
]
