#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Theory of Mind (ToM) & Contextual Awareness Engine (SPEC-05, SPEC-03).
=====================================================================
Analyzes user posture, spine inclination, head orientation, and activity
to deduce mental and physiological context:

States:
- DO_NOT_DISTURB: User is focused/working at workbench (spine bent, head down).
                  Suppresses proactive curiosity and reduces TTS speech volume.
- ENGAGED:        User is standing or seated facing Marcus; open to interaction.
- RESTING:        User is calmly reclining/resting on couch or bed.
- FALL_EMERGENCY: Person suddenly on floor plane with horizontal spine aspect ratio.
                  Triggers 100% autonomous local vocal emergency check & LED beacon.
"""

import math
import time
import logging
from enum import Enum
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any

logger = logging.getLogger("TheoryOfMind")


class ToMState(str, Enum):
    ENGAGED = "ENGAGED"
    DO_NOT_DISTURB = "DO_NOT_DISTURB"
    RESTING = "RESTING"
    FALL_EMERGENCY = "FALL_EMERGENCY"


@dataclass
class ToMEvaluation:
    state: ToMState
    confidence: float
    spine_angle_deg: float
    head_pitch_deg: float
    is_on_ground: bool
    fall_duration_sec: float
    recommended_tts_volume: float
    explanation: str
    timestamp: float = 0.0


class TheoryOfMindEngine:
    """
    Engine deterministico per la deduzione dello stato attentivo dell'utente e sicurezza anticaduta.
    """

    NOSE = 0
    LEFT_EYE = 1
    RIGHT_EYE = 2
    LEFT_SHOULDER = 5
    RIGHT_SHOULDER = 6
    LEFT_HIP = 11
    RIGHT_HIP = 12
    LEFT_KNEE = 13
    RIGHT_KNEE = 14
    LEFT_ANKLE = 15
    RIGHT_ANKLE = 16

    def __init__(self, fall_confirmation_time_s: float = 3.0):
        self.fall_confirmation_time_s = fall_confirmation_time_s
        self.current_state: ToMState = ToMState.ENGAGED
        self.fall_first_detected_time: Optional[float] = None
        self.last_state_change_time: float = time.time()
        self.min_keypoint_conf: float = 0.30

    def evaluate_posture(
        self,
        keypoints: List[Tuple[float, float, float]],
        face_detected: bool = True,
        gaze_at_robot: bool = True,
        depth_m: float = 1.5
    ) -> ToMEvaluation:
        """
        Valuta i 17 keypoint normalizzati COCO:
        Restituisce lo stato ToM corrente con raccomandazioni per volume TTS e gating.
        """
        now = time.time()
        if len(keypoints) < 17:
            return ToMEvaluation(
                state=self.current_state,
                confidence=0.5,
                spine_angle_deg=0.0,
                head_pitch_deg=0.0,
                is_on_ground=False,
                fall_duration_sec=0.0,
                recommended_tts_volume=0.8,
                explanation="Insufficienza keypoints scheletro",
                timestamp=now
            )

        # 1. Calcolo centri anatomici
        ls = keypoints[self.LEFT_SHOULDER]
        rs = keypoints[self.RIGHT_SHOULDER]
        lh = keypoints[self.LEFT_HIP]
        rh = keypoints[self.RIGHT_HIP]
        nose = keypoints[self.NOSE]
        la = keypoints[self.LEFT_ANKLE]
        ra = keypoints[self.RIGHT_ANKLE]

        # Validazione confidenze minime busto
        torso_conf = (ls[2] + rs[2] + lh[2] + rh[2]) / 4.0
        if torso_conf < self.min_keypoint_conf:
            return ToMEvaluation(
                state=self.current_state,
                confidence=torso_conf,
                spine_angle_deg=0.0,
                head_pitch_deg=0.0,
                is_on_ground=False,
                fall_duration_sec=0.0,
                recommended_tts_volume=0.8,
                explanation="Confidenza scheletro troppo bassa",
                timestamp=now
            )

        mid_shoulder_x = (ls[0] + rs[0]) * 0.5
        mid_shoulder_y = (ls[1] + rs[1]) * 0.5
        mid_hip_x = (lh[0] + rh[0]) * 0.5
        mid_hip_y = (lh[1] + rh[1]) * 0.5

        # Vettore spina dorsale (dal bacino alle spalle)
        spine_dx = mid_shoulder_x - mid_hip_x
        spine_dy = mid_shoulder_y - mid_hip_y  # y=0 è in alto nell'immagine, y=1 è in basso
        spine_length = math.hypot(spine_dx, spine_dy)

        # Angolo rispetto alla verticale (0 deg = perfettamente eretto)
        # spine_dy è negativo quando le spalle sono sopra le anche
        spine_angle_deg = abs(math.degrees(math.atan2(abs(spine_dx), max(0.001, abs(spine_dy)))))

        # Inclinazione della testa (dal naso al centro spalle)
        head_dx = nose[0] - mid_shoulder_x
        head_dy = nose[1] - mid_shoulder_y
        head_pitch_deg = math.degrees(math.atan2(head_dy, max(0.001, abs(head_dx))))

        # Calcolo bounding box orizzontale vs verticale
        all_y = [kp[1] for kp in keypoints if kp[2] > self.min_keypoint_conf]
        all_x = [kp[0] for kp in keypoints if kp[2] > self.min_keypoint_conf]
        height_norm = (max(all_y) - min(all_y)) if all_y else 0.5
        width_norm = (max(all_x) - min(all_x)) if all_x else 0.5
        aspect_ratio = width_norm / max(0.01, height_norm)

        # Posizione verticale media del corpo (y basso = quota alta, y alto = pavimento)
        avg_body_y = (mid_shoulder_y + mid_hip_y) * 0.5
        is_on_ground = (avg_body_y > 0.65) and (aspect_ratio > 1.25 or spine_angle_deg > 60.0)

        # 2. Rilevamento FALL_EMERGENCY
        if is_on_ground:
            if self.fall_first_detected_time is None:
                self.fall_first_detected_time = now
            fall_duration = now - self.fall_first_detected_time
            if fall_duration >= self.fall_confirmation_time_s:
                self.current_state = ToMState.FALL_EMERGENCY
                return ToMEvaluation(
                    state=ToMState.FALL_EMERGENCY,
                    confidence=0.92,
                    spine_angle_deg=spine_angle_deg,
                    head_pitch_deg=head_pitch_deg,
                    is_on_ground=True,
                    fall_duration_sec=fall_duration,
                    recommended_tts_volume=1.0,
                    explanation=f"Caduta a terra confermata (durata={fall_duration:.1f}s, aspect_ratio={aspect_ratio:.2f})",
                    timestamp=now
                )
        else:
            self.fall_first_detected_time = None

        # 3. Rilevamento DO_NOT_DISTURB (Utente concentrato al lavoro/scrivania)
        # Spina dorsale inclinata in avanti (20-55 deg), testa chinata verso il basso, non guarda il robot
        is_bent_over = (20.0 <= spine_angle_deg < 60.0) and (head_pitch_deg > 15.0 or not gaze_at_robot)
        if is_bent_over:
            self.current_state = ToMState.DO_NOT_DISTURB
            return ToMEvaluation(
                state=ToMState.DO_NOT_DISTURB,
                confidence=0.85,
                spine_angle_deg=spine_angle_deg,
                head_pitch_deg=head_pitch_deg,
                is_on_ground=False,
                fall_duration_sec=0.0,
                recommended_tts_volume=0.35,  # Volume attenuato per non disturbare
                explanation="Utente chinato/concentrato al banco di lavoro (DND attivo)",
                timestamp=now
            )

        # 4. Rilevamento RESTING
        if spine_angle_deg > 65.0 and not is_on_ground:
            self.current_state = ToMState.RESTING
            return ToMEvaluation(
                state=ToMState.RESTING,
                confidence=0.75,
                spine_angle_deg=spine_angle_deg,
                head_pitch_deg=head_pitch_deg,
                is_on_ground=False,
                fall_duration_sec=0.0,
                recommended_tts_volume=0.40,
                explanation="Utente disteso o reclinato in stato di riposo",
                timestamp=now
            )

        # 5. Default ENGAGED
        self.current_state = ToMState.ENGAGED
        return ToMEvaluation(
            state=ToMState.ENGAGED,
            confidence=0.90,
            spine_angle_deg=spine_angle_deg,
            head_pitch_deg=head_pitch_deg,
            is_on_ground=False,
            fall_duration_sec=0.0,
            recommended_tts_volume=0.85,
            explanation="Utente in postura eretta/disponibile ad interazione",
            timestamp=now
        )
