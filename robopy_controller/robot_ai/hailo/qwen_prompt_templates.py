"""
Qwen Prompt Templates & Defensive JSON Call Extractor
=====================================================
ChatML prompt templates and resilient function-calling extractor for local
Qwen2 / Qwen2.5 (0.5B/1.5B) models executing on the Hailo-10H NPU (40 TOPS).

Complies with:
- SPEC-01 (Kinematic Limits)
- SPEC-02 (Navigation & Clearances)
- SPEC-05 (Cognitive Brain TRINITY & Token Budget <= 2500 tokens)
- Requirement R3 & Milestone 3 (Fase 3 Gate)
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("marcus.qwen_templates")

# Catalog of 10 Operational Functions Supported by Local Qwen2
OPERATIONAL_FUNCTIONS: List[Dict[str, Any]] = [
    {
        "name": "move_distance",
        "description": "Muove il robot in linea retta avanti o indietro per una distanza specificata.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["avanti", "indietro"],
                    "description": "Direzione del moto: 'avanti' o 'indietro'.",
                },
                "distance_m": {
                    "type": "number",
                    "minimum": 0.05,
                    "maximum": 2.00,
                    "description": "Distanza in metri da percorrere (0.05 <= d <= 2.00).",
                },
                "speed": {
                    "type": "number",
                    "minimum": 0.10,
                    "maximum": 0.25,
                    "default": 0.18,
                    "description": "Velocita lineare in m/s (default 0.18, max 0.25).",
                },
            },
            "required": ["direction", "distance_m"],
        },
    },
    {
        "name": "rotate_angle",
        "description": "Ruota il robot sul posto a sinistra o a destra di un angolo specificato.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["sinistra", "destra"],
                    "description": "Direzione di rotazione: 'sinistra' (antiorario) o 'destra' (orario).",
                },
                "degrees": {
                    "type": "number",
                    "minimum": 1.0,
                    "maximum": 180.0,
                    "description": "Angolo in gradi di rotazione (1.0 <= deg <= 180.0).",
                },
                "speed": {
                    "type": "number",
                    "minimum": 0.30,
                    "maximum": 1.00,
                    "default": 0.60,
                    "description": "Velocita angolare in rad/s (default 0.60, max 1.00).",
                },
            },
            "required": ["direction", "degrees"],
        },
    },
    {
        "name": "navigate_to",
        "description": "Naviga autonomamente verso una stanza o destinazione topologica salvata.",
        "parameters": {
            "type": "object",
            "properties": {
                "destination": {
                    "type": "string",
                    "enum": ["cucina", "salotto", "soggiorno", "camera", "bagno", "studio", "ingresso", "base"],
                    "description": "Destinazione target tra quelle registrate nella mappa.",
                },
            },
            "required": ["destination"],
        },
    },
    {
        "name": "stop_motion",
        "description": "Arresta immediatamente ogni movimento fisico in corso.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "default": "user_command",
                    "description": "Motivo dell'arresto (default: user_command).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_battery_status",
        "description": "Legge lo stato attuale della batteria (tensione, SoC %, stato di carica).",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_system_health",
        "description": "Legge lo stato di salute dell'hardware (temperature CPU/NPU, carichi, allarmi).",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "start_frontier_exploration",
        "description": "Avvia l'esplorazione autonoma dell'ambiente basata su frontiere sconosciute.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "pause_or_resume_exploration",
        "description": "Mette in pausa o riprende l'esplorazione autonoma.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["pause", "resume"],
                    "description": "Azione: 'pause' o 'resume'.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "set_smart_device",
        "description": "Controlla un dispositivo domotico Home Assistant (luci, prese, etc.).",
        "parameters": {
            "type": "object",
            "properties": {
                "device_name": {
                    "type": "string",
                    "description": "Identificativo o nome del dispositivo domotico.",
                },
                "state": {
                    "type": "string",
                    "enum": ["on", "off"],
                    "description": "Stato target del dispositivo: 'on' o 'off'.",
                },
            },
            "required": ["device_name", "state"],
        },
    },
    {
        "name": "query_knowledge",
        "description": "Interroga la memoria autobiografica o Zettelkasten di Marcus per recuperare informazioni.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Testo o domanda da ricercare nella memoria MAG.",
                },
            },
            "required": ["query"],
        },
    },
]

VALID_ROOM_DESTINATIONS = frozenset([
    "cucina",
    "salotto",
    "soggiorno",
    "camera",
    "bagno",
    "studio",
    "ingresso",
    "base",
])

# ChatML System Prompt Template for Local Qwen2
QWEN2_SYSTEM_PROMPT = """<|im_start|>system
Sei Marcus, un assistente robotico autonomo. Esegui comandi operativi rispondendo ESCLUSIVAMENTE con un blocco JSON valido contenente la funzione da invocare.
Non aggiungere spiegazioni, preamboli o markdown al di fuori del JSON.

FUNZIONI DISPONIBILI:
1. move_distance(direction: str, distance_m: float, speed: float)
   - direction in ["avanti", "indietro"]
   - distance_m in [0.05, 2.00]
   - speed in [0.10, 0.25] (default 0.18)
2. rotate_angle(direction: str, degrees: float, speed: float)
   - direction in ["sinistra", "destra"]
   - degrees in [1.0, 180.0]
   - speed in [0.30, 1.00] (default 0.60)
3. navigate_to(destination: str)
   - destination in ["cucina", "salotto", "soggiorno", "camera", "bagno", "studio", "ingresso", "base"]
4. stop_motion(reason: str)
5. get_battery_status()
6. get_system_health()
7. start_frontier_exploration()
8. pause_or_resume_exploration(action: str)
   - action in ["pause", "resume"]
9. set_smart_device(device_name: str, state: str)
   - state in ["on", "off"]
10. query_knowledge(query: str)

FORMATO RISPOSTA:
{"call": {"name": "<nome_funzione>", "arguments": {<chiave>: <valore>}}}
<|im_end|>"""


def format_qwen_chatml_prompt(user_transcript: str, system_override: Optional[str] = None) -> str:
    """
    Format a full ChatML prompt for Qwen2/Qwen2.5 on Hailo-10H NPU.
    
    Args:
        user_transcript: The user query or transcription.
        system_override: Optional custom system prompt string.
        
    Returns:
        Formatted ChatML string ready for Hailo tokenization.
    """
    sys_part = system_override.strip() if system_override else QWEN2_SYSTEM_PROMPT.strip()
    clean_user = user_transcript.strip()
    return f"{sys_part}\n<|im_start|>user\n{clean_user}\n<|im_end|>\n<|im_start|>assistant\n"


@dataclass
class RawJsonCall:
    """Representation of an extracted function call before sandbox validation."""
    function_name: str
    arguments: Dict[str, Any]
    raw_payload: str
    extraction_latency_us: float = 0.0
    repaired: bool = False


class JsonCallExtractor:
    """
    Defensive JSON Extractor & Repair Engine for Small Language Models (0.5B/1.5B).
    
    Tolerates:
    - Markdown backtick fences (```json ... ``` or ``` ... ```)
    - Preamble and postscript conversational text
    - Unbalanced outer brackets and trailing commas
    - Both {"call": {"name": ..., "arguments": ...}} and {"action": ..., "parameters": ...}
    - Direct {"name": ..., "arguments": ...} formats
    """

    _FENCE_REGEX = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    _TRAILING_COMMA_REGEX = re.compile(r",\s*([\]}])")

    @classmethod
    def extract_call(cls, raw_text: str) -> Tuple[Optional[RawJsonCall], Optional[str]]:
        """
        Extract and defensively parse a JSON call from model output.
        
        Args:
            raw_text: Raw output string from Qwen2.
            
        Returns:
            Tuple of (RawJsonCall or None, error_message or None)
        """
        t0 = time.perf_counter()
        if not raw_text or not raw_text.strip():
            return None, "Empty model output"

        clean_text = raw_text.strip()
        repaired = False

        # Step 1: Strip markdown fences if present
        fence_match = cls._FENCE_REGEX.search(clean_text)
        candidate = fence_match.group(1).strip() if fence_match else clean_text

        # Step 2: Bracket balancing to extract the primary JSON object
        json_str, bracket_repaired = cls._extract_outermost_json_object(candidate)
        if json_str is None:
            # Fallback search anywhere in clean_text
            json_str, bracket_repaired = cls._extract_outermost_json_object(clean_text)

        if json_str is None:
            elapsed_us = (time.perf_counter() - t0) * 1_000_000
            return None, f"No JSON object found in output (processed in {elapsed_us:.1f}µs)"

        if bracket_repaired:
            repaired = True

        # Step 3: Parse JSON with fallback repairs
        parsed, parse_repaired, parse_err = cls._safe_json_parse(json_str)
        if parsed is None or not isinstance(parsed, dict):
            elapsed_us = (time.perf_counter() - t0) * 1_000_000
            return None, f"JSON parse failure: {parse_err} (in {elapsed_us:.1f}µs)"

        if parse_repaired:
            repaired = True

        # Step 4: Normalize schema variations
        norm_res = cls._normalize_call_structure(parsed)
        if norm_res is None:
            elapsed_us = (time.perf_counter() - t0) * 1_000_000
            return None, f"Malformed call envelope: unrecognized function calling schema in {parsed}"

        func_name, args = norm_res
        elapsed_us = (time.perf_counter() - t0) * 1_000_000

        return RawJsonCall(
            function_name=func_name,
            arguments=args,
            raw_payload=raw_text,
            extraction_latency_us=elapsed_us,
            repaired=repaired,
        ), None

    @classmethod
    def _extract_outermost_json_object(cls, text: str) -> Tuple[Optional[str], bool]:
        """Find the outermost balanced {...} substring, repairing missing closing braces if needed."""
        start_idx = text.find("{")
        if start_idx == -1:
            return None, False

        depth = 0
        in_string = False
        escape = False
        end_idx = -1

        for i in range(start_idx, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue

            if not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end_idx = i
                        break

        if end_idx != -1:
            return text[start_idx : end_idx + 1], False

        # If unbalanced (depth > 0) due to truncation at the end, attempt auto-closing
        if depth > 0:
            extracted = text[start_idx:]
            # Close unclosed strings if odd quotes
            if in_string:
                extracted += '"'
            extracted += "}" * depth
            return extracted, True

        return None, False

    @classmethod
    def _safe_json_parse(cls, json_str: str) -> Tuple[Optional[Any], bool, Optional[str]]:
        """Parse JSON with auto-healing for trailing commas and loose quotes."""
        # Attempt 1: Standard parser
        try:
            return json.loads(json_str), False, None
        except Exception:
            pass

        # Attempt 2: Strip trailing commas
        repaired_str = cls._TRAILING_COMMA_REGEX.sub(r"\1", json_str)
        try:
            return json.loads(repaired_str), True, None
        except Exception:
            pass

        # Attempt 3: Single quote to double quote substitution if valid
        try:
            # Simple heuristic replacement for Python dict string outputs
            sq_repaired = re.sub(r"(?<!\\)'", '"', repaired_str)
            return json.loads(sq_repaired), True, None
        except Exception as exc:
            return None, False, str(exc)

    @classmethod
    def _normalize_call_structure(cls, data: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Normalize varied call structures into (function_name, arguments_dict)."""
        # Form 1: {"call": {"name": "...", "arguments": {...}}}
        if "call" in data and isinstance(data["call"], dict):
            inner = data["call"]
            name = inner.get("name")
            args = inner.get("arguments", {})
            if isinstance(name, str) and isinstance(args, dict):
                return name.strip(), args

        # Form 2: {"action": "...", "parameters": {...}}
        if "action" in data and isinstance(data["action"], str):
            name = data["action"].strip()
            args = data.get("parameters", {})
            if isinstance(args, dict):
                return name, args

        # Form 3: {"name": "...", "arguments": {...}}
        if "name" in data and isinstance(data["name"], str):
            name = data["name"].strip()
            args = data.get("arguments", {})
            if isinstance(args, dict):
                return name, args

        # Form 4: {"function": "...", "parameters": {...}}
        if "function" in data and isinstance(data["function"], str):
            name = data["function"].strip()
            args = data.get("arguments", data.get("parameters", {}))
            if isinstance(args, dict):
                return name, args

        # Form 5: {"function_name": "...", "arguments": {...}}
        if "function_name" in data and isinstance(data["function_name"], str):
            name = data["function_name"].strip()
            args = data.get("arguments", {})
            if isinstance(args, dict):
                return name, args

        return None
