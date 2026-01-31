"""
Robot AI Skills - Navigation Skill
====================================
Skill for semantic navigation commands.
"""

import re
from typing import Any, Dict, List, Optional

from ..base_skill import BaseSkill, SkillMetadata, SkillResult


class NavigationSkill(BaseSkill):
    """
    Skill for robot navigation.
    
    Handles commands like:
    - "Vai in cucina"
    - "Portami del salotto"
    - "Vieni qui"
    - "Seguimi"
    - "Fermati"
    """
    
    # Navigation intent patterns
    GOTO_PATTERNS = [
        re.compile(r'\b(vai|porta|portami|muoviti|spostati|raggiungi)\b', re.IGNORECASE),
    ]
    COME_PATTERNS = [
        re.compile(r'\b(vieni|avvicinati)\b', re.IGNORECASE),
    ]
    FOLLOW_PATTERNS = [
        re.compile(r'\b(segui|seguimi)\b', re.IGNORECASE),
    ]
    STOP_PATTERNS = [
        re.compile(r'\b(ferma|fermati|stop|alt|basta)\b', re.IGNORECASE),
    ]
    RETURN_PATTERNS = [
        re.compile(r'\b(torna|ritorna|tornatene)\b', re.IGNORECASE),
    ]
    
    # Semantic waypoints with aliases
    WAYPOINTS = {
        "cucina": {
            "aliases": ["cucina", "kitchen"],
            "x": 2.5,
            "y": 1.0,
        },
        "soggiorno": {
            "aliases": ["soggiorno", "salotto", "living", "divano", "sofa"],
            "x": 0.0,
            "y": 0.0,
        },
        "camera": {
            "aliases": ["camera", "stanza", "letto", "bedroom"],
            "x": 4.0,
            "y": 3.0,
        },
        "bagno": {
            "aliases": ["bagno", "bathroom"],
            "x": 3.0,
            "y": 2.5,
        },
        "studio": {
            "aliases": ["studio", "ufficio", "scrivania", "office"],
            "x": 1.5,
            "y": 3.5,
        },
        "ingresso": {
            "aliases": ["ingresso", "entrata", "porta", "entrance"],
            "x": 0.0,
            "y": 2.0,
        },
        "base": {
            "aliases": ["base", "casa", "home", "ricarica", "dock"],
            "x": 0.0,
            "y": 0.0,
        },
    }
    
    def __init__(self, nav_client = None):
        super().__init__()
        self.nav_client = nav_client  # Will be injected
        self._current_destination = None
        self._is_following = False
    
    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="navigation",
            description="Navigate the robot to semantic locations",
            version="1.0.0",
            keywords=[
                "vai", "porta", "vieni", "muoviti", "segui", "fermati",
                "cucina", "soggiorno", "camera", "bagno"
            ],
            priority=8,
            requires_nav=True
        )
    
    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        """Calculate match confidence for navigation commands."""
        score = 0.0
        
        # Check for navigation action keywords
        all_patterns = (
            self.GOTO_PATTERNS +
            self.COME_PATTERNS +
            self.FOLLOW_PATTERNS +
            self.STOP_PATTERNS +
            self.RETURN_PATTERNS
        )
        
        if any(p.search(text) for p in all_patterns):
            score += 0.5
        
        # Check for location keywords
        text_lower = text.lower()
        for waypoint, data in self.WAYPOINTS.items():
            if any(alias in text_lower for alias in data["aliases"]):
                score += 0.4
                break
        
        # "Vieni qui" special case
        if "qui" in text_lower and any(p.search(text) for p in self.COME_PATTERNS):
            score += 0.4
        
        # "Seguimi" special case
        if any(p.search(text) for p in self.FOLLOW_PATTERNS):
            score = 0.9
        
        # "Fermati" special case
        if any(p.search(text) for p in self.STOP_PATTERNS):
            score = 0.95
        
        return min(1.0, score)
    
    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        """Execute navigation command."""
        intent = self._parse_intent(text)
        
        if intent["action"] == "stop":
            return await self._handle_stop()
        
        if intent["action"] == "follow":
            return await self._handle_follow()
        
        if intent["action"] in ["goto", "return"]:
            destination = intent.get("destination")
            if not destination:
                return SkillResult(
                    success=False,
                    message="Non ho capito dove vuoi che vada",
                    speak="Non ho capito dove vuoi che vada"
                )
            
            return await self._handle_goto(destination)
        
        if intent["action"] == "come":
            # Come to user's current position
            user_position = context.get("user_position") if context else None
            if user_position:
                return await self._handle_goto_position(
                    user_position["x"],
                    user_position["y"],
                    "da te"
                )
            else:
                return SkillResult(
                    success=False,
                    message="Non so dove sei",
                    speak="Non so dove sei"
                )
        
        return SkillResult(
            success=False,
            message="Non ho capito il comando di navigazione"
        )
    
    def _parse_intent(self, text: str) -> Dict[str, Any]:
        """Parse navigation intent from text."""
        intent = {
            "action": None,
            "destination": None,
            "speed": "normal"
        }
        
        text_lower = text.lower()
        
        # Determine action
        if any(p.search(text) for p in self.STOP_PATTERNS):
            intent["action"] = "stop"
        elif any(p.search(text) for p in self.FOLLOW_PATTERNS):
            intent["action"] = "follow"
        elif any(p.search(text) for p in self.RETURN_PATTERNS):
            intent["action"] = "return"
            intent["destination"] = "base"
        elif any(p.search(text) for p in self.COME_PATTERNS):
            intent["action"] = "come"
        elif any(p.search(text) for p in self.GOTO_PATTERNS):
            intent["action"] = "goto"
        
        # Find destination
        for waypoint, data in self.WAYPOINTS.items():
            if any(alias in text_lower for alias in data["aliases"]):
                intent["destination"] = waypoint
                break
        
        # Speed detection
        if any(w in text_lower for w in ["veloce", "presto", "rapidamente"]):
            intent["speed"] = "fast"
        elif any(w in text_lower for w in ["piano", "lento", "lentamente"]):
            intent["speed"] = "slow"
        
        return intent
    
    async def _handle_goto(self, destination: str) -> SkillResult:
        """Handle goto command."""
        waypoint = self.WAYPOINTS.get(destination)
        if not waypoint:
            return SkillResult(
                success=False,
                message=f"Non conosco la posizione '{destination}'"
            )
        
        self._current_destination = destination
        
        action = {
            "action_type": "nav_goto",
            "destination": destination,
            "x": waypoint["x"],
            "y": waypoint["y"],
            "frame_id": "map"
        }
        
        # If we have a nav client, execute
        if self.nav_client:
            try:
                result = await self.nav_client.navigate_to_pose(
                    x=waypoint["x"],
                    y=waypoint["y"]
                )
                
                return SkillResult(
                    success=True,
                    message=f"Sto andando in {destination}",
                    speak=f"Ok, arrivo in {destination}",
                    actions=[action]
                )
            except Exception as e:
                return SkillResult(
                    success=False,
                    message=f"Errore navigazione: {str(e)}"
                )
        
        return SkillResult(
            success=True,
            message=f"Sto andando in {destination}",
            speak=f"Ok, arrivo in {destination}",
            actions=[action]
        )
    
    async def _handle_goto_position(self, x: float, y: float, name: str) -> SkillResult:
        """Handle goto a specific position."""
        action = {
            "action_type": "nav_goto",
            "destination": name,
            "x": x,
            "y": y,
            "frame_id": "map"
        }
        
        return SkillResult(
            success=True,
            message=f"Sto venendo {name}",
            speak=f"Ok, arrivo!",
            actions=[action]
        )
    
    async def _handle_stop(self) -> SkillResult:
        """Handle stop command."""
        self._is_following = False
        self._current_destination = None
        
        action = {
            "action_type": "nav_stop"
        }
        
        if self.nav_client:
            try:
                await self.nav_client.cancel_navigation()
            except Exception:
                pass
        
        return SkillResult(
            success=True,
            message="Mi sono fermato",
            speak="Ok, mi fermo",
            actions=[action]
        )
    
    async def _handle_follow(self) -> SkillResult:
        """Handle follow command."""
        self._is_following = True
        
        action = {
            "action_type": "nav_follow",
            "target": "person"
        }
        
        return SkillResult(
            success=True,
            message="Ti sto seguendo",
            speak="Ok, ti seguo!",
            actions=[action]
        )
    
    def get_waypoints(self) -> List[str]:
        """Get list of known waypoint names."""
        return list(self.WAYPOINTS.keys())
    
    def add_waypoint(self, name: str, x: float, y: float, aliases: List[str] = None) -> None:
        """Add a new waypoint."""
        self.WAYPOINTS[name] = {
            "aliases": aliases or [name],
            "x": x,
            "y": y
        }
    
    def get_parameters_schema(self) -> Dict[str, Any]:
        """Schema for LLM function calling."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["goto", "come", "follow", "stop", "return"],
                    "description": "Navigation action"
                },
                "destination": {
                    "type": "string",
                    "description": "Target location name"
                },
                "speed": {
                    "type": "string",
                    "enum": ["slow", "normal", "fast"],
                    "description": "Movement speed"
                }
            },
            "required": ["action"]
        }
