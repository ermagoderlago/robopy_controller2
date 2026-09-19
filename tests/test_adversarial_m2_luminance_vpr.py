#!/usr/bin/env python3
"""
Empirical Adversarial Test Suite for Milestone 2: Luminance Gate & CosPlace VPR
=============================================================================
Author: challenger_m2_1 (Adversarial Luminance & CosPlace VPR Verifier)
Target Modules:
    - robopy_controller/nodes/luminance_safety_gate.py
    - robopy_controller/nodes/vpr_room_recognizer.py

Covers:
1. Floating-point precision at extreme boundaries (24.999/25.000/25.001, 29.999/30.000/30.001)
2. Extreme image dimensions (1x1, 1080p, 4K, non-standard/prime ratios) and channel encodings
3. Empirical reproduction of single-channel 3D (H, W, 1) callback crash vulnerability
4. Empirical reproduction of NaN/empty image fail-safe bypass vulnerability
5. Rapid oscillation across hysteresis band [25.0, 30.0] with zero state chattering
6. Strict L2 normalization invariance on extreme-magnitude, near-zero, and noisy vectors
7. Corner-case embeddings: orthogonal (0.0), diametrical (-1.0), NaN, and Inf vulnerability
8. Strict boundary discrimination (0.8390, 0.8399, 0.8400 rejected vs 0.8401, 0.8410 accepted)
9. High-throughput vector matching benchmark: 10,000 queries against 50 rooms
"""

import math
import random
import time
from typing import List, Tuple
import numpy as np
import pytest

from robopy_controller.nodes.luminance_safety_gate import (
    LuminanceSafetyGate,
    LuminanceSafetyGateNode,
    HAS_ROS2
)
from robopy_controller.nodes.vpr_room_recognizer import (
    CosPlaceModelWrapper,
    VPRMatcherEngine
)


# ==============================================================================
# 1. Adversarial Luminance Stress: Extreme Boundaries & Precision
# ==============================================================================

class TestAdversarialLuminanceBoundaries:
    """Stress-tests floating-point precision at strict threshold boundaries."""

    @pytest.mark.parametrize(
        "val,expected_status,expected_auth",
        [
            (24.999, LuminanceSafetyGate.STATE_INHIBITED_DARK, False),
            (25.000, LuminanceSafetyGate.STATE_INHIBITED_DARK, False),
            (25.001, LuminanceSafetyGate.STATE_INHIBITED_DARK, False),
            (29.999, LuminanceSafetyGate.STATE_INHIBITED_DARK, False),
            (30.000, LuminanceSafetyGate.STATE_INHIBITED_DARK, False),
            (30.001, LuminanceSafetyGate.STATE_AUTHORIZED, True),
        ]
    )
    def test_static_evaluate_precision_boundaries(self, val, expected_status, expected_auth):
        """Static evaluate contract: strictly > 30.0 authorized, all else inhibited."""
        gate = LuminanceSafetyGate()
        res = gate.evaluate(val)
        assert res["status"] == expected_status, f"Evaluate({val}) status mismatch: {res['status']}"
        assert res["authorized"] == expected_auth, f"Evaluate({val}) auth mismatch: {res['authorized']}"

    def test_stateful_update_rising_from_dark(self):
        """When starting in DARK, threshold 30.0 must not authorize until strictly > 30.0."""
        gate = LuminanceSafetyGate()
        assert gate.current_state == LuminanceSafetyGate.STATE_INHIBITED_DARK

        # 24.999 -> DARK
        assert gate.update(24.999)["status"] == LuminanceSafetyGate.STATE_INHIBITED_DARK
        # 25.000 -> DARK
        assert gate.update(25.000)["status"] == LuminanceSafetyGate.STATE_INHIBITED_DARK
        # 25.001 -> DARK (deadband retains DARK)
        assert gate.update(25.001)["status"] == LuminanceSafetyGate.STATE_INHIBITED_DARK
        # 29.999 -> DARK (deadband retains DARK)
        assert gate.update(29.999)["status"] == LuminanceSafetyGate.STATE_INHIBITED_DARK
        # 30.000 -> DARK (boundary value does NOT authorize)
        assert gate.update(30.000)["status"] == LuminanceSafetyGate.STATE_INHIBITED_DARK
        # 30.001 -> AUTHORIZED (strictly > 30.0 triggers authorization)
        res = gate.update(30.001)
        assert res["status"] == LuminanceSafetyGate.STATE_AUTHORIZED
        assert res["authorized"] is True

    def test_stateful_update_falling_from_authorized(self):
        """When in AUTHORIZED, threshold 25.0 must retain authorization until strictly < 25.0."""
        gate = LuminanceSafetyGate()
        gate.update(40.0)
        assert gate.current_state == LuminanceSafetyGate.STATE_AUTHORIZED

        # 30.001 -> AUTH
        assert gate.update(30.001)["status"] == LuminanceSafetyGate.STATE_AUTHORIZED
        # 30.000 -> AUTH (deadband retains AUTH)
        assert gate.update(30.000)["status"] == LuminanceSafetyGate.STATE_AUTHORIZED
        # 29.999 -> AUTH (deadband retains AUTH)
        assert gate.update(29.999)["status"] == LuminanceSafetyGate.STATE_AUTHORIZED
        # 25.001 -> AUTH (deadband retains AUTH)
        assert gate.update(25.001)["status"] == LuminanceSafetyGate.STATE_AUTHORIZED
        # 25.000 -> AUTH (boundary value does NOT inhibit)
        assert gate.update(25.000)["status"] == LuminanceSafetyGate.STATE_AUTHORIZED
        # 24.999 -> INHIBITED_DARK (strictly < 25.0 triggers inhibition)
        res = gate.update(24.999)
        assert res["status"] == LuminanceSafetyGate.STATE_INHIBITED_DARK
        assert res["authorized"] is False


# ==============================================================================
# 2. Adversarial Luminance Stress: Extreme Dimensions, Formats & Vulnerabilities
# ==============================================================================

class TestAdversarialLuminanceImageRobustness:
    """Stress-tests extreme image resolutions, aspect ratios, and channels."""

    def test_extreme_resolutions_1x1_and_high_res(self):
        """Evaluates 1x1 minimal images, 1080p, and 4K images."""
        # 1x1 RGB
        img_1x1_rgb = np.array([[[100, 150, 200]]], dtype=np.uint8)
        expected_y = 0.299 * 100 + 0.587 * 150 + 0.114 * 200
        lum_1x1 = LuminanceSafetyGate.compute_mean_luminance(img_1x1_rgb, encoding="rgb8")
        assert abs(lum_1x1 - expected_y) < 1e-4

        # 1x1 Grayscale 2D
        img_1x1_gray = np.array([[128]], dtype=np.uint8)
        assert abs(LuminanceSafetyGate.compute_mean_luminance(img_1x1_gray) - 128.0) < 1e-4

        # 1920x1080 Full HD
        img_1080p = np.full((1080, 1920, 3), 42, dtype=np.uint8)
        assert abs(LuminanceSafetyGate.compute_mean_luminance(img_1080p, encoding="rgb8") - 42.0) < 1e-4

        # 3840x2160 4K UHD
        img_4k = np.full((2160, 3840, 3), 64, dtype=np.uint8)
        assert abs(LuminanceSafetyGate.compute_mean_luminance(img_4k, encoding="bgr8") - 64.0) < 1e-4

    def test_non_standard_aspect_ratios(self):
        """Evaluates highly anisotropic and prime dimensions."""
        shapes = [
            (1, 1000, 3),
            (1000, 1, 3),
            (7, 13, 3),
            (333, 555, 3),
            (17, 97, 3),
        ]
        for s in shapes:
            img = np.full(s, 50, dtype=np.uint8)
            lum = LuminanceSafetyGate.compute_mean_luminance(img, encoding="rgb8")
            assert abs(lum - 50.0) < 1e-4

    def test_channel_encodings_bgr_vs_rgb(self):
        """Verifies correct BT.601 weight application for BGR vs RGB."""
        # Pure red image (R=255, G=0, B=0)
        pure_red_rgb = np.zeros((10, 10, 3), dtype=np.uint8)
        pure_red_rgb[:, :, 0] = 255  # Red channel in RGB is idx 0
        lum_rgb = LuminanceSafetyGate.compute_mean_luminance(pure_red_rgb, encoding="rgb8")
        assert abs(lum_rgb - (0.299 * 255)) < 1e-4

        # Pure red in BGR is channel index 2
        pure_red_bgr = np.zeros((10, 10, 3), dtype=np.uint8)
        pure_red_bgr[:, :, 2] = 255  # Red channel in BGR is idx 2
        lum_bgr = LuminanceSafetyGate.compute_mean_luminance(pure_red_bgr, encoding="bgr8")
        assert abs(lum_bgr - (0.299 * 255)) < 1e-4

    def test_vulnerability_single_channel_3d_crashes_compute_mean(self):
        """
        HARDENED BEHAVIOR:
        compute_mean_luminance supports single-channel 3D images (H, W, 1) without
        raising ValueError, returning the scalar mean value.
        """
        arr_single_channel_3d = np.full((100, 100, 1), 50, dtype=np.uint8)
        mean_val = LuminanceSafetyGate.compute_mean_luminance(arr_single_channel_3d)
        assert abs(mean_val - 50.0) < 1e-4

    def test_vulnerability_nan_empty_image_fails_to_inhibit_authorized(self):
        """
        HARDENED BEHAVIOR:
        When an empty image (0, 0, 3) is passed, compute_mean_luminance returns NaN.
        In update(nan), non-finite / NaN values safely fail-safe immediately to
        INHIBITED_DARK with authorized=False, preventing false state retention.
        """
        gate = LuminanceSafetyGate()
        gate.update(50.0)
        assert gate.current_state == LuminanceSafetyGate.STATE_AUTHORIZED

        # An empty image produces NaN luminance
        empty_img = np.zeros((0, 0, 3), dtype=np.uint8)
        nan_lum = LuminanceSafetyGate.compute_mean_luminance(empty_img)
        assert np.isnan(nan_lum)

        # Updating with NaN safely transitions to INHIBITED_DARK!
        res = gate.update(nan_lum)
        assert res["status"] == LuminanceSafetyGate.STATE_INHIBITED_DARK
        assert res["authorized"] is False


# ==============================================================================
# 3. Adversarial Luminance Stress: Rapid Oscillation & Hysteresis Stability
# ==============================================================================

class TestAdversarialHysteresisStability:
    """Stress-tests hysteresis deadband against rapid oscillation and state chattering."""

    def test_rapid_oscillation_zero_chattering_from_dark(self):
        """10,000 rapid oscillations strictly inside [25.0001, 29.9999] starting from DARK."""
        gate = LuminanceSafetyGate()
        random.seed(42)
        state_changes = 0

        for _ in range(10000):
            sample = random.uniform(25.0001, 29.9999)
            res = gate.update(sample)
            if res["status"] != LuminanceSafetyGate.STATE_INHIBITED_DARK:
                state_changes += 1

        assert state_changes == 0, f"Expected 0 state changes from DARK, got {state_changes}"

    def test_rapid_oscillation_zero_chattering_from_authorized(self):
        """10,000 rapid oscillations strictly inside [25.0001, 29.9999] starting from AUTHORIZED."""
        gate = LuminanceSafetyGate()
        gate.update(60.0)  # initialize to AUTHORIZED
        assert gate.current_state == LuminanceSafetyGate.STATE_AUTHORIZED
        random.seed(1337)
        state_changes = 0

        for _ in range(10000):
            sample = random.uniform(25.0001, 29.9999)
            res = gate.update(sample)
            if res["status"] != LuminanceSafetyGate.STATE_AUTHORIZED:
                state_changes += 1

        assert state_changes == 0, f"Expected 0 state changes from AUTHORIZED, got {state_changes}"

    def test_cyclic_traversal_hysteresis_exact_transitions(self):
        """500 cyclic sweeps crossing 10.0 -> 40.0 -> 10.0 produce exactly 500 auth and 500 dark transitions."""
        gate = LuminanceSafetyGate()
        num_cycles = 500
        transitions_to_auth = 0
        transitions_to_dark = 0

        for _ in range(num_cycles):
            # Sweep up: 10.0 -> 24.0 -> 28.0 -> 35.0
            r1 = gate.update(10.0)
            r2 = gate.update(28.0)  # deadband, remains dark
            assert r2["status"] == LuminanceSafetyGate.STATE_INHIBITED_DARK
            r3 = gate.update(35.0)  # crosses 30.0 -> auth
            assert r3["status"] == LuminanceSafetyGate.STATE_AUTHORIZED
            transitions_to_auth += 1

            # Sweep down: 35.0 -> 28.0 -> 10.0
            r4 = gate.update(28.0)  # deadband, remains auth
            assert r4["status"] == LuminanceSafetyGate.STATE_AUTHORIZED
            r5 = gate.update(10.0)  # crosses 25.0 -> dark
            assert r5["status"] == LuminanceSafetyGate.STATE_INHIBITED_DARK
            transitions_to_dark += 1

        assert transitions_to_auth == num_cycles
        assert transitions_to_dark == num_cycles


# ==============================================================================
# 4. Adversarial CosPlace 512D Vector Stress: L2 Normalization Invariance
# ==============================================================================

class TestAdversarialCosPlaceNormalization:
    """Stress-tests strict L2 normalization invariance on extreme vectors."""

    def test_l2_normalization_valid_extreme_magnitudes(self):
        """Verifies ||v||_2 = 1.0 +- 1e-5 across valid dynamic ranges 10^-20 to 10^15."""
        exponents = [-20, -15, -10, -5, -1, 1, 5, 10, 15]
        for exp in exponents:
            raw = np.full(512, 10.0**exp, dtype=np.float32)
            norm_v = CosPlaceModelWrapper.normalize_l2(raw)
            norm_val = float(np.linalg.norm(norm_v))
            assert abs(norm_val - 1.0) <= 1e-5, f"L2 norm failed for 10^{exp}: norm={norm_val}"

    def test_l2_normalization_overflow_underflow_safely_rejected(self):
        """Values causing IEEE 754 float32 overflow or underflow raise ValueError."""
        # Overflow exponents: 10^18 squared overflows float32 max (3.4e38)
        for exp in [18, 20, 30]:
            raw = np.full(512, 10.0**exp, dtype=np.float32)
            with pytest.raises(ValueError, match="Zero or NaN vector cannot be normalized"):
                CosPlaceModelWrapper.normalize_l2(raw)

        # Underflow exponents: 10^-30 squared underflows float32 to 0.0
        for exp in [-30, -40]:
            raw = np.full(512, 10.0**exp, dtype=np.float32)
            with pytest.raises(ValueError, match="Zero or NaN vector cannot be normalized"):
                CosPlaceModelWrapper.normalize_l2(raw)

    def test_l2_normalization_noise_distributions(self):
        """Tests normalization invariance under diverse noise distributions."""
        rng = np.random.RandomState(42)
        # Gaussian noise
        v_gauss = rng.randn(512).astype(np.float32)
        n1 = np.linalg.norm(CosPlaceModelWrapper.normalize_l2(v_gauss))
        assert abs(n1 - 1.0) <= 1e-5

        # Laplace noise (heavy-tailed)
        v_laplace = rng.laplace(0, 5.0, 512).astype(np.float32)
        n2 = np.linalg.norm(CosPlaceModelWrapper.normalize_l2(v_laplace))
        assert abs(n2 - 1.0) <= 1e-5

        # Uniform noise
        v_unif = rng.uniform(-100.0, 100.0, 512).astype(np.float32)
        n3 = np.linalg.norm(CosPlaceModelWrapper.normalize_l2(v_unif))
        assert abs(n3 - 1.0) <= 1e-5

        # Sparse spike vector (single active non-zero element)
        v_spike = np.zeros(512, dtype=np.float32)
        v_spike[255] = 1337.0
        norm_spike = CosPlaceModelWrapper.normalize_l2(v_spike)
        assert abs(np.linalg.norm(norm_spike) - 1.0) <= 1e-5
        assert norm_spike[255] == 1.0

    def test_l2_normalization_rejects_zeros_nans_and_infs(self):
        """Zero vectors, NaNs, and Infs must raise ValueError."""
        with pytest.raises(ValueError, match="Zero or NaN vector cannot be normalized"):
            CosPlaceModelWrapper.normalize_l2(np.zeros(512, dtype=np.float32))

        v_nan = np.ones(512, dtype=np.float32)
        v_nan[10] = float("nan")
        with pytest.raises(ValueError, match="Zero or NaN vector cannot be normalized"):
            CosPlaceModelWrapper.normalize_l2(v_nan)

        v_inf = np.ones(512, dtype=np.float32)
        v_inf[42] = float("inf")
        with pytest.raises(ValueError, match="Zero or NaN vector cannot be normalized"):
            CosPlaceModelWrapper.normalize_l2(v_inf)


# ==============================================================================
# 5. Adversarial CosPlace 512D Vector Stress: Corner Cases & Infiltration
# ==============================================================================

class TestAdversarialVPRCornerCases:
    """Stress-tests corner-case embeddings (orthogonal, diametrical, NaN, Inf)."""

    def test_orthogonal_vectors_similarity_zero(self):
        """Orthogonal vectors (sim = 0.0) must never match."""
        matcher = VPRMatcherEngine()
        ref = np.zeros(512, dtype=np.float32)
        ref[0] = 1.0
        matcher.register_room("cucina", [ref])

        query = np.zeros(512, dtype=np.float32)
        query[1] = 1.0  # orthogonal
        res = matcher.match_single_frame(query)
        assert res["matched"] is False
        assert res["room_name"] is None
        assert abs(res["similarity"] - 0.0) < 1e-5

    def test_diametrically_opposed_vectors_similarity_negative_one(self):
        """Diametrically opposed vectors (sim = -1.0) must never match."""
        matcher = VPRMatcherEngine()
        ref = np.zeros(512, dtype=np.float32)
        ref[0] = 1.0
        matcher.register_room("camera", [ref])

        query = np.zeros(512, dtype=np.float32)
        query[0] = -1.0  # opposite
        res = matcher.match_single_frame(query)
        assert res["matched"] is False
        assert res["room_name"] is None
        assert abs(res["similarity"] - (-1.0)) < 1e-5

    def test_nan_vector_does_not_match(self):
        """Query containing NaN must not produce a false match."""
        matcher = VPRMatcherEngine()
        ref = np.zeros(512, dtype=np.float32)
        ref[0] = 1.0
        matcher.register_room("salotto", [ref])

        nan_query = np.full(512, float("nan"), dtype=np.float32)
        res = matcher.match_single_frame(nan_query)
        assert res["matched"] is False
        assert res["room_name"] is None

    def test_vulnerability_unvalidated_inf_query_vector_false_match(self):
        """
        HARDENED BEHAVIOR:
        VPRMatcherEngine.match_single_frame validates query_vector for finite values.
        If a query with +inf (or NaN) is passed, it safely and immediately returns
        matched=False with similarity=-1.0, confidence=-1.0, preventing false match
        and corrupt JSON serialization.
        """
        matcher = VPRMatcherEngine()
        ref = np.zeros(512, dtype=np.float32)
        ref[0] = 1.0
        matcher.register_room("salotto", [ref])

        # An adversarial non-finite vector
        inf_query = np.zeros(512, dtype=np.float32)
        inf_query[0] = float("inf")

        res = matcher.match_single_frame(inf_query)
        assert res["matched"] is False
        assert res["room_name"] is None
        assert res["similarity"] == -1.0
        assert res["confidence"] == -1.0


# ==============================================================================
# 6. Adversarial CosPlace 512D Vector Stress: Strict Boundary Discrimination
# ==============================================================================

class TestAdversarialVPRBoundaryDiscrimination:
    """Strict discrimination test: 0.8390, 0.8399, 0.8400 REJECTED; 0.8401, 0.8410 ACCEPTED."""

    @staticmethod
    def _create_unit_vector_with_cosine(s: float) -> Tuple[np.ndarray, np.ndarray]:
        """Creates reference e1 and query vector with exact cosine dot product s."""
        ref = np.zeros(512, dtype=np.float32)
        ref[0] = 1.0

        query = np.zeros(512, dtype=np.float32)
        query[0] = s
        query[1] = math.sqrt(max(0.0, 1.0 - s * s))
        # Ensure unit norm
        norm_q = float(np.linalg.norm(query))
        assert abs(norm_q - 1.0) < 1e-6
        return ref, query

    @pytest.mark.parametrize(
        "sim_score,expected_matched",
        [
            (0.8390, False),
            (0.8399, False),
            (0.8400, False),  # Boundary value must be strictly > 0.840
            (0.8401, True),
            (0.8410, True),
        ]
    )
    def test_strict_boundary_discrimination(self, sim_score, expected_matched):
        """Validates that (similarity - 0.840) > 1e-5 strictly separates rejection from match."""
        matcher = VPRMatcherEngine()
        ref, query = self._create_unit_vector_with_cosine(sim_score)
        matcher.register_room("test_locale", [ref])

        res = matcher.match_single_frame(query)
        assert res["matched"] is expected_matched, (
            f"Discrimination failed for score {sim_score}: expected matched={expected_matched}, got {res['matched']}"
        )
        if expected_matched:
            assert res["room_name"] == "test_locale"
        else:
            assert res["room_name"] is None


# ==============================================================================
# 7. Adversarial Throughput Benchmark: 10,000 Queries Against 50 Rooms
# ==============================================================================

class TestAdversarialThroughputBenchmark:
    """Benchmarks matching latency and throughput over 10,000 queries against 50 rooms."""

    def test_10000_queries_against_50_rooms(self):
        """
        Populates 50 rooms with 5 exemplar descriptors each (250 vectors in memory).
        Executes 10,000 query evaluations and measures latency profile.
        Requirements:
        - Mean latency < 1.0 ms (budget is < 50 ms)
        - p99 latency < 5.0 ms
        - Throughput > 1,000 QPS
        """
        matcher = VPRMatcherEngine()
        rng = np.random.RandomState(42)

        num_rooms = 50
        exemplars_per_room = 5
        for i in range(num_rooms):
            room_name = f"room_{i:02d}"
            vecs = []
            for _ in range(exemplars_per_room):
                v = rng.randn(512).astype(np.float32)
                vecs.append(v / np.linalg.norm(v))
            matcher.register_room(room_name, vecs)

        num_queries = 10000
        queries = []
        for _ in range(num_queries):
            q = rng.randn(512).astype(np.float32)
            queries.append(q / np.linalg.norm(q))

        latencies_ms = []
        t0 = time.perf_counter()
        for q in queries:
            t_sub = time.perf_counter()
            matcher.match_single_frame(q)
            latencies_ms.append((time.perf_counter() - t_sub) * 1000.0)
        total_time_s = time.perf_counter() - t0

        latencies_ms = np.array(latencies_ms)
        mean_ms = float(np.mean(latencies_ms))
        p50_ms = float(np.percentile(latencies_ms, 50))
        p95_ms = float(np.percentile(latencies_ms, 95))
        p99_ms = float(np.percentile(latencies_ms, 99))
        qps = num_queries / total_time_s

        print(f"\n[BENCHMARK] 10,000 queries across 50 rooms:")
        print(f"  Total time: {total_time_s:.3f} s")
        print(f"  Throughput: {qps:.1f} queries/sec")
        print(f"  Mean latency: {mean_ms:.4f} ms")
        print(f"  Median (p50): {p50_ms:.4f} ms")
        print(f"  p95 latency:  {p95_ms:.4f} ms")
        print(f"  p99 latency:  {p99_ms:.4f} ms")

        assert mean_ms < 1.0, f"Mean latency too high: {mean_ms:.4f} ms >= 1.0 ms"
        assert p99_ms < 5.0, f"p99 latency too high: {p99_ms:.4f} ms >= 5.0 ms"
        assert qps > 1000.0, f"Throughput too low: {qps:.1f} QPS <= 1000 QPS"
