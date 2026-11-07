#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_hailo_digital_twin.py
==========================
Suite di validazione automatica dell'Emulatore Digital Twin della NPU Hailo-10H (40 TOPS)
su PC privo di hardware Hailo fisico, in conformità a SPEC-03 e IMP-BIO-001 / IMP-BIO-004.
"""

import numpy as np
import pytest

from robopy_controller.robot_ai.trinity.shadow_memory_sandbox import ShadowMemorySandbox
from robot_simulation.robot_simulation.hailo_digital_twin_node import (
    HailoDigitalTwinEngine,
    WorldObject3D,
    COCO_SKELETON_PAIRS,
)


def test_arcface_512d_and_netvlad_4096d_strict_l2_normalization():
    """
    Verifica il vincolo di Zona Rossa SPEC-03: tutti gli embedding biometrici ArcFace (512-dim)
    e i descrittori di luogo NetVLAD (4096-dim) devono avere norma Euclidea L2 = 1.0 ± 1e-5.
    """
    engine = HailoDigitalTwinEngine()
    emb_luca = engine.generate_deterministic_arcface_embedding("luca", noise_level=0.05)
    assert emb_luca.shape == (512,)
    assert emb_luca.dtype == np.float32
    norm_arc = float(np.linalg.norm(emb_luca))
    assert abs(norm_arc - 1.0) < 1e-5

    matched_id, sim = engine.match_arcface_embedding(emb_luca)
    assert matched_id == "luca"
    assert sim >= 0.72

    _, _, vlad_vec = engine.infer_scene(0.0, 0.0, 0.0, [])
    assert vlad_vec.shape == (4096,)
    norm_vlad = float(np.linalg.norm(vlad_vec))
    assert abs(norm_vlad - 1.0) < 1e-5


def test_3d_frustum_culling_wall_and_depth_16uc1_occlusion():
    """
    Verifica che l'emulatore Hailo-10H:
    - Rilevi gli oggetti nel cono visivo della OAK-D Lite (FOV 69°)
    - Scarti gli oggetti dietro al robot o occlusi da un muro divisorio o da un ostacolo Depth 16UC1.
    """
    engine = HailoDigitalTwinEngine()
    objs = [
        WorldObject3D("person_front", "person", x=2.0, y=0.0, person_identity="luca", posture_state="WAVING"),
        WorldObject3D("chair_behind", "chair", x=-2.0, y=0.0),
    ]

    dets, _, _ = engine.infer_scene(robot_x=0.0, robot_y=0.0, robot_yaw=0.0, world_objects=objs)
    assert len(dets) == 1
    d = dets[0]
    assert d.object_id == "person_front"
    assert d.recognized_face_id == "luca"
    assert d.keypoints_17 is not None and len(d.keypoints_17) == 17
    assert len(COCO_SKELETON_PAIRS) == 16
    assert d.keypoints_17[10][1] < d.keypoints_17[6][1]

    wall = [((1.0, -2.0), (1.0, 2.0))]
    dets_wall, _, _ = engine.infer_scene(0.0, 0.0, 0.0, objs, wall_segments=wall)
    assert len(dets_wall) == 0

    depth_occluding = np.full((480, 640), 800, dtype=np.uint16)
    dets_depth, _, _ = engine.infer_scene(0.0, 0.0, 0.0, objs, depth_16uc1_mm=depth_occluding)
    assert len(dets_depth) == 0


def test_dynamic_face_enrollment_in_shadow_sandbox():
    """
    Verifica l'enrollment dinamico su 10 frame consecutivi (SPEC-03 Zona Verde)
    isolato nella `ShadowMemorySandbox` (FM-SIM-001).
    """
    sandbox = ShadowMemorySandbox()
    paths = sandbox.activate()
    try:
        engine = HailoDigitalTwinEngine(known_faces_dir=paths["shadow_known_faces_dir"])
        engine.start_dynamic_enrollment("marco")

        raw_vec = engine.generate_deterministic_arcface_embedding("marco", noise_level=0.02)
        assert engine.process_enrollment_sample(raw_vec, iou_quality=0.40) is False

        completed = False
        for _ in range(10):
            sample = engine.generate_deterministic_arcface_embedding("marco", noise_level=0.02)
            completed = engine.process_enrollment_sample(sample, iou_quality=0.88, luma=120.0)

        assert completed is True
        matched_name, sim = engine.match_arcface_embedding(raw_vec)
        assert matched_name == "marco"
        assert sim >= 0.85
    finally:
        rep = sandbox.teardown(delete_files=True)
        assert rep.production_integrity_verified is True


def test_vdevice_arbitration_and_spatial_surprise_generation():
    """
    Verifica:
    - Servizio `/hailo_bridge/yield_vdevice` (Break-Before-Make IMP-BIO-001)
    - Generazione dell'anomalia di sorpresa spaziale `S(O_i) >= 0.38` quando appare un oggetto inatteso.
    """
    engine = HailoDigitalTwinEngine()
    anomaly_obj = WorldObject3D(
        object_id="mystery_suitcase",
        class_label="suitcase",
        x=1.6,
        y=0.1,
        is_dynamic_anomaly=True,
        base_surprise=0.84,
    )

    ok, _ = engine.set_vdevice_yield(True)
    assert ok is True
    dets_yielded, anoms_yielded, _ = engine.infer_scene(0.0, 0.0, 0.0, [anomaly_obj])
    assert len(dets_yielded) == 0 and len(anoms_yielded) == 0

    engine.set_vdevice_yield(False)
    dets, anomalies, _ = engine.infer_scene(0.0, 0.0, 0.0, [anomaly_obj])
    assert len(dets) == 1
    assert len(anomalies) == 1
    assert anomalies[0]["label"] == "suitcase"
    assert anomalies[0]["surprise_score"] >= 0.80
