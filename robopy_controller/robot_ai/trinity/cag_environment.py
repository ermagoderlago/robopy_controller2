from typing import Dict, Any, List
import time
from robot_ai.utils import get_logger

class EnvironmentSnapshot:
    """
    Aggregates environment state for the CAG module.
    """
    
    def __init__(self):
        self.logger = get_logger("EnvironmentSnapshot")
        self.room_name = "Unknown"
        self.location = (0.0, 0.0)
        self.recognized_humans = []
        self.visual_objects = []
        self.smart_home_states = {}
        self.last_update = time.time()

    def update_location(self, room_name: str, location: tuple):
        self.room_name = room_name
        self.location = location
        self.last_update = time.time()
        
    def update_perception(self, humans: List[str], objects: List[str]):
        self.recognized_humans = humans
        self.visual_objects = objects
        self.last_update = time.time()
        
    def update_smart_home(self, states: Dict[str, Any]):
        self.smart_home_states = states
        self.last_update = time.time()

    def to_text(self) -> str:
        """Returns a compact string representation of the environment."""
        humans = ",".join(self.recognized_humans) if self.recognized_humans else "None"
        objs = ",".join(self.visual_objects[:3]) if self.visual_objects else "None" # Only top 3
        if len(self.visual_objects) > 3:
            objs += f" (+{len(self.visual_objects)-3} more)"
            
        return f"[ENV] Room: {self.room_name}, Humans: {humans}, Objects: {objs}"
