from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple
from collections import deque
from robot_ai.core import EventBus, EventType
from robot_ai.utils import get_logger

@dataclass
class WorldModel:
    rooms: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    current_user: Optional[Dict[str, Any]] = None
    battery_level: Optional[float] = None
    position: Optional[Tuple[float, float]] = None
    current_task: Optional[str] = None
    recent_events: deque = field(default_factory=lambda: deque(maxlen=10))
    recent_interactions: deque = field(default_factory=lambda: deque(maxlen=5))

    def to_prompt_section(self) -> str:
        parts = ["[WORLD MODEL]"]
        if self.battery_level is not None:
            parts.append(f"- Batteria: {self.battery_level}%")
        if self.position is not None:
            x, y = self.position
            parts.append(f"- Posizione: x={x:.2f}, y={y:.2f}")
        if self.current_task:
            parts.append(f"- Task Corrente: {self.current_task}")
        if self.recent_events:
            parts.append("- Eventi Recenti:")
            for ev in list(self.recent_events)[-3:]:
                parts.append(f"  * {ev}")
        if self.recent_interactions:
            parts.append("- Interazioni Recenti:")
            for inter in list(self.recent_interactions)[-3:]:
                parts.append(f"  * {inter}")
        return "\n".join(parts)

class WorldModelUpdater:
    def __init__(self, world_model: WorldModel, event_bus: EventBus):
        self.world_model = world_model
        self.event_bus = event_bus
        self._logger = get_logger("world_updater")
        self._subscribe_events()

    def _subscribe_events(self):
        self.event_bus.subscribe(EventType.DIAGNOSTIC_UPDATE, self._on_diagnostic)
        self.event_bus.subscribe(EventType.FACE_RECOGNIZED, self._on_face_recognized)

    async def _on_diagnostic(self, event_data: dict):
        if "battery" in event_data:
            self.world_model.battery_level = event_data["battery"]

    async def _on_face_recognized(self, event_data: dict):
        name = event_data.get("name", "Unknown")
        self.world_model.current_user = {"name": name}
        self.world_model.recent_events.append(f"Riconosciuto utente: {name}")
        self._logger.info(f"World model updated: recognized user {name}")
