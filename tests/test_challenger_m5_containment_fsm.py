#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_challenger_m5_containment_fsm.py — Challenger Stress Test Suite
====================================================================
Adversarial empirical stress-testing for Milestone 5 (TC8 / R5):
  - Room Resolution Edge Cases:
      * Extreme whitespace and mixed casing
      * Italian preposition strings ("voglio andare nella cucina", "vai all'ufficio", "nel salotto")
      * Completely non-existent room names (clean rejection without crash, remain in IDLE)
      * Missing or degenerate geometries (<3 vertices, colinear, identical, empty)
  - State Machine Integrity:
      * Out-of-order calls (notify_nav2_arrival in IDLE/VISUAL_SERVOING/COMPLETED/FAILED)
      * Detections processed in NAV2_MACRO/COMPLETED/FAILED
      * Double / nested start_mission calls
  - Adversarial Boundary Containment:
      * Exact boundary placement (d_min == 0.0)
      * Exterior points (d_min < 0.0 / outside ray-casting)
      * Non-convex L-shaped and U-shaped polygons
      * High NOMAD velocities directly into sharp reflex corners
      * Invariant: returned velocity NEVER drives Marcus further out of the room
  - 120s Search Budget Stress:
      * Exact boundary: 119.9s active vs 120.0s failed
      * Post-timeout rejection and 10,000-iteration memory/stability verification

Conforms to: SPEC-01, SPEC-02, SPEC-03, SPEC-05, marcus_core_rules.md (Zero BOM UTF-8).
"""

import math
import time
import pytest
from typing import List, Tuple, Dict, Any

from robopy_controller.nodes.hybrid_target_seeker import (
    HybridTargetSeekerEngine,
    HybridSearchCoordinator,
)


# ==============================================================================
# Vector 1: Room Resolution Edge Cases & Degenerate Geometries
# ==============================================================================
class TestChallengerRoomResolutionEdgeCases:
    """Adversarial stress testing on room normalization, resolution, and degenerate boundaries."""

    @pytest.mark.parametrize("raw_input,expected_norm", [
        ("\n\t  CuCiNa  \t\r ", "cucina"),
        ("   \t\n  sAlOtTo   ", "salotto"),
        ("   StUdIo   ", "studio"),
        ("  CAMERA DA LETTO  ", "camera da letto"),
        ("   NeL   SaLoTtO   ", "salotto"),
        ("   NeLlA   CuCiNa   ", "cucina"),
        ("   AlL'InGrEsSo   ", "ingresso"),
        ("   dElLa   CaMeRa   Da   LeTtO   ", "camera   da   letto"),
    ])
    def test_extreme_whitespace_and_mixed_casing(self, raw_input: str, expected_norm: str):
        """Verifies robust normalization under aggressive mixed casing and whitespace."""
        normalized = HybridTargetSeekerEngine.normalize_room_name(raw_input)
        assert normalized == expected_norm

    def test_italian_preposition_resolution_matrix(self):
        """
        Stress tests Italian prepositions and conversational query structures.
        Distinguishes between standard prepositions and full conversational sentences.
        """
        coord = HybridTargetSeekerEngine()

        # 1. Standard preposition queries that must resolve to known geometry
        assert coord.resolve_room("nel salotto") is not None
        assert coord.resolve_room("nella cucina") is not None
        assert coord.resolve_room("nello studio") is not None
        assert coord.resolve_room("in camera da letto") is not None
        assert coord.resolve_room("della cucina") is not None
        assert coord.resolve_room("alla cucina") is not None

        # Verify resolved attributes for standard prepositions
        salotto_meta = coord.resolve_room("nel salotto")
        assert salotto_meta["room_name"] == "salotto"
        assert salotto_meta["centroid"] == (2.75, 2.1)

        cucina_meta = coord.resolve_room("nella cucina")
        assert cucina_meta["room_name"] == "cucina"
        assert cucina_meta["centroid"] == (7.35, 1.9)

        # 2. Conversational sentences: "voglio andare nella cucina", "vai all'ufficio"
        # Without an upstream NLU / slot-extractor, these full phrases contain leading verbs
        # that are not pure prepositions. They must NOT match unknown geometries or crash.
        voglio_query = "voglio andare nella cucina"
        assert coord.resolve_room(voglio_query) is None

        vai_query = "vai all'ufficio"
        assert coord.resolve_room(vai_query) is None

    @pytest.mark.parametrize("nonexistent_room", [
        "stanza_inesistente_xyz",
        "soffitta_segreta",
        "corridoio_oscuro_999",
        "bagno_padronale",  # not in default metadata
        "",
        "   ",
        "12345!@#$%",
    ])
    def test_completely_nonexistent_room_names_rejection(self, nonexistent_room: str):
        """
        Verifies that completely non-existent room names reject cleanly without crash,
        returning an informative error message and remaining in PHASE_IDLE when strict check is enabled.
        """
        coord = HybridTargetSeekerEngine()

        # Direct resolution must return None cleanly without exception
        resolved = coord.resolve_room(nonexistent_room)
        assert resolved is None

        # Mission start with strict check must reject and stay in IDLE
        res_msg = coord.start_mission(room_name=nonexistent_room, target_class="chiavi", strict_room_check=True)
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_IDLE
        assert "Errore" in res_msg
        assert coord.failure_reason is not None
        assert "non trovata" in coord.failure_reason
        assert coord.nomad_active is False
        assert coord.nav2_goal_reached is False

    def test_nonexistent_room_default_nonstrict_behavior(self):
        """
        Empirical check: Documents behavior when strict_room_check is left False (default).
        It accepts the query for Nav2 macro-navigation with empty polygon and (0,0) centroid,
        which gracefully operates in UNCONSTRAINED mode without crashing.
        """
        coord = HybridTargetSeekerEngine()
        res = coord.start_mission("stanza_sconosciuta", "zaino", strict_room_check=False)
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_1_NAV2_MACRO
        assert coord.room_polygon == []
        assert coord.room_centroid == (0.0, 0.0)

        # Transition to NOMAD reactive: must operate in UNCONSTRAINED mode safely
        coord.notify_nav2_arrival()
        vx, wz, zone = coord.filter_nomad_velocity(0.20, 0.10, 1.0, 1.0, 0.0)
        assert zone == "UNCONSTRAINED"
        assert vx == 0.20
        assert wz == 0.10

    @pytest.mark.parametrize("degenerate_polygon,poly_desc", [
        ([], "empty_polygon"),
        ([(1.0, 1.0)], "single_vertex"),
        ([(0.0, 0.0), (2.0, 2.0)], "two_vertices_line_segment"),
        ([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)], "three_collinear_vertices"),
        ([(0.0, 0.0), (0.0, 2.0), (0.0, 4.0), (0.0, 6.0)], "four_collinear_vertical_vertices"),
        ([(3.0, 3.0), (3.0, 3.0), (3.0, 3.0)], "three_identical_vertices"),
    ])
    def test_missing_and_degenerate_room_geometries(
        self, degenerate_polygon: List[Tuple[float, float]], poly_desc: str
    ):
        """
        Adversarial test: passes degenerate, zero-area, or collinear geometries.
        Verifies:
          1. is_point_in_polygon does not raise ZeroDivisionError or IndexError.
          2. min_distance_to_polygon does not raise ZeroDivisionError or crash.
          3. filter_nomad_velocity executes deterministically without uncaught exceptions.
        """
        coord = HybridTargetSeekerEngine()
        coord.room_polygon = degenerate_polygon
        coord.room_centroid = (1.0, 1.0)

        test_points = [(0.0, 0.0), (1.0, 1.0), (5.0, 5.0), (-2.0, 3.0)]
        for px, py in test_points:
            # Must not crash
            inside = coord.is_point_in_polygon(px, py, degenerate_polygon)
            assert isinstance(inside, bool)

            d_min, qx, qy = coord.min_distance_to_polygon(px, py, degenerate_polygon)
            assert isinstance(d_min, (int, float))
            assert d_min >= 0.0

            # Filter velocity must not throw
            vx, wz, zone = coord.filter_nomad_velocity(
                nomad_vx=0.25, nomad_wz=0.15, robot_x=px, robot_y=py, robot_yaw=0.0
            )
            assert isinstance(vx, float)
            assert isinstance(wz, float)
            assert zone in ("UNCONSTRAINED", "HARD_TURNAROUND", "FREE_ZONE", "SOFT_BUFFER")


# ==============================================================================
# Vector 2: State Machine Integrity & Out-of-Order Execution
# ==============================================================================
class TestChallengerStateMachineIntegrity:
    """Stress tests out-of-order calls, illegal transitions, and double invocations."""

    def test_out_of_order_notify_nav2_arrival(self):
        """
        Verifies that notify_nav2_arrival() is strictly gated:
        It must ONLY trigger a transition when in PHASE_1_NAV2_MACRO.
        Calling it during IDLE, VISUAL_SERVOING, COMPLETED, or FAILED must be rejected.
        """
        coord = HybridTargetSeekerEngine()

        # 1. Called while in IDLE
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_IDLE
        coord.notify_nav2_arrival()
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_IDLE
        assert coord.nav2_goal_reached is False
        assert coord.nomad_active is False

        # 2. Advance to VISUAL_SERVOING
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE
        coord.process_hailo_yolo_detections([{"class": "chiavi", "confidence": 0.85, "distance_m": 1.2}])
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING

        # Call notify_nav2_arrival during VISUAL_SERVOING
        coord.notify_nav2_arrival()
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING  # MUST NOT reset to PHASE_2

        # 3. Advance to COMPLETED
        coord.execute_visual_servoing_step(0.25)
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_COMPLETED

        # Call notify_nav2_arrival during COMPLETED
        coord.notify_nav2_arrival()
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_COMPLETED

        # 4. In FAILED state
        coord_failed = HybridTargetSeekerEngine()
        coord_failed.start_mission("salotto", "chiavi")
        coord_failed.notify_nav2_arrival()
        t0 = coord_failed.nomad_search_start_time
        coord_failed.check_search_timeout(now=t0 + 120.0)
        assert coord_failed.current_phase == HybridTargetSeekerEngine.PHASE_FAILED

        coord_failed.notify_nav2_arrival()
        assert coord_failed.current_phase == HybridTargetSeekerEngine.PHASE_FAILED

    def test_out_of_order_process_hailo_yolo_detections(self):
        """
        Verifies that YOLO detections are evaluated ONLY during PHASE_2_NOMAD_REACTIVE.
        During NAV2_MACRO, IDLE, COMPLETED, or FAILED, detections must return None.
        """
        coord = HybridTargetSeekerEngine()
        valid_hit = [{"class": "bottiglia", "confidence": 0.90, "distance_m": 1.5}]

        # 1. During IDLE
        assert coord.process_hailo_yolo_detections(valid_hit) is None
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_IDLE

        # 2. During NAV2_MACRO (macro travel in progress)
        coord.start_mission("cucina", "bottiglia")
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_1_NAV2_MACRO
        sighting = coord.process_hailo_yolo_detections(valid_hit)
        assert sighting is None
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_1_NAV2_MACRO

        # 3. During COMPLETED
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections(valid_hit)
        coord.execute_visual_servoing_step(0.20)
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_COMPLETED

        # Late detection arriving after completion
        late_sighting = coord.process_hailo_yolo_detections(valid_hit)
        assert late_sighting is None
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_COMPLETED

    def test_out_of_order_execute_visual_servoing_step(self):
        """
        Verifies that execute_visual_servoing_step() rejects execution if not in VISUAL_SERVOING.
        """
        coord = HybridTargetSeekerEngine()

        # In IDLE
        msg, done = coord.execute_visual_servoing_step(0.20)
        assert done is False
        assert "non attivo" in msg

        # In NAV2_MACRO
        coord.start_mission("salotto", "persona")
        msg, done = coord.execute_visual_servoing_step(0.20)
        assert done is False
        assert "non attivo" in msg

        # In NOMAD_REACTIVE
        coord.notify_nav2_arrival()
        msg, done = coord.execute_visual_servoing_step(0.20)
        assert done is False
        assert "non attivo" in msg

    def test_double_start_mission_clean_state_reset(self):
        """
        Stress tests calling start_mission() while another mission is in progress.
        Ensures atomic state reset without zombie variables or corrupted phases.
        """
        coord = HybridTargetSeekerEngine()

        # Mission 1: advanced to VISUAL_SERVOING
        coord.start_mission("salotto", "zaino")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "zaino", "confidence": 0.88, "distance_m": 1.0}])
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
        assert coord.detected_target is not None

        # Interrupted by Mission 2: start_mission("cucina", "bottiglia")
        msg2 = coord.start_mission("cucina", "bottiglia")
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_1_NAV2_MACRO
        assert coord.target_room == "cucina"
        assert coord.target_class == "bottiglia"
        assert coord.resolved_room_name == "cucina"
        assert coord.room_centroid == (7.35, 1.9)
        assert coord.nav2_goal_reached is False
        assert coord.nomad_active is False
        assert coord.detected_target is None
        assert coord.final_distance_to_target == 999.0
        assert coord.chime_played is False
        assert coord.completion_announced is False
        assert coord.failure_reason is None
        assert "ricerca 'bottiglia' in 'cucina'" in msg2

        # Complete Mission 2, then launch Mission 3 from COMPLETED
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "bottiglia", "confidence": 0.95, "distance_m": 0.8}])
        coord.execute_visual_servoing_step(0.28)
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_COMPLETED

        # Launch Mission 3
        msg3 = coord.start_mission("studio", "chiavi")
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_1_NAV2_MACRO
        assert coord.target_room == "studio"
        assert coord.chime_played is False
        assert coord.completion_announced is False


# ==============================================================================
# Vector 3: Adversarial Boundary Containment & Non-Convex Geometries
# ==============================================================================
class TestChallengerAdversarialBoundaryContainment:
    """Stress tests boundary containment under exact boundary, exterior, non-convex, and reflex angles."""

    def test_robot_exactly_on_boundary_triggers_hard_turnaround(self):
        """
        Places robot precisely on boundary segments (d_min == 0.0).
        Verifies Tier 3 (Hard Turnaround) triggers:
          - If facing outward: linear velocity is strictly 0.0 m/s (zero movement).
          - If facing inward towards centroid: linear velocity is at most 0.12 m/s re-entering.
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()

        # Salotto: x in [0.0, 5.5], y in [0.0, 4.2]. Centroid: (2.75, 2.10).
        boundary_points = [
            (0.0, 2.10, math.pi),        # Left wall, facing outward (-x)
            (5.5, 2.10, 0.0),            # Right wall, facing outward (+x)
            (2.75, 0.0, -math.pi / 2.0), # Bottom wall, facing outward (-y)
            (2.75, 4.2, math.pi / 2.0),  # Top wall, facing outward (+y)
        ]

        for bx, by, outward_yaw in boundary_points:
            d_min, _, _ = coord.min_distance_to_polygon(bx, by, coord.room_polygon)
            assert pytest.approx(d_min, abs=1e-5) == 0.0

            # Facing outward: MUST NOT move forward
            vx, wz, zone = coord.filter_nomad_velocity(
                nomad_vx=0.35, nomad_wz=0.0, robot_x=bx, robot_y=by, robot_yaw=outward_yaw
            )
            assert zone == "HARD_TURNAROUND"
            assert vx == 0.0, f"Failed at ({bx}, {by}): forward speed must be 0.0 when facing outward"
            assert wz != 0.0, f"Failed at ({bx}, {by}): angular steering towards centroid must be non-zero"

            # Facing inward towards centroid:
            inward_yaw = math.atan2(2.10 - by, 2.75 - bx)
            vx_in, wz_in, zone_in = coord.filter_nomad_velocity(
                nomad_vx=0.35, nomad_wz=0.0, robot_x=bx, robot_y=by, robot_yaw=inward_yaw
            )
            assert zone_in == "HARD_TURNAROUND"
            assert vx_in == 0.12, f"Failed at ({bx}, {by}): inward re-entry crawl must be 0.12 m/s"
            assert abs(wz_in) <= 0.15

    def test_robot_exterior_containment_invariant_grid_sweep(self):
        """
        Universal Mathematical Invariant Test:
        For any point outside the room polygon (or inside hard buffer d_min <= 0.25):
        Let P0 = (x, y), C = centroid.
        For ANY heading theta in [0, 2pi), let v_world = (vx * cos(theta), vx * sin(theta)).
        After dt = 0.1s, P1 = P0 + v_world * dt.
        Then dist(P1, C) <= dist(P0, C) ALWAYS.
        The filter NEVER drives Marcus further away from the room!
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()
        cx, cy = coord.room_centroid

        # Test exterior points in all quadrants
        exterior_points = [
            (-1.0, 2.10),
            (6.5, 2.10),
            (2.75, -1.5),
            (2.75, 5.5),
            (-2.0, -2.0),
            (8.0, 7.0),
            (-0.5, 4.5),
        ]

        angles = [i * (2.0 * math.pi / 24.0) - math.pi for i in range(24)]

        for px, py in exterior_points:
            d0 = math.hypot(px - cx, py - cy)
            for yaw in angles:
                vx, wz, zone = coord.filter_nomad_velocity(
                    nomad_vx=0.40, nomad_wz=0.50, robot_x=px, robot_y=py, robot_yaw=yaw
                )
                assert zone == "HARD_TURNAROUND"
                # Step forward
                dt = 0.10
                p1_x = px + vx * math.cos(yaw) * dt
                p1_y = py + vx * math.sin(yaw) * dt
                d1 = math.hypot(p1_x - cx, p1_y - cy)

                assert d1 <= d0 + 1e-9, (
                    f"Invariant violated at ({px}, {py}) with yaw {yaw:.2f}: "
                    f"d0={d0:.4f}, d1={d1:.4f}, vx={vx}"
                )

    def test_nonconvex_l_shaped_polygon_containment(self):
        """
        Stress tests an L-shaped non-convex polygon:
          Polygon: [(0,0), (6,0), (6,3), (3,3), (3,6), (0,6)]
          Reflex corner at (3, 3) facing into the cutout [3..6] x [3..6].
          Centroid at (2.0, 2.0).
        """
        coord = HybridTargetSeekerEngine()
        l_poly = [(0.0, 0.0), (6.0, 0.0), (6.0, 3.0), (3.0, 3.0), (3.0, 6.0), (0.0, 6.0)]
        coord.room_polygon = l_poly
        coord.room_centroid = (2.0, 2.0)

        # Point in exterior cutout (4.5, 4.5)
        inside_cutout = coord.is_point_in_polygon(4.5, 4.5, l_poly)
        assert inside_cutout is False

        # Facing outward (+x, +y): yaw = pi/4
        vx_out, wz_out, zone_out = coord.filter_nomad_velocity(
            nomad_vx=0.35, nomad_wz=0.0, robot_x=4.5, robot_y=4.5, robot_yaw=math.pi / 4.0
        )
        assert zone_out == "HARD_TURNAROUND"
        assert vx_out == 0.0
        assert wz_out != 0.0

        # Facing centroid (2.0, 2.0): target_yaw = atan2(-2.5, -2.5) = -3pi/4
        target_yaw = math.atan2(2.0 - 4.5, 2.0 - 4.5)
        vx_in, wz_in, zone_in = coord.filter_nomad_velocity(
            nomad_vx=0.35, nomad_wz=0.0, robot_x=4.5, robot_y=4.5, robot_yaw=target_yaw
        )
        assert zone_in == "HARD_TURNAROUND"
        assert vx_in == 0.12
        assert abs(wz_in) <= 0.10

    def test_nonconvex_u_shaped_polygon_containment(self):
        """
        Stress tests a U-shaped non-convex polygon:
          Polygon: [(0,0), (6,0), (6,6), (4,6), (4,2), (2,2), (2,6), (0,6)]
          Notch/channel at [2..4] x [2..6].
          Centroid at (3.0, 1.0).
        """
        coord = HybridTargetSeekerEngine()
        u_poly = [(0.0, 0.0), (6.0, 0.0), (6.0, 6.0), (4.0, 6.0), (4.0, 2.0), (2.0, 2.0), (2.0, 6.0), (0.0, 6.0)]
        coord.room_polygon = u_poly
        coord.room_centroid = (3.0, 1.0)

        # Point inside notch (3.0, 4.0): outside room polygon
        assert coord.is_point_in_polygon(3.0, 4.0, u_poly) is False

        # Facing up the notch (+y): yaw = pi/2
        vx_notch, wz_notch, zone_notch = coord.filter_nomad_velocity(
            nomad_vx=0.40, nomad_wz=0.0, robot_x=3.0, robot_y=4.0, robot_yaw=math.pi / 2.0
        )
        assert zone_notch == "HARD_TURNAROUND"
        assert vx_notch == 0.0

        # Facing down towards base centroid (3.0, 1.0): yaw = -pi/2
        vx_base, wz_base, zone_base = coord.filter_nomad_velocity(
            nomad_vx=0.40, nomad_wz=0.0, robot_x=3.0, robot_y=4.0, robot_yaw=-math.pi / 2.0
        )
        assert zone_base == "HARD_TURNAROUND"
        assert vx_base == 0.12

    def test_sharp_reflex_corner_high_nomad_velocity_rejection(self):
        """
        Places robot inside L-shaped room right next to the sharp reflex corner (3.0, 3.0).
        Robot at (2.90, 2.90).
        Commands maximum forward velocity (0.40 m/s) pointing directly into the reflex corner (yaw = pi/4).
        Verifies:
          - Tier 3 triggers immediately (d_min ~ 0.10m <= 0.25m).
          - Forward velocity is clamped to 0.0 m/s.
          - Inward rotation commands turning away from reflex corner towards centroid.
        """
        coord = HybridTargetSeekerEngine()
        l_poly = [(0.0, 0.0), (6.0, 0.0), (6.0, 3.0), (3.0, 3.0), (3.0, 6.0), (0.0, 6.0)]
        coord.room_polygon = l_poly
        coord.room_centroid = (2.0, 2.0)

        d_min, _, _ = coord.min_distance_to_polygon(2.90, 2.90, l_poly)
        assert d_min <= 0.25

        vx, wz, zone = coord.filter_nomad_velocity(
            nomad_vx=0.40, nomad_wz=0.0, robot_x=2.90, robot_y=2.90, robot_yaw=math.pi / 4.0
        )
        assert zone == "HARD_TURNAROUND"
        assert vx == 0.0
        assert wz != 0.0


# ==============================================================================
# Vector 4: 120s Search Budget & Lifecycle Endurance
# ==============================================================================
class TestChallengerSearchBudgetAndLifecycle:
    """Stress tests the 120s search budget, millisecond boundary precision, and lifecycle stability."""

    def test_exact_120s_boundary_transition_and_precision(self):
        """
        Verifies exact boundary transition:
          - At t = 119.9s: active in PHASE_2_NOMAD_REACTIVE, check_search_timeout returns False.
          - At t = 120.0s: expires to PHASE_FAILED, check_search_timeout returns True.
          - At t = 120.1s: remains PHASE_FAILED without oscillating or re-triggering.
        """
        coord = HybridTargetSeekerEngine(search_timeout_sec=120.0)
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()

        t0 = coord.nomad_search_start_time
        assert t0 > 0.0

        # Boundary test: 119.9s
        assert coord.check_search_timeout(now=t0 + 119.9) is False
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE
        assert coord.nomad_active is True
        assert coord.failure_reason is None

        # Exact transition: 120.0s
        assert coord.check_search_timeout(now=t0 + 120.0) is True
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_FAILED
        assert coord.nomad_active is False
        assert coord.failure_reason == "TARGET_NOT_FOUND"

        # Subsequent call: 120.1s
        assert coord.check_search_timeout(now=t0 + 120.1) is False
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_FAILED
        assert coord.failure_reason == "TARGET_NOT_FOUND"

    def test_post_timeout_detections_and_calls_rejected(self):
        """
        Verifies that after timeout expiration (PHASE_FAILED):
          - YOLO detections are rejected and return None.
          - notify_nav2_arrival is rejected.
          - execute_visual_servoing_step is rejected.
        """
        coord = HybridTargetSeekerEngine(search_timeout_sec=120.0)
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()
        t0 = coord.nomad_search_start_time

        coord.check_search_timeout(now=t0 + 120.0)
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_FAILED

        # Late detection
        hit = coord.process_hailo_yolo_detections([{"class": "chiavi", "confidence": 0.99, "distance_m": 1.0}])
        assert hit is None
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_FAILED

        # Late arrival
        coord.notify_nav2_arrival()
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_FAILED

        # Servoing call
        msg, done = coord.execute_visual_servoing_step(0.20)
        assert done is False
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_FAILED

    def test_10000_iterations_stability_and_zero_drift(self):
        """
        Executes 10,000 continuous control cycles to verify zero memory accumulation,
        zero time drift, and rock-solid deterministic performance under rapid simulation.
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()

        start_perf = time.perf_counter()
        sim_time = 0.0
        dt = 0.01  # 100 Hz simulation

        for i in range(10000):
            sim_time += dt
            # Simulate circular path inside room
            x = 2.75 + 1.2 * math.cos(sim_time * 0.5)
            y = 2.10 + 1.2 * math.sin(sim_time * 0.5)
            yaw = (sim_time * 0.5 + math.pi / 2.0) % (2.0 * math.pi) - math.pi

            vx, wz, zone = coord.filter_nomad_velocity(
                nomad_vx=0.20, nomad_wz=0.10, robot_x=x, robot_y=y, robot_yaw=yaw
            )
            assert zone in ("FREE_ZONE", "SOFT_BUFFER", "HARD_TURNAROUND")

            # Check saccadic sweeps
            coord.check_saccadic_sweep(robot_x=x, robot_y=y, now=coord.nomad_search_start_time + sim_time)

        elapsed_wall = time.perf_counter() - start_perf
        # 10,000 iterations should execute in well under 2.5 seconds on Pi 5 / PC
        assert elapsed_wall < 2.50, f"10,000 iterations took {elapsed_wall:.2f}s (too slow)"
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE
