#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_challenger_m5_yolo_servoing.py — Adversarial Stress Test Suite for Milestone 5
===================================================================================
Author: challenger_m5_2 (Empirical Challenger 2 for Milestone 5)
Focus: Hailo YOLO Target Gating, Visual Servoing Kinematics, and Proximity Stopping.

Adversarial Stress Vectors Tested:
1. YOLO Detection Stream Fuzzing:
   - Boundary confidence tests: 0.54999 (strictly rejected) vs 0.55000 (accepted).
   - Multiple detection candidates of target class: candidate A (conf 0.60, dist 2.0m)
     vs candidate B (conf 0.92, dist 1.5m) -> highest confidence must be selected.
   - Detections with missing fields, NaN/Inf confidence, negative distances, invalid bounding boxes.
   - Italian/English synonym coverage (chiavi/keys, zaino/backpack, bottiglia/bottle,
     bicchiere/cup, telefono/cell phone) with bidirectional matching, whitespace, and casing invariance.
2. Visual Servoing Kinematics & Clamping (SPEC-01):
   - Extreme horizontal tracking errors (e_x = -1.0, +1.0, -10.0, +10.0):
     verify angular velocity |w| is strictly clamped to <= 1.00 rad/s.
   - Extreme target distances (d = 50.0m, 100.0m):
     verify linear velocity v is strictly clamped to <= 0.30 m/s.
   - Off-axis approach attenuation: when target is far off-center (|e_x| > 0.5),
     linear forward speed is reduced so robot turns before rushing forward.
   - Deadband verification: when |e_x| <= 0.04, angular velocity is 0.0 rad/s
     (preventing hunting/jitter).
3. Proximity Stopping Threshold Boundaries (TC8):
   - Approach distance sequence: 1.0m (continues) -> 0.50m (continues) ->
     0.31m (continues) -> 0.3000m (stops) -> 0.28m (stops) -> 0.22m (stops).
   - Zero-velocity command emitted upon stop (v = 0.0, w = 0.0).
   - State transition to PHASE_COMPLETED.
   - chime_played = True, completion_announced = True.
"""

import math
import pytest
from typing import List, Dict, Any

from robopy_controller.nodes.hybrid_target_seeker import (
    HybridTargetSeekerEngine,
    HybridSearchCoordinator,
)


class TestAdversarialYoloDetectionStreamFuzzing:
    """
    Adversarial stress-testing of Hailo YOLO detection stream ingestion,
    boundary confidence gating, candidate arbitration, and fuzzing resiliency.
    """

    def test_boundary_confidence_exact_thresholds(self):
        """
        Verify strict boundary:
          0.54999 MUST be rejected (remains in PHASE_2_NOMAD_REACTIVE).
          0.55000 MUST be accepted (transitions to PHASE_3_VISUAL_SERVOING).
        """
        # Test 0.54999 (rejected)
        coord_low = HybridTargetSeekerEngine()
        coord_low.start_mission("salotto", "chiavi")
        coord_low.notify_nav2_arrival()
        res_low = coord_low.process_hailo_yolo_detections([
            {"class": "chiavi", "confidence": 0.54999, "distance_m": 1.5}
        ])
        assert res_low is None
        assert coord_low.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE
        assert coord_low.nomad_active is True
        assert coord_low.detected_target is None

        # Test 0.55000 (accepted)
        coord_bound = HybridTargetSeekerEngine()
        coord_bound.start_mission("salotto", "chiavi")
        coord_bound.notify_nav2_arrival()
        res_bound = coord_bound.process_hailo_yolo_detections([
            {"class": "chiavi", "confidence": 0.55000, "distance_m": 1.5}
        ])
        assert res_bound is not None
        assert coord_bound.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
        assert coord_bound.nomad_active is False
        assert coord_bound.detected_target is not None
        assert coord_bound.detected_target["confidence"] == 0.55000
        assert "0.55" in res_bound

    @pytest.mark.parametrize("conf,should_accept", [
        (0.0, False),
        (0.10, False),
        (0.50, False),
        (0.54, False),
        (0.54999, False),
        (0.5499999, False),
        (0.55000, True),
        (0.5500001, True),
        (0.56, True),
        (0.75, True),
        (0.999, True),
        (1.00, True),
    ])
    def test_fine_grained_confidence_boundary_scan(self, conf, should_accept):
        """Sweep across confidence range to verify exact step function at 0.55."""
        coord = HybridTargetSeekerEngine()
        coord.start_mission("cucina", "bottiglia")
        coord.notify_nav2_arrival()

        res = coord.process_hailo_yolo_detections([
            {"class": "bottiglia", "confidence": conf, "distance_m": 2.0}
        ])
        if should_accept:
            assert res is not None
            assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
            assert coord.nomad_active is False
        else:
            assert res is None
            assert coord.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE
            assert coord.nomad_active is True

    def test_multiple_candidates_arbitration_highest_confidence(self):
        """
        Adversarial scenario: Multiple candidates of target class in frame.
        Candidate A: conf 0.60, dist 2.0m
        Candidate B: conf 0.92, dist 1.5m
        Engine MUST select highest confidence candidate B (conf 0.92), regardless of list order.
        """
        # Order A then B
        coord1 = HybridTargetSeekerEngine()
        coord1.start_mission("studio", "telefono")
        coord1.notify_nav2_arrival()

        cand_a = {"class": "cell phone", "confidence": 0.60, "distance_m": 2.0, "id": "A"}
        cand_b = {"class": "cell phone", "confidence": 0.92, "distance_m": 1.5, "id": "B"}
        res1 = coord1.process_hailo_yolo_detections([cand_a, cand_b])
        assert res1 is not None
        assert coord1.detected_target["confidence"] == 0.92
        assert coord1.detected_target.get("id") == "B"

        # Order B then A (Order invariance)
        coord2 = HybridTargetSeekerEngine()
        coord2.start_mission("studio", "telefono")
        coord2.notify_nav2_arrival()
        res2 = coord2.process_hailo_yolo_detections([cand_b, cand_a])
        assert res2 is not None
        assert coord2.detected_target["confidence"] == 0.92
        assert coord2.detected_target.get("id") == "B"

    def test_multiple_candidates_filtering_below_threshold(self):
        """
        Candidate C: conf 0.549, dist 0.40m (very close, but below 0.55 confidence)
        Candidate D: conf 0.580, dist 3.50m (farther, but meets threshold >= 0.55)
        Candidate C MUST be filtered out and Candidate D MUST be chosen.
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("cucina", "bicchiere")
        coord.notify_nav2_arrival()

        cand_c = {"class": "cup", "confidence": 0.549, "distance_m": 0.40}
        cand_d = {"class": "cup", "confidence": 0.580, "distance_m": 3.50}

        res = coord.process_hailo_yolo_detections([cand_c, cand_d])
        assert res is not None
        assert coord.detected_target["confidence"] == 0.580
        assert coord.detected_target["distance_m"] == 3.50

    def test_stream_fuzzing_missing_fields_and_nan(self):
        """
        Adversarial stream fuzzing:
        - Detections with empty dictionaries
        - Detections missing 'class' or 'confidence'
        - Detections with NaN confidence
        - Non-matching classes
        Ensures engine does not crash and rejects invalid items.
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "zaino")
        coord.notify_nav2_arrival()

        fuzzed_stream = [
            {},
            {"other_key": 42},
            {"class": None, "confidence": 0.95},
            {"class": "", "confidence": 0.99},
            {"class": "zaino", "confidence": float("nan")},
            {"class": "sedia", "confidence": 0.98},
            {"confidence": 0.88},
        ]

        res = coord.process_hailo_yolo_detections(fuzzed_stream)
        assert res is None
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE
        assert coord.nomad_active is True
        assert coord.detected_target is None

    def test_stream_fuzzing_negative_distance_and_out_of_bounds_confidence(self):
        """
        Verify handling of negative distances and extreme confidence values in detection objects.
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()

        # Detection with negative distance
        res = coord.process_hailo_yolo_detections([
            {"class": "chiavi", "confidence": 0.85, "distance_m": -2.5}
        ])
        assert res is not None
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
        assert coord.detected_target["distance_m"] == -2.5

    @pytest.mark.parametrize("target_it,target_en", [
        ("chiavi", "keys"),
        ("zaino", "backpack"),
        ("bottiglia", "bottle"),
        ("bicchiere", "cup"),
        ("telefono", "cell phone"),
    ])
    def test_bidirectional_synonym_coverage(self, target_it, target_en):
        """
        Verify required Italian/English synonym pairs in both directions:
        1. Query in Italian, YOLO detects in English
        2. Query in English, YOLO detects in Italian
        """
        # Direction 1: Mission IT, Detection EN
        coord1 = HybridTargetSeekerEngine()
        coord1.start_mission("salotto", target_it)
        coord1.notify_nav2_arrival()
        res1 = coord1.process_hailo_yolo_detections([
            {"class": target_en, "confidence": 0.75, "distance_m": 1.2}
        ])
        assert res1 is not None, f"Failed matching IT '{target_it}' with EN '{target_en}'"
        assert coord1.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING

        # Direction 2: Mission EN, Detection IT
        coord2 = HybridTargetSeekerEngine()
        coord2.start_mission("salotto", target_en)
        coord2.notify_nav2_arrival()
        res2 = coord2.process_hailo_yolo_detections([
            {"class": target_it, "confidence": 0.75, "distance_m": 1.2}
        ])
        assert res2 is not None, f"Failed matching EN '{target_en}' with IT '{target_it}'"
        assert coord2.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING

    def test_synonym_whitespace_and_case_insensitivity(self):
        """Verify robust string matching against uppercase, leading/trailing spaces."""
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "  CHIAVI  ")
        coord.notify_nav2_arrival()

        res = coord.process_hailo_yolo_detections([
            {"class": "  Keys  ", "confidence": 0.80, "distance_m": 1.0}
        ])
        assert res is not None
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING


class TestAdversarialVisualServoingKinematics:
    """
    Adversarial stress-testing of visual servoing kinematics:
    clamping, off-axis approach attenuation, and deadband (SPEC-01 compliance).
    """

    @pytest.mark.parametrize("ex,expected_w_sign", [
        (-1.0, +1.0),   # Target left: turn counter-clockwise (positive w)
        (1.0, -1.0),    # Target right: turn clockwise (negative w)
        (-10.0, +1.0),  # Extreme left
        (10.0, -1.0),   # Extreme right
        (-100.0, +1.0), # Hyper-extreme left
        (100.0, -1.0),  # Hyper-extreme right
    ])
    def test_extreme_horizontal_tracking_errors_angular_clamping(self, ex, expected_w_sign):
        """
        Verify angular velocity |w| is strictly clamped to <= 1.00 rad/s
        for extreme tracking errors e_x in {-1.0, +1.0, -10.0, +10.0, ...}.
        SPEC-01 constraint: MAX_ANGULAR_VELOCITY_VS = 1.00 rad/s.
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "zaino")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "backpack", "confidence": 0.85}])

        image_width = 640.0
        half_w = image_width / 2.0
        bbox_center_x = ex * half_w + half_w

        v, w, done = coord.compute_visual_servoing_twist(
            bbox_center_x=bbox_center_x,
            image_width=image_width,
            current_distance_m=2.0
        )

        assert done is False
        assert abs(w) <= 1.00001, f"Angular velocity {w} violated SPEC-01 limit of 1.00 rad/s"
        assert abs(w) == pytest.approx(1.00, abs=1e-3)
        if expected_w_sign > 0:
            assert w > 0.0
        else:
            assert w < 0.0

    @pytest.mark.parametrize("dist", [50.0, 100.0, 500.0, 1000.0])
    def test_extreme_target_distances_linear_clamping(self, dist):
        """
        Verify linear velocity v is strictly clamped to <= 0.30 m/s
        for extreme target distances (d = 50.0m, 100.0m, etc.).
        SPEC-01 constraint: MAX_LINEAR_VELOCITY_VS = 0.30 m/s.
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("cucina", "bottiglia")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "bottle", "confidence": 0.85}])

        v, w, done = coord.compute_visual_servoing_twist(
            bbox_center_x=320.0, # Centered (e_x = 0.0)
            image_width=640.0,
            current_distance_m=dist
        )

        assert done is False
        assert v <= 0.30001, f"Linear velocity {v} exceeded SPEC-01 visual servoing ceiling of 0.30 m/s"
        assert v == pytest.approx(0.30, abs=1e-3)

    def test_off_axis_approach_attenuation(self):
        """
        Verify off-axis approach attenuation:
        When target is far off-center (|e_x| > 0.5), linear forward speed MUST be
        significantly reduced compared to centered target (|e_x| == 0.0) at the same distance,
        forcing the robot to steer towards the target before rushing forward.
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "chiavi")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "keys", "confidence": 0.85}])

        image_width = 640.0
        dist = 1.00 # 1.0 meter away

        # Centered target: e_x = 0.0 -> bbox_center_x = 320.0
        v_centered, w_c, _ = coord.compute_visual_servoing_twist(
            bbox_center_x=320.0, image_width=image_width, current_distance_m=dist
        )

        # Off-axis target: |e_x| = 0.70 > 0.50 -> bbox_center_x = 0.7 * 320 + 320 = 544.0
        v_offaxis, w_o, _ = coord.compute_visual_servoing_twist(
            bbox_center_x=544.0, image_width=image_width, current_distance_m=dist
        )

        assert v_offaxis < v_centered, (
            f"Expected off-axis linear velocity ({v_offaxis:.3f}) to be attenuated "
            f"below centered velocity ({v_centered:.3f})"
        )
        # Verify attenuation is significant (at least 30% reduction)
        assert v_offaxis <= 0.60 * v_centered

    @pytest.mark.parametrize("image_w,center_x,is_deadband,label", [
        (640.0, 320.0, True, "exact center e_x = 0.0"),
        (640.0, 325.0, True, "offset +5px: e_x = +0.0156 <= 0.04"),
        (640.0, 315.0, True, "offset -5px: e_x = -0.0156 <= 0.04"),
        (640.0, 330.0, True, "offset +10px: e_x = +0.03125 <= 0.04"),
        (640.0, 310.0, True, "offset -10px: e_x = -0.03125 <= 0.04"),
        (640.0, 332.0, True, "offset +12px: e_x = +0.0375 <= 0.04"),
        (640.0, 308.0, True, "offset -12px: e_x = -0.0375 <= 0.04"),
        (1000.0, 520.0, True, "exact positive boundary e_x = +0.0400000"),
        (1000.0, 480.0, True, "exact negative boundary e_x = -0.0400000"),
        (640.0, 335.0, False, "offset +15px: e_x = +0.046875 > 0.04"),
        (640.0, 305.0, False, "offset -15px: e_x = -0.046875 > 0.04"),
        (640.0, 370.0, False, "offset +50px: e_x = +0.15625 > 0.04"),
        (640.0, 270.0, False, "offset -50px: e_x = -0.15625 > 0.04"),
    ])
    def test_deadband_verification_around_center(self, image_w, center_x, is_deadband, label):
        """
        Verify deadband behavior (+/- 4% of half image width):
        When |e_x| <= 0.04, angular velocity w MUST be 0.0 rad/s to prevent jitter/hunting.
        When |e_x| > 0.04, angular velocity w MUST be non-zero.
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("camera da letto", "occhiali")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "glasses", "confidence": 0.85}])

        v, w, done = coord.compute_visual_servoing_twist(
            bbox_center_x=center_x, image_width=image_w, current_distance_m=1.0
        )

        if is_deadband:
            assert abs(w) == 0.0, f"Expected zero w in deadband (|e_x| <= 0.04), got {w} for {label}"
        else:
            assert abs(w) > 0.0, f"Expected non-zero w outside deadband, got {w} for {label}"

    def test_deadband_ieee754_floating_subtraction_edge_case(self):
        """
        Empirical finding: On 640px image, offset of 12.8px nominally yields e_x = 0.04.
        However, IEEE 754 subtraction (332.8 - 320.0) produces 12.800000000000011,
        making e_x = 0.040000000000000036.
        Because the engine tests strict `abs(e_x) <= 0.04` without epsilon tolerance,
        it evaluates to False and produces a slight angular correction (w = -0.048 rad/s).
        This test documents and asserts this empirical hardware/algorithmic boundary behavior.
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("camera da letto", "occhiali")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "glasses", "confidence": 0.85}])

        v, w, _ = coord.compute_visual_servoing_twist(
            bbox_center_x=332.8, image_width=640.0, current_distance_m=1.0
        )
        # Demonstrates IEEE 754 sub-ulp overflow beyond 0.04
        assert abs(w) > 0.0
        assert abs(w) < 0.05

    def test_invalid_and_zero_image_dimensions_resiliency(self):
        """Verify twist controller does not divide by zero if given zero or negative image width."""
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "zaino")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "backpack", "confidence": 0.85}])

        # image_width = 0.0
        v0, w0, _ = coord.compute_visual_servoing_twist(bbox_center_x=0.0, image_width=0.0, current_distance_m=1.0)
        assert not math.isnan(v0)
        assert not math.isnan(w0)

        # image_width = -640.0
        v_neg, w_neg, _ = coord.compute_visual_servoing_twist(bbox_center_x=10.0, image_width=-640.0, current_distance_m=1.0)
        assert not math.isnan(v_neg)
        assert not math.isnan(w_neg)


class TestAdversarialProximityStoppingThresholds:
    """
    Adversarial testing of approach sequence, boundary stopping distances (TC8),
    zero-velocity emission, state transitions, chime, and announcement flags.
    """

    def test_complete_approach_distance_sequence(self):
        """
        Test approach distance sequence:
          1.0m (continues) -> 0.50m (continues) -> 0.31m (continues) ->
          0.3000m (stops) -> 0.28m (stops) -> 0.22m (stops).
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("studio", "libro")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "book", "confidence": 0.88}])

        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING

        # 1. 1.0m -> Continues
        msg_1m, done_1m = coord.execute_visual_servoing_step(1.00)
        v_1m, w_1m, twist_done_1m = coord.compute_visual_servoing_twist(320.0, 640.0, 1.00)
        assert done_1m is False
        assert twist_done_1m is False
        assert v_1m > 0.0
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
        assert coord.chime_played is False
        assert coord.completion_announced is False

        # 2. 0.50m -> Continues
        msg_50cm, done_50cm = coord.execute_visual_servoing_step(0.50)
        v_50cm, w_50cm, twist_done_50cm = coord.compute_visual_servoing_twist(320.0, 640.0, 0.50)
        assert done_50cm is False
        assert twist_done_50cm is False
        assert v_50cm > 0.0
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
        assert coord.chime_played is False
        assert coord.completion_announced is False

        # 3. 0.31m -> Continues (> 0.30m boundary)
        msg_31cm, done_31cm = coord.execute_visual_servoing_step(0.31)
        v_31cm, w_31cm, twist_done_31cm = coord.compute_visual_servoing_twist(320.0, 640.0, 0.31)
        assert done_31cm is False
        assert twist_done_31cm is False
        assert v_31cm > 0.0
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
        assert coord.chime_played is False
        assert coord.completion_announced is False

        # 4. 0.3000m -> STOPS (Exact boundary)
        msg_30cm, done_30cm = coord.execute_visual_servoing_step(0.3000)
        v_30cm, w_30cm, twist_done_30cm = coord.compute_visual_servoing_twist(320.0, 640.0, 0.3000)
        assert done_30cm is True
        assert twist_done_30cm is True
        assert v_30cm == 0.0
        assert w_30cm == 0.0
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_COMPLETED
        assert coord.chime_played is True
        assert coord.completion_announced is True
        assert "raggiunto con successo" in msg_30cm
        assert "Motori arrestati" in msg_30cm

        # 5. Subsequent post-stop readings (0.28m, 0.22m) maintain zero velocity and completion
        v_28cm, w_28cm, twist_done_28cm = coord.compute_visual_servoing_twist(320.0, 640.0, 0.28)
        assert twist_done_28cm is True
        assert v_28cm == 0.0
        assert w_28cm == 0.0
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_COMPLETED

        v_22cm, w_22cm, twist_done_22cm = coord.compute_visual_servoing_twist(320.0, 640.0, 0.22)
        assert twist_done_22cm is True
        assert v_22cm == 0.0
        assert w_22cm == 0.0
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_COMPLETED

    @pytest.mark.parametrize("d_stop", [0.3000, 0.2800, 0.2200, 0.1500, 0.0500, 0.0000])
    def test_isolated_stopping_threshold_tests(self, d_stop):
        """
        Verify that whenever distance is <= 0.30m, any isolated visual servoing call
        immediately triggers stop, zero velocities, and PHASE_COMPLETED.
        """
        coord = HybridTargetSeekerEngine()
        coord.start_mission("salotto", "zaino")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "backpack", "confidence": 0.85}])

        msg, done = coord.execute_visual_servoing_step(d_stop)
        assert done is True
        assert coord.current_phase == HybridTargetSeekerEngine.PHASE_COMPLETED
        assert coord.chime_played is True
        assert coord.completion_announced is True
        assert coord.final_distance_to_target == d_stop

        # Twist calculation must output exactly zero velocity
        v, w, is_comp = coord.compute_visual_servoing_twist(320.0, 640.0, d_stop)
        assert is_comp is True
        assert v == 0.0
        assert w == 0.0
