from typing import Dict, Any, List, Optional, Tuple
import time
from robot_ai.utils.logging_utils import get_logger

class EnvironmentSnapshot:
    """
    Aggregates environment state for the CAG module.
    Maintains semantic room localization, active map, covariance trace,
    and visual perception entities for token-efficient LLM prompt injection.
    """
    
    def __init__(self):
        self.logger = get_logger("EnvironmentSnapshot")
        self.room_name: str = "Unknown"
        self.location: Tuple[float, float] = (0.0, 0.0)
        self.map_name: str = "default"
        self.covariance_trace: float = 0.0
        self.dist_to_centroid: Optional[float] = None
        self.recognized_humans: List[str] = []
        self.visual_objects: List[str] = []
        self.smart_home_states: Dict[str, Any] = {}
        # Exploration State Tracking (Frontier Explorer & Hunt Mode)
        self.exploration_active: bool = False
        self.exploration_mode: str = "IDLE"  # IDLE, EXPLORE, HUNT
        self.exploration_target: Optional[str] = None
        self.remaining_frontiers: int = 0
        self.biometrics_summary: Optional[str] = None
        self.last_update: float = time.time()

    def update_location(
        self,
        room_name: str,
        location: Tuple[float, float],
        map_name: str = "default",
        covariance_trace: float = 0.0,
        dist_to_centroid: Optional[float] = None
    ) -> None:
        """
        Updates localization state with semantic room, map, and AMCL confidence.
        Backwards-compatible with (room_name, location).
        """
        self.room_name = room_name
        self.location = location
        self.map_name = map_name
        self.covariance_trace = covariance_trace
        self.dist_to_centroid = dist_to_centroid
        self.last_update = time.time()
        
    def update_perception(self, humans: List[str], objects: List[str]) -> None:
        self.recognized_humans = humans
        self.visual_objects = objects
        self.last_update = time.time()
        
    def update_smart_home(self, states: Dict[str, Any]) -> None:
        self.smart_home_states = states
        self.last_update = time.time()

    def update_exploration(
        self,
        active: bool,
        mode: str = "IDLE",
        target: Optional[str] = None,
        remaining_frontiers: int = 0
    ) -> None:
        """Updates autonomous exploration status for CAG LLM grounding."""
        self.exploration_active = bool(active)
        self.exploration_mode = str(mode).upper()
        self.exploration_target = target if target else None
        self.remaining_frontiers = max(0, int(remaining_frontiers))
        self.last_update = time.time()

    def update_biometrics(self, summary: str) -> None:
        """Updates multimodal biometric identity for CAG LLM grounding."""
        self.biometrics_summary = summary
        self.last_update = time.time()

    def to_text(self) -> str:
        """
        Returns a concise, token-efficient environmental summary for LLM CAG.
        Example: [ENV] Room: salotto (near centroid 0.4m) | Map: casa_piano1 | AMCL cov: 0.042 | Biometrics: Active: Luca (fused 95%)
        """
        if self.dist_to_centroid is not None:
            room_desc = f"{self.room_name} (near centroid {self.dist_to_centroid:.1f}m)"
        else:
            room_desc = f"{self.room_name}"
            
        parts = [
            f"Room: {room_desc}",
            f"Map: {self.map_name}",
            f"AMCL cov: {self.covariance_trace:.3f}"
        ]
        
        if self.biometrics_summary:
            parts.append(self.biometrics_summary.replace("[BIOMETRICS] ", ""))
        elif self.recognized_humans:
            parts.append(f"Humans: {','.join(self.recognized_humans)}")
            
        if self.visual_objects:
            objs = ",".join(self.visual_objects[:3])
            if len(self.visual_objects) > 3:
                objs += f" (+{len(self.visual_objects)-3} more)"
            parts.append(f"Objs: {objs}")

        if self.exploration_active:
            if self.exploration_mode == "HUNT" and self.exploration_target:
                parts.append(f"Exploration: HUNT({self.exploration_target}, frontiers={self.remaining_frontiers})")
            else:
                parts.append(f"Exploration: ACTIVE({self.exploration_mode}, frontiers={self.remaining_frontiers})")
            
        return f"[ENV] {' | '.join(parts)}"
