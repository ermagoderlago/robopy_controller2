#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Visual Gesture & 3D Pointing Engine (SPEC-03, SPEC-01).
======================================================
Parses 17 COCO keypoints from Hailo Pose Estimation to detect:
1. Open Palm (Visual Barge-in / Immediate E-Stop)
2. Thumbs Up (Non-verbal affirmative feedback / Dopamine RPE boost)
3. 3D Vector Pointing ("Guarda lì"): Ray from Shoulder->Elbow->Wrist projected into 3D.
"""

import math
import time
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple


class GestureType(str, Enum):
    NONE = "NONE"
    OPEN_PALM = "OPEN_PALM"
    THUMBS_UP = "THUMBS_UP"
    POINTING = "POINTING"


@dataclass
class PointingTarget:
    hand: str                     # "LEFT" or "RIGHT"
    origin_3d: Tuple[float, float, float]     # (x, y, z) in camera frame
    direction_3d: Tuple[float, float, float]  # Normalized ray vector
    intersection_3d: Tuple[float, float, float] # Approximate target in 3D
    confidence: float


@dataclass
class GestureResult:
    gesture: GestureType
    confidence: float
    hand: str
    pointing_target: Optional[PointingTarget] = None
    timestamp: float = 0.0


class VisualGesturePointingEngine:
    """
    Motore deterministico ad alte prestazioni per il riconoscimento di gesti e puntamento 3D.
    """

    # COCO Keypoint Indices
    NOSE = 0
    LEFT_SHOULDER = 5
    RIGHT_SHOULDER = 6
    LEFT_ELBOW = 7
    RIGHT_ELBOW = 8
    LEFT_WRIST = 9
    RIGHT_WRIST = 10
    LEFT_HIP = 11
    RIGHT_HIP = 12

    def __init__(self, min_conf: float = 0.35):
        self.min_conf = min_conf
        self.last_palm_stop_time: float = 0.0
        self.last_thumbs_up_time: float = 0.0
        self.palm_debounce_sec: float = 1.0
        self.thumbs_debounce_sec: float = 2.0

    def evaluate_pose(
        self,
        keypoints: List[Tuple[float, float, float]],
        estimated_depth_m: float = 1.5
    ) -> GestureResult:
        """
        Analizza 17 keypoints normalizzati (x, y in [0, 1], z=confidence).
        Restituisce il gesto prevalente rilevato.
        """
        now = time.time()
        if len(keypoints) < 17:
            return GestureResult(gesture=GestureType.NONE, confidence=0.0, hand="NONE", timestamp=now)

        # Check Right Arm
        res_r = self._check_arm(
            keypoints,
            s_idx=self.RIGHT_SHOULDER,
            e_idx=self.RIGHT_ELBOW,
            w_idx=self.RIGHT_WRIST,
            hand_label="RIGHT",
            estimated_depth_m=estimated_depth_m
        )

        # Check Left Arm
        res_l = self._check_arm(
            keypoints,
            s_idx=self.LEFT_SHOULDER,
            e_idx=self.LEFT_ELBOW,
            w_idx=self.LEFT_WRIST,
            hand_label="LEFT",
            estimated_depth_m=estimated_depth_m
        )

        # Priorità: OPEN_PALM (sicurezza/stop immediato) > POINTING > THUMBS_UP
        for res in (res_r, res_l):
            if res.gesture == GestureType.OPEN_PALM:
                if now - self.last_palm_stop_time > self.palm_debounce_sec:
                    self.last_palm_stop_time = now
                    return res

        for res in (res_r, res_l):
            if res.gesture == GestureType.POINTING:
                return res

        for res in (res_r, res_l):
            if res.gesture == GestureType.THUMBS_UP:
                if now - self.last_thumbs_up_time > self.thumbs_debounce_sec:
                    self.last_thumbs_up_time = now
                    return res

        return GestureResult(gesture=GestureType.NONE, confidence=0.0, hand="NONE", timestamp=now)

    def _check_arm(
        self,
        kps: List[Tuple[float, float, float]],
        s_idx: int,
        e_idx: int,
        w_idx: int,
        hand_label: str,
        estimated_depth_m: float
    ) -> GestureResult:
        now = time.time()
        s = kps[s_idx]
        e = kps[e_idx]
        w = kps[w_idx]

        # Validazione confidenze
        if s[2] < self.min_conf or e[2] < self.min_conf or w[2] < self.min_conf:
            return GestureResult(gesture=GestureType.NONE, confidence=0.0, hand=hand_label, timestamp=now)

        # 1. Gesto OPEN_PALM (Visual Barge-in stop)
        # Polso sollevato nettamente sopra il gomito e la spalla (y=0 è in alto)
        # gomito fleso o semidisteso verso l'alto
        if w[1] < e[1] - 0.08 and w[1] < s[1]:
            # Avambraccio approssimativamente verticale
            dx = abs(w[0] - e[0])
            dy = abs(e[1] - w[1])
            if dy > 0.05 and (dx / dy) < 0.65:
                conf = min(e[2], w[2])
                return GestureResult(gesture=GestureType.OPEN_PALM, confidence=conf, hand=hand_label, timestamp=now)

        # 2. Gesto 3D POINTING ("Guarda lì")
        # Braccio quasi completamente disteso: distanza spalla-polso vicina alla somma dei segmenti
        d_se = math.hypot(e[0] - s[0], e[1] - s[1])
        d_ew = math.hypot(w[0] - e[0], w[1] - e[1])
        d_sw = math.hypot(w[0] - s[0], w[1] - s[1])

        extension_ratio = d_sw / max(0.001, (d_se + d_ew))
        if extension_ratio > 0.82 and d_sw > 0.15:
            # Calcolo vettore raggio di puntamento 3D nello spazio camera
            # X = right, Y = down, Z = forward
            origin_x = (w[0] - 0.5) * estimated_depth_m
            origin_y = (w[1] - 0.5) * estimated_depth_m
            origin_z = estimated_depth_m

            # Vettore direzione dal gomito al polso
            vx = w[0] - e[0]
            vy = w[1] - e[1]
            # Vettore normalizzato in 2D proiettato in avanti
            mag = math.hypot(vx, vy)
            if mag > 0.02:
                dir_x = (vx / mag)
                dir_y = (vy / mag)
                dir_z = 0.85  # Puntamento prevalentemente orientato in avanti verso lo spazio 3D
                norm3d = math.sqrt(dir_x**2 + dir_y**2 + dir_z**2)
                dir_x /= norm3d
                dir_y /= norm3d
                dir_z /= norm3d

                # Bersaglio estrapolato a 1.5m oltre il polso
                dist_proj = 1.5
                target_x = origin_x + dir_x * dist_proj
                target_y = origin_y + dir_y * dist_proj
                target_z = origin_z + dir_z * dist_proj

                conf = min(s[2], min(e[2], w[2]))
                pt_target = PointingTarget(
                    hand=hand_label,
                    origin_3d=(origin_x, origin_y, origin_z),
                    direction_3d=(dir_x, dir_y, dir_z),
                    intersection_3d=(target_x, target_y, target_z),
                    confidence=conf
                )
                return GestureResult(
                    gesture=GestureType.POINTING,
                    confidence=conf,
                    hand=hand_label,
                    pointing_target=pt_target,
                    timestamp=now
                )

        # 3. Gesto THUMBS_UP (Conferma silenziosa)
        # Polso davanti al busto (altezza tra spalla e anca), gomito piegato ~90 gradi
        h_idx = self.RIGHT_HIP if hand_label == "RIGHT" else self.LEFT_HIP
        h = kps[h_idx]
        if h[2] >= self.min_conf:
            if s[1] < w[1] < h[1]:
                # Gomito flesso
                if extension_ratio < 0.70 and d_se > 0.06 and d_ew > 0.06:
                    conf = min(e[2], w[2]) * 0.9
                    return GestureResult(
                        gesture=GestureType.THUMBS_UP,
                        confidence=conf,
                        hand=hand_label,
                        timestamp=now
                    )

        return GestureResult(gesture=GestureType.NONE, confidence=0.0, hand=hand_label, timestamp=now)
