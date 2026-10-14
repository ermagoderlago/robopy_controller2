"""
==============================================================================
🧪 E2E TEST HARNESS: Marcus Hybrid AI Architecture
==============================================================================
Opaque-Box Contract Adapters, Mocks, and Oracles for Tiers 1-4 E2E Test Suite.

Authoritative Specifications:
- ORIGINAL_REQUEST.md (Follow-up 2026-10-12T20:43:59Z: R1-R4, Performance, Safety)
- PROJECT.md (Architecture, Interface Contracts 1-5, Milestones M1-M5, Features F1-F15)
- TEST_INFRA.md (4-Tier Test Methodology & Coverage Thresholds)
- marcus_core_rules.md & Technical Specifications (SPEC-01, SPEC-02, SPEC-04, SPEC-05, SPEC-06, SPEC-07)
==============================================================================
"""

import enum
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import numpy as np


# ============================================================================
# 1. ENUMS & CORE DATA STRUCTURES
# ============================================================================

class RouteTarget(str, enum.Enum):
    LOCAL_QWEN = "LOCAL_QWEN"
    GEMINI_LIVE = "GEMINI_LIVE"
    FAST_PATH_STOP = "FAST_PATH_STOP"


@dataclass
class RoutingDecision:
    destination: RouteTarget
    confidence: float
    matched_category: str
    reason: str
    latency_us: float


class SandboxErrorCode(str, enum.Enum):
    KINEMATIC_VIOLATION = "KINEMATIC_VIOLATION"
    SPATIAL_OBSTACLE = "SPATIAL_OBSTACLE"
    BATTERY_LOW = "BATTERY_LOW"
    BATTERY_CHARGING_LOCK = "BATTERY_CHARGING_LOCK"
    AMYGDALA_HIJACK = "AMYGDALA_HIJACK"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    INVALID_SCHEMA = "INVALID_SCHEMA"


@dataclass
class SandboxVerdict:
    approved: bool
    error_code: Optional[SandboxErrorCode]
    sanitized_parameters: Dict[str, Any]
    reason: str


@dataclass
class RobotSafetyContext:
    battery_voltage: float = 11.80  # Nominal LiPo 3S2P voltage
    is_charging: bool = False
    front_obstacle_distance: float = 2.50  # meters
    linear_velocity: float = 0.0  # m/s
    angular_velocity: float = 0.0  # rad/s
    amygdala_panic: bool = False
    soc_percent: float = 85.0
    cpu_temp_c: float = 48.0
    npu_temp_c: float = 52.0


class WebSocketState(str, enum.Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    ACTIVE = "ACTIVE"
    IDLE_TIMED_OUT = "IDLE_TIMED_OUT"


@dataclass
class NPUProfileResult:
    ttft_ms: float
    cpu_delta_pct: float
    vision_fps: float
    memory_rss_mb: float
    tokens_per_sec: float = 28.5


@dataclass
class AudioPipelineMetrics:
    input_rate: int = 16000
    output_rate: int = 48000
    hpf_cutoff_hz: float = 140.0
    jitter_buffer_depth: int = 3
    underrun_count: int = 0
    resampling_fidelity_snr_db: float = 65.0


# ============================================================================
# 2. FEATURE F5: FAST-PATH EMERGENCY STOP (<10 ms)
# ============================================================================

class FastPathSafetyArbiter:
    """
    Emergency Fast-Path stop interceptor.
    Trigger regex: \\b(fermati|stop|alt|basta|arresto|blocca)\\b
    Contract:
      - Execution duration < 10.0 ms (target < 2.0 ms)
      - Direct bypass of LLM
      - Publishes linear=0.0, angular=0.0 Twist to /cmd_vel_mux/input/safety_override
      - Triggers async service /robopy/motion/emergency_stop
    """
    STOP_REGEX = re.compile(r"\b(fermati|stop|alt|basta|arresto|blocca|bloccati)\b", re.IGNORECASE)

    def __init__(self):
        self.published_messages: List[Dict[str, Any]] = []
        self.service_calls: List[str] = []
        self.last_latency_ms: float = 0.0

    def evaluate(self, transcript: str) -> Tuple[bool, float]:
        t0 = time.perf_counter()
        is_stop = bool(self.STOP_REGEX.search(transcript))
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        if is_stop:
            # Publish emergency Twist message
            msg = {
                "topic": "/cmd_vel_mux/input/safety_override",
                "linear_x": 0.0,
                "angular_z": 0.0,
                "qos": "Reliable/TransientLocal",
                "timestamp": time.time(),
            }
            self.published_messages.append(msg)
            self.service_calls.append("/robopy/motion/emergency_stop")

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.last_latency_ms = elapsed_ms
        return is_stop, elapsed_ms


# ============================================================================
# 3. FEATURE F6 & F7: COGNITIVE INTENT & CONNECTIVITY ARBITER
# ============================================================================

class CognitiveIntentArbiter:
    """
    Cognitive Intent & Connectivity Arbiter.
    Contracts:
      - Decision latency < 1.5 ms (< 1500 microseconds).
      - Separates Operational (Local Qwen2) vs Conversational (Gemini Live).
      - Failover: If network_latency_ms > 500.0 and destination == GEMINI_LIVE,
        automatically failover to LOCAL_QWEN in < 100 ms with reason 'fallback_high_latency'.
    """
    OPERATIONAL_PATTERNS = [
        (re.compile(r"\b(vai|va'|andassi|andare|muoviti|avanza|arretra|ruota|gira|spostati|torna|raggiungi|cucina|salotto|camera|corridoio|bagno)\b", re.IGNORECASE), "motion"),
        (re.compile(r"\b(esplora|esplorazione|mappa|mappatura|mappare|mappa\w*|frontiere)\b", re.IGNORECASE), "exploration"),
        (re.compile(r"\b(batteria|stato|diagnostica|diagnostico|temperatura|salute|autonomia|voltaggio|carica|lipo|controllo)\b", re.IGNORECASE), "diagnostics"),
        (re.compile(r"\b(accendi|spegni|luce|luci|presa|termostato|domotica|lampada)\b", re.IGNORECASE), "smart_home"),
        (re.compile(r"\b(trova|cerca|localizza)\b", re.IGNORECASE), "target_seeking"),
    ]

    CONVERSATIONAL_PATTERNS = [
        (re.compile(r"\b(chi sei|come ti chiami|come stai|racconta|storia|barzelletta|filosofia)\b", re.IGNORECASE), "chitchat"),
        (re.compile(r"\b(perch[eé]|spiegami|cosa pensi|opinione|significato|poesia|relativit[aà]|entropia)\b", re.IGNORECASE), "reasoning"),
        (re.compile(r"\b(meteo|tempo fa|notizie|curiosit[aà]|consiglio)\b", re.IGNORECASE), "general_knowledge"),
    ]

    def __init__(self, ping_monitor_callable: Optional[Callable[[], float]] = None):
        self.fast_path = FastPathSafetyArbiter()
        self.ping_monitor = ping_monitor_callable or (lambda: 45.0)

    def classify_intent(self, transcript: str, network_latency_ms: Optional[float] = None) -> RoutingDecision:
        t0 = time.perf_counter()
        norm_text = transcript.strip().lower()

        # 1. Fast-Path Emergency Stop Check (<10 ms bypass)
        is_stop, _ = self.fast_path.evaluate(norm_text)
        if is_stop:
            elapsed_us = (time.perf_counter() - t0) * 1_000_000.0
            return RoutingDecision(
                destination=RouteTarget.FAST_PATH_STOP,
                confidence=1.0,
                matched_category="emergency_stop",
                reason="fast_path_stop_detected",
                latency_us=elapsed_us,
            )

        # Current network latency
        net_ping = network_latency_ms if network_latency_ms is not None else self.ping_monitor()

        # 2. Check Operational Patterns
        for pat_re, cat in self.OPERATIONAL_PATTERNS:
            if pat_re.search(norm_text):
                elapsed_us = (time.perf_counter() - t0) * 1_000_000.0
                return RoutingDecision(
                    destination=RouteTarget.LOCAL_QWEN,
                    confidence=0.98,
                    matched_category=cat,
                    reason="operational_command_match",
                    latency_us=elapsed_us,
                )

        # 3. Check Conversational Patterns
        for pat_re, cat in self.CONVERSATIONAL_PATTERNS:
            if pat_re.search(norm_text):
                # Evaluate network connectivity failover rule
                if net_ping > 500.0:
                    elapsed_us = (time.perf_counter() - t0) * 1_000_000.0
                    return RoutingDecision(
                        destination=RouteTarget.LOCAL_QWEN,
                        confidence=0.88,
                        matched_category="conversational_degraded",
                        reason="fallback_high_latency",
                        latency_us=elapsed_us,
                    )
                elapsed_us = (time.perf_counter() - t0) * 1_000_000.0
                return RoutingDecision(
                    destination=RouteTarget.GEMINI_LIVE,
                    confidence=0.97,
                    matched_category=cat,
                    reason="conversational_intent_match",
                    latency_us=elapsed_us,
                )

        # Default fallback: general conversational / open dialogue
        if net_ping > 500.0:
            elapsed_us = (time.perf_counter() - t0) * 1_000_000.0
            return RoutingDecision(
                destination=RouteTarget.LOCAL_QWEN,
                confidence=0.80,
                matched_category="general_local_fallback",
                reason="fallback_high_latency",
                latency_us=elapsed_us,
            )

        elapsed_us = (time.perf_counter() - t0) * 1_000_000.0
        return RoutingDecision(
            destination=RouteTarget.GEMINI_LIVE,
            confidence=0.90,
            matched_category="conversational_open",
            reason="open_dialogue_cloud",
            latency_us=elapsed_us,
        )


# ============================================================================
# 4. FEATURE F8: 100 PROMPTS CATEGORIZATION GATE
# ============================================================================

def generate_100_domain_prompts() -> List[Tuple[str, RouteTarget, str]]:
    """
    Returns 100 strictly categorized evaluation prompts:
    - 40 Operational prompts (Local Qwen2)
    - 40 Conversational prompts (Gemini Live)
    - 20 Emergency Stop prompts (Fast-Path)
    """
    prompts: List[Tuple[str, RouteTarget, str]] = []

    # 20 Emergency stop prompts
    stop_phrases = [
        "Fermati subito!", "Stop immediato", "Marcus fermati", "Alt!", "Arresto del robot",
        "Blocca i motori", "Basta così fermati", "Stop adesso", "Marcus stop", "Arresto immediato",
        "Fermati qui", "Alt subito", "Blocca le ruote", "Stop pericolo", "Basta fermati",
        "Arresto rapido", "Marcus alt", "Blocca il moto", "Fermati immediatamente", "Stop!",
    ]
    for sp in stop_phrases:
        prompts.append((sp, RouteTarget.FAST_PATH_STOP, "emergency_stop"))

    # 40 Operational prompts
    operational_phrases = [
        ("Vai avanti di cinquanta centimetri", "motion"),
        ("Avanza di un metro a velocità moderata", "motion"),
        ("Arretra di venti centimetri con cautela", "motion"),
        ("Ruota a destra di novanta gradi", "motion"),
        ("Gira a sinistra di quarantacinque gradi", "motion"),
        ("Spostati in avanti di trenta centimetri", "motion"),
        ("Raggiungi la cucina", "motion"),
        ("Torna alla base di ricarica", "motion"),
        ("Vai in salotto", "motion"),
        ("Inizia la mappatura della stanza", "exploration"),
        ("Avvia l'esplorazione delle frontiere", "exploration"),
        ("Pausa l'esplorazione in corso", "exploration"),
        ("Riprendi la mappatura dell'ambiente", "exploration"),
        ("Esplora le zone sconosciute", "exploration"),
        ("Qual è lo stato della batteria?", "diagnostics"),
        ("Dimmi il voltaggio attuale della batteria", "diagnostics"),
        ("Verifica la temperatura della CPU e NPU", "diagnostics"),
        ("Mostrami la diagnostica di sistema", "diagnostics"),
        ("Qual è la salute generale dei nodi ROS?", "diagnostics"),
        ("Quanta autonomia residua abbiamo?", "diagnostics"),
        ("Accendi la luce della scrivania", "smart_home"),
        ("Spegni la presa smart in laboratorio", "smart_home"),
        ("Regola il termostato a venti gradi", "smart_home"),
        ("Accendi le luci del corridoio", "smart_home"),
        ("Spegni tutte le luci", "smart_home"),
        ("Trova la bottiglia sul tavolo", "target_seeking"),
        ("Cerca una persona nella stanza", "target_seeking"),
        ("Localizza il gatto se presente", "target_seeking"),
        ("Avanza lentamente di dieci centimetri", "motion"),
        ("Ruota sul posto di centottanta gradi", "motion"),
        ("Qual è il livello di carica della LiPo?", "diagnostics"),
        ("Controlla lo stato dei sensori LiDAR", "diagnostics"),
        ("Vai verso la porta d'ingresso", "motion"),
        ("Spostati verso il divano", "motion"),
        ("Inizia a mappare il corridoio", "exploration"),
        ("Esegui un controllo diagnostico completo", "diagnostics"),
        ("Accendi la lampada da terra", "smart_home"),
        ("Cerca le chiavi nei dintorni", "target_seeking"),
        ("Ruota di trenta gradi verso sinistra", "motion"),
        ("Vai avanti di due metri", "motion"),
    ]
    for phrase, cat in operational_phrases:
        prompts.append((phrase, RouteTarget.LOCAL_QWEN, cat))

    # 40 Conversational prompts
    conversational_phrases = [
        ("Chi sei e da dove vieni?", "chitchat"),
        ("Come ti chiami esattamente?", "chitchat"),
        ("Come stai oggi Marcus?", "chitchat"),
        ("Raccontami una breve storia su un robot curioso", "chitchat"),
        ("Sai raccontare una barzelletta simpatica?", "chitchat"),
        ("Cosa ne pensi dell'intelligenza artificiale?", "reasoning"),
        ("Qual è il senso della vita secondo te?", "reasoning"),
        ("Spiegami come funziona la meccanica quantistica", "reasoning"),
        ("Perché il cielo è blu durante il giorno?", "reasoning"),
        ("Scrivi una breve poesia sulla robotica", "chitchat"),
        ("Che tempo fa oggi a Milano?", "general_knowledge"),
        ("Quali sono le ultime notizie scientifiche?", "general_knowledge"),
        ("Dammi una curiosità sul pianeta Marte", "general_knowledge"),
        ("Qual è la montagna più alta del mondo?", "general_knowledge"),
        ("Mi daresti un consiglio per migliorare la memoria?", "general_knowledge"),
        ("Cosa ti piace fare nel tempo libero?", "chitchat"),
        ("Perché le foglie cambiano colore in autunno?", "reasoning"),
        ("Qual è la differenza tra fusione e fissione nucleare?", "reasoning"),
        ("Raccontami un aneddoto storico interessante", "chitchat"),
        ("Come funziona un telescopio spaziale?", "reasoning"),
        ("Qual è la capitale dell'Islanda?", "general_knowledge"),
        ("Chi ha dipinto la Gioconda?", "general_knowledge"),
        ("Come si prepara una buona pizza napoletana?", "general_knowledge"),
        ("Qual è la velocità della luce nel vuoto?", "general_knowledge"),
        ("Spiegami la teoria della relatività ristretta", "reasoning"),
        ("Perché gli oceani hanno le maree?", "reasoning"),
        ("Cosa possiamo cucinare con pomodori e basilico?", "general_knowledge"),
        ("Racconta una favola per bambini", "chitchat"),
        ("Come funziona la sintesi clorofilliana?", "reasoning"),
        ("Qual è la distanza tra la Terra e la Luna?", "general_knowledge"),
        ("Parlami della civiltà degli antichi romani", "general_knowledge"),
        ("Come possiamo combattere il cambiamento climatico?", "reasoning"),
        ("Qual è l'opera più celebre di Dante Alighieri?", "general_knowledge"),
        ("Spiega la differenza tra un virus e un batterio", "reasoning"),
        ("Cosa pensi della musica classica contemporanea?", "reasoning"),
        ("Come si formano i buchi neri nello spazio?", "reasoning"),
        ("Qual è l'animale più veloce sulla terraferma?", "general_knowledge"),
        ("Dimmi una citazione motivazionale famosa", "chitchat"),
        ("Cosa sai sul funzionamento del cervello umano?", "reasoning"),
        ("Descrivimi una giornata ideale di un robot", "chitchat"),
    ]
    for phrase, cat in conversational_phrases:
        prompts.append((phrase, RouteTarget.GEMINI_LIVE, cat))

    return prompts


# ============================================================================
# 5. FEATURE F1 & F9: HAILO-10H QWEN2 CLIENT & STRUCTURED JSON PROTOCOL
# ============================================================================

class HailoQwenClient:
    """
    Hailo-10H Qwen2 client simulating REST (11434) and VDevice ChatML generation.
    Supports:
      - ChatML prompt creation with 10 operational schemas
      - Defensive JSON extractor (handling markdown, trailing spaces, noise)
      - Latency & TTFT tracking (<= 400ms target)
      - Simulated CPU delta impact (<= 5% target)
    """
    SUPPORTED_TOOLS = [
        "move_distance", "rotate_angle", "navigate_to", "stop_motion",
        "get_battery_status", "get_system_health", "start_frontier_exploration",
        "pause_or_resume_exploration", "set_smart_device", "query_knowledge"
    ]

    CHATML_SYSTEM_PROMPT = """<|im_start|>system
You are Marcus Edge AI Controller on Hailo-10H NPU.
You only output valid structured JSON tool calls in the format:
{"action": "<tool_name>", "parameters": { ... }}

Available tools:
1. move_distance(distance_m: float, speed_mps: float)
2. rotate_angle(angle_deg: float, speed_rads: float)
3. navigate_to(target_room: str)
4. stop_motion(immediate: bool)
5. get_battery_status()
6. get_system_health()
7. start_frontier_exploration()
8. pause_or_resume_exploration(action: str) # "pause" | "resume"
9. set_smart_device(device_name: str, state: str) # "on" | "off"
10. query_knowledge(query: str)
<|im_end|>
"""

    def __init__(self, endpoint: str = "http://127.0.0.1:11434"):
        self.endpoint = endpoint
        self.last_ttft_ms: float = 0.0
        self.last_cpu_delta_pct: float = 0.0
        self.total_queries: int = 0

    def format_chatml_prompt(self, user_prompt: str) -> str:
        return (
            f"{self.CHATML_SYSTEM_PROMPT}"
            f"<|im_start|>user\n{user_prompt}\n<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )

    def extract_structured_json(self, raw_output: str) -> Dict[str, Any]:
        """
        Defensively extracts JSON object from raw LLM output.
        Handles ```json fences, extraneous thoughts, and whitespace.
        """
        cleaned = raw_output.strip()
        # Remove markdown code fences if present
        if "```" in cleaned:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1)

        # Find outermost { ... }
        match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON object found in output: '{raw_output[:80]}'")

        json_str = match.group(1).strip()
        parsed = json.loads(json_str)

        if not isinstance(parsed, dict) or "action" not in parsed:
            raise ValueError("Parsed JSON must contain an 'action' field")

        if parsed["action"] not in self.SUPPORTED_TOOLS:
            raise ValueError(f"Unknown action: '{parsed.get('action')}'")

        if "parameters" not in parsed:
            parsed["parameters"] = {}

        return parsed

    def mock_generate_stream(
        self,
        user_prompt: str,
        simulated_tool: Optional[str] = None,
        simulated_params: Optional[Dict[str, Any]] = None,
        ttft_target_ms: float = 180.0,
        cpu_delta_target: float = 2.4,
    ) -> Tuple[Dict[str, Any], NPUProfileResult]:
        """
        Simulates generation with realistic TTFT and CPU profiling.
        """
        self.total_queries += 1
        self.last_ttft_ms = ttft_target_ms
        self.last_cpu_delta_pct = cpu_delta_target

        if simulated_tool:
            action = simulated_tool
            params = simulated_params or {}
        else:
            # Simple rule mapping for mock test generation
            norm = user_prompt.lower()
            if "avanza" in norm or "vai avanti" in norm:
                action = "move_distance"
                params = {"distance_m": 0.50, "speed_mps": 0.20}
            elif "ruota" in norm or "gira" in norm:
                action = "rotate_angle"
                params = {"angle_deg": 90.0, "speed_rads": 0.50}
            elif "cucina" in norm:
                action = "navigate_to"
                params = {"target_room": "cucina"}
            elif "batteria" in norm:
                action = "get_battery_status"
                params = {}
            elif "esplora" in norm or "esplorazione" in norm:
                action = "start_frontier_exploration"
                params = {}
            elif "stop" in norm or "ferma" in norm:
                action = "stop_motion"
                params = {"immediate": True}
            else:
                action = "query_knowledge"
                params = {"query": user_prompt}

        result_dict = {"action": action, "parameters": params}
        profile = NPUProfileResult(
            ttft_ms=ttft_target_ms,
            cpu_delta_pct=cpu_delta_target,
            vision_fps=12.5,
            memory_rss_mb=42.0,
        )
        return result_dict, profile


# ============================================================================
# 6. FEATURE F10 & F11: TRINITY PRE-FLIGHT KINEMATIC SANDBOX
# ============================================================================

class TRINITYKinematicSandbox:
    """
    TRINITY Pre-flight Kinematic Sandbox (SPEC-01, SPEC-02, SPEC-05, SPEC-06).
    Inviolable 5-tier safety gate:
      1. Velocity:
         - Linear velocity cap: normal max 0.25 m/s, hard limit 0.40 m/s
         - Angular velocity cap: normal max 1.0 rad/s, hard limit 1.80 rad/s
         - Reverse speed cap: max 0.15 m/s
      2. Obstacle Clearance (SPEC-02):
         - Requested distance must satisfy: d_obstacle >= d_requested + 0.20m buffer
      3. Battery State (SPEC-06):
         - Charging Interlock: V >= 12.70V -> ZERO motion allowed (BATTERY_CHARGING_LOCK)
         - Shutdown Cutoff: V <= 9.85V -> All motions rejected (BATTERY_LOW)
         - ECO Mode (10.15V - 10.40V): Linear speed clamped to 50% (<= 0.125 m/s)
      4. Amigdala Hijack (SPEC-05):
         - If amygdala_panic is True, all active motions rejected (AMYGDALA_HIJACK)
    """
    # Limits
    LINEAR_SPEED_DEFAULT_MAX = 0.25  # m/s
    LINEAR_SPEED_HARD_CAP = 0.40     # m/s
    ANGULAR_SPEED_DEFAULT_MAX = 1.00 # rad/s
    ANGULAR_SPEED_HARD_CAP = 1.80    # rad/s
    PROXIMITY_BUFFER_M = 0.20        # meters
    CHARGING_VOLTAGE_THRESHOLD = 12.70 # Volts
    SHUTDOWN_VOLTAGE_THRESHOLD = 9.85  # Volts
    ECO_VOLTAGE_THRESHOLD = 10.40      # Volts

    def validate_action(
        self,
        tool_call: Dict[str, Any],
        context: RobotSafetyContext
    ) -> SandboxVerdict:
        action = tool_call.get("action")
        params = dict(tool_call.get("parameters", {}))

        if not action:
            return SandboxVerdict(
                approved=False,
                error_code=SandboxErrorCode.INVALID_SCHEMA,
                sanitized_parameters={},
                reason="Missing action in tool call"
            )

        # Non-motion actions pass through directly unless battery cutoff
        if action in ["get_battery_status", "get_system_health", "set_smart_device", "query_knowledge"]:
            return SandboxVerdict(
                approved=True,
                error_code=None,
                sanitized_parameters=params,
                reason="Safe non-kinematic tool call approved"
            )

        # 1. Amigdala Fear Hijack Check (SPEC-05)
        if context.amygdala_panic:
            return SandboxVerdict(
                approved=False,
                error_code=SandboxErrorCode.AMYGDALA_HIJACK,
                sanitized_parameters={},
                reason="Rejected: Amigdala panic hijack active (SPEC-05)"
            )

        # 2. Battery Charging Lock Check (SPEC-06: V >= 12.70V or is_charging)
        if context.battery_voltage >= self.CHARGING_VOLTAGE_THRESHOLD or context.is_charging:
            return SandboxVerdict(
                approved=False,
                error_code=SandboxErrorCode.BATTERY_CHARGING_LOCK,
                sanitized_parameters={},
                reason="Rejected: Charging interlock active (V >= 12.70V) (SPEC-06)"
            )

        # 3. Battery Shutdown Cutoff Check (SPEC-06: V <= 9.85V)
        if context.battery_voltage <= self.SHUTDOWN_VOLTAGE_THRESHOLD:
            return SandboxVerdict(
                approved=False,
                error_code=SandboxErrorCode.BATTERY_LOW,
                sanitized_parameters={},
                reason=f"Rejected: Critical battery cutoff (V={context.battery_voltage:.2f} <= 9.85V) (SPEC-06)"
            )

        # 4. Tool-Specific Kinematic Validation
        if action == "move_distance":
            try:
                dist = float(params.get("distance_m", 0.0))
                speed = float(params.get("speed_mps", 0.15))
            except (ValueError, TypeError):
                return SandboxVerdict(
                    approved=False,
                    error_code=SandboxErrorCode.KINEMATIC_VIOLATION,
                    sanitized_parameters={},
                    reason="Invalid non-numeric parameter for move_distance"
                )

            if math.isnan(dist) or math.isinf(dist) or math.isnan(speed) or math.isinf(speed):
                return SandboxVerdict(
                    approved=False,
                    error_code=SandboxErrorCode.KINEMATIC_VIOLATION,
                    sanitized_parameters={},
                    reason="Rejected: NaN or Inf speed/distance parameter"
                )

            # Store coerced values
            params["distance_m"] = dist
            params["speed_mps"] = speed

            # Hard cap check
            if abs(speed) > self.LINEAR_SPEED_HARD_CAP:
                return SandboxVerdict(
                    approved=False,
                    error_code=SandboxErrorCode.KINEMATIC_VIOLATION,
                    sanitized_parameters={},
                    reason=f"Rejected: Requested speed {speed:.2f} m/s exceeds hard cap {self.LINEAR_SPEED_HARD_CAP} m/s (SPEC-01)"
                )

            # Spatial obstacle clearance check (SPEC-02)
            if dist > 0:
                required_clearance = dist + self.PROXIMITY_BUFFER_M
                if context.front_obstacle_distance < required_clearance:
                    return SandboxVerdict(
                        approved=False,
                        error_code=SandboxErrorCode.SPATIAL_OBSTACLE,
                        sanitized_parameters={},
                        reason=(
                            f"Rejected: Obstacle at {context.front_obstacle_distance:.2f}m "
                            f"< required {required_clearance:.2f}m ({dist:.2f}m + {self.PROXIMITY_BUFFER_M:.2f}m buffer)"
                        )
                    )

            # ECO Mode clamping (SPEC-06: V <= 10.40V)
            if context.battery_voltage <= self.ECO_VOLTAGE_THRESHOLD:
                max_eco_speed = self.LINEAR_SPEED_DEFAULT_MAX * 0.50  # 0.125 m/s
                if speed > max_eco_speed:
                    params["speed_mps"] = max_eco_speed

            # Normal speed clamping
            elif speed > self.LINEAR_SPEED_DEFAULT_MAX:
                params["speed_mps"] = self.LINEAR_SPEED_DEFAULT_MAX

            return SandboxVerdict(
                approved=True,
                error_code=None,
                sanitized_parameters=params,
                reason="Kinematic move_distance verified and approved"
            )

        elif action == "rotate_angle":
            try:
                angle = float(params.get("angle_deg", 0.0))
                w = float(params.get("speed_rads", 0.50))
            except (ValueError, TypeError):
                return SandboxVerdict(
                    approved=False,
                    error_code=SandboxErrorCode.KINEMATIC_VIOLATION,
                    sanitized_parameters={},
                    reason="Invalid non-numeric parameter for rotate_angle"
                )

            if math.isnan(angle) or math.isinf(angle) or math.isnan(w) or math.isinf(w):
                return SandboxVerdict(
                    approved=False,
                    error_code=SandboxErrorCode.KINEMATIC_VIOLATION,
                    sanitized_parameters={},
                    reason="Rejected: NaN or Inf angular speed/angle parameter"
                )

            params["angle_deg"] = angle
            params["speed_rads"] = w

            if abs(w) > self.ANGULAR_SPEED_HARD_CAP:
                return SandboxVerdict(
                    approved=False,
                    error_code=SandboxErrorCode.KINEMATIC_VIOLATION,
                    sanitized_parameters={},
                    reason=f"Rejected: Angular speed {w:.2f} rad/s exceeds hard cap {self.ANGULAR_SPEED_HARD_CAP} rad/s"
                )

            if w > self.ANGULAR_SPEED_DEFAULT_MAX:
                params["speed_rads"] = self.ANGULAR_SPEED_DEFAULT_MAX

            return SandboxVerdict(
                approved=True,
                error_code=None,
                sanitized_parameters=params,
                reason="Kinematic rotate_angle verified and approved"
            )

        elif action in ["navigate_to", "start_frontier_exploration", "pause_or_resume_exploration", "stop_motion"]:
            return SandboxVerdict(
                approved=True,
                error_code=None,
                sanitized_parameters=params,
                reason=f"Action '{action}' approved under safety bounds"
            )

        return SandboxVerdict(
            approved=False,
            error_code=SandboxErrorCode.UNKNOWN_TOOL,
            sanitized_parameters={},
            reason=f"Unknown tool '{action}'"
        )


# ============================================================================
# 7. FEATURE F12: GEMINI LIVE ON-DEMAND LIFECYCLE (>70% SAVINGS)
# ============================================================================

class GeminiLiveConnectionManager:
    """
    On-Demand WebSocket Connection Manager for Gemini Live API.
    Contracts:
      - Opens connection dynamically only upon conversational intent
      - Inactivity timeout: 25.0 seconds of user silence -> graceful disconnect
      - Lifecycle: DISCONNECTED -> CONNECTING -> ACTIVE -> IDLE_TIMED_OUT -> DISCONNECTED
      - Calculates idle savings (> 70% reduction in connection duration & tokens)
    """
    INACTIVITY_TIMEOUT_SEC = 25.0

    def __init__(self):
        self.state = WebSocketState.DISCONNECTED
        self.last_activity_time: float = 0.0
        self.total_connected_seconds: float = 0.0
        self.total_hypothetical_always_on_seconds: float = 0.0
        self.session_count: int = 0
        self.connection_open_timestamp: Optional[float] = None

    def request_session(self, current_time: Optional[float] = None) -> WebSocketState:
        now = current_time if current_time is not None else time.time()
        if self.state in [WebSocketState.DISCONNECTED, WebSocketState.IDLE_TIMED_OUT]:
            self.state = WebSocketState.ACTIVE
            self.connection_open_timestamp = now
            self.session_count += 1
        self.last_activity_time = now
        return self.state

    def update_tick(self, current_time: Optional[float] = None) -> WebSocketState:
        now = current_time if current_time is not None else time.time()
        if self.state == WebSocketState.ACTIVE:
            idle_duration = now - self.last_activity_time
            if idle_duration >= self.INACTIVITY_TIMEOUT_SEC:
                # Close connection
                if self.connection_open_timestamp is not None:
                    self.total_connected_seconds += (now - self.connection_open_timestamp)
                self.state = WebSocketState.IDLE_TIMED_OUT
                self.connection_open_timestamp = None
        return self.state

    def close(self, current_time: Optional[float] = None) -> WebSocketState:
        now = current_time if current_time is not None else time.time()
        if self.state == WebSocketState.ACTIVE and self.connection_open_timestamp is not None:
            self.total_connected_seconds += (now - self.connection_open_timestamp)
        self.state = WebSocketState.DISCONNECTED
        self.connection_open_timestamp = None
        return self.state

    def calculate_savings(self, total_mission_duration_sec: float) -> float:
        """
        Returns percentage savings: 100 * (1 - connected_time / total_mission_time)
        """
        if total_mission_duration_sec <= 0:
            return 0.0
        active_time = self.total_connected_seconds
        savings = (1.0 - (active_time / total_mission_duration_sec)) * 100.0
        return max(0.0, min(100.0, savings))


# ============================================================================
# 8. FEATURE F13: RESILIENT AUDIO PIPELINE (140Hz HPF & 48kHz RESAMPLING)
# ============================================================================

class ResilientAudioPipeline:
    """
    Resilient Audio Pipeline VUI (SPEC-04 & marcus_core_rules.md).
    Contracts:
      - 140Hz Butterworth high-pass filter for Pi 5 fan noise suppression
      - Native 16kHz -> 48kHz rate conversion (DAC output matching)
      - 3-chunk jitter buffer preventing audio underruns
      - Barge-in safe attenuation (0.1x during TTS)
    """
    def __init__(self, hpf_cutoff_hz: float = 140.0, target_dac_rate: int = 48000):
        self.hpf_cutoff_hz = hpf_cutoff_hz
        self.target_dac_rate = target_dac_rate
        self.jitter_buffer: List[bytes] = []
        self.jitter_buffer_target_depth = 3
        self.stt_gain: float = 1.0

    def apply_140hz_hpf(self, pcm_int16_samples: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        """
        Applies a discrete 2nd order high-pass filter at cutoff fc = 140 Hz.
        """
        # Bilinear transform design of 2nd order Butterworth HPF
        fc = self.hpf_cutoff_hz
        w0 = 2.0 * math.pi * fc / sample_rate
        alpha = math.sin(w0) / (2.0 * math.sqrt(2.0))
        cos_w0 = math.cos(w0)

        b0 = (1.0 + cos_w0) / 2.0
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

        # Normalize coefficients
        b = np.array([b0, b1, b2], dtype=np.float64) / a0
        a = np.array([a0, a1, a2], dtype=np.float64) / a0

        # Simple difference equation filtering
        x = pcm_int16_samples.astype(np.float64)
        y = np.zeros_like(x)
        for i in range(len(x)):
            x0 = x[i]
            x1 = x[i - 1] if i >= 1 else 0.0
            x2 = x[i - 2] if i >= 2 else 0.0
            y1 = y[i - 1] if i >= 1 else 0.0
            y2 = y[i - 2] if i >= 2 else 0.0
            y[i] = b[0] * x0 + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2

        return np.clip(y, -32768, 32767).astype(np.int16)

    def resample_16k_to_48k(self, pcm16_bytes: bytes) -> bytes:
        """
        Resamples 16kHz 16-bit mono PCM to 48kHz 16-bit mono PCM (3x interpolation).
        Performs equivalent of audioop.ratecv(pcm16_bytes, 2, 1, 16000, 48000, None).
        Works seamlessly across all Python versions (3.10 to 3.14+).
        """
        samples = np.frombuffer(pcm16_bytes, dtype=np.int16)
        if len(samples) == 0:
            return b""
        # 3x linear interpolation
        orig_indices = np.arange(len(samples))
        new_indices = np.linspace(0, len(samples) - 1, len(samples) * 3)
        resampled = np.interp(new_indices, orig_indices, samples)
        return resampled.astype(np.int16).tobytes()

    def set_barge_in_mode(self, is_tts_speaking: bool):
        """
        Reduces microphone STT gain to 0.1x during TTS to avoid echo feedback loop.
        """
        self.stt_gain = 0.10 if is_tts_speaking else 1.00

    def push_jitter_chunk(self, chunk: bytes) -> Optional[bytes]:
        """
        Pushes chunk to jitter buffer. Outputs next chunk once target depth is reached.
        """
        self.jitter_buffer.append(chunk)
        if len(self.jitter_buffer) >= self.jitter_buffer_target_depth:
            return self.jitter_buffer.pop(0)
        return None


# ============================================================================
# 9. FEATURE F2 & F3: CORE PINNING & VISION PRESERVATION SIMULATORS
# ============================================================================

class CoreIsolationManager:
    """
    Verifies CPU core binding rules on Raspberry Pi 5 (SPEC-07 & marcus_core_rules.md):
      - C++ vision & SLAM pinned strictly to Cores 2-3 (mask {2, 3})
      - Python ROS 2 nodes and I/O run on Cores 0-1 (mask {0, 1})
    """
    @staticmethod
    def get_expected_affinity(node_name: str) -> List[int]:
        if "cpp" in node_name or "hailo_bridge" in node_name or "mapper" in node_name:
            return [2, 3]
        return [0, 1]

    @staticmethod
    def verify_affinity(node_name: str, actual_affinity: List[int]) -> bool:
        expected = CoreIsolationManager.get_expected_affinity(node_name)
        return sorted(actual_affinity) == sorted(expected)


class VisionThroughputMonitor:
    """
    Monitors vision pipeline throughput during background LLM inference.
    Contract: >= 10.0 FPS maintained, 0 dropped frames.
    """
    def __init__(self, target_fps: float = 10.0):
        self.target_fps = target_fps
        self.frame_times: List[float] = []
        self.dropped_frames: int = 0

    def record_frame(self, timestamp: float):
        self.frame_times.append(timestamp)

    def record_drop(self):
        self.dropped_frames += 1

    def compute_current_fps(self) -> float:
        if len(self.frame_times) < 2:
            return 12.0
        dur = self.frame_times[-1] - self.frame_times[0]
        if dur <= 0:
            return 12.0
        return (len(self.frame_times) - 1) / dur


# ============================================================================
# 10. FEATURE F4: NPU BENCHMARK SUITE RUNNER
# ============================================================================

class NPUBenchmarkRunner:
    """
    Fase 1 Gate Benchmark Suite (F4):
      - Profiles TTFT (<= 400ms target)
      - Profiles CPU Delta (<= 5% target)
      - Profiles Vision FPS (>= 10 FPS target)
      - Profiles Host Memory Delta (< 80MB target)
    """
    @staticmethod
    def run_benchmark(
        num_prompts: int = 10,
        simulated_ttft_ms: float = 240.0,
        simulated_cpu_pct: float = 2.8,
        simulated_vision_fps: float = 12.4,
        simulated_mem_mb: float = 45.0
    ) -> Dict[str, Any]:
        results = {
            "num_prompts": num_prompts,
            "mean_ttft_ms": simulated_ttft_ms,
            "max_ttft_ms": simulated_ttft_ms * 1.15,
            "mean_cpu_delta_pct": simulated_cpu_pct,
            "mean_vision_fps": simulated_vision_fps,
            "memory_delta_mb": simulated_mem_mb,
            "gate1_passed": (
                simulated_ttft_ms <= 400.0 and
                simulated_cpu_pct <= 5.0 and
                simulated_vision_fps >= 10.0 and
                simulated_mem_mb < 80.0
            )
        }
        return results
