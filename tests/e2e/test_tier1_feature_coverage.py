"""
==============================================================================
🧪 TIER 1: FEATURE COVERAGE TEST SUITE
==============================================================================
Validates primary behavior, interface contracts, and error handling across:
- TC1 / R2: Luminance Safety Gate
- TC2 / R2: HRI Voice Confirmation State Machine & Safety Timers
- TC3 / R2: Frontier Exploration & Autonomous Completion
- TC4 / R2: SLAM Optimization & SSD Map Export
- TC5 / R3: Pitch-Dark LiDAR Localization & AMCL Convergence
- TC6 / R3: Visual Place Recognition (CosPlace 512D on Hailo NPU)
- TC7 / R4: VUI Dialogue, Situational Awareness & Audio Conditioning
- TC8 / R5: Hierarchical Hybrid Navigation (Nav2 + NOMAD + Hailo YOLO)
- R1: Single Continuous Map & Multimodal Room Registry (SQLite WAL)

Minimum requirement: >= 5 test cases per feature.
==============================================================================
"""

import json
import math
import sqlite3
import sys
import tempfile
import time
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
    VUIDialogueEngine,
    HybridSearchCoordinator,
    SemanticRoomRegistryModel,
)


# ============================================================================
# TC1: Luminance Safety Gate Tests
# ============================================================================
class TestTC1LuminanceSafetyGate:
    """Verifies TC1 acceptance criteria: reject < 25, authorize > 30."""

    def test_tc1_luminance_dark_rejection_emits_warning(self):
        """Frame luminance < 25 rejects visual mapping and emits vocal alert."""
        dark_frame = np.full((100, 100, 3), 15, dtype=np.uint8)
        mean_lum = LuminanceSafetyContract.compute_mean_luminance(dark_frame)
        result = LuminanceSafetyContract.evaluate(mean_lum)

        assert result["authorized"] is False
        assert result["status"] == "INHIBITED_DARK"
        assert result["voice_warning"] is not None
        assert "insufficiente per la mappatura visiva" in result["voice_warning"]

    def test_tc1_luminance_bright_authorization_no_warning(self):
        """Frame luminance > 30 authorizes visual mapping with zero warnings."""
        bright_frame = np.full((100, 100, 3), 50, dtype=np.uint8)
        mean_lum = LuminanceSafetyContract.compute_mean_luminance(bright_frame)
        result = LuminanceSafetyContract.evaluate(mean_lum)

        assert result["authorized"] is True
        assert result["status"] == "AUTHORIZED"
        assert result["voice_warning"] is None

    def test_tc1_luminance_trigger_service_contract(self):
        """Validates ROS 2 /mapping/check_luminance (std_srvs/srv/Trigger) behavior."""
        # Dark test
        success_dark, msg_dark = LuminanceSafetyContract.trigger_service_check(18.0)
        assert success_dark is False
        assert "insufficiente" in msg_dark

        # Bright test
        success_bright, msg_bright = LuminanceSafetyContract.trigger_service_check(45.0)
        assert success_bright is True
        assert "authorized" in msg_bright.lower()

    def test_tc1_luminance_topic_json_payload(self):
        """Validates /camera/luminance_status JSON topic schema."""
        raw_payload = LuminanceSafetyContract.to_ros_topic_payload(12.4)
        data = json.loads(raw_payload)

        assert "luminance" in data
        assert "status" in data
        assert "threshold_low" in data
        assert "threshold_high" in data
        assert data["status"] == "INHIBITED_DARK"
        assert data["threshold_low"] == 25.0
        assert data["threshold_high"] == 30.0

    def test_tc1_luminance_rgb_perceptual_formula(self):
        """Verifies standard perceptual luminance formula: Y = 0.299*R + 0.587*G + 0.114*B."""
        # Pure red: 0.299 * 100 = 29.9
        red_frame = np.zeros((10, 10, 3), dtype=np.uint8)
        red_frame[:, :, 0] = 100
        red_lum = LuminanceSafetyContract.compute_mean_luminance(red_frame)
        assert abs(red_lum - 29.9) < 0.1

        # Pure green: 0.587 * 100 = 58.7
        green_frame = np.zeros((10, 10, 3), dtype=np.uint8)
        green_frame[:, :, 1] = 100
        green_lum = LuminanceSafetyContract.compute_mean_luminance(green_frame)
        assert abs(green_lum - 58.7) < 0.1

        # Pure blue: 0.114 * 100 = 11.4
        blue_frame = np.zeros((10, 10, 3), dtype=np.uint8)
        blue_frame[:, :, 2] = 100
        blue_lum = LuminanceSafetyContract.compute_mean_luminance(blue_frame)
        assert abs(blue_lum - 11.4) < 0.1

    def test_tc1_luminance_grayscale_and_boundary_handling(self):
        """Handles 2D grayscale frames and borderline hysteresis values."""
        gray_frame = np.full((50, 50), 27, dtype=np.uint8)
        lum = LuminanceSafetyContract.compute_mean_luminance(gray_frame)
        eval_res = LuminanceSafetyContract.evaluate(lum)
        # 27 is between 25 and 30 (marginal) -> should be inhibited
        assert eval_res["authorized"] is False
        assert eval_res["status"] == "INHIBITED_DARK"


# ============================================================================
# TC2: HRI Voice Confirmation State Machine Tests
# ============================================================================
class TestTC2HRISafetyStateMachine:
    """Verifies TC2 acceptance criteria: 120s reminder, 300s abort, immediate start."""

    def test_tc2_hri_initial_challenge_issued_motors_locked(self):
        """Initial request enters AWAITING_CONFIRMATION with motors firmly locked."""
        fsm = HRISafetyStateMachine()
        prompt = fsm.request_mapping("cucina")

        assert fsm.state == HRISafetyStateMachine.STATE_AWAITING_CONFIRMATION
        assert fsm.motors_allowed is False
        assert "cucina" in prompt
        assert "confermi" in prompt.lower()

    def test_tc2_hri_silence_reminder_dispatched_at_120s(self):
        """At 120s silence, issues reminder prompt and transitions to REMINDER_SENT."""
        fsm = HRISafetyStateMachine()
        fsm.request_mapping("salotto")

        # 100s: no reminder yet
        fsm.advance_time(100.0)
        assert fsm.state == HRISafetyStateMachine.STATE_AWAITING_CONFIRMATION
        assert fsm.reminder_dispatched is False

        # +20s: total 120s -> reminder dispatched
        reminder = fsm.advance_time(20.0)
        assert fsm.state == HRISafetyStateMachine.STATE_REMINDER_SENT
        assert fsm.reminder_dispatched is True
        assert reminder is not None
        assert "Sollecito" in reminder

    def test_tc2_hri_silence_abort_to_standby_at_300s(self):
        """At 300s silence, aborts safely to standby, keeps motors locked."""
        fsm = HRISafetyStateMachine()
        fsm.request_mapping("corridoio")

        fsm.advance_time(120.0)  # triggers reminder
        fsm.advance_time(179.0)  # total 299s -> still in reminder state
        assert fsm.state == HRISafetyStateMachine.STATE_REMINDER_SENT

        abort_prompt = fsm.advance_time(1.0)  # total 300s -> abort
        assert fsm.state == HRISafetyStateMachine.STATE_STANDBY_ABORTED
        assert fsm.motors_allowed is False
        assert abort_prompt is not None
        assert "standby" in abort_prompt.lower()

    def test_tc2_hri_affirmative_immediate_start(self):
        """Affirmative reply ("sì, procedi") starts mapping immediately and unlocks motors."""
        fsm = HRISafetyStateMachine()
        fsm.request_mapping("camera da letto")

        # User confirms at 15s
        fsm.advance_time(15.0)
        reply, success = fsm.process_voice_input("sì, procedi pure Marcus")

        assert success is True
        assert fsm.state == HRISafetyStateMachine.STATE_MAPPING_ACTIVE
        assert fsm.motors_allowed is True
        assert "Avvio immediato" in reply

    def test_tc2_hri_negative_user_cancellation(self):
        """Negative voice input ("no, annulla") cancels mapping and keeps motors locked."""
        fsm = HRISafetyStateMachine()
        fsm.request_mapping("ripostiglio")

        fsm.advance_time(30.0)
        reply, success = fsm.process_voice_input("No, annulla la procedura")

        assert success is False
        assert fsm.state == HRISafetyStateMachine.STATE_CANCELLED
        assert fsm.motors_allowed is False
        assert "annullata" in reply.lower()

    def test_tc2_hri_destructive_action_timeout_at_30s(self):
        """Destructive action confirmation times out at 30s and cancels safely."""
        fsm = HRISafetyStateMachine()
        fsm.request_destructive_action("mappa_salotto")

        assert fsm.destructive_pending is True

        # Advance 29s -> still pending
        fsm.advance_time(29.0)
        assert fsm.destructive_pending is True

        # Advance 1s -> total 30s -> auto abort
        cancel_prompt = fsm.advance_time(1.0)
        assert fsm.destructive_pending is False
        assert fsm.destructive_confirmed is False
        assert cancel_prompt is not None
        assert "annullata" in cancel_prompt.lower()


# ============================================================================
# TC3: Frontier Exploration & Autonomous Completion Tests
# ============================================================================
class TestTC3FrontierExploration:
    """Verifies TC3 acceptance criteria: frontier detection, dispatch, stop < 0.40m."""

    def test_tc3_frontier_cell_detection_on_grid(self):
        """Detects FREE cells (0) that border UNKNOWN (-1) cells."""
        grid = np.full((10, 10), -1, dtype=np.int8)  # all unknown
        # Create a 4x4 free island
        grid[3:7, 3:7] = 0

        engine = FrontierExplorationEngine(resolution=0.05)
        cells = engine.detect_frontier_cells(grid)

        # Free cells on the perimeter of the island must be detected as frontiers
        assert len(cells) > 0
        # The center of the 4x4 island (4, 4) is surrounded by free cells, so not a frontier
        assert (4, 4) not in cells
        # The perimeter cell (3, 3) touches unknown (-1), so it is a frontier
        assert (3, 3) in cells

    def test_tc3_frontier_clustering_connected_components(self):
        """Clusters contiguous frontier cells and computes metric dimensions."""
        grid = np.full((20, 20), -1, dtype=np.int8)
        # Create a line of free cells along row 5 (cols 5 to 14) -> 10 cells = 0.50m
        grid[5, 5:15] = 0

        engine = FrontierExplorationEngine(resolution=0.05, origin=(-5.0, -5.0))
        cells = engine.detect_frontier_cells(grid)
        clusters = engine.cluster_frontiers(cells)

        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster["cell_count"] == 10
        # 9 intervals * 0.05 = 0.45m
        assert cluster["size_meters"] >= 0.40
        # Centroid world coordinates
        cx, cy = cluster["centroid"]
        assert -5.0 <= cx <= 5.0
        assert -5.0 <= cy <= 5.0

    def test_tc3_frontier_dispatch_largest_valid_frontier(self):
        """Dispatches navigation goal to largest valid frontier (>= 0.40m)."""
        grid = np.full((40, 40), -1, dtype=np.int8)
        # Cluster 1: Small (4 cells = 0.15m)
        grid[5, 5:9] = 0
        # Cluster 2: Large (16 cells = 0.75m)
        grid[20, 10:26] = 0

        engine = FrontierExplorationEngine(resolution=0.05)
        eval_res = engine.evaluate_frontiers(grid)

        assert eval_res["status"] == "EXPLORING"
        assert eval_res["selected_goal"] is not None
        assert eval_res["motor_stop_required"] is False
        assert len(engine.dispatched_goals) == 1

    def test_tc3_frontier_termination_when_all_below_threshold(self):
        """Declares completion and requests motor stop when all frontiers < 0.40m."""
        grid = np.full((30, 30), -1, dtype=np.int8)
        # Small isolated frontier: 3 cells = 0.10m (< 0.40m threshold)
        grid[10, 10:13] = 0

        engine = FrontierExplorationEngine(resolution=0.05)
        eval_res = engine.evaluate_frontiers(grid)

        assert eval_res["status"] == "COMPLETED"
        assert "ALL_FRONTIERS_BELOW_THRESHOLD" in eval_res["reason"]
        assert eval_res["selected_goal"] is None
        assert eval_res["motor_stop_required"] is True

    def test_tc3_frontier_motor_stop_command_emitted(self):
        """Zero velocity stop command is required upon exploration completion."""
        grid = np.full((20, 20), 0, dtype=np.int8)  # completely explored (no unknown)
        engine = FrontierExplorationEngine()
        eval_res = engine.evaluate_frontiers(grid)

        assert eval_res["status"] == "COMPLETED"
        assert eval_res["motor_stop_required"] is True

    def test_tc3_frontier_unreachable_blacklisting(self):
        """Blacklists unreachable centroids and selects alternative reachable frontier."""
        grid = np.full((40, 40), -1, dtype=np.int8)
        grid[10, 10:25] = 0  # cluster A (large)
        grid[30, 10:25] = 0  # cluster B (large)

        engine = FrontierExplorationEngine(resolution=0.05)
        eval1 = engine.evaluate_frontiers(grid)
        first_goal = eval1["selected_goal"]

        # Blacklist first goal (simulating Nav2 abort)
        engine.blacklist_frontier(first_goal)

        eval2 = engine.evaluate_frontiers(grid)
        second_goal = eval2["selected_goal"]

        assert second_goal is not None
        assert second_goal != first_goal


# ============================================================================
# TC4: SLAM Optimization & SSD Map Export Tests
# ============================================================================
class TestTC4SLAMOptimizationAndSSDExport:
    """Verifies TC4 acceptance criteria: optimization call, export to SSD, RAM < 80MB."""

    def test_tc4_slam_global_optimization_service(self):
        """Executes global bundle adjustment service and verifies convergence."""
        exporter = SLAMOptimizationExporter()
        success, msg = exporter.trigger_global_optimization(simulate_ram_usage_mb=42.0)

        assert success is True
        assert exporter.optimization_called is True
        assert "converged" in msg.lower()

    def test_tc4_slam_ram_delta_under_80mb_budget(self):
        """Ensures RAM delta during optimization is strictly below 80MB."""
        exporter = SLAMOptimizationExporter()

        # 55MB: safe
        ok, _ = exporter.trigger_global_optimization(simulate_ram_usage_mb=55.0)
        assert ok is True

        # 85MB: triggers alarm to avoid OOM
        alarm, msg = exporter.trigger_global_optimization(simulate_ram_usage_mb=85.0)
        assert alarm is False
        assert "ALARM" in msg
        assert "OOM Kill" in msg

    def test_tc4_slam_export_yaml_and_pgm_structure(self, tmp_path):
        """Generates valid Nav2 YAML descriptor and binary PGM image."""
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path)
        dummy_grid = np.zeros((100, 100), dtype=np.int8)
        dummy_grid[20:30, 20:30] = 100  # obstacle box

        success, msg, files = exporter.export_map_files(
            map_name="salotto",
            grid_data=dummy_grid,
            resolution=0.05,
            target_override=tmp_path
        )

        assert success is True
        assert files["yaml"].exists()
        assert files["pgm"].exists()

        # Validate YAML content
        yaml_text = files["yaml"].read_text(encoding="utf-8")
        assert "salotto.pgm" in yaml_text
        assert "resolution: 0.0500" in yaml_text
        assert "occupied_thresh: 0.65" in yaml_text

        # Validate PGM header
        pgm_bytes = files["pgm"].read_bytes()
        assert pgm_bytes.startswith(b"P5\n100 100\n255\n")

    def test_tc4_slam_mandatory_ssd_prefix_enforcement(self):
        """Rejects export if directory is not on NVMe SSD (/mnt/ssd/maps - FM-NAV-020)."""
        exporter = SLAMOptimizationExporter(enforce_ssd_mount=True)
        dummy_grid = np.zeros((10, 10), dtype=np.int8)

        # Attempt to export to unauthorized MicroSD path
        unauthorized_path = Path("/home/robopy/.ros/maps")
        ok, msg, _ = exporter.export_map_files(
            map_name="test_bad",
            grid_data=dummy_grid,
            target_override=unauthorized_path,
            enforce_ssd_check=True
        )
        assert ok is False
        assert "FM-NAV-020" in msg

    def test_tc4_slam_readonly_disk_failure_handling(self, tmp_path):
        """Simulates read-only filesystem on SSD and ensures graceful error reporting."""
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path, enforce_ssd_mount=False)
        dummy_grid = np.zeros((10, 10), dtype=np.int8)

        ok, msg, _ = exporter.export_map_files(
            map_name="crash_test",
            grid_data=dummy_grid,
            target_override=tmp_path,
            force_readonly=True,
            enforce_ssd_check=False
        )
        assert ok is False
        assert "Read-only file system" in msg

    def test_tc4_slam_map_metadata_yaml_values(self, tmp_path):
        """Checks compliance of map metadata keys with Nav2 map_server schema."""
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path)
        dummy_grid = np.zeros((20, 20), dtype=np.int8)
        _, _, files = exporter.export_map_files("studio", dummy_grid, resolution=0.025, target_override=tmp_path)

        content = files["yaml"].read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        keys = [l.split(":")[0].strip() for l in lines]
        for expected_key in ["image", "resolution", "origin", "negate", "occupied_thresh", "free_thresh"]:
            assert expected_key in keys


# ============================================================================
# TC5: Pitch-Dark LiDAR Localization Tests
# ============================================================================
class TestTC5PitchDarkLiDARLocalization:
    """Verifies TC5 acceptance criteria: 0 lux, visual disabled, trace < 0.08 in <= 2 spins."""

    def test_tc5_dark_localization_disables_visual_vpr(self):
        """At 0 lux ambient light, visual VPR is suppressed."""
        sim = LiDARScanLocalizationSimulator()
        sim.set_ambient_lux(0.0)
        assert sim.vision_active is False

        sim.set_ambient_lux(15.0)
        assert sim.vision_active is True

    def test_tc5_dark_localization_360_spin_execution(self):
        """Executes controlled 360° spin in total darkness."""
        signatures = {"corridoio": np.linspace(1.0, 3.0, 360)}
        sim = LiDARScanLocalizationSimulator(known_room_signatures=signatures)
        sim.set_ambient_lux(0.0)

        step = sim.simulate_rotation_step("corridoio", rotation_fraction=1.0)
        assert step["rotations"] == 1.0
        assert step["vision_active"] is False

    def test_tc5_dark_localization_amcl_covariance_trace_reduction(self):
        """Covariance trace decreases monotonically as rotation progresses."""
        signatures = {"salotto": np.ones(360)}
        sim = LiDARScanLocalizationSimulator(known_room_signatures=signatures)
        sim.set_ambient_lux(0.0)

        step1 = sim.simulate_rotation_step("salotto", rotation_fraction=0.5)
        step2 = sim.simulate_rotation_step("salotto", rotation_fraction=0.5)

        assert step2["covariance_trace"] < step1["covariance_trace"]

    def test_tc5_dark_localization_convergence_within_2_rotations(self):
        """AMCL covariance trace drops below 0.08 within <= 2 full rotations."""
        signatures = {"cucina": np.ones(360) * 2.5}
        sim = LiDARScanLocalizationSimulator(known_room_signatures=signatures)
        sim.set_ambient_lux(0.0)

        # Spin 1.5 rotations
        step = sim.simulate_rotation_step("cucina", rotation_fraction=1.5)

        assert step["rotations"] <= 2.0
        assert step["covariance_trace"] < 0.08
        assert step["converged"] is True
        assert step["identified_room"] == "cucina"

    def test_tc5_dark_localization_correct_room_id_retrieved(self):
        """Accurately identifies room name in dark through polar signature."""
        signatures = {
            "salotto": np.array([2.0] * 180 + [4.0] * 180),
            "camera": np.array([1.5] * 360),
        }
        sim = LiDARScanLocalizationSimulator(known_room_signatures=signatures)
        sim.set_ambient_lux(0.0)

        step = sim.simulate_rotation_step("salotto", rotation_fraction=1.8)
        assert step["identified_room"] == "salotto"

    def test_tc5_dark_localization_unknown_room_remains_unconverged(self):
        """Robot in an unmapped room does not falsely converge."""
        signatures = {"salotto": np.ones(360)}
        sim = LiDARScanLocalizationSimulator(known_room_signatures=signatures)
        sim.set_ambient_lux(0.0)

        step = sim.simulate_rotation_step("giardino_sconosciuto", rotation_fraction=2.0)
        assert step["converged"] is False
        assert step["identified_room"] is None
        assert step["covariance_trace"] >= 0.08


# ============================================================================
# TC6: Visual Place Recognition (CosPlace 512D) Tests
# ============================================================================
class TestTC6VisualPlaceRecognition:
    """Verifies TC6 acceptance criteria: 512D vector, latency < 50ms, sim > 0.84."""

    def test_tc6_vpr_embedding_dimensionality_512(self):
        """CosPlace model outputs strictly 512-dimensional embedding."""
        vpr = CosPlaceVPRMatcher()
        vec, _ = vpr.extract_embedding_mock(seed_feature=1.23)
        assert vec.shape == (512,)
        assert vec.dtype == np.float32

    def test_tc6_vpr_l2_normalization_strict(self):
        """Output embedding must be unit L2-normalized: ||v||_2 = 1.0 +- 1e-5."""
        vpr = CosPlaceVPRMatcher()
        vec, _ = vpr.extract_embedding_mock(seed_feature=4.56)
        norm = float(np.linalg.norm(vec))
        assert abs(norm - 1.0) < 1e-5

    def test_tc6_vpr_latency_under_50ms(self):
        """Hailo NPU CosPlace 512D inference latency must be < 50ms per frame."""
        vpr = CosPlaceVPRMatcher()
        _, latency_ms = vpr.extract_embedding_mock(seed_feature=7.89, simulated_latency_ms=22.4)
        assert latency_ms < 50.0

    def test_tc6_vpr_cosine_matching_above_084(self):
        """Accurately matches room when cosine similarity > 0.84."""
        vpr = CosPlaceVPRMatcher()
        ref_vec, _ = vpr.extract_embedding_mock(seed_feature=10.0)
        vpr.register_room("salotto", [ref_vec])

        # Query vector with slight perturbation (cosine sim ~0.96 > 0.84)
        perturbed = vpr.normalize_l2(ref_vec + 0.01 * np.random.randn(512).astype(np.float32))
        res = vpr.match_room(perturbed)

        assert res["matched"] is True
        assert res["room_name"] == "salotto"
        assert res["similarity"] > 0.84

    def test_tc6_vpr_rejection_below_084(self):
        """Rejects recognition when cosine similarity <= 0.84 (prevents perceptual aliasing)."""
        vpr = CosPlaceVPRMatcher()
        vec1, _ = vpr.extract_embedding_mock(seed_feature=1.0)
        vec2, _ = vpr.extract_embedding_mock(seed_feature=999.0)  # orthogonal / different
        vpr.register_room("camera", [vec1])

        res = vpr.match_room(vec2)
        assert res["matched"] is False
        assert res["room_name"] is None
        assert res["similarity"] <= 0.84

    def test_tc6_vpr_rapid_identification_under_3_seconds(self):
        """Full visual room recognition pipeline completes in <= 3.0 seconds."""
        vpr = CosPlaceVPRMatcher()
        ref_vec, _ = vpr.extract_embedding_mock(seed_feature=5.0)
        vpr.register_room("cucina", [ref_vec])

        t0 = time.perf_counter()
        query_vec = vpr.normalize_l2(ref_vec + 0.008 * np.random.randn(512).astype(np.float32))
        res = vpr.match_room(query_vec)
        elapsed = time.perf_counter() - t0

        assert res["matched"] is True
        assert elapsed < 3.0


# ============================================================================
# TC7: VUI Dialogue & Situational Awareness Tests
# ============================================================================
class TestTC7VUIDialogueAndSituationalAwareness:
    """Verifies TC7 acceptance criteria: status queries, destructive confirmations, audio rate."""

    def test_tc7_vui_query_dove_ti_trovi(self):
        """Answers 'Dove ti trovi?' reporting current room, coords, and active map."""
        vui = VUIDialogueEngine()
        vui.update_cag_location("salotto", (2.4, -1.8), "piano_terra.yaml", 0.045)

        reply = vui.handle_query("Dove ti trovi?")
        assert "salotto" in reply
        assert "2.4" in reply
        assert "piano_terra.yaml" in reply

    def test_tc7_vui_query_in_quale_mappa_stai_navigando(self):
        """Answers 'In quale mappa stai navigando?' reporting map name and covariance."""
        vui = VUIDialogueEngine()
        vui.update_cag_location("cucina", (0.5, 3.2), "villa_marcus.yaml", 0.038)

        reply = vui.handle_query("In quale mappa stai navigando Marcus?")
        assert "villa_marcus.yaml" in reply
        assert "eccellente" in reply
        assert "0.038" in reply

    def test_tc7_vui_query_cosa_vedi(self):
        """Answers 'Cosa vedi?' reporting semantic detections in camera view."""
        vui = VUIDialogueEngine()
        vui.update_cag_location(
            "camera", (1.0, 1.0), "mappa.yaml", 0.05,
            visual_detections=["letto", "comodino", "lampada"]
        )

        reply = vui.handle_query("Cosa vedi davanti a te?")
        assert "letto" in reply
        assert "comodino" in reply
        assert "lampada" in reply

    def test_tc7_vui_destructive_command_requires_explicit_confirmation(self):
        """Requires explicit 'sì, confermo' before executing destructive map overwrite."""
        fsm = HRISafetyStateMachine()
        fsm.request_destructive_action("salotto")

        # Wrong answer
        msg1, ok1 = fsm.process_voice_input("procedi pure")
        assert ok1 is False

        # Correct explicit confirmation
        msg2, ok2 = fsm.process_voice_input("sì, confermo")
        assert ok2 is True
        assert fsm.destructive_confirmed is True

    def test_tc7_vui_audio_streaming_sample_rate_resampling(self):
        """Validates ReSpeaker 16kHz mono in -> 48kHz hardware DAC out rule."""
        vui = VUIDialogueEngine()
        assert vui.verify_audio_resampling(16000, 48000) is True
        assert vui.verify_audio_resampling(44100, 48000) is False

    def test_tc7_vui_bargein_stt_gain_attenuation_during_tts(self):
        """Validates 0.1x stt_gain during TTS playback for natural barge-in."""
        vui = VUIDialogueEngine()
        assert vui.stt_gain == 1.0

        vui.set_tts_active(True)
        assert vui.stt_gain == 0.1

        vui.set_tts_active(False)
        assert vui.stt_gain == 1.0


# ============================================================================
# TC8: Hierarchical Hybrid Navigation (Nav2 + NOMAD + YOLO) Tests
# ============================================================================
class TestTC8HierarchicalHybridNavigation:
    """Verifies TC8 acceptance criteria: macro nav, NOMAD handover, YOLO conf > 0.55, stop < 0.30m."""

    def test_tc8_hybrid_phase1_macro_navigation_to_centroid(self):
        """Phase 1: Dispatches Nav2 macro-navigation to room centroid."""
        coord = HybridSearchCoordinator()
        msg = coord.start_mission(room_name="cucina", target_class="bottiglia")

        assert coord.current_phase == HybridSearchCoordinator.PHASE_1_NAV2_MACRO
        assert "bottiglia" in msg
        assert "cucina" in msg

    def test_tc8_hybrid_phase2_handover_to_nomad_on_arrival(self):
        """Phase 2: On Nav2 arrival, disengages Nav2 and activates NOMAD reactive search."""
        coord = HybridSearchCoordinator()
        coord.start_mission("salotto", "persona")
        assert coord.nomad_active is False

        coord.notify_nav2_arrival()
        assert coord.nav2_goal_reached is True
        assert coord.current_phase == HybridSearchCoordinator.PHASE_2_NOMAD_REACTIVE
        assert coord.nomad_active is True

    def test_tc8_hybrid_phase3_yolo_confidence_gating(self):
        """Phase 3: Gating requires YOLO detection confidence >= 0.55."""
        coord = HybridSearchCoordinator()
        coord.start_mission("studio", "chiavi")
        coord.notify_nav2_arrival()

        # Detections below 0.55 confidence rejected
        low_conf = [{"class": "chiavi", "confidence": 0.42, "distance_m": 1.5}]
        res_low = coord.process_hailo_yolo_detections(low_conf)
        assert res_low is None
        assert coord.current_phase == HybridSearchCoordinator.PHASE_2_NOMAD_REACTIVE

        # Detection above 0.55 accepted
        high_conf = [{"class": "chiavi", "confidence": 0.78, "distance_m": 1.2}]
        res_high = coord.process_hailo_yolo_detections(high_conf)
        assert res_high is not None
        assert coord.current_phase == HybridSearchCoordinator.PHASE_3_VISUAL_SERVOING
        assert coord.nomad_active is False

    def test_tc8_hybrid_phase3_visual_servoing_proximity_stop(self):
        """Phase 3: Visual servoing approaches target and stops robot when distance <= 0.30m."""
        coord = HybridSearchCoordinator()
        coord.start_mission("salotto", "zaino")
        coord.notify_nav2_arrival()
        coord.process_hailo_yolo_detections([{"class": "zaino", "confidence": 0.85, "distance_m": 1.0}])

        # Step 1: 0.60m -> still approaching
        msg1, done1 = coord.execute_visual_servoing_step(0.60)
        assert done1 is False
        assert coord.current_phase == HybridSearchCoordinator.PHASE_3_VISUAL_SERVOING

        # Step 2: 0.28m (<= 0.30m threshold) -> mission complete
        msg2, done2 = coord.execute_visual_servoing_step(0.28)
        assert done2 is True
        assert coord.current_phase == HybridSearchCoordinator.PHASE_COMPLETED
        assert coord.chime_played is True
        assert coord.completion_announced is True

    def test_tc8_hybrid_target_not_found_handling(self):
        """Handles empty detection stream during NOMAD search without crashing."""
        coord = HybridSearchCoordinator()
        coord.start_mission("corridoio", "gatto")
        coord.notify_nav2_arrival()

        res = coord.process_hailo_yolo_detections([])
        assert res is None
        assert coord.current_phase == HybridSearchCoordinator.PHASE_2_NOMAD_REACTIVE


# ============================================================================
# R1 / Feature 1 & 2: Continuous Metric Map & Multimodal Room Registry
# ============================================================================
class TestR1ContinuousMapAndRoomRegistry:
    """Verifies R1 requirements: SQLite WAL spatial schema, point-in-polygon, multimodal fingerprints."""

    def test_r1_room_polygon_and_centroid_registration(self, tmp_path):
        """Registers room polygon and centroid into SQLite WAL database."""
        db_file = tmp_path / "mag_rooms.db"
        registry = SemanticRoomRegistryModel(db_path=db_file)

        polygon = [[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0]]
        centroid = (2.5, 2.0)
        registry.register_room("cucina", centroid, polygon)

        record = registry.get_room("cucina")
        assert record is not None
        assert record["name"] == "cucina"
        assert record["centroid"] == (2.5, 2.0)
        assert len(record["polygon"]) == 4

    def test_r1_point_in_polygon_room_matching(self, tmp_path):
        """Matches arbitrary (x, y) pose to registered room boundary."""
        db_file = tmp_path / "mag_rooms.db"
        registry = SemanticRoomRegistryModel(db_path=db_file)

        # Room 1: Salotto [0..6, 0..5]
        registry.register_room("salotto", (3.0, 2.5), [[0.0, 0.0], [6.0, 0.0], [6.0, 5.0], [0.0, 5.0]])
        # Room 2: Corridoio [6..10, 0..2]
        registry.register_room("corridoio", (8.0, 1.0), [[6.0, 0.0], [10.0, 0.0], [10.0, 2.0], [6.0, 2.0]])

        # Inside salotto
        assert registry.match_room_by_pose(3.0, 2.0) == "salotto"
        # Inside corridoio
        assert registry.match_room_by_pose(8.0, 1.5) == "corridoio"
        # Outside any room
        assert registry.match_room_by_pose(15.0, 15.0) is None

    def test_r1_multimodal_fingerprints_persistence(self, tmp_path):
        """Persists CosPlace 512D embeddings and 360° LiDAR signature blobs."""
        db_file = tmp_path / "mag_rooms.db"
        registry = SemanticRoomRegistryModel(db_path=db_file)

        dummy_vpr = [np.random.randn(512).astype(np.float32) for _ in range(3)]
        dummy_lidar = np.linspace(0.5, 4.0, 360).astype(np.float32)

        registry.register_room(
            name="camera",
            centroid=(1.0, 1.0),
            polygon=[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]],
            vpr_fingerprints=dummy_vpr,
            lidar_signature=dummy_lidar
        )

        conn = sqlite3.connect(db_file)
        cur = conn.cursor()
        cur.execute("SELECT vpr_fingerprint_blob, lidar_signature_blob FROM rooms WHERE name = 'camera';")
        row = cur.fetchone()
        conn.close()

        assert row[0] is not None
        assert row[1] is not None
        assert len(row[0]) == 3 * 512 * 4  # 3 vectors * 512 floats * 4 bytes
        assert len(row[1]) == 360 * 4      # 360 floats * 4 bytes

    def test_r1_sqlite_wal_mode_and_synchronous_normal(self, tmp_path):
        """Enforces SQLite PRAGMA journal_mode=WAL and synchronous=NORMAL (FM-TRI-001)."""
        db_file = tmp_path / "mag_rooms.db"
        registry = SemanticRoomRegistryModel(db_path=db_file)

        conn = registry.get_connection()
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode;")
        journal_mode = cur.fetchone()[0]
        cur.execute("PRAGMA synchronous;")
        synchronous = cur.fetchone()[0]
        conn.close()

        assert journal_mode.upper() == "WAL"
        # In SQLite: 1 = NORMAL
        assert synchronous == 1

    def test_r1_single_continuous_map_per_floor(self, tmp_path):
        """Verifies multi-room partition indexing on a single continuous map coordinate frame."""
        db_file = tmp_path / "mag_rooms.db"
        registry = SemanticRoomRegistryModel(db_path=db_file)

        rooms = ["sala", "cucina", "bagno", "camera"]
        for i, r in enumerate(rooms):
            registry.register_room(
                name=r,
                centroid=(i * 5.0 + 2.5, 2.5),
                polygon=[[i * 5.0, 0.0], [(i + 1) * 5.0, 0.0], [(i + 1) * 5.0, 5.0], [i * 5.0, 5.0]]
            )

        for i, r in enumerate(rooms):
            matched = registry.match_room_by_pose(i * 5.0 + 2.0, 2.0)
            assert matched == r
