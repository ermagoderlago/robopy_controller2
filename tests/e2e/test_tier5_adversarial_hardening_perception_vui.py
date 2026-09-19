"""
==============================================================================
🧪 TIER 5: ADVERSARIAL HARDENING TEST SUITE (PERCEPTION & VUI)
==============================================================================
Empirical adversarial challenge suite targeting white-box edge cases,
boundary conditions, and pathological inputs across:
1. robopy_controller/nodes/luminance_safety_gate.py
2. robopy_controller/nodes/vpr_room_recognizer.py
3. robopy_controller/nodes/lidar_room_recognizer.py
4. robopy_controller/robot_ai/vui/vui_dialogue_engine.py
5. robopy_controller/robot_ai/core/destructive_confirmation_gate.py
6. robopy_controller/nodes/hybrid_target_seeker.py

Coverage Areas:
- Pathological luminance inputs (all-black, all-white, checkerboard, flash)
- CosPlace 512D embeddings (underflow/overflow, zero-norm, infinite norms)
- Dark LiDAR RPLIDAR C1 Fourier spectrum (noisy/missing beams, zero-range, specular)
- ASR dialogue fuzzing (Italian dialects, contradictory intents, acoustic echo leakage)
- Hybrid navigation visual servoing edge cases (target disappearing, distance jumps, occlusions)

Conforms to:
- TC1 - TC8 & R1 - R5 Acceptance Criteria
- SPEC-01, SPEC-02, SPEC-03, SPEC-04, SPEC-05, SPEC-07
- marcus_core_rules.md: Clean UTF-8 without BOM
==============================================================================
"""

import os
import sys
import math
import time
import json
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Tuple

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

ROBOPY_DIR = WORKSPACE_ROOT / "robopy_controller"
if str(ROBOPY_DIR) not in sys.path:
    sys.path.insert(0, str(ROBOPY_DIR))

import numpy as np
import pytest

# Direct production imports
from robopy_controller.nodes.luminance_safety_gate import (
    LuminanceSafetyGate,
)
from robopy_controller.nodes.vpr_room_recognizer import (
    CosPlaceModelWrapper,
    VPRMatcherEngine,
)
from robopy_controller.nodes.lidar_room_recognizer import (
    resample_scan_to_360_bins,
    compute_fourier_descriptor,
    compute_circular_ncc,
    LidarRoomRecognizerEngine,
)
from robopy_controller.robot_ai.vui.vui_dialogue_engine import (
    VUIDialogueEngine,
)
from robopy_controller.robot_ai.core.destructive_confirmation_gate import (
    DestructiveConfirmationGate,
)
from robopy_controller.nodes.hybrid_target_seeker import (
    HybridTargetSeekerEngine,
)


# ============================================================================
# 1. Pathological Luminance Inputs (Luminance Safety Gate)
# ============================================================================
class TestTier5PathologicalLuminance:
    """
    White-box stress tests for ITU-R BT.601 perceptual luminance calculation,
    Schmitt-trigger hysteresis, and vocal warning dispatch under pathological inputs.
    """

    def test_all_black_frame_rgb_mono_bytes(self):
        """Pathological all-black frames across RGB, grayscale, and raw byte buffers."""
        gate = LuminanceSafetyGate(threshold_low=25.0, threshold_high=30.0)

        # 1. 3D RGB all zeros
        black_rgb = np.zeros((480, 640, 3), dtype=np.uint8)
        mean_rgb = LuminanceSafetyGate.compute_mean_luminance(black_rgb, encoding="rgb8")
        assert mean_rgb == 0.0

        eval_rgb = gate.evaluate(mean_rgb)
        assert eval_rgb["status"] == "INHIBITED_DARK"
        assert not eval_rgb["authorized"]
        assert eval_rgb["voice_warning"] is not None
        assert "insufficiente" in eval_rgb["voice_warning"]

        # 2. 2D Grayscale all zeros
        black_mono = np.zeros((480, 640), dtype=np.uint8)
        mean_mono = LuminanceSafetyGate.compute_mean_luminance(black_mono, encoding="mono8")
        assert mean_mono == 0.0
        eval_mono = gate.evaluate(mean_mono)
        assert eval_mono["status"] == "INHIBITED_DARK"
        assert not eval_mono["authorized"]

        # 3. Raw byte buffer
        black_bytes = bytes(480 * 640 * 3)
        mean_bytes = LuminanceSafetyGate.compute_mean_luminance(
            black_bytes, encoding="rgb8", height=480, width=640
        )
        assert mean_bytes == 0.0

        # 4. Stateful hysteresis update
        state = gate.update(mean_rgb)
        assert state["status"] == "INHIBITED_DARK"
        assert not state["authorized"]

    def test_all_white_frame_rgb_mono(self):
        """Pathological all-white saturation across RGB and grayscale."""
        gate = LuminanceSafetyGate(threshold_low=25.0, threshold_high=30.0)

        white_rgb = np.full((480, 640, 3), 255, dtype=np.uint8)
        mean_rgb = LuminanceSafetyGate.compute_mean_luminance(white_rgb, encoding="rgb8")
        assert abs(mean_rgb - 255.0) < 1e-4

        eval_rgb = gate.evaluate(mean_rgb)
        assert eval_rgb["status"] == "AUTHORIZED"
        assert eval_rgb["authorized"]
        assert eval_rgb["voice_warning"] is None

        white_mono = np.full((480, 640), 255, dtype=np.uint8)
        mean_mono = LuminanceSafetyGate.compute_mean_luminance(white_mono, encoding="mono8")
        assert abs(mean_mono - 255.0) < 1e-4

        state = gate.update(mean_rgb)
        assert state["status"] == "AUTHORIZED"
        assert state["authorized"]

    def test_alternating_checkerboard_patterns(self):
        """Alternating black/white checkerboard patterns and deadband hysteresis."""
        gate = LuminanceSafetyGate(threshold_low=25.0, threshold_high=30.0)

        # 50/50 symmetric checkerboard (block size 8x8)
        h, w = 480, 640
        y_coords, x_coords = np.indices((h, w))
        checker_sym = (((y_coords // 8) + (x_coords // 8)) % 2) * 255
        checker_rgb = np.stack([checker_sym, checker_sym, checker_sym], axis=-1).astype(np.uint8)

        mean_sym = LuminanceSafetyGate.compute_mean_luminance(checker_rgb)
        assert abs(mean_sym - 127.5) < 0.1
        eval_sym = gate.evaluate(mean_sym)
        assert eval_sym["status"] == "AUTHORIZED"
        assert eval_sym["authorized"]

        # Asymmetric checkerboard: 10% white (255), 90% black (0) -> mean = 25.5
        # 25.5 falls squarely in the critical hysteresis deadband [25.0, 30.0]
        asym_arr = np.zeros((100, 100, 3), dtype=np.uint8)
        asym_arr[:10, :, :] = 255  # exactly 10% white
        mean_asym = LuminanceSafetyGate.compute_mean_luminance(asym_arr)
        assert abs(mean_asym - 25.5) < 0.1

        # Static evaluation rejects in deadband
        eval_asym = gate.evaluate(mean_asym)
        assert eval_asym["status"] == "INHIBITED_DARK"
        assert not eval_asym["authorized"]
        assert "limite critico" in eval_asym["voice_warning"]

        # Stateful gate starting from dark remains dark
        gate_stateful = LuminanceSafetyGate(25.0, 30.0)
        assert gate_stateful.update(mean_asym)["status"] == "INHIBITED_DARK"

        # Stateful gate starting from bright retains authorized state in deadband
        gate_stateful.update(50.0)  # authorized
        assert gate_stateful.current_state == "AUTHORIZED"
        assert gate_stateful.update(mean_asym)["status"] == "AUTHORIZED"

    def test_high_contrast_localized_flash_glare(self):
        """Single bright glare/specular spot in an otherwise pitch-dark frame."""
        gate = LuminanceSafetyGate(25.0, 30.0)

        # 480x640 frame with a 10x10 white spot (specular reflection or camera flash reflection)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[200:210, 300:310, :] = 255  # 100 pixels at 255 out of 307,200

        mean_lum = LuminanceSafetyGate.compute_mean_luminance(frame)
        expected_mean = (100 * 255) / (480 * 640)
        assert abs(mean_lum - expected_mean) < 1e-3
        assert mean_lum < 0.10

        # Gate MUST firmly reject mapping despite the localized saturation
        eval_res = gate.evaluate(mean_lum)
        assert eval_res["status"] == "INHIBITED_DARK"
        assert not eval_res["authorized"]

    def test_rapid_flash_transition_hysteresis_sequence(self):
        """Rapid strobe/flash sequence validating Schmitt-trigger state preservation."""
        gate = LuminanceSafetyGate(threshold_low=25.0, threshold_high=30.0)

        # Sequence: Dark -> Full Flash -> Decaying into deadband -> Dimming below threshold -> Re-rising into deadband
        # 1. Dark (0.0) -> INHIBITED_DARK
        assert gate.update(0.0)["status"] == "INHIBITED_DARK"

        # 2. Flash (250.0) -> AUTHORIZED
        assert gate.update(250.0)["status"] == "AUTHORIZED"

        # 3. Flash decaying into deadband (28.0) -> REMAINS AUTHORIZED
        s3 = gate.update(28.0)
        assert s3["status"] == "AUTHORIZED"
        assert s3["authorized"]
        assert s3["voice_warning"] is None

        # 4. Drops below low threshold (24.0) -> Transitions to INHIBITED_DARK
        s4 = gate.update(24.0)
        assert s4["status"] == "INHIBITED_DARK"
        assert not s4["authorized"]
        assert s4["voice_warning"] is not None

        # 5. Rises back into deadband (28.0) -> REMAINS INHIBITED_DARK
        s5 = gate.update(28.0)
        assert s5["status"] == "INHIBITED_DARK"
        assert not s5["authorized"]

    def test_pathological_image_formats_and_channel_weights(self):
        """Pathological dimensions, empty arrays, 4-channel BGRA, and ITU-R BT.601 weights."""
        gate = LuminanceSafetyGate(25.0, 30.0)

        # Empty array
        empty_arr = np.zeros((0,))
        mean_empty = LuminanceSafetyGate.compute_mean_luminance(empty_arr)
        assert math.isnan(mean_empty)
        assert gate.evaluate(mean_empty)["status"] == "INHIBITED_DARK"

        # Pure Red RGB [255, 0, 0]: Y = 0.299 * 255 = 76.245 -> AUTHORIZED
        red_rgb = np.zeros((10, 10, 3), dtype=np.uint8)
        red_rgb[:, :, 0] = 255
        mean_red = LuminanceSafetyGate.compute_mean_luminance(red_rgb, encoding="rgb8")
        assert abs(mean_red - 76.245) < 1e-2
        assert gate.evaluate(mean_red)["status"] == "AUTHORIZED"

        # Pure Blue RGB [0, 0, 255]: Y = 0.114 * 255 = 29.07 -> in deadband [25, 30] -> INHIBITED_DARK
        blue_rgb = np.zeros((10, 10, 3), dtype=np.uint8)
        blue_rgb[:, :, 2] = 255
        mean_blue = LuminanceSafetyGate.compute_mean_luminance(blue_rgb, encoding="rgb8")
        assert abs(mean_blue - 29.070) < 1e-2
        assert gate.evaluate(mean_blue)["status"] == "INHIBITED_DARK"

        # Pure Blue in BGR encoding: [255, 0, 0] -> channel 0 is Blue in BGR -> Y = 0.114 * 255 = 29.07
        blue_bgr = np.zeros((10, 10, 3), dtype=np.uint8)
        blue_bgr[:, :, 0] = 255
        mean_blue_bgr = LuminanceSafetyGate.compute_mean_luminance(blue_bgr, encoding="bgr8")
        assert abs(mean_blue_bgr - 29.070) < 1e-2

        # 4-channel BGRA array: should process first 3 channels safely
        bgra_arr = np.zeros((10, 10, 4), dtype=np.uint8)
        bgra_arr[:, :, 0] = 255  # Blue
        bgra_arr[:, :, 3] = 255  # Alpha
        mean_bgra = LuminanceSafetyGate.compute_mean_luminance(bgra_arr, encoding="bgra8")
        assert abs(mean_bgra - 29.070) < 1e-2

        # Non-finite and NaN inputs
        assert gate.evaluate(float("inf"))["status"] == "INHIBITED_DARK"
        assert gate.evaluate(float("-inf"))["status"] == "INHIBITED_DARK"

        # ROS JSON serialization and Trigger service
        payload = json.loads(gate.to_ros_topic_payload(float("nan")))
        assert payload["luminance"] == 0.0
        assert payload["status"] == "INHIBITED_DARK"

        srv_ok, msg = gate.trigger_service_check(15.0)
        assert not srv_ok
        assert "insufficiente" in msg

        srv_ok_2, msg_2 = gate.trigger_service_check(50.0)
        assert srv_ok_2
        assert "Mapping authorized" in msg_2


# ============================================================================
# 2. CosPlace 512D Embeddings (VPR Room Recognizer)
# ============================================================================
class TestTier5CosPlace512DAdversarial:
    """
    White-box stress tests for CosPlace 512D L2 normalization, floating-point
    extremes, dimensional contracts, and cosine similarity matching boundaries.
    """

    def test_zero_norm_vector_rejection(self):
        """All-zero 512D vectors must raise ValueError during L2 normalization."""
        zero_vec = np.zeros(512, dtype=np.float32)
        with pytest.raises(ValueError, match="Zero or NaN vector cannot be normalized"):
            CosPlaceModelWrapper.normalize_l2(zero_vec)

    def test_floating_point_underflow_overflow(self):
        """Subnormal floating point numbers and extreme float32 overflow values."""
        # 1. Extreme underflow (subnormal numbers that underflow norm to 0.0)
        underflow_vec = np.full(512, 1e-40, dtype=np.float32)
        with pytest.raises(ValueError, match="Zero or NaN vector cannot be normalized"):
            CosPlaceModelWrapper.normalize_l2(underflow_vec)

        # 2. Extreme overflow (huge values that overflow norm to inf)
        overflow_vec = np.full(512, 1e38, dtype=np.float32)
        with pytest.raises(ValueError, match="Zero or NaN vector cannot be normalized"):
            CosPlaceModelWrapper.normalize_l2(overflow_vec)

        # 3. Small but valid numbers (e.g. 1e-12) properly normalized to unit length
        small_vec = np.full(512, 1e-12, dtype=np.float32)
        norm_small = CosPlaceModelWrapper.normalize_l2(small_vec)
        assert abs(float(np.linalg.norm(norm_small)) - 1.0) <= 1e-5
        assert norm_small.dtype == np.float32

    def test_infinite_and_nan_vectors(self):
        """Vectors containing inf, -inf, and nan must be rejected."""
        # Inf vector
        inf_vec = np.full(512, np.inf, dtype=np.float32)
        with pytest.raises(ValueError, match="Zero or NaN vector cannot be normalized"):
            CosPlaceModelWrapper.normalize_l2(inf_vec)

        # -Inf vector
        ninf_vec = np.full(512, -np.inf, dtype=np.float32)
        with pytest.raises(ValueError, match="Zero or NaN vector cannot be normalized"):
            CosPlaceModelWrapper.normalize_l2(ninf_vec)

        # NaN vector
        nan_vec = np.full(512, np.nan, dtype=np.float32)
        with pytest.raises(ValueError, match="Zero or NaN vector cannot be normalized"):
            CosPlaceModelWrapper.normalize_l2(nan_vec)

        # Single NaN in valid vector
        single_nan = np.ones(512, dtype=np.float32)
        single_nan[256] = np.nan
        with pytest.raises(ValueError, match="Zero or NaN vector cannot be normalized"):
            CosPlaceModelWrapper.normalize_l2(single_nan)

    def test_dimension_mismatch_rejection(self):
        """Vectors with dimension != 512 must be rejected with informative ValueError."""
        for bad_dim in [0, 1, 511, 513, 1024]:
            vec = np.ones(bad_dim, dtype=np.float32)
            with pytest.raises(ValueError, match="Vector length must be 512"):
                CosPlaceModelWrapper.normalize_l2(vec)

    def test_vpr_matcher_pathological_query_handling(self):
        """VPRMatcherEngine must gracefully handle NaN, Inf, empty, and unregistered queries."""
        matcher = VPRMatcherEngine()

        # Query on empty database
        res_empty = matcher.match_single_frame(np.ones(512, dtype=np.float32))
        assert not res_empty["matched"]
        assert res_empty["similarity"] == -1.0
        assert res_empty["room_name"] is None

        # Register a valid room
        ref_vec = np.zeros(512, dtype=np.float32)
        ref_vec[0] = 1.0
        matcher.register_room("salotto", [ref_vec])

        # NaN query vector
        res_nan = matcher.match_single_frame(np.full(512, np.nan, dtype=np.float32))
        assert not res_nan["matched"]
        assert res_nan["similarity"] == -1.0
        assert res_nan["room_name"] is None

        # Inf query vector
        res_inf = matcher.match_single_frame(np.full(512, np.inf, dtype=np.float32))
        assert not res_inf["matched"]
        assert res_inf["similarity"] == -1.0

        # Empty array query
        res_arr0 = matcher.match_single_frame(np.array([], dtype=np.float32))
        assert not res_arr0["matched"]
        assert res_arr0["similarity"] == -1.0

    def test_vpr_exact_similarity_threshold_boundary(self):
        """Strict verification of the >0.840 cosine similarity boundary."""
        matcher = VPRMatcherEngine()
        # Reference vector along axis 0
        ref_vec = np.zeros(512, dtype=np.float32)
        ref_vec[0] = 1.0
        matcher.register_room("cucina", [ref_vec])

        # Helper to construct unit vector with exact cosine similarity s
        def make_query_with_similarity(s: float) -> np.ndarray:
            q = np.zeros(512, dtype=np.float32)
            q[0] = float(s)
            q[1] = float(math.sqrt(max(0.0, 1.0 - s * s)))
            return q

        # Case 1: 0.8390 (below threshold) -> REJECT
        q_839 = make_query_with_similarity(0.8390)
        res_839 = matcher.match_single_frame(q_839)
        assert not res_839["matched"]
        assert res_839["room_name"] is None

        # Case 2: 0.8400 (exact threshold) -> REJECT (contract mandates > 0.840)
        q_840 = make_query_with_similarity(0.8400)
        res_840 = matcher.match_single_frame(q_840)
        assert not res_840["matched"]

        # Case 3: 0.8402 (strictly above threshold) -> ACCEPT
        q_8402 = make_query_with_similarity(0.8402)
        res_8402 = matcher.match_single_frame(q_8402)
        assert res_8402["matched"]
        assert res_8402["room_name"] == "cucina"
        assert res_8402["similarity"] >= 0.840

        # Case 4: Orthogonal vector (similarity 0.0) -> REJECT
        q_ortho = np.zeros(512, dtype=np.float32)
        q_ortho[1] = 1.0
        res_ortho = matcher.match_single_frame(q_ortho)
        assert not res_ortho["matched"]
        assert res_ortho["similarity"] == 0.0

        # Case 5: Anti-parallel vector (similarity -1.0) -> REJECT
        q_anti = np.zeros(512, dtype=np.float32)
        q_anti[0] = -1.0
        res_anti = matcher.match_single_frame(q_anti)
        assert not res_anti["matched"]
        assert res_anti["similarity"] == -1.0

    def test_vpr_temporal_consensus_under_adversarial_flapping(self):
        """Adversarial flapping between two rooms suppresses premature recognition."""
        matcher = VPRMatcherEngine(consensus_window=5)

        # Two distinct rooms
        v_salotto = np.zeros(512, dtype=np.float32)
        v_salotto[0] = 1.0
        v_cucina = np.zeros(512, dtype=np.float32)
        v_cucina[1] = 1.0
        matcher.register_room("salotto", [v_salotto])
        matcher.register_room("cucina", [v_cucina])

        # Step 1: Query matches salotto with similarity 0.86 (moderate confidence)
        q1 = np.zeros(512, dtype=np.float32)
        q1[0] = 0.86
        q1[2] = math.sqrt(1.0 - 0.86**2)
        m1 = matcher.match_single_frame(q1)
        assert m1["matched"]
        c1 = matcher.evaluate_consensus(m1)
        # Window size is 1, so single agreement accepted
        assert c1["matched"]

        # Step 2: Next frame flaps to cucina with similarity 0.86
        q2 = np.zeros(512, dtype=np.float32)
        q2[1] = 0.86
        q2[2] = math.sqrt(1.0 - 0.86**2)
        m2 = matcher.match_single_frame(q2)
        assert m2["matched"]
        c2 = matcher.evaluate_consensus(m2)
        # Window size is 2, agreement for cucina is only 1 (<2), consensus must be pending!
        assert not c2["matched"]
        assert c2.get("consensus_pending") is True

        # Step 3: High confidence fast-track (>0.90) bypasses consensus requirement
        q_high = np.zeros(512, dtype=np.float32)
        q_high[1] = 0.95
        q_high[2] = math.sqrt(1.0 - 0.95**2)
        m_high = matcher.match_single_frame(q_high)
        c_high = matcher.evaluate_consensus(m_high)
        assert c_high["matched"]
        assert c_high["room_name"] == "cucina"


# ============================================================================
# 3. Dark LiDAR RPLIDAR C1 Fourier Spectrum (LiDAR Room Recognizer)
# ============================================================================
class TestTier5DarkLidarFourierSpectrumAdversarial:
    """
    White-box stress tests for 360-bin polar resampling, rotation-invariant
    1D Fourier magnitude spectra, circular NCC yaw estimation, and dark localization.
    """

    def test_zero_range_and_specular_absorption_scans(self):
        """Zero-range readings (closer than 0.10m) and infinite specular reflections."""
        # RPLIDAR C1 returns 0.0 for blind range or hardware error
        zero_ranges = [0.0] * 360
        resampled_zero = resample_scan_to_360_bins(zero_ranges, range_min=0.10, range_max=8.0)
        assert len(resampled_zero) == 360
        # All 0.0 values are < range_min, so must be replaced with range_max (8.0m)
        assert np.allclose(resampled_zero, 8.0)

        # Specular reflection / absorbant black velvet: returns inf or nan
        specular_ranges = [float("inf")] * 180 + [float("nan")] * 180
        resampled_spec = resample_scan_to_360_bins(specular_ranges, range_min=0.10, range_max=8.0)
        assert np.allclose(resampled_spec, 8.0)

        # Circular NCC on zero-variance flat scan must safely return (0.0, 0.0) without ZeroDivisionError
        score, yaw = compute_circular_ncc(resampled_zero, resampled_spec)
        assert score == 0.0
        assert yaw == 0.0

        # LidarRoomRecognizerEngine must reject flat uninformative scan
        engine = LidarRoomRecognizerEngine()
        res = engine.recognize_scan(resampled_zero)
        assert not res["matched"]
        assert res["room_name"] is None

    def test_noisy_and_missing_beams_resilience(self):
        """50% missing beams and severe Gaussian noise on rectangular room geometry."""
        # Generate synthetic rectangular room (6.0m x 4.0m) viewed from center
        angles = np.linspace(0, 2.0 * math.pi, 360, endpoint=False)
        rect_scan = np.zeros(360, dtype=np.float32)
        for i, th in enumerate(angles):
            # Distance to 6x4 rectangle: x in [-3, +3], y in [-2, +2]
            cos_t = max(abs(math.cos(th)), 1e-4)
            sin_t = max(abs(math.sin(th)), 1e-4)
            r = min(3.0 / cos_t, 2.0 / sin_t)
            rect_scan[i] = r

        engine = LidarRoomRecognizerEngine(fourier_threshold=0.25, ncc_threshold=0.70)
        engine.register_room("salotto_rettangolare", rect_scan)

        # 1. 50% missing beams (dropouts from black furniture / dark objects)
        noisy_ranges = rect_scan.copy()
        rng = np.random.RandomState(42)
        drop_mask = rng.rand(360) > 0.50
        noisy_ranges[drop_mask] = float("nan")

        resampled_clean = resample_scan_to_360_bins(noisy_ranges)
        assert not np.any(np.isnan(resampled_clean))

        # 2. Add zero-mean Gaussian noise (std = 0.15m) to the scan
        noise = rng.normal(0.0, 0.15, size=360).astype(np.float32)
        noisy_scan = np.clip(rect_scan + noise, 0.10, 8.0)

        match_res = engine.recognize_scan(noisy_scan)
        assert match_res["matched"]
        assert match_res["room_name"] == "salotto_rettangolare"
        assert match_res["ncc_score"] > 0.85
        assert match_res["fourier_distance"] < 0.20

    def test_rotation_invariance_and_yaw_recovery(self):
        """Strict rotation-invariance of 1D Fourier spectrum and exact circular NCC yaw."""
        # Asymmetric rectangular room (7.0m x 3.0m) with asymmetric alcove
        angles = np.linspace(0, 2.0 * math.pi, 360, endpoint=False)
        base_scan = np.zeros(360, dtype=np.float32)
        for i, th in enumerate(angles):
            cos_t = max(abs(math.cos(th)), 1e-4)
            sin_t = max(abs(math.sin(th)), 1e-4)
            base_scan[i] = min(3.5 / cos_t, 1.5 / sin_t)
        # Add asymmetric alcove on positive X wall to break 180-degree symmetry
        base_scan[350:360] += 0.8
        base_scan[0:15] += 0.8

        desc_ref = compute_fourier_descriptor(base_scan, num_harmonics=32)

        # Test across various arbitrary rotation angles (including 45°, 90°, 180°, 270°)
        test_rot_degrees = [30, 45, 90, 137, 180, 270]
        for deg in test_rot_degrees:
            shift_bins = int(round(deg)) % 360
            rotated_scan = np.roll(base_scan, shift_bins)

            # 1. Rotation Invariance Check: Fourier magnitude spectrum must match ref
            desc_rot = compute_fourier_descriptor(rotated_scan, num_harmonics=32)
            fourier_dist = float(np.linalg.norm(desc_rot - desc_ref))
            assert fourier_dist < 1e-4, f"Fourier descriptor not rotation invariant at {deg} deg (dist={fourier_dist})"

            # 2. Circular NCC Yaw Offset Check: Must recover the exact shift
            score, yaw_off = compute_circular_ncc(rotated_scan, base_scan)
            assert score > 0.99
            expected_yaw = float(shift_bins * (2.0 * math.pi / 360.0))
            if expected_yaw > math.pi:
                expected_yaw -= 2.0 * math.pi
            assert abs(yaw_off - expected_yaw) < 0.03, f"Yaw offset recovery failed for {deg} deg"

    def test_extreme_scan_densities_and_invalid_inputs(self):
        """Sparse scans (12 rays), dense scans (1440 rays), and length verification."""
        # 1. Very sparse scan: 12 rays spanning 360 degrees
        sparse_ranges = [2.5] * 12
        resampled_sparse = resample_scan_to_360_bins(sparse_ranges, range_min=0.10, range_max=8.0)
        assert len(resampled_sparse) == 360
        assert np.allclose(resampled_sparse, 2.5)

        # 2. Very dense scan: 1440 rays (0.25 deg resolution)
        dense_ranges = [3.0] * 1440
        resampled_dense = resample_scan_to_360_bins(dense_ranges, range_min=0.10, range_max=8.0)
        assert len(resampled_dense) == 360
        assert np.allclose(resampled_dense, 3.0)

        # 3. Invalid array size passed to compute_fourier_descriptor
        with pytest.raises(ValueError, match="Input must have exactly 360 bins"):
            compute_fourier_descriptor(np.ones(359, dtype=np.float32))

        with pytest.raises(ValueError, match="Input must have exactly 360 bins"):
            compute_fourier_descriptor(np.ones(361, dtype=np.float32))


# ============================================================================
# 4. ASR Dialogue Fuzzing & VUI Safety Gates (VUI Engine & Confirmation Gate)
# ============================================================================
class TestTier5ASRDialogueFuzzingVUI:
    """
    White-box stress tests for Italian natural situational queries, dialectal/slang fuzzing,
    contradictory utterance handling, BUG-HRI-01 negation priority, and acoustic echo suppression.
    """

    def test_italian_situational_queries_and_dialects(self):
        """Location, map, and vision queries with dialectal variations and noise."""
        engine = VUIDialogueEngine()
        engine.update_cag_location(
            room_name="salotto",
            location=(3.2, 1.8),
            map_name="appartamento_piano1",
            covariance_trace=0.045,
            visual_detections=["tavolo", "lampada", "sedia"]
        )

        # 1. Canonical and colloquial location queries
        valid_location_queries = [
            "Dove ti trovi?",
            "dove sei?",
            "In che stanza sei?",
            "in che stanza ti trovi?",
            "in quale stanza ti trovi adesso?",
            "DOVE TI TROVI???",
            "  dove   sei   ?! ",
        ]
        for q in valid_location_queries:
            reply = engine.handle_query(q)
            assert "Mi trovo in salotto" in reply
            assert "appartamento_piano1" in reply

        # 2. Map queries
        valid_map_queries = [
            "In quale mappa stai navigando?",
            "in che mappa navighi?",
            "quale mappa?",
            "IN QUALE MAPPA SEI?",
        ]
        for q in valid_map_queries:
            reply = engine.handle_query(q)
            assert "appartamento_piano1" in reply
            assert "eccellente" in reply  # covariance 0.045 < 0.08

        # 3. Vision queries
        valid_vision_queries = [
            "Cosa vedi?",
            "cosa stai vedendo?",
            "cosa c'è davanti a te?",
            "COSA VEDI???",
        ]
        for q in valid_vision_queries:
            reply = engine.handle_query(q)
            assert "tavolo" in reply
            assert "lampada" in reply
            assert "sedia" in reply

        # 4. Unrecognized Italian slang / dialect fails safe to friendly guidance
        unrecognized_fuzzing = [
            "ndo stai de casa?",
            "ndo te trovi cumpà?",
            "che stai a fa?",
            "dimmi 'na poesia",
            "qual è il senso della vita?",
            "",
            "   ",
        ]
        for q in unrecognized_fuzzing:
            reply = engine.handle_query(q)
            assert "Non ho compreso la domanda" in reply

    def test_multiple_contradictory_intents_in_single_utterance(self):
        """Compound queries and contradictory intents in dialogue and confirmation gates."""
        vui = VUIDialogueEngine()
        vui.update_cag_location("cucina", (1.0, 2.0), "mappa1", 0.05)

        # Multi-intent compound: "Dove ti trovi e in quale mappa navighi?"
        # Location intent matches first in evaluation chain
        reply = vui.handle_query("Dove ti trovi e in quale mappa navighi?")
        assert "Mi trovo in cucina" in reply

        # Destructive Confirmation Gate Contradictions
        gate = DestructiveConfirmationGate()
        gate.request_action("cucina", "MAP_OVERWRITE")
        assert gate.pending

        # 1. Contradictory utterance containing BOTH affirmative and negation:
        # e.g., "sì, confermo... no, aspetta annulla"
        # BUG-HRI-01 Negation Priority dictates that ANY negation cancels immediately!
        res_text, confirmed = gate.process_voice_input("sì, confermo... no, aspetta annulla")
        assert not confirmed
        assert not gate.pending
        assert "annullata su richiesta" in res_text

        # Re-arm gate
        gate.request_action("cucina", "MAP_OVERWRITE")

        # 2. "non confermo" (contains affirmative substring 'confermo' prefixed by 'non')
        # Negation priority MUST prevent execution
        res_text2, confirmed2 = gate.process_voice_input("non confermo")
        assert not confirmed2
        assert not gate.pending
        assert "annullata su richiesta" in res_text2

        # Re-arm gate
        gate.request_action("cucina", "MAP_OVERWRITE")

        # 3. "confermo... no, stop!"
        res_text3, confirmed3 = gate.process_voice_input("confermo... no, stop!")
        assert not confirmed3
        assert not gate.pending
        assert "annullata" in res_text3

    def test_destructive_confirmation_gate_vague_rejection_and_strict_affirmative(self):
        """Vague affirmations rejected while strict affirmations execute."""
        gate = DestructiveConfirmationGate()
        gate.request_action("camera_da_letto", "MAP_DELETE")
        assert gate.pending

        # Vague affirmative phrases MUST be rejected and leave gate pending
        vague_phrases = [
            "ok",
            "va bene",
            "procedi pure",
            "vai avanti",
            "chiaro",
            "d'accordo",
            "fai pure",
        ]
        for phrase in vague_phrases:
            reply, confirmed = gate.process_voice_input(phrase)
            assert not confirmed
            assert gate.pending
            assert "Risposta non sufficientemente esplicita" in reply

        # Strict affirmative phrase confirms
        reply, confirmed = gate.process_voice_input("sì, confermo")
        assert confirmed
        assert not gate.pending
        assert "Conferma registrata" in reply

    def test_destructive_confirmation_timer_virtual_clock_countdown(self):
        """Virtual clock countdown triggers cancellation at 30.0s."""
        gate = DestructiveConfirmationGate()
        gate.request_action("studio", "MAP_OVERWRITE")
        assert gate.pending

        # Advance to 29.99s -> still pending
        msg_sub = gate.advance_time(29.99)
        assert msg_sub is None
        assert gate.pending

        # Advance another 0.02s -> reaches 30.01s -> timeout cancellation
        msg_timeout = gate.advance_time(0.02)
        assert msg_timeout is not None
        assert not gate.pending
        assert "Tempo scaduto" in msg_timeout

    def test_barge_in_attenuation_and_audio_echo_leakage(self):
        """SPEC-04 audio streaming rates and 0.1x barge-in gain protection."""
        engine = VUIDialogueEngine()

        # 1. Idle state: normal listening gain
        assert engine.stt_gain == 1.0

        # 2. TTS active: attenuation to 0.1x (-20 dB suppression of speaker audio)
        engine.set_tts_active(True)
        assert engine.stt_gain == 0.1

        # 3. TTS finished: restored to 1.0x
        engine.set_tts_active(False)
        assert engine.stt_gain == 1.0

        # 4. Audio streaming rate verification
        assert engine.verify_audio_resampling(16000, 48000) is True
        assert engine.verify_audio_resampling(16000, 16000) is False
        assert engine.verify_audio_resampling(44100, 48000) is False
        assert engine.verify_audio_resampling(48000, 48000) is False


# ============================================================================
# 5. Hybrid Navigation Visual Servoing (Hybrid Target Seeker)
# ============================================================================
class TestTier5HybridNavVisualServoingAdversarial:
    """
    White-box stress tests for hierarchical hybrid navigation (Nav2 -> NOMAD -> YOLO),
    visual servoing kinematics, target loss, sudden distance jumps, and boundary containment.
    """

    def test_target_disappearing_during_approach(self):
        """Visual servoing controller behavior when target candidate disappears."""
        engine = HybridTargetSeekerEngine()
        engine.start_mission("salotto", "persona")
        engine.notify_nav2_arrival()
        assert engine.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE

        # Sighting of person with conf 0.88 -> Phase 3
        detections = [{"class": "persona", "confidence": 0.88, "distance_m": 1.5}]
        sighting = engine.process_hailo_yolo_detections(detections)
        assert sighting is not None
        assert engine.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
        assert not engine.nomad_active

        # Subsequent detection cycle receives empty detection stream (target occluded / disappeared)
        detections_empty = []
        # In Phase 3, process_hailo_yolo_detections ignores Phase 2 detections
        assert engine.process_hailo_yolo_detections(detections_empty) is None

        # Kinematic controller deadband and centering checks:
        # Centered target (center_x = 320, width = 640): deadband (+-4%) yields zero angular velocity
        v_c, w_c, done_c = engine.compute_visual_servoing_twist(320.0, 640.0, 1.0)
        assert not done_c
        assert abs(w_c) == 0.0
        assert v_c > 0.0

        # Extreme left offset (center_x = 20): maximum positive angular velocity (turns left)
        v_l, w_l, done_l = engine.compute_visual_servoing_twist(20.0, 640.0, 1.0)
        assert w_l > 0.80
        assert w_l <= engine.MAX_ANGULAR_VELOCITY_VS

        # Extreme right offset (center_x = 620): maximum negative angular velocity (turns right)
        v_r, w_r, done_r = engine.compute_visual_servoing_twist(620.0, 640.0, 1.0)
        assert w_r < -0.80
        assert w_r >= -engine.MAX_ANGULAR_VELOCITY_VS

    def test_sudden_jumps_in_distance(self):
        """Sensor glitches, sudden obstacle drop-in, and extreme range jumps."""
        engine = HybridTargetSeekerEngine()
        engine.current_phase = HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
        engine.target_class = "sedia"

        # 1. Sudden drop: Target distance jumps from 1.5m to 0.15m (obstacle appears)
        # Visual servoing must immediately halt at <= 0.30m, play chime, and announce completion
        msg, is_done = engine.execute_visual_servoing_step(0.15)
        assert is_done
        assert engine.current_phase == HybridTargetSeekerEngine.PHASE_COMPLETED
        assert engine.chime_played
        assert engine.completion_announced
        assert "raggiunto con successo" in msg

        # Twist calculation at <= 0.30m must return full brake (v=0, w=0)
        v_stop, w_stop, done_stop = engine.compute_visual_servoing_twist(320.0, 640.0, 0.15)
        assert done_stop
        assert v_stop == 0.0
        assert w_stop == 0.0

        # Re-arm Phase 3 for sudden large distance jump
        engine.current_phase = HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING
        # 2. Sudden leap: Target distance jumps from 0.40m to 12.0m
        v_far, w_far, done_far = engine.compute_visual_servoing_twist(320.0, 640.0, 12.0)
        assert not done_far
        # Linear velocity MUST be strictly clamped to MAX_LINEAR_VELOCITY_VS (0.30 m/s)
        assert v_far <= engine.MAX_LINEAR_VELOCITY_VS
        assert abs(v_far - 0.30) < 1e-4

    def test_multilingual_synonyms_and_confidence_gating(self):
        """Multilingual synonym matching and confidence threshold boundary (0.55)."""
        engine = HybridTargetSeekerEngine()
        engine.current_phase = HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE
        engine.target_class = "tavolo"  # Italian query

        # Synonym matching: "dining table" in COCO 80 matches "tavolo"
        assert HybridTargetSeekerEngine._matches_target_class("dining table", "tavolo")
        assert HybridTargetSeekerEngine._matches_target_class("person", "persona")
        assert HybridTargetSeekerEngine._matches_target_class("chair", "sedia")
        assert HybridTargetSeekerEngine._matches_target_class("couch", "divano")

        # 1. Detection below threshold (0.54) -> REJECT
        det_low = [{"class": "dining table", "confidence": 0.54}]
        assert engine.process_hailo_yolo_detections(det_low) is None
        assert engine.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE

        # 2. Detection at threshold (0.55) -> ACCEPT
        det_thresh = [{"class": "dining table", "confidence": 0.55}]
        assert engine.process_hailo_yolo_detections(det_thresh) is not None
        assert engine.current_phase == HybridTargetSeekerEngine.PHASE_3_VISUAL_SERVOING

        # Re-arm Phase 2
        engine.current_phase = HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE
        # 3. Multiple candidates: must select the candidate with highest confidence
        det_multi = [
            {"class": "dining table", "confidence": 0.60, "id": 1},
            {"class": "chair", "confidence": 0.99, "id": 2},  # wrong class
            {"class": "dining table", "confidence": 0.92, "id": 3},  # best hit
        ]
        sighting = engine.process_hailo_yolo_detections(det_multi)
        assert sighting is not None
        assert engine.detected_target["confidence"] == 0.92

    def test_room_boundary_containment_zones(self):
        """3-Tier Room Boundary Containment: Free Zone, Soft Buffer, Hard Turnaround."""
        engine = HybridTargetSeekerEngine()
        # Define 5.0m x 4.0m rectangular room polygon
        engine.room_polygon = [(0.0, 0.0), (5.0, 0.0), (5.0, 4.0), (0.0, 4.0)]
        engine.room_centroid = (2.5, 2.0)

        # 1. Interior Free Zone (d > 0.50m from walls): e.g. at (2.5, 2.0)
        # Velocity passes through unhindered
        vx1, wz1, zone1 = engine.filter_nomad_velocity(
            nomad_vx=0.25, nomad_wz=0.10, robot_x=2.5, robot_y=2.0, robot_yaw=0.0
        )
        assert zone1 == "FREE_ZONE"
        assert abs(vx1 - 0.25) < 1e-4
        assert abs(wz1 - 0.10) < 1e-4

        # 2. Soft Buffer Zone (0.25m < d <= 0.50m): e.g. at (0.35, 2.0) -> dist to left wall is 0.35m
        # Forward speed must be attenuated, steering deflected inward
        vx2, wz2, zone2 = engine.filter_nomad_velocity(
            nomad_vx=0.25, nomad_wz=0.0, robot_x=0.35, robot_y=2.0, robot_yaw=math.pi  # facing wall
        )
        assert zone2 == "SOFT_BUFFER"
        assert vx2 < 0.25  # attenuated
        assert vx2 > 0.0

        # 3. Hard Turnaround (d <= 0.25m or outside room): e.g. at (-0.1, 2.0) [outside]
        # Forward speed halted, robot steers directly towards room centroid (2.5, 2.0)
        vx3, wz3, zone3 = engine.filter_nomad_velocity(
            nomad_vx=0.25, nomad_wz=0.0, robot_x=-0.1, robot_y=2.0, robot_yaw=math.pi  # facing away from room
        )
        assert zone3 == "HARD_TURNAROUND"
        assert vx3 == 0.0  # not yet aligned to centroid
        assert abs(wz3) > 0.0  # steering towards centroid

    def test_saccadic_sweeps_and_search_timeout(self):
        """Active saccadic sweeps every 12s / 1.5m and 120s search budget timeout."""
        engine = HybridTargetSeekerEngine(search_timeout_sec=120.0)
        engine.start_mission("salotto", "libro")
        engine.notify_nav2_arrival()
        assert engine.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE

        # Saccadic sweep by time: 12.0s elapsed
        t0 = engine.nomad_search_start_time
        sweep_cmd = engine.check_saccadic_sweep(now=t0 + 12.5)
        assert sweep_cmd is not None
        assert sweep_cmd[0] == 0.0
        assert sweep_cmd[1] == engine.SWEEP_YAW_RATE  # 0.45 rad/s

        # Saccadic sweep by distance: 1.6m traveled
        engine.check_saccadic_sweep(robot_x=0.0, robot_y=0.0, now=t0 + 13.0)
        sweep_dist = engine.check_saccadic_sweep(robot_x=1.6, robot_y=0.0, now=t0 + 14.0)
        assert sweep_dist is not None

        # Search timeout check at 119.0s -> NOT timed out
        assert not engine.check_search_timeout(now=t0 + 119.0)
        assert engine.current_phase == HybridTargetSeekerEngine.PHASE_2_NOMAD_REACTIVE

        # Search timeout check at 120.1s -> TIMED OUT
        assert engine.check_search_timeout(now=t0 + 120.1)
        assert engine.current_phase == HybridTargetSeekerEngine.PHASE_FAILED
        assert engine.failure_reason == "TARGET_NOT_FOUND"
        assert not engine.nomad_active
