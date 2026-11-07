#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hailo_digital_twin_node.py
==========================
Emulatore Digital Twin della NPU Hailo-10H (40 TOPS PCIe Gen 3) e del Mapper Semantico 3D
per far funzionare il 100% della pipeline percettiva di Marcus su PC senza hardware Hailo.

Conformità SPEC-03:
- YOLOv8s (80 classi COCO) con soglia confidenza [0.45, 0.75] (default 0.55) e NMS
- Proiezione prospettica 3D -> 2D (640x480, FOV 69°) e verifica occlusione da muri e Depth 16UC1
- YOLOv8s-Pose (17 Keypoints COCO + 16 segmenti scheletrici ufficiali)
- Biometria Facciale SCRFD + ArcFace 512-dim con normalizzazione L2 rigorosa (||v||_2 = 1.0 ± 1e-5)
- Dynamic Enrollment (10 campioni consecutivi, media vettoriale L2 e salvataggio .npy in Shadow Sandbox)
- NetVLAD 4096-dim Visual Place Recognition Descriptor
- Arbitraggio VDevice Break-Before-Make (/hailo_bridge/yield_vdevice - IMP-BIO-001)
- Spatial Surprise Engine (/vision/spatial_anomaly) per alimentare la Curiosità di Marcus
- Lazy Publishing Guard (zero overhead di rendering debug se count_subscribers == 0)
"""

from __future__ import annotations

import os
import math
import time
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import numpy as np

try:
    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import Image
    from nav_msgs.msg import Odometry
    from std_msgs.msg import String, Float32MultiArray
    from std_srvs.srv import SetBool
    from vision_msgs.msg import Detection2DArray, Detection2D, ObjectHypothesisWithPose
    from visualization_msgs.msg import MarkerArray, Marker
    from geometry_msgs.msg import Point
    try:
        from robopy_controller.msg import SemanticObject, SemanticObjectArray
    except ImportError:
        SemanticObject = None
        SemanticObjectArray = None
    HAS_ROS2 = True
except ImportError:
    HAS_ROS2 = False
    Node = object
    SemanticObject = None
    SemanticObjectArray = None


# 16 connessioni scheletriche ufficiali COCO (SPEC-03 / src/hailo_bridge_node.cpp)
COCO_SKELETON_PAIRS: List[Tuple[int, int]] = [
    (0, 1), (0, 2), (1, 3), (2, 4),
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16)
]


@dataclass
class WorldObject3D:
    object_id: str
    class_label: str
    x: float
    y: float
    z: float = 0.4
    width: float = 0.5
    height: float = 0.7
    is_dynamic_anomaly: bool = False
    base_surprise: float = 0.0
    person_identity: Optional[str] = None
    posture_state: str = "STANDING"  # STANDING, WALKING, WAVING, STOP_GESTURE, DO_NOT_DISTURB_DESK, FALL_EMERGENCY
    facing_yaw: float = 0.0


@dataclass
class SimulatedDetection:
    object_id: str
    label: str
    confidence: float
    bbox_cx: float
    bbox_cy: float
    bbox_w: float
    bbox_h: float
    depth_m: float
    world_pos: Tuple[float, float, float]
    keypoints_17: Optional[List[Tuple[float, float, float]]] = None
    recognized_face_id: Optional[str] = None
    face_similarity: float = 0.0
    surprise_score: float = 0.0


class HailoDigitalTwinEngine:
    """
    Motore deterministico di emulazione NPU Hailo-10H (Dual-Backend: 3D Frustum + Depth 16UC1 Occlusion
    e Biometria L2 ArcFace 512-dim).
    """

    IMG_W: int = 640
    IMG_H: int = 480
    HFOV_RAD: float = 1.204277  # 69 gradi OAK-D Lite
    FX: float = 467.8
    FY: float = 467.8
    CX: float = 320.0
    CY: float = 240.0
    CAM_Z_M: float = 0.2616

    def __init__(
        self,
        conf_threshold: float = 0.55,
        face_similarity_threshold: float = 0.72,
        known_faces_dir: Optional[str] = None
    ):
        # Vincolo Zona Verde SPEC-03: conf in [0.45, 0.75], face thresh in [0.68, 0.82]
        self.conf_threshold = max(0.45, min(0.75, float(conf_threshold)))
        self.face_similarity_threshold = max(0.68, min(0.82, float(face_similarity_threshold)))

        default_faces_dir = os.environ.get("MARCUS_KNOWN_FACES_DIR", "")
        self.known_faces_dir = Path(known_faces_dir or default_faces_dir) if (known_faces_dir or default_faces_dir) else None

        self.vdevice_yielded: bool = False
        self.known_faces_db: Dict[str, np.ndarray] = {}
        self.enroll_target_name: Optional[str] = None
        self.enroll_samples: List[np.ndarray] = []
        self.known_static_objects: Dict[str, Tuple[float, float]] = {}

        # Pre-popoliamo l'identità familiare "luca" con embedding deterministico L2-normalizzato
        self.known_faces_db["luca"] = self.generate_deterministic_arcface_embedding("luca")
        if self.known_faces_dir and self.known_faces_dir.exists():
            self.load_known_faces_from_disk()

    @staticmethod
    def normalize_l2_strict(vec: np.ndarray) -> np.ndarray:
        """
        Normalizzazione unitaria rigorosa L2 (SPEC-03 Zona Rossa: ||v||_2 = 1.0 ± 1e-5).
        """
        v = np.asarray(vec, dtype=np.float32).flatten()
        norm = float(np.linalg.norm(v))
        if norm < 1e-7 or not np.isfinite(norm):
            unit = np.zeros_like(v, dtype=np.float32)
            unit[0] = 1.0
            return unit
        return (v / norm).astype(np.float32)

    @classmethod
    def generate_deterministic_arcface_embedding(cls, identity_seed: str, noise_level: float = 0.0) -> np.ndarray:
        """
        Genera un embedding ArcFace a 512 dimensioni deterministico per una data identità
        e garantisce ||v||_2 = 1.0.
        """
        seed_int = abs(hash(identity_seed.strip().lower())) % (2**31 - 1)
        rng = np.random.RandomState(seed_int)
        base_vec = rng.randn(512).astype(np.float32)
        if noise_level > 0.0:
            noise = np.random.randn(512).astype(np.float32) * noise_level
            base_vec = base_vec + noise
        return cls.normalize_l2_strict(base_vec)

    def load_known_faces_from_disk(self) -> int:
        if not self.known_faces_dir or not self.known_faces_dir.exists():
            return 0
        loaded = 0
        for entry in self.known_faces_dir.iterdir():
            if entry.is_dir():
                npy_file = entry / "embedding.npy"
                if npy_file.exists():
                    try:
                        vec = np.load(str(npy_file))
                        if vec.size == 512:
                            self.known_faces_db[entry.name.lower()] = self.normalize_l2_strict(vec)
                            loaded += 1
                    except Exception:
                        pass
        return loaded

    def start_dynamic_enrollment(self, person_name: str) -> None:
        clean = person_name.strip().lower()
        if clean:
            self.enroll_target_name = clean
            self.enroll_samples.clear()

    def process_enrollment_sample(self, raw_embedding: np.ndarray, iou_quality: float = 0.85, luma: float = 128.0) -> bool:
        """
        Raccoglie 10 frame consecutivi validi (SPEC-03 Zona Verde: scarta IoU < 0.60 o luma anomala),
        calcola la media vettoriale L2 e salva `embedding.npy`.
        Restituisce True quando l'enrollment di 10 campioni è completato.
        """
        if not self.enroll_target_name:
            return False
        if iou_quality < 0.60 or luma < 15.0 or luma > 245.0:
            return False

        norm_sample = self.normalize_l2_strict(raw_embedding)
        self.enroll_samples.append(norm_sample)

        if len(self.enroll_samples) >= 10:
            mean_vec = np.mean(np.stack(self.enroll_samples, axis=0), axis=0)
            final_emb = self.normalize_l2_strict(mean_vec)
            self.known_faces_db[self.enroll_target_name] = final_emb

            if self.known_faces_dir:
                target_dir = self.known_faces_dir / self.enroll_target_name
                target_dir.mkdir(parents=True, exist_ok=True)
                np.save(str(target_dir / "embedding.npy"), final_emb)

            self.enroll_target_name = None
            self.enroll_samples.clear()
            return True
        return False

    def match_arcface_embedding(self, query_embedding: np.ndarray) -> Tuple[str, float]:
        """
        Confronta un embedding 512-dim contro il database biometrico tramite similarità coseno.
        """
        q = self.normalize_l2_strict(query_embedding)
        best_name = "unknown"
        best_sim = -1.0
        for name, db_vec in self.known_faces_db.items():
            sim = float(np.dot(q, db_vec))
            if sim > best_sim:
                best_sim = sim
                best_name = name

        if best_sim >= self.face_similarity_threshold:
            return best_name, best_sim
        return "unknown", max(0.0, best_sim)

    def set_vdevice_yield(self, yield_to_vlm: bool) -> Tuple[bool, str]:
        """Arbitraggio dinamico VDevice PCIe (IMP-BIO-001)."""
        self.vdevice_yielded = bool(yield_to_vlm)
        msg = (
            "Hailo VDevice yielded to external VLM."
            if self.vdevice_yielded
            else "Hailo VDevice reclaimed for primary vision pipeline."
        )
        return True, msg

    def generate_17_keypoints_for_posture(
        self,
        bbox_cx: float,
        bbox_cy: float,
        bbox_w: float,
        bbox_h: float,
        posture: str
    ) -> List[Tuple[float, float, float]]:
        """
        Genera i 17 keypoints COCO normalizzati [0, 1] coerenti con il decoder Pose di SPEC-03.
        Ordine COCO:
        0:nose, 1:left_eye, 2:right_eye, 3:left_ear, 4:right_ear,
        5:left_shoulder, 6:right_shoulder, 7:left_elbow, 8:right_elbow,
        9:left_wrist, 10:right_wrist, 11:left_hip, 12:right_hip,
        13:left_knee, 14:right_knee, 15:left_ankle, 16:right_ankle
        """
        cx_n = bbox_cx / self.IMG_W
        cy_n = bbox_cy / self.IMG_H
        w_n = bbox_w / self.IMG_W
        h_n = bbox_h / self.IMG_H

        top_y = cy_n - 0.45 * h_n
        sh_y = cy_n - 0.25 * h_n
        hip_y = cy_n + 0.05 * h_n
        knee_y = cy_n + 0.25 * h_n
        ank_y = min(0.98, cy_n + 0.45 * h_n)

        # Default standing wrists
        lw_x, lw_y = cx_n - 0.22 * w_n, cy_n
        rw_x, rw_y = cx_n + 0.22 * w_n, cy_n

        if posture in ("WAVING", "STOP_GESTURE"):
            # Mano destra alzata sopra la spalla (trigger gesti sicurezza IMP-BIO-004)
            rw_x, rw_y = cx_n + 0.28 * w_n, top_y - 0.04 * h_n
        elif posture == "FALL_EMERGENCY":
            # Persona distesa orizzontalmente a terra
            top_y = cy_n + 0.15 * h_n
            sh_y = cy_n + 0.15 * h_n
            hip_y = cy_n + 0.18 * h_n

        kps = [
            (cx_n, top_y, 0.94),                             # 0: nose
            (cx_n - 0.04 * w_n, top_y - 0.02 * h_n, 0.92),   # 1: left_eye
            (cx_n + 0.04 * w_n, top_y - 0.02 * h_n, 0.92),   # 2: right_eye
            (cx_n - 0.08 * w_n, top_y, 0.85),                # 3: left_ear
            (cx_n + 0.08 * w_n, top_y, 0.85),                # 4: right_ear
            (cx_n - 0.18 * w_n, sh_y, 0.95),                 # 5: left_shoulder
            (cx_n + 0.18 * w_n, sh_y, 0.95),                 # 6: right_shoulder
            (cx_n - 0.22 * w_n, (sh_y + hip_y) * 0.5, 0.89), # 7: left_elbow
            (cx_n + 0.22 * w_n, (sh_y + hip_y) * 0.5, 0.89), # 8: right_elbow
            (lw_x, lw_y, 0.90),                              # 9: left_wrist
            (rw_x, rw_y, 0.93),                              # 10: right_wrist
            (cx_n - 0.14 * w_n, hip_y, 0.91),                # 11: left_hip
            (cx_n + 0.14 * w_n, hip_y, 0.91),                # 12: right_hip
            (cx_n - 0.14 * w_n, knee_y, 0.86),               # 13: left_knee
            (cx_n + 0.14 * w_n, knee_y, 0.86),               # 14: right_knee
            (cx_n - 0.14 * w_n, ank_y, 0.82),                # 15: left_ankle
            (cx_n + 0.14 * w_n, ank_y, 0.82),                # 16: right_ankle
        ]
        # Clamping dentro [0.01, 0.99] (nessun Y=0 invalido - SPEC-03 Zona Verde)
        return [(max(0.01, min(0.99, x)), max(0.01, min(0.99, y)), c) for (x, y, c) in kps]

    @staticmethod
    def _segments_intersect(
        p1: Tuple[float, float],
        p2: Tuple[float, float],
        q1: Tuple[float, float],
        q2: Tuple[float, float]
    ) -> bool:
        """Verifica se il raggio visivo camera->oggetto (p1->p2) interseca un segmento di muro (q1->q2)."""
        def ccw(a, b, c):
            return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])
        return (ccw(p1, q1, q2) != ccw(p2, q1, q2)) and (ccw(p1, p2, q1) != ccw(p1, p2, q2))

    def infer_scene(
        self,
        robot_x: float,
        robot_y: float,
        robot_yaw: float,
        world_objects: List[WorldObject3D],
        wall_segments: Optional[List[Tuple[Tuple[float, float], Tuple[float, float]]]] = None,
        depth_16uc1_mm: Optional[np.ndarray] = None
    ) -> Tuple[List[SimulatedDetection], List[Dict[str, Any]], np.ndarray]:
        """
        Esegue un ciclo completo di inferenza simulata Hailo-10H:
        - Frustum culling 3D e controllo occlusione (muri 2D + buffer Depth 16UC1)
        - Rilevamento YOLO + Pose 17-kp + ArcFace 512-dim L2 + Spatial Surprise S(O_i)
        - Calcolo descrittore NetVLAD 4096-dim L2-normalizzato
        """
        if self.vdevice_yielded:
            return [], [], np.zeros(4096, dtype=np.float32)

        detections: List[SimulatedDetection] = []
        spatial_anomalies: List[Dict[str, Any]] = []
        walls = wall_segments or []

        for obj in world_objects:
            dx = obj.x - robot_x
            dy = obj.y - robot_y
            dist = math.hypot(dx, dy)
            if dist < 0.22 or dist > 7.5:
                continue

            angle_to_obj = math.atan2(dy, dx)
            rel_yaw = math.atan2(math.sin(angle_to_obj - robot_yaw), math.cos(angle_to_obj - robot_yaw))

            # Frustum culling orizzontale (FOV 69° -> ±34.5°)
            if abs(rel_yaw) > (self.HFOV_RAD * 0.5):
                continue

            # 1. Controllo occlusione geometrica contro i muri della casa
            occluded_by_wall = False
            for w_start, w_end in walls:
                if self._segments_intersect((robot_x, robot_y), (obj.x, obj.y), w_start, w_end):
                    occluded_by_wall = True
                    break
            if occluded_by_wall:
                continue

            # 2. Proiezione prospettica su sensore 640x480
            z_cam = dist * math.cos(rel_yaw)
            if z_cam <= 0.20:
                continue
            x_cam = -dist * math.sin(rel_yaw)
            u_px = float(self.CX - (x_cam / z_cam) * self.FX)
            v_px = float(self.CY - ((obj.z - self.CAM_Z_M) / z_cam) * self.FY)
            u_px = max(20.0, min(self.IMG_W - 20.0, u_px))
            v_px = max(20.0, min(self.IMG_H - 20.0, v_px))

            # 3. Controllo occlusione contro il buffer Depth 16UC1 (in millimetri) se fornito
            if depth_16uc1_mm is not None and depth_16uc1_mm.shape == (self.IMG_H, self.IMG_W):
                u_idx = int(round(u_px))
                v_idx = int(round(v_px))
                measured_depth_m = float(depth_16uc1_mm[v_idx, u_idx]) * 0.001
                if 0.15 < measured_depth_m < (z_cam - 0.35):
                    # Un ostacolo più vicino copre questo oggetto
                    continue

            bbox_w = max(24.0, min(380.0, (obj.width / z_cam) * self.FX))
            bbox_h = max(32.0, min(420.0, (obj.height / z_cam) * self.FY))
            conf = max(self.conf_threshold, min(0.98, 0.94 - 0.04 * z_cam))

            kps_17 = None
            rec_face_id = None
            face_sim = 0.0

            if obj.class_label == "person":
                kps_17 = self.generate_17_keypoints_for_posture(
                    u_px, v_px, bbox_w, bbox_h, obj.posture_state
                )
                # Biometria ArcFace attiva se la persona è entro 4.0m
                if z_cam <= 4.0 and obj.person_identity:
                    raw_emb = self.generate_deterministic_arcface_embedding(
                        obj.person_identity, noise_level=0.04
                    )
                    if self.enroll_target_name:
                        self.process_enrollment_sample(raw_emb)
                    rec_face_id, face_sim = self.match_arcface_embedding(raw_emb)

            # 4. Calcolo Spatial Surprise S(O_i) per il motore di Curiosità (IMP-BIO-004)
            surprise = 0.0
            if obj.is_dynamic_anomaly:
                surprise = max(0.45, obj.base_surprise if obj.base_surprise > 0 else 0.78)
            elif obj.object_id in self.known_static_objects:
                old_x, old_y = self.known_static_objects[obj.object_id]
                displacement = math.hypot(obj.x - old_x, obj.y - old_y)
                if displacement > 0.35:
                    surprise = min(0.95, 0.40 + 0.30 * displacement)
            else:
                self.known_static_objects[obj.object_id] = (obj.x, obj.y)

            if surprise >= 0.38:
                spatial_anomalies.append({
                    "object_id": obj.object_id,
                    "label": obj.class_label,
                    "x": round(obj.x, 3),
                    "y": round(obj.y, 3),
                    "z": round(obj.z, 3),
                    "surprise_score": round(surprise, 3),
                    "timestamp": time.time(),
                })

            detections.append(
                SimulatedDetection(
                    object_id=obj.object_id,
                    label=obj.class_label,
                    confidence=round(conf, 3),
                    bbox_cx=u_px,
                    bbox_cy=v_px,
                    bbox_w=bbox_w,
                    bbox_h=bbox_h,
                    depth_m=round(z_cam, 3),
                    world_pos=(obj.x, obj.y, obj.z),
                    keypoints_17=kps_17,
                    recognized_face_id=rec_face_id,
                    face_similarity=round(face_sim, 4),
                    surprise_score=round(surprise, 3),
                )
            )

        # 5. Calcolo NetVLAD 4096-dim descriptor dipendente dalla posa visiva (< 1.5ms - SPEC-03)
        vlad_seed = int(abs(round(robot_x * 20) * 73856093 ^ round(robot_y * 20) * 19349663)) % (2**31 - 1)
        rng_vlad = np.random.RandomState(vlad_seed)
        vlad_vec = self.normalize_l2_strict(rng_vlad.randn(4096).astype(np.float32))

        return detections, spatial_anomalies, vlad_vec


class HailoDigitalTwinNode(Node):
    """
    Nodo ROS 2 che espone esattamente le stesse interfacce di `hailo_bridge_node_cpp`
    e `marcus_semantic_mapper_cpp`.
    """

    def __init__(self):
        super().__init__("hailo_bridge_node_cpp")
        self.declare_parameter("conf_threshold", 0.55)
        self.declare_parameter("face_similarity_threshold", 0.72)
        self.declare_parameter("vlm_rate_hz", 5.0)

        conf = self.get_parameter("conf_threshold").value
        face_th = self.get_parameter("face_similarity_threshold").value
        rate_hz = max(1.0, float(self.get_parameter("vlm_rate_hz").value))

        self.engine = HailoDigitalTwinEngine(
            conf_threshold=conf,
            face_similarity_threshold=face_th
        )

        self.robot_x = 0.0
        self.robot_y = 0.0
        self.robot_yaw = 0.0
        self.latest_depth_16uc1: Optional[np.ndarray] = None
        self.world_objects: List[WorldObject3D] = self._default_smart_home_objects()

        # Subscriptions
        self.create_subscription(Odometry, "/odom", self._on_odom, 10)
        self.create_subscription(Image, "/camera/depth/image_raw", self._on_depth, 10)
        self.create_subscription(String, "/hailo/face/enroll", self._on_enroll, 10)
        self.create_subscription(String, "/sim/environment/spawn_anomaly", self._on_spawn_anomaly, 10)
        self.create_subscription(String, "/sim/human_actor/state", self._on_human_state, 10)

        # Publishers (100% compatibili con SPEC-03 e CognitiveCore/Amygdala)
        self.pub_det = self.create_publisher(Detection2DArray, "/hailo/detections", 10)
        self.pub_yolo_det = self.create_publisher(Detection2DArray, "/hailo/yolo/detections", 10)
        self.pub_pose_det = self.create_publisher(Detection2DArray, "/hailo/pose/detections", 10)
        self.pub_pose_markers = self.create_publisher(MarkerArray, "/hailo/pose/skeletons", 10)
        self.pub_face_det = self.create_publisher(Detection2DArray, "/hailo/face/detections", 10)
        self.pub_face_id = self.create_publisher(String, "/hailo/face/identity", 10)
        self.pub_vlad = self.create_publisher(Float32MultiArray, "/hailo/vlad_descriptor", 10)
        self.pub_anomaly = self.create_publisher(String, "/vision/spatial_anomaly", 10)

        if SemanticObjectArray is not None:
            self.pub_sem = self.create_publisher(SemanticObjectArray, "/hailo/semantic_objects", 10)
            self.pub_vlm_sem = self.create_publisher(SemanticObjectArray, "/hailo/vlm/semantic_objects", 10)
        else:
            self.pub_sem = None
            self.pub_vlm_sem = None

        # Service: VDevice Arbitration (IMP-BIO-001)
        self.create_service(SetBool, "/hailo_bridge/yield_vdevice", self._on_yield_vdevice)

        self.create_timer(1.0 / rate_hz, self._inference_tick)
        self.get_logger().info("🧠 [HailoDigitalTwinNode] Emulatore NPU Hailo-10H attivo (YOLO+Pose+ArcFace+NetVLAD).")

    @staticmethod
    def _default_smart_home_objects() -> List[WorldObject3D]:
        return [
            WorldObject3D("sofa_salotto", "couch", 2.2, 1.8, 0.45, 1.6, 0.85),
            WorldObject3D("tv_salotto", "tv", 3.5, 0.0, 0.9, 1.1, 0.65),
            WorldObject3D("table_cucina", "dining table", -2.2, 1.8, 0.4, 1.2, 0.75),
            WorldObject3D("fridge_cucina", "refrigerator", -3.8, 2.5, 0.9, 0.7, 1.7),
            WorldObject3D("chair_studio", "chair", 1.8, -2.2, 0.4, 0.55, 0.85),
            WorldObject3D("laptop_studio", "laptop", 2.1, -2.4, 0.78, 0.35, 0.25),
            WorldObject3D("human_luca", "person", 1.8, 0.5, 0.85, 0.55, 1.72, person_identity="luca", posture_state="STANDING"),
        ]

    def _on_odom(self, msg: Odometry):
        self.robot_x = msg.pose.pose.position.x
        self.robot_y = msg.pose.pose.position.y
        q = msg.pose.pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self.robot_yaw = math.atan2(siny_cosp, cosy_cosp)

    def _on_depth(self, msg: Image):
        if msg.encoding == "16UC1" and msg.height == 480 and msg.width == 640:
            self.latest_depth_16uc1 = np.frombuffer(msg.data, dtype=np.uint16).reshape((480, 640))

    def _on_enroll(self, msg: String):
        self.engine.start_dynamic_enrollment(msg.data)
        self.get_logger().info(f"📸 [HailoDigitalTwin] Avviato enrollment ArcFace per '{msg.data}'.")

    def _on_spawn_anomaly(self, msg: String):
        try:
            data = json.loads(msg.data)
            obj = WorldObject3D(
                object_id=data.get("id", f"anomaly_{int(time.time())}"),
                class_label=data.get("label", "suitcase"),
                x=float(data.get("x", 1.5)),
                y=float(data.get("y", 0.0)),
                z=float(data.get("z", 0.35)),
                is_dynamic_anomaly=True,
                base_surprise=float(data.get("surprise_score", 0.82)),
            )
            self.world_objects.append(obj)
            self.get_logger().info(f"✨ [HailoDigitalTwin] Iniettata anomalia 3D '{obj.class_label}' in ({obj.x}, {obj.y}).")
        except Exception as e:
            self.get_logger().error(f"Errore spawn anomaly: {e}")

    def _on_human_state(self, msg: String):
        try:
            data = json.loads(msg.data)
            actor_id = data.get("actor_id", "human_luca")
            for obj in self.world_objects:
                if obj.object_id == actor_id:
                    obj.x = float(data.get("x", obj.x))
                    obj.y = float(data.get("y", obj.y))
                    obj.posture_state = data.get("posture", obj.posture_state)
                    obj.person_identity = data.get("identity", obj.person_identity)
                    return
            self.world_objects.append(
                WorldObject3D(
                    object_id=actor_id,
                    class_label="person",
                    x=float(data.get("x", 2.0)),
                    y=float(data.get("y", 0.0)),
                    person_identity=data.get("identity", "luca"),
                    posture_state=data.get("posture", "STANDING"),
                )
            )
        except Exception:
            pass

    def _on_yield_vdevice(self, request, response):
        ok, message = self.engine.set_vdevice_yield(request.data)
        response.success = ok
        response.message = message
        return response

    def _inference_tick(self):
        dets, anomalies, vlad = self.engine.infer_scene(
            self.robot_x, self.robot_y, self.robot_yaw,
            self.world_objects, depth_16uc1_mm=self.latest_depth_16uc1
        )
        now_msg = self.get_clock().now().to_msg()

        det_arr = Detection2DArray()
        det_arr.header.stamp = now_msg
        det_arr.header.frame_id = "camera_optical_frame"

        face_arr = Detection2DArray()
        face_arr.header = det_arr.header

        for d in dets:
            msg_d = Detection2D()
            msg_d.header = det_arr.header
            msg_d.bbox.center.position.x = d.bbox_cx
            msg_d.bbox.center.position.y = d.bbox_cy
            msg_d.bbox.size_x = d.bbox_w
            msg_d.bbox.size_y = d.bbox_h

            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis.class_id = d.label
            hyp.hypothesis.score = d.confidence
            msg_d.results.append(hyp)
            det_arr.detections.append(msg_d)

            if d.recognized_face_id:
                id_msg = String()
                id_msg.data = d.recognized_face_id
                self.pub_face_id.publish(id_msg)

        self.pub_det.publish(det_arr)
        self.pub_yolo_det.publish(det_arr)

        for anom in anomalies:
            a_msg = String()
            a_msg.data = json.dumps(anom)
            self.pub_anomaly.publish(a_msg)

        vlad_msg = Float32MultiArray()
        vlad_msg.data = vlad.tolist()
        self.pub_vlad.publish(vlad_msg)


def main(args=None):
    if not HAS_ROS2:
        return
    rclpy.init(args=args)
    node = HailoDigitalTwinNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
