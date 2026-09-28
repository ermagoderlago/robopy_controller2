"""
Robot AI Skills - Frontier Exploration Skill
=============================================
Skill per l'esplorazione autonoma dell'ambiente basata su frontiere (explore_lite)
e modalità caccia target semantica (HUNT mode) integrata con la memoria TRINITY (CAG / MAG / RAG).
"""

import re
import asyncio
from typing import Any, Dict, Optional, Tuple

from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability

try:
    import rclpy
    from std_msgs.msg import Bool, String
    from geometry_msgs.msg import PoseStamped
except ImportError:
    Bool = None
    String = None
    PoseStamped = None


class FrontierExplorationSkill(BaseSkill):
    """
    Skill for autonomous frontier-based exploration and semantic target hunting.
    Directly interfaces with frontier_explorer_node, Nav2, and TRINITY memory.
    """

    EXPLORE_PATTERNS = [
        re.compile(r'\b(esplora|esplorare|esplorazione|ricognizione|perlustra|perlustrazione)\b', re.IGNORECASE),
        re.compile(r'\b(fai\s+un\s+giro|fai\s+un\'?esplorazione|fai\s+una\s+ricognizione|vai\s+in\s+esplorazione|inizia\s+a\s+esplorare|comincia\s+a\s+esplorare|avvia\s+l\'?esplorazione)\b', re.IGNORECASE),
        re.compile(r'\b(gira\s+e\s+mappa|mappa\s+la\s+casa|mappa\s+la\s+stanza|mappa\s+l\'?ambiente)\b', re.IGNORECASE),
        re.compile(r'\b(nomad|nomade|nomadi)\b', re.IGNORECASE)  # Legacy fallback
    ]

    HUNT_PATTERNS = [
        re.compile(r'\b(trova|trovami|cerca|cercami|dov\'?è|dove\s+si\s+trova|rintraccia|scova)\s+(?:(?:il|lo|la|i|gli|le|l\'|un|uno|una|un\'|mio|mia|miei|mie)\s+)?([a-zA-Z0-9_àèéìòù]+)', re.IGNORECASE),
        re.compile(r'\b(vai\s+a\s+cercare|inizia\s+a\s+cercare|mettiti\s+a\s+cercare)\s+(?:(?:il|lo|la|i|gli|le|l\'|un|uno|una|un\'|mio|mia|miei|mie)\s+)?([a-zA-Z0-9_àèéìòù]+)', re.IGNORECASE)
    ]

    STOP_PATTERNS = [
        re.compile(r'\b(ferma|stop|basta|annulla|interrompi|blocca)\s+(l\'?esplorazione|esplorazione|esplorare|la\s+ricerca|cercare|il\s+giro|la\s+ricognizione|nomad)\b', re.IGNORECASE),
        re.compile(r'\b(fermati|stop\s+esplorazione|stop\s+ricerca|basta\s+esplorare|basta\s+cercare)\b', re.IGNORECASE)
    ]

    def __init__(self, ros_node=None, memory_store=None, trinity_engine=None, nav_client=None):
        super().__init__()
        self.ros_node = ros_node
        self.memory_store = memory_store
        self.trinity_engine = trinity_engine
        self.nav_client = nav_client
        self.is_exploring = False
        self.current_target: Optional[str] = None

        self.pub_enable = None
        self.pub_target = None
        if self.ros_node is not None:
            self._setup_ros_interfaces()

    def _setup_ros_interfaces(self):
        try:
            if Bool is not None:
                self.pub_enable = self.ros_node.create_publisher(Bool, '/exploration/enable', 10)
            if String is not None:
                self.pub_target = self.ros_node.create_publisher(String, '/exploration/search_target', 10)
        except Exception:
            pass

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="frontier_exploration",
            description="Avvia o ferma l'esplorazione autonoma dell'ambiente (mappatura frontiere) oppure la ricerca di un target/persona (modalità HUNT) con integrazione memoria TRINITY.",
            version="2.0.0",
            keywords=["esplora", "esplorazione", "mappa", "trova", "cerca", "ricognizione", "frontiere"],
            priority=35,
            requires_nav=True,
            capabilities=[Capability.NAV_MOVE, Capability.MEMORY_RW]
        )

    def get_parameters_schema(self) -> Dict[str, Any]:
        """Schema per function calling LLM (Gemini Live API e REST)."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start_explore", "search_target", "stop"],
                    "description": "Azione: 'start_explore' per mappatura autonoma, 'search_target' per cercare un oggetto/persona specifica, 'stop' per interrompere il moto."
                },
                "target": {
                    "type": "string",
                    "description": "Nome dell'oggetto o persona da cercare (solo per action='search_target', es. 'chiavi', 'Marco', 'persona', 'sedia')."
                }
            },
            "required": ["action"]
        }

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        clean_text = (text or "").lower().strip()
        if any(p.search(clean_text) for p in self.STOP_PATTERNS):
            return 0.99
        if any(p.search(clean_text) for p in self.HUNT_PATTERNS):
            return 0.95
        if any(p.search(clean_text) for p in self.EXPLORE_PATTERNS):
            return 0.92
        return 0.0

    def _extract_target(self, text: str) -> Optional[str]:
        clean_text = (text or "").lower().strip()
        stop_words = {"la", "il", "lo", "le", "i", "gli", "un", "uno", "una", "casa", "stanza", "qui", "subito", "per", "favore"}
        for p in self.HUNT_PATTERNS:
            m = p.search(clean_text)
            if m:
                # Group 2 is the target name
                raw_target = m.group(2).strip().lower()
                if raw_target not in stop_words:
                    return raw_target
        return None

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        context = context or {}
        action = context.get("action", "").lower().strip()
        target = context.get("target", "").lower().strip()
        clean_text = (text or "").lower().strip()

        # 1. Stop exploration
        if action == "stop" or (clean_text and any(p.search(clean_text) for p in self.STOP_PATTERNS)):
            return self.stop_exploration()

        # 2. Search target (HUNT mode)
        if not target and clean_text:
            target = self._extract_target(clean_text) or ""

        if action == "search_target" or target:
            return await self.start_hunt(target)

        # 3. Pure frontier exploration (EXPLORE mode)
        return await self.start_explore()

    async def start_explore(self) -> SkillResult:
        """Avvia la mappatura autonoma di tutte le frontiere aperte."""
        if self.ros_node is not None:
            if self.pub_enable is None or self.pub_target is None:
                self._setup_ros_interfaces()

            # Resetta target (modalità EXPLORE pura)
            if self.pub_target is not None:
                t_msg = String()
                t_msg.data = ""
                self.pub_target.publish(t_msg)

            # Abilita esploratore
            if self.pub_enable is not None:
                e_msg = Bool()
                e_msg.data = True
                self.pub_enable.publish(e_msg)

        self.is_exploring = True
        self.current_target = None
        speak_msg = "Avvio l'esplorazione autonoma con il motore a frontiere. Mappo l'ambiente ed evito le zone già visitate."

        return SkillResult(
            success=True,
            message="Esplorazione frontiere avviata.",
            speak=speak_msg,
            data={"mode": "EXPLORE"}
        )

    async def start_hunt(self, target: str) -> SkillResult:
        """Cerca un target semantico consultando prima MAG e poi attivando HUNT mode su frontiere."""
        target_clean = (target or "target").strip().lower()
        self.current_target = target_clean

        # Step A: Interroga MAG per coordinate note recenti
        known_location = None
        if self.trinity_engine and hasattr(self.trinity_engine, "find_target_location"):
            known_location = self.trinity_engine.find_target_location(target_clean)

        if known_location and "coordinates" in known_location:
            x, y = known_location["coordinates"]
            speak_msg = f"Ricordo di aver visto '{target_clean}' alle coordinate ({x:.2f}, {y:.2f}). Mi dirigo sul posto per verificare."

            # Attiva comunque target listener in explorer
            if self.ros_node is not None:
                if self.pub_target is not None:
                    t_msg = String()
                    t_msg.data = target_clean
                    self.pub_target.publish(t_msg)

            return SkillResult(
                success=True,
                message=f"Coordinate note trovate in memoria MAG per '{target_clean}'.",
                speak=speak_msg,
                data={
                    "mode": "HUNT_KNOWN_LOCATION",
                    "target": target_clean,
                    "coordinates": [x, y]
                }
            )

        # Step B: Nessuna coordinata nota -> attiva esplorazione con bias semantico verso il target
        if self.ros_node is not None:
            if self.pub_enable is None or self.pub_target is None:
                self._setup_ros_interfaces()

            if self.pub_target is not None:
                t_msg = String()
                t_msg.data = target_clean
                self.pub_target.publish(t_msg)

            if self.pub_enable is not None:
                e_msg = Bool()
                e_msg.data = True
                self.pub_enable.publish(e_msg)

        self.is_exploring = True
        speak_msg = f"Avvio la ricerca attiva di '{target_clean}'. Esploro le aree sconosciute e monitoro con la visione semantica."

        return SkillResult(
            success=True,
            message=f"Modalità HUNT attivata per '{target_clean}'.",
            speak=speak_msg,
            data={
                "mode": "HUNT",
                "target": target_clean
            }
        )

    def stop_exploration(self) -> SkillResult:
        """Ferma l'esplorazione e la navigazione attiva."""
        if self.ros_node is not None:
            if self.pub_enable is None:
                self._setup_ros_interfaces()

            if self.pub_enable is not None:
                e_msg = Bool()
                e_msg.data = False
                self.pub_enable.publish(e_msg)

            if self.pub_target is not None:
                t_msg = String()
                t_msg.data = ""
                self.pub_target.publish(t_msg)

        self.is_exploring = False
        self.current_target = None
        speak_msg = "Esplorazione e ricerca interrotte. Mi fermo qui."

        return SkillResult(
            success=True,
            message="Esplorazione fermata con successo.",
            speak=speak_msg,
            data={"mode": "IDLE"}
        )
