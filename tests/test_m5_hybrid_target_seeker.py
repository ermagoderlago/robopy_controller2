#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_m5_hybrid_target_seeker.py — Unit Test Suite for Milestone 5
================================================================
Comprehensive verification of Hierarchical Hybrid Navigation & Target Seeking:
  - Initialization & Idle State
  - Room Name Normalization (Italian Prepositions: in, nella, all', dell') & Spatial Resolution
  - Invalid / Unknown Room Handling
  - Phase 1 (Nav2 Macro Navigation): start_mission, arrival notification
  - Phase 2 (NOMAD Reactive Search):
      * Room Boundary Containment: Free Zone, Soft Buffer, Hard Turnaround
      * Saccadic Visual Sweeps (12s / 1.5m)
      * Search Timeout (120s budget -> PHASE_FAILED / TARGET_NOT_FOUND)
  - Phase 3 (Hailo YOLO Target Sighting & Visual Servoing):
      * Confidence Gating: 0.54 rejected, 0.55 accepted, 0.78 accepted
      * Multilingual Synonym Matching (zaino/backpack, persona/person, chiavi/keys, etc.)
      * Multiple Candidate Selection (highest confidence wins)
      * Empty Detection Stream Safety
      * Handover & NOMAD Disengagement
      * Visual Servoing Step: 0.60m approaching, 0.31m continuing
      * Proximity Stopping Threshold: 0.30m, 0.28m, 0.22m stop (PHASE_COMPLETED)
      * Kinematic Velocity Clamping to SPEC-01 Limits (|v| <= 0.30 m/s, |w| <= 1.00 rad/s)
      * Chime Played & Completion Announced
  - ROS 2 Node Wrapper Logic
"""

import pytest
import math
import time
from typing import List, Dict, Any

from robopy_controller.nodes.hybrid_target_seeker import (
    HybridTargetSeekerEngine,
    HybridSearchCoordinator,
    HAS_ROS2,
)


class TestM5HybridTargetSeekerInitialization:
    """Verifies baseline state machine invariants, constants, and initial parameters."""

    def test_initial_state_and_defaults(self):
        coord = HybridTargetSeekerEngine()
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_IDLE
        assert coord.target_room is None
        assert coord.target_class is None
        assert coord.nav2_goal_reached is False
        assert coord.nomad_active is False
        assert coord.detected_target is None
        assert coord.final_distance_to_target == 999.0
        assert coord.chime_played is False
        assert coord.completion_announced is False

    def test_thresholds_and_kinematic_constants(self):
        coord = HybridTargetSeekerEngine()
        assert coord.YOLO_MIN_CONFIDENCE == 0.55
        assert coord.TARGET_PROXIMITY_METERS == 0.30
        assert coord.DEFAULT_SEARCH_TIMEOUT_SEC == 120.0
        assert coord.MAX_LINEAR_VELOCITY_VS == 0.30
        assert coord.MAX_ANGULAR_VELOCITY_VS == 1.00
        assert coord.CHASSIS_MAX_LINEAR == 0.40
        assert coord.CHASSIS_MAX_ANGULAR == 1.80

    def test_backward_compatibility_alias(self):
        """Ensures HybridSearchCoordinator is an exact alias of HybridTargetSeekerEngine."""
        assert HybridSearchCoordinator is HybridTargetSeekerEngine
        instance = HybridSearchCoordinator()
        assert isinstance(instance, HybridTargetSeekerEngine)


class TestM5RoomResolutionAndNormalization:
    """Verifies spatial room resolution, Italian preposition stripping, and geometry retrieval."""

    @pytest.mark.parametrize("raw_input,expected_norm", [
        ("in cucina", "cucina"),
        ("nella cucina", "cucina"),
        ("nel salotto", "salotto"),
        ("nello studio", "studio"),
        ("all'ingresso", "ingresso"),
        ("alla cucina", "cucina"),
        ("della camera da letto", "camera da letto"),
        ("in camera da letto", "camera da letto"),
        ("camera da letto", "camera da letto"),
        ("Salotto", "salotto"),
        ("  CUCINA  ", "cucina"),
        ("verso il salotto", "il salotto"),
    ])
    def test_normalize_room_name(self, raw_input, expected_norm):
        normalized = HybridTargetSeekerEngine.normalize_room_name(raw_input)
        assert normalized == expected_norm

    def test_resolve_default_rooms_geometry(self):
        coord = HybridTargetSeekerEngine()

        # Salotto
        salotto = coord.resolve_room("in salotto")
        assert salotto is not None
        assert salotto["centroid"] == (2.75, 2.1)
        assert len(salotto["polygon"]) == 4

        # Cucina
        cucina = coord.resolve_room("nella cucina")
        assert cucina is not None
        assert cucina["centroid"] == (7.35, 1.9)
        assert len(cucina["polygon"]) == 4

        # Camera da letto
        camera = coord.resolve_room("in camera da letto")
        assert camera is not None
        assert camera["centroid"] == (1.5, 8.0)

        # Studio
        studio = coord.resolve_room("nello studio")
        assert studio is not None
        assert studio["centroid"] == (4.25, 8.0)

    def test_invalid_room_resolution_and_handling(self):
        coord = HybridTargetSeekerEngine()
        assert coord.resolve_room("stanza_sconosciuta_123") is None
        assert coord.resolve_room("") is None
        assert coord.resolve_room(None) is None

        # Strict room checking in start_mission
        res = coord.start_mission("stanza_sconosciuta_123", "chiavi", strict_room_check=True)
        assert "Errore" in res
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_IDLE
        assert coord.failure_reason is not None


class TestM5Phase1MacroNavigation:
    """Verifies Phase 1 dispatch, verbatim prompt formatting, and Nav2 arrival notification."""

    def test_start_mission_sets_phase1_and_returns_verbatim_string(self):
        coord = HybridTargetSeekerEngine()
        msg = coord.start_mission(room_name="cucina", target_class="bottiglia")

        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_1_NAV2_MACRO
        assert coord.target_room == "cucina"
        assert coord.target_class == "bottiglia"
        assert coord.nav2_goal_reached is False
        assert coord.nomad_active is False
        assert msg == "Missione avviata: ricerca 'bottiglia' in 'cucina' via Nav2 macro-navigazione."

    def test_notify_nav2_arrival_transitions_to_phase2(self):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "persona")
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_1_NAV2_MACRO

        coord.notify_nav2_arrival()
        assert coord.nav2_goal_reached is True
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE
        assert coord.nomad_active is True
        assert coord.nomad_search_start_time > 0.0

    def test_notify_nav2_arrival_ignored_if_not_in_phase1(self):
        coord = HybridTargetSeekerEngine()
        # In IDLE
        coord.notify_nav2_arrival()
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_IDLE
        assert coord.nomad_active is False


class TestM5Phase2NomadReactiveSearch:
    """Verifies NOMAD boundary containment (3-tier), saccadic visual sweeps, and timeout."""

    def test_room_boundary_containment_free_zone(self):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()

        # Salotto: x in [0.0, 5.5], y in [0.0, 4.2]. Centroid is at (2.75, 2.1).
        # At (2.75, 2.1), distance to all walls is > 2.0m (> 0.50m Free Zone).
        vx, wz, zone = coord.filter_nomad_velocity(
            nomad_vx=0.18, nomad_wz=0.15,
            robot_x=2.75, robot_y=2.10, robot_yaw=0.0
        )
        assert zone == "FREE_ZONE"
        assert pytest.approx(vx, abs=1e-3) == 0.18
        assert pytest.approx(wz, abs=1e-3) == 0.15

    def test_room_boundary_containment_soft_buffer(self):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()

        # At (0.35, 2.10), distance to x=0.0 wall is 0.35m.
        # This falls into Soft Buffer (0.25m < d <= 0.50m).
        vx, wz, zone = coord.filter_nomad_velocity(
            nomad_vx=0.20, nomad_wz=0.0,
            robot_x=0.35, robot_y=2.10, robot_yaw=math.pi # facing towards left wall
        )
        assert zone == "SOFT_BUFFER"
        # Forward speed should be attenuated
        assert vx < 0.20
        assert vx > 0.0
        # Inward steering deflection should be injected
        assert wz != 0.0

    def test_room_boundary_containment_hard_turnaround(self):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()

        # Case A: too close to wall (d = 0.10m <= 0.25m) facing away from centroid
        vx1, wz1, zone1 = coord.filter_nomad_velocity(
            nomad_vx=0.20, nomad_wz=0.0,
            robot_x=0.10, robot_y=2.10, robot_yaw=math.pi
        )
        assert zone1 == "HARD_TURNAROUND"
        assert vx1 == 0.0  # Linear speed completely halted
        assert wz1 != 0.0  # Rotation towards centroid (2.75, 2.10)

        # Case B: outside polygon entirely facing away from centroid
        vx2, wz2, zone2 = coord.filter_nomad_velocity(
            nomad_vx=0.18, nomad_wz=0.10,
            robot_x=-0.50, robot_y=2.10, robot_yaw=math.pi
        )
        assert zone2 == "HARD_TURNAROUND"
        assert vx2 == 0.0
        assert wz2 != 0.0

        # Case C: outside polygon facing directly towards centroid (re-entry)
        vx3, wz3, zone3 = coord.filter_nomad_velocity(
            nomad_vx=0.18, nomad_wz=0.0,
            robot_x=-0.50, robot_y=2.10, robot_yaw=0.0
        )
        assert zone3 == "HARD_TURNAROUND"
        assert vx3 == 0.12  # Translates forward into room
        assert abs(wz3) <= 0.10

    def test_saccadic_visual_sweeps_triggering(self):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()

        t0 = coord.nomad_search_start_time

        # At t0 + 5s: no sweep
        res_early = coord.check_saccadic_sweep(now=t0 + 5.0)
        assert res_early is None

        # At t0 + 12.0s: sweep triggered
        res_sweep = coord.check_saccadic_sweep(now=t0 + 12.0)
        assert res_sweep is not None
        vx, wz = res_sweep
        assert vx == 0.0
        assert wz == coord.SWEEP_YAW_RATE  # 0.45 rad/s

        # Cumulative distance sweep trigger
        coord.last_sweep_time = time.monotonic()
        coord.distance_traveled_since_sweep = 0.0
        coord.check_saccadic_sweep(robot_x=0.0, robot_y=0.0, now=t0 + 13.0)
        # Move 1.6m (>= 1.5m threshold)
        res_dist = coord.check_saccadic_sweep(robot_x=1.60, robot_y=0.0, now=t0 + 14.0)
        assert res_dist is not None
        assert res_dist[1] == coord.SWEEP_YAW_RATE

    def test_nomad_search_timeout_at_120s(self):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()
        t0 = coord.nomad_search_start_time

        # At 119s: still searching
        timed_out_119 = coord.check_search_timeout(now=t0 + 119.0)
        assert timed_out_119 is False
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE
        assert coord.nomad_active is True

        # At 120s: timeout expires
        timed_out_120 = coord.check_search_timeout(now=t0 + 120.0)
        assert timed_out_120 is True
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_FAILED
        assert coord.nomad_active is False
        assert coord.failure_reason == "TARGET_NOT_FOUND"


class TestM5Phase3YoloTargetGatingAndServoing:
    """Verifies Hailo YOLO confidence gating (>=0.55), synonym matching, and visual servoing."""

    def test_confidence_gating_boundary_thresholds(self):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("studio", "chiavi")
        coord.notify_nav2_arrival()

        # 0.54 is REJECTED
        res_low = coord.process_hailo_yolo_detections([{"class": "chiavi", "confidence": 0.54, "distance_m": 1.5}])
        assert res_low is None
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE
        assert coord.nomad_active is True

        # 0.55 is ACCEPTED (exact boundary)
        res_bound = coord.process_hailo_yolo_detections([{"class": "chiavi", "confidence": 0.55, "distance_m": 1.5}])
        assert res_bound is not None
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
        assert coord.nomad_active is False
        assert "0.55" in res_bound

    def test_wrong_class_rejected_and_empty_stream_safe(self):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "zaino")
        coord.notify_nav2_arrival()

        # Empty stream
        assert coord.process_hailo_yolo_detections([]) is None
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE

        # Irrelevant high-confidence class
        res_irrelevant = coord.process_hailo_yolo_detections([{"class": "sedia", "confidence": 0.95, "distance_m": 1.0}])
        assert res_irrelevant is None
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE

    def test_multiple_candidates_selects_highest_confidence(self):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("cucina", "bottiglia")
        coord.notify_nav2_arrival()

        detections = [
            {"class": "bottiglia", "confidence": 0.60, "distance_m": 2.0},
            {"class": "bottiglia", "confidence": 0.91, "distance_m": 1.2},
            {"class": "bottiglia", "confidence": 0.75, "distance_m": 1.8},
        ]
        msg = coord.process_hailo_yolo_detections(detections)
        assert msg is not None
        assert coord.detected_target["confidence"] == 0.91
        assert "0.91" in msg

    @pytest.mark.parametrize("target,det_label", [
        ("zaino", "backpack"),
        ("persona", "person"),
        ("chiavi", "keys"),
        ("bottiglia", "bottle"),
        ("occhiali", "glasses"),
        ("sedia", "chair"),
        ("tavolo", "dining table"),
        ("divano", "couch"),
        ("gatto", "cat"),
    ])
    def test_multilingual_synonym_matching(self, target, det_label):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", target)
        coord.notify_nav2_arrival()

        res = coord.process_hailo_yolo_detections([{"class": det_label, "confidence": 0.78, "distance_m": 1.4}])
        assert res is not None
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
        assert coord.nomad_active is False

    def test_visual_servoing_step_approaching(self):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("camera da letto", "occhiali")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "occhiali", "confidence": 0.82, "distance_m": 1.5}])

        # 0.60m -> in progress
        msg1, done1 = coord.execute_visual_servoing_step(0.60)
        assert done1 is False
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
        assert "0.60m" in msg1

        # 0.31m -> still approaching (> 0.30m)
        msg2, done2 = coord.execute_visual_servoing_step(0.31)
        assert done2 is False
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING

    @pytest.mark.parametrize("stopping_dist", [0.30, 0.28, 0.22])
    def test_proximity_stopping_threshold_exact_boundaries(self, stopping_dist):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("camera da letto", "occhiali")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "occhiali", "confidence": 0.80, "distance_m": 1.0}])

        msg, done = coord.execute_visual_servoing_step(stopping_dist)
        assert done is True
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_COMPLETED
        assert coord.chime_played is True
        assert coord.completion_announced is True
        assert f"{stopping_dist:.2f}m" in msg
        assert "Motori arrestati" in msg

    def test_visual_servoing_not_active_guard(self):
        coord = HybridTargetSeekerEngine()
        # In IDLE
        msg, done = coord.execute_visual_servoing_step(0.25)
        assert done is False
        assert "non attivo" in msg

    def test_kinematic_velocity_clamping_to_spec01_limits(self):
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "zaino")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "zaino", "confidence": 0.85, "distance_m": 4.0}])

        # Even with large distance (5.0m) and extreme pixel offset (bbox at edge: 640px on 640px wide frame)
        v, w, complete = coord.compute_visual_servoing_twist(
            bbox_center_x=640.0, image_width=640.0, current_distance_m=5.0
        )
        assert complete is False
        assert abs(v) <= HybridTargetSeekerEngine.MAX_LINEAR_VELOCITY_VS  # <= 0.30 m/s
        assert abs(w) <= HybridTargetSeekerEngine.MAX_ANGULAR_VELOCITY_VS # <= 1.00 rad/s

        # Test deadband: bounding box near center (322px on 640px wide image -> |e_x| < 0.04)
        v_db, w_db, complete_db = coord.compute_visual_servoing_twist(
            bbox_center_x=322.0, image_width=640.0, current_distance_m=0.80
        )
        assert w_db == 0.0
        assert v_db > 0.0

        # At distance <= 0.30m
        v_stop, w_stop, complete_stop = coord.compute_visual_servoing_twist(
            bbox_center_x=320.0, image_width=640.0, current_distance_m=0.28
        )
        assert complete_stop is True
        assert v_stop == 0.0
        assert w_stop == 0.0
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_COMPLETED


class TestM5Ros2NodeLogic:
    """Verifies node wrapper initialization and fallback."""

    def test_ros2_node_creation_or_fallback(self):
        if not HAS_ROS2:
            pytest.skip("ROS 2 not installed in this environment.")
        from robopy_controller.nodes.hybrid_target_seeker import HybridTargetSeekerNode
        node = HybridTargetSeekerNode()
        assert node is not None
        assert node.engine.current_phase == HybridTargetSeekerEngine.PHASE_IDLE
        node.destroy_node()
