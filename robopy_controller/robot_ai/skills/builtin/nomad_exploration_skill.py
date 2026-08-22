"""
Robot AI Skills - NOMAD Autonomous Exploration Skill
====================================================
Skill per l'esplorazione autonoma e navigazione visiva guidata dal modello NOMAD.
"""

import re
import asyncio
from typing import Any, Dict, Optional

from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode

import rclpy
from std_msgs.msg import Bool, String
from sensor_msgs.msg import Image


class NomadExplorationSkill(BaseSkill):
    """
    Skill for vision-based autonomous exploration using NOMAD foundation model.
    """

    EXPLORE_PATTERNS = [
        re.compile(r'\b(nomad|nomade|nomadi|norman|noma)\b', re.IGNORECASE),
        re.compile(r'\b(esplora|esplorare|esplorazione|ricognizione|perlustra|perlustrazione)\b', re.IGNORECASE),
        re.compile(r'\b(fai\s+un\s+giro|fai\s+un\'?esplorazione|fai\s+una\s+ricognizione|vai\s+in\s+esplorazione|inizia\s+a\s+esplorare|comincia\s+a\s+esplorare|avvia\s+l\'?esplorazione)\b', re.IGNORECASE),
        re.compile(r'\b(gira\s+e\s+mappa|mappa\s+la\s+casa|mappa\s+la\s+stanza|mappa\s+l\'?ambiente)\b', re.IGNORECASE)
    ]

    STOP_EXPLORE_PATTERNS = [
        re.compile(r'\b(ferma|stop|basta|annulla|interrompi|blocca)\s+(l\'?esplorazione|esplorazione|esplorare|nomad|nomade|il\s+giro|la\s+ricognizione)\b', re.IGNORECASE),
        re.compile(r'\b(fermati|stop\s+nomad|stop\s+esplorazione|basta\s+esplorare)\b', re.IGNORECASE)
    ]

    def __init__(self, ros_node=None, memory_store=None, visual_memory=None):
        super().__init__()
        self.ros_node = ros_node
        self.memory_store = memory_store
        self.visual_memory = visual_memory
        self.is_exploring = False

        # ROS 2 Publishers if ros_node is available
        self.pub_enable = None
        self.pub_mode = None
        if self.ros_node is not None:
            self._setup_ros_interfaces()

    def _setup_ros_interfaces(self):
        try:
            self.pub_enable = self.ros_node.create_publisher(Bool, '/nomad/enable', 10)
            self.pub_mode = self.ros_node.create_publisher(String, '/nomad/set_mode', 10)
        except Exception:
            pass

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="nomad_exploration",
            description="Esplora autonomamente la stanza e crea mappe usando il modello di visione fondazionale NOMAD senza LiDAR",
            version="1.0.0",
            keywords=["nomad", "nomade", "esplora", "esplorazione", "ricognizione", "perlustra", "mappa"],
            priority=25,  # Highest priority for exploration intents
            requires_nav=True
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        score = 0.0
        clean_text = text.lower().strip()
        if any(p.search(clean_text) for p in self.STOP_EXPLORE_PATTERNS):
            return 0.99
        if any(p.search(clean_text) for p in self.EXPLORE_PATTERNS):
            return 0.98
        return score

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        # Check stop request
        if any(p.search(text) for p in self.STOP_EXPLORE_PATTERNS):
            return self._stop_nomad_exploration()

        # Start exploration
        return await self._start_nomad_exploration()

    async def _start_nomad_exploration(self) -> SkillResult:
        if self.ros_node is not None:
            if self.pub_enable is None or self.pub_mode is None:
                self._setup_ros_interfaces()

            # Publish enable & mode explore
            mode_msg = String()
            mode_msg.data = "EXPLORE"
            self.pub_mode.publish(mode_msg)

            enable_msg = Bool()
            enable_msg.data = True
            self.pub_enable.publish(enable_msg)

        self.is_exploring = True
        msg = "Avvio l'esplorazione autonoma con il modello visivo NOMAD. Mappo l'ambiente e memorizzo i punti salienti."
        
        return SkillResult(
            success=True,
            message="Esplorazione NOMAD avviata con successo.",
            speak=msg
        )

    def _stop_nomad_exploration(self) -> SkillResult:
        if self.ros_node is not None:
            if self.pub_enable is None or self.pub_mode is None:
                self._setup_ros_interfaces()

            stop_msg = String()
            stop_msg.data = "STOP"
            self.pub_mode.publish(stop_msg)

            enable_msg = Bool()
            enable_msg.data = False
            self.pub_enable.publish(enable_msg)

        self.is_exploring = False
        msg = "Esplorazione NOMAD interrotta. Mi fermo qui."

        return SkillResult(
            success=True,
            message="Esplorazione NOMAD fermata.",
            speak=msg
        )
