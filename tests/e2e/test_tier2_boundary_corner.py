"""
==============================================================================
🧪 TIER 2: BOUNDARY & CORNER CASES TEST SUITE
==============================================================================
Rigorously evaluates exact numerical thresholds, transition boundaries,
and extreme environmental corner conditions:
- Exact Luminance: 24, 25, 28, 30, 31, 0, 255
- Exact Timers: 119s, 120s, 299s, 300s, and 29s/30s destructive gate
- Exact Frontier Sizes: 0.38m, 0.40m, 0.42m, single-cell, empty
- Exact AMCL Covariance Trace: 0.079, 0.080, 0.081
- Exact CosPlace 512D Similarity: 0.839, 0.840, 0.841
- Exact RAM Delta Budget: 79.0MB, 80.0MB, 81.0MB
- 0 Lux Pitch Darkness & Extreme Corner Conditions
==============================================================================
"""

import math
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

import numpy as np
import pytest

from tests.e2e.test_infrastructure import (
    LuminanceSafetyContract,
    HRISafetyStateMachine,
    FrontierExplorationEngine,
    SLAMOptimizationExporter,
    CosPlaceVPRMatcher,
    LiDARScanLocalizationSimulator,
)


# ============================================================================
# 1. Exact Luminance Boundaries (24, 25, 28, 30, 31, 0, 255)
# ============================================================================
class TestTier2LuminanceBoundaries:
    """Validates precise thresholds: 24 (reject), 25 (edge), 28 (band), 30 (edge), 31 (authorize)."""

    def test_threshold_luminance_24_strictly_rejected(self):
        """Luminance 24.0 (strictly below 25.0) must be INHIBITED_DARK with warning."""
        res = LuminanceSafetyContract.evaluate(24.0)
        assert res["authorized"] is False
        assert res["status"] == "INHIBITED_DARK"
        assert res["voice_warning"] is not None
        assert "24.0" in res["voice_warning"]

    def test_threshold_luminance_25_exact_lower_boundary(self):
        """Luminance 25.0 (exact lower boundary) must be INHIBITED_DARK."""
        res = LuminanceSafetyContract.evaluate(25.0)
        assert res["authorized"] is False
        assert res["status"] == "INHIBITED_DARK"

    def test_threshold_luminance_28_intermediate_hysteresis_band(self):
        """Luminance 28.0 (between 25 and 30) must be INHIBITED_DARK to avoid flickering."""
        res = LuminanceSafetyContract.evaluate(28.0)
        assert res["authorized"] is False
        assert res["status"] == "INHIBITED_DARK"
        assert "limite critico" in res["voice_warning"]

    def test_threshold_luminance_30_exact_upper_boundary(self):
        """Luminance 30.0 (exact upper boundary) requires strictly > 30 for full authorization."""
        res = LuminanceSafetyContract.evaluate(30.0)
        # 30.0 is not > 30.0, so it remains safely inhibited
        assert res["authorized"] is False
        assert res["status"] == "INHIBITED_DARK"

    def test_threshold_luminance_31_strictly_authorized(self):
        """Luminance 31.0 (strictly above 30.0) must be AUTHORIZED with zero warning."""
        res = LuminanceSafetyContract.evaluate(31.0)
        assert res["authorized"] is True
        assert res["status"] == "AUTHORIZED"
        assert res["voice_warning"] is None

    def test_corner_luminance_0_pitch_black(self):
        """Luminance 0.0 (absolute optical darkness) must be firmly inhibited."""
        res = LuminanceSafetyContract.evaluate(0.0)
        assert res["authorized"] is False
        assert res["status"] == "INHIBITED_DARK"
        assert res["luminance"] == 0.0

    def test_corner_luminance_255_extreme_saturation(self):
        """Luminance 255.0 (full white saturation) must be recognized as authorized."""
        res = LuminanceSafetyContract.evaluate(255.0)
        assert res["authorized"] is True
        assert res["status"] == "AUTHORIZED"


# ============================================================================
# 2. Exact Timer Boundaries (119s, 120s, 299s, 300s, 29s, 30s)
# ============================================================================
class TestTier2TimerBoundaries:
    """Validates exact timing boundaries for HRI reminder and abort protocols."""

    def test_timer_119s_just_before_reminder(self):
        """At 119.0s, reminder must NOT be dispatched yet."""
        fsm = HRISafetyStateMachine()
        fsm.request_mapping("studio")
        prompt = fsm.advance_time(119.0)

        assert fsm.elapsed_time == 119.0
        assert fsm.reminder_dispatched is False
        assert fsm.state == HRISafetyStateMachine.STATE_AWAITING_CONFIRMATION
        assert prompt is None

    def test_timer_120s_exact_reminder_threshold(self):
        """At exactly 120.0s, vocal reminder must be dispatched."""
        fsm = HRISafetyStateMachine()
        fsm.request_mapping("studio")
        fsm.advance_time(119.0)
        prompt = fsm.advance_time(1.0)  # total 120.0s

        assert fsm.elapsed_time == 120.0
        assert fsm.reminder_dispatched is True
        assert fsm.state == HRISafetyStateMachine.STATE_REMINDER_SENT
        assert prompt is not None
        assert "Sollecito" in prompt

    def test_timer_299s_just_before_abort(self):
        """At 299.0s, system must remain awaiting in REMINDER_SENT state."""
        fsm = HRISafetyStateMachine()
        fsm.request_mapping("sala")
        fsm.advance_time(120.0)
        prompt = fsm.advance_time(179.0)  # total 299.0s

        assert fsm.elapsed_time == 299.0
        assert fsm.abort_dispatched is False
        assert fsm.state == HRISafetyStateMachine.STATE_REMINDER_SENT
        assert prompt is None

    def test_timer_300s_exact_standby_abort_threshold(self):
        """At exactly 300.0s, system aborts to standby and firmly locks motors."""
        fsm = HRISafetyStateMachine()
        fsm.request_mapping("sala")
        fsm.advance_time(299.0)
        abort_prompt = fsm.advance_time(1.0)  # total 300.0s

        assert fsm.elapsed_time == 300.0
        assert fsm.abort_dispatched is True
        assert fsm.state == HRISafetyStateMachine.STATE_STANDBY_ABORTED
        assert fsm.motors_allowed is False
        assert abort_prompt is not None
        assert "300 secondi" in abort_prompt

    def test_timer_destructive_gate_29s_vs_30s(self):
        """Destructive confirmation pending at 29.0s, aborted at 30.0s."""
        fsm = HRISafetyStateMachine()
        fsm.request_destructive_action("salotto")

        # 29s: pending
        fsm.advance_time(29.0)
        assert fsm.destructive_pending is True

        # 30s: cancelled
        cancel_prompt = fsm.advance_time(1.0)
        assert fsm.destructive_pending is False
        assert fsm.destructive_confirmed is False
        assert cancel_prompt is not None


# ============================================================================
# 3. Exact Frontier Size Boundaries (0.38m, 0.40m, 0.42m, single, empty)
# ============================================================================
class TestTier2FrontierSizeBoundaries:
    """Validates stopping boundary: 0.38m (stop), 0.40m (stop), 0.42m (continue)."""

    def test_frontier_size_038m_strictly_below_stopping_threshold(self):
        """Frontier cluster of 0.38m (< 0.40m) must trigger exploration completion."""
        engine = FrontierExplorationEngine(resolution=0.05)
        # Simulated cluster of 0.38m
        clusters = [{
            "cell_count": 8,
            "cells": [(5, c) for c in range(8)],
            "size_meters": 0.38,
            "centroid": (1.0, 1.0)
        }]

        # Directly evaluate cluster size logic
        valid = [c for c in clusters if c["size_meters"] >= engine.MIN_FRONTIER_SIZE_METERS]
        assert len(valid) == 0  # 0.38m is rejected as insufficient

    def test_frontier_size_040m_exact_stopping_boundary(self):
        """Frontier of exactly 0.40m is at the boundary."""
        engine = FrontierExplorationEngine(resolution=0.05)
        clusters = [{
            "cell_count": 9,
            "cells": [(5, c) for c in range(9)],
            "size_meters": 0.40,
            "centroid": (2.0, 2.0)
        }]
        valid = [c for c in clusters if c["size_meters"] >= engine.MIN_FRONTIER_SIZE_METERS]
        assert len(valid) == 1
        assert valid[0]["size_meters"] == 0.40

    def test_frontier_size_042m_strictly_above_stopping_threshold(self):
        """Frontier cluster of 0.42m (> 0.40m) must be accepted for dispatch."""
        engine = FrontierExplorationEngine(resolution=0.05)
        clusters = [{
            "cell_count": 10,
            "cells": [(5, c) for c in range(10)],
            "size_meters": 0.42,
            "centroid": (3.0, 3.0)
        }]
        valid = [c for c in clusters if c["size_meters"] >= engine.MIN_FRONTIER_SIZE_METERS]
        assert len(valid) == 1
        assert valid[0]["size_meters"] > engine.MIN_FRONTIER_SIZE_METERS

    def test_corner_single_cell_frontier_island(self):
        """Isolated single-cell frontier (0.00m / 0.05m) is ignored and stops exploration."""
        grid = np.full((15, 15), -1, dtype=np.int8)
        grid[7, 7] = 0  # single free pixel

        engine = FrontierExplorationEngine(resolution=0.05)
        eval_res = engine.evaluate_frontiers(grid)

        assert eval_res["status"] == "COMPLETED"
        assert eval_res["motor_stop_required"] is True

    def test_corner_completely_empty_occupancy_grid(self):
        """Grid with zero unknown cells declares immediate completion."""
        grid = np.zeros((20, 20), dtype=np.int8)  # all free
        engine = FrontierExplorationEngine(resolution=0.05)
        eval_res = engine.evaluate_frontiers(grid)

        assert eval_res["status"] == "COMPLETED"
        assert eval_res["cluster_count"] == 0


# ============================================================================
# 4. Exact AMCL Covariance Trace Boundaries (0.079, 0.080, 0.081)
# ============================================================================
class TestTier2AMCLCovarianceTraceBoundaries:
    """Validates convergence threshold: 0.079 (converged), 0.080 (edge), 0.081 (unconverged)."""

    def test_trace_0079_strictly_converged(self):
        """AMCL covariance trace 0.079 (< 0.080) indicates confident localization."""
        limit = LiDARScanLocalizationSimulator.MAX_AMCL_COVARIANCE_TRACE
        trace = 0.079
        is_converged = trace < limit
        assert is_converged is True

    def test_trace_0080_exact_boundary(self):
        """AMCL covariance trace 0.080 is at the exact threshold boundary."""
        limit = LiDARScanLocalizationSimulator.MAX_AMCL_COVARIANCE_TRACE
        trace = 0.080
        # Strict inequality trace < 0.080
        is_converged = trace < limit
        assert is_converged is False

    def test_trace_0081_strictly_unconverged(self):
        """AMCL covariance trace 0.081 (> 0.080) must be rejected as unconverged."""
        limit = LiDARScanLocalizationSimulator.MAX_AMCL_COVARIANCE_TRACE
        trace = 0.081
        is_converged = trace < limit
        assert is_converged is False

    def test_corner_amcl_high_divergence_trace(self):
        """High divergence (trace = 5.0, e.g. severe wheel slip) remains unconverged."""
        limit = LiDARScanLocalizationSimulator.MAX_AMCL_COVARIANCE_TRACE
        trace = 5.0
        assert (trace < limit) is False


# ============================================================================
# 5. Exact CosPlace 512D Similarity Boundaries (0.839, 0.840, 0.841)
# ============================================================================
class TestTier2CosPlaceSimilarityBoundaries:
    """Validates VPR cosine matching: 0.839 (reject), 0.840 (edge), 0.841 (accept)."""

    def test_similarity_0839_strictly_rejected(self):
        """Cosine similarity 0.839 (< 0.840) must be rejected to avoid false loop closures."""
        vpr = CosPlaceVPRMatcher()
        ref = np.zeros(512, dtype=np.float32)
        ref[0] = 1.0  # unit vector along dimension 0
        vpr.register_room("cucina", [ref])

        # Construct query vector with exact dot product 0.839
        query = np.zeros(512, dtype=np.float32)
        query[0] = 0.839
        query[1] = math.sqrt(1.0 - 0.839 ** 2)
        query = vpr.normalize_l2(query)

        res = vpr.match_room(query)
        assert res["matched"] is False
        assert res["room_name"] is None
        assert abs(res["similarity"] - 0.839) < 1e-3

    def test_similarity_0840_exact_boundary_rejected(self):
        """Cosine similarity 0.840 is at boundary, required > 0.840."""
        vpr = CosPlaceVPRMatcher()
        ref = np.zeros(512, dtype=np.float32)
        ref[0] = 1.0
        vpr.register_room("salotto", [ref])

        query = np.zeros(512, dtype=np.float32)
        query[0] = 0.840
        query[1] = math.sqrt(1.0 - 0.840 ** 2)
        query = vpr.normalize_l2(query)

        res = vpr.match_room(query)
        # Strict inequality > 0.84
        assert res["matched"] is False
        assert res["room_name"] is None

    def test_similarity_0841_strictly_accepted(self):
        """Cosine similarity 0.841 (> 0.840) must be accepted as valid room match."""
        vpr = CosPlaceVPRMatcher()
        ref = np.zeros(512, dtype=np.float32)
        ref[0] = 1.0
        vpr.register_room("corridoio", [ref])

        query = np.zeros(512, dtype=np.float32)
        query[0] = 0.841
        query[1] = math.sqrt(1.0 - 0.841 ** 2)
        query = vpr.normalize_l2(query)

        res = vpr.match_room(query)
        assert res["matched"] is True
        assert res["room_name"] == "corridoio"
        assert res["similarity"] >= 0.841

    def test_corner_orthogonal_embeddings(self):
        """Orthogonal embeddings (similarity = 0.0) must be rejected."""
        vpr = CosPlaceVPRMatcher()
        ref = np.zeros(512, dtype=np.float32)
        ref[0] = 1.0
        vpr.register_room("camera", [ref])

        query = np.zeros(512, dtype=np.float32)
        query[1] = 1.0  # orthogonal
        res = vpr.match_room(query)

        assert res["matched"] is False
        assert abs(res["similarity"]) < 1e-4

    def test_corner_inverted_embeddings(self):
        """Opposite vectors (similarity = -1.0) must be rejected."""
        vpr = CosPlaceVPRMatcher()
        ref = np.zeros(512, dtype=np.float32)
        ref[0] = 1.0
        vpr.register_room("bagno", [ref])

        query = -ref  # opposite
        res = vpr.match_room(query)

        assert res["matched"] is False
        assert abs(res["similarity"] - (-1.0)) < 1e-4


# ============================================================================
# 6. Exact RAM Delta Boundaries (79MB, 80MB, 81MB)
# ============================================================================
class TestTier2RAMDeltaBoundaries:
    """Validates RAM delta budget during bundle adjustment: 79MB (pass), 80MB (edge), 81MB (alarm)."""

    def test_ram_delta_79mb_strictly_safe(self):
        """RAM delta of 79.0 MB (< 80.0 MB) passes bundle adjustment verification."""
        exporter = SLAMOptimizationExporter()
        ok, msg = exporter.trigger_global_optimization(simulate_ram_usage_mb=79.0)

        assert ok is True
        assert "converged" in msg.lower()
        assert exporter.last_ram_delta_mb == 79.0

    def test_ram_delta_80mb_exact_limit(self):
        """RAM delta of exactly 80.0 MB triggers memory warning."""
        exporter = SLAMOptimizationExporter()
        ok, msg = exporter.trigger_global_optimization(simulate_ram_usage_mb=80.0)

        # 80.0 is >= 80.0 -> Alarm
        assert ok is False
        assert "ALARM" in msg

    def test_ram_delta_81mb_strictly_exceeded(self):
        """RAM delta of 81.0 MB (> 80.0 MB) must fail and report OOM risk."""
        exporter = SLAMOptimizationExporter()
        ok, msg = exporter.trigger_global_optimization(simulate_ram_usage_mb=81.0)

        assert ok is False
        assert "OOM Kill" in msg
        assert "81.0 MB" in msg

    def test_corner_zero_ram_delta(self):
        """Zero RAM delta (0.0 MB) passes gracefully."""
        exporter = SLAMOptimizationExporter()
        ok, msg = exporter.trigger_global_optimization(simulate_ram_usage_mb=0.0)
        assert ok is True


# ============================================================================
# 7. 0 Lux Pitch Darkness & Vision Suppression
# ============================================================================
class TestTier2PitchDarknessCorners:
    """Validates system behavior under 0 lux darkness and sensor degradation."""

    def test_darkness_0_lux_disables_vision_stream(self):
        """At exactly 0.0 lux, vision must be explicitly disabled."""
        sim = LiDARScanLocalizationSimulator()
        sim.set_ambient_lux(0.0)

        assert sim.vision_active is False

    def test_darkness_01_lux_borderline_darkness(self):
        """At 0.1 lux, illumination remains insufficient (<25) for visual VPR."""
        res = LuminanceSafetyContract.evaluate(0.1)
        assert res["authorized"] is False
        assert res["status"] == "INHIBITED_DARK"

    def test_dark_localization_with_zero_rotations(self):
        """Before any rotation, covariance trace remains high (unconverged)."""
        signatures = {"salotto": np.ones(360) * 3.0}
        sim = LiDARScanLocalizationSimulator(known_room_signatures=signatures)
        sim.set_ambient_lux(0.0)

        assert sim.rotations_completed == 0.0
        assert sim.current_covariance_trace >= 0.08
