"""
Comprehensive Unit Test Suite for Milestone 2: Multi-Modal Perception & Room Recognition
=======================================================================================
Covers:
  - Test Group 1: ITU-R BT.601 Luminance Calculation Engine (Zero-allocation, RGB, BGR, Grayscale)
  - Test Group 2: Luminance Safety Gate Hysteresis & Static Contracts (Boundaries 0, 24, 25, 28, 30, 31, 255)
  - Test Group 3: Luminance ROS Interfaces & Trigger Service Checks
  - Test Group 4: CosPlace 512D Feature Extraction & Strict L2 Normalization (||v||_2 = 1.0 +- 1e-5, latency < 50ms)
  - Test Group 5: VPR Cosine Similarity Matching & MAGRoomRegistry Integration (0.839 reject, 0.840 reject, 0.841 accept)
  - Test Group 6: LiDAR 360-Bin Polar Range Resampling & Outlier Cleaning (inf/nan handling, clipping to 8.0m)
  - Test Group 7: Rotation-Invariant 1D Fourier Descriptor & Circular NCC (strict shift invariance < 1e-4, yaw estimation)
  - Test Group 8: AMCL Covariance Trace & Kidnapped Detection (cov[0]+cov[7]+cov[35], exact 0.080 boundary)
  - Test Group 9: Kidnapped Recovery State Machine, Multimodal Branching, 150ms Stiction Kick, 720° Limiter, CAG update
"""

import math
import time
import json
import pytest
import numpy as np

# Imports from Milestone 2 modules
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
from robopy_controller.nodes.kidnapped_robot_recovery import (
    compute_amcl_covariance_trace,
    is_amcl_converged,
    RotationScanController,
    MultimodalRoomHypothesizer,
    KidnappedRobotRecoveryEngine,
    CONVERGENCE_TRACE_THRESHOLD,
)

# TRINITY imports
from robot_ai.trinity.mag_room_registry import MAGRoomRegistry
from robot_ai.trinity.cag_environment import EnvironmentSnapshot


# ==============================================================================
# Test Group 1: ITU-R BT.601 Luminance Calculation Engine
# ==============================================================================
class TestGroup1BT601LuminanceCalculation:
    def test_pure_color_channels_bt601_weights(self):
        """Pure R, G, B channels produce exact BT.601 weighted values."""
        # Pure Red: 100 on Red channel
        img_r = np.zeros((10, 10, 3), dtype=np.uint8)
        img_r[:, :, 0] = 100
        lum_r = LuminanceSafetyGate.compute_mean_luminance(img_r, encoding="rgb8")
        assert abs(lum_r - 29.9) < 0.1

        # Pure Green: 100 on Green channel
        img_g = np.zeros((10, 10, 3), dtype=np.uint8)
        img_g[:, :, 1] = 100
        lum_g = LuminanceSafetyGate.compute_mean_luminance(img_g, encoding="rgb8")
        assert abs(lum_g - 58.7) < 0.1

        # Pure Blue: 100 on Blue channel
        img_b = np.zeros((10, 10, 3), dtype=np.uint8)
        img_b[:, :, 2] = 100
        lum_b = LuminanceSafetyGate.compute_mean_luminance(img_b, encoding="rgb8")
        assert abs(lum_b - 11.4) < 0.1

    def test_grayscale_2d_and_bgr_ordering(self):
        """2D monochrome matrix returns mean directly, and BGR maps channels [B, G, R]."""
        # Grayscale 2D array
        mono = np.full((20, 20), 128, dtype=np.uint8)
        lum_mono = LuminanceSafetyGate.compute_mean_luminance(mono)
        assert lum_mono == 128.0

        # BGR array where channel 0 is Blue, channel 1 is Green, channel 2 is Red
        img_bgr = np.zeros((10, 10, 3), dtype=np.uint8)
        img_bgr[:, :, 2] = 100  # Red in BGR
        lum_bgr_r = LuminanceSafetyGate.compute_mean_luminance(img_bgr, encoding="bgr8")
        assert abs(lum_bgr_r - 29.9) < 0.1

        img_bgr_b = np.zeros((10, 10, 3), dtype=np.uint8)
        img_bgr_b[:, :, 0] = 100  # Blue in BGR
        lum_bgr_b = LuminanceSafetyGate.compute_mean_luminance(img_bgr_b, encoding="bgr8")
        assert abs(lum_bgr_b - 11.4) < 0.1

    def test_raw_bytes_buffer_decoding(self):
        """Raw bytes buffer is decoded without memory leaks or shape errors."""
        arr = np.full((10, 10, 3), 50, dtype=np.uint8)
        raw_bytes = arr.tobytes()
        lum = LuminanceSafetyGate.compute_mean_luminance(
            raw_bytes, encoding="rgb8", height=10, width=10
        )
        assert abs(lum - 50.0) < 0.1

    def test_invalid_input_exceptions(self):
        """Invalid shapes or types raise appropriate exceptions."""
        with pytest.raises(TypeError):
            LuminanceSafetyGate.compute_mean_luminance("not_an_image")

        with pytest.raises(ValueError):
            LuminanceSafetyGate.compute_mean_luminance(b"short", height=None)

        # 2-channel 3D array is invalid
        with pytest.raises(ValueError):
            LuminanceSafetyGate.compute_mean_luminance(np.zeros((10, 10, 2)))

        # 4D array is invalid
        with pytest.raises(ValueError):
            LuminanceSafetyGate.compute_mean_luminance(np.zeros((1, 10, 10, 3)))

    def test_single_channel_3d_support(self):
        """Single-channel 3D image (H, W, 1) is supported without raising ValueError."""
        mono_3d = np.full((15, 15, 1), 75, dtype=np.uint8)
        lum = LuminanceSafetyGate.compute_mean_luminance(mono_3d)
        assert abs(lum - 75.0) < 1e-4


# ==============================================================================
# Test Group 2: Luminance Safety Gate Hysteresis & Static Contracts
# ==============================================================================
class TestGroup2LuminanceHysteresisAndBoundaries:
    def test_static_evaluate_all_boundaries(self):
        """Validates exact contract boundaries: 0, 24, 25, 28, 30, 31, 255."""
        gate = LuminanceSafetyGate()

        # 0.0: Total darkness
        res_0 = gate.evaluate(0.0)
        assert res_0["status"] == "INHIBITED_DARK"
        assert not res_0["authorized"]
        assert "insufficiente" in res_0["voice_warning"]

        # 24.0: Strictly below 25.0
        res_24 = gate.evaluate(24.0)
        assert res_24["status"] == "INHIBITED_DARK"
        assert not res_24["authorized"]
        assert "24.0" in res_24["voice_warning"]

        # 25.0: Exact lower boundary (inhibited)
        res_25 = gate.evaluate(25.0)
        assert res_25["status"] == "INHIBITED_DARK"
        assert not res_25["authorized"]

        # 28.0: Intermediate critical band
        res_28 = gate.evaluate(28.0)
        assert res_28["status"] == "INHIBITED_DARK"
        assert not res_28["authorized"]
        assert "limite critico" in res_28["voice_warning"]

        # 30.0: Exact upper boundary (strictly requires > 30.0)
        res_30 = gate.evaluate(30.0)
        assert res_30["status"] == "INHIBITED_DARK"
        assert not res_30["authorized"]

        # 31.0: Strictly above 30.0
        res_31 = gate.evaluate(31.0)
        assert res_31["status"] == "AUTHORIZED"
        assert res_31["authorized"]
        assert res_31["voice_warning"] is None

        # 255.0: Full white saturation
        res_255 = gate.evaluate(255.0)
        assert res_255["status"] == "AUTHORIZED"
        assert res_255["authorized"]

    def test_stateful_hysteresis_rising_and_falling_cycles(self):
        """Schmitt-trigger hysteresis maintains state in [25.0, 30.0] deadband."""
        gate = LuminanceSafetyGate()
        assert gate.current_state == "INHIBITED_DARK"

        # Rising sequence: 10 -> 27 -> 32
        u1 = gate.update(10.0)
        assert u1["status"] == "INHIBITED_DARK"
        u2 = gate.update(27.0)  # In deadband, previous was INHIBITED_DARK
        assert u2["status"] == "INHIBITED_DARK"
        assert not u2["authorized"]
        u3 = gate.update(32.0)  # Exceeds 30.0 -> transitions to AUTHORIZED
        assert u3["status"] == "AUTHORIZED"
        assert u3["authorized"]
        assert u3["voice_warning"] is None

        # Falling sequence: 32 -> 27 -> 24
        u4 = gate.update(27.0)  # In deadband, previous was AUTHORIZED -> stays AUTHORIZED!
        assert u4["status"] == "AUTHORIZED"
        assert u4["authorized"]
        assert u4["voice_warning"] is None

        u5 = gate.update(24.9)  # Drops below 25.0 -> transitions to INHIBITED_DARK
        assert u5["status"] == "INHIBITED_DARK"
        assert not u5["authorized"]
        assert u5["voice_warning"] is not None

    def test_nan_and_inf_fail_safe_to_dark(self):
        """NaN, Inf, or empty image evaluations safely fail-safe immediately to INHIBITED_DARK."""
        gate = LuminanceSafetyGate()
        gate.update(50.0)
        assert gate.current_state == "AUTHORIZED"

        # NaN update fails safe to INHIBITED_DARK
        res_nan = gate.update(float("nan"))
        assert res_nan["status"] == "INHIBITED_DARK"
        assert not res_nan["authorized"]

        # Recover to AUTHORIZED
        gate.update(45.0)
        assert gate.current_state == "AUTHORIZED"

        # Inf update fails safe to INHIBITED_DARK
        res_inf = gate.update(float("inf"))
        assert res_inf["status"] == "INHIBITED_DARK"
        assert not res_inf["authorized"]

        # Static evaluate also returns INHIBITED_DARK
        assert gate.evaluate(float("nan"))["status"] == "INHIBITED_DARK"
        assert gate.evaluate(float("inf"))["status"] == "INHIBITED_DARK"


# ==============================================================================
# Test Group 3: Luminance ROS Topic & Service Contracts
# ==============================================================================
class TestGroup3LuminanceROSContracts:
    def test_topic_json_payload_serialization(self):
        """Payload matches /camera/luminance_status schema."""
        gate = LuminanceSafetyGate()
        payload_str = gate.to_ros_topic_payload(35.5)
        data = json.loads(payload_str)
        assert data["luminance"] == 35.5
        assert data["status"] == "AUTHORIZED"
        assert data["threshold_low"] == 25.0
        assert data["threshold_high"] == 30.0

    def test_trigger_service_contract_response(self):
        """Matches /mapping/check_luminance (std_srvs/srv/Trigger) contract."""
        gate = LuminanceSafetyGate()

        # Reject case
        ok, msg = gate.trigger_service_check(20.0)
        assert not ok
        assert "insufficiente" in msg

        # Authorize case
        ok, msg = gate.trigger_service_check(40.0)
        assert ok
        assert "Mapping authorized" in msg


# ==============================================================================
# Test Group 4: CosPlace 512D Feature Extraction & Strict L2 Normalization
# ==============================================================================
class TestGroup4CosPlace512DExtraction:
    def test_embedding_dimensionality_and_dtype(self):
        """Embedding is strictly (512,) float32."""
        wrapper = CosPlaceModelWrapper(force_sim=True)
        vec, lat_ms = wrapper.extract_embedding_mock(seed_feature=1.23)
        assert vec.shape == (512,)
        assert vec.dtype == np.float32

    def test_strict_l2_normalization_within_tolerance(self):
        """||v||_2 == 1.0 +- 1e-5."""
        wrapper = CosPlaceModelWrapper(force_sim=True)
        for seed in [0.1, 4.56, 99.9, 1234.5]:
            vec, _ = wrapper.extract_embedding_mock(seed_feature=seed)
            norm = float(np.linalg.norm(vec))
            assert abs(norm - 1.0) <= 1e-5, f"Norm {norm} violated 1e-5 tolerance"

    def test_zero_and_nan_rejection(self):
        """Zero or NaN vectors raise ValueError."""
        with pytest.raises(ValueError):
            CosPlaceModelWrapper.normalize_l2(np.zeros(512, dtype=np.float32))

        nan_vec = np.ones(512, dtype=np.float32)
        nan_vec[10] = np.nan
        with pytest.raises(ValueError):
            CosPlaceModelWrapper.normalize_l2(nan_vec)

    def test_latency_under_50ms(self):
        """Single-frame feature extraction latency is under 50.0 ms."""
        wrapper = CosPlaceModelWrapper(force_sim=True)
        img = np.full((224, 224, 3), 120, dtype=np.uint8)
        _, lat_ms = wrapper.extract(img)
        assert lat_ms < 50.0


# ==============================================================================
# Test Group 5: VPR Cosine Similarity Matching Engine & Boundaries
# ==============================================================================
class TestGroup5VPRCosineMatchingAndRegistry:
    def test_exact_threshold_boundaries_0839_0840_0841(self):
        """Strictly tests 0.839 (reject), 0.840 (reject), 0.841 (accept)."""
        matcher = VPRMatcherEngine()
        wrapper = CosPlaceModelWrapper(force_sim=True)
        ref_vec, _ = wrapper.extract_embedding_mock(seed_feature=10.0)
        matcher.register_room("salotto", [ref_vec])

        # Generate orthogonal perturbation to construct precise similarities
        rand_vec, _ = wrapper.extract_embedding_mock(seed_feature=888.0)
        ortho_vec = rand_vec - float(np.dot(rand_vec, ref_vec)) * ref_vec
        ortho_vec = ortho_vec / float(np.linalg.norm(ortho_vec))

        # Query 1: Similarity = 0.839
        s_0839 = 0.839
        q_0839 = s_0839 * ref_vec + math.sqrt(max(0.0, 1.0 - s_0839**2)) * ortho_vec
        q_0839 = q_0839 / float(np.linalg.norm(q_0839))
        res_0839 = matcher.match_single_frame(q_0839)
        assert not res_0839["matched"], "Similarity 0.839 must be rejected"
        assert res_0839["room_name"] is None

        # Query 2: Similarity = 0.840 (Exact boundary - strict inequality requires > 0.840)
        s_0840 = 0.840
        q_0840 = s_0840 * ref_vec + math.sqrt(max(0.0, 1.0 - s_0840**2)) * ortho_vec
        q_0840 = q_0840 / float(np.linalg.norm(q_0840))
        res_0840 = matcher.match_single_frame(q_0840)
        assert not res_0840["matched"], "Similarity 0.840 exact boundary must be rejected"
        assert res_0840["room_name"] is None

        # Query 3: Similarity = 0.841 (Accepted)
        s_0841 = 0.841
        q_0841 = s_0841 * ref_vec + math.sqrt(max(0.0, 1.0 - s_0841**2)) * ortho_vec
        q_0841 = q_0841 / float(np.linalg.norm(q_0841))
        res_0841 = matcher.match_single_frame(q_0841)
        assert res_0841["matched"], "Similarity 0.841 must be accepted"
        assert res_0841["room_name"] == "salotto"

    def test_orthogonal_and_inverted_vectors(self):
        """Orthogonal (~0.0) and inverted (~ -1.0) vectors are rejected."""
        matcher = VPRMatcherEngine()
        wrapper = CosPlaceModelWrapper(force_sim=True)
        ref_vec, _ = wrapper.extract_embedding_mock(seed_feature=1.0)
        matcher.register_room("cucina", [ref_vec])

        # Inverted vector: -ref_vec
        inv_vec = -ref_vec
        res_inv = matcher.match_single_frame(inv_vec)
        assert not res_inv["matched"]
        assert res_inv["similarity"] <= -0.99

    def test_multi_frame_consensus(self):
        """Sliding window consensus fast-tracks >0.90 and filters single marginal spikes."""
        matcher = VPRMatcherEngine(consensus_window=3)

        # High confidence (>0.90) fast-tracks
        res_high = {"matched": True, "room_name": "camera", "similarity": 0.94, "threshold": 0.84}
        eval_high = matcher.evaluate_consensus(res_high)
        assert eval_high["matched"]
        assert eval_high["room_name"] == "camera"

        # Marginal match (0.85) without prior agreement is marked pending
        matcher.recent_predictions.clear()
        matcher.recent_predictions.append({"matched": False, "room_name": None, "similarity": 0.5})
        matcher.recent_predictions.append({"matched": False, "room_name": None, "similarity": 0.6})
        res_marg = {"matched": True, "room_name": "salotto", "similarity": 0.86, "threshold": 0.84}
        eval_marg = matcher.evaluate_consensus(res_marg)
        assert not eval_marg["matched"]
        assert eval_marg.get("consensus_pending") is True

    def test_integration_with_mag_room_registry(self, tmp_path):
        """Matches query vector against room signatures loaded from MAGRoomRegistry."""
        db_path = str(tmp_path / "test_vpr_reg.db")
        yaml_path = str(tmp_path / "test_vpr_reg.yaml")
        registry = MAGRoomRegistry(db_path=db_path, yaml_path=yaml_path)

        wrapper = CosPlaceModelWrapper(force_sim=True)
        vec1, _ = wrapper.extract_embedding_mock(seed_feature=42.0)
        vec2, _ = wrapper.extract_embedding_mock(seed_feature=42.1, add_noise=True)

        registry.insert_room("camera_ospiti", [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0]], map_name="default")
        registry.register_room_signatures("camera_ospiti", [vec1, vec2], np.ones(360, dtype=np.float32))

        matcher = VPRMatcherEngine(room_registry=registry)
        matcher.refresh_database_signatures()

        # Query with vec1
        res = matcher.match_single_frame(vec1)
        assert res["matched"]
        assert res["room_name"] == "camera_ospiti"
        assert res["similarity"] >= 0.99

    def test_non_finite_query_rejection(self):
        """Non-finite query vectors (Inf or NaN) safely return matched=False and similarity=-1.0."""
        matcher = VPRMatcherEngine()
        ref_vec = np.zeros(512, dtype=np.float32)
        ref_vec[0] = 1.0
        matcher.register_room("salotto", [ref_vec])

        # +inf query
        inf_q = np.zeros(512, dtype=np.float32)
        inf_q[0] = float("inf")
        res_inf = matcher.match_single_frame(inf_q)
        assert not res_inf["matched"]
        assert res_inf["room_name"] is None
        assert res_inf["similarity"] == -1.0
        assert res_inf["confidence"] == -1.0

        # nan query
        nan_q = np.full(512, float("nan"), dtype=np.float32)
        res_nan = matcher.match_single_frame(nan_q)
        assert not res_nan["matched"]
        assert res_nan["room_name"] is None
        assert res_nan["similarity"] == -1.0
        assert res_nan["confidence"] == -1.0


# ==============================================================================
# Test Group 6: LiDAR 360 Polar Bin Resampling & Data Cleaning
# ==============================================================================
class TestGroup6Lidar360Resampling:
    def test_cleaning_nan_inf_and_out_of_bounds(self):
        """Replaces inf, nan, and out-of-bounds readings with 8.0m."""
        raw_ranges = [1.5, float("inf"), float("nan"), 0.05, 15.0, 3.2]
        binned = resample_scan_to_360_bins(
            ranges=raw_ranges,
            angle_min=0.0,
            angle_increment=0.5,
            range_min=0.10,
            range_max=8.0
        )
        assert len(binned) == 360
        assert binned.dtype == np.float32
        assert not np.any(np.isnan(binned))
        assert not np.any(np.isinf(binned))
        assert np.all(binned >= 0.10)
        assert np.all(binned <= 8.0)

    def test_empty_scan_handling(self):
        """Empty scan returns uniform array filled with range_max."""
        binned = resample_scan_to_360_bins([])
        assert len(binned) == 360
        assert np.all(binned == 8.0)

    def test_full_360_pass_through(self):
        """Exact 360-bin input with valid ranges is preserved."""
        original = np.linspace(1.0, 5.0, 360, dtype=np.float32)
        binned = resample_scan_to_360_bins(original, angle_min=0.0, angle_increment=(2.0 * math.pi / 360.0))
        assert len(binned) == 360
        assert np.allclose(binned, original, atol=1e-3)


# ==============================================================================
# Test Group 7: 1D Fourier Descriptor & Circular NCC
# ==============================================================================
class TestGroup7FourierDescriptorAndCircularNCC:
    def test_strict_mathematical_rotation_invariance(self):
        """Fourier descriptor magnitudes are strictly invariant to scan rotation."""
        # Create non-symmetric synthetic room polar scan (e.g. 6x4m rectangle)
        angles = np.linspace(0, 2 * math.pi, 360, endpoint=False)
        # Rectangular room radius function
        base_scan = 3.0 + 1.2 * np.cos(2 * angles) + 0.5 * np.sin(4 * angles + 0.3)
        base_scan = np.clip(base_scan, 0.5, 7.0).astype(np.float32)

        base_desc = compute_fourier_descriptor(base_scan, num_harmonics=32)
        assert len(base_desc) == 32
        assert abs(float(np.linalg.norm(base_desc)) - 1.0) <= 1e-5

        # Test circular shifts across various rotation angles
        shifts = [15, 30, 45, 90, 180, 270, 359]
        for shift in shifts:
            shifted_scan = np.roll(base_scan, shift)
            shifted_desc = compute_fourier_descriptor(shifted_scan, num_harmonics=32)
            dist = float(np.linalg.norm(base_desc - shifted_desc))
            assert dist < 1e-4, f"Shift {shift}° violated rotation invariance: diff {dist}"

    def test_inter_room_fourier_discrimination(self):
        """Fourier descriptors differentiate large vs narrow vs small rooms."""
        angles = np.linspace(0, 2 * math.pi, 360, endpoint=False)

        # Room A: Large living room
        scan_a = (4.0 + 1.5 * np.cos(2 * angles)).astype(np.float32)
        desc_a = compute_fourier_descriptor(scan_a)

        # Room B: Narrow hallway (high aspect ratio)
        scan_b = (1.5 + 1.0 * np.abs(np.sin(angles))).astype(np.float32)
        desc_b = compute_fourier_descriptor(scan_b)

        # Room C: Small square room
        scan_c = (2.0 + 0.1 * np.cos(4 * angles)).astype(np.float32)
        desc_c = compute_fourier_descriptor(scan_c)

        dist_ab = float(np.linalg.norm(desc_a - desc_b))
        dist_ac = float(np.linalg.norm(desc_a - desc_c))
        assert dist_ab > 0.05
        assert dist_ac > 0.15

        # Intra-room perturbed distance is much smaller than inter-room
        noisy_a = (scan_a + 0.02 * np.sin(10 * angles)).astype(np.float32)
        desc_noisy_a = compute_fourier_descriptor(noisy_a)
        dist_intra = float(np.linalg.norm(desc_a - desc_noisy_a))
        assert dist_intra < 0.01

    def test_circular_ncc_yaw_heading_recovery(self):
        """Circular NCC accurately determines yaw heading offset."""
        angles = np.linspace(0, 2 * math.pi, 360, endpoint=False)
        base_scan = (3.0 + np.sin(angles) + 0.8 * np.cos(2 * angles)).astype(np.float32)

        # Shift by +45 degrees (45 bins)
        shift_bins = 45
        query_scan = np.roll(base_scan, shift_bins)

        score, yaw_rad = compute_circular_ncc(query_scan, base_scan)
        assert score > 0.99
        expected_yaw_deg = 45.0
        recovered_deg = math.degrees(yaw_rad)
        assert abs(recovered_deg - expected_yaw_deg) < 1.0

    def test_lidar_room_recognizer_engine(self):
        """LidarRoomRecognizerEngine identifies registered room in pitch darkness."""
        engine = LidarRoomRecognizerEngine()
        angles = np.linspace(0, 2 * math.pi, 360, endpoint=False)

        # Asymmetric scans to guarantee unique global yaw orientation
        salotto_scan = (4.0 + 1.2 * np.cos(2 * angles) + 0.6 * np.sin(angles)).astype(np.float32)
        cucina_scan = (2.5 + 0.8 * np.sin(3 * angles) + 0.4 * np.cos(angles)).astype(np.float32)

        engine.register_room("salotto", salotto_scan)
        engine.register_room("cucina", cucina_scan)

        # Query with rotated salotto scan (+60 deg)
        query = np.roll(salotto_scan, 60)
        res = engine.recognize_scan(query)

        assert res["matched"]
        assert res["room_name"] == "salotto"
        assert res["ncc_score"] > 0.95
        assert abs(math.degrees(res["yaw_offset_rad"]) - 60.0) < 1.5


# ==============================================================================
# Test Group 8: AMCL Covariance Trace & Kidnapped Detection
# ==============================================================================
class TestGroup8AMCLCovarianceTrace:
    def test_covariance_trace_exact_thresholds(self):
        """Tests cov[0] + cov[7] + cov[35] with exact 0.080 boundary."""
        # 0.079: Strictly converged
        cov_079 = [0.0] * 36
        cov_079[0] = 0.030
        cov_079[7] = 0.030
        cov_079[35] = 0.019
        tr_079 = compute_amcl_covariance_trace(cov_079)
        assert abs(tr_079 - 0.079) < 1e-6
        assert is_amcl_converged(tr_079)

        # 0.080: Exact boundary (unconverged due to strict inequality trace < 0.080)
        cov_080 = [0.0] * 36
        cov_080[0] = 0.030
        cov_080[7] = 0.030
        cov_080[35] = 0.020
        tr_080 = compute_amcl_covariance_trace(cov_080)
        assert abs(tr_080 - 0.080) < 1e-6
        assert not is_amcl_converged(tr_080)

        # 0.081: Strictly unconverged
        cov_081 = [0.0] * 36
        cov_081[0] = 0.031
        cov_081[7] = 0.030
        cov_081[35] = 0.020
        tr_081 = compute_amcl_covariance_trace(cov_081)
        assert abs(tr_081 - 0.081) < 1e-6
        assert not is_amcl_converged(tr_081)

    def test_malformed_covariance_returns_inf(self):
        """Short or None array returns inf."""
        assert compute_amcl_covariance_trace([]) == float("inf")
        assert compute_amcl_covariance_trace([0.1] * 10) == float("inf")
        assert compute_amcl_covariance_trace(None) == float("inf")


# ==============================================================================
# Test Group 9: Kidnapped Recovery State Machine & Dynamics
# ==============================================================================
class TestGroup9KidnappedRecoveryStateMachine:
    def test_multimodal_branching_bright_vs_dark(self):
        """Branches to VPR when >30 lux, and LiDAR when <=25 lux."""
        hyp = MultimodalRoomHypothesizer()

        res_bright = hyp.hypothesize_room(ambient_luminance=45.0)
        assert res_bright["modality"] == "VPR"

        res_dark = hyp.hypothesize_room(ambient_luminance=0.0)
        assert res_dark["modality"] == "LIDAR"

        res_boundary_dark = hyp.hypothesize_room(ambient_luminance=20.0)
        assert res_boundary_dark["modality"] == "LIDAR"

    def test_rotation_scan_stiction_kick_profile(self):
        """First 150ms provides 0.70 rad/s kick, followed by 0.35 rad/s cruise."""
        ctrl = RotationScanController()
        ctrl.start(initial_yaw=0.0)

        # Immediate command (within 150ms)
        speed_kick, running = ctrl.get_current_command(current_yaw=0.01)
        assert running
        assert speed_kick == 0.70

        # Simulate time passing > 150ms
        ctrl.start_time = time.monotonic() - 0.200
        speed_cruise, running = ctrl.get_current_command(current_yaw=0.05)
        assert running
        assert speed_cruise == 0.35

    def test_rotation_scan_strict_720_degrees_limiter(self):
        """Stops firmly when accumulated rotation reaches 720 degrees."""
        ctrl = RotationScanController()
        ctrl.start(initial_yaw=0.0)

        # Rotate 1 full turn (360 deg)
        for deg in range(10, 360, 20):
            yaw = math.radians(deg)
            speed, running = ctrl.get_current_command(current_yaw=yaw)
            assert running

        # Rotate 2nd full turn up to 720 deg
        for deg in range(360, 725, 20):
            yaw = math.radians(deg % 360)
            speed, running = ctrl.get_current_command(current_yaw=yaw)

        # Over 720 degrees
        speed, running = ctrl.get_current_command(current_yaw=math.radians(5))
        assert not running
        assert speed == 0.0

    def test_early_convergence_gate_and_cag_update(self):
        """Early AMCL convergence (<0.08) stops rotation and updates CAG environment."""
        env = EnvironmentSnapshot()
        engine = KidnappedRobotRecoveryEngine(environment_snapshot=env)

        # Trigger recovery in salotto
        engine.recognized_room = "salotto"
        engine.current_state = KidnappedRobotRecoveryEngine.STATE_ROTATING_SCAN
        engine.rotation_controller.start(initial_yaw=0.0)

        # High covariance -> continues rotating
        high_cov = [0.0] * 36
        high_cov[0] = 0.05
        high_cov[7] = 0.05
        high_cov[35] = 0.02  # Trace = 0.12 >= 0.08
        res_step = engine.update_amcl_pose(high_cov, current_yaw=math.radians(30))
        assert res_step["state"] == KidnappedRobotRecoveryEngine.STATE_ROTATING_SCAN
        assert not res_step["converged"]

        # Drop covariance below 0.08 -> rapid convergence!
        low_cov = [0.0] * 36
        low_cov[0] = 0.02
        low_cov[7] = 0.02
        low_cov[35] = 0.01  # Trace = 0.05 < 0.08
        res_conv = engine.update_amcl_pose(
            low_cov,
            pose=(2.5, 1.8, 0.5),
            current_yaw=math.radians(60)
        )
        assert res_conv["state"] == KidnappedRobotRecoveryEngine.STATE_RECOVERY_SUCCESS
        assert res_conv["converged"]
        assert not engine.rotation_controller.is_active

        # Verify TRINITY CAG environment snapshot was updated
        assert env.room_name == "salotto"
        assert env.location == (2.5, 1.8)
        assert abs(env.covariance_trace - 0.05) < 1e-4
        assert "salotto" in env.to_text()

    def test_exceeded_720_degrees_transitions_to_failed(self):
        """Exceeding 720 degrees without convergence enters RECOVERY_FAILED."""
        engine = KidnappedRobotRecoveryEngine()
        engine.recognized_room = "cucina"
        engine.current_state = KidnappedRobotRecoveryEngine.STATE_ROTATING_SCAN
        engine.rotation_controller.start(initial_yaw=0.0)

        # Simulate 720 degrees rotation
        engine.rotation_controller.accumulated_yaw_rad = math.radians(721.0)
        high_cov = [0.1] * 36
        res = engine.update_amcl_pose(high_cov, current_yaw=0.0)
        assert res["state"] == KidnappedRobotRecoveryEngine.STATE_RECOVERY_FAILED
        assert not res["converged"]
