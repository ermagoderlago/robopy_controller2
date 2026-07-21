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
MAX_LINEAR_SPEED = 0.18   # m/s max per prevenire sottotensione e stallo
DEFAULT_LINEAR_SPEED = 0.15 # m/s velocità di crociera consigliata
MAX_ANGULAR_SPEED = 0.8   # rad/s max
DEFAULT_ANGULAR_SPEED = 0.5 # rad/s (~28.6 deg/s)

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
    con ciclo di pubblicazione cmd_vel sicuro ed arresto controllato.
    """
    def __init__(self):
        self.logger = get_logger("motion_manager")

    async def execute_primitive(self, primitive: MotionPrimitive, publish_twist_cb: Callable[[float, float], None]) -> None:
        """
        Esegue un movimento primitivo singolo pubblicando cmd_vel a 10Hz.
        """
        v_x, w_z = primitive.get_velocities()
        dur = primitive.duration or 1.0

        self.logger.info(
            f"🚀 [MotionManager] Esecuzione primitivo '{primitive.direction}': "
            f"v_x={v_x:.2f}m/s, w_z={w_z:.2f}rad/s per {dur:.2f}s "
            f"(dist={primitive.distance_m}m, deg={primitive.degrees}°)"
        )

        t_end = time.monotonic() + dur
        while time.monotonic() < t_end:
            publish_twist_cb(v_x, w_z)
            await asyncio.sleep(0.1)  # 10Hz execution loop

        # Stop pulito al termine
        publish_twist_cb(0.0, 0.0)
        self.logger.info(f"🛑 [MotionManager] Primitivo '{primitive.direction}' completato.")

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
