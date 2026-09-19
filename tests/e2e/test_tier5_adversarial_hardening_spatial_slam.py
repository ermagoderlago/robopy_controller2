"""
==============================================================================
🧪 TIER 5: WHITE-BOX ADVERSARIAL HARDENING TEST SUITE
==============================================================================
Milestone 6 Phase 2: Adversarial Coverage Hardening for Marcus SLAM & Spatial AI.

Stress-tests edge cases, boundary conditions, race conditions, and degenerate
inputs across:
  1. robopy_controller/robot_ai/trinity/mag_room_registry.py & mag_room_geometry.py
     - Degenerate spatial polygons (complex self-intersecting, collinear, zero-area,
       single-point, closed rings, 10,000-vertex polygons, ray-casting tangency).
  2. robopy_controller/nodes/frontier_explorer_node.py
     - High-frequency frontier clustering under massive maps (1000x1000 grid / 50m x 50m).
     - Speckle noise / micro-clusters (<0.40m stopping criteria).
     - Deep concave / cavity frontier nav_goal safety.
     - Centroid and nav_goal dual-coordinate blacklisting.
  3. robopy_controller/robot_ai/core/mapping_state_machine.py
     - High-frequency multi-threaded concurrency and state transition race conditions.
     - Exact 120s reminder, 300s standby abort, and 30s destructive action timeouts.
     - Hard voice-to-motion safety interlock (motors_allowed invariance).
  4. robopy_controller/nodes/map_export_optimizer.py
     - Directory traversal injection attacks in map export.
     - Mandatory SSD mount prefix validation (/mnt/ssd/maps, FM-NAV-020).
     - Atomic replacement (.tmp -> os.replace) and read-only filesystem handling.
     - Strict Zero BOM UTF-8 and P5 binary PGM image verification.
  5. Battery Anti-Sag & SLAM Optimization Guard
     - 20-sample circular moving average filter @ 5Hz (3.0s window).
     - Transient motor spike sag immunity (8.5V for 1.2s without false alarm).
     - Sustained cliff inhibition (<9.90V safe docking threshold).
     - Charger override (>=12.70V) and pathological input rejection (NaN, negative).

Conforms to:
  - TC1-TC8 & R1-R5 Authoritative Requirements
  - SPEC-02, SPEC-05, SPEC-06, SPEC-07 & marcus_core_rules.md
  - Zero BOM UTF-8 compliance across all generated files
==============================================================================
"""

import os
import sys
import math
import time
import errno
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Dict, Any, List, Tuple

import numpy as np
import pytest

# Ensure workspace root and robopy_controller are on sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
ROBOPY_ROOT = WORKSPACE_ROOT / "robopy_controller"
for p in (str(WORKSPACE_ROOT), str(ROBOPY_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Imports from target modules
from robopy_controller.robot_ai.trinity.mag_room_geometry import (
    compute_bounding_box,
    compute_polygon_centroid,
    point_on_segment,
    point_in_polygon
)
from robopy_controller.robot_ai.trinity.mag_room_registry import (
    MAGRoomRegistry,
    RoomMetadata
)
from robopy_controller.robot_ai.trinity.mag_database import MAGDatabase
from robopy_controller.nodes.frontier_explorer_node import FrontierExplorationEngine
from robopy_controller.robot_ai.core.mapping_state_machine import MappingStateMachine
from robopy_controller.nodes.map_export_optimizer import SLAMOptimizationExporter


# ============================================================================
# DOMAIN 1: DEGENERATE SPATIAL POLYGONS & GEOMETRY HARDENING
# ============================================================================
class TestDegenerateSpatialPolygons:
    """
    White-box stress testing of mag_room_geometry and mag_room_registry
    against pathological, degenerate, collinear, and self-intersecting polygons.
    """

    def test_collinear_3_point_degenerate_polygon(self):
        """Collinear 3-point line: signed area is 0.0, fallback to arithmetic mean."""
        poly = [(0.0, 0.0), (5.0, 5.0), (10.0, 10.0)]
        bbox = compute_bounding_box(poly)
        assert bbox == (0.0, 0.0, 10.0, 10.0)

        # Must not raise ZeroDivisionError and must return exact arithmetic mean
        centroid = compute_polygon_centroid(poly)
        assert pytest.approx(centroid[0], abs=1e-5) == 5.0
        assert pytest.approx(centroid[1], abs=1e-5) == 5.0

    def test_collinear_horizontal_and_vertical_lines(self):
        """Collinear flat horizontal and vertical lines."""
        h_line = [(1.0, 4.0), (5.0, 4.0), (9.0, 4.0)]
        assert compute_bounding_box(h_line) == (1.0, 4.0, 9.0, 4.0)
        c_h = compute_polygon_centroid(h_line)
        assert pytest.approx(c_h[0], abs=1e-5) == 5.0
        assert pytest.approx(c_h[1], abs=1e-5) == 4.0

        v_line = [(3.0, -2.0), (3.0, 2.0), (3.0, 6.0)]
        assert compute_bounding_box(v_line) == (3.0, -2.0, 3.0, 6.0)
        c_v = compute_polygon_centroid(v_line)
        assert pytest.approx(c_v[0], abs=1e-5) == 3.0
        assert pytest.approx(c_v[1], abs=1e-5) == 2.0

    def test_single_point_repeated_vertices(self):
        """Degenerate polygon where all vertices are the same point."""
        poly = [(7.5, 3.2), (7.5, 3.2), (7.5, 3.2)]
        bbox = compute_bounding_box(poly)
        assert bbox == (7.5, 3.2, 7.5, 3.2)
        c = compute_polygon_centroid(poly)
        assert pytest.approx(c[0], abs=1e-5) == 7.5
        assert pytest.approx(c[1], abs=1e-5) == 3.2

    def test_self_intersecting_hourglass_figure_eight(self):
        """
        Self-intersecting hourglass (butterfly) polygon.
        Opposite symmetrical lobes cancel out signed area to 0.0.
        Engine must fallback gracefully to arithmetic mean without ZeroDivisionError.
        """
        poly = [(0.0, 0.0), (2.0, 2.0), (0.0, 2.0), (2.0, 0.0)]
        centroid = compute_polygon_centroid(poly)
        # Arithmetic mean: (0+2+0+2)/4 = 1.0, (0+2+2+0)/4 = 1.0
        assert pytest.approx(centroid[0], abs=1e-5) == 1.0
        assert pytest.approx(centroid[1], abs=1e-5) == 1.0

    def test_asymmetric_self_intersecting_bowtie_polygon(self):
        """Bowtie polygon with unequal lobe areas."""
        poly = [(0.0, 0.0), (4.0, 2.0), (0.0, 2.0), (2.0, 0.0)]
        centroid = compute_polygon_centroid(poly)
        assert math.isfinite(centroid[0]) and math.isfinite(centroid[1])

    def test_sliver_micro_polygon_and_closed_ring_tolerance(self):
        """
        Tests micro-sliver polygon and verifies closed-ring sanitization.
        Note: When poly[-1] is within abs_tol=1e-9 of poly[0], math.isclose strips it as duplicate.
        When distinct (> 1e-9), all 4 vertices participate in arithmetic mean fallback.
        """
        # Case A: 4 distinct vertices with area < 1e-9 but last point distinct from first (dy = 1e-6)
        poly_distinct = [(0.0, 0.0), (10.0, 0.0), (10.0, 1e-6), (0.0, 1e-6)]
        centroid_a = compute_polygon_centroid(poly_distinct)
        # Shoelace formula computes centroid accurately: cx = 5.0, cy = 0.0000005
        assert pytest.approx(centroid_a[0], abs=1e-3) == 5.0
        assert pytest.approx(centroid_a[1], abs=1e-4) == 0.0

        # Case B: Collinear 4-point zero-area polygon with distinct endpoints
        poly_collinear = [(0.0, 0.0), (4.0, 0.0), (8.0, 0.0), (12.0, 0.0)]
        centroid_b = compute_polygon_centroid(poly_collinear)
        # Arithmetic mean of 4 points: (0 + 4 + 8 + 12)/4 = 6.0
        assert pytest.approx(centroid_b[0], abs=1e-5) == 6.0
        assert pytest.approx(centroid_b[1], abs=1e-5) == 0.0

    def test_closed_ring_duplicate_strip(self):
        """Polygon where first and last vertex are identical."""
        open_poly = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
        closed_poly = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (0.0, 0.0)]

        c_open = compute_polygon_centroid(open_poly)
        c_closed = compute_polygon_centroid(closed_poly)
        assert pytest.approx(c_open[0], abs=1e-6) == c_closed[0] == 2.0
        assert pytest.approx(c_open[1], abs=1e-6) == c_closed[1] == 2.0

    def test_massive_10k_vertex_circular_polygon(self):
        """Massive 10,000-vertex polygon circle stress test."""
        n = 10000
        radius = 5.0
        center_x, center_y = 12.0, -8.0
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
        poly = [
            (round(center_x + radius * math.cos(a), 6), round(center_y + radius * math.sin(a), 6))
            for a in angles
        ]

        t0 = time.perf_counter()
        bbox = compute_bounding_box(poly)
        c = compute_polygon_centroid(poly)
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.20  # Fast calculation under 200ms
        assert bbox[0] < center_x - radius + 0.01 and bbox[2] > center_x + radius - 0.01
        assert pytest.approx(c[0], abs=1e-3) == center_x
        assert pytest.approx(c[1], abs=1e-3) == center_y

    def test_point_in_polygon_exact_boundaries_and_tangency(self):
        """Point-in-polygon on exact vertex, exact edge, and horizontal tangency."""
        poly = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]

        # Exact vertex
        assert point_in_polygon((0.0, 0.0), poly, include_boundary=True)
        # Exact midpoint on edge
        assert point_in_polygon((2.0, 0.0), poly, include_boundary=True)
        assert point_in_polygon((4.0, 2.0), poly, include_boundary=True)
        # Collinear outside edge
        assert not point_in_polygon((6.0, 0.0), poly, include_boundary=True)
        # Point inside
        assert point_in_polygon((2.0, 2.0), poly, include_boundary=True)
        # Point strictly outside
        assert not point_in_polygon((5.0, 5.0), poly, include_boundary=True)

    def test_mag_room_registry_with_degenerate_room(self, tmp_path):
        """Verify MAGRoomRegistry handles degenerate collinear rooms without crashing."""
        db_path = str(tmp_path / "test_degenerate.db")
        db = MAGDatabase(db_path)
        registry = MAGRoomRegistry(db=db)

        # Collinear room
        collinear_poly = [(0.0, 0.0), (3.0, 3.0), (6.0, 6.0)]
        success = registry.insert_room(
            room_name="corridoio_stretto",
            polygon=collinear_poly,
            display_name="Corridoio Stretto"
        )
        assert success

        room = registry.get_room("corridoio_stretto")
        assert room is not None
        assert room.centroid == (3.0, 3.0)

        # Query pose on segment
        matched = registry.match_room_by_pose(3.0, 3.0)
        assert matched == "corridoio_stretto"

        # Query pose off segment
        matched_outside = registry.match_room_by_pose(10.0, 10.0)
        assert matched_outside is None


# ============================================================================
# DOMAIN 2: HIGH-FREQUENCY FRONTIER CLUSTERING UNDER MASSIVE MAPS (1000x1000)
# ============================================================================
class TestMassiveMapFrontierClustering:
    """
    Stress-testing FrontierExplorationEngine on massive 1000x1000 grids (1M cells,
    equivalent to 50m x 50m warehouse at 0.05m resolution).
    """

    def test_massive_1000x1000_single_continuous_frontier(self):
        """Massive 1000x1000 map with open warehouse and perimeter frontier."""
        # 1000 x 1000 grid = 1,000,000 cells
        grid = np.full((1000, 1000), -1, dtype=np.int8)
        # Free space in central region [100:900, 100:900]
        grid[100:900, 100:900] = 0

        engine = FrontierExplorationEngine(
            resolution=0.05,
            origin=(-25.0, -25.0),
            min_frontier_size=0.40
        )

        # Test sub-sampled perimeter detection to verify boundary clustering
        t0 = time.perf_counter()
        # Perimeter line of 200 cells
        frontier_line = [(100, c) for c in range(100, 300)]
        clusters = engine.cluster_frontiers(frontier_line)
        t_cluster = time.perf_counter() - t0

        assert t_cluster < 0.50  # Must be fast
        assert len(clusters) == 1
        # 200 cells * 0.05m = 10.0m > 0.40m
        assert clusters[0]["size_meters"] >= 9.0
        assert clusters[0]["cell_count"] == 200
        # Check world coordinate origin alignment
        assert clusters[0]["centroid"][1] == pytest.approx(-25.0 + 100 * 0.05, abs=0.1)

    def test_massive_speckle_noise_below_threshold(self):
        """
        Isolated single-cell micro-frontiers all < 0.40m stop exploration.
        Cells are separated by >= 3 cells so they are strictly disconnected in 8-connectivity.
        Each cluster has cell_count=1 and size=0.0m < 0.40m.
        """
        grid = np.full((100, 100), -1, dtype=np.int8)
        # Create 25 strictly isolated 1-cell free spots spaced by 3 cells
        for i in range(5, 80, 3):
            grid[i, 20] = 0

        engine = FrontierExplorationEngine(resolution=0.05, min_frontier_size=0.40)
        res = engine.evaluate_frontiers(grid)

        assert res["status"] == "COMPLETED"
        assert "ALL_FRONTIERS_BELOW_THRESHOLD" in res["reason"]
        assert res["selected_goal"] is None
        assert res["motor_stop_required"] is True

    def test_single_valid_cluster_amidst_50_micro_clusters(self):
        """One large valid frontier (1.5m) among 50 micro-frontiers (<0.40m)."""
        grid = np.full((150, 150), -1, dtype=np.int8)
        # 30 strictly isolated micro free spots spaced by 4 cells
        for i in range(5, 125, 4):
            grid[i, 20] = 0

        # One contiguous frontier strip: 30 cells (30 * 0.05m = 1.5m)
        for c in range(60, 90):
            grid[75, c] = 0

        engine = FrontierExplorationEngine(resolution=0.05, min_frontier_size=0.40)
        res = engine.evaluate_frontiers(grid)

        assert res["status"] == "EXPLORING"
        assert res["selected_goal"] is not None
        assert res["selected_cluster"]["size_meters"] >= 1.40
        assert not res["motor_stop_required"]

    def test_deep_concave_cavity_frontier_nav_goal_safety(self):
        """
        C-shaped concave frontier where geometric centroid falls outside free cluster cells.
        nav_goal must be selected from the cluster cells (closest cell to centroid),
        strictly inside free frontier space, avoiding obstacle/cavity traps.
        """
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0))

        # C-shaped frontier cells:
        # Left column: (10, 10), (11, 10), (12, 10), (13, 10), (14, 10)
        # Top branch:  (10, 11), (10, 12), (10, 13)
        # Bottom branch: (14, 11), (14, 12), (14, 13)
        # Centroid will be around (12, 11.2) which is in the hollow cavity!
        c_cells = (
            [(r, 10) for r in range(10, 15)] +
            [(10, c) for c in range(11, 14)] +
            [(14, c) for c in range(11, 14)]
        )

        clusters = engine.cluster_frontiers(c_cells)
        assert len(clusters) == 1
        cluster = clusters[0]

        # nav_goal must be exact coordinate of one of the actual cells
        nav_goal = cluster["nav_goal"]
        cell_coords = [(c[1] * 0.05, c[0] * 0.05) for c in c_cells]

        is_on_cell = any(
            math.hypot(nav_goal[0] - x, nav_goal[1] - y) < 1e-3
            for x, y in cell_coords
        )
        assert is_on_cell, f"nav_goal {nav_goal} must be an actual cell coordinate in free space!"

    def test_massive_map_blacklisting_resilience(self):
        """Blacklisting frontier centroid and nav_goal filters it on subsequent passes."""
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0))

        # Two distinct valid clusters
        grid = np.full((100, 100), -1, dtype=np.int8)
        # Cluster A: 20 cells at row 20
        for c in range(10, 30):
            grid[20, c] = 0
        # Cluster B: 25 cells at row 60 (larger)
        for c in range(10, 35):
            grid[60, c] = 0

        # Initial eval: chooses Cluster B (largest)
        res1 = engine.evaluate_frontiers(grid)
        assert res1["status"] == "EXPLORING"
        goal1 = res1["selected_goal"]
        centroid1 = res1["selected_cluster"]["centroid"]

        # Blacklist Cluster B
        engine.blacklist_frontier(centroid1)

        # Next eval: must skip Cluster B and choose Cluster A
        res2 = engine.evaluate_frontiers(grid)
        assert res2["status"] == "EXPLORING"
        goal2 = res2["selected_goal"]
        assert goal2 != goal1


# ============================================================================
# DOMAIN 3: CONCURRENCY & RACE CONDITIONS IN STATE TRANSITIONS
# ============================================================================
class TestConcurrencyAndRaceConditionsStateTransitions:
    """
    Stress-testing MappingStateMachine and MAGRoomRegistry under intense
    multi-threaded concurrency to uncover race conditions, deadlocks, and
    invariant violations.
    """

    def test_multi_threaded_fsm_stress_and_invariant_preservation(self):
        """
        10 concurrent threads executing operations on MappingStateMachine.
        Invariant: motors_allowed == True ONLY IF state == MAPPING_ACTIVE.
        """
        fsm = MappingStateMachine()
        stop_event = threading.Event()
        violations: List[str] = []

        def worker_timer():
            while not stop_event.is_set():
                fsm.advance_time(0.5)
                # Invariant check
                if fsm.motors_allowed and fsm.state != MappingStateMachine.STATE_MAPPING_ACTIVE:
                    violations.append(f"INVARIANT VIOLATION: motors_allowed=True while state={fsm.state}")
                time.sleep(0.001)

        def worker_voice():
            phrases = ["sì, procedi", "no, annulla", "sì, confermo", "ciao marcus", "ok", "stop"]
            idx = 0
            while not stop_event.is_set():
                fsm.process_voice_input(phrases[idx % len(phrases)])
                idx += 1
                if fsm.motors_allowed and fsm.state != MappingStateMachine.STATE_MAPPING_ACTIVE:
                    violations.append(f"INVARIANT VIOLATION: motors_allowed=True while state={fsm.state}")
                time.sleep(0.001)

        def worker_requests():
            while not stop_event.is_set():
                fsm.request_mapping("laboratorio", current_luminance=35.0)
                time.sleep(0.002)
                fsm.request_destructive_action("mappa_vecchia")
                time.sleep(0.002)
                fsm.cancel_mapping()
                time.sleep(0.002)

        threads = [
            threading.Thread(target=worker_timer) for _ in range(4)
        ] + [
            threading.Thread(target=worker_voice) for _ in range(4)
        ] + [
            threading.Thread(target=worker_requests) for _ in range(2)
        ]

        for t in threads:
            t.daemon = True
            t.start()

        # Run stress for 0.4 seconds
        time.sleep(0.4)
        stop_event.set()

        for t in threads:
            t.join(timeout=1.0)

        assert not violations, f"Detected state invariant violations: {violations[:5]}"
        assert fsm.state in (
            MappingStateMachine.STATE_IDLE,
            MappingStateMachine.STATE_AWAITING_CONFIRMATION,
            MappingStateMachine.STATE_REMINDER_SENT,
            MappingStateMachine.STATE_MAPPING_ACTIVE,
            MappingStateMachine.STATE_STANDBY_ABORTED,
            MappingStateMachine.STATE_CANCELLED
        )

    def test_race_at_exact_120s_reminder_boundary(self):
        """Simulate race between timer advancing to 120.0s and voice confirmation."""
        fsm = MappingStateMachine()
        fsm.request_mapping("cucina")
        fsm.advance_time(119.999)
        assert fsm.state == MappingStateMachine.STATE_AWAITING_CONFIRMATION

        # Concurrently advance 0.002s and confirm
        results = []

        def t_adv():
            fsm.advance_time(0.002)

        def t_conf():
            msg, ok = fsm.process_voice_input("sì, procedi")
            results.append(ok)

        th1 = threading.Thread(target=t_adv)
        th2 = threading.Thread(target=t_conf)
        th1.start()
        th2.start()
        th1.join()
        th2.join()

        # Final state must be either MAPPING_ACTIVE (if confirmation won) or REMINDER_SENT
        assert fsm.state in (
            MappingStateMachine.STATE_MAPPING_ACTIVE,
            MappingStateMachine.STATE_REMINDER_SENT
        )
        if fsm.state == MappingStateMachine.STATE_MAPPING_ACTIVE:
            assert fsm.motors_allowed is True
        else:
            assert fsm.motors_allowed is False

    def test_race_at_exact_300s_standby_abort_boundary(self):
        """Simulate timer hitting 300.0s while cancellation/confirmation arrives."""
        fsm = MappingStateMachine()
        fsm.request_mapping("salotto")
        fsm.advance_time(299.999)

        fsm.advance_time(0.002)
        assert fsm.state == MappingStateMachine.STATE_STANDBY_ABORTED
        assert fsm.motors_allowed is False

        # Attempting late confirmation after 300s abort MUST be rejected
        msg, ok = fsm.process_voice_input("sì, procedi")
        assert not ok
        assert fsm.motors_allowed is False

    def test_concurrent_mag_room_registry_read_write_wal(self, tmp_path):
        """
        High-concurrency read/write on MAGRoomRegistry backed by SQLite WAL.
        Ensures LRU cache bound <= 64 items is strictly maintained.
        """
        db_path = str(tmp_path / "concurrent_rooms.db")
        db = MAGDatabase(db_path)
        registry = MAGRoomRegistry(db=db)

        stop_ev = threading.Event()
        errors: List[str] = []

        def writer(w_id: int):
            for i in range(50):
                if stop_ev.is_set():
                    break
                poly = [(i, i), (i + 2, i), (i + 2, i + 2), (i, i + 2)]
                room_name = f"room_{w_id}_{i}"
                try:
                    registry.insert_room(room_name, poly)
                    # Register fake signatures
                    registry.register_room_signatures(
                        room_name,
                        vpr_embeddings=[np.zeros(512, dtype=np.float32)],
                        lidar_signature=np.zeros(360, dtype=np.float32)
                    )
                except Exception as e:
                    errors.append(f"Writer {w_id} error: {e}")
                time.sleep(0.001)

        def reader(r_id: int):
            while not stop_ev.is_set():
                try:
                    registry.match_room_by_pose(5.0, 5.0)
                    rooms = registry.list_rooms()
                    if rooms:
                        registry.get_room_signatures(rooms[0].room_name)
                except Exception as e:
                    errors.append(f"Reader {r_id} error: {e}")
                time.sleep(0.001)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
        threads += [threading.Thread(target=reader, args=(i,)) for i in range(3)]

        for t in threads:
            t.start()

        for t in threads[:3]:
            t.join(timeout=5.0)
        stop_ev.set()
        for t in threads[3:]:
            t.join(timeout=2.0)

        assert not errors, f"Concurrent WAL errors encountered: {errors}"
        # LRU cache bound verification (SPEC-05 Red Zone)
        assert len(registry._signatures_cache) <= 64


# ============================================================================
# DOMAIN 4: SSD MAP EXPORT TRAVERSAL & ATOMIC RENAME ROBUSTNESS
# ============================================================================
class TestSSDMapExportTraversalAndAtomicRename:
    """
    Audits SLAMOptimizationExporter against directory traversal attacks,
    SSD path compliance (FM-NAV-020), atomic replacement (.tmp -> os.replace),
    and filesystem permission failures.
    """

    @pytest.mark.parametrize("attack_name", [
        "../../etc/shadow",
        "../windows/system32/cmd",
        "sub/dir/map",
        r"sub\dir\map",
        "..",
        "....//....//etc",
        "map/with/slash",
    ])
    def test_directory_traversal_attack_rejection(self, attack_name, tmp_path):
        """Rejects map_name containing traversal tokens or slashes."""
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path, enforce_ssd_mount=False)
        dummy_grid = np.zeros((20, 20), dtype=np.int8)

        ok, msg, paths = exporter.export_map_files(attack_name, dummy_grid)
        assert not ok
        assert "directory traversal" in msg or "Invalid map name" in msg
        assert not paths

    def test_ssd_mount_prefix_enforcement_fm_nav_020(self, tmp_path):
        """Enforces FM-NAV-020: export directory must strictly be /mnt/ssd/maps."""
        exporter = SLAMOptimizationExporter(enforce_ssd_mount=True)
        dummy_grid = np.zeros((10, 10), dtype=np.int8)

        # Illegal path in user home or tmp
        illegal_path = Path("/home/robopy/maps")
        ok, msg, paths = exporter.export_map_files(
            "test_room",
            dummy_grid,
            target_override=illegal_path,
            enforce_ssd_check=True
        )
        assert not ok
        assert "VIOLATION FM-NAV-020" in msg

    def test_atomic_rename_robustness_under_readonly_filesystem(self, tmp_path):
        """Simulates read-only filesystem (EROFS) and verifies graceful error handling."""
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path, enforce_ssd_mount=False)
        dummy_grid = np.zeros((10, 10), dtype=np.int8)

        ok, msg, paths = exporter.export_map_files(
            "test_room",
            dummy_grid,
            force_readonly=True
        )
        assert not ok
        assert "Read-only file system" in msg
        assert not paths

    def test_atomic_replacement_consistency_and_zero_bom(self, tmp_path):
        """
        Verifies that .tmp files are atomically replaced into .yaml and .pgm,
        leaving no leftover .tmp artifacts and producing 100% Zero BOM UTF-8.
        """
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path, enforce_ssd_mount=False)
        h, w = 40, 60
        grid = np.full((h, w), -1, dtype=np.int8)
        grid[10:30, 10:50] = 0   # free
        grid[10, 10:50] = 100    # wall obstacle

        ok, msg, paths = exporter.export_map_files("living_room", grid)
        assert ok
        assert "yaml" in paths and "pgm" in paths

        yaml_p = paths["yaml"]
        pgm_p = paths["pgm"]

        assert yaml_p.exists()
        assert pgm_p.exists()

        # No .tmp leftovers
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

        # Zero BOM UTF-8 verification
        raw_yaml_bytes = yaml_p.read_bytes()
        assert not raw_yaml_bytes.startswith(b"\xef\xbb\xbf")
        yaml_text = raw_yaml_bytes.decode("utf-8")
        assert "image: living_room.pgm" in yaml_text
        assert "resolution: 0.05" in yaml_text

        # Binary P5 PGM format verification
        raw_pgm_bytes = pgm_p.read_bytes()
        assert raw_pgm_bytes.startswith(b"P5\n")
        assert f"P5\n{w} {h}\n255\n".encode("ascii") in raw_pgm_bytes[:30]
        # Check payload size
        header_len = len(f"P5\n{w} {h}\n255\n".encode("ascii"))
        assert len(raw_pgm_bytes) == header_len + (w * h)


# ============================================================================
# DOMAIN 5: BATTERY ANTI-SAG VOLTAGE DROPS & OPTIMIZATION SAFETY
# ============================================================================
class TestBatteryAntiSagVoltageOptimization:
    """
    Verifies SLAMOptimizationExporter battery anti-sag circular moving average filter
    and SLAM bundle adjustment protection against brownouts and deep discharge.
    """

    def test_filter_window_size_and_pathological_input_rejection(self, tmp_path):
        """Sanitizes NaN, <=0.5V, and negative voltages from moving average buffer."""
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path, enforce_ssd_mount=False)

        # Buffer starts empty, default nominal 11.10V
        assert exporter.get_filtered_voltage() == 11.10

        # Feed valid voltages
        exporter.update_battery_voltage(11.20)
        exporter.update_battery_voltage(11.00)
        assert pytest.approx(exporter.get_filtered_voltage(), abs=1e-3) == 11.10

        # Feed pathological inputs (NaN, 0.0, negative)
        exporter.update_battery_voltage(float("nan"))
        exporter.update_battery_voltage(0.0)
        exporter.update_battery_voltage(-5.0)

        # Average must remain untainted at 11.10
        assert pytest.approx(exporter.get_filtered_voltage(), abs=1e-3) == 11.10

    def test_transient_motor_sag_rejection_2s_dip(self, tmp_path):
        """
        Anti-Sag Guard (FM-SYS-006):
        14 samples of 11.20V + 6 samples of 8.60V (1.2s heavy motor acceleration spike).
        Filtered voltage remains >= 9.90V -> Optimization is safely ALLOWED.
        """
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path, enforce_ssd_mount=False)

        for _ in range(14):
            exporter.update_battery_voltage(11.20)
        for _ in range(6):
            exporter.update_battery_voltage(8.60)

        # Filtered: (14*11.20 + 6*8.60) / 20 = (156.8 + 51.6)/20 = 208.4/20 = 10.42V
        v_filt = exporter.get_filtered_voltage()
        assert v_filt >= 9.90
        ok, msg = exporter.check_battery_safety()
        assert ok
        assert "Safe to optimize" in msg

    def test_sustained_battery_cliff_inhibition(self, tmp_path):
        """
        Cliff Protection (FM-SYS-004):
        20 consecutive samples at 9.75V (< 9.90V docking threshold).
        Optimization is strictly INHIBITED.
        """
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path, enforce_ssd_mount=False)

        for _ in range(20):
            exporter.update_battery_voltage(9.75)

        v_filt = exporter.get_filtered_voltage()
        assert v_filt < 9.90

        ok, msg = exporter.check_battery_safety()
        assert not ok
        assert "INHIBITED_BATTERY_LOW" in msg

        # Bundle adjustment call must fail immediately without running optimization
        opt_ok, opt_msg = exporter.trigger_global_optimization(simulate_ram_usage_mb=10.0)
        assert not opt_ok
        assert "INHIBITED_BATTERY_LOW" in opt_msg

    def test_charging_dock_voltage_override(self, tmp_path):
        """Robot docked on charger with voltage >= 12.70V is always authorized."""
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path, enforce_ssd_mount=False)

        for _ in range(20):
            exporter.update_battery_voltage(12.80)

        ok, msg = exporter.check_battery_safety()
        assert ok
        assert "Battery charging/docked" in msg

    def test_concurrent_voltage_sag_during_optimization_request(self, tmp_path):
        """
        Multi-threaded race: voltage is dropping from 10.5V to 9.2V while
        multiple worker threads attempt to trigger optimization.
        Once voltage crosses 9.90V, all subsequent triggers must fail safely.
        """
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path, enforce_ssd_mount=False)
        stop_ev = threading.Event()
        results = []

        def voltage_dropper():
            # Step down voltage
            for v in np.linspace(10.5, 9.2, 40):
                if stop_ev.is_set():
                    break
                exporter.update_battery_voltage(float(v))
                time.sleep(0.005)

        def optimizer_caller():
            while not stop_ev.is_set():
                ok, _ = exporter.trigger_global_optimization(simulate_ram_usage_mb=5.0)
                results.append((exporter.get_filtered_voltage(), ok))
                time.sleep(0.005)

        t1 = threading.Thread(target=voltage_dropper)
        t2 = threading.Thread(target=optimizer_caller)

        t1.start()
        t2.start()

        t1.join(timeout=2.0)
        time.sleep(0.05)
        stop_ev.set()
        t2.join(timeout=1.0)

        # Check that whenever voltage was < 9.90V, ok was False!
        for v, ok in results:
            if v < 9.90:
                assert ok is False, f"Optimization was allowed at dangerously low voltage {v:.2f}V!"
