"""
Robot AI Skills - Home Assistant Skill
=======================================
Skill for controlling Home Assistant entities.
"""

import re
from typing import Any, Dict, List, Optional

from ..base_skill import BaseSkill, SkillMetadata, SkillResult


class HomeAssistantSkill(BaseSkill):
    """
    Skill for Home Assistant control.
    
    Handles commands like:
    - "Accendi la luce in cucina"
    - "Spegni tutte le luci"
    - "Imposta la temperatura a 22 gradi"
    - "Accendi la TV"
    """
    
    # Intent patterns
    TURN_ON_PATTERNS = [
        re.compile(r'\b(accendi|attiva|apri)\b', re.IGNORECASE),
    ]
    TURN_OFF_PATTERNS = [
        re.compile(r'\b(spegni|disattiva|chiudi)\b', re.IGNORECASE),
    ]
    SET_PATTERNS = [
        re.compile(r'\b(imposta|metti|setta)\b', re.IGNORECASE),
    ]
    TOGGLE_PATTERNS = [
        re.compile(r'\b(cambia|alterna|toggle)\b', re.IGNORECASE),
    ]
    
    # Entity patterns
    ENTITY_PATTERNS = {
        "light": [
            re.compile(r'\b(luce|luci|lampada|lampadina|illuminazione)\b', re.IGNORECASE),
        ],
        "switch": [
            re.compile(r'\b(presa|interruttore|switch)\b', re.IGNORECASE),
        ],
        "climate": [
            re.compile(r'\b(clima|condizionatore|riscaldamento|temperatura)\b', re.IGNORECASE),
        ],
        "media_player": [
            re.compile(r'\b(tv|televisione|televisore|stereo|musica|spotify)\b', re.IGNORECASE),
        ],
        "cover": [
            re.compile(r'\b(tapparella|tenda|persiana|serranda)\b', re.IGNORECASE),
        ],
    }
    
    # Location patterns
    LOCATION_PATTERNS = {
        "cucina": ["cucina", "kitchen"],
        "soggiorno": ["soggiorno", "salotto", "living"],
        "camera": ["camera", "stanza", "bedroom"],
        "bagno": ["bagno", "bathroom"],
        "studio": ["studio", "ufficio", "office"],
        "corridoio": ["corridoio", "ingresso", "entrata"],
        "esterno": ["esterno", "giardino", "terrazzo", "balcone"],
        "garage": ["garage", "box"],
        "tutto": ["tutte", "tutti", "tutto", "ovunque", "casa"],
    }
    
    def __init__(self, ha_client = None):
        super().__init__()
        self.ha_client = ha_client  # Will be injected
    
    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="home_assistant",
            description="Control Home Assistant devices and automations",
            version="1.0.0",
            keywords=[
                "luce", "luci", "accendi", "spegni", "temperatura",
                "clima", "tv", "tapparella", "presa", "imposta"
            ],
            priority=10,
            requires_ha=True
        )
    
    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        """Calculate match confidence for HA commands."""
        score = 0.0
        text_lower = text.lower()
        
        # Check for action keywords
        has_action = any(
            p.search(text) for patterns in [
                self.TURN_ON_PATTERNS,
                self.TURN_OFF_PATTERNS,
                self.SET_PATTERNS,
                self.TOGGLE_PATTERNS
            ] for p in patterns
        )
        
        if has_action:
            score += 0.4
        
        # Check for entity keywords
        for entity_type, patterns in self.ENTITY_PATTERNS.items():
            if any(p.search(text) for p in patterns):
                score += 0.3
                break
        
        # Check for location keywords
        for location, keywords in self.LOCATION_PATTERNS.items():
            if any(kw in text_lower for kw in keywords):
                score += 0.2
                break
        
        return min(1.0, score)
    
    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        """Execute Home Assistant command."""
        # Parse intent
        intent = self._parse_intent(text)
        
        if not intent.get("action"):
            return SkillResult(
                success=False,
                message="Non ho capito cosa vuoi fare"
            )
        
        if not intent.get("entity_type"):
            return SkillResult(
                success=False,
                message="Non ho capito quale dispositivo controllare"
            )
        
        # Build HA action
        action = self._build_action(intent)
        
        # If we have an HA client, execute
        if self.ha_client:
            try:
                result = await self.ha_client.call_service(
                    domain=action["domain"],
                    service=action["service"],
                    entity_id=action.get("entity_id"),
                    data=action.get("data")
                )
                
                return SkillResult(
                    success=True,
                    message=self._build_response(intent),
                    speak=self._build_response(intent),
                    actions=[action]
                )
            except Exception as e:
                return SkillResult(
                    success=False,
                    message=f"Errore: {str(e)}"
                )
        
        # No client, return action to be executed by orchestrator
        return SkillResult(
            success=True,
            message=self._build_response(intent),
            speak=self._build_response(intent),
            actions=[action]
        )
    
    def _parse_intent(self, text: str) -> Dict[str, Any]:
        """Parse user intent from text."""
        intent = {
            "action": None,
            "entity_type": None,
            "location": None,
            "target_value": None,
            "all_entities": False
        }
        
        text_lower = text.lower()
        
        # Detect action
        if any(p.search(text) for p in self.TURN_ON_PATTERNS):
            intent["action"] = "turn_on"
        elif any(p.search(text) for p in self.TURN_OFF_PATTERNS):
            intent["action"] = "turn_off"
        elif any(p.search(text) for p in self.SET_PATTERNS):
            intent["action"] = "set"
        elif any(p.search(text) for p in self.TOGGLE_PATTERNS):
            intent["action"] = "toggle"
        
        # Detect entity type
        for entity_type, patterns in self.ENTITY_PATTERNS.items():
            if any(p.search(text) for p in patterns):
                intent["entity_type"] = entity_type
                break
        
        # Detect location
        for location, keywords in self.LOCATION_PATTERNS.items():
            if any(kw in text_lower for kw in keywords):
                intent["location"] = location
                if location == "tutto":
                    intent["all_entities"] = True
                break
        
        # Detect value (for set commands)
        value_match = re.search(r'(\d+)\s*(gradi|°|%|percent)?', text)
        if value_match:
            intent["target_value"] = int(value_match.group(1))
            intent["value_unit"] = value_match.group(2)
        
        return intent
    
    def _build_action(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """Build HA action from intent."""
        domain = intent["entity_type"]
        service = intent["action"]
        
        # Map actions to HA services
        if intent["action"] == "set":
            if domain == "climate":
                service = "set_temperature"
            elif domain == "light":
                service = "turn_on"  # with brightness
        
        action = {
            "action_type": "ha_call",
            "domain": domain,
            "service": service,
        }
        
        # Add entity ID if we can determine it
        if intent["location"] and not intent["all_entities"]:
            entity_id = f"{domain}.{intent['location']}"
            action["entity_id"] = entity_id
        
        # Add data for set commands
        if intent["target_value"]:
            if domain == "climate":
                action["data"] = {"temperature": intent["target_value"]}
            elif domain == "light":
                # Convert percentage to 0-255 brightness
                action["data"] = {"brightness_pct": intent["target_value"]}
        
        return action
    
    def _build_response(self, intent: Dict[str, Any]) -> str:
        """Build natural language response."""
        action_words = {
            "turn_on": "acceso",
            "turn_off": "spento",
            "toggle": "cambiato",
            "set": "impostato",
        }
        
        entity_words = {
            "light": "luce",
            "switch": "presa",
            "climate": "clima",
            "media_player": "dispositivo",
            "cover": "tapparella",
        }
        
        action = action_words.get(intent["action"], intent["action"])
        entity = entity_words.get(intent["entity_type"], intent["entity_type"])
        location = intent["location"] or ""
        
        if intent["all_entities"]:
            return f"Ho {action} tutte le {entity}"
        elif location:
            return f"Ho {action} la {entity} in {location}"
        else:
            return f"Ho {action} la {entity}"
    
    def get_parameters_schema(self) -> Dict[str, Any]:
        """Schema for LLM function calling."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["turn_on", "turn_off", "toggle", "set"],
                    "description": "Action to perform"
                },
                "entity_type": {
                    "type": "string",
                    "enum": ["light", "switch", "climate", "media_player", "cover"],
                    "description": "Type of device"
                },
                "location": {
                    "type": "string",
                    "description": "Room or location of the device"
                },
                "value": {
                    "type": "number",
                    "description": "Target value for set commands"
                }
            },
            "required": ["action", "entity_type"]
        }
