"""
Robot AI Skills - Home Assistant Skill
=======================================
Skill per il controllo delle entità Home Assistant.
"""

import re
from typing import Any, Dict, List, Optional

from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode


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
        """Execute Home Assistant command with dynamic entity fallback and clear error diagnostics."""
        # Parse intent
        intent = self._parse_intent(text)
        
        if not intent.get("action"):
            return SkillResult.failure_result(
                "Non ho capito quale azione domotica vuoi eseguire (es. accendi, spegni, imposta).",
                SkillErrorCode.INVALID_PARAMETERS,
                speak="Non ho capito se vuoi accendere, spegnere o regolare il dispositivo.",
            )
        
        # [Nightly Dream 2026-10-10] Generic whole-house commands ("spegni tutto" / "accendi tutto")
        if not intent.get("entity_type"):
            if intent.get("all_entities") and intent["action"] in ("turn_on", "turn_off"):
                intent["entity_type"] = "light"
            else:
                return SkillResult.failure_result(
                    "Non ho capito quale dispositivo controllare",
                    SkillErrorCode.INVALID_PARAMETERS,
                    speak="Dimmi quale dispositivo o stanza vuoi controllare, ad esempio la luce in cucina o in bagno.",
                )
        
        # Build HA action
        action = self._build_action(intent)
        
        # If we have an HA client, resolve best matching entity and execute
        if self.ha_client:
            resolved_entity = await self._resolve_entity_from_ha(intent, action)
            if resolved_entity:
                action["entity_id"] = resolved_entity
            try:
                await self.ha_client.call_service(
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
                err_str = str(e)
                err_low = err_str.lower()
                # Retry once with alternative entity synonym (e.g. luce <-> lampada) if parameter/entity mismatch
                alt_entity = await self._find_fallback_entity(intent, exclude_entity=action.get("entity_id"))
                if alt_entity:
                    try:
                        action["entity_id"] = alt_entity
                        await self.ha_client.call_service(
                            domain=action["domain"],
                            service=action["service"],
                            entity_id=alt_entity,
                            data=action.get("data"),
                        )
                        return SkillResult(
                            success=True,
                            message=self._build_response(intent),
                            speak=self._build_response(intent),
                            actions=[action],
                        )
                    except Exception as retry_err:
                        err_str = str(retry_err)
                        err_low = err_str.lower()

                if any(k in err_low for k in ("not connected", "connect", "timeout", "unreachable", "websocket")):
                    speak_msg = "Non riesco a raggiungere Home Assistant in questo momento; la connessione domotica non risponde."
                    code = SkillErrorCode.EXTERNAL_SERVICE_ERROR
                elif any(k in err_low for k in ("entity", "not found", "invalid", "parameter", "schema")):
                    loc = intent.get("location") or "quella stanza"
                    speak_msg = (
                        f"Home Assistant non riconosce il dispositivo richiesto per {loc}. "
                        "Puoi indicarmi il nome esatto della luce o dell'interruttore?"
                    )
                    code = SkillErrorCode.INVALID_PARAMETERS
                else:
                    speak_msg = f"Home Assistant ha restituito un errore durante il comando sul dispositivo {action.get('entity_id') or action['domain']}."
                    code = SkillErrorCode.EXTERNAL_SERVICE_ERROR
                return SkillResult.failure_result(
                    f"Errore HA ({action.get('entity_id', action['domain'])}): {err_str}",
                    code,
                    speak=speak_msg,
                )
        
        # No client, return action to be executed by orchestrator
        return SkillResult(
            success=True,
            message=self._build_response(intent),
            speak=self._build_response(intent),
            actions=[action]
        )

    async def _get_ha_states_safe(self) -> List[Dict[str, Any]]:
        """Best-effort fetch of cached/live HA states for entity resolution."""
        if not self.ha_client:
            return []
        try:
            if hasattr(self.ha_client, "get_states"):
                res = self.ha_client.get_states()
                if hasattr(res, "__await__"):
                    res = await res
                if isinstance(res, list):
                    return res
                if isinstance(res, dict):
                    return list(res.values())
            cached = getattr(self.ha_client, "states", None)
            if isinstance(cached, dict):
                return list(cached.values())
            if isinstance(cached, list):
                return cached
        except Exception:
            return []
        return []

    async def _resolve_entity_from_ha(self, intent: Dict[str, Any], action: Dict[str, Any]) -> Optional[str]:
        """Resolve an entity_id from live/cached Home Assistant states matching domain & room/synonyms."""
        if intent.get("all_entities"):
            return "all"
        location = intent.get("location")
        domain = action.get("domain")
        if not location or not domain:
            return action.get("entity_id")

        states = await self._get_ha_states_safe()
        if not states:
            return action.get("entity_id")

        loc_aliases = self.LOCATION_PATTERNS.get(location, [location])
        synonyms = ["luce", "lampada", "lampadina", "specchio", "plafoniera"] if domain == "light" else []

        exact_id = f"{domain}.{location}"
        candidates: List[tuple] = []
        for st in states:
            if not isinstance(st, dict):
                continue
            eid = str(st.get("entity_id", ""))
            if not eid.startswith(f"{domain}."):
                continue
            if eid == exact_id:
                return eid
            friendly = str((st.get("attributes") or {}).get("friendly_name", "")).lower()
            haystack = f"{eid.lower()} {friendly}"
            if any(alias in haystack for alias in loc_aliases):
                score = 2 if any(syn in haystack for syn in synonyms) else 1
                candidates.append((score, eid))

        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            return candidates[0][1]
        return action.get("entity_id")

    async def _find_fallback_entity(self, intent: Dict[str, Any], exclude_entity: Optional[str]) -> Optional[str]:
        """Find an alternative entity in the same room (e.g. switch/light lampada) if primary call failed."""
        location = intent.get("location")
        if not location or intent.get("all_entities"):
            return None
        states = await self._get_ha_states_safe()
        if not states:
            return None
        loc_aliases = self.LOCATION_PATTERNS.get(location, [location])
        allowed_domains = ("light", "switch") if intent.get("entity_type") == "light" else (intent.get("entity_type"),)
        for st in states:
            if not isinstance(st, dict):
                continue
            eid = str(st.get("entity_id", ""))
            if not eid or eid == exclude_entity:
                continue
            dom = eid.split(".", 1)[0]
            if dom not in allowed_domains:
                continue
            friendly = str((st.get("attributes") or {}).get("friendly_name", "")).lower()
            if any(alias in f"{eid.lower()} {friendly}" for alias in loc_aliases):
                return eid
        return None
    
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
