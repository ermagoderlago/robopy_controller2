"""
Adversarial Stress Test Suite for Milestone 2:
=============================================
Focus: LiDAR Fourier Invariance, Polar Resampling, and Kidnapped Robot Auto-Recovery.

Empirical verification coverage:
1. Adversarial LiDAR Polar Resampling & Corrupt Inputs:
   - 100% inf, 100% nan, all zeros (below range_min).
   - Extreme ray counts: 10 rays, 3600 rays, 1 ray, 0 rays (empty scan).
   - Negative angle wrapping: angle_min = -pi, angle_max = +pi.
   - Descriptor and NCC numerical stability under flat / corrupt inputs.
2. Mathematical & Empirical Fourier Magnitude Rotation Invariance:
   - Complex non-circular room range profiles: L-shaped room, narrow corridor, asymmetric polygon.
   - Rotational shifts: 1°, 15°, 47°, 90°, 180°, 270°, 359°.
   - Machine precision invariance assertion: ||F(R') - F(R)||_2 < 1e-6.
3. Circular NCC Yaw Estimation Accuracy:
   - Full 360-degree sweep: all integer shifts in [0..359] degrees.
   - Sub-degree fractional shifts via continuous scan resampling.
   - Noise robustness: additive Gaussian noise up to 10cm.
4. AMCL Covariance Trace Exact Float Boundaries:
   - Strict boundary tests: trace = 0.0799 (True), 0.0800 (False), 0.0801 (False).
   - Sub-epsilon floating point behavior.
   - Malformed, short, or non-sequence covariance payloads.
5. Kidnapped Recovery Rotation Turn Limiter:
   - Continuous high covariance simulation: strictly halts at <= 720.0° (2 full turns).
   - Stiction kick profile (0.70 rad/s for first 150ms, then 0.35 rad/s cruise).
   - Zero-velocity latching after turn limit exhaustion.
6. Rapid Convergence Early Stop:
   - Injects trace < 0.080 at 45° and 90° rotation.
   - Immediate halt verification (commanded angular velocity drops to 0.0).
   - Verification of TRINITY CAG environment snapshot location update.
7. Multimodal Perception Branching:
   - lux = 0.0 (total darkness) -> selects LiDAR ToF recognizer.
   - lux = 50.0 (bright) -> selects VPR CosPlace.
   - Boundary tests at 25.0 and 30.0 lux.
   - Full pitch-dark kidnapped recovery integration test.
"""

import os
import sys
import math
import time
from typing import List, Tuple, Dict, Any

import pytest
import numpy as np

# Ensure robopy_controller is on path
current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
robopy_path = os.path.join(workspace_root, "robopy_controller")
if robopy_path not in sys.path:
    sys.path.insert(0, robopy_path)

from nodes.lidar_room_recognizer import (
    resample_scan_to_360_bins,
    compute_fourier_descriptor,
    compute_circular_ncc,
    LidarRoomRecognizerEngine
)
from nodes.kidnapped_robot_recovery import (
    compute_amcl_covariance_trace,
    is_amcl_converged,
    RotationScanController,
    MultimodalRoomHypothesizer,
    KidnappedRobotRecoveryEngine,
    CONVERGENCE_TRACE_THRESHOLD
)
from robot_ai.trinity.cag_environment import EnvironmentSnapshot


# ==============================================================================
# Helpers: Synthetic Room Range Profile Generators
# ==============================================================================

def generate_l_shaped_room_scan() -> np.ndarray:
    """
    Generates a 360-bin range profile for an L-shaped room (e.g. 8x6m with a 4x3m corner notch).
    Viewpoint located at (2.5, 2.0) inside the room.
    """
    angles = np.linspace(0, 2.0 * math.pi, 360, endpoint=False)
    # Parametric model representing L-shaped boundary variations
    ranges = (
        3.2
        + 1.4 * np.sin(angles)
        + 0.9 * np.cos(2.0 * angles)
        + 0.6 * np.sin(3.0 * angles + 0.4)
        + 0.3 * np.cos(4.0 * angles - 0.2)
    )
    return np.clip(ranges, 0.8, 7.5).astype(np.float32)


def generate_narrow_corridor_scan() -> np.ndarray:
    """
    Generates a 360-bin range profile for a long narrow hallway (1.2m wide, 10m long).
    Viewpoint near middle of corridor.
    """
    angles = np.linspace(0, 2.0 * math.pi, 360, endpoint=False)
    # High range at 0° and 180° (along corridor axis), low range at 90° and 270° (walls)
    ranges = 0.6 / (np.abs(np.sin(angles)) + 0.10)
    return np.clip(ranges, 0.6, 7.8).astype(np.float32)


def generate_asymmetric_polygon_scan() -> np.ndarray:
    """
    Generates a 360-bin range profile for a highly irregular, asymmetric room.
    Guarantees no rotational symmetry (unique NCC peak across all 360 degrees).
    """
    angles = np.linspace(0, 2.0 * math.pi, 360, endpoint=False)
    ranges = (
        3.5
        + 1.35 * np.sin(angles)
        + 0.85 * np.cos(2.0 * angles + 0.35)
        + 0.55 * np.sin(3.0 * angles + 0.75)
        + 0.35 * np.cos(5.0 * angles - 0.25)
        + 0.15 * np.sin(7.0 * angles + 1.10)
    )
    return np.clip(ranges, 0.5, 7.8).astype(np.float32)


# ==============================================================================
# 1. Adversarial Polar Resampling & Corrupt Inputs
# ==============================================================================

class TestAdversarialPolarResamplingCorruptInputs:
    """Stress-tests resample_scan_to_360_bins under pathological and corrupted LaserScans."""

    def test_100_percent_inf_scan(self):
        """100% inf readings must be safely imputed to range_max (8.0m)."""
        scan_inf = [float("inf")] * 360
        binned = resample_scan_to_360_bins(scan_inf, range_max=8.0)
        assert binned.shape == (360,)
        assert binned.dtype == np.float32
        assert not np.isnan(binned).any()
        assert not np.isinf(binned).any()
        assert np.all(binned == 8.0)

    def test_100_percent_nan_scan(self):
        """100% nan readings must be safely imputed to range_max (8.0m)."""
        scan_nan = [float("nan")] * 360
        binned = resample_scan_to_360_bins(scan_nan, range_max=8.0)
        assert binned.shape == (360,)
        assert binned.dtype == np.float32
        assert not np.isnan(binned).any()
        assert not np.isinf(binned).any()
        assert np.all(binned == 8.0)

    def test_all_zeros_scan(self):
        """All zero readings (below range_min = 0.10) must be imputed to range_max."""
        scan_zeros = [0.0] * 360
        binned = resample_scan_to_360_bins(scan_zeros, range_min=0.10, range_max=8.0)
        assert binned.shape == (360,)
        assert np.all(binned == 8.0)

    def test_extreme_low_ray_count_10_rays(self):
        """Sparse scan with only 10 rays must interpolate smoothly to 360 bins without NaNs."""
        angles_10 = np.linspace(0, 2.0 * math.pi, 10, endpoint=False)
        ranges_10 = [2.0 + 0.5 * math.sin(a) for a in angles_10]
        binned = resample_scan_to_360_bins(ranges_10, angle_min=0.0)
        assert binned.shape == (360,)
        assert not np.isnan(binned).any()
        assert (binned >= 1.0).all() and (binned <= 3.0).all()

    def test_extreme_high_ray_count_3600_rays(self):
        """Dense scan with 3600 rays (0.1 deg) must downsample accurately to 360 bins."""
        angles_3600 = np.linspace(0, 2.0 * math.pi, 3600, endpoint=False)
        ranges_3600 = [3.0 + math.sin(a) for a in angles_3600]
        binned = resample_scan_to_360_bins(ranges_3600, angle_min=0.0)
        assert binned.shape == (360,)
        assert not np.isnan(binned).any()
        # Verify fidelity at 90° (pi/2) -> range ~ 4.0
        assert abs(binned[90] - 4.0) < 0.05
        # Verify fidelity at 270° (3pi/2) -> range ~ 2.0
        assert abs(binned[270] - 2.0) < 0.05

    def test_degenerate_ray_counts_empty_and_single_ray(self):
        """Empty scan and single-ray scan must be handled gracefully."""
        # Empty scan
        binned_empty = resample_scan_to_360_bins([], range_max=8.0)
        assert binned_empty.shape == (360,)
        assert np.all(binned_empty == 8.0)

        # Single ray scan
        binned_single = resample_scan_to_360_bins([3.5], range_max=8.0)
        assert binned_single.shape == (360,)
        assert not np.isnan(binned_single).any()
        assert np.all(binned_single == 3.5)

    def test_negative_angle_scan_wrapping_minus_pi_to_pi(self):
        """Standard ROS LaserScan spanning [-pi, +pi) must be correctly mapped to [0, 360)."""
        num_rays = 360
        angle_min = -math.pi
        angle_inc = (2.0 * math.pi) / num_rays
        # Ray at angle 0.0 (index 180 in -pi..pi) has range 5.0; others 2.0
        ranges = [2.0] * num_rays
        ranges[180] = 5.0  # Angle 0.0 rad

        binned = resample_scan_to_360_bins(
            ranges=ranges,
            angle_min=angle_min,
            angle_increment=angle_inc
        )
        assert binned.shape == (360,)
        assert not np.isnan(binned).any()
        # In 0..360 grid, bin 0 corresponds to angle 0.0 rad
        assert abs(binned[0] - 5.0) < 0.05

    def test_corrupt_scans_fourier_descriptor_stability(self):
        """Corrupt scans (all inf, all nan, all zeros) must yield valid normalized Fourier descriptors without NaN."""
        for name, scan in [
            ("inf", [float("inf")] * 360),
            ("nan", [float("nan")] * 360),
            ("zeros", [0.0] * 360)
        ]:
            binned = resample_scan_to_360_bins(scan)
            desc = compute_fourier_descriptor(binned)
            assert desc.shape == (32,), f"{name} failed shape"
            assert not np.isnan(desc).any(), f"{name} produced NaNs in Fourier descriptor"
            assert not np.isinf(desc).any(), f"{name} produced Infs in Fourier descriptor"
            # Since scan is constant flat 8.0, only DC harmonic (k=0) is non-zero, normalized to 1.0
            assert abs(desc[0] - 1.0) < 1e-4
            assert np.all(desc[1:] < 1e-5)

    def test_corrupt_flat_scans_circular_ncc_safety(self):
        """Circular NCC with zero-variance flat scans must return (0.0, 0.0) without ZeroDivisionError."""
        flat_scan = np.full(360, 8.0, dtype=np.float32)
        real_scan = generate_asymmetric_polygon_scan()

        score1, yaw1 = compute_circular_ncc(flat_scan, real_scan)
        assert score1 == 0.0
        assert yaw1 == 0.0

        score2, yaw2 = compute_circular_ncc(real_scan, flat_scan)
        assert score2 == 0.0
        assert yaw2 == 0.0

        score3, yaw3 = compute_circular_ncc(flat_scan, flat_scan)
        assert score3 == 0.0
        assert yaw3 == 0.0


# ==============================================================================
# 2. Adversarial Fourier Magnitude Rotation Invariance
# ==============================================================================

class TestAdversarialFourierRotationInvariance:
    """Stress-tests Fourier magnitude descriptor rotation invariance across complex room profiles and arbitrary shifts."""

    SHIFTS_DEG = [1, 15, 47, 90, 180, 270, 359]

    def test_l_shaped_room_profile_rotation_invariance(self):
        """L-shaped room range profile must satisfy ||F(R') - F(R)||_2 < 1e-6 across all shifts."""
        scan_ref = generate_l_shaped_room_scan()
        desc_ref = compute_fourier_descriptor(scan_ref)

        for shift in self.SHIFTS_DEG:
            scan_shifted = np.roll(scan_ref, shift)
            desc_shifted = compute_fourier_descriptor(scan_shifted)
            diff = float(np.linalg.norm(desc_shifted - desc_ref))
            assert diff < 1e-6, (
                f"L-shaped room Fourier invariance violated at shift {shift}°: "
                f"||F(R') - F(R)||_2 = {diff:.2e} >= 1e-6"
            )

    def test_narrow_hallway_corridor_rotation_invariance(self):
        """Narrow hallway range profile must satisfy ||F(R') - F(R)||_2 < 1e-6 across all shifts."""
        scan_ref = generate_narrow_corridor_scan()
        desc_ref = compute_fourier_descriptor(scan_ref)

        for shift in self.SHIFTS_DEG:
            scan_shifted = np.roll(scan_ref, shift)
            desc_shifted = compute_fourier_descriptor(scan_shifted)
            diff = float(np.linalg.norm(desc_shifted - desc_ref))
            assert diff < 1e-6, (
                f"Narrow hallway Fourier invariance violated at shift {shift}°: "
                f"||F(R') - F(R)||_2 = {diff:.2e} >= 1e-6"
            )

    def test_asymmetric_polygon_room_rotation_invariance(self):
        """Asymmetric polygon room range profile must satisfy ||F(R') - F(R)||_2 < 1e-6 across all shifts."""
        scan_ref = generate_asymmetric_polygon_scan()
        desc_ref = compute_fourier_descriptor(scan_ref)

        for shift in self.SHIFTS_DEG:
            scan_shifted = np.roll(scan_ref, shift)
            desc_shifted = compute_fourier_descriptor(scan_shifted)
            diff = float(np.linalg.norm(desc_shifted - desc_ref))
            assert diff < 1e-6, (
                f"Asymmetric polygon Fourier invariance violated at shift {shift}°: "
                f"||F(R') - F(R)||_2 = {diff:.2e} >= 1e-6"
            )

    def test_fourier_descriptor_norm_and_dimensions(self):
        """Fourier descriptor must strictly have shape (32,) and L2 norm 1.0 +- 1e-5."""
        for scan in [
            generate_l_shaped_room_scan(),
            generate_narrow_corridor_scan(),
            generate_asymmetric_polygon_scan()
        ]:
            desc = compute_fourier_descriptor(scan, num_harmonics=32)
            assert desc.shape == (32,)
            norm = float(np.linalg.norm(desc))
            assert abs(norm - 1.0) < 1e-5

    def test_inter_room_fourier_discriminability(self):
        """Fourier descriptors of distinct rooms must be clearly separable (distance > 0.05)."""
        desc_l = compute_fourier_descriptor(generate_l_shaped_room_scan())
        desc_corridor = compute_fourier_descriptor(generate_narrow_corridor_scan())
        desc_poly = compute_fourier_descriptor(generate_asymmetric_polygon_scan())

        dist_l_corridor = float(np.linalg.norm(desc_l - desc_corridor))
        dist_l_poly = float(np.linalg.norm(desc_l - desc_poly))
        dist_corridor_poly = float(np.linalg.norm(desc_corridor - desc_poly))

        assert dist_l_corridor > 0.08, f"L vs Corridor distance too small: {dist_l_corridor}"
        assert dist_l_poly > 0.05, f"L vs Polygon distance too small: {dist_l_poly}"
        assert dist_corridor_poly > 0.08, f"Corridor vs Polygon distance too small: {dist_corridor_poly}"


# ==============================================================================
# 3. Adversarial Circular NCC Yaw Estimation
# ==============================================================================

class TestAdversarialCircularNCCYawEstimation:
    """Stress-tests circular NCC yaw estimation across all 360 degree shifts, noise, and sub-degree shifts."""

    def test_ncc_yaw_estimation_all_360_degree_shifts(self):
        """Tests circular NCC accuracy across every single integer shift in [0..359] degrees."""
        ref_scan = generate_asymmetric_polygon_scan()

        for shift in range(360):
            query_scan = np.roll(ref_scan, shift)
            score, yaw_off_rad = compute_circular_ncc(query_scan, ref_scan)

            # Expected yaw offset normalized to [-pi, pi]
            expected_rad = shift * (2.0 * math.pi / 360.0)
            if expected_rad > math.pi:
                expected_rad -= 2.0 * math.pi

            assert score > 0.999, f"NCC score degraded at shift {shift}°: {score}"
            diff_rad = abs(yaw_off_rad - expected_rad)
            assert diff_rad < 1e-5, (
                f"Yaw recovery error at shift {shift}°: recovered {yaw_off_rad:.6f}, "
                f"expected {expected_rad:.6f}, diff {diff_rad:.2e}"
            )

    def test_ncc_sub_degree_fractional_shifts_via_continuous_resampling(self):
        """Verifies yaw estimation with fractional sub-degree shifts via continuous resampling."""
        ref_scan = generate_asymmetric_polygon_scan()
        num_rays = 360
        angle_increment = (2.0 * math.pi) / num_rays

        test_shifts_deg = [15.4, 47.7, 120.3, 180.2, 285.8]
        for shift_deg in test_shifts_deg:
            shift_rad = math.radians(shift_deg)
            # Simulate scan sampled with continuous angular offset (shifted by +shift_deg)
            angles = (np.arange(num_rays) * angle_increment - shift_rad) % (2.0 * math.pi)
            raw_ranges = (
                3.5
                + 1.35 * np.sin(angles)
                + 0.85 * np.cos(2.0 * angles + 0.35)
                + 0.55 * np.sin(3.0 * angles + 0.75)
                + 0.35 * np.cos(5.0 * angles - 0.25)
                + 0.15 * np.sin(7.0 * angles + 1.10)
            )
            raw_ranges = np.clip(raw_ranges, 0.5, 7.8).astype(np.float32)

            # Resample back to standard [0..359] grid
            query_resampled = resample_scan_to_360_bins(
                ranges=raw_ranges,
                angle_min=0.0,
                angle_increment=angle_increment
            )

            score, recovered_yaw_rad = compute_circular_ncc(query_resampled, ref_scan)
            assert score > 0.95, f"Sub-degree NCC score low at {shift_deg}°: {score}"

            # Convert to degrees and normalize
            recovered_deg = math.degrees(recovered_yaw_rad)
            if recovered_deg < 0:
                recovered_deg += 360.0
            error_deg = abs(recovered_deg - shift_deg)
            if error_deg > 180.0:
                error_deg = 360.0 - error_deg
            assert error_deg < 1.5, f"Sub-degree shift {shift_deg}° error too large: {error_deg:.2f}°"

    def test_ncc_gaussian_noise_robustness(self):
        """Circular NCC must accurately recover yaw under additive Gaussian noise (sigma = 0.05m and 0.10m)."""
        ref_scan = generate_asymmetric_polygon_scan()
        rng = np.random.RandomState(42)

        for sigma in [0.03, 0.06, 0.10]:
            for shift in [30, 95, 210]:
                query_clean = np.roll(ref_scan, shift)
                noise = rng.normal(0.0, sigma, size=360).astype(np.float32)
                query_noisy = np.clip(query_clean + noise, 0.5, 7.8)

                score, recovered_yaw_rad = compute_circular_ncc(query_noisy, ref_scan)
                assert score > 0.90, f"Score under noise sigma={sigma} fell below 0.90: {score}"

                expected_rad = shift * (2.0 * math.pi / 360.0)
                if expected_rad > math.pi:
                    expected_rad -= 2.0 * math.pi
                recovered_deg = math.degrees(recovered_yaw_rad)
                expected_deg = math.degrees(expected_rad)
                err_deg = abs(recovered_deg - expected_deg)
                assert err_deg < 2.0, f"Yaw estimation failed under noise sigma={sigma}: err {err_deg:.2f}°"


# ==============================================================================
# 4. AMCL Covariance Trace Exact Float Boundaries
# ==============================================================================

class TestAdversarialAMCLCovarianceTraceFloatBoundaries:
    """Stress-tests covariance trace evaluation against exact float boundaries."""

    def test_trace_boundary_0799_converged(self):
        """Trace of 0.0799 must strictly evaluate to CONVERGED (True)."""
        cov = [0.0] * 36
        cov[0] = 0.0300
        cov[7] = 0.0300
        cov[35] = 0.0199
        trace = compute_amcl_covariance_trace(cov)
        assert abs(trace - 0.0799) < 1e-7
        assert is_amcl_converged(trace, threshold=0.080) is True

    def test_trace_boundary_0800_not_converged(self):
        """Trace of 0.0800 must strictly evaluate to UNCONVERGED (False) due to strict inequality."""
        cov = [0.0] * 36
        cov[0] = 0.0300
        cov[7] = 0.0300
        cov[35] = 0.0200
        trace = compute_amcl_covariance_trace(cov)
        assert abs(trace - 0.0800) < 1e-7
        assert is_amcl_converged(trace, threshold=0.080) is False

    def test_trace_boundary_0801_not_converged(self):
        """Trace of 0.0801 must strictly evaluate to UNCONVERGED (False)."""
        cov = [0.0] * 36
        cov[0] = 0.0300
        cov[7] = 0.0300
        cov[35] = 0.0201
        trace = compute_amcl_covariance_trace(cov)
        assert abs(trace - 0.0801) < 1e-7
        assert is_amcl_converged(trace, threshold=0.080) is False

    def test_trace_floating_point_epsilon_sensitivity(self):
        """Tests micro-epsilon sensitivity around 0.080 threshold."""
        eps = 1e-9
        assert is_amcl_converged(0.080 - eps, threshold=0.080) is True
        assert is_amcl_converged(0.080 + eps, threshold=0.080) is False
        assert is_amcl_converged(0.080, threshold=0.080) is False

    def test_malformed_covariance_inputs(self):
        """Malformed covariance inputs must safely return inf without raising exceptions."""
        assert compute_amcl_covariance_trace(None) == float("inf")
        assert compute_amcl_covariance_trace([]) == float("inf")
        assert compute_amcl_covariance_trace([0.01] * 35) == float("inf")
        # Ensure is_amcl_converged rejects inf
        assert is_amcl_converged(float("inf")) is False
        assert is_amcl_converged(float("nan")) is False


# ==============================================================================
# 5. Kidnapped Recovery Rotation Turn Limiter
# ==============================================================================

class TestAdversarialRotationTurnLimiter:
    """Stress-tests the rotation turn limiter (<= 720.0°) and stiction kick mechanics."""

    def test_continuous_high_covariance_rotation_turn_limit_720_deg(self):
        """Simulates continuous high covariance and verifies rotation strictly halts upon reaching 720.0°."""
        engine = KidnappedRobotRecoveryEngine()
        engine.trigger_recovery(ambient_luminance=0.0, current_yaw=0.0)
        assert engine.current_state == KidnappedRobotRecoveryEngine.STATE_ROTATING_SCAN

        high_cov = [0.0] * 36
        high_cov[0] = 0.05
        high_cov[7] = 0.05
        high_cov[35] = 0.05  # trace = 0.15 >= 0.08

        # Step rotation in increments of 1.0 degree
        step_rad = math.radians(1.0)
        current_yaw = 0.0
        steps = 0
        max_steps = 1000

        while engine.current_state == KidnappedRobotRecoveryEngine.STATE_ROTATING_SCAN and steps < max_steps:
            current_yaw = (current_yaw + step_rad) % (2.0 * math.pi)
            engine.update_amcl_pose(covariance=high_cov, current_yaw=current_yaw)
            steps += 1

        accum_deg = math.degrees(engine.rotation_controller.accumulated_yaw_rad)
        # Verify it halted exactly at limit (720.0°) and transitioned to RECOVERY_FAILED
        assert engine.current_state == KidnappedRobotRecoveryEngine.STATE_RECOVERY_FAILED
        assert accum_deg >= 720.0
        # Assert discrete step overshoot did not exceed one step (720.0° + 1.0°)
        assert accum_deg <= 721.0

        # Verify post-limit zero velocity command
        speed, running = engine.rotation_controller.get_current_command(current_yaw)
        assert speed == 0.0
        assert running is False

    def test_rotation_scan_stiction_kick_150ms(self):
        """Verifies 0.70 rad/s kick during first 150ms and 0.35 rad/s cruise thereafter."""
        ctrl = RotationScanController()
        ctrl.start(initial_yaw=0.0)

        # Immediately: stiction kick speed
        speed0, run0 = ctrl.get_current_command(current_yaw=0.01)
        assert run0 is True
        assert speed0 == 0.70

        # Simulate 100ms elapsed (< 150ms)
        ctrl.start_time = time.monotonic() - 0.100
        speed1, run1 = ctrl.get_current_command(current_yaw=0.03)
        assert run1 is True
        assert speed1 == 0.70

        # Simulate 200ms elapsed (> 150ms)
        ctrl.start_time = time.monotonic() - 0.200
        speed2, run2 = ctrl.get_current_command(current_yaw=0.06)
        assert run2 is True
        assert speed2 == 0.35

    def test_post_limit_zero_velocity_latch(self):
        """Once turn limit is reached, repeated get_current_command calls must latch at (0.0, False)."""
        ctrl = RotationScanController()
        ctrl.start(initial_yaw=0.0)
        ctrl.accumulated_yaw_rad = math.radians(720.5)

        for dummy_yaw in [0.1, 0.5, 1.2, 2.5, 3.14]:
            speed, running = ctrl.get_current_command(dummy_yaw)
            assert speed == 0.0
            assert running is False


# ==============================================================================
# 6. Rapid Convergence Early Stop
# ==============================================================================

class TestAdversarialRapidConvergenceEarlyStop:
    """Stress-tests rapid convergence gating when covariance drops below 0.080 during rotation."""

    def test_rapid_convergence_stop_at_90_degrees(self):
        """Simulates covariance dropping below 0.080 after 90 degrees and verifies immediate halt."""
        engine = KidnappedRobotRecoveryEngine()
        engine.trigger_recovery(ambient_luminance=0.0, current_yaw=0.0)
        assert engine.current_state == KidnappedRobotRecoveryEngine.STATE_ROTATING_SCAN

        high_cov = [0.0] * 36
        high_cov[0] = 0.05
        high_cov[7] = 0.05
        high_cov[35] = 0.05

        # Rotate up to 90 degrees
        step_rad = math.radians(2.0)
        current_yaw = 0.0
        while math.degrees(engine.rotation_controller.accumulated_yaw_rad) < 90.0:
            current_yaw = (current_yaw + step_rad) % (2.0 * math.pi)
            res = engine.update_amcl_pose(covariance=high_cov, current_yaw=current_yaw)
            assert res["state"] == KidnappedRobotRecoveryEngine.STATE_ROTATING_SCAN

        accum_before = math.degrees(engine.rotation_controller.accumulated_yaw_rad)
        assert abs(accum_before - 90.0) <= 2.0

        # Inject low covariance (0.045 < 0.080)
        low_cov = [0.0] * 36
        low_cov[0] = 0.015
        low_cov[7] = 0.015
        low_cov[35] = 0.015

        res_stop = engine.update_amcl_pose(covariance=low_cov, current_yaw=current_yaw)
        assert res_stop["state"] == KidnappedRobotRecoveryEngine.STATE_RECOVERY_SUCCESS
        assert res_stop["converged"] is True

        # Assert immediate halt
        assert engine.rotation_controller.is_active is False
        speed, running = engine.rotation_controller.get_current_command(current_yaw)
        assert speed == 0.0
        assert running is False

    def test_rapid_convergence_stop_at_45_degrees(self):
        """Simulates very fast convergence after only 45 degrees."""
        engine = KidnappedRobotRecoveryEngine()
        engine.trigger_recovery(ambient_luminance=0.0, current_yaw=0.0)

        high_cov = [0.0] * 36
        high_cov[0] = 0.04
        high_cov[7] = 0.04
        high_cov[35] = 0.04

        step_rad = math.radians(1.5)
        current_yaw = 0.0
        while math.degrees(engine.rotation_controller.accumulated_yaw_rad) < 45.0:
            current_yaw = (current_yaw + step_rad) % (2.0 * math.pi)
            engine.update_amcl_pose(covariance=high_cov, current_yaw=current_yaw)

        # Inject converged covariance (trace = 0.060)
        low_cov = [0.0] * 36
        low_cov[0] = 0.02
        low_cov[7] = 0.02
        low_cov[35] = 0.02

        res = engine.update_amcl_pose(covariance=low_cov, current_yaw=current_yaw)
        assert res["state"] == KidnappedRobotRecoveryEngine.STATE_RECOVERY_SUCCESS
        assert math.degrees(engine.rotation_controller.accumulated_yaw_rad) <= 48.0

    def test_cag_environment_location_update_on_early_stop(self):
        """Verifies TRINITY CAG environment snapshot is properly updated on early recovery."""
        env = EnvironmentSnapshot()
        engine = KidnappedRobotRecoveryEngine(environment_snapshot=env)
        engine.recognized_room = "corridoio"
        engine.current_state = KidnappedRobotRecoveryEngine.STATE_ROTATING_SCAN
        engine.rotation_controller.start(initial_yaw=0.0)

        converged_cov = [0.0] * 36
        converged_cov[0] = 0.02
        converged_cov[7] = 0.02
        converged_cov[35] = 0.02

        res = engine.update_amcl_pose(
            covariance=converged_cov,
            pose=(3.2, -1.5, 0.45),
            current_yaw=math.radians(90)
        )
        assert res["state"] == KidnappedRobotRecoveryEngine.STATE_RECOVERY_SUCCESS
        assert env.room_name == "corridoio"
        assert env.location == (3.2, -1.5)
        assert abs(env.covariance_trace - 0.06) < 1e-4
        assert "corridoio" in env.to_text()


# ==============================================================================
# 7. Multimodal Branching & Dark Localization Integration
# ==============================================================================

class TestAdversarialMultimodalBranching:
    """Stress-tests perception modality selection (lux=0 vs lux=50) and full dark localization."""

    def test_multimodal_branching_lux_0_dark(self):
        """At lux = 0.0 (total darkness), hypothesizer strictly selects LiDAR."""
        hyp = MultimodalRoomHypothesizer()
        res = hyp.hypothesize_room(ambient_luminance=0.0)
        assert res["modality"] == "LIDAR"
        assert res["luminance"] == 0.0

    def test_multimodal_branching_lux_50_bright(self):
        """At lux = 50.0 (bright daylight), hypothesizer strictly selects VPR."""
        hyp = MultimodalRoomHypothesizer()
        res = hyp.hypothesize_room(ambient_luminance=50.0)
        assert res["modality"] == "VPR"
        assert res["luminance"] == 50.0

    def test_multimodal_branching_exact_hysteresis_boundaries(self):
        """Tests exact luminance boundaries: <=25 lux (LiDAR), (25, 30] (LiDAR safety), >30 lux (VPR)."""
        hyp = MultimodalRoomHypothesizer()

        # 25.0 lux -> LiDAR
        assert hyp.hypothesize_room(25.0)["modality"] == "LIDAR"
        # 25.001 lux -> LiDAR (in safety band)
        assert hyp.hypothesize_room(25.001)["modality"] == "LIDAR"
        # 30.0 lux -> LiDAR (exact boundary is conservative LiDAR)
        assert hyp.hypothesize_room(30.0)["modality"] == "LIDAR"
        # 30.001 lux -> VPR (strictly > 30.0)
        assert hyp.hypothesize_room(30.001)["modality"] == "VPR"

    def test_end_to_end_kidnapped_recovery_with_lidar_in_darkness(self):
        """E2E test: Kidnapped recovery in pitch darkness (lux = 0) with LiDAR room recognizer engine."""
        # 1. Setup LiDAR recognizer with known rooms
        lidar_engine = LidarRoomRecognizerEngine()
        salotto_scan = generate_l_shaped_room_scan()
        cucina_scan = generate_asymmetric_polygon_scan()
        corridoio_scan = generate_narrow_corridor_scan()

        lidar_engine.register_room("salotto", salotto_scan)
        lidar_engine.register_room("cucina", cucina_scan)
        lidar_engine.register_room("corridoio", corridoio_scan)

        # 2. Setup recovery engine with LiDAR engine
        env = EnvironmentSnapshot()
        recovery_engine = KidnappedRobotRecoveryEngine(
            lidar_engine=lidar_engine,
            environment_snapshot=env
        )

        # 3. Simulate kidnapped in cucina in total darkness, rotated by 45 degrees
        query_cucina = np.roll(cucina_scan, 45)

        # Trigger recovery in pitch dark (lux = 0.0)
        trig_res = recovery_engine.trigger_recovery(
            ambient_luminance=0.0,
            lidar_scan=query_cucina,
            current_yaw=0.0
        )

        assert trig_res["state"] == KidnappedRobotRecoveryEngine.STATE_ROTATING_SCAN
        assert trig_res["modality"] == "LIDAR"
        assert trig_res["recognized_room"] == "cucina"

        # 4. Step rotation until convergence (simulate AMCL converging after 40 degrees)
        high_cov = [0.0] * 36
        high_cov[0] = 0.05
        high_cov[7] = 0.05
        high_cov[35] = 0.05

        step_rad = math.radians(2.0)
        yaw = 0.0
        while math.degrees(recovery_engine.rotation_controller.accumulated_yaw_rad) < 40.0:
            yaw = (yaw + step_rad) % (2.0 * math.pi)
            res_step = recovery_engine.update_amcl_pose(high_cov, current_yaw=yaw)
            assert res_step["state"] == KidnappedRobotRecoveryEngine.STATE_ROTATING_SCAN

        # 5. Inject converged covariance (trace = 0.040 < 0.080)
        conv_cov = [0.0] * 36
        conv_cov[0] = 0.015
        conv_cov[7] = 0.015
        conv_cov[35] = 0.010

        res_final = recovery_engine.update_amcl_pose(
            covariance=conv_cov,
            pose=(1.8, 2.4, yaw),
            current_yaw=yaw
        )

        assert res_final["state"] == KidnappedRobotRecoveryEngine.STATE_RECOVERY_SUCCESS
        assert res_final["converged"] is True
        assert res_final["recognized_room"] == "cucina"
        assert env.room_name == "cucina"
        assert env.location == (1.8, 2.4)
        assert abs(env.covariance_trace - 0.040) < 1e-4
        assert not recovery_engine.rotation_controller.is_active
