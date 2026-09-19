"""
Adversarial Stress Test Suite for Milestone 1:
1. Adversarial Spatial Geometry Stress:
   - Deep U-shapes (concave cavity, narrow aspect ratio, theoretical centroid)
   - Jagged star polygons (12-point non-convex star, sharp tips, deep notches)
   - Collinear multi-point edges (collinear vertices along horizontal & vertical edges)
   - Near-zero area slivers (elongated micro-rectangles, sub-1e-9 degenerate triangles)
   - Floating-point precision at extreme coordinates (1e-8 distance from edges/vertices, tol sensitivity)
   - Benchmark point-in-polygon throughput across 50,000 queries
2. Adversarial Database Concurrency Stress:
   - 20 concurrent worker threads (10 readers, 10 writers) on MAGRoomRegistry and MAGDatabase
   - Independent SQLite connections per thread to stress cross-connection WAL concurrency
   - Mixed operations: pose matching, signature read/write (512D VPR, 360-bin LiDAR),
     room insertion/deletion, TRINITY episodic and fact writes, user preferences
   - Assert zero database lock exceptions (PRAGMA busy_timeout=5000ms validation)
   - Assert 100% data consistency, PRAGMA integrity_check, and LRU cache constraint (<= 64 items)
"""

import os
import sys
import time
import math
import random
import tempfile
import threading
import sqlite3
from typing import List, Tuple, Dict, Any

import pytest
import numpy as np

# Ensure robopy_controller is on path
current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
robopy_path = os.path.join(workspace_root, "robopy_controller")
if robopy_path not in sys.path:
    sys.path.insert(0, robopy_path)

from robot_ai.trinity.mag_database import MAGDatabase
from robot_ai.trinity.mag_room_geometry import (
    compute_bounding_box,
    compute_polygon_centroid,
    point_on_segment,
    point_in_polygon
)
from robot_ai.trinity.mag_room_registry import MAGRoomRegistry, RoomMetadata


@pytest.fixture
def temp_db(tmp_path):
    """Temporary SQLite database path for testing."""
    db_file = tmp_path / "adv_test_mag.db"
    return str(db_file)


@pytest.fixture
def temp_yaml(tmp_path):
    """Temporary YAML metadata file path."""
    yaml_file = tmp_path / "adv_rooms_metadata.yaml"
    return str(yaml_file)


# ==============================================================================
# SECTION 1: Adversarial Spatial Geometry Stress Tests
# ==============================================================================
class TestAdversarialSpatialGeometry:
    """Stress tests covering complex, non-convex, collinear, and degenerate geometries."""

    def test_deep_u_shape_geometry(self, temp_db):
        """
        Deep U-shape geometry test.
        Coordinates:
            (0,0) -> (10,0) -> (10,10) -> (8,10) -> (8,2) -> (2,2) -> (2,10) -> (0,10)
        Theoretical centroid:
            Bottom corridor: [0, 10] x [0, 2] -> Area = 20, centroid = (5, 1)
            Left arm:        [0, 2] x [2, 10] -> Area = 16, centroid = (1, 6)
            Right arm:       [8, 10] x [2, 10]-> Area = 16, centroid = (9, 6)
            Total Area = 52
            Cx = (20*5 + 16*1 + 16*9) / 52 = 260 / 52 = 5.000000
            Cy = (20*1 + 16*6 + 16*6) / 52 = 212 / 52 = 4.076923
        """
        registry = MAGRoomRegistry(db_path=temp_db)
        u_poly = [
            [0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [8.0, 10.0],
            [8.0, 2.0], [2.0, 2.0], [2.0, 10.0], [0.0, 10.0]
        ]
        registry.insert_room("u_room", u_poly, map_name="stress_map")
        
        # 1. Verify theoretical centroid calculation
        centroid = compute_polygon_centroid(u_poly)
        assert pytest.approx(centroid[0], abs=1e-5) == 5.0
        assert pytest.approx(centroid[1], abs=1e-5) == 212.0 / 52.0

        # 2. Interior points: Left arm, Right arm, Bottom corridor
        assert registry.match_room_by_pose(1.0, 5.0) == "u_room"
        assert registry.match_room_by_pose(1.0, 9.5) == "u_room"
        assert registry.match_room_by_pose(9.0, 5.0) == "u_room"
        assert registry.match_room_by_pose(9.0, 9.5) == "u_room"
        assert registry.match_room_by_pose(5.0, 1.0) == "u_room"

        # 3. Exterior cavity (deep notch between arms)
        assert registry.match_room_by_pose(5.0, 3.0) is None
        assert registry.match_room_by_pose(5.0, 6.0) is None
        assert registry.match_room_by_pose(5.0, 9.9) is None
        assert registry.match_room_by_pose(2.1, 5.0) is None  # Just inside cavity
        assert registry.match_room_by_pose(7.9, 5.0) is None  # Just inside cavity

        # 4. Outside bounding box
        assert registry.match_room_by_pose(-0.5, 5.0) is None
        assert registry.match_room_by_pose(10.5, 5.0) is None
        assert registry.match_room_by_pose(5.0, -0.5) is None
        assert registry.match_room_by_pose(5.0, 10.5) is None

        # 5. Boundary points
        assert registry.match_room_by_pose(2.0, 5.0) == "u_room"  # Inner wall left arm
        assert registry.match_room_by_pose(8.0, 5.0) == "u_room"  # Inner wall right arm
        assert registry.match_room_by_pose(5.0, 2.0) == "u_room"  # Bottom of cavity

    def test_jagged_star_polygon(self, temp_db):
        """
        12-point jagged star non-convex polygon.
        Alternating outer radius R=10.0 and inner radius r=2.0.
        Tests sharp outward tips and deep inward acute cusps.
        Centroid is analytically (0.0, 0.0) due to 6-fold radial symmetry.
        """
        num_points = 12
        r_outer = 10.0
        r_inner = 2.0
        star_poly = []
        for i in range(num_points):
            angle = i * (2.0 * math.pi / num_points)
            r = r_outer if (i % 2 == 0) else r_inner
            star_poly.append([r * math.cos(angle), r * math.sin(angle)])

        # 1. Centroid must be (0.0, 0.0)
        centroid = compute_polygon_centroid(star_poly)
        assert pytest.approx(centroid[0], abs=1e-5) == 0.0
        assert pytest.approx(centroid[1], abs=1e-5) == 0.0

        # 2. Test center point
        assert point_in_polygon((0.0, 0.0), star_poly) is True

        # 3. Test tips and notches
        for i in range(num_points):
            angle = i * (2.0 * math.pi / num_points)
            if i % 2 == 0:
                # Outer tip at r=10.0
                # Point inside tip (r=9.5)
                pt_in = (9.5 * math.cos(angle), 9.5 * math.sin(angle))
                assert point_in_polygon(pt_in, star_poly) is True, f"Failed at tip {i} inside"
                # Point outside tip (r=10.5)
                pt_out = (10.5 * math.cos(angle), 10.5 * math.sin(angle))
                assert point_in_polygon(pt_out, star_poly) is False, f"Failed at tip {i} outside"
            else:
                # Inward notch at r=2.0
                # Point inside near notch (r=1.5)
                pt_in = (1.5 * math.cos(angle), 1.5 * math.sin(angle))
                assert point_in_polygon(pt_in, star_poly) is True, f"Failed at notch {i} inside"
                # Point in exterior pocket between star arms (r=4.0 along notch angle)
                pt_out = (4.0 * math.cos(angle), 4.0 * math.sin(angle))
                assert point_in_polygon(pt_out, star_poly) is False, f"Failed at notch {i} exterior pocket"

    def test_collinear_multipoint_edges(self, temp_db):
        """
        Tests polygons where edges contain multiple collinear vertices.
        Standard ray-casting can fail if collinear vertices trigger double-crossing
        or false intersections. Franklin PNPoly half-open interval must handle this.
        """
        # Rectangle [0,0] to [10, 6] with 5 collinear points on bottom and 4 on left
        collinear_poly = [
            [0.0, 0.0],
            [2.0, 0.0],
            [4.0, 0.0],
            [6.0, 0.0],
            [8.0, 0.0],
            [10.0, 0.0],  # Bottom edge: 6 vertices
            [10.0, 6.0],  # Right edge
            [0.0, 6.0],   # Top edge
            [0.0, 4.0],
            [0.0, 2.0]    # Left edge: 3 vertices
        ]
        
        # 1. Centroid must remain exactly (5.0, 3.0) despite redundant vertices
        c = compute_polygon_centroid(collinear_poly)
        assert pytest.approx(c[0], abs=1e-5) == 5.0
        assert pytest.approx(c[1], abs=1e-5) == 3.0

        # 2. Interior points
        assert point_in_polygon((5.0, 3.0), collinear_poly) is True
        assert point_in_polygon((1.0, 1.0), collinear_poly) is True
        assert point_in_polygon((9.0, 5.0), collinear_poly) is True

        # 3. Collinear vertices themselves
        assert point_in_polygon((4.0, 0.0), collinear_poly, include_boundary=True) is True
        assert point_in_polygon((0.0, 2.0), collinear_poly, include_boundary=True) is True

        # 4. Collinear segment midpoints
        assert point_in_polygon((3.0, 0.0), collinear_poly, include_boundary=True) is True
        assert point_in_polygon((0.0, 3.0), collinear_poly, include_boundary=True) is True

        # 5. Points slightly outside collinear edges
        assert point_in_polygon((4.0, -0.2), collinear_poly) is False
        assert point_in_polygon((-0.2, 2.0), collinear_poly) is False

        # 6. Ray cast along the exact horizontal line of collinear vertices (y = 0.0)
        # Testing horizontal ray interaction with collinear horizontal segments
        assert point_in_polygon((12.0, 0.0), collinear_poly) is False
        assert point_in_polygon((-2.0, 0.0), collinear_poly) is False

    def test_near_zero_area_slivers(self, temp_db):
        """
        Near-zero area sliver tests (degenerate and micro-polygons).
        Verifies numerical stability, Shoelace formula zero-division protection,
        and bounding box behavior.
        """
        # 1. Elongated micro-sliver: length 10.0m, width 1e-6m (1 micrometer)
        # Area = 1e-5 m^2
        micro_sliver = [
            [0.0, 0.0],
            [10.0, 0.0],
            [10.0, 1e-6],
            [0.0, 1e-6]
        ]
        c = compute_polygon_centroid(micro_sliver)
        assert pytest.approx(c[0], abs=1e-5) == 5.0
        assert pytest.approx(c[1], abs=1e-9) == 5e-7

        # Point inside micro-sliver
        assert point_in_polygon((5.0, 5e-7), micro_sliver) is True
        # Point outside micro-sliver (width is 1e-6, query at y = 5e-6)
        assert point_in_polygon((5.0, 5e-6), micro_sliver) is False

        # 2. Extreme degenerate sliver with area < 1e-9 m^2
        # Triangle base = 10.0, height = 1e-10 -> Area = 5e-10 < 1e-9
        # In compute_polygon_centroid: triggers `abs(signed_area) < 1e-9` fallback to arithmetic mean.
        degenerate_triangle = [
            [0.0, 0.0],
            [10.0, 0.0],
            [5.0, 1e-10]
        ]
        # Must execute cleanly without ZeroDivisionError or crash
        c_deg = compute_polygon_centroid(degenerate_triangle)
        expected_x = (0.0 + 10.0 + 5.0) / 3.0  # 5.0
        expected_y = (0.0 + 0.0 + 1e-10) / 3.0
        assert pytest.approx(c_deg[0], abs=1e-6) == expected_x
        assert pytest.approx(c_deg[1], abs=1e-12) == expected_y

    def test_extreme_floating_point_precision(self):
        """
        Adversarial floating-point test at extreme precision (1e-8 distance from boundary).
        Evaluates boundary classification and tolerance sensitivity.
        """
        poly = [[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0]]
        
        # Test 1: With default tolerance tol = 1e-7
        # Points at distance 1e-8 are smaller than tol (1e-8 < 1e-7),
        # so point_on_segment recognizes them within the boundary tolerance.
        # Inside by 1e-8:
        pt_inside_eps = (2.5, 1e-8)
        assert point_in_polygon(pt_inside_eps, poly, include_boundary=True, tol=1e-7) is True

        # Outside by 1e-8:
        pt_outside_eps = (2.5, -1e-8)
        # If include_boundary=True, distance 1e-8 is within tolerance 1e-7 of the segment [0,0]-[5,0]
        assert point_in_polygon(pt_outside_eps, poly, include_boundary=True, tol=1e-7) is True
        # If include_boundary=False, point on segment tolerance returns False
        assert point_in_polygon(pt_outside_eps, poly, include_boundary=False, tol=1e-7) is False

        # Test 2: With strict tolerance tol = 1e-9
        # Now 1e-8 > tol (1e-8 > 1e-9), so distance 1e-8 is OUTSIDE segment tolerance!
        # Thus point_on_segment returns False, and ray-casting evaluates pure topology:
        # Pt inside: ray crosses y=0 boundary -> strictly inside
        assert point_in_polygon((2.5, 1e-8), poly, include_boundary=False, tol=1e-9) is True
        # Pt outside: ray does not cross -> strictly outside
        assert point_in_polygon((2.5, -1e-8), poly, include_boundary=True, tol=1e-9) is False

        # Test 3: Large coordinate offsets (e.g. coordinates in hundreds of meters)
        offset_poly = [[1000.0, 2000.0], [1005.0, 2000.0], [1005.0, 2004.0], [1000.0, 2004.0]]
        c_off = compute_polygon_centroid(offset_poly)
        assert pytest.approx(c_off[0], abs=1e-6) == 1002.5
        assert pytest.approx(c_off[1], abs=1e-6) == 2002.0
        assert point_in_polygon((1002.5, 2002.0), offset_poly) is True
        assert point_in_polygon((999.0, 2002.0), offset_poly) is False

    def test_pip_throughput_benchmark_50k(self):
        """
        Empirical throughput benchmark: 50,000 queries over complex geometries.
        Verifies execution speed and latency requirements for real-time ROS 2 integration.
        """
        # Concave U-shape with 8 vertices
        u_poly = [
            [0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [8.0, 10.0],
            [8.0, 2.0], [2.0, 2.0], [2.0, 10.0], [0.0, 10.0]
        ]
        bbox = compute_bounding_box(u_poly)

        # Pre-generate 50,000 query points: mix of inside, cavity, and outside
        random.seed(42)
        queries = []
        for _ in range(50000):
            qx = random.uniform(-2.0, 12.0)
            qy = random.uniform(-2.0, 12.0)
            queries.append((qx, qy))

        start_time = time.perf_counter()
        inside_count = 0
        for pt in queries:
            if point_in_polygon(pt, u_poly, bounding_box=bbox, include_boundary=True):
                inside_count += 1
        elapsed = time.perf_counter() - start_time

        qps = len(queries) / elapsed
        us_per_query = (elapsed / len(queries)) * 1e6

        print(f"\n[BENCHMARK] 50,000 PIP Queries Completed in {elapsed:.4f}s")
        # Use ASCII 'us/query' to avoid Windows CP1252 charmap encoding issues
        print(f"[BENCHMARK] Throughput: {qps:,.0f} queries/sec | Latency: {us_per_query:.2f} us/query")
        print(f"[BENCHMARK] Positive matches: {inside_count}/{len(queries)} ({inside_count/len(queries)*100:.1f}%)")

        # Assertion: Throughput must exceed 20,000 queries/sec (< 50 microseconds per query)
        # Even on Pi 5, Nav2 runs pose updates at 10-50Hz, so 20,000 QPS provides 400x safety headroom.
        assert qps > 20000, f"Throughput too low: {qps:.0f} QPS < 20,000 QPS target"


# ==============================================================================
# SECTION 2: Adversarial Database Concurrency Stress Tests
# ==============================================================================
class TestAdversarialDatabaseConcurrency:
    """
    High-concurrency stress test for MAGRoomRegistry and MAGDatabase.
    Launches 20 concurrent worker threads reading and writing simultaneously.
    Validates zero database lock errors, PRAGMA busy_timeout resilience,
    100% data consistency, and LRU cache adherence (<= 64 items).
    """

    def test_20_thread_high_concurrency_stress(self, temp_db, temp_yaml):
        """
        20 Concurrent Threads with independent SQLite connections:
        - 10 Reader Threads:
            - Continuous match_room_by_pose
            - Continuous get_room
            - Continuous get_room_signatures
            - Continuous list_rooms
            - Continuous get_nearest_room
        - 10 Writer Threads:
            - 3 Room Insertion / Upsert threads (dynamic rooms)
            - 3 Multimodal Signature Registration threads (512D VPR, 360-bin LiDAR)
            - 2 TRINITY Direct DB Writer threads (episodes, facts)
            - 2 User Preference & Room Deletion threads
        """
        # Initialize base rooms with an initial registry
        init_reg = MAGRoomRegistry(db_path=temp_db, yaml_path=temp_yaml)
        
        base_rooms = [
            ("salotto", [[0.0, 0.0], [6.0, 0.0], [6.0, 5.0], [0.0, 5.0]]),
            ("cucina", [[6.0, 0.0], [10.0, 0.0], [10.0, 5.0], [6.0, 5.0]]),
            ("corridoio", [[0.0, 5.0], [10.0, 5.0], [10.0, 7.0], [0.0, 7.0]]),
            ("camera", [[0.0, 7.0], [5.0, 7.0], [5.0, 12.0], [0.0, 12.0]]),
            ("bagno", [[5.0, 7.0], [10.0, 7.0], [10.0, 12.0], [5.0, 12.0]])
        ]
        for name, poly in base_rooms:
            init_reg.insert_room(name, poly, map_name="stress_casa")
            vpr_init = [np.random.randn(512).astype(np.float32)]
            lidar_init = np.random.uniform(0.5, 6.0, 360).astype(np.float32)
            init_reg.register_room_signatures(name, vpr_init, lidar_init)

        errors: List[str] = []
        lock_exceptions: List[str] = []
        stop_event = threading.Event()
        stats = {
            "reads_completed": 0,
            "room_writes_completed": 0,
            "sig_writes_completed": 0,
            "db_writes_completed": 0,
            "deletes_completed": 0
        }
        stats_lock = threading.Lock()

        # ----------------------------------------------------------------------
        # 10 Reader Tasks (each thread has its own registry / connection)
        # ----------------------------------------------------------------------
        def reader_task(worker_id: int):
            reg = MAGRoomRegistry(db_path=temp_db)
            reads = 0
            while not stop_event.is_set():
                try:
                    # Random pose
                    qx = random.uniform(0.0, 10.0)
                    qy = random.uniform(0.0, 12.0)
                    reg.match_room_by_pose(qx, qy)
                    
                    # Random room query
                    target = random.choice(["salotto", "cucina", "corridoio", "camera", "bagno"])
                    reg.get_room(target)
                    reg.get_room_signatures(target)
                    reg.get_nearest_room(qx, qy)
                    reg.list_rooms()
                    reads += 1
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower():
                        lock_exceptions.append(f"Reader {worker_id} lock error: {e}")
                    errors.append(f"Reader {worker_id} sqlite error: {e}")
                except Exception as e:
                    errors.append(f"Reader {worker_id} unexpected error: {e}")
                time.sleep(0.001)

            with stats_lock:
                stats["reads_completed"] += reads

        # ----------------------------------------------------------------------
        # 3 Room Upsert Writer Tasks (each thread has its own registry)
        # ----------------------------------------------------------------------
        def room_writer_task(worker_id: int):
            reg = MAGRoomRegistry(db_path=temp_db)
            writes = 0
            counter = 0
            while not stop_event.is_set():
                try:
                    room_name = f"dyn_room_{worker_id}_{counter % 20}"
                    x0 = 15.0 + worker_id * 5.0
                    y0 = 15.0 + (counter % 5) * 5.0
                    poly = [[x0, y0], [x0 + 4.0, y0], [x0 + 4.0, y0 + 4.0], [x0, y0 + 4.0]]
                    success = reg.insert_room(room_name, poly, map_name="stress_casa")
                    if success:
                        writes += 1
                    counter += 1
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower():
                        lock_exceptions.append(f"RoomWriter {worker_id} lock error: {e}")
                    errors.append(f"RoomWriter {worker_id} sqlite error: {e}")
                except Exception as e:
                    errors.append(f"RoomWriter {worker_id} error: {e}")
                time.sleep(0.005)

            with stats_lock:
                stats["room_writes_completed"] += writes

        # ----------------------------------------------------------------------
        # 3 Signature Writer Tasks
        # ----------------------------------------------------------------------
        def sig_writer_task(worker_id: int):
            reg = MAGRoomRegistry(db_path=temp_db)
            writes = 0
            while not stop_event.is_set():
                try:
                    target = random.choice(["salotto", "cucina", "corridoio", "camera", "bagno"])
                    vprs = [np.random.randn(512).astype(np.float32) for _ in range(2)]
                    lidar = np.random.uniform(0.2, 8.0, 360).astype(np.float32)
                    success = reg.register_room_signatures(target, vprs, lidar)
                    if success:
                        writes += 1
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower():
                        lock_exceptions.append(f"SigWriter {worker_id} lock error: {e}")
                    errors.append(f"SigWriter {worker_id} sqlite error: {e}")
                except Exception as e:
                    errors.append(f"SigWriter {worker_id} error: {e}")
                time.sleep(0.008)

            with stats_lock:
                stats["sig_writes_completed"] += writes

        # ----------------------------------------------------------------------
        # 2 Direct Database Writer Tasks (Episodes & Facts)
        # ----------------------------------------------------------------------
        def direct_db_writer_task(worker_id: int):
            writes = 0
            db = MAGDatabase(temp_db)
            while not stop_event.is_set():
                try:
                    # Simulate TRINITY VUI logging during navigation
                    ep_id = db.insert_episode(
                        user_input=f"Dove ti trovi Marcus {writes}?",
                        robot_response=f"Mi trovo nel salotto (worker {worker_id})",
                        importance=0.6,
                        was_successful=1
                    )
                    db.insert_fact(
                        fact_text=f"Marcus ha localizzato la stanza {worker_id} alle {time.time()}",
                        fact_type="spatial_localization",
                        source_episode_id=ep_id,
                        confidence=0.9
                    )
                    writes += 1
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower():
                        lock_exceptions.append(f"DirectDbWriter {worker_id} lock error: {e}")
                    errors.append(f"DirectDbWriter {worker_id} sqlite error: {e}")
                except Exception as e:
                    errors.append(f"DirectDbWriter {worker_id} error: {e}")
                time.sleep(0.005)

            with stats_lock:
                stats["db_writes_completed"] += writes

        # ----------------------------------------------------------------------
        # 2 User Preference & Deletion Tasks
        # ----------------------------------------------------------------------
        def preference_and_delete_task(worker_id: int):
            deletes = 0
            db = MAGDatabase(temp_db)
            reg = MAGRoomRegistry(db_path=temp_db)
            while not stop_event.is_set():
                try:
                    db.upsert_user_preference(
                        user_name=f"operator_{worker_id}",
                        preference_key="language",
                        preference_value="it",
                        confidence=0.95
                    )
                    # Delete dynamic room if exists
                    target = f"dyn_room_{worker_id}_{random.randint(0, 19)}"
                    if reg.get_room(target):
                        reg.delete_room(target)
                        deletes += 1
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower():
                        lock_exceptions.append(f"PrefDelWriter {worker_id} lock error: {e}")
                    errors.append(f"PrefDelWriter {worker_id} sqlite error: {e}")
                except Exception as e:
                    errors.append(f"PrefDelWriter {worker_id} error: {e}")
                time.sleep(0.01)

            with stats_lock:
                stats["deletes_completed"] += deletes

        # ----------------------------------------------------------------------
        # Spawn exactly 20 concurrent worker threads
        # ----------------------------------------------------------------------
        threads: List[threading.Thread] = []
        for i in range(10):
            threads.append(threading.Thread(target=reader_task, args=(i,), name=f"Reader-{i}"))
        for i in range(3):
            threads.append(threading.Thread(target=room_writer_task, args=(i,), name=f"RoomWriter-{i}"))
        for i in range(3):
            threads.append(threading.Thread(target=sig_writer_task, args=(i,), name=f"SigWriter-{i}"))
        for i in range(2):
            threads.append(threading.Thread(target=direct_db_writer_task, args=(i,), name=f"DbWriter-{i}"))
        for i in range(2):
            threads.append(threading.Thread(target=preference_and_delete_task, args=(i,), name=f"PrefDel-{i}"))

        assert len(threads) == 20, f"Expected 20 threads, created {len(threads)}"

        print(f"\n[CONCURRENCY] Starting {len(threads)} concurrent threads for 3.0 seconds...")
        start_concurrency = time.perf_counter()
        for t in threads:
            t.start()

        # Run under full concurrent contention for 3.0 seconds
        time.sleep(3.0)
        stop_event.set()

        for t in threads:
            t.join(timeout=10.0)
        concurrency_duration = time.perf_counter() - start_concurrency

        print(f"[CONCURRENCY] Concurrency Run Finished in {concurrency_duration:.2f}s")
        print(f"[CONCURRENCY] Stats: Reads={stats['reads_completed']}, "
              f"RoomWrites={stats['room_writes_completed']}, "
              f"SigWrites={stats['sig_writes_completed']}, "
              f"DbWrites={stats['db_writes_completed']}, "
              f"Deletes={stats['deletes_completed']}")

        # ----------------------------------------------------------------------
        # Assertions & Invariants Verification
        # ----------------------------------------------------------------------
        # 1. Zero database lock exceptions
        assert len(lock_exceptions) == 0, f"Encountered database lock exceptions: {lock_exceptions}"
        assert len(errors) == 0, f"Encountered concurrency errors: {errors}"

        # 2. SQLite Database Integrity Check
        conn = sqlite3.connect(temp_db)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert integrity.lower() == "ok", f"Integrity check failed: {integrity}"
        assert journal_mode.lower() == "wal", f"Journal mode lost WAL: {journal_mode}"

        # 3. Base rooms consistency & persistence
        verify_reg = MAGRoomRegistry(db_path=temp_db)
        for name, _ in base_rooms:
            room = verify_reg.get_room(name)
            assert room is not None, f"Base room '{name}' missing after concurrency test"
            sigs = verify_reg.get_room_signatures(name)
            assert sigs is not None, f"Signatures for '{name}' missing"
            assert sigs["vpr_cluster"] is not None
            assert sigs["vpr_cluster"].shape[1] == 512, "VPR embedding dimension corrupted"
            assert sigs["lidar_signature"] is not None
            assert len(sigs["lidar_signature"]) == 360, "LiDAR signature bins corrupted"

        # 4. SPEC-05 Red Zone Constraint: LRU Cache <= 64 items
        with verify_reg._lock:
            lru_size = len(verify_reg._signatures_cache)
            assert lru_size <= 64, f"LRU signatures cache violated Red Zone limit: {lru_size} > 64"

        # 5. Direct DB table counts
        db_inspect = MAGDatabase(temp_db)
        db_stats = db_inspect.get_stats()
        assert db_stats.get("total_episodes", 0) > 0, "No episodes written to database"
        assert db_stats.get("total_facts", 0) > 0, "No facts written to database"
        assert db_stats.get("total_profiles", 0) > 0, "No profiles written to database"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "-s"]))
