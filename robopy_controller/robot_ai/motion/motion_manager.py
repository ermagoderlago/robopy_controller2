"""
Robot AI Motion Manager
======================
Ossatura estensibile per l'esecuzione di comandi di movimento relativo,
primitivi di movimento (lineare, angolare, archi) e sequenze cinematiche.
"""

import math
import time
import asyncio
from enum import Enum
from typing import List, Optional, Dict, Any, Callable

from ..utils.logging_utils import get_logger

# Vincoli fisici di sicurezza (Regola 14 da actuation_motor_driver.md)
MAX_LINEAR_SPEED = 0.25     # m/s max per tollerare spunto iniziale e salite
DEFAULT_LINEAR_SPEED = 0.18 # m/s velocità di crociera per superare strizione (stiction)
MAX_ANGULAR_SPEED = 1.0     # rad/s max
DEFAULT_ANGULAR_SPEED = 0.6  # rad/s (~34.4 deg/s)

class MotionType(str, Enum):
    LINEAR = "linear"
    ANGULAR = "angular"
    ARC = "arc"
    SEQUENCE = "sequence"

class MotionPrimitive:
    """
    Rappresenta un movimento primitivo atomicamente eseguibile dal robot.
    """
    def __init__(
        self,
        direction: str,
        distance_m: Optional[float] = None,
        degrees: Optional[float] = None,
        linear_speed: Optional[float] = None,
        angular_speed: Optional[float] = None,
        duration: Optional[float] = None
    ):
        self.direction = direction.lower().strip()
        self.distance_m = abs(distance_m) if distance_m is not None else None
        self.degrees = degrees
        
        # Determina il tipo di movimento
        if self.direction in ("avanti", "indietro", "forward", "backward"):
            self.motion_type = MotionType.LINEAR
        elif self.direction in ("sinistra", "destra", "left", "right"):
            self.motion_type = MotionType.ANGULAR
        else:
            self.motion_type = MotionType.LINEAR

        # Saturazione di sicurezza per le velocità
        if self.motion_type == MotionType.LINEAR:
            base_speed = linear_speed if linear_speed is not None else DEFAULT_LINEAR_SPEED
            self.linear_speed = min(abs(base_speed), MAX_LINEAR_SPEED)
            self.angular_speed = 0.0
        else:
            base_speed = angular_speed if angular_speed is not None else DEFAULT_ANGULAR_SPEED
            self.linear_speed = 0.0
            self.angular_speed = min(abs(base_speed), MAX_ANGULAR_SPEED)

        self.duration = duration
        self._calculate_timing()

    def _calculate_timing(self):
        """Calcola la durata precisa in base a distanza (m) o angoli (gradi)."""
        if self.motion_type == MotionType.LINEAR:
            if self.distance_m is not None and self.linear_speed > 0:
                self.duration = self.distance_m / self.linear_speed
            elif self.duration is None:
                self.duration = 1.5  # Durata di fallback (1.5s)
        elif self.motion_type == MotionType.ANGULAR:
            if self.degrees is not None and self.angular_speed > 0:
                rad = math.radians(abs(self.degrees))
                self.duration = rad / self.angular_speed
            elif self.duration is None:
                self.duration = 1.5  # Durata di fallback (1.5s)

    def get_velocities(self) -> tuple:
        """Restituisce le velocità (linear_x, angular_z) con il segno corretto."""
        linear_x = 0.0
        angular_z = 0.0

        if self.direction in ("avanti", "forward"):
            linear_x = self.linear_speed
        elif self.direction in ("indietro", "backward"):
            linear_x = -self.linear_speed
        elif self.direction in ("sinistra", "left"):
            angular_z = self.angular_speed
        elif self.direction in ("destra", "right"):
            angular_z = -self.angular_speed

        return linear_x, angular_z


class MotionSequence:
    """
    Rappresenta una sequenza di movimenti primitivi eseguiti in serie.
    Costruisce l'ossatura per manovre complesse (es. traiettorie, curve ad angolo, quadrati).
    """
    def __init__(self, name: str = "custom_sequence"):
        self.name = name
        self.primitives: List[MotionPrimitive] = []

    def add_primitive(self, primitive: MotionPrimitive) -> "MotionSequence":
        self.primitives.append(primitive)
        return self

    def total_duration(self) -> float:
        return sum(p.duration for p in self.primitives)


class MotionManager:
    """
    Gestore del movimento del robot. Esegue comandi di movimento relativo o sequenze
    con ciclo di pubblicazione cmd_vel sicuro, impulso di spunto iniziale (stiction kick)
    ed arresto controllato.
    """
    def __init__(self):
        self.logger = get_logger("motion_manager")

    async def execute_primitive(
        self,
        primitive: MotionPrimitive,
        publish_twist_cb: Callable[[float, float], None],
        get_odom_cb: Optional[Callable[[], Optional[tuple]]] = None
    ) -> None:
        """
        Esegue un movimento primitivo singolo. Se get_odom_cb è fornito, esegue un controllo
        closed-loop PID basato sullo spostamento reale dagli encoder odometrici (invece che aperto in tempo).
        """
        v_x, w_z = primitive.get_velocities()
        dur = primitive.duration or 1.5

        start_pose = get_odom_cb() if get_odom_cb else None

        # --- CONTROLLO CLOSED-LOOP PID SU ODOMETRIA REALE ---
        if start_pose is not None and (primitive.distance_m is not None or primitive.degrees is not None):
            x0, y0, yaw0 = start_pose
            self.logger.info(
                f"🎯 [MotionManager PID Closed-Loop] Avvio moto '{primitive.direction}' "
                f"da pose iniziale (x={x0:.3f}, y={y0:.3f}, yaw={yaw0:.3f})"
            )
            
            target_dist = primitive.distance_m
            target_rad = math.radians(abs(primitive.degrees)) if primitive.degrees is not None else None
            
            # Guadagni PID e spunto per superare l'attrito dei riduttori
            Kp_lin, Ki_lin = 1.5, 0.4
            Kp_ang, Ki_ang = 1.8, 0.5
            accum_error = 0.0
            
            t_start = time.monotonic()
            timeout = (target_dist / 0.10 * 2.0 + 4.0) if target_dist else ((target_rad / 0.4 * 2.0 + 4.0) if target_rad else 5.0)
            t_max = t_start + min(timeout, 12.0)
            
            dt = 0.05
            while time.monotonic() < t_max:
                curr_pose = get_odom_cb()
                if curr_pose is None:
                    publish_twist_cb(v_x, w_z)
                    await asyncio.sleep(dt)
                    continue

                cx, cy, cyaw = curr_pose
                elapsed = time.monotonic() - t_start

                if primitive.motion_type == MotionType.LINEAR and target_dist is not None:
                    moved = math.sqrt((cx - x0)**2 + (cy - y0)**2)
                    error = target_dist - moved

                    if error <= 0.01:  # Target raggiunto (tolleranza 1cm)
                        self.logger.info(f"✅ [MotionManager PID] Target distanza {target_dist:.2f}m raggiunto! Spostamento reale: {moved:.3f}m")
                        break

                    accum_error += error * dt
                    # Aumento dinamico della velocità PID se lo spostamento non procede
                    cmd_v = Kp_lin * error + Ki_lin * accum_error
                    if elapsed < 0.20:
                        cmd_v = max(cmd_v, 0.22)
                    
                    cmd_v = min(max(abs(cmd_v), 0.15), MAX_LINEAR_SPEED)
                    cmd_vx = cmd_v if v_x >= 0 else -cmd_v
                    publish_twist_cb(cmd_vx, 0.0)

                elif primitive.motion_type == MotionType.ANGULAR and target_rad is not None:
                    # Calcolo rotazione accumulata
                    dyaw = cyaw - yaw0
                    dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
                    moved = abs(dyaw)
                    error = target_rad - moved

                    if error <= 0.035:  # Target raggiunto (tolleranza ~2°)
                        self.logger.info(f"✅ [MotionManager PID] Target angolo {math.degrees(target_rad):.1f}° raggiunto! Rotazione reale: {math.degrees(moved):.1f}°")
                        break

                    accum_error += error * dt
                    cmd_w = Kp_ang * error + Ki_ang * accum_error
                    if elapsed < 0.20:
                        cmd_w = max(cmd_w, 0.70)
                    
                    cmd_w = min(max(abs(cmd_w), 0.50), MAX_ANGULAR_SPEED)
                    cmd_wz = cmd_w if w_z >= 0 else -cmd_w
                    publish_twist_cb(0.0, cmd_wz)
                else:
                    publish_twist_cb(v_x, w_z)

                await asyncio.sleep(dt)

            publish_twist_cb(0.0, 0.0)
            self.logger.info(f"🛑 [MotionManager PID] Movimento '{primitive.direction}' completato.")
            return

        # --- FALLBACK OPEN-LOOP IN TEMPO (Se Odometria non disponibile) ---
        # Calcolo impulso di spunto (stiction compensation kick) nei primi 0.20s
        if v_x > 0:
            v_kick = max(v_x * 1.35, 0.22)
        elif v_x < 0:
            v_kick = min(v_x * 1.35, -0.22)
        else:
            v_kick = 0.0

        if w_z > 0:
            w_kick = max(w_z * 1.35, 0.70)
        elif w_z < 0:
            w_kick = min(w_z * 1.35, -0.70)
        else:
            w_kick = 0.0

        v_kick = max(min(v_kick, MAX_LINEAR_SPEED), -MAX_LINEAR_SPEED)
        w_kick = max(min(w_kick, MAX_ANGULAR_SPEED), -MAX_ANGULAR_SPEED)

        self.logger.info(
            f"🚀 [MotionManager Open-Loop] Esecuzione primitivo '{primitive.direction}': "
            f"v_x={v_x:.2f}m/s, w_z={w_z:.2f}rad/s per {dur:.2f}s "
            f"(dist={primitive.distance_m}m, deg={primitive.degrees}°, kick_v={v_kick:.2f}m/s, kick_w={w_kick:.2f}rad/s)"
        )

        t_start = time.monotonic()
        t_end = t_start + dur
        
        while time.monotonic() < t_end:
            elapsed = time.monotonic() - t_start
            if elapsed < 0.20:
                publish_twist_cb(v_kick, w_kick)
            else:
                publish_twist_cb(v_x, w_z)
            await asyncio.sleep(0.05)

        publish_twist_cb(0.0, 0.0)
        self.logger.info(f"🛑 [MotionManager Open-Loop] Primitivo '{primitive.direction}' completato.")

    async def execute_sequence(self, sequence: MotionSequence, publish_twist_cb: Callable[[float, float], None]) -> None:
        """
        Esegue una sequenza di movimenti primitivi uno dopo l'altro.
        """
        self.logger.info(f"🎬 [MotionManager] Avvio sequenza '{sequence.name}' ({len(sequence.primitives)} step)")
        for idx, primitive in enumerate(sequence.primitives):
            self.logger.info(f"📍 Step {idx+1}/{len(sequence.primitives)}")
            await self.execute_primitive(primitive, publish_twist_cb)
            await asyncio.sleep(0.2)  # Piccola pausa tra step per stabilizzare lo chassis

        self.logger.info(f"✅ [MotionManager] Sequenza '{sequence.name}' completata.")
