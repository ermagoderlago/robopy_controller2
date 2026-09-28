"""
Robot AI Skills - NOMAD Autonomous Exploration Skill (Compatibility Layer)
==========================================================================
Re-indirizza la legacy skill NOMAD verso il motore di frontiere (FrontierExplorationSkill)
e la caccia target semantica TRINITY, dismettendo completamente la pipeline NoMaD.
"""

from typing import Any, Dict, Optional
from .frontier_exploration_skill import FrontierExplorationSkill
from ..base_skill import SkillMetadata, SkillResult


class NomadExplorationSkill(FrontierExplorationSkill):
    """
    Backward-compatibility wrapper for NomadExplorationSkill.
    Redirects calls to FrontierExplorationSkill using explore_lite and TRINITY.
    """

    def __init__(self, ros_node=None, memory_store=None, visual_memory=None, trinity_engine=None):
        super().__init__(
            ros_node=ros_node,
            memory_store=memory_store,
            trinity_engine=trinity_engine
        )
        self.visual_memory = visual_memory

    def get_metadata(self) -> SkillMetadata:
        meta = super().get_metadata()
        meta.name = "nomad_exploration"
        meta.description = (
            "Avvia o ferma l'esplorazione autonoma e la ricerca semantica con il motore di navigazione Marcus. "
            "Usare per richieste come 'esplora la casa', 'fai un giro', 'cerca [persona/oggetto]', 'ferma esplorazione'."
        )
        meta.keywords = ["nomad", "esplora", "esplorazione", "ricognizione", "perlustra", "mappa", "cerca", "trova"]
        meta.priority = 35
        return meta
