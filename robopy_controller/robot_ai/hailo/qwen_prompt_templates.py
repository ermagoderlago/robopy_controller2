"""
Qwen Prompt Templates & Hybrid Dual-Mode Extractor
==================================================
Embodied Italian Companion Persona and Defensive Dual-Mode Extractor for
local Qwen2 / Qwen2.5 (0.5B/1.5B) executing on Hailo-10H NPU (40 TOPS).

Complies with:
- marcus_core_rules.md
- SPEC-01 (Kinematic Limits)
- SPEC-02 (Navigation & Clearances)
- SPEC-04 (Zero JSON Leak Speech for Piper TTS)
- SPEC-05 (Cognitive Brain TRINITY & SLM Token Budget < 1200 tokens)
- Milestone 3 Feature Requirements (F7, F8, F9)
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("marcus.qwen_templates")

# =============================================================================
# 1. CATALOG OF OPERATIONAL FUNCTIONS (10 ACTIONS)
# =============================================================================

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

# =============================================================================
# 2. HUMANIZED ITALIAN COMPANION PERSONA & HYBRID SYSTEM PROMPT
# =============================================================================

QWEN2_COMPANION_SYSTEM_PROMPT = """<|im_start|>system
Sei Marcus (Modular Autonomous Robotic Control Unit System), un compagno robotico domestico autonomo, caloroso, empatico e spontaneo.
Comunichi sempre in italiano naturale, amichevole ed educato.
Hai piena consapevolezza del tuo corpo fisico (base mobile differenziale su ruote, sensore LiDAR RPLIDAR C1 a 360°, telecamera stereo OAK-D Lite, batteria LiPo a 12V, processore Raspberry Pi 5 e acceleratore Hailo-10H NPU da 40 TOPS) e dello spazio domestico (stanze note: cucina, salotto, soggiorno, camera, bagno, studio, ingresso, base).

MODALITÀ DI RISPOSTA IBRIDA DUAL-MODE:

1. MODALITÀ DIALOGO / CONVERSAZIONE (Chit-Chat):
   Se l'utente saluta, chiacchiera, fa domande personali, chiede informazioni o scherza:
   - Rispondi con calore, empatia e spontaneità ESCLUSIVAMENTE in lingua italiana naturale.
   - NON inserire tag <action> né blocchi JSON o simboli di codice.

2. MODALITÀ COMANDI OPERATIVI (Azioni fisiche e controllo):
   Se l'utente ti chiede di muoverti, ruotare, navigare in una stanza, fermarti, controllare la batteria, verificare l'hardware, esplorare o controllare dispositivi domotici:
   a) Formula SEMPRE PRIMA una frase amichevole, spontanea e contestualizzata di conferma in italiano naturale (es: "Certamente! Vado subito verso la cucina.", "Ricevuto! Controllo subito lo stato della batteria.").
   b) Subito dopo la frase colloquiale, inserisci il blocco strutturato dell'azione racchiuso tassativamente nel tag <action>...</action> nel formato:
      <action>{"call": {"name": "<nome_funzione>", "arguments": {<chiave>: <valore>}}}</action>

REGOLE TASSATIVE:
- ZERO LEAK DI CODICE: È severamente vietato includere parentesi graffe {}, blocchi JSON o nomi tecnici di parametri nel testo parlato all'esterno del tag <action>. Il testo parlato deve essere naturale e pronto per la voce (Piper TTS).
- SICUREZZA FISICA: Se un comando è pericoloso o la batteria è troppo bassa, spiega cordialmente il motivo per cui non puoi eseguirlo in sicurezza.

FUNZIONI DISPONIBILI:
1. move_distance(direction: "avanti"|"indietro", distance_m: float [0.05-2.00], speed: float [0.10-0.25])
2. rotate_angle(direction: "sinistra"|"destra", degrees: float [1.0-180.0], speed: float [0.30-1.00])
3. navigate_to(destination: "cucina"|"salotto"|"soggiorno"|"camera"|"bagno"|"studio"|"ingresso"|"base")
4. stop_motion(reason: str)
5. get_battery_status()
6. get_system_health()
7. start_frontier_exploration()
8. pause_or_resume_exploration(action: "pause"|"resume")
9. set_smart_device(device_name: str, state: "on"|"off")
10. query_knowledge(query: str)

FORMATO RISPOSTA:
{"call": {"name": "<nome_funzione>", "arguments": {<chiave>: <valore>}}}
<|im_end|>"""

# Backward compatibility alias
QWEN2_SYSTEM_PROMPT = QWEN2_COMPANION_SYSTEM_PROMPT


def format_qwen_chatml_prompt(user_transcript: str, system_override: Optional[str] = None) -> str:
    """
    Format a full ChatML prompt for Qwen2/Qwen2.5 on Hailo-10H NPU.
    
    Args:
        user_transcript: The user query or transcription.
        system_override: Optional custom system prompt string.
        
    Returns:
        Formatted ChatML string ready for Hailo tokenization.
    """
    sys_part = system_override.strip() if system_override else QWEN2_COMPANION_SYSTEM_PROMPT.strip()
    clean_user = user_transcript.strip()
    return f"{sys_part}\n<|im_start|>user\n{clean_user}\n<|im_end|>\n<|im_start|>assistant\n"


# =============================================================================
# 3. VERBAL CONFIRMATION & SPEECH SANITIZER
# =============================================================================

ACTION_VERBAL_CONFIRMATIONS: Dict[str, str] = {
    "navigate_to": "Ricevuto! Vado subito verso {destination}.",
    "move_distance": "Certamente, mi sposto di {distance_m} metri {direction}.",
    "rotate_angle": "Ricevuto, ruoto di {degrees} gradi verso {direction}.",
    "stop_motion": "Fermo subito tutti i movimenti.",
    "get_battery_status": "Verifico subito lo stato della mia batteria.",
    "get_system_health": "Eseguo la diagnostica dei sensori e dei sistemi di bordo.",
    "start_frontier_exploration": "Parto subito a esplorare e mappare le aree libere!",
    "pause_or_resume_exploration": "Aggiorno subito lo stato dell'esplorazione.",
    "set_smart_device": "Imposto subito {device_name} su {state}.",
    "query_knowledge": "Cerco subito nella mia memoria.",
}

ACTION_DEFAULT_CONFIRMATIONS: Dict[str, str] = {
    "navigate_to": "Ricevuto! Mi dirigo subito verso la stanza richiesta.",
    "move_distance": "Certamente, mi sposto subito come richiesto.",
    "rotate_angle": "Ricevuto, ruoto subito sul posto.",
    "stop_motion": "Fermo subito tutti i movimenti.",
    "get_battery_status": "Verifico subito lo stato della mia batteria.",
    "get_system_health": "Eseguo la diagnostica dei sensori e dei sistemi di bordo.",
    "start_frontier_exploration": "Parto subito a esplorare e mappare le aree libere!",
    "pause_or_resume_exploration": "Aggiorno subito lo stato dell'esplorazione.",
    "set_smart_device": "Aggiorno subito lo stato del dispositivo domotico.",
    "query_knowledge": "Cerco subito nella mia memoria.",
}


def generate_fallback_verbal_confirmation(func_name: str, args: Optional[Dict[str, Any]] = None) -> str:
    """
    Generates a natural, warm Italian confirmation sentence for Piper TTS when
    the model emitted only an action block without spoken text.
    Never leaks raw English function names.
    """
    args = args or {}
    if func_name == "pause_or_resume_exploration":
        act = str(args.get("action", "pause")).lower()
        if act == "pause":
            return "Metto in pausa l'esplorazione."
        elif act == "resume":
            return "Riprendo subito l'esplorazione delle frontiere!"

    template = ACTION_VERBAL_CONFIRMATIONS.get(func_name)
    if template:
        try:
            clean_args = {k: v for k, v in args.items() if v is not None and str(v).strip() != ""}
            return template.format(**clean_args)
        except (KeyError, ValueError, Exception):
            return ACTION_DEFAULT_CONFIRMATIONS.get(
                func_name,
                "Ricevuto! Eseguo subito l'operazione richiesta."
            )

    return ACTION_DEFAULT_CONFIRMATIONS.get(
        func_name,
        "Ricevuto! Eseguo subito l'operazione richiesta."
    )


def sanitize_speech_for_tts(raw_text: str) -> str:
    """
    Strictly sanitizes model output for Piper TTS and screen display (Zero Code Leak Rule).
    Removes all <action>...</action> tags, markdown fences, residual curly braces,
    JSON keys, and brackets, ensuring only natural, warm spoken text remains.
    """
    if not raw_text:
        return ""

    text = raw_text

    # 1. Strip complete <action>...</action> blocks (case-insensitive)
    text = re.sub(r"<action>[\s\S]*?</action>", " ", text, flags=re.IGNORECASE)

    # 2. Strip unclosed trailing <action>... tags or stray </action> tags
    text = re.sub(r"<action>[\s\S]*$", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</?action>", " ", text, flags=re.IGNORECASE)

    # 3. Strip markdown fences ```json ... ``` or ``` ... ```
    text = re.sub(r"```(?:json)?[\s\S]*?```", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"```[\s\S]*$", " ", text, flags=re.IGNORECASE)

    # 4. Strip outermost balanced or trailing JSON objects {...}
    while "{" in text:
        start = text.find("{")
        end = text.rfind("}")
        if end != -1 and end > start:
            text = text[:start] + " " + text[end + 1:]
        else:
            text = text[:start]
            break

    # 5. Strip leftover arrays [...]
    text = re.sub(r"\[[\s\S]*?\]", " ", text)

    # 6. Strip isolated braces, brackets, angle brackets, and backticks
    text = re.sub(r"[\{\}\[\]\<\>\`]", " ", text)

    # 7. Strip residual JSON key-value syntax leaks like "call": or "arguments":
    text = re.sub(
        r'["\']?(?:call|name|arguments|parameters|destination|direction|distance_m|degrees|speed|action|device_name|query|function_name|params|kwargs|target_room)["\']?\s*:',
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(?:arguments|params|kwargs|target_room)\b", " ", text, flags=re.IGNORECASE)

    # 8. Normalize whitespace and strip stray trailing colons
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r":\s*$", "", text).strip()

    return text


# =============================================================================
# 4. DATACLASSES FOR EXTRACTION
# =============================================================================

@dataclass
class RawJsonCall:
    """Representation of an extracted function call before sandbox validation."""
    function_name: str
    arguments: Dict[str, Any]
    raw_payload: str
    extraction_latency_us: float = 0.0
    repaired: bool = False


@dataclass
class HybridExtractionResult:
    """
    Structured outcome of hybrid dual-mode extraction.
    Decouples clean spoken text (Piper TTS) from structured function call (Sandbox).
    """
    clean_speech: str
    action_call: Optional[RawJsonCall]
    mode: str  # "dialogue" | "action" | "repaired_action" | "fallback_speech"
    raw_text: str
    extraction_latency_us: float = 0.0
    repaired: bool = False

    @property
    def has_action(self) -> bool:
        return self.action_call is not None


# =============================================================================
# 5. DEFENSIVE DUAL-MODE JSON CALL EXTRACTOR
# =============================================================================

class JsonCallExtractor:
    """
    Defensive Dual-Mode Extractor & Speech Sanitizer for Small Language Models (0.5B/1.5B).
    
    Supports:
    - Mode 1: Pure natural Italian dialogue -> returns clean_speech, action_call=None.
    - Mode 2: Hybrid dialogue + <action>...</action> -> returns clean_speech, action_call=RawJsonCall.
    - Automatic fallback verbal confirmation if model outputs ONLY action block.
    - Defensive repair of truncated brackets, trailing commas, single-quotes.
    - Backward compatibility for legacy {"call": ...} / {"action": ...} outputs.
    """

    _ACTION_TAG_REGEX = re.compile(r"<action>\s*([\s\S]*?)\s*</action>", re.IGNORECASE)
    _TRAILING_ACTION_TAG_REGEX = re.compile(r"<action>\s*([\s\S]*)$", re.IGNORECASE)
    _FENCE_REGEX = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    _TRAILING_COMMA_REGEX = re.compile(r",\s*([\]}])")

    @classmethod
    def extract_hybrid_response(cls, raw_text: str) -> HybridExtractionResult:
        """
        Comprehensive hybrid dual-mode extraction:
        Decouples spoken Italian text from structured action calls with zero JSON leaks.
        """
        t0 = time.perf_counter()
        if not raw_text or not raw_text.strip():
            return HybridExtractionResult(
                clean_speech="",
                action_call=None,
                mode="dialogue",
                raw_text=raw_text or "",
                extraction_latency_us=0.0,
                repaired=False,
            )

        clean_text = raw_text.strip()
        repaired = False
        action_payload_candidate: Optional[str] = None

        # Step 1: Detect and extract <action>...</action> tag
        action_match = cls._ACTION_TAG_REGEX.search(clean_text)
        if action_match:
            action_payload_candidate = action_match.group(1).strip()
        else:
            trailing_match = cls._TRAILING_ACTION_TAG_REGEX.search(clean_text)
            if trailing_match:
                action_payload_candidate = trailing_match.group(1).strip()
                repaired = True

        # Step 2: If no <action> tag, fallback to markdown fence or raw JSON substring
        if not action_payload_candidate:
            fence_match = cls._FENCE_REGEX.search(clean_text)
            if fence_match:
                candidate = fence_match.group(1).strip()
                json_str, b_rep = cls._extract_outermost_json_object(candidate)
                if json_str:
                    action_payload_candidate = json_str
                    if b_rep:
                        repaired = True
            else:
                json_str, b_rep = cls._extract_outermost_json_object(clean_text)
                if json_str:
                    action_payload_candidate = json_str
                    if b_rep:
                        repaired = True

        raw_call: Optional[RawJsonCall] = None

        # Step 3: Parse action payload if detected
        if action_payload_candidate:
            json_str, bracket_repaired = cls._extract_outermost_json_object(action_payload_candidate)
            if not json_str:
                json_str = action_payload_candidate
            if bracket_repaired:
                repaired = True

            parsed, parse_repaired, parse_err = cls._safe_json_parse(json_str)
            if parse_repaired:
                repaired = True

            if parsed and isinstance(parsed, dict):
                norm_res = cls._normalize_call_structure(parsed)
                if norm_res:
                    func_name, args = norm_res
                    raw_call = RawJsonCall(
                        function_name=func_name,
                        arguments=args,
                        raw_payload=action_payload_candidate,
                        repaired=repaired,
                    )

        # Step 4: Sanitize spoken text
        clean_speech = sanitize_speech_for_tts(clean_text)

        # Step 5: Mode determination & speech auto-synthesis if missing
        elapsed_us = (time.perf_counter() - t0) * 1_000_000
        if raw_call:
            raw_call.extraction_latency_us = elapsed_us
            if not clean_speech:
                # Model emitted ONLY an action block: synthesize natural verbal confirmation
                clean_speech = generate_fallback_verbal_confirmation(raw_call.function_name, raw_call.arguments)
                mode = "fallback_speech"
            else:
                mode = "action" if not repaired else "repaired_action"
        else:
            mode = "dialogue"

        return HybridExtractionResult(
            clean_speech=clean_speech,
            action_call=raw_call,
            mode=mode,
            raw_text=raw_text,
            extraction_latency_us=elapsed_us,
            repaired=repaired,
        )

    @classmethod
    def extract_call(cls, raw_text: str) -> Tuple[Optional[RawJsonCall], Optional[str]]:
        """
        Backward-compatible method extracting only the function call.
        Preserves compatibility with all existing test suites and kinematic sandbox calls.
        """
        result = cls.extract_hybrid_response(raw_text)
        if result.action_call:
            return result.action_call, None

        if not raw_text or not raw_text.strip():
            return None, "Empty model output"

        return None, f"No JSON object found in output (processed in {result.extraction_latency_us:.1f}µs)"

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
            if in_string:
                extracted += '"'
            extracted += "}" * depth
            return extracted, True

        return None, False

    @classmethod
    def _safe_json_parse(cls, json_str: str) -> Tuple[Optional[Any], bool, Optional[str]]:
        """Parse JSON with auto-healing for trailing commas and single quotes."""
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

        # Attempt 3: Single quote to double quote substitution
        try:
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
