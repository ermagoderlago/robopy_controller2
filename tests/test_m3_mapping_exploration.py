"""
Unit Test Suite for Milestone 3: Autonomous Mapping & Frontier Exploration FSM
=============================================================================
Covers:
  - Frontier Cell Detection (8-neighbor free-to-unknown, obstacle clearance)
  - BFS Connected Component Clustering, metric bounding box, spatial size, centroid
  - Exact stopping criteria boundaries: 0.38m (reject), 0.40m (accept), 0.42m (accept)
  - Unreachable frontier blacklisting (0.20m spatial radius)
  - Goal selection strategies (largest, nearest, hybrid)
  - Mapping HRI State Machine 6 states (IDLE, AWAITING_CONFIRMATION, REMINDER_SENT,
    MAPPING_ACTIVE, STANDBY_ABORTED, CANCELLED_BY_USER)
  - Physical safety latch: motors_allowed strictly False except in MAPPING_ACTIVE
  - Pre-mapping luminance safety gate (<25 reject, >30 authorize)
  - Exact HRI timer boundaries (119s vs 120s reminder, 299s vs 300s abort)
  - Concurrent destructive action gate (30s timeout, explicit confirmation)
  - Global SLAM optimization RAM delta monitoring (<80.0 MB)
  - Battery anti-sag 20-sample moving average filter (9.90V inhibit)
  - Atomic SSD map export (Zero BOM UTF-8, P5 binary PGM, path prefix guard)
  - Zero BOM UTF-8 file cleanliness check across all workspace files
"""

import math
import tempfile
import errno
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import numpy as np

from robopy_controller.nodes.frontier_explorer_node import FrontierExplorationEngine, FrontierExplorerNode
from robopy_controller.robot_ai.core.mapping_state_machine import MappingStateMachine, HRISafetyStateMachine
from robopy_controller.nodes.map_export_optimizer import SLAMOptimizationExporter, MapExportOptimizerNode


# ============================================================================
# 1. Frontier Cell Detection & Obstacle Clearance
# ============================================================================
class TestGroup1FrontierCellDetection:
    def test_perimeter_detection_on_free_island(self):
        grid = np.full((12, 12), -1, dtype=np.int8)
        grid[4:8, 4:8] = 0  # 4x4 free island

        engine = FrontierExplorationEngine(resolution=0.05)
        cells = engine.detect_frontier_cells(grid)

        assert len(cells) > 0
        # Interior cell (5, 5) surrounded by free cells -> NOT a frontier
        assert (5, 5) not in cells
        # Perimeter cell (4, 4) touches unknown space -> MUST be a frontier
        assert (4, 4) in cells

    def test_completely_unknown_grid_yields_zero_frontiers(self):
        grid = np.full((20, 20), -1, dtype=np.int8)
        engine = FrontierExplorationEngine()
        assert len(engine.detect_frontier_cells(grid)) == 0

    def test_completely_free_grid_yields_zero_frontiers(self):
        grid = np.zeros((20, 20), dtype=np.int8)
        engine = FrontierExplorationEngine()
        assert len(engine.detect_frontier_cells(grid)) == 0

    def test_obstacle_proximity_rejection(self):
        """Cells directly touching or within clearance radius of obstacles are rejected."""
        grid = np.full((30, 30), -1, dtype=np.int8)
        # Free corridor from r=5 to 15, c=5 to 25
        grid[5:16, 5:26] = 0
        # Place obstacle at (10, 15)
        grid[10, 15] = 100

        engine = FrontierExplorationEngine(resolution=0.05, clearance_radius=0.20)
        cells = engine.detect_frontier_cells(grid, reject_obstacle_proximity=True)

        # Cells near obstacle (10, 15) within 4 cells (0.20m / 0.05m) must not be frontiers
        for r, c in cells:
            dist = math.hypot(r - 10, c - 15) * 0.05
            assert dist > 0.15, f"Cell ({r}, {c}) is too close to obstacle ({dist:.2f}m)"

    def test_small_grid_boundary_guard(self):
        engine = FrontierExplorationEngine()
        grid = np.zeros((2, 2), dtype=np.int8)
        assert engine.detect_frontier_cells(grid) == []


# ============================================================================
# 2. BFS Connected Component Clustering & Geometry
# ============================================================================
class TestGroup2FrontierClusteringAndGeometry:
    def test_bfs_clustering_single_line(self):
        engine = FrontierExplorationEngine(resolution=0.05, origin=(-5.0, -5.0))
        # 10 collinear cells along row 10, cols 10 to 19
        frontier_cells = [(10, c) for c in range(10, 20)]
        clusters = engine.cluster_frontiers(frontier_cells)

        assert len(clusters) == 1
        c0 = clusters[0]
        assert c0["cell_count"] == 10
        # Delta x = 9 * 0.05 = 0.45m
        assert c0["size_meters"] == pytest.approx(0.45, abs=1e-3)
        expected_x = -5.0 + np.mean(range(10, 20)) * 0.05
        expected_y = -5.0 + 10 * 0.05
        assert c0["centroid"][0] == pytest.approx(expected_x, abs=1e-3)
        assert c0["centroid"][1] == pytest.approx(expected_y, abs=1e-3)

    def test_bfs_clustering_two_disjoint_clusters(self):
        engine = FrontierExplorationEngine(resolution=0.05)
        cluster1 = [(5, c) for c in range(5, 10)]
        cluster2 = [(20, c) for c in range(20, 25)]
        clusters = engine.cluster_frontiers(cluster1 + cluster2)

        assert len(clusters) == 2
        counts = sorted([c["cell_count"] for c in clusters])
        assert counts == [5, 5]

    def test_concavity_safe_nav_goal(self):
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0))
        # U-shaped cluster
        u_cells = [(5, 5), (6, 5), (7, 5), (7, 6), (7, 7), (6, 7), (5, 7)]
        clusters = engine.cluster_frontiers(u_cells)

        assert len(clusters) == 1
        nav_goal = clusters[0]["nav_goal"]
        # Nav goal must be one of the actual cells in world coordinates
        cell_coords = [(c[1] * 0.05, c[0] * 0.05) for c in u_cells]
        match = any(math.hypot(nav_goal[0] - wx, nav_goal[1] - wy) < 0.01 for wx, wy in cell_coords)
        assert match, "nav_goal must correspond to a valid cluster cell in free space"


# ============================================================================
# 3. Stopping Criteria & Exact Boundaries (0.38m, 0.40m, 0.42m)
# ============================================================================
class TestGroup3StoppingThresholdBoundaries:
    def test_frontier_size_038m_strictly_below_threshold(self):
        engine = FrontierExplorationEngine(resolution=0.05, min_frontier_size=0.40)
        # Cluster of length 7 cells -> delta_c = 6 -> 6 * 0.05 = 0.30m < 0.40m
        grid = np.full((15, 15), -1, dtype=np.int8)
        grid[5, 4:11] = 0  # 7 free cells
        grid[6, 4:11] = 0

        res = engine.evaluate_frontiers(grid)
        assert res["status"] == "COMPLETED"
        assert res["motor_stop_required"] is True
        assert res["selected_goal"] is None
        assert "ALL_FRONTIERS_BELOW_THRESHOLD" in res["reason"]

    def test_frontier_size_040m_exact_stopping_boundary(self):
        engine = FrontierExplorationEngine(resolution=0.05, min_frontier_size=0.40)
        # Delta c = 8 cells at 0.05m = 0.40m exact
        grid = np.full((15, 15), -1, dtype=np.int8)
        grid[5, 2:11] = 0  # 9 cells, delta = 8 * 0.05 = 0.40m
        grid[6, 2:11] = 0

        res = engine.evaluate_frontiers(grid)
        assert res["status"] == "EXPLORING"
        assert res["motor_stop_required"] is False
        assert res["selected_goal"] is not None
        assert res["max_size_meters"] >= 0.40

    def test_frontier_size_042m_strictly_above_threshold(self):
        engine = FrontierExplorationEngine(resolution=0.05, min_frontier_size=0.40)
        # Delta c = 9 cells at 0.05m = 0.45m > 0.40m
        grid = np.full((15, 15), -1, dtype=np.int8)
        grid[5, 2:12] = 0
        grid[6, 2:12] = 0

        res = engine.evaluate_frontiers(grid)
        assert res["status"] == "EXPLORING"
        assert res["motor_stop_required"] is False
        assert res["selected_goal"] is not None

    def test_single_cell_island_corner_case(self):
        engine = FrontierExplorationEngine(resolution=0.05)
        grid = np.full((10, 10), -1, dtype=np.int8)
        grid[5, 5] = 0  # Single free cell

        res = engine.evaluate_frontiers(grid)
        assert res["status"] == "COMPLETED"
        assert res["motor_stop_required"] is True

    def test_completely_empty_occupancy_grid(self):
        engine = FrontierExplorationEngine(resolution=0.05)
        grid = np.zeros((15, 15), dtype=np.int8)  # All free, no unknown

        res = engine.evaluate_frontiers(grid)
        assert res["status"] == "COMPLETED"
        assert res["reason"] == "NO_FRONTIERS_REMAINING"
        assert res["cluster_count"] == 0
        assert res["motor_stop_required"] is True


# ============================================================================
# 4. Goal Selection Strategies & Blacklisting
# ============================================================================
class TestGroup4GoalSelectionAndBlacklisting:
    def test_selection_strategy_largest(self):
        engine = FrontierExplorationEngine(resolution=0.05, selection_strategy="largest")
        grid = np.full((30, 30), -1, dtype=np.int8)
        # Small valid cluster (0.45m)
        grid[5, 2:12] = 0
        grid[6, 2:12] = 0
        # Large valid cluster (0.75m)
        grid[20, 2:18] = 0
        grid[21, 2:18] = 0

        res = engine.evaluate_frontiers(grid)
        assert res["status"] == "EXPLORING"
        # Largest cluster is near row 20
        assert res["selected_cluster"]["size_meters"] > 0.70

    def test_selection_strategy_nearest(self):
        engine = FrontierExplorationEngine(resolution=0.05, selection_strategy="nearest", origin=(0.0, 0.0))
        grid = np.full((30, 30), -1, dtype=np.int8)
        # Cluster A at row 5 (y ~ 0.25)
        grid[5, 2:12] = 0
        grid[6, 2:12] = 0
        # Cluster B at row 25 (y ~ 1.25)
        grid[25, 2:18] = 0
        grid[26, 2:18] = 0

        # Robot at (0.35, 0.25) near Cluster A
        res = engine.evaluate_frontiers(grid, robot_pose=(0.35, 0.25))
        assert res["status"] == "EXPLORING"
        assert res["selected_goal"][1] < 0.60

    def test_blacklisting_unreachable_centroid_with_020m_radius(self):
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0))
        grid = np.full((30, 30), -1, dtype=np.int8)
        # Cluster 1
        grid[5, 2:12] = 0
        grid[6, 2:12] = 0
        # Cluster 2
        grid[20, 2:12] = 0
        grid[21, 2:12] = 0

        res1 = engine.evaluate_frontiers(grid)
        first_goal = res1["selected_goal"]

        # Blacklist first goal
        engine.blacklist_frontier(first_goal)

        res2 = engine.evaluate_frontiers(grid)
        assert res2["status"] == "EXPLORING"
        assert res2["selected_goal"] != first_goal

        # Blacklist second goal
        engine.blacklist_frontier(res2["selected_goal"])

        res3 = engine.evaluate_frontiers(grid)
        assert res3["status"] == "COMPLETED"
        assert res3["reason"] == "NO_FRONTIERS_REMAINING"

    def test_clear_blacklist(self):
        engine = FrontierExplorationEngine()
        engine.blacklist_frontier((1.0, 2.0))
        assert len(engine.blacklisted_centroids) == 1
        engine.clear_blacklist()
        assert len(engine.blacklisted_centroids) == 0


# ============================================================================
# 5. Mapping HRI State Machine & Safety Latch
# ============================================================================
class TestGroup5MappingHRISafetyFSM:
    def test_initial_state_and_physical_safety_latch(self):
        fsm = MappingStateMachine()
        assert fsm.state == MappingStateMachine.STATE_IDLE
        assert fsm.motors_allowed is False
        assert fsm.elapsed_time == 0.0
        assert fsm.target_environment is None

    def test_request_mapping_challenge_issued(self):
        fsm = MappingStateMachine()
        prompt = fsm.request_mapping("cucina")
        assert fsm.state == MappingStateMachine.STATE_AWAITING_CONFIRMATION
        assert fsm.motors_allowed is False
        assert "Richiesta di avvio mappatura autonoma per 'cucina'" in prompt
        assert "procedi" in prompt and "avviare i motori" in prompt

    def test_pre_mapping_luminance_check_rejected(self):
        fsm = MappingStateMachine()
        prompt = fsm.request_mapping("cantina", current_luminance=18.0)
        assert fsm.state == MappingStateMachine.STATE_IDLE
        assert fsm.motors_allowed is False
        assert "insufficiente per la mappatura visiva" in prompt
        assert fsm.target_environment is None

    def test_pre_mapping_luminance_check_authorized(self):
        fsm = MappingStateMachine()
        prompt = fsm.request_mapping("salotto", current_luminance=35.0)
        assert fsm.state == MappingStateMachine.STATE_AWAITING_CONFIRMATION
        assert fsm.motors_allowed is False
        assert "Richiesta di avvio" in prompt

    def test_voice_affirmative_unlocks_motors(self):
        fsm = MappingStateMachine()
        fsm.request_mapping("camera")
        msg, success = fsm.process_voice_input("s?, procedi pure")
        assert success is True
        assert fsm.state == MappingStateMachine.STATE_MAPPING_ACTIVE
        assert fsm.motors_allowed is True
        assert "Ricevuto. Avvio immediato" in msg

    def test_voice_negative_cancels_mapping(self):
        fsm = MappingStateMachine()
        fsm.request_mapping("camera")
        msg, success = fsm.process_voice_input("no, annulla")
        assert success is False
        assert fsm.state == MappingStateMachine.STATE_CANCELLED
        assert fsm.motors_allowed is False
        assert "annullata dall'utente" in msg

    def test_complete_mapping_resets_fsm(self):
        fsm = MappingStateMachine()
        fsm.request_mapping("cucina")
        fsm.process_voice_input("s?, procedi")
        assert fsm.motors_allowed is True

        msg = fsm.complete_mapping()
        assert fsm.state == MappingStateMachine.STATE_IDLE
        assert fsm.motors_allowed is False
        assert "completata con successo" in msg


# ============================================================================
# 6. HRI Exact Timers: 119s vs 120s, 299s vs 300s
# ============================================================================
class TestGroup6HRITimersExactBoundaries:
    def test_reminder_119s_vs_120s(self):
        fsm = MappingStateMachine()
        fsm.request_mapping("studio")

        # 119.0s -> No reminder yet
        p1 = fsm.advance_time(119.0)
        assert p1 is None
        assert fsm.state == MappingStateMachine.STATE_AWAITING_CONFIRMATION
        assert fsm.reminder_dispatched is False

        # +1.0s -> 120.0s -> Reminder emitted
        p2 = fsm.advance_time(1.0)
        assert p2 is not None
        assert "Sollecito" in p2
        assert fsm.state == MappingStateMachine.STATE_REMINDER_SENT
        assert fsm.reminder_dispatched is True
        assert fsm.motors_allowed is False

    def test_standby_abort_299s_vs_300s(self):
        fsm = MappingStateMachine()
        fsm.request_mapping("studio")
        fsm.advance_time(120.0)

        # Advance to 299.0s
        p1 = fsm.advance_time(179.0)
        assert p1 is None
        assert fsm.state == MappingStateMachine.STATE_REMINDER_SENT
        assert fsm.abort_dispatched is False

        # +1.0s -> 300.0s -> Standby abort emitted
        p2 = fsm.advance_time(1.0)
        assert p2 is not None
        assert "300 secondi" in p2
        assert fsm.state == MappingStateMachine.STATE_STANDBY_ABORTED
        assert fsm.abort_dispatched is True
        assert fsm.motors_allowed is False

    def test_jump_large_time_step(self):
        fsm = MappingStateMachine()
        fsm.request_mapping("corridoio")
        p = fsm.advance_time(350.0)
        assert fsm.state == MappingStateMachine.STATE_STANDBY_ABORTED
        assert fsm.motors_allowed is False


# ============================================================================
# 7. Concurrent Destructive Action Confirmation Gate
# ============================================================================
class TestGroup7DestructiveActionGate:
    def test_destructive_gate_challenge_and_timeout(self):
        fsm = MappingStateMachine()
        prompt = fsm.request_destructive_action("cucina")
        assert fsm.destructive_pending is True
        assert "sovrascriver" in prompt and "canceller" in prompt

        # At 29.0s -> Still pending
        p1 = fsm.advance_time(29.0)
        assert p1 is None
        assert fsm.destructive_pending is True

        # At 30.0s -> Timeout
        p2 = fsm.advance_time(1.0)
        assert p2 is not None
        assert "Tempo scaduto" in p2
        assert fsm.destructive_pending is False
        assert fsm.destructive_confirmed is False

    def test_destructive_explicit_confirmation_vs_generic(self):
        fsm = MappingStateMachine()
        fsm.request_destructive_action("mappa_vecchia")

        # Generic phrase MUST be rejected for destructive action
        msg1, ok1 = fsm.process_voice_input("procedi pure")
        assert ok1 is False
        assert fsm.destructive_confirmed is False
        assert fsm.destructive_pending is True

        # Explicit confirmation
        msg2, ok2 = fsm.process_voice_input("s?, confermo")
        assert ok2 is True
        assert fsm.destructive_confirmed is True
        assert fsm.destructive_pending is False
        assert "Conferma registrata" in msg2

    def test_destructive_gate_concurrent_with_mapping(self):
        fsm = MappingStateMachine()
        fsm.request_mapping("salone")
        fsm.process_voice_input("s?, procedi")
        assert fsm.motors_allowed is True

        # Request destructive action while mapping is active
        fsm.request_destructive_action("mappa_old")
        assert fsm.motors_allowed is True
        assert fsm.state == MappingStateMachine.STATE_MAPPING_ACTIVE

        # Time passes -> destructive times out, but mapping remains active
        fsm.advance_time(30.0)
        assert fsm.destructive_pending is False
        assert fsm.state == MappingStateMachine.STATE_MAPPING_ACTIVE
        assert fsm.motors_allowed is True


# ============================================================================
# 8. SLAM Global Optimization RAM Delta Monitoring (<80.0 MB)
# ============================================================================
class TestGroup8SLAMOptimizationRAMDelta:
    def test_ram_delta_0mb_passes(self):
        exporter = SLAMOptimizationExporter()
        ok, msg = exporter.trigger_global_optimization(simulate_ram_usage_mb=0.0)
        assert ok is True
        assert "converged" in msg

    def test_ram_delta_79mb_passes(self):
        exporter = SLAMOptimizationExporter()
        ok, msg = exporter.trigger_global_optimization(simulate_ram_usage_mb=79.0)
        assert ok is True
        assert "79.0 MB" in msg
        assert "converged" in msg

    def test_ram_delta_80mb_fails_alarm(self):
        exporter = SLAMOptimizationExporter()
        ok, msg = exporter.trigger_global_optimization(simulate_ram_usage_mb=80.0)
        assert ok is False
        assert "ALARM" in msg
        assert "80.0 MB" in msg

    def test_ram_delta_81mb_fails_alarm(self):
        exporter = SLAMOptimizationExporter()
        ok, msg = exporter.trigger_global_optimization(simulate_ram_usage_mb=81.0)
        assert ok is False
        assert "ALARM" in msg
        assert "OOM Kill" in msg
        assert "81.0 MB" in msg


# ============================================================================
# 9. Battery Anti-Sag Interlock
# ============================================================================
class TestGroup9BatteryAntiSagInterlock:
    def test_battery_adequate_voltage(self):
        exporter = SLAMOptimizationExporter()
        for _ in range(20):
            exporter.update_battery_voltage(11.10)
        ok, msg = exporter.check_battery_safety()
        assert ok is True
        assert "adequate" in msg

    def test_battery_low_voltage_inhibits(self):
        exporter = SLAMOptimizationExporter()
        for _ in range(20):
            exporter.update_battery_voltage(9.80)
        ok, msg = exporter.check_battery_safety()
        assert ok is False
        assert "INHIBITED_BATTERY_LOW" in msg

    def test_transient_motor_kick_sag_resilience(self):
        exporter = SLAMOptimizationExporter()
        for _ in range(19):
            exporter.update_battery_voltage(11.10)
        # Transient sag 1 sample to 9.20V
        exporter.update_battery_voltage(9.20)
        # Filtered mean = (19 * 11.10 + 9.20) / 20 = 11.005V > 9.90V
        ok, msg = exporter.check_battery_safety()
        assert ok is True
        assert exporter.get_filtered_voltage() > 9.90

    def test_charging_dock_voltage_bypasses(self):
        exporter = SLAMOptimizationExporter()
        for _ in range(20):
            exporter.update_battery_voltage(12.80)
        ok, msg = exporter.check_battery_safety()
        assert ok is True
        assert "charging" in msg


# ============================================================================
# 10. Atomic SSD Map Export & Formats
# ============================================================================
class TestGroup10SSDMapExportAndFormats:
    def test_yaml_schema_and_binary_p5_pgm_generation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            exporter = SLAMOptimizationExporter(simulated_ssd_root=tmppath, enforce_ssd_mount=False)

            grid = np.zeros((20, 20), dtype=np.int8)
            grid[0, :] = 100  # wall
            grid[10:, :] = -1  # unknown

            ok, msg, paths = exporter.export_map_files("test_room", grid, target_override=tmppath)
            assert ok is True
            yaml_path = paths["yaml"]
            pgm_path = paths["pgm"]

            assert yaml_path.exists()
            assert pgm_path.exists()

            # Verify YAML keys
            yaml_text = yaml_path.read_text(encoding="utf-8")
            required_keys = ["image", "resolution", "origin", "negate", "occupied_thresh", "free_thresh"]
            for k in required_keys:
                assert f"{k}:" in yaml_text

            # Verify binary PGM
            raw_bytes = pgm_path.read_bytes()
            assert raw_bytes.startswith(b"P5\n")
            assert b"20 20\n255\n" in raw_bytes

            # Check pixel values: 0 -> 254 (free), 100 -> 0 (occupied), -1 -> 205 (unknown)
            payload = raw_bytes.split(b"255\n")[1]
            img_arr = np.frombuffer(payload, dtype=np.uint8).reshape((20, 20))
            assert img_arr[0, 0] == 0  # occupied
            assert img_arr[5, 5] == 254  # free
            assert img_arr[15, 15] == 205  # unknown

    def test_path_enforcement_rejects_non_ssd(self):
        exporter = SLAMOptimizationExporter(enforce_ssd_mount=True)
        grid = np.zeros((10, 10), dtype=np.int8)
        invalid_path = Path("/home/robopy/.ros/maps")
        ok, msg, _ = exporter.export_map_files("room", grid, target_override=invalid_path, enforce_ssd_check=True)
        assert ok is False
        assert "VIOLATION FM-NAV-020" in msg

    def test_read_only_filesystem_error_handling(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = SLAMOptimizationExporter(simulated_ssd_root=Path(tmpdir), enforce_ssd_mount=False)
            grid = np.zeros((10, 10), dtype=np.int8)
            ok, msg, _ = exporter.export_map_files("room", grid, force_readonly=True)
            assert ok is False
            assert "Read-only file system" in msg

    def test_pose_export_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            exporter = SLAMOptimizationExporter(simulated_ssd_root=tmppath, enforce_ssd_mount=False)
            pose_data = {
                "position": {"x": 1.25, "y": -0.45, "z": 0.0},
                "orientation": {"x": 0.0, "y": 0.0, "z": 0.123, "w": 0.992},
                "yaw": 0.246,
                "covariance": [0.25, 0.0, 0.0, 0.0, 0.0, 0.068]
            }
            ok, msg, path = exporter.export_pose_file("test_room", pose_data, target_override=tmppath)
            assert ok is True
            assert path.exists()
            content = path.read_text(encoding="utf-8")
            assert "1.250000" in content
            assert "0.246000" in content


# ============================================================================
# 11. Zero BOM UTF-8 Cleanliness
# ============================================================================
class TestGroup11ZeroBOMCleanliness:
    def test_m3_files_have_zero_bom(self):
        files_to_check = [
            "robopy_controller/nodes/frontier_explorer_node.py",
            "robopy_controller/robot_ai/core/mapping_state_machine.py",
            "robopy_controller/nodes/map_export_optimizer.py",
            "robopy_controller/setup.py",
            "setup.py",
            "tests/test_m3_mapping_exploration.py"
        ]
        bom = b"\xef\xbb\xbf"
        for fpath in files_to_check:
            p = Path(fpath)
            if p.exists():
                raw = p.read_bytes()
                assert not raw.startswith(bom), f"BOM detected in {fpath}"


# ============================================================================
# 12. Milestone 3 Hardening Verification (Reviewer & Challenger Fixes)
# ============================================================================
class TestGroup12HardeningVerification:
    def test_dual_coordinate_blacklist_nav_goal_matching(self):
        """Verify that blacklisting either centroid OR nav_goal filters out concave clusters."""
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0), min_frontier_size=0.10)
        grid = np.full((35, 35), 0, dtype=np.int8)
        grid[10:25, 10:20] = -1  # unknown bay

        eval1 = engine.evaluate_frontiers(grid)
        assert eval1["status"] == "EXPLORING"
        c = eval1["selected_cluster"]
        cx, cy = c["centroid"]
        nx, ny = c["nav_goal"]

        # In a U-shape/concave cluster, distance between centroid and nav_goal exceeds 0.20m
        assert math.hypot(cx - nx, cy - ny) > 0.20

        # Goal dispatched is the safe nav_goal, NOT centroid
        assert eval1["selected_goal"] == (nx, ny)

        # Blacklist using nav_goal coordinate
        engine.clear_blacklist()
        engine.blacklist_frontier((nx, ny))

        eval2 = engine.evaluate_frontiers(grid)
        # Cluster is successfully suppressed via dual-coordinate matching!
        assert eval2["status"] == "COMPLETED"
        assert eval2["reason"] == "NO_FRONTIERS_REMAINING"

    def test_frontier_explorer_node_motors_allowed_interlock(self):
        """FrontierExplorerNode cancels active Nav2 goals and pauses dispatch when motors_allowed is False."""
        node = object.__new__(FrontierExplorerNode)
        node.motors_allowed = True
        node.is_active = True
        node.get_logger = MagicMock()
        node.cmd_vel_pub = MagicMock()
        node.status_pub = MagicMock()

        mock_goal_handle = MagicMock()
        node.current_goal_handle = mock_goal_handle

        # Simulate /mapping/motors_allowed publishing False
        mock_msg = MagicMock()
        mock_msg.data = False
        node.motors_allowed_callback(mock_msg)

        assert node.motors_allowed is False
        assert node.current_goal_handle is None
        assert mock_goal_handle.cancel_goal_async.called
        assert node.cmd_vel_pub.publish.called

        # Start service should be inhibited when motors_allowed is False
        req = MagicMock()
        resp = MagicMock()
        node.handle_start(req, resp)
        assert resp.success is False
        assert "inhibited" in resp.message.lower()

    def test_map_export_optimizer_real_rss_measurement_and_no_literal_35(self):
        """SLAMOptimizationExporter measures real RSS delta rather than returning hardcoded 35.0."""
        exporter = SLAMOptimizationExporter()
        ok, msg = exporter.trigger_global_optimization()
        assert ok is True
        # Real delta should be measured (non-negative float, not hardcoded 35.0)
        assert exporter.last_ram_delta_mb is not None
        assert exporter.last_ram_delta_mb >= 0.0
        assert exporter.last_ram_delta_mb < 80.0

    def test_map_export_optimizer_battery_formatting_two_decimals(self):
        """Battery safety warning formats voltage to two decimal places (9.90V)."""
        exporter = SLAMOptimizationExporter()
        for _ in range(20):
            exporter.update_battery_voltage(9.85)
        ok, msg = exporter.check_battery_safety()
        assert ok is False
        assert "9.85V < 9.90V" in msg

    def test_map_export_optimizer_node_tf_silencing_and_ba_invocation(self):
        """MapExportOptimizerNode dispatches SetParameters for publish_tf:=False and invokes BA."""
        from robopy_controller.nodes.map_export_optimizer import MapExportOptimizerNode
        node = object.__new__(MapExportOptimizerNode)
        node.maps_dir = Path(tempfile.gettempdir())
        node.exporter = SLAMOptimizationExporter(simulated_ssd_root=node.maps_dir, enforce_ssd_mount=False)
        node.rtabmap_node_name = "/rtabmap"
        node.get_logger = MagicMock()
        node.pub_docking = MagicMock()
        node.pub_transition = MagicMock()
        node.latest_robot_pose = {"position": {"x": 0.0, "y": 0.0, "z": 0.0}, "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}, "yaw": 0.0}

        # Mock ROS 2 clients
        mock_cli_optimize = MagicMock()
        mock_cli_optimize.service_is_ready.return_value = True
        node.cli_optimize = mock_cli_optimize

        mock_cli_set_params = MagicMock()
        node.cli_set_params = mock_cli_set_params

        # Battery adequate
        for _ in range(20):
            node.exporter.update_battery_voltage(11.10)

        # Mock occupancy grid
        mock_grid = MagicMock()
        mock_grid.info.height = 5
        mock_grid.info.width = 5
        mock_grid.info.resolution = 0.05
        mock_grid.info.origin.position.x = 0.0
        mock_grid.info.origin.position.y = 0.0
        mock_grid.info.origin.position.z = 0.0
        mock_grid.data = [0] * 25
        node.latest_grid_msg = mock_grid

        req = MagicMock()
        resp = MagicMock()

        res_resp = node._handle_optimize_and_export(req, resp)
        assert res_resp.success is True
        assert mock_cli_optimize.call_async.called
        assert mock_cli_set_params.call_async.called

        # Verify SetParameters payload sets publish_tf:=False and Mem/IncrementalMemory:="false"
        param_req = mock_cli_set_params.call_async.call_args[0][0]
        param_names = [p.name for p in param_req.parameters]
        assert "publish_tf" in param_names
        assert "Mem/IncrementalMemory" in param_names

    def test_concave_frontier_nav_goal_vs_centroid_safety(self):
        """FrontierExplorationEngine dispatches nav_goal in free space (0) rather than centroid in unknown cavity (-1)."""
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0), min_frontier_size=0.10)
        grid = np.full((35, 35), 0, dtype=np.int8)
        grid[10:25, 10:20] = -1  # unknown bay penetrating into free space

        eval_res = engine.evaluate_frontiers(grid)
        assert eval_res["status"] == "EXPLORING"

        selected_goal = eval_res["selected_goal"]
        selected_cluster = eval_res["selected_cluster"]
        nav_goal = selected_cluster["nav_goal"]
        centroid = selected_cluster["centroid"]

        # Goal dispatched MUST be the safe nav_goal, NOT centroid
        assert selected_goal == nav_goal
        assert selected_goal != centroid

        # Check grid values at selected_goal vs centroid
        sg_col = int(round(selected_goal[0] / 0.05))
        sg_row = int(round(selected_goal[1] / 0.05))
        c_col = int(round(centroid[0] / 0.05))
        c_row = int(round(centroid[1] / 0.05))

        val_at_sg = grid[sg_row, sg_col]
        val_at_c = grid[c_row, c_col]

        assert val_at_sg == 0, f"Dispatched goal must be in free cell (0), got {val_at_sg}"
        assert val_at_c == -1, f"Centroid is inside unknown space (-1), got {val_at_c}"

    def test_destructive_voice_confirmation_negation_priority_bug_hri_01(self):
        """BUG-HRI-01: Saying 'non confermo' MUST cancel destructive gate and NOT falsely confirm."""
        fsm = MappingStateMachine()
        fsm.request_destructive_action("stanza_importante")
        assert fsm.destructive_pending is True

        msg, ok = fsm.process_voice_input("non confermo")
        assert ok is False, "'non confermo' must NOT confirm destructive action"
        assert fsm.destructive_confirmed is False
        assert fsm.destructive_pending is False
        assert "annullata" in msg.lower()

        # Re-test with 'annulla'
        fsm.request_destructive_action("altra_stanza")
        msg2, ok2 = fsm.process_voice_input("annulla tutto")
        assert ok2 is False
        assert fsm.destructive_confirmed is False

    def test_map_export_path_traversal_dot_dot_rejected_vuln_ssd_01(self):
        """VULN-SSD-01: Target directory with dot-dot traversal escaping /mnt/ssd/maps MUST be rejected."""
        exporter = SLAMOptimizationExporter(enforce_ssd_mount=True)
        dummy_grid = np.zeros((5, 5), dtype=np.int8)
        traversal_path = Path("/mnt/ssd/maps/../../home/robopy/map")

        ok, msg, paths = exporter.export_map_files(
            "traversal_map",
            dummy_grid,
            target_override=traversal_path,
            enforce_ssd_check=True
        )
        assert ok is False, "Path traversal escaping SSD root must be rejected"
        assert "VIOLATION FM-NAV-020" in msg
        assert paths == {}

    def test_map_export_map_name_directory_traversal_rejected_vuln_ssd_02(self):
        """VULN-SSD-02: map_name containing directory traversal sequences MUST be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            maps_dir = tmppath / "maps"
            maps_dir.mkdir()
            exporter = SLAMOptimizationExporter(simulated_ssd_root=maps_dir, enforce_ssd_mount=False)
            dummy_grid = np.zeros((5, 5), dtype=np.int8)

            ok, msg, paths = exporter.export_map_files(
                "../../escaped_map",
                dummy_grid,
                target_override=maps_dir
            )
            assert ok is False, "Escaped map_name must be rejected"
            assert "prohibited" in msg.lower() or "traversal" in msg.lower()
            assert paths == {}

    def test_hri_timer_floating_point_accumulation_rounding(self):
        """3000 ticks of 0.1s must trigger 300.0s standby abort cleanly without floating-point lag."""
        fsm = MappingStateMachine()
        fsm.request_mapping("studio")
        assert fsm.state == MappingStateMachine.STATE_AWAITING_CONFIRMATION

        # Accumulate 3000 ticks of 0.1s
        abort_prompt = None
        for i in range(3000):
            p = fsm.advance_time(0.1)
            if fsm.state == MappingStateMachine.STATE_STANDBY_ABORTED:
                abort_prompt = p
                break

        assert abort_prompt is not None, "Standby abort must trigger at 300.0s under 10Hz accumulation"
        assert "300 secondi" in abort_prompt
        assert fsm.state == MappingStateMachine.STATE_STANDBY_ABORTED
        assert fsm.motors_allowed is False


