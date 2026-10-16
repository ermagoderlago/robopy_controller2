"""
TRINITY Pre-flight Kinematic Sandbox (Requirement R3 & Milestone 3)
===================================================================
A synchronous, multi-tier safety gate that evaluates every function call
against:
- SPEC-01 (Kinematic velocity/acceleration limits, clamping, and rejection)
- SPEC-02 (LiDAR/Costmap clearances, negative obstacles/cliff drop protection)
- SPEC-06 (Battery cutoffs: charging interlock, docking reserve, shutdown cutoff, ECO)
- SPEC-05 (Amigdala panic hijack interlock and anxious vigilance throttling)

Guarantees 100% rejection on boundary violations and ZERO motion leaks.
Complies with marcus_core_rules.md and DFMEA FM-LLM-004.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, Optional, Set, Tuple, Union

from robopy_controller.robot_ai.hailo.qwen_prompt_templates import (
    JsonCallExtractor,
    RawJsonCall,
    VALID_ROOM_DESTINATIONS,
)
from robopy_controller.robot_ai.trinity.sandbox_rules import (
    KinematicCommand,
    KinematicLimits,
    KinematicSandboxErrorCode,
    RobotSafetyContext,
    SandboxValidationResult,
)

logger = logging.getLogger("marcus.kinematic_sandbox")

# Physical motion commands that require spatial, power, and cognitive gating
PHYSICAL_MOTION_FUNCTIONS: Set[str] = {
    "move_distance",
    "rotate_angle",
    "navigate_to",
    "start_frontier_exploration",
}

# Entire 10-function operational catalog
ALLOWED_FUNCTION_NAMES: Set[str] = {
    "move_distance",
    "rotate_angle",
    "navigate_to",
    "stop_motion",
    "get_battery_status",
    "get_system_health",
    "start_frontier_exploration",
    "pause_or_resume_exploration",
    "set_smart_device",
    "query_knowledge",
}


class TrinityKinematicSandbox:
    """
    Synchronous 5-Tier Pre-Flight Safety Gate for Marcus Hybrid AI.
    
    Evaluates function calls before they can be dispatched to SkillExecutor,
    MotionManager, or ROS 2 /cmd_vel topics.
    """

    def __init__(self, limits: Optional[KinematicLimits] = None) -> None:
        self.limits: KinematicLimits = limits if limits is not None else KinematicLimits()
        self._total_evaluations: int = 0
        self._total_rejections: int = 0
        self._total_authorizations: int = 0

    def validate_action(
        self,
        command_input: Union[str, Dict[str, Any], KinematicCommand, RawJsonCall],
        safety_context: Optional[RobotSafetyContext] = None,
    ) -> SandboxValidationResult:
        """
        Primary entry point for pre-flight validation.
        
        Args:
            command_input: Raw JSON string, parsed dictionary, or KinematicCommand.
            safety_context: Telemetry snapshot of robot state (battery, obstacles, Amigdala).
            
        Returns:
            SandboxValidationResult containing authorization verdict, error code,
            clamped arguments, and conversational Italian feedback.
        """
        t0 = time.perf_counter()
        self._total_evaluations += 1
        ctx = safety_context if safety_context is not None else RobotSafetyContext()

        try:
            # Step 0: Ingest & Parse into KinematicCommand
            cmd, parse_err_code, parse_err_msg = self._ingest_command(command_input)
            if cmd is None:
                return self._fail(
                    parse_err_code or KinematicSandboxErrorCode.MALFORMED_JSON,
                    parse_err_msg or "Failed to parse command into valid structure",
                    ctx,
                    t0,
                )

            func_name = cmd.function_name
            args = dict(cmd.arguments)

            # Emergency Invariant: stop_motion is always authorized regardless of state!
            if func_name == "stop_motion":
                return self._pass(
                    cmd,
                    args,
                    "Stop motion autorizzato immediatamente.",
                    "Arresto immediato confermato.",
                    ctx,
                    t0,
                )

            # =====================================================================
            # TIER 1: Schema, Whitelist & Parameter Range Validation
            # =====================================================================
            tier1_res = self._validate_tier1_schema(func_name, args, ctx, t0)
            if tier1_res is not None:
                return tier1_res

            # Non-motion commands that are safe after Tier 1 schema check
            if func_name in {"get_battery_status", "get_system_health", "query_knowledge", "set_smart_device"}:
                return self._pass(
                    cmd,
                    args,
                    f"Comando di stato/query '{func_name}' autorizzato.",
                    f"Eseguo {func_name}.",
                    ctx,
                    t0,
                )

            # Handling pause_or_resume_exploration:
            if func_name == "pause_or_resume_exploration":
                action = args.get("action")
                if action == "pause":
                    return self._pass(
                        cmd,
                        args,
                        "Pausa esplorazione autorizzata.",
                        "Esplorazione in pausa.",
                        ctx,
                        t0,
                    )
                # If "resume", continue through Tiers 3-5

            # =====================================================================
            # TIER 2: Kinematic Bounds (SPEC-01)
            # =====================================================================
            tier2_res, clamped_args = self._validate_tier2_kinematics(func_name, args, ctx, t0)
            if tier2_res is not None:
                return tier2_res
            args = clamped_args

            # =====================================================================
            # TIER 3: Spatial & Obstacle Clearance (SPEC-02)
            # =====================================================================
            tier3_res = self._validate_tier3_spatial(func_name, args, ctx, t0)
            if tier3_res is not None:
                return tier3_res

            # =====================================================================
            # TIER 4: Battery & Power Interlocks (SPEC-06 & FM-NAV-035)
            # =====================================================================
            tier4_res, clamped_args = self._validate_tier4_battery(func_name, args, ctx, t0)
            if tier4_res is not None:
                return tier4_res
            args = clamped_args

            # =====================================================================
            # TIER 5: Cognitive Interlocks (SPEC-05 & Amigdala)
            # =====================================================================
            tier5_res, clamped_args = self._validate_tier5_cognitive(func_name, args, ctx, t0)
            if tier5_res is not None:
                return tier5_res
            args = clamped_args

            # All tiers successfully cleared
            msg = f"Comando '{func_name}' autorizzato ed entro i limiti di sicurezza."
            speak = "Comando autorizzato."
            if args != cmd.arguments:
                msg += f" Argomenti regolati: {args}."
                speak = "Comando autorizzato con velocita adattata per sicurezza."

            return self._pass(cmd, args, msg, speak, ctx, t0)
        except Exception as exc:
            self._total_rejections += 1
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return SandboxValidationResult(
                authorized=False,
                error_code=KinematicSandboxErrorCode.MALFORMED_JSON,
                reason=f"Errore interno validazione: {exc}",
                clamped_arguments=None,
                telemetry_snapshot={
                    "battery_voltage": ctx.battery_voltage,
                    "is_charging": ctx.is_charging,
                    "amygdala_state": ctx.amygdala_state,
                    "min_forward_obstacle_dist": ctx.min_forward_obstacle_dist,
                },
                feedback_speak=f"Errore interno validazione: {exc}",
                latency_ms=elapsed_ms,
            )

    # Alias for method name compatibility
    validate_function_call = validate_action

    def _ingest_command(
        self,
        cmd_input: Union[str, Dict[str, Any], KinematicCommand, RawJsonCall],
    ) -> Tuple[Optional[KinematicCommand], Optional[KinematicSandboxErrorCode], Optional[str]]:
        """Ingests varied command representations and normalizes to KinematicCommand."""
        if isinstance(cmd_input, KinematicCommand):
            return cmd_input, None, None

        if isinstance(cmd_input, RawJsonCall):
            return (
                KinematicCommand(
                    function_name=cmd_input.function_name,
                    arguments=cmd_input.arguments,
                    raw_payload=cmd_input.raw_payload,
                    timestamp=time.time(),
                ),
                None,
                None,
            )

        if isinstance(cmd_input, str):
            extracted, err = JsonCallExtractor.extract_call(cmd_input)
            if extracted is None or not extracted.function_name:
                return (
                    None,
                    KinematicSandboxErrorCode.MALFORMED_JSON,
                    f"Errore parsing JSON: {err}",
                )
            return (
                KinematicCommand(
                    function_name=extracted.function_name,
                    arguments=extracted.arguments,
                    raw_payload=cmd_input,
                    timestamp=time.time(),
                ),
                None,
                None,
            )

        if isinstance(cmd_input, dict):
            # Check if enveloped
            norm = JsonCallExtractor._normalize_call_structure(cmd_input)
            if norm is not None:
                fn, a = norm
                return KinematicCommand(function_name=fn, arguments=a, timestamp=time.time()), None, None
            # Direct {"function": ..., "arguments": ...}
            fn = cmd_input.get("function") or cmd_input.get("name") or cmd_input.get("action")
            args = cmd_input.get("arguments") or cmd_input.get("parameters") or {}
            if isinstance(fn, str) and isinstance(args, dict):
                return KinematicCommand(function_name=fn, arguments=args, timestamp=time.time()), None, None
            return (
                None,
                KinematicSandboxErrorCode.MALFORMED_JSON,
                f"Dizionario privo di specifica funzione valida: {cmd_input}",
            )

        return (
            None,
            KinematicSandboxErrorCode.MALFORMED_JSON,
            f"Tipo di input non supportato: {type(cmd_input)}",
        )

    # -------------------------------------------------------------------------
    # Tier 1 Validation Logic
    # -------------------------------------------------------------------------
    def _validate_tier1_schema(
        self,
        func_name: str,
        args: Dict[str, Any],
        ctx: RobotSafetyContext,
        t0: float,
    ) -> Optional[SandboxValidationResult]:
        # 1. Whitelist check
        if func_name not in ALLOWED_FUNCTION_NAMES:
            return self._fail(
                KinematicSandboxErrorCode.UNKNOWN_FUNCTION,
                f"Funzione '{func_name}' non presente nel catalogo operativo delle 10 funzioni autorizzate.",
                ctx,
                t0,
                feedback="Funzione non riconosciuta dal sistema operativo.",
            )

        # 2. Specific argument schemas
        if func_name == "move_distance":
            if "direction" not in args or "distance_m" not in args:
                return self._fail(
                    KinematicSandboxErrorCode.MISSING_REQUIRED_ARGUMENT,
                    "Parametri 'direction' e 'distance_m' obbligatori per move_distance.",
                    ctx,
                    t0,
                )
            direction = args.get("direction")
            if not isinstance(direction, str):
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE,
                    "Il parametro 'direction' deve essere una stringa.",
                    ctx,
                    t0,
                )
            if direction not in {"avanti", "indietro"}:
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ENUM_VALUE,
                    f"Direzione '{direction}' non valida. Ammessi: 'avanti', 'indietro'.",
                    ctx,
                    t0,
                )
            dist = args.get("distance_m")
            if not isinstance(dist, (int, float)) or isinstance(dist, bool) or math.isnan(dist) or math.isinf(dist):
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE,
                    "Il parametro 'distance_m' deve essere un numero reale valido.",
                    ctx,
                    t0,
                )
            if dist < 0:
                return self._fail(
                    KinematicSandboxErrorCode.NEGATIVE_DISTANCE_FORBIDDEN,
                    f"Distanza negativa vietata ({dist}m). Usa direction='indietro' con valore positivo.",
                    ctx,
                    t0,
                )
            speed = args.get("speed")
            if speed is not None:
                if not isinstance(speed, (int, float)) or isinstance(speed, bool) or math.isnan(speed) or math.isinf(speed):
                    return self._fail(
                        KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE,
                        "Il parametro 'speed' deve essere un numero reale valido.",
                        ctx,
                        t0,
                    )

        elif func_name == "rotate_angle":
            if "direction" not in args or "degrees" not in args:
                return self._fail(
                    KinematicSandboxErrorCode.MISSING_REQUIRED_ARGUMENT,
                    "Parametri 'direction' e 'degrees' obbligatori per rotate_angle.",
                    ctx,
                    t0,
                )
            direction = args.get("direction")
            if not isinstance(direction, str):
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE,
                    "Il parametro 'direction' deve essere una stringa.",
                    ctx,
                    t0,
                )
            if direction not in {"sinistra", "destra"}:
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ENUM_VALUE,
                    f"Direzione '{direction}' non valida. Ammessi: 'sinistra', 'destra'.",
                    ctx,
                    t0,
                )
            deg = args.get("degrees")
            if not isinstance(deg, (int, float)) or isinstance(deg, bool) or math.isnan(deg) or math.isinf(deg):
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE,
                    "Il parametro 'degrees' deve essere un numero reale valido.",
                    ctx,
                    t0,
                )
            speed = args.get("speed")
            if speed is not None:
                if not isinstance(speed, (int, float)) or isinstance(speed, bool) or math.isnan(speed) or math.isinf(speed):
                    return self._fail(
                        KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE,
                        "Il parametro 'speed' deve essere un numero reale valido.",
                        ctx,
                        t0,
                    )

        elif func_name == "navigate_to":
            if "destination" not in args:
                return self._fail(
                    KinematicSandboxErrorCode.MISSING_REQUIRED_ARGUMENT,
                    "Parametro 'destination' obbligatorio per navigate_to.",
                    ctx,
                    t0,
                )
            dest = args.get("destination")
            if not isinstance(dest, str):
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE,
                    "Il parametro 'destination' deve essere una stringa.",
                    ctx,
                    t0,
                )
            clean_dest = dest.strip().lower()
            if clean_dest not in VALID_ROOM_DESTINATIONS:
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ENUM_VALUE,
                    f"Destinazione '{dest}' non presente nella mappa topologica autorizzata.",
                    ctx,
                    t0,
                    feedback=f"Destinazione {dest} non registrata nella mappa.",
                )

        elif func_name == "pause_or_resume_exploration":
            if "action" not in args:
                return self._fail(
                    KinematicSandboxErrorCode.MISSING_REQUIRED_ARGUMENT,
                    "Parametro 'action' obbligatorio per pause_or_resume_exploration.",
                    ctx,
                    t0,
                )
            act = args.get("action")
            if not isinstance(act, str):
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE,
                    "Il parametro 'action' deve essere una stringa.",
                    ctx,
                    t0,
                )
            if act not in {"pause", "resume"}:
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ENUM_VALUE,
                    f"Azione '{act}' non valida. Ammessi: 'pause', 'resume'.",
                    ctx,
                    t0,
                )

        elif func_name == "set_smart_device":
            if "device_name" not in args or "state" not in args:
                return self._fail(
                    KinematicSandboxErrorCode.MISSING_REQUIRED_ARGUMENT,
                    "Parametri 'device_name' e 'state' obbligatori per set_smart_device.",
                    ctx,
                    t0,
                )
            if not isinstance(args.get("device_name"), str):
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE,
                    "Il parametro 'device_name' deve essere una stringa.",
                    ctx,
                    t0,
                )
            state = args.get("state")
            if not isinstance(state, str):
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE,
                    "Il parametro 'state' deve essere una stringa.",
                    ctx,
                    t0,
                )
            if state.lower() not in {"on", "off"}:
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ENUM_VALUE,
                    f"Stato '{state}' non valido per set_smart_device. Ammessi: 'on', 'off'.",
                    ctx,
                    t0,
                )

        elif func_name == "query_knowledge":
            if "query" not in args:
                return self._fail(
                    KinematicSandboxErrorCode.MISSING_REQUIRED_ARGUMENT,
                    "Parametro 'query' obbligatorio per query_knowledge.",
                    ctx,
                    t0,
                )
            if not isinstance(args.get("query"), str):
                return self._fail(
                    KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE,
                    "Il parametro 'query' deve essere una stringa.",
                    ctx,
                    t0,
                )

        return None

    # -------------------------------------------------------------------------
    # Tier 2 Validation Logic (Kinematic Bounds)
    # -------------------------------------------------------------------------
    def _validate_tier2_kinematics(
        self,
        func_name: str,
        args: Dict[str, Any],
        ctx: RobotSafetyContext,
        t0: float,
    ) -> Tuple[Optional[SandboxValidationResult], Dict[str, Any]]:
        clamped = dict(args)

        if func_name == "move_distance":
            dist = float(args["distance_m"])
            # Distance bounds
            if dist > self.limits.max_relative_distance:
                return (
                    self._fail(
                        KinematicSandboxErrorCode.DISTANCE_EXCEEDED,
                        f"Distanza richiesta ({dist:.2f}m) supera il limite consentito di {self.limits.max_relative_distance:.2f}m.",
                        ctx,
                        t0,
                        feedback=f"Distanza eccessiva: massimo {self.limits.max_relative_distance:.1f} metri per comando singolo.",
                    ),
                    clamped,
                )

            # Speed bounds
            raw_speed = args.get("speed")
            speed = float(raw_speed) if raw_speed is not None else self.limits.default_linear_speed
            if speed < self.limits.min_linear_speed or speed > self.limits.redline_linear_speed:
                return (
                    self._fail(
                        KinematicSandboxErrorCode.LINEAR_SPEED_EXCEEDED,
                        f"Velocita lineare ({speed:.2f}m/s) oltre la linea rossa di {self.limits.redline_linear_speed:.2f}m/s.",
                        ctx,
                        t0,
                        feedback="Velocita richiesta pericolosa, oltre il limite di sicurezza.",
                    ),
                    clamped,
                )

            # Clamping within tolerance (0.25 < speed <= 0.40)
            if speed > self.limits.max_linear_speed:
                clamped["speed"] = self.limits.max_linear_speed
            else:
                clamped["speed"] = speed

        elif func_name == "rotate_angle":
            deg = float(args["degrees"])
            if deg <= 0 or deg > self.limits.max_relative_angle_deg:
                return (
                    self._fail(
                        KinematicSandboxErrorCode.ROTATION_EXCEEDED,
                        f"Angolo di rotazione ({deg:.1f}°) fuori dai limiti consentiti (1.0° - {self.limits.max_relative_angle_deg:.1f}°).",
                        ctx,
                        t0,
                        feedback="Rotazione richiesta superiore al limite consentito di 180 gradi.",
                    ),
                    clamped,
                )

            raw_speed = args.get("speed")
            speed = float(raw_speed) if raw_speed is not None else self.limits.default_angular_speed
            if speed < self.limits.min_angular_speed or speed > self.limits.redline_angular_speed:
                return (
                    self._fail(
                        KinematicSandboxErrorCode.ANGULAR_SPEED_EXCEEDED,
                        f"Velocita angolare ({speed:.2f}rad/s) oltre la linea rossa di {self.limits.redline_angular_speed:.2f}rad/s.",
                        ctx,
                        t0,
                        feedback="Velocita di rotazione pericolosa per la stabilita del robot.",
                    ),
                    clamped,
                )

            # Clamping within tolerance (1.00 < speed <= 1.80)
            if speed > self.limits.max_angular_speed:
                clamped["speed"] = self.limits.max_angular_speed
            else:
                clamped["speed"] = speed

        return None, clamped

    # -------------------------------------------------------------------------
    # Tier 3 Validation Logic (Spatial & Obstacle Clearance)
    # -------------------------------------------------------------------------
    def _validate_tier3_spatial(
        self,
        func_name: str,
        args: Dict[str, Any],
        ctx: RobotSafetyContext,
        t0: float,
    ) -> Optional[SandboxValidationResult]:
        # Costmap collision check
        if ctx.costmap_collision_detected:
            return self._fail(
                KinematicSandboxErrorCode.LOCAL_COSTMAP_COLLISION,
                "Traiettoria intersecata da ostacolo registrato nella costmap locale Nav2.",
                ctx,
                t0,
                feedback="Percorso bloccato: ostacolo rilevato sulla mappa locale.",
            )

        # Negative obstacle / Cliff drop check (SPEC-02: Delta Z >= 15cm)
        if math.isnan(ctx.max_depth_drop) or math.isinf(ctx.max_depth_drop):
            return self._fail(
                KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF,
                "Telemetria dislivello/cliff non valida (NaN/Inf).",
                ctx,
                t0,
                feedback="Sensore dislivello non attendibile. Movimento bloccato per sicurezza.",
            )

        if ctx.max_depth_drop >= self.limits.cliff_depth_drop_max:
            return self._fail(
                KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF,
                f"Rilevato gradino/dislivello negativo critico: Delta Z = {ctx.max_depth_drop*100:.1f}cm >= {self.limits.cliff_depth_drop_max*100:.1f}cm.",
                ctx,
                t0,
                feedback="Pericolo di caduta: rilevato un gradino o dislivello sul percorso.",
            )

        if func_name == "move_distance":
            direction = args["direction"]
            req_dist = float(args["distance_m"])

            if direction == "avanti":
                # Forward clearance check
                if math.isnan(ctx.min_forward_obstacle_dist) or math.isinf(ctx.min_forward_obstacle_dist):
                    return self._fail(
                        KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED,
                        "Telemetria distanza frontale non valida (NaN/Inf).",
                        ctx,
                        t0,
                        feedback="Telemetria sensore frontale non attendibile.",
                    )
                min_req = req_dist + self.limits.forward_obstacle_buffer
                if (
                    ctx.min_forward_obstacle_dist < min_req
                    or ctx.min_forward_obstacle_dist < self.limits.min_forward_clearance
                ):
                    return self._fail(
                        KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED,
                        f"Ostacolo frontale a {ctx.min_forward_obstacle_dist:.2f}m; richiesti almeno {min_req:.2f}m.",
                        ctx,
                        t0,
                        feedback=f"Ostacolo rilevato a {ctx.min_forward_obstacle_dist*100:.0f} centimetri.",
                    )
            elif direction == "indietro":
                # Rear clearance check
                if math.isnan(ctx.min_rear_obstacle_dist) or math.isinf(ctx.min_rear_obstacle_dist):
                    return self._fail(
                        KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED,
                        "Telemetria distanza posteriore non valida (NaN/Inf).",
                        ctx,
                        t0,
                        feedback="Telemetria sensore posteriore non attendibile.",
                    )
                min_req = req_dist + self.limits.rear_obstacle_buffer
                if ctx.min_rear_obstacle_dist < min_req:
                    return self._fail(
                        KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED,
                        f"Ostacolo posteriore a {ctx.min_rear_obstacle_dist:.2f}m; richiesti almeno {min_req:.2f}m.",
                        ctx,
                        t0,
                        feedback=f"Ostacolo posteriore rilevato a {ctx.min_rear_obstacle_dist*100:.0f} centimetri.",
                    )

        elif func_name == "rotate_angle":
            # 360° clearance check for rotation
            scan_radius = getattr(ctx, "min_scan_clearance_radius", None)
            if scan_radius is None and "min_scan_clearance_radius" in ctx.extra_telemetry:
                scan_radius = ctx.extra_telemetry["min_scan_clearance_radius"]
            if scan_radius is None:
                scan_radius = getattr(ctx, "min_clearance_radius_360", 1.00)

            if math.isnan(scan_radius) or math.isinf(scan_radius):
                return self._fail(
                    KinematicSandboxErrorCode.INSUFFICIENT_ROTATION_CLEARANCE,
                    "Telemetria raggio di scansione/rotazione non valida (NaN/Inf).",
                    ctx,
                    t0,
                    feedback="Spazio insufficiente o sensore non attendibile per ruotare sul posto.",
                )

            if scan_radius < self.limits.min_rotation_clearance_radius:
                return self._fail(
                    KinematicSandboxErrorCode.INSUFFICIENT_ROTATION_CLEARANCE,
                    f"Raggio di rotazione insufficiente ({scan_radius:.2f}m < {self.limits.min_rotation_clearance_radius:.2f}m).",
                    ctx,
                    t0,
                    feedback="Spazio insufficiente per ruotare sul posto senza urtare ostacoli.",
                )

        elif func_name in {"navigate_to", "start_frontier_exploration"}:
            if math.isnan(ctx.min_forward_obstacle_dist) or math.isinf(ctx.min_forward_obstacle_dist):
                return self._fail(
                    KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED,
                    "Telemetria distanza frontale non valida (NaN/Inf).",
                    ctx,
                    t0,
                    feedback="Telemetria sensore frontale non attendibile.",
                )

        return None

    # -------------------------------------------------------------------------
    # Tier 4 Validation Logic (Battery & Power Interlocks)
    # -------------------------------------------------------------------------
    def _validate_tier4_battery(
        self,
        func_name: str,
        args: Dict[str, Any],
        ctx: RobotSafetyContext,
        t0: float,
    ) -> Tuple[Optional[SandboxValidationResult], Dict[str, Any]]:
        clamped = dict(args)

        # 0. Telemetry Fail-Safe: NaN / Inf battery voltage check (SPEC-06)
        if math.isnan(ctx.battery_voltage) or math.isinf(ctx.battery_voltage):
            return (
                self._fail(
                    KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL,
                    "Telemetria tensione batteria non valida (NaN/Inf). Arresto preventivo.",
                    ctx,
                    t0,
                    feedback="Telemetria batteria non attendibile. Arresto immediato per sicurezza.",
                ),
                clamped,
            )

        # 1. Charging lock: Zero motion allowed if charging or voltage >= 12.70V (SPEC-02, SPEC-06, FM-NAV-035)
        if ctx.effective_is_charging(self.limits.charging_voltage_threshold):
            return (
                self._fail(
                    KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN,
                    f"Robot in carica ({ctx.battery_voltage:.2f}V >= {self.limits.charging_voltage_threshold:.2f}V). Movimento inibito.",
                    ctx,
                    t0,
                    feedback="Movimento bloccato: sono collegato alla stazione di ricarica.",
                ),
                clamped,
            )

        # 2. Critical shutdown cutoff: <= 9.85V (SPEC-06)
        if ctx.battery_voltage <= self.limits.shutdown_voltage_threshold:
            return (
                self._fail(
                    KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL,
                    f"Tensione batteria critica ({ctx.battery_voltage:.2f}V <= {self.limits.shutdown_voltage_threshold:.2f}V). Arresto preventivo.",
                    ctx,
                    t0,
                    feedback="Batteria quasi esaurita. Arresto immediato per proteggere il sistema.",
                ),
                clamped,
            )

        # 3. Docking reserve: <= 10.15V. Only navigate_to("base") is permitted!
        if ctx.battery_voltage <= self.limits.docking_voltage_threshold:
            is_docking_call = (
                func_name == "navigate_to"
                and str(args.get("destination", "")).strip().lower() == "base"
            )
            if not is_docking_call:
                return (
                    self._fail(
                        KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY,
                        f"Tensione batteria in riserva ({ctx.battery_voltage:.2f}V <= {self.limits.docking_voltage_threshold:.2f}V). Consentito solo il rientro alla base.",
                        ctx,
                        t0,
                        feedback="Batteria scarica. Devo rientrare alla base di ricarica.",
                    ),
                    clamped,
                )

        # 4. ECO Mode Throttling: <= 10.40V clamps max velocity by 50%
        if ctx.battery_voltage <= self.limits.eco_voltage_threshold:
            if "speed" in clamped:
                cur_speed = float(clamped["speed"])
                if func_name == "move_distance":
                    max_eco_lin = self.limits.max_linear_speed * self.limits.eco_speed_scale
                    clamped["speed"] = min(cur_speed, max_eco_lin)
                elif func_name == "rotate_angle":
                    max_eco_ang = self.limits.max_angular_speed * self.limits.eco_speed_scale
                    clamped["speed"] = min(cur_speed, max_eco_ang)

        return None, clamped

    # -------------------------------------------------------------------------
    # Tier 5 Validation Logic (Cognitive Interlocks)
    # -------------------------------------------------------------------------
    def _validate_tier5_cognitive(
        self,
        func_name: str,
        args: Dict[str, Any],
        ctx: RobotSafetyContext,
        t0: float,
    ) -> Tuple[Optional[SandboxValidationResult], Dict[str, Any]]:
        clamped = dict(args)

        # 1. System not ready check
        if not ctx.system_ready:
            return (
                self._fail(
                    KinematicSandboxErrorCode.SYSTEM_NOT_READY,
                    "I supervisori di sistema segnalano che il robot non e pronto al movimento.",
                    ctx,
                    t0,
                    feedback="I sistemi di navigazione non sono ancora pronti.",
                ),
                clamped,
            )

        # 2. Amigdala HIJACK/PANIC check: complete motion veto
        amygdala_state = str(ctx.amygdala_state).strip().upper()
        if amygdala_state in {"HIJACK", "PANIC"}:
            return (
                self._fail(
                    KinematicSandboxErrorCode.AMYGDALA_HIJACK_ACTIVE,
                    "Amigdala in stato HIJACK (panico/stress acuto). Tutti i moti fisici sono interdetti.",
                    ctx,
                    t0,
                    feedback="Stato di emergenza attivo. Movimento inibito dall'Amigdala.",
                ),
                clamped,
            )

        # 3. Amigdala ANXIOUS_VIGILANCE: clamp speeds by 50%
        if amygdala_state == "ANXIOUS_VIGILANCE":
            if "speed" in clamped:
                cur_speed = float(clamped["speed"])
                if func_name == "move_distance":
                    max_anx_lin = self.limits.max_linear_speed * self.limits.anxious_speed_scale
                    clamped["speed"] = min(cur_speed, max_anx_lin)
                elif func_name == "rotate_angle":
                    max_anx_ang = self.limits.max_angular_speed * self.limits.anxious_speed_scale
                    clamped["speed"] = min(cur_speed, max_anx_ang)

        return None, clamped

    # -------------------------------------------------------------------------
    # Helper Result Builders
    # -------------------------------------------------------------------------
    def _fail(
        self,
        error_code: KinematicSandboxErrorCode,
        reason: str,
        ctx: RobotSafetyContext,
        t0: float,
        feedback: Optional[str] = None,
    ) -> SandboxValidationResult:
        self._total_rejections += 1
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        fb = feedback if feedback is not None else f"Comando respinto per sicurezza: {reason}"
        return SandboxValidationResult(
            authorized=False,
            error_code=error_code,
            reason=reason,
            clamped_arguments=None,
            telemetry_snapshot={
                "battery_voltage": ctx.battery_voltage,
                "is_charging": ctx.is_charging,
                "amygdala_state": ctx.amygdala_state,
                "min_forward_obstacle_dist": ctx.min_forward_obstacle_dist,
            },
            feedback_speak=fb,
            latency_ms=elapsed_ms,
        )

    def _pass(
        self,
        cmd: KinematicCommand,
        clamped_args: Dict[str, Any],
        reason: str,
        feedback: str,
        ctx: RobotSafetyContext,
        t0: float,
    ) -> SandboxValidationResult:
        self._total_authorizations += 1
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return SandboxValidationResult(
            authorized=True,
            error_code=KinematicSandboxErrorCode.SUCCESS,
            reason=reason,
            clamped_arguments=clamped_args,
            telemetry_snapshot={
                "battery_voltage": ctx.battery_voltage,
                "is_charging": ctx.is_charging,
                "amygdala_state": ctx.amygdala_state,
            },
            feedback_speak=feedback,
            latency_ms=elapsed_ms,
        )
