#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spatial-Postural Scene Fusion Engine (SPEC-03, SPEC-05).
=========================================================
Fonde:
1. Keypoints posturali COCO-17 (Hailo Pose + VisualGesturePointingEngine + TheoryOfMindEngine)
2. Bounding boxes 2D/3D degli oggetti circostanti (/hailo/semantic_objects)
3. Contesto topologico di mappa (stanza + posa robot)
4. Principio di minimizzazione dell'energia metabolica (MetabolicEffortTier)

Produce uno snapshot compatto (~45 token) iniettabile in CagEnvironment / SLMMetapromptFusion
senza saturare il budget rigido di Qwen 2.5 sulla NPU Hailo-10H (<1200 token).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple


class MetabolicEffortTier(str, Enum):
    """Gerarchia energetica ispirata alla biologia umana per evitare moto superfluo."""
    ZERO_MOTOR = "ZERO_MOTOR"              # ~0 J: solo voce / LED
    MICRO_PRESENCE = "MICRO_PRESENCE"      # ~0.5 J: micro-movimento coda [-45°, +45°] + voce
    OCULOMOTOR_PIVOT = "OCULOMOTOR_PIVOT"  # ~3 J: rotazione sul posto se persona fuori asse
    PROXEMIC_STEP = "PROXEMIC_STEP"        # ~15 J: breve traslazione prossemica (es. 0.50m o arretramento)
    LOCOMOTION_NAV = "LOCOMOTION_NAV"       # ~50+ J: following o navigazione Nav2 verso stanza/base


@dataclass
class DetectedBoxObject:
    """Rappresentazione normalizzata di un oggetto rilevato con bbox 2D e centroide 3D."""
    label: str
    bbox_2d: Tuple[float, float, float, float] = (0.45, 0.45, 0.55, 0.55)  # [x1, y1, x2, y2] in [0, 1]
    centroid_3d: Tuple[float, float, float] = (0.0, 0.0, 1.5)              # (x, y, z) metri optical frame
    confidence: float = 0.80
    room: Optional[str] = None

    @property
    def center_2d(self) -> Tuple[float, float]:
        x1, y1, x2, y2 = self.bbox_2d
        return ((x1 + x2) * 0.5, (y1 + y2) * 0.5)

    @property
    def distance_m(self) -> float:
        x, y, z = self.centroid_3d
        return math.sqrt(x * x + y * y + z * z)


@dataclass
class SpatialPosturalSceneSnapshot:
    """Snapshot multimodale fuso pronto per CAG / Qwen 2.5 e Skill polimorfiche."""
    person_name: str = "Utente"
    person_distance_m: float = 1.5
    person_azimuth_deg: float = 0.0
    tom_state: str = "ENGAGED"
    gesture: str = "NONE"
    gaze_at_robot: bool = True
    pointing_target_object: Optional[str] = None
    near_hand_objects: List[str] = field(default_factory=list)
    scene_objects: List[str] = field(default_factory=list)
    map_room: str = "Unknown"
    map_pose_xy: Optional[Tuple[float, float]] = None
    recommended_effort_tier: MetabolicEffortTier = MetabolicEffortTier.MICRO_PRESENCE
    target_proxemic_distance_m: float = 1.20
    social_momentum: float = 0.0
    social_inhibition: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_compact_cag_string(self) -> str:
        """
        Formatta la scena fusa in un riassunto compatto (<60 token) per il contesto Qwen.
        """
        near_str = ",".join(self.near_hand_objects[:3]) if self.near_hand_objects else "none"
        objs_str = ",".join(self.scene_objects[:4]) if self.scene_objects else "none"
        az_sign = "+" if self.person_azimuth_deg >= 0 else ""
        gaze_str = "robot" if self.gaze_at_robot else "away"
        parts = [
            f"person={self.person_name}@{self.person_distance_m:.2f}m,az={az_sign}{self.person_azimuth_deg:.0f}deg",
            f"tom={self.tom_state.lower()}",
            f"gesture={self.gesture.lower()}",
            f"gaze={gaze_str}",
            f"near_hand=[{near_str}]",
            f"objs=[{objs_str}]",
            f"room={self.map_room}",
            f"soc_mom={self.social_momentum:.2f},soc_inh={self.social_inhibition:.2f}",
            f"min_effort={self.recommended_effort_tier.value},prox_target={self.target_proxemic_distance_m:.2f}m",
        ]
        if self.pointing_target_object:
            parts.insert(4, f"pointing_at={self.pointing_target_object}")
        return "PoseScene: " + " | ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "person_name": self.person_name,
            "person_distance_m": round(self.person_distance_m, 2),
            "person_azimuth_deg": round(self.person_azimuth_deg, 1),
            "tom_state": self.tom_state,
            "gesture": self.gesture,
            "gaze_at_robot": self.gaze_at_robot,
            "pointing_target_object": self.pointing_target_object,
            "near_hand_objects": list(self.near_hand_objects),
            "scene_objects": list(self.scene_objects),
            "map_room": self.map_room,
            "map_pose_xy": list(self.map_pose_xy) if self.map_pose_xy else None,
            "recommended_effort_tier": self.recommended_effort_tier.value,
            "target_proxemic_distance_m": round(self.target_proxemic_distance_m, 2),
            "social_momentum": round(self.social_momentum, 2),
            "social_inhibition": round(self.social_inhibition, 2),
            "timestamp": self.timestamp,
        }


class SpatialPosturalSceneBuilder:
    """
    Costruisce `SpatialPosturalSceneSnapshot` correlando scheletro COCO-17,
    bounding boxes YOLO 2D/3D e posa sulla mappa, selezionando il livello
    minimo di energia metabolica necessario per rispondere al contesto.
    """

    HFOV_DEG: float = 69.0  # OAK-D Lite RGB HFOV (SPEC-03)
    LEFT_SHOULDER = 5
    RIGHT_SHOULDER = 6
    LEFT_WRIST = 9
    RIGHT_WRIST = 10
    NOSE = 0

    def build_scene(
        self,
        keypoints: Optional[List[Tuple[float, float, float]]] = None,
        detected_objects: Optional[Iterable[Any]] = None,
        person_name: str = "Utente",
        person_distance_m: float = 1.5,
        tom_state: str = "ENGAGED",
        gesture: str = "NONE",
        gaze_at_robot: bool = True,
        pointing_intersection_3d: Optional[Tuple[float, float, float]] = None,
        map_room: str = "Unknown",
        map_pose_xy: Optional[Tuple[float, float]] = None,
        target_proxemic_distance_m: float = 1.20,
        social_momentum: float = 0.0,
        social_inhibition: float = 0.0,
        follow_active: bool = False,
    ) -> SpatialPosturalSceneSnapshot:
        kps = list(keypoints) if keypoints else []
        cx = self._estimate_person_center_x(kps)
        azimuth_deg = round((cx - 0.5) * self.HFOV_DEG, 1)

        normalized_boxes = [self._coerce_box(obj) for obj in (detected_objects or [])]
        # Filter out generic person boxes from object-focus matching unless needed
        non_person_boxes = [b for b in normalized_boxes if b.label.lower() not in ("person", "persona")]

        near_hand_objects, pointing_target_object = self._correlate_hands_with_objects(
            kps, non_person_boxes, pointing_intersection_3d
        )

        # Compact distance-sorted list of scene objects for Qwen awareness
        sorted_objs = sorted(non_person_boxes, key=lambda b: b.distance_m)
        scene_objects = [
            f"{b.label}@{max(0.1, b.centroid_3d[2]):.1f}m"
            for b in sorted_objs[:5]
        ]

        effort_tier = self._select_metabolic_effort_tier(
            person_distance_m=person_distance_m,
            azimuth_deg=azimuth_deg,
            tom_state=tom_state,
            gesture=gesture,
            gaze_at_robot=gaze_at_robot,
            target_proxemic_distance_m=target_proxemic_distance_m,
            social_inhibition=social_inhibition,
            follow_active=follow_active,
        )

        return SpatialPosturalSceneSnapshot(
            person_name=person_name or "Utente",
            person_distance_m=float(person_distance_m),
            person_azimuth_deg=azimuth_deg,
            tom_state=str(tom_state),
            gesture=str(gesture),
            gaze_at_robot=bool(gaze_at_robot),
            pointing_target_object=pointing_target_object,
            near_hand_objects=near_hand_objects,
            scene_objects=scene_objects,
            map_room=map_room or "Unknown",
            map_pose_xy=map_pose_xy,
            recommended_effort_tier=effort_tier,
            target_proxemic_distance_m=float(target_proxemic_distance_m),
            social_momentum=max(0.0, min(1.0, float(social_momentum))),
            social_inhibition=max(0.0, min(1.0, float(social_inhibition))),
        )

    def _estimate_person_center_x(self, kps: List[Tuple[float, float, float]]) -> float:
        if len(kps) >= 7:
            ls = kps[self.LEFT_SHOULDER]
            rs = kps[self.RIGHT_SHOULDER]
            if ls[2] >= 0.25 and rs[2] >= 0.25:
                return max(0.0, min(1.0, (ls[0] + rs[0]) * 0.5))
        if len(kps) >= 1 and kps[self.NOSE][2] >= 0.25:
            return max(0.0, min(1.0, float(kps[self.NOSE][0])))
        return 0.5

    @staticmethod
    def _coerce_box(obj: Any) -> DetectedBoxObject:
        if isinstance(obj, DetectedBoxObject):
            return obj
        if isinstance(obj, dict):
            bbox = obj.get("bbox_2d") or obj.get("bbox") or (0.45, 0.45, 0.55, 0.55)
            c3d = obj.get("centroid_3d")
            if c3d is None:
                c3d = (
                    float(obj.get("x", 0.0)),
                    float(obj.get("y", 0.0)),
                    float(obj.get("z", obj.get("distance_m", 1.5))),
                )
            return DetectedBoxObject(
                label=str(obj.get("label", "oggetto")),
                bbox_2d=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
                centroid_3d=(float(c3d[0]), float(c3d[1]), float(c3d[2])),
                confidence=float(obj.get("confidence", 0.80)),
                room=obj.get("room"),
            )
        # Support SemanticObject-like objects with attributes
        raw_bbox = getattr(obj, "bbox_2d", None)
        bbox = (
            raw_bbox
            if (raw_bbox is not None and len(raw_bbox) >= 4)
            else (0.45, 0.45, 0.55, 0.55)
        )
        centroid = getattr(obj, "centroid_3d", None)
        if centroid is not None and hasattr(centroid, "x"):
            cx, cy, cz = float(centroid.x), float(centroid.y), float(centroid.z)
            if abs(cx) < 1e-4 and abs(cy) < 1e-4 and abs(cz) < 1e-4:
                est_d = float(getattr(obj, "estimated_depth_m", 0.0) or 0.0)
                c3d = (0.0, 0.0, est_d if est_d > 0.05 else 1.5)
            else:
                c3d = (cx, cy, cz)
        elif isinstance(centroid, (list, tuple)) and len(centroid) >= 3:
            c3d = (float(centroid[0]), float(centroid[1]), float(centroid[2]))
        else:
            c3d = (0.0, 0.0, 1.5)
        return DetectedBoxObject(
            label=str(getattr(obj, "label", "oggetto")),
            bbox_2d=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
            centroid_3d=c3d,
            confidence=float(getattr(obj, "confidence", 0.80)),
            room=getattr(obj, "room_name", None),
        )

    def _correlate_hands_with_objects(
        self,
        kps: List[Tuple[float, float, float]],
        boxes: List[DetectedBoxObject],
        pointing_intersection_3d: Optional[Tuple[float, float, float]],
    ) -> Tuple[List[str], Optional[str]]:
        if not boxes:
            return [], None

        near_labels: List[str] = []
        wrists: List[Tuple[float, float]] = []
        if len(kps) > self.RIGHT_WRIST:
            for idx in (self.LEFT_WRIST, self.RIGHT_WRIST):
                if kps[idx][2] >= 0.30:
                    wrists.append((float(kps[idx][0]), float(kps[idx][1])))

        # Match wrists to 2D bbox expanded slightly for proxemic grasp tolerance
        for box in boxes:
            x1, y1, x2, y2 = box.bbox_2d
            cx, cy = box.center_2d
            matched = False
            for wx, wy in wrists:
                inside_expanded = (x1 - 0.12 <= wx <= x2 + 0.12) and (y1 - 0.12 <= wy <= y2 + 0.12)
                close_center = math.hypot(wx - cx, wy - cy) <= 0.22
                if inside_expanded or close_center:
                    matched = True
                    break
            if matched and box.label not in near_labels:
                near_labels.append(box.label)

        # Match 3D pointing intersection vector if present
        pointing_label: Optional[str] = None
        if pointing_intersection_3d is not None:
            px, py, pz = pointing_intersection_3d
            best_dist = 0.85  # Max 0.85m match radius from ray endpoint
            for box in boxes:
                bx, by, bz = box.centroid_3d
                d = math.sqrt((px - bx) ** 2 + (py - by) ** 2 + (pz - bz) ** 2)
                if d < best_dist:
                    best_dist = d
                    pointing_label = box.label
            if pointing_label and pointing_label not in near_labels:
                near_labels.append(pointing_label)

        return near_labels, pointing_label

    @staticmethod
    def _select_metabolic_effort_tier(
        person_distance_m: float,
        azimuth_deg: float,
        tom_state: str,
        gesture: str,
        gaze_at_robot: bool,
        target_proxemic_distance_m: float,
        social_inhibition: float,
        follow_active: bool,
    ) -> MetabolicEffortTier:
        if social_inhibition >= 0.50:
            return MetabolicEffortTier.PROXEMIC_STEP
        if tom_state == "KNEELING_EYE_LEVEL":
            if abs(person_distance_m - target_proxemic_distance_m) > 0.12:
                return MetabolicEffortTier.PROXEMIC_STEP
            return MetabolicEffortTier.MICRO_PRESENCE
        if gesture == "BECKON_COME" or follow_active:
            return MetabolicEffortTier.LOCOMOTION_NAV
        if abs(azimuth_deg) > 20.0:
            return MetabolicEffortTier.OCULOMOTOR_PIVOT
        if gesture in ("WAVE_HELLO", "THUMBS_UP", "POINTING") or gaze_at_robot:
            return MetabolicEffortTier.MICRO_PRESENCE
        return MetabolicEffortTier.ZERO_MOTOR
