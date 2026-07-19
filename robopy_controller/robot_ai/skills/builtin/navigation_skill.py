"""
Robot AI Skills - Navigation Skill
====================================
Skill per comandi di navigazione semantica.
"""

import re
import json
import time
from typing import Any, Dict, List, Optional

from ...utils.logging_utils import get_logger
from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode


class NavigationSkill(BaseSkill):
    """
    Skill for robot navigation.
    
    Handles commands like:
    - "Vai in cucina"
    - "Portami del salotto"
    - "Vieni qui"
    - "Seguimi"
    - "Fermati"
    - "Vai avanti" / "Gira a sinistra" (relative movement)
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
    MOVE_RELATIVE_PATTERNS = [
        re.compile(r'\b(avanti|indietro|forward|backward)\b', re.IGNORECASE),
        re.compile(r'\bgira\s+a\s+(destra|sinistra)\b', re.IGNORECASE),
        re.compile(r'\b(muoviti|spostati|vai)\s+(avanti|indietro|un po)\b', re.IGNORECASE),
        re.compile(r'\b(gira|ruota|girare)\s+(di\s+)?\d+', re.IGNORECASE),
    ]
    EXPLORE_PATTERNS = [
        re.compile(r'\b(esplora|esplorare|mappa|\bmappare\b|perlustra)\b', re.IGNORECASE),
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
    
    def __init__(self, nav_client=None, move_handler=None, memory_store=None):
        super().__init__()
        self.nav_client = nav_client  # Will be injected
        self.move_handler = move_handler  # Callback: move_handler(direction, speed, duration)
        self.memory_store = memory_store
        self._current_destination = None
        self._is_following = False
        self._current_session_id = 1
        self.logger = get_logger("navigation_skill")
        
        # Subscribe to /rtabmap/info to track current session ID
        if self.nav_client and hasattr(self.nav_client, '_node') and self.nav_client._node:
            try:
                from rtabmap_msgs.msg import Info
                self._info_sub = self.nav_client._node.create_subscription(
                    Info,
                    '/rtabmap/info',
                    self._rtabmap_info_callback,
                    10
                )
                self.logger.info("Subscribed to /rtabmap/info for dynamic session tracking.")
            except Exception as e:
                self.logger.warning(f"Could not subscribe to /rtabmap/info: {e}")

    def _rtabmap_info_callback(self, msg):
        try:
            for key, val in zip(msg.stats_keys, msg.stats_values):
                if "map_id" in key.lower() or "session_id" in key.lower():
                    self._current_session_id = int(val)
                    break
        except Exception:
            pass
    
    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="navigation",
            description="Navigate the robot to semantic locations",
            version="1.0.0",
            keywords=[
                "vai", "porta", "vieni", "muoviti", "segui", "fermati",
                "cucina", "soggiorno", "camera", "bagno", "esplora", "mappa"
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
            self.RETURN_PATTERNS +
            self.MOVE_RELATIVE_PATTERNS +
            self.EXPLORE_PATTERNS
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
            
        # "Esplora" special case
        if any(p.search(text) for p in self.EXPLORE_PATTERNS):
            score = 0.95
        
        # Relative movement ("vai avanti", "gira a destra") 
        if any(p.search(text) for p in self.MOVE_RELATIVE_PATTERNS):
            # Only if NO waypoint destination mentioned
            has_waypoint = any(
                any(alias in text_lower for alias in data["aliases"])
                for data in self.WAYPOINTS.values()
            )
            if not has_waypoint:
                score = max(score, 0.9)
        
        return min(1.0, score)
    
    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        """Execute navigation command."""
        intent = self._parse_intent(text)
        
        if intent["action"] == "stop":
            return await self._handle_stop()
        
        if intent["action"] == "follow":
            return await self._handle_follow()
            
        if intent["action"] == "explore":
            return await self._handle_explore()
        
        if intent["action"] == "move_relative":
            return self._handle_move_relative(intent)
        
        if intent["action"] in ["goto", "return"]:
            destination = intent.get("destination")
            if not destination:
                return SkillResult.failure_result(
                    "Non ho capito dove vuoi che vada",
                    SkillErrorCode.INVALID_PARAMETERS,
                    speak="Non ho capito dove vuoi che vada",
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
                return SkillResult.failure_result(
                    "Non so dove sei",
                    SkillErrorCode.INVALID_PARAMETERS,
                    speak="Non so dove sei",
                )
        
        return SkillResult.failure_result(
            "Non ho capito il comando di navigazione",
            SkillErrorCode.INVALID_PARAMETERS,
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
        elif any(p.search(text) for p in self.EXPLORE_PATTERNS):
            intent["action"] = "explore"
        
        # Check for relative movement (before waypoint check)
        has_waypoint = False
        for waypoint, data in self.WAYPOINTS.items():
            if any(alias in text_lower for alias in data["aliases"]):
                intent["destination"] = waypoint
                has_waypoint = True
                break
        
        # If relative movement words AND no waypoint, it's a relative move
        if not has_waypoint and any(p.search(text) for p in self.MOVE_RELATIVE_PATTERNS):
            intent["action"] = "move_relative"
            # Parse direction
            if "avanti" in text_lower or "forward" in text_lower:
                intent["direction"] = "avanti"
            elif "indietro" in text_lower or "backward" in text_lower:
                intent["direction"] = "indietro"
            elif "sinistra" in text_lower or "left" in text_lower:
                intent["direction"] = "sinistra"
            elif "destra" in text_lower or "right" in text_lower:
                intent["direction"] = "destra"
            # Parse duration from text (e.g. "per 2 secondi")
            dur_match = re.search(r'per\s+(\d+\.?\d*)\s*second', text_lower)
            if dur_match:
                intent["duration"] = float(dur_match.group(1))
            else:
                intent["duration"] = 1.5  # Default
            
            # Parse degrees from text (e.g. "gira di 30 gradi", "ruota di 90°")
            deg_match = re.search(r'(?:di\s+)?(\d+\.?\d*)\s*(?:grad|°)', text_lower)
            if deg_match:
                intent["degrees"] = float(deg_match.group(1))
                # If we have degrees but no direction yet, infer from context
                if not intent.get("direction"):
                    if "sinistra" in text_lower or "left" in text_lower:
                        intent["direction"] = "sinistra"
                    else:
                        intent["direction"] = "destra"  # Default to right for degree rotations
        
        # Speed detection
        if any(w in text_lower for w in ["veloce", "presto", "rapidamente"]):
            intent["speed"] = "fast"
        elif any(w in text_lower for w in ["piano", "lento", "lentamente"]):
            intent["speed"] = "slow"
        
        return intent
    
    async def _handle_goto(self, destination: str) -> SkillResult:
        """Handle goto command."""
        found_location = None
        
        # 1. Search in MemoryType.LOCATION in ChromaDB
        if self.memory_store:
            try:
                from ...rag.memory_store import MemoryType
                loc_memories = self.memory_store.get_recent(limit=100, memory_type=MemoryType.LOCATION)
                current_session = getattr(self, "_current_session_id", 1)
                
                # First pass: matching session
                for mem in loc_memories:
                    meta = mem.metadata or {}
                    label = meta.get("label", "").lower()
                    aliases_str = meta.get("aliases", "[]")
                    try:
                        aliases = json.loads(aliases_str)
                    except Exception:
                        aliases = [label]
                    aliases_lower = [a.lower() for a in aliases]
                    
                    if destination.lower() == label or destination.lower() in aliases_lower:
                        session_val = meta.get("session_id")
                        try:
                            session_id = int(session_val) if session_val else 1
                        except ValueError:
                            session_id = 1
                        
                        if session_id == current_session:
                            found_location = {
                                "x": float(meta.get("x", 0.0)),
                                "y": float(meta.get("y", 0.0)),
                                "frame_id": meta.get("frame_id", "map")
                            }
                            self.logger.info(f"Dynamic waypoint '{destination}' found in LOCATION (session {current_session})")
                            break
                            
                # Second pass: fallback to any session
                if not found_location:
                    for mem in loc_memories:
                        meta = mem.metadata or {}
                        label = meta.get("label", "").lower()
                        aliases_str = meta.get("aliases", "[]")
                        try:
                            aliases = json.loads(aliases_str)
                        except Exception:
                            aliases = [label]
                        aliases_lower = [a.lower() for a in aliases]
                        
                        if destination.lower() == label or destination.lower() in aliases_lower:
                            found_location = {
                                "x": float(meta.get("x", 0.0)),
                                "y": float(meta.get("y", 0.0)),
                                "frame_id": meta.get("frame_id", "map")
                            }
                            self.logger.info(f"Dynamic waypoint '{destination}' found in LOCATION (any session)")
                            break
            except Exception as e:
                self.logger.error(f"Error querying LOCATION in ChromaDB: {e}")

        # 2. Search in MemoryType.VISUAL_OBSERVATION in ChromaDB
        if not found_location and self.memory_store:
            try:
                from ...rag.memory_store import MemoryType
                obs_memories = self.memory_store.get_recent(limit=100, memory_type=MemoryType.VISUAL_OBSERVATION)
                current_session = getattr(self, "_current_session_id", 1)
                
                # First pass: matching session
                for mem in obs_memories:
                    meta = mem.metadata or {}
                    session_val = meta.get("session_id")
                    try:
                        session_id = int(session_val) if session_val else 1
                    except ValueError:
                        session_id = 1
                        
                    if session_id != current_session:
                        continue
                        
                    objects_str = meta.get("objects", "[]")
                    try:
                        objects = json.loads(objects_str)
                    except Exception:
                        objects = []
                        
                    for obj in objects:
                        label = obj.get("label", "").lower()
                        if destination.lower() in label or label in destination.lower():
                            found_location = {
                                "x": float(obj.get("x", 0.0)),
                                "y": float(obj.get("y", 0.0)),
                                "frame_id": obj.get("frame", "map")
                            }
                            self.logger.info(f"Dynamic target '{destination}' found in VISUAL_OBSERVATION (session {current_session})")
                            break
                    if found_location:
                        break
                        
                # Second pass: fallback to any session
                if not found_location:
                    for mem in obs_memories:
                        meta = mem.metadata or {}
                        objects_str = meta.get("objects", "[]")
                        try:
                            objects = json.loads(objects_str)
                        except Exception:
                            objects = []
                            
                        for obj in objects:
                            label = obj.get("label", "").lower()
                            if destination.lower() in label or label in destination.lower():
                                found_location = {
                                    "x": float(obj.get("x", 0.0)),
                                    "y": float(obj.get("y", 0.0)),
                                    "frame_id": obj.get("frame", "map")
                                }
                                self.logger.info(f"Dynamic target '{destination}' found in VISUAL_OBSERVATION (any session)")
                                break
                        if found_location:
                            break
            except Exception as e:
                self.logger.error(f"Error querying VISUAL_OBSERVATION in ChromaDB: {e}")

        # 3. Fallback to static WAYPOINTS
        if not found_location:
            waypoint = self.WAYPOINTS.get(destination)
            if waypoint:
                found_location = {
                    "x": waypoint["x"],
                    "y": waypoint["y"],
                    "frame_id": "map"
                }
                self.logger.info(f"Waypoint '{destination}' found in static fallbacks")
        
        if not found_location:
            return SkillResult.failure_result(
                f"Non conosco la posizione o l'oggetto '{destination}'",
                SkillErrorCode.INVALID_PARAMETERS,
            )
        
        self._current_destination = destination
        
        action = {
            "action_type": "nav_goto",
            "destination": destination,
            "x": found_location["x"],
            "y": found_location["y"],
            "frame_id": found_location["frame_id"]
        }
        
        # If we have a nav client, execute
        if self.nav_client:
            try:
                result = await self.nav_client.navigate_to_pose(
                    x=found_location["x"],
                    y=found_location["y"],
                    frame_id=found_location["frame_id"]
                )
                
                return SkillResult(
                    success=True,
                    message=f"Sto andando in {destination}",
                    speak=f"Ok, arrivo in {destination}",
                    actions=[action]
                )
            except Exception as e:
                return SkillResult.failure_result(
                    f"Errore navigazione: {str(e)}",
                    SkillErrorCode.EXTERNAL_SERVICE_ERROR,
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
                await self.nav_client.stop_exploration()
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
        
    async def _handle_explore(self) -> SkillResult:
        """Handle explore command."""
        action = {
            "action_type": "nav_explore"
        }
        
        if self.nav_client:
            try:
                success = await self.nav_client.start_exploration(radius=2.0, max_points=15)
                if success:
                    return SkillResult(
                        success=True,
                        message="Esplorazione avviata",
                        speak="Ok, cancello la mappa e comincio a esplorare la stanza.",
                        actions=[action]
                    )
                else:
                    return SkillResult.failure_result(
                        "Esplorazione già in corso",
                        SkillErrorCode.INVALID_PARAMETERS,
                        speak="Sto già esplorando!"
                    )
            except Exception as e:
                return SkillResult.failure_result(
                    f"Errore avvio esplorazione: {str(e)}",
                    SkillErrorCode.EXTERNAL_SERVICE_ERROR,
                    speak="Ho avuto un problema ad avviare l'esplorazione"
                )
                
        return SkillResult(
            success=True,
            message="Esplorazione simulata avviata",
            speak="Ok, comincio a esplorare.",
            actions=[action]
        )
    
    def _handle_move_relative(self, intent: Dict[str, Any]) -> SkillResult:
        """Handle relative movement command (avanti, indietro, sinistra, destra)."""
        direction = intent.get("direction")
        if not direction:
            return SkillResult.failure_result(
                "Non ho capito in che direzione vuoi che mi muova",
                SkillErrorCode.INVALID_PARAMETERS,
            )
        
        speed_map = {"slow": 0.15, "normal": 0.3, "fast": 0.5}
        speed = speed_map.get(intent.get("speed", "normal"), 0.3)
        duration = intent.get("duration", 1.5)
        degrees = intent.get("degrees")  # None if not specified
        
        if self.move_handler:
            self.move_handler(direction, speed, duration, degrees)
            direction_text = {
                "avanti": "avanti", "indietro": "indietro",
                "sinistra": "a sinistra", "destra": "a destra"
            }.get(direction, direction)
            deg_info = f" di {degrees:.0f} gradi" if degrees else ""
            return SkillResult(
                success=True,
                message=f"Mi muovo {direction_text}{deg_info}",
                speak=f"Ok, mi muovo {direction_text}{deg_info}",
            )
        else:
            return SkillResult.failure_result(
                "Movimento diretto non disponibile al momento",
                SkillErrorCode.EXTERNAL_SERVICE_ERROR,
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
        if self.memory_store:
            try:
                from ...rag.memory_store import Memory, MemoryType
                mem_id = f"loc_{name.lower().replace(' ', '_')}"
                
                # Check if it already exists to overwrite/update cleanly
                mem = Memory(
                    id=mem_id,
                    content=f"Location: {name}",
                    memory_type=MemoryType.LOCATION,
                    metadata={
                        "label": name,
                        "aliases": json.dumps(aliases or [name]),
                        "x": float(x),
                        "y": float(y),
                        "frame_id": "map",
                        "session_id": getattr(self, "_current_session_id", 1),
                        "timestamp": time.time()
                    }
                )
                self.memory_store.add(mem)
                self.logger.info(f"Saved location '{name}' to ChromaDB for session {getattr(self, '_current_session_id', 1)}")
            except Exception as e:
                self.logger.error(f"Error saving location to ChromaDB: {e}")
    
    def get_parameters_schema(self) -> Dict[str, Any]:
        """Schema for LLM function calling."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["goto", "come", "follow", "stop", "return", "move_relative", "explore"],
                    "description": "Navigation action. Use 'move_relative' for directional commands like 'vai avanti', 'gira a sinistra'"
                },
                "destination": {
                    "type": "string",
                    "description": "Target location name (for goto/return)"
                },
                "direction": {
                    "type": "string",
                    "enum": ["avanti", "indietro", "sinistra", "destra"],
                    "description": "Movement direction (for move_relative)"
                },
                "speed": {
                    "type": "string",
                    "enum": ["slow", "normal", "fast"],
                    "description": "Movement speed"
                },
                "duration": {
                    "type": "number",
                    "description": "Duration in seconds (for move_relative, default 1.5)"
                }
            },
            "required": ["action"]
        }
