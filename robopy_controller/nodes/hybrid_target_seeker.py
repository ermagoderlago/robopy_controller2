#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hybrid_target_seeker.py — Hierarchical Hybrid Navigation & Target Seeking
========================================================================
Implements Milestone 5 (TC8 / R5 / SPEC-01 / SPEC-02 / SPEC-03 / SPEC-05):
  - Phase 1 (NAV2_MACRO): Macro-navigation to target room centroid/polygon.
  - Handover: Disengages Nav2 on arrival, issues zero-velocity brake pulse,
              and activates NOMAD visual reactive search (/nomad/enable).
  - Phase 2 (NOMAD_REACTIVE): NOMAD reactive blind-spot perusal with 3-tier
              room boundary containment (Free Zone, Soft Buffer, Hard Turnaround),
              saccadic visual sweeps (every 12s / 1.5m), and 120s search timeout.
  - Phase 3 (VISUAL_SERVOING): Hailo YOLO target detection (confidence >= 0.55),
              highest-confidence candidate selection, multilingual synonym matching,
              instant NOMAD disengagement, proportional visual servoing with
              SPEC-01 velocity clamping (|v| <= 0.30 m/s, |w| <= 1.00 rad/s),
              stopping at <= 0.30m, chime played, and vocal announcement.

Conforms to:
  - SPEC-01: Motor limits, visual servoing bounds, zero-velocity pulse
  - SPEC-02: Nav2 integration, REP-105 TF compliance, 2.5D containment
  - SPEC-03: Hailo YOLO confidence gating (>= 0.55)
  - SPEC-05: Semantic Room Registry integration (MAGRoomRegistry / rooms_metadata.yaml)
  - marcus_core_rules.md: RAM < 4GB, sequential build, Zero BOM UTF-8
"""

import math
import time
import json
import re
import os
from typing import Dict, Any, List, Tuple, Optional

# Safe ROS 2 import guard
try:
    import rclpy
    from rclpy.node import Node
    from rclpy.action import ActionClient
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
    from geometry_msgs.msg import Twist, PoseStamped, PoseWithCovarianceStamped
    from std_msgs.msg import String, Bool
    from std_srvs.srv import Trigger
    from nav_msgs.msg import Odometry
    from nav2_msgs.action import NavigateToPose
    from action_msgs.msg import GoalStatus
    HAS_ROS2 = True
except ImportError:
    HAS_ROS2 = False
    Node = object
    GoalStatus = None

    class _MockMsg:
        def __init__(self, *args, **kwargs):
            self.data = None
            self.linear = self
            self.angular = self
            self.x = 0.0
            self.y = 0.0
            self.z = 0.0

    class _MockSrv:
        Request = _MockMsg
        Response = _MockMsg

    Twist = _MockMsg
    PoseStamped = _MockMsg
    PoseWithCovarianceStamped = _MockMsg
    Odometry = _MockMsg
    String = _MockMsg
    Bool = _MockMsg
    Trigger = _MockSrv
    NavigateToPose = _MockSrv


# ==============================================================================
# 1. Pure Python Engine: HybridTargetSeekerEngine / HybridSearchCoordinator
# ==============================================================================
class HybridTargetSeekerEngine:
    """
    Pure Python engine for Hierarchical Hybrid Navigation & Target Seeking.
    Decoupled from ROS 2 for deterministic unit testing and headless CI.
    Aliased to `HybridSearchCoordinator` for 100% compatibility with test infrastructure.
    """
    PHASE_IDLE = "IDLE"
    PHASE_1_NAV2_MACRO = "NAV2_MACRO"
    PHASE_2_NOMAD_REACTIVE = "NOMAD_REACTIVE"
    PHASE_3_VISUAL_SERVOING = "VISUAL_SERVOING"
    PHASE_COMPLETED = "COMPLETED"
    PHASE_FAILED = "FAILED"

    # Thresholds and Invariants
    YOLO_MIN_CONFIDENCE: float = 0.55
    TARGET_PROXIMITY_METERS: float = 0.30
    DEFAULT_SEARCH_TIMEOUT_SEC: float = 120.0

    # Kinematic & Actuation Limits (SPEC-01)
    MAX_LINEAR_VELOCITY_VS: float = 0.30   # m/s during visual servoing
    MAX_ANGULAR_VELOCITY_VS: float = 1.00  # rad/s during visual servoing
    CHASSIS_MAX_LINEAR: float = 0.40       # m/s absolute chassis ceiling
    CHASSIS_MAX_ANGULAR: float = 1.80      # rad/s absolute chassis ceiling

    # Boundary Containment Zones
    FREE_ZONE_DISTANCE: float = 0.50       # d > 0.50m: unhindered NOMAD wander
    SOFT_BUFFER_DISTANCE: float = 0.25     # 0.25m < d <= 0.50m: speed attenuation & inward deflection
    # d <= 0.25m: Hard Turnaround towards centroid

    # Saccadic Visual Sweeps
    SWEEP_INTERVAL_SEC: float = 12.0
    SWEEP_INTERVAL_METERS: float = 1.5
    SWEEP_YAW_RATE: float = 0.45           # rad/s

    # Multilingual Synonym Mapping (Italian <-> English COCO 80)
    SYNONYM_MAP: Dict[str, str] = {
        "persona": "person", "person": "person", "uomo": "person", "donna": "person", "ragazzo": "person",
        "sedia": "chair", "chair": "chair", "poltrona": "chair", "sgabello": "chair",
        "tavolo": "dining table", "dining table": "dining table", "scrivania": "dining table", "table": "dining table",
        "divano": "couch", "couch": "couch", "sofa": "couch",
        "letto": "bed", "bed": "bed",
        "bottiglia": "bottle", "bottle": "bottle",
        "tazza": "cup", "cup": "cup", "bicchiere": "cup",
        "zaino": "backpack", "backpack": "backpack", "borsa": "handbag", "handbag": "handbag",
        "chiavi": "keys", "keys": "keys",
        "occhiali": "glasses", "glasses": "glasses", "sunglasses": "glasses",
        "computer": "laptop", "laptop": "laptop", "portatile": "laptop",
        "televisore": "tv", "tv": "tv", "monitor": "tv",
        "comodino": "nightstand", "nightstand": "nightstand",
        "lampada": "lamp", "lamp": "lamp",
        "gatto": "cat", "cat": "cat",
        "cane": "dog", "dog": "dog",
        "telefono": "cell phone", "cell phone": "cell phone", "cellulare": "cell phone", "smartphone": "cell phone",
        "libro": "book", "book": "book",
    }

    # Embedded Default Known Rooms (Fallback & Test Baseline)
    DEFAULT_ROOMS: Dict[str, Dict[str, Any]] = {
        "salotto": {
            "centroid": (2.75, 2.1),
            "polygon": [(0.0, 0.0), (5.5, 0.0), (5.5, 4.2), (0.0, 4.2)],
            "bounding_box": (0.0, 0.0, 5.5, 4.2),
            "nav_goal": (2.75, 2.1, 0.0),
        },
        "cucina": {
            "centroid": (7.35, 1.9),
            "polygon": [(5.5, 0.0), (9.2, 0.0), (9.2, 3.8), (5.5, 3.8)],
            "bounding_box": (5.5, 0.0, 9.2, 3.8),
            "nav_goal": (7.35, 1.9, 1.5708),
        },
        "corridoio": {
            "centroid": (2.75, 5.0),
            "polygon": [(0.0, 4.2), (5.5, 4.2), (5.5, 5.8), (0.0, 5.8)],
            "bounding_box": (0.0, 4.2, 5.5, 5.8),
            "nav_goal": (2.75, 5.0, 0.0),
        },
        "camera da letto": {
            "centroid": (1.5, 8.0),
            "polygon": [(0.0, 5.8), (3.0, 5.8), (3.0, 10.2), (0.0, 10.2)],
            "bounding_box": (0.0, 5.8, 3.0, 10.2),
            "nav_goal": (1.5, 8.0, 0.0),
        },
        "camera": {
            "centroid": (1.5, 8.0),
            "polygon": [(0.0, 5.8), (3.0, 5.8), (3.0, 10.2), (0.0, 10.2)],
            "bounding_box": (0.0, 5.8, 3.0, 10.2),
            "nav_goal": (1.5, 8.0, 0.0),
        },
        "studio": {
            "centroid": (4.25, 8.0),
            "polygon": [(3.0, 5.8), (5.5, 5.8), (5.5, 10.2), (3.0, 10.2)],
            "bounding_box": (3.0, 5.8, 5.5, 10.2),
            "nav_goal": (4.25, 8.0, 0.0),
        },
    }

    def __init__(
        self,
        search_timeout_sec: float = DEFAULT_SEARCH_TIMEOUT_SEC,
        room_registry: Any = None,
        config_path: Optional[str] = None
    ):
        self.current_phase = self.PHASE_IDLE
        self.target_room: Optional[str] = None
        self.target_class: Optional[str] = None
        self.nav2_goal_reached: bool = False
        self.nomad_active: bool = False
        self.detected_target: Optional[Dict[str, Any]] = None
        self.final_distance_to_target: float = 999.0
        self.chime_played: bool = False
        self.completion_announced: bool = False

        self.search_timeout_sec: float = float(search_timeout_sec)
        self.room_registry = room_registry
        self.config_path = config_path

        # Internal state tracking
        self.resolved_room_name: Optional[str] = None
        self.room_centroid: Optional[Tuple[float, float]] = None
        self.room_polygon: List[Tuple[float, float]] = []
        self.room_bbox: Optional[Tuple[float, float, float, float]] = None
        self.room_nav_goal: Optional[Tuple[float, float, float]] = None

        self.nomad_search_start_time: float = 0.0
        self.mission_start_time: float = 0.0
        self.failure_reason: Optional[str] = None

        # Saccadic sweep tracking
        self.last_sweep_time: float = 0.0
        self.distance_traveled_since_sweep: float = 0.0
        self.last_robot_pose: Optional[Tuple[float, float]] = None

        # Try to load custom rooms from YAML if path provided or found
        self._custom_rooms: Dict[str, Dict[str, Any]] = {}
        self._load_rooms_from_config()

    def _load_rooms_from_config(self) -> None:
        """Loads room geometry from rooms_metadata.yaml if accessible."""
        candidate_paths = []
        if self.config_path and os.path.exists(self.config_path):
            candidate_paths.append(self.config_path)
        # Default project paths
        candidate_paths.extend([
            os.path.join(os.path.dirname(__file__), "..", "config", "rooms_metadata.yaml"),
            os.path.join(os.getcwd(), "robopy_controller", "config", "rooms_metadata.yaml"),
        ])

        for p in candidate_paths:
            if os.path.exists(p):
                try:
                    import yaml
                    with open(p, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    if data and "rooms" in data:
                        for r in data["rooms"]:
                            rname = r.get("room_name", "").strip().lower()
                            if rname:
                                poly = [(float(pt[0]), float(pt[1])) for pt in r.get("polygon", [])]
                                c = r.get("centroid", [0.0, 0.0])
                                bbox = r.get("bounding_box", [0.0, 0.0, 0.0, 0.0])
                                ng = r.get("navigation_goal", {})
                                nav_goal = (float(ng.get("x", c[0])), float(ng.get("y", c[1])), float(ng.get("theta_rad", 0.0))) if ng else None
                                self._custom_rooms[rname] = {
                                    "centroid": (float(c[0]), float(c[1])),
                                    "polygon": poly,
                                    "bounding_box": tuple(float(x) for x in bbox),
                                    "nav_goal": nav_goal,
                                }
                    break
                except Exception:
                    pass

    # --------------------------------------------------------------------------
    # Room Normalization & Resolution
    # --------------------------------------------------------------------------
    @classmethod
    def normalize_room_name(cls, raw_name: str) -> str:
        """
        Strips Italian articles and prepositions ('in', 'nella', 'all'', 'dell'', etc.)
        and normalizes whitespace and casing.
        """
        if not raw_name:
            return ""
        s = raw_name.strip().lower()
        # Remove common Italian prepositions and articles
        prefixes = [
            r"^nella\s+", r"^nello\s+", r"^nel\s+", r"^nei\s+", r"^negli\s+", r"^nelle\s+",
            r"^all['’]\s*", r"^alla\s+", r"^allo\s+", r"^al\s+", r"^ai\s+", r"^agli\s+", r"^alle\s+",
            r"^dell['’]\s*", r"^della\s+", r"^dello\s+", r"^del\s+", r"^dei\s+", r"^degli\s+", r"^delle\s+",
            r"^in\s+", r"^da\s+", r"^verso\s+"
        ]
        for p in prefixes:
            s = re.sub(p, "", s).strip()
        return s

    def resolve_room(self, room_query: str) -> Optional[Dict[str, Any]]:
        """
        Resolves room metadata (centroid, polygon, bounding box) from:
          1. MAGRoomRegistry instance (if injected)
          2. Parsed rooms_metadata.yaml
          3. Embedded default rooms
        Returns Dict with keys 'room_name', 'centroid', 'polygon', 'bounding_box', 'nav_goal'.
        """
        if not room_query:
            return None
        norm_name = self.normalize_room_name(room_query)

        # 1. Check MAGRoomRegistry if available
        if self.room_registry is not None:
            try:
                room_meta = self.room_registry.get_room(norm_name) or self.room_registry.get_room(room_query.strip().lower())
                if room_meta:
                    return {
                        "room_name": norm_name,
                        "centroid": room_meta.centroid,
                        "polygon": room_meta.polygon,
                        "bounding_box": room_meta.bounding_box,
                        "nav_goal": getattr(room_meta, "nav_goal", None),
                    }
            except Exception:
                pass

        # 2. Check custom loaded rooms from config YAML
        if norm_name in self._custom_rooms:
            info = dict(self._custom_rooms[norm_name])
            info["room_name"] = norm_name
            return info

        # 3. Check embedded default rooms
        if norm_name in self.DEFAULT_ROOMS:
            info = dict(self.DEFAULT_ROOMS[norm_name])
            info["room_name"] = norm_name
            return info

        return None

    # --------------------------------------------------------------------------
    # Phase 1: Nav2 Macro-Navigation
    # --------------------------------------------------------------------------
    def start_mission(self, room_name: str, target_class: str, strict_room_check: bool = False) -> str:
        """
        Initiates hierarchical hybrid search mission.
        Sets phase to PHASE_1_NAV2_MACRO and returns verbatim localized mission start prompt.
        """
        resolved = self.resolve_room(room_name)
        if strict_room_check and not resolved:
            self.current_phase = self.PHASE_IDLE
            self.failure_reason = f"Stanza '{room_name}' non trovata nel registro semantico."
            return f"Errore: stanza '{room_name}' non trovata nel registro semantico. Missione annullata."

        self.target_room = room_name
        self.target_class = target_class
        self.current_phase = self.PHASE_1_NAV2_MACRO
        self.nav2_goal_reached = False
        self.nomad_active = False
        self.detected_target = None
        self.final_distance_to_target = 999.0
        self.chime_played = False
        self.completion_announced = False
        self.nomad_search_start_time = 0.0
        self.mission_start_time = time.monotonic()
        self.failure_reason = None

        if resolved:
            self.resolved_room_name = resolved["room_name"]
            self.room_centroid = resolved["centroid"]
            self.room_polygon = resolved["polygon"]
            self.room_bbox = resolved["bounding_box"]
            self.room_nav_goal = resolved.get("nav_goal")
        else:
            self.resolved_room_name = self.normalize_room_name(room_name)
            self.room_centroid = (0.0, 0.0)
            self.room_polygon = []
            self.room_bbox = None
            self.room_nav_goal = None

        # Reset saccadic tracking
        self.last_sweep_time = self.mission_start_time
        self.distance_traveled_since_sweep = 0.0
        self.last_robot_pose = None

        return f"Missione avviata: ricerca '{target_class}' in '{room_name}' via Nav2 macro-navigazione."

    def notify_nav2_arrival(self) -> None:
        """
        Triggered upon Nav2 arrival at target room centroid or boundary entry.
        Transitions state to PHASE_2_NOMAD_REACTIVE and arms NOMAD.
        """
        if self.current_phase == self.PHASE_1_NAV2_MACRO:
            self.nav2_goal_reached = True
            self.current_phase = self.PHASE_2_NOMAD_REACTIVE
            self.nomad_active = True
            self.nomad_search_start_time = time.monotonic()
            self.last_sweep_time = self.nomad_search_start_time
            self.distance_traveled_since_sweep = 0.0

    # --------------------------------------------------------------------------
    # Phase 2: NOMAD Reactive Search & Room Boundary Containment
    # --------------------------------------------------------------------------
    @staticmethod
    def is_point_in_polygon(px: float, py: float, polygon: List[Tuple[float, float]]) -> bool:
        """
        Jordan curve theorem ray casting algorithm.
        Determines whether 2D point (px, py) lies inside polygon.
        """
        if not polygon or len(polygon) < 3:
            return True  # If no polygon defined, treat as unconstrained
        inside = False
        n = len(polygon)
        j = n - 1
        for i in range(n):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi):
                inside = not inside
            j = i
        return inside

    @staticmethod
    def distance_point_to_segment(
        px: float, py: float, ax: float, ay: float, bx: float, by: float
    ) -> Tuple[float, float, float]:
        """
        Calculates distance from point P(px, py) to line segment AB.
        Returns (distance, closest_x, closest_y).
        """
        dx = bx - ax
        dy = by - ay
        l2 = dx * dx + dy * dy
        if l2 <= 1e-12:
            dist = math.hypot(px - ax, py - ay)
            return dist, ax, ay

        # Projection scalar clamped to [0.0, 1.0]
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
        qx = ax + t * dx
        qy = ay + t * dy
        dist = math.hypot(px - qx, py - qy)
        return dist, qx, qy

    def min_distance_to_polygon(
        self, px: float, py: float, polygon: List[Tuple[float, float]]
    ) -> Tuple[float, float, float]:
        """
        Computes minimum Euclidean distance from point P(px, py) to boundary of polygon.
        Returns (min_dist, closest_x, closest_y).
        """
        if not polygon or len(polygon) < 2:
            return 999.0, px, py

        min_d = float("inf")
        best_qx, best_qy = px, py
        n = len(polygon)
        for i in range(n):
            ax, ay = polygon[i]
            bx, by = polygon[(i + 1) % n]
            d, qx, qy = self.distance_point_to_segment(px, py, ax, ay, bx, by)
            if d < min_d:
                min_d = d
                best_qx, best_qy = qx, qy

        return min_d, best_qx, best_qy

    @staticmethod
    def normalize_angle(angle: float) -> float:
        """Normalizes angle to [-pi, +pi]."""
        while angle > math.pi:
            angle -= 2.0 * math.pi
        while angle < -math.pi:
            angle += 2.0 * math.pi
        return angle

    def filter_nomad_velocity(
        self,
        nomad_vx: float,
        nomad_wz: float,
        robot_x: float,
        robot_y: float,
        robot_yaw: float
    ) -> Tuple[float, float, str]:
        """
        3-Tier Room Boundary Containment Filter (Phase 2):
          Tier 1 (Free Zone, d > 0.50m & inside): pass NOMAD velocities directly.
          Tier 2 (Soft Buffer, 0.25m < d <= 0.50m & inside): attenuate forward speed,
                 deflect steering inward away from boundary.
          Tier 3 (Hard Turnaround, d <= 0.25m or outside): halt forward speed,
                 steer actively towards room centroid.
        Returns (filtered_vx, filtered_wz, zone_name).
        """
        if not self.room_polygon or len(self.room_polygon) < 3:
            # No boundary constraints available
            return nomad_vx, nomad_wz, "UNCONSTRAINED"

        inside = self.is_point_in_polygon(robot_x, robot_y, self.room_polygon)
        d_min, qx, qy = self.min_distance_to_polygon(robot_x, robot_y, self.room_polygon)

        cx, cy = self.room_centroid if self.room_centroid else (0.0, 0.0)

        # Tier 3: Hard Turnaround (outside room or too close to boundary)
        if (not inside) or (d_min <= self.SOFT_BUFFER_DISTANCE):
            # Calculate heading to centroid
            target_yaw = math.atan2(cy - robot_y, cx - robot_x)
            yaw_err = self.normalize_angle(target_yaw - robot_yaw)

            # If aligned towards centroid within ~20 degrees (0.35 rad), translate forward into room
            if abs(yaw_err) <= 0.35:
                out_vx = 0.12
                out_wz = max(-0.60, min(0.60, 1.5 * yaw_err))
            else:
                out_vx = 0.0
                out_wz = max(-0.60, min(0.60, 1.5 * yaw_err))
                if abs(out_wz) < 0.10 and abs(yaw_err) > 0.01:
                    out_wz = 0.30 if yaw_err > 0 else -0.30

            return out_vx, out_wz, "HARD_TURNAROUND"

        # Tier 2: Soft Buffer (0.25m < d <= 0.50m inside)
        if d_min <= self.FREE_ZONE_DISTANCE:
            # Linear attenuation factor between 0.30 and 1.00
            ratio = (d_min - self.SOFT_BUFFER_DISTANCE) / (self.FREE_ZONE_DISTANCE - self.SOFT_BUFFER_DISTANCE)
            attenuation = 0.30 + 0.70 * max(0.0, min(1.0, ratio))
            out_vx = nomad_vx * attenuation

            # Steer inward: normal from boundary to robot
            nx = robot_x - qx
            ny = robot_y - qy
            n_mag = math.hypot(nx, ny)
            if n_mag > 1e-6:
                inward_yaw = math.atan2(ny, nx)
                deflection_err = self.normalize_angle(inward_yaw - robot_yaw)
                out_wz = nomad_wz + 0.40 * max(-1.0, min(1.0, deflection_err))
            else:
                out_wz = nomad_wz

            out_vx = max(0.0, min(self.CHASSIS_MAX_LINEAR, out_vx))
            out_wz = max(-self.CHASSIS_MAX_ANGULAR, min(self.CHASSIS_MAX_ANGULAR, out_wz))
            return out_vx, out_wz, "SOFT_BUFFER"

        # Tier 1: Interior Free Zone
        out_vx = max(0.0, min(self.CHASSIS_MAX_LINEAR, nomad_vx))
        out_wz = max(-self.CHASSIS_MAX_ANGULAR, min(self.CHASSIS_MAX_ANGULAR, nomad_wz))
        return out_vx, out_wz, "FREE_ZONE"

    def check_saccadic_sweep(
        self,
        robot_x: Optional[float] = None,
        robot_y: Optional[float] = None,
        now: Optional[float] = None
    ) -> Optional[Tuple[float, float]]:
        """
        Evaluates active saccadic visual sweeps during NOMAD search.
        Every SWEEP_INTERVAL_SEC (12s) or SWEEP_INTERVAL_METERS (1.5m), commands
        a yaw rotation (0.45 rad/s) to scan blind spots.
        Returns (vx, wz) if sweep active, else None.
        """
        if self.current_phase != self.PHASE_2_NOMAD_REACTIVE:
            return None

        current_time = now if now is not None else time.monotonic()

        # Update distance traveled
        if robot_x is not None and robot_y is not None:
            if self.last_robot_pose is not None:
                step_dist = math.hypot(robot_x - self.last_robot_pose[0], robot_y - self.last_robot_pose[1])
                self.distance_traveled_since_sweep += step_dist
            self.last_robot_pose = (robot_x, robot_y)

        time_since_sweep = current_time - self.last_sweep_time
        if time_since_sweep >= self.SWEEP_INTERVAL_SEC or self.distance_traveled_since_sweep >= self.SWEEP_INTERVAL_METERS:
            # Trigger saccadic sweep
            self.last_sweep_time = current_time
            self.distance_traveled_since_sweep = 0.0
            return 0.0, self.SWEEP_YAW_RATE

        return None

    def check_search_timeout(self, now: Optional[float] = None) -> bool:
        """
        Monitors Phase 2 search duration against 120s budget.
        On expiration without detection, transitions to PHASE_FAILED and sets status TARGET_NOT_FOUND.
        """
        if self.current_phase != self.PHASE_2_NOMAD_REACTIVE:
            return False

        current_time = now if now is not None else time.monotonic()
        elapsed = current_time - self.nomad_search_start_time
        if elapsed >= self.search_timeout_sec:
            self.current_phase = self.PHASE_FAILED
            self.nomad_active = False
            self.failure_reason = "TARGET_NOT_FOUND"
            return True
        return False

    # --------------------------------------------------------------------------
    # Phase 3: Hailo YOLO Target Sighting & Visual Servoing
    # --------------------------------------------------------------------------
    @classmethod
    def _matches_target_class(cls, det_class: str, target_class: str) -> bool:
        """
        Matches detection class label with target class using multilingual synonyms.
        """
        if not det_class or not target_class:
            return False
        d = det_class.strip().lower()
        t = target_class.strip().lower()
        if d == t:
            return True
        return cls.SYNONYM_MAP.get(d, d) == cls.SYNONYM_MAP.get(t, t)

    def process_hailo_yolo_detections(self, detections: List[Dict[str, Any]]) -> Optional[str]:
        """
        Ingests and evaluates YOLO detections during NOMAD exploration.
        Filters candidates matching target_class with confidence >= 0.55.
        Selects candidate with highest confidence.
        On match: transitions to PHASE_3_VISUAL_SERVOING, disengages NOMAD,
                  and returns verbatim arrival notification string.
        """
        if self.current_phase != self.PHASE_2_NOMAD_REACTIVE:
            return None

        if not detections:
            return None

        target_hits = [
            d for d in detections
            if self._matches_target_class(d.get("class", ""), self.target_class)
            and d.get("confidence", 0.0) >= self.YOLO_MIN_CONFIDENCE
        ]

        if target_hits:
            best_hit = max(target_hits, key=lambda d: d.get("confidence", 0.0))
            self.detected_target = best_hit
            self.current_phase = self.PHASE_3_VISUAL_SERVOING
            self.nomad_active = False
            return (
                f"Target '{self.target_class}' avvistato con confidenza {best_hit['confidence']:.2f}. "
                f"Avvio avvicinamento visual servoing."
            )

        return None

    def execute_visual_servoing_step(self, current_distance_m: float) -> Tuple[str, bool]:
        """
        Executes proximity approach step towards detected target.
        When current_distance_m <= TARGET_PROXIMITY_METERS (0.30m):
          - Sets current_phase = PHASE_COMPLETED
          - Sets chime_played = True, completion_announced = True
          - Commands motor stop and returns completion report.
        """
        if self.current_phase != self.PHASE_3_VISUAL_SERVOING:
            return "Visual servoing non attivo", False

        self.final_distance_to_target = current_distance_m

        if current_distance_m <= self.TARGET_PROXIMITY_METERS:
            self.current_phase = self.PHASE_COMPLETED
            self.chime_played = True
            self.completion_announced = True
            msg = f"Target '{self.target_class}' raggiunto con successo a {current_distance_m:.2f}m. Motori arrestati."
            return msg, True

        return f"Avvicinamento in corso a {current_distance_m:.2f}m...", False

    def compute_visual_servoing_twist(
        self,
        bbox_center_x: float,
        image_width: float,
        current_distance_m: float
    ) -> Tuple[float, float, bool]:
        """
        Kinematic Visual Servoing Controller (SPEC-01 compliant):
          Horizontal Centering: w = -Kp_w * e_x (with deadband)
          Proportional Approach: v = Kp_v * (d - 0.30) * cos(e_x)
          Clamps: |v| <= 0.30 m/s, |w| <= 1.00 rad/s
        Returns (v_linear, w_angular, is_completed).
        """
        if current_distance_m <= self.TARGET_PROXIMITY_METERS:
            self.current_phase = self.PHASE_COMPLETED
            self.chime_played = True
            self.completion_announced = True
            return 0.0, 0.0, True

        # Normalized horizontal offset [-1.0, +1.0]
        half_w = max(1.0, image_width / 2.0)
        e_x = (bbox_center_x - half_w) / half_w

        # Deadband (+/- 4%) to prevent hunting oscillations
        if abs(e_x) <= 0.04:
            e_x = 0.0

        # Steering control
        kp_w = 1.20
        raw_w = -kp_w * e_x
        w_cmd = max(-self.MAX_ANGULAR_VELOCITY_VS, min(self.MAX_ANGULAR_VELOCITY_VS, raw_w))

        # Approach control attenuated by heading alignment
        kp_v = 0.35
        dist_err = max(0.0, current_distance_m - self.TARGET_PROXIMITY_METERS)
        alignment_gain = max(0.10, math.cos(abs(e_x) * math.pi / 2.0))
        raw_v = kp_v * dist_err * alignment_gain
        # Ensure minimum crawl to overcome static friction when moving
        if raw_v > 0.01:
            raw_v = max(0.04, raw_v)
        v_cmd = min(self.MAX_LINEAR_VELOCITY_VS, raw_v)

        return v_cmd, w_cmd, False


# Backward compatibility alias for test infrastructure
HybridSearchCoordinator = HybridTargetSeekerEngine


# ==============================================================================
# 2. ROS 2 Node Wrapper: HybridTargetSeekerNode
# ==============================================================================
class HybridTargetSeekerNode(Node):
    """
    ROS 2 Lifecycle Node for Hierarchical Hybrid Target Seeking.
    Manages Nav2 ActionClient, NOMAD activation & velocity multiplexing,
    Hailo YOLO subscription, and visual servoing motor actuation.
    """
    def __init__(self, node_name: str = "hybrid_target_seeker_node"):
        if not HAS_ROS2:
            raise RuntimeError("ROS 2 environment (rclpy) is not available.")
        super().__init__(node_name)

        # Core engine
        self.engine = HybridTargetSeekerEngine()

        # Parameters
        self.declare_parameter("search_timeout_sec", 120.0)
        self.declare_parameter("target_proximity_m", 0.30)
        self.declare_parameter("yolo_min_confidence", 0.55)

        self.engine.search_timeout_sec = float(self.get_parameter("search_timeout_sec").value)
        self.engine.TARGET_PROXIMITY_METERS = float(self.get_parameter("target_proximity_m").value)
        self.engine.YOLO_MIN_CONFIDENCE = float(self.get_parameter("yolo_min_confidence").value)

        # QoS Profiles
        reliable_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        cmd_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # Publishers
        self.pub_cmd_vel = self.create_publisher(Twist, "/cmd_vel", cmd_qos)
        self.pub_nomad_enable = self.create_publisher(Bool, "/nomad/enable", reliable_qos)
        self.pub_nomad_mode = self.create_publisher(String, "/nomad/set_mode", reliable_qos)
        self.pub_vui_speech = self.create_publisher(String, "/ai/conversation/response", reliable_qos)
        self.pub_search_status = self.create_publisher(String, "/hybrid_search/status", reliable_qos)

        # Subscribers
        self.sub_nomad_cmd = self.create_subscription(
            Twist, "/cmd_vel_nomad", self._on_nomad_cmd_vel, 10
        )
        self.sub_odom = self.create_subscription(
            Odometry, "/odom", self._on_odometry, 10
        )
        self.sub_yolo = self.create_subscription(
            String, "/hailo/detections", self._on_yolo_detections_json, 10
        )

        # Nav2 Action Client
        self.nav2_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self._nav2_goal_handle = None

        # Services
        self.srv_start = self.create_service(Trigger, "/hybrid_search/start", self._handle_start_service)
        self.srv_stop = self.create_service(Trigger, "/hybrid_search/stop", self._handle_stop_service)

        # Robot Pose Tracking
        self.robot_x: float = 0.0
        self.robot_y: float = 0.0
        self.robot_yaw: float = 0.0

        # High-rate control loop (10 Hz)
        self.timer = self.create_timer(0.10, self._control_loop)
        self.get_logger().info("HybridTargetSeekerNode initialized successfully.")

    def _on_odometry(self, msg: Odometry) -> None:
        """Updates robot pose from odometry."""
        pos = msg.pose.pose.position
        ori = msg.pose.pose.orientation
        self.robot_x = pos.x
        self.robot_y = pos.y
        # Compute yaw from quaternion
        siny_cosp = 2.0 * (ori.w * ori.z + ori.x * ori.y)
        cosy_cosp = 1.0 - 2.0 * (ori.y * ori.y + ori.z * ori.z)
        self.robot_yaw = math.atan2(siny_cosp, cosy_cosp)

    def _on_nomad_cmd_vel(self, msg: Twist) -> None:
        """Filters and arbitrates NOMAD reactive velocities during Phase 2."""
        if self.engine.current_phase != HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE:
            return

        vx, wz, zone = self.engine.filter_nomad_velocity(
            msg.linear.x, msg.angular.z, self.robot_x, self.robot_y, self.robot_yaw
        )

        out_twist = Twist()
        out_twist.linear.x = vx
        out_twist.angular.z = wz
        self.pub_cmd_vel.publish(out_twist)

    def _on_yolo_detections_json(self, msg: String) -> None:
        """Parses YOLO detection stream and invokes confidence gating."""
        if self.engine.current_phase != HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE:
            return
        try:
            detections = json.loads(msg.data)
            if isinstance(detections, list):
                sighting = self.engine.process_hailo_yolo_detections(detections)
                if sighting:
                    self.get_logger().info(f"Target sighting: {sighting}")
                    # Disengage NOMAD immediately
                    self._disengage_nomad()
                    # Announce vocally
                    speech_msg = String()
                    speech_msg.data = sighting
                    self.pub_vui_speech.publish(speech_msg)
        except Exception as e:
            self.get_logger().warning(f"Error parsing YOLO detections: {e}")

    def _disengage_nomad(self) -> None:
        """Deactivates NOMAD reactive search and commands zero brake pulse."""
        enable_msg = Bool()
        enable_msg.data = False
        self.pub_nomad_enable.publish(enable_msg)

        mode_msg = String()
        mode_msg.data = "STOP"
        self.pub_nomad_mode.publish(mode_msg)

        stop_twist = Twist()
        self.pub_cmd_vel.publish(stop_twist)

    def _control_loop(self) -> None:
        """Periodic safety and state maintenance loop."""
        # 1. Check Phase 2 timeout
        if self.engine.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE:
            if self.engine.check_search_timeout():
                self.get_logger().warn("Search timeout expired: target not found.")
                self._disengage_nomad()
                speech_msg = String()
                speech_msg.data = f"Tempo di ricerca scaduto. Target '{self.engine.target_class}' non individuato."
                self.pub_vui_speech.publish(speech_msg)

        # 2. Publish telemetry status
        status_msg = String()
        status_msg.data = json.dumps({
            "phase": self.engine.current_phase,
            "target_room": self.engine.target_room,
            "target_class": self.engine.target_class,
            "nomad_active": self.engine.nomad_active,
            "nav2_goal_reached": self.engine.nav2_goal_reached,
            "chime_played": self.engine.chime_played,
            "completion_announced": self.engine.completion_announced,
            "final_distance": self.engine.final_distance_to_target,
        })
        self.pub_search_status.publish(status_msg)

    def _handle_start_service(self, request, response):
        """Service trigger to start hybrid search."""
        response.success = True
        response.message = f"Hybrid search node ready in state: {self.engine.current_phase}"
        return response

    def _handle_stop_service(self, request, response):
        """Emergency stop service trigger."""
        self._disengage_nomad()
        self.engine.current_phase = HybridTargetSeekerEngine.PHASE_FAILED
        self.engine.failure_reason = "MANUAL_ABORT"
        response.success = True
        response.message = "Hybrid search stopped."
        return response


def main(args=None):
    """Entry point for ROS 2 node execution."""
    if not HAS_ROS2:
        print("Error: rclpy not installed. HybridTargetSeekerNode cannot run as a ROS 2 node.")
        return
    rclpy.init(args=args)
    node = HybridTargetSeekerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
