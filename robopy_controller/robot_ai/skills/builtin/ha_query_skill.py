"""
Robot AI Skills - Home Assistant Query Skill
=============================================
Skill per interrogare lo stato delle entità Home Assistant.

Viene invocata dall'LLM tramite function call con nome "check_home_assistant"
quando l'utente chiede informazioni sullo stato di dispositivi domotici.

Esempio action LLM:
    {"name": "check_home_assistant", "args": {"entity_id": "cover.tapparella_salotto"}}
"""

import re
from typing import Any, Dict, List, Optional

from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode
from ...utils.logging_utils import get_logger


# Mappatura dominio → template di risposta italiana
DOMAIN_TEMPLATES = {
    "light": {
        "on": "La luce {name} è accesa",
        "off": "La luce {name} è spenta",
        "unavailable": "La luce {name} non è raggiungibile",
    },
    "switch": {
        "on": "L'interruttore {name} è acceso",
        "off": "L'interruttore {name} è spento",
        "unavailable": "{name} non è raggiungibile",
    },
    "cover": {
        "open": "La tapparella {name} è aperta",
        "closed": "La tapparella {name} è chiusa",
        "opening": "La tapparella {name} si sta aprendo",
        "closing": "La tapparella {name} si sta chiudendo",
        "unavailable": "La tapparella {name} non è raggiungibile",
    },
    "climate": {
        "_default": "Il clima {name} è impostato su {state}",
        "off": "Il climatizzatore {name} è spento",
        "unavailable": "Il climatizzatore {name} non è raggiungibile",
    },
    "sensor": {
        "_default": "Il sensore {name} segna {state}{unit}",
        "unavailable": "Il sensore {name} non è raggiungibile",
    },
    "binary_sensor": {
        "on": "Il sensore {name} è attivo",
        "off": "Il sensore {name} è inattivo",
        "unavailable": "{name} non è raggiungibile",
    },
    "media_player": {
        "playing": "Il media player {name} sta riproducendo",
        "paused": "Il media player {name} è in pausa",
        "idle": "Il media player {name} è inattivo",
        "off": "Il media player {name} è spento",
        "unavailable": "{name} non è raggiungibile",
    },
    "vacuum": {
        "cleaning": "L'aspirapolvere {name} sta pulendo",
        "docked": "L'aspirapolvere {name} è alla base",
        "returning": "L'aspirapolvere {name} sta tornando alla base",
        "idle": "L'aspirapolvere {name} è fermo",
        "unavailable": "{name} non è raggiungibile",
    },
}


class HAQuerySkill(BaseSkill):
    """
    Skill per interrogare lo stato di entità Home Assistant.
    
    Invocata dall'LLM con function call "check_home_assistant".
    Supporta:
    - Query singola per entity_id
    - Query per dominio (tutte le luci, tutti i sensori, ecc.)
    - Formattazione risposta naturale in italiano
    """
    
    def __init__(self, ha_client=None):
        super().__init__()
        self.ha_client = ha_client
        self._logger = get_logger("ha_query_skill")
    
    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="check_home_assistant",
            description="Query Home Assistant entity states, attributes and sensor values",
            version="1.0.0",
            keywords=[
                "stato", "aperta", "chiusa", "accesa", "spenta",
                "temperatura", "quanto", "umidità", "sensore",
                "tapparella", "luce", "clima", "com'è"
            ],
            priority=8,
            requires_ha=True,
        )
    
    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        """
        Pattern matching testuale per query HA.
        Score basso perché questa skill è pensata per essere invocata via
        function call dall'LLM, non dal fast-path regex del ConversationManager.
        """
        text_lower = text.lower()
        score = 0.0
        
        # Query keywords
        query_words = [
            "stato", "com'è", "è aperta", "è chiusa", "è accesa", "è spenta",
            "che temperatura", "quanti gradi", "umidità", "quanto",
            "è acceso", "è spento", "funziona"
        ]
        if any(kw in text_lower for kw in query_words):
            score += 0.3
        
        # Entity keywords
        entity_words = [
            "tapparella", "luce", "luci", "clima", "sensore",
            "termostato", "presa", "tv", "aspirapolvere"
        ]
        if any(kw in text_lower for kw in entity_words):
            score += 0.2
        
        # Location keywords
        location_words = [
            "cucina", "salotto", "soggiorno", "camera", "bagno",
            "studio", "corridoio", "garage", "esterno"
        ]
        if any(kw in text_lower for kw in location_words):
            score += 0.1
        
        return min(1.0, score)
    
    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        """
        Esegue la query HA.
        
        Il context può contenere:
        - entity_id: ID dell'entità specifica (es. "cover.tapparella_salotto")
        - domain: dominio per query multi-entità (es. "light")
        """
        context = context or {}
        entity_id = context.get("entity_id", "")
        domain = context.get("domain", "")
        
        if not self.ha_client:
            return SkillResult.failure_result(
                "Client Home Assistant non disponibile",
                SkillErrorCode.EXTERNAL_SERVICE_ERROR,
                speak="Non riesco a comunicare con la domotica.",
            )
        
        if not self.ha_client.is_connected:
            return SkillResult.failure_result(
                "Home Assistant non connesso",
                SkillErrorCode.EXTERNAL_SERVICE_ERROR,
                speak="Home Assistant non è connesso al momento.",
            )
        
        try:
            if entity_id:
                return await self._query_single_entity(entity_id)
            elif domain:
                return await self._query_domain(domain)
            else:
                # Fallback: prova a estrarre entity_id dal testo
                extracted = self._extract_entity_from_text(text)
                if extracted:
                    return await self._query_single_entity(extracted)
                
                return SkillResult.failure_result(
                    "Non ho capito quale dispositivo vuoi controllare",
                    SkillErrorCode.INVALID_PARAMETERS,
                    speak="Non ho capito quale dispositivo vuoi controllare. "
                          "Prova a specificare il nome esatto.",
                )
        except Exception as e:
            self._logger.error(f"Errore query HA: {e}", exc_info=True)
            return SkillResult.failure_result(
                f"Errore durante la lettura da Home Assistant: {e}",
                SkillErrorCode.EXTERNAL_SERVICE_ERROR,
                speak="Ho avuto un problema a leggere i dati dalla domotica.",
            )
    
    async def _query_single_entity(self, entity_id: str) -> SkillResult:
        """Interroga una singola entità HA."""
        self._logger.info(f"🏠 Querying HA entity: {entity_id}")
        
        # Recupera lo stato
        state = await self.ha_client.get_state(entity_id)
        
        if state is None:
            return SkillResult.failure_result(
                f"Entità {entity_id} non trovata",
                SkillErrorCode.INVALID_PARAMETERS,
                speak=f"Non ho trovato il dispositivo {entity_id} in Home Assistant.",
            )
        
        # Recupera attributi dettagliati dalla cache interna del client
        entity = self.ha_client._entities.get(entity_id)
        attributes = entity.attributes if entity else {}
        friendly_name = attributes.get("friendly_name", entity_id)
        domain = entity_id.split(".")[0] if "." in entity_id else ""
        
        # Formatta risposta
        speak_text = self._format_response(domain, state, friendly_name, attributes)
        
        self._logger.info(f"🏠 HA Query result: {entity_id} = {state} → \"{speak_text}\"")
        
        return SkillResult.success_result(
            message=f"{entity_id}: {state}",
            data={
                "entity_id": entity_id,
                "state": state,
                "friendly_name": friendly_name,
                "attributes": attributes,
            },
            speak=speak_text,
        )
    
    async def _query_domain(self, domain: str) -> SkillResult:
        """Interroga tutte le entità di un dominio."""
        self._logger.info(f"🏠 Querying HA domain: {domain}")
        
        entities = await self.ha_client.get_entities(domain)
        
        if not entities:
            return SkillResult.failure_result(
                f"Nessuna entità trovata per il dominio {domain}",
                SkillErrorCode.INVALID_PARAMETERS,
                speak=f"Non ho trovato dispositivi di tipo {domain}.",
            )
        
        # Formatta lista
        lines = []
        entity_data = []
        for entity in entities:
            name = entity.attributes.get("friendly_name", entity.entity_id)
            state = entity.state
            line = self._format_response(domain, state, name, entity.attributes)
            lines.append(line)
            entity_data.append({
                "entity_id": entity.entity_id,
                "state": state,
                "friendly_name": name,
            })
        
        # Componi risposta parlata
        if len(lines) == 1:
            speak_text = lines[0]
        elif len(lines) <= 5:
            speak_text = ". ".join(lines)
        else:
            # Troppe entità, riassumi
            on_count = sum(1 for e in entities if e.state in ("on", "open", "playing"))
            off_count = sum(1 for e in entities if e.state in ("off", "closed", "idle", "docked"))
            speak_text = (
                f"Ho trovato {len(entities)} dispositivi di tipo {domain}. "
                f"{on_count} sono attivi, {off_count} sono spenti o inattivi."
            )
        
        return SkillResult.success_result(
            message=f"Domain {domain}: {len(entities)} entities",
            data={"domain": domain, "entities": entity_data},
            speak=speak_text,
        )
    
    def _format_response(
        self,
        domain: str,
        state: str,
        friendly_name: str,
        attributes: Dict[str, Any],
    ) -> str:
        """Formatta una risposta naturale in italiano basata su dominio e stato."""
        
        # Ottieni il template per il dominio
        templates = DOMAIN_TEMPLATES.get(domain, {})
        template = templates.get(state)
        
        if not template:
            template = templates.get("_default")
        if not template:
            # Fallback generico
            template = "{name} è {state}"
        
        # Preparazione variabili
        unit = attributes.get("unit_of_measurement", "")
        unit_str = f" {unit}" if unit else ""
        
        result = template.format(
            name=friendly_name,
            state=state,
            unit=unit_str,
        )
        
        # Arricchisci con attributi specifici del dominio
        extra_info = self._get_extra_info(domain, state, attributes)
        if extra_info:
            result += f". {extra_info}"
        
        return result
    
    def _get_extra_info(
        self,
        domain: str,
        state: str,
        attributes: Dict[str, Any],
    ) -> str:
        """Estrae informazioni aggiuntive dagli attributi."""
        extras = []
        
        if domain == "light" and state == "on":
            brightness = attributes.get("brightness")
            if brightness is not None:
                pct = round(brightness / 255 * 100)
                extras.append(f"luminosità al {pct}%")
            color_temp = attributes.get("color_temp_kelvin")
            if color_temp is not None:
                extras.append(f"temperatura colore {color_temp}K")
        
        elif domain == "cover":
            position = attributes.get("current_position")
            if position is not None:
                extras.append(f"posizione al {position}%")
        
        elif domain == "climate":
            current_temp = attributes.get("current_temperature")
            target_temp = attributes.get("temperature")
            if current_temp is not None:
                extras.append(f"temperatura attuale {current_temp}°C")
            if target_temp is not None:
                extras.append(f"target {target_temp}°C")
            hvac_action = attributes.get("hvac_action")
            if hvac_action and hvac_action != "idle":
                extras.append(f"in modalità {hvac_action}")
        
        elif domain == "sensor":
            # I sensori hanno già state+unit nel template
            pass
        
        elif domain == "media_player" and state == "playing":
            title = attributes.get("media_title")
            artist = attributes.get("media_artist")
            if title:
                info = title
                if artist:
                    info = f"{artist} - {title}"
                extras.append(f"riproduce {info}")
        
        return ", ".join(extras)
    
    def _extract_entity_from_text(self, text: str) -> Optional[str]:
        """Tenta di estrarre un entity_id dal testo libero."""
        # Pattern: dominio.nome (es. cover.tapparella_salotto)
        match = re.search(r'([a-z_]+\.[a-z_0-9]+)', text.lower())
        if match:
            return match.group(1)
        return None
    
    def get_parameters_schema(self) -> Dict[str, Any]:
        """Schema per LLM function calling."""
        return {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": (
                        "Home Assistant entity ID to query (e.g. cover.tapparella_salotto, "
                        "light.cucina, sensor.temperatura_soggiorno, climate.termostato). "
                        "If omitted, use 'domain' to query all entities of a type."
                    ),
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "Entity domain to query multiple entities "
                        "(e.g. light, cover, sensor, climate, switch, media_player)"
                    ),
                },
            },
            "required": [],
        }
