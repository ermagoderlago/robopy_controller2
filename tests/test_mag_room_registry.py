"""
Unit Test Suite for MAGRoomRegistry, 2D Geometry Engine, SQLite WAL Concurrency,
and TRINITY CAG Integration.
Milestone 1 Verification Specification (7 Test Groups).
"""

import os
import time
import math
import tempfile
import threading
import sqlite3
import pytest
import numpy as np
import yaml

from robot_ai.trinity.mag_database import MAGDatabase
from robot_ai.trinity.mag_room_geometry import (
    compute_bounding_box,
    compute_polygon_centroid,
    point_on_segment,
    point_in_polygon
)
from robot_ai.trinity.mag_room_registry import MAGRoomRegistry, RoomMetadata
from robot_ai.trinity.cag_environment import EnvironmentSnapshot
from robot_ai.trinity.cag_room_bridge import CAGRoomBridge
from robot_ai.trinity.cag_aggregator import ContextAggregator
from robot_ai.trinity.metaprompt_fusion import MetapromptFusion


@pytest.fixture
def temp_db(tmp_path):
    """Creates a path for a temporary test database."""
    db_file = tmp_path / "test_mag_rooms.db"
    return str(db_file)


@pytest.fixture
def temp_yaml(tmp_path):
    """Creates a path for a temporary rooms YAML file."""
    yaml_file = tmp_path / "rooms_metadata.yaml"
    return str(yaml_file)


# ==============================================================================
# Test Group 1: Database Creation & Table Schema Integrity (WAL Mode)
# ==============================================================================
class TestDatabaseAndWAL:
    def test_wal_mode_and_pragmas(self, temp_db):
        """Verifies WAL mode, synchronous=NORMAL, and busy_timeout=5000ms."""
        registry = MAGRoomRegistry(db_path=temp_db)
        
        conn = registry._db._get_connection()
        cursor = conn.cursor()
        mode = cursor.execute("PRAGMA journal_mode").fetchone()[0]
        sync = cursor.execute("PRAGMA synchronous").fetchone()[0]
        timeout = cursor.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        
        assert mode.lower() == "wal"
        assert sync in (1, "NORMAL", "normal")
        assert timeout >= 5000

    def test_schema_integrity(self, temp_db):
        """Verifies rooms and room_signatures tables exist with correct columns and indices."""
        registry = MAGRoomRegistry(db_path=temp_db)
        
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "rooms" in tables
        assert "room_signatures" in tables
        
        # Verify rooms columns
        room_cols = [r["name"] for r in cursor.execute("PRAGMA table_info(rooms)").fetchall()]
        for col in ["id", "room_name", "display_name", "floor_id", "centroid_x", "centroid_y", "polygon_json", "created_at", "updated_at"]:
            assert col in room_cols
            
        # Verify room_signatures columns
        sig_cols = [r["name"] for r in cursor.execute("PRAGMA table_info(room_signatures)").fetchall()]
        for col in ["id", "room_name", "vpr_cluster_blob", "vpr_count", "lidar_signature_blob", "created_at", "updated_at"]:
            assert col in sig_cols
            
        conn.close()


# ==============================================================================
# Test Group 2: CRUD Operations for Rooms and Multimodal Signatures
# ==============================================================================
class TestRoomCRUDAndSignatures:
    def test_room_crud(self, temp_db):
        """Tests Create, Read, List, Update and Delete of room records."""
        registry = MAGRoomRegistry(db_path=temp_db)
        
        # 1. Create
        poly = [[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0]]
        res = registry.insert_room("salotto", poly, map_name="casa_piano1", floor="piano_1", display_name="Salotto Principale")
        assert res is True
        
        # 2. Read
        room = registry.get_room("salotto")
        assert room is not None
        assert room.room_name == "salotto"
        assert room.display_name == "Salotto Principale"
        assert room.floor == "piano_1"
        assert room.map_name == "casa_piano1"
        assert room.centroid == (2.5, 2.0)
        assert room.bounding_box == (0.0, 0.0, 5.0, 4.0)
        
        # 3. List
        all_rooms = registry.list_rooms()
        assert len(all_rooms) == 1
        assert all_rooms[0].room_name == "salotto"
        
        # 4. Update (re-insert with new boundary)
        new_poly = [[0.0, 0.0], [6.0, 0.0], [6.0, 4.0], [0.0, 4.0]]
        registry.insert_room("salotto", new_poly, map_name="casa_piano1", floor="piano_1")
        updated_room = registry.get_room("salotto")
        assert updated_room.centroid == (3.0, 2.0)
        assert updated_room.bounding_box == (0.0, 0.0, 6.0, 4.0)
        
        # 5. Delete
        deleted = registry.delete_room("salotto")
        assert deleted is True
        assert registry.get_room("salotto") is None
        assert len(registry.list_rooms()) == 0

    def test_signatures_and_cascade(self, temp_db):
        """Tests storing and retrieving VPR and LiDAR signatures, and cascade deletion."""
        registry = MAGRoomRegistry(db_path=temp_db)
        
        poly = [[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0]]
        registry.insert_room("salotto", poly, map_name="casa_piano1")
        
        vpr = np.random.randn(512).astype(np.float32)
        vpr /= np.linalg.norm(vpr)
        lidar = np.linspace(0.5, 6.0, 360).astype(np.float32)
        
        success = registry.register_room_signatures("salotto", [vpr], lidar)
        assert success is True
        
        sigs = registry.get_room_signatures("salotto")
        assert sigs is not None
        assert len(sigs["vpr_embeddings"]) == 1
        assert sigs["vpr_embeddings"][0].shape == (512,)
        # Tolerance for float16 half-precision round-trip
        assert np.allclose(sigs["vpr_embeddings"][0], vpr, atol=1e-3)
        assert sigs["lidar_signature"].shape == (360,)
        assert np.allclose(sigs["lidar_signature"], lidar, atol=1e-5)
        
        # Cascade delete test
        registry.delete_room("salotto")
        sigs_after = registry.get_room_signatures("salotto")
        assert sigs_after is None or len(sigs_after.get("vpr_embeddings", [])) == 0


# ==============================================================================
# Test Group 3: YAML Round-Trip Loading and Saving
# ==============================================================================
class TestYAMLRoundTripAndSync:
    def test_yaml_round_trip_export_import(self, temp_db, temp_yaml):
        """Tests that rooms exported to YAML can be imported back with full fidelity."""
        reg1 = MAGRoomRegistry(db_path=temp_db, yaml_path=temp_yaml)
        
        reg1.insert_room("salotto", [[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0]], map_name="casa_piano1")
        reg1.insert_room("cucina", [[5.0, 0.0], [8.0, 0.0], [8.0, 4.0], [5.0, 4.0]], map_name="casa_piano1")
        
        vpr = np.random.randn(512).astype(np.float32)
        vpr /= np.linalg.norm(vpr)
        lidar = np.ones(360, dtype=np.float32) * 2.5
        reg1.register_room_signatures("salotto", [vpr], lidar)
        
        # Export
        export_res = reg1.export_to_yaml(temp_yaml)
        assert export_res is True
        assert os.path.exists(temp_yaml)
        
        # Create a fresh database and import from YAML
        db2_path = temp_db + "_imported.db"
        reg2 = MAGRoomRegistry(db_path=db2_path, yaml_path=temp_yaml)
        
        rooms = reg2.list_rooms()
        assert len(rooms) == 2
        room_names = [r.room_name for r in rooms]
        assert "salotto" in room_names
        assert "cucina" in room_names
        
        # Check coordinates and centroid
        salotto2 = reg2.get_room("salotto")
        assert salotto2.centroid == (2.5, 2.0)
        assert salotto2.bounding_box == (0.0, 0.0, 5.0, 4.0)
        
        # Check signatures imported
        sigs2 = reg2.get_room_signatures("salotto")
        assert sigs2 is not None
        assert len(sigs2["vpr_embeddings"]) == 1
        assert np.allclose(sigs2["vpr_embeddings"][0], vpr, atol=1e-3)

    def test_yaml_error_handling(self, temp_db, temp_yaml):
        """Tests graceful handling of invalid YAML syntax and malformed room entries."""
        reg = MAGRoomRegistry(db_path=temp_db, yaml_path=temp_yaml)
        
        # 1. Broken YAML syntax
        with open(temp_yaml, "w", encoding="utf-8") as f:
            f.write("rooms: [bad_syntax :::: }}}")
            
        with pytest.raises(Exception):
            reg.import_from_yaml(temp_yaml)
            
        # 2. Malformed rooms list: one valid room and one invalid (polygon < 3 vertices)
        valid_and_invalid_yaml = {
            "version": "1.0",
            "rooms": [
                {
                    "room_name": "valid_room",
                    "polygon": [[0.0, 0.0], [3.0, 0.0], [3.0, 3.0], [0.0, 3.0]]
                },
                {
                    "room_name": "invalid_room",
                    "polygon": [[0.0, 0.0], [1.0, 1.0]]  # Only 2 points
                }
            ]
        }
        with open(temp_yaml, "w", encoding="utf-8") as f:
            yaml.safe_dump(valid_and_invalid_yaml, f)
            
        count = reg.import_from_yaml(temp_yaml)
        assert count == 1
        assert reg.get_room("valid_room") is not None
        assert reg.get_room("invalid_room") is None


# ==============================================================================
# Test Group 4: Point-In-Polygon Edge Cases (Ray-Casting Algorithm)
# ==============================================================================
class TestGeometryAlgorithms:
    def test_pip_all_cases(self, temp_db):
        """Tests inside, outside, vertex, edge, and collinear outside cases."""
        registry = MAGRoomRegistry(db_path=temp_db)
        
        # Rectangle [0,0] to [5,4]
        poly = [[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0]]
        registry.insert_room("salotto", poly, map_name="casa_piano1")
        
        # Strictly inside
        assert registry.match_room_by_pose(2.5, 2.0) == "salotto"
        # Strictly outside
        assert registry.match_room_by_pose(6.0, 2.0) is None
        assert registry.match_room_by_pose(-1.0, 1.0) is None
        assert registry.match_room_by_pose(2.5, 5.0) is None
        # On vertices (boundary inclusive)
        assert registry.match_room_by_pose(0.0, 0.0) == "salotto"
        assert registry.match_room_by_pose(5.0, 0.0) == "salotto"
        assert registry.match_room_by_pose(5.0, 4.0) == "salotto"
        assert registry.match_room_by_pose(0.0, 4.0) == "salotto"
        # On edges
        assert registry.match_room_by_pose(2.5, 0.0) == "salotto"
        assert registry.match_room_by_pose(5.0, 2.0) == "salotto"
        assert registry.match_room_by_pose(2.5, 4.0) == "salotto"
        assert registry.match_room_by_pose(0.0, 2.0) == "salotto"
        # Collinear outside segment
        assert registry.match_room_by_pose(7.0, 0.0) is None
        assert registry.match_room_by_pose(-2.0, 0.0) is None

    def test_concave_l_shape(self, temp_db):
        """Tests concave L-shaped polygon interior points and exterior cutout pocket."""
        registry = MAGRoomRegistry(db_path=temp_db)
        
        # L-Shape: [0,0]->[4,0]->[4,2]->[2,2]->[2,4]->[0,4]
        l_poly = [[0.0, 0.0], [4.0, 0.0], [4.0, 2.0], [2.0, 2.0], [2.0, 4.0], [0.0, 4.0]]
        registry.insert_room("corridoio", l_poly, map_name="casa_piano1")
        
        # Interior points
        assert registry.match_room_by_pose(1.0, 1.0) == "corridoio"
        assert registry.match_room_by_pose(3.0, 1.0) == "corridoio"
        assert registry.match_room_by_pose(1.0, 3.0) == "corridoio"
        # Cutout pocket (exterior of L-shape)
        assert registry.match_room_by_pose(3.0, 3.0) is None
        # Boundary of cutout
        assert registry.match_room_by_pose(2.0, 3.0) == "corridoio"

    def test_adjacent_shared_boundary(self, temp_db):
        """Tests deterministic resolution of points on or near shared wall between adjacent rooms."""
        registry = MAGRoomRegistry(db_path=temp_db)
        
        # Room A: [0,0] to [4,4] (centroid at 2.0, 2.0)
        # Room B: [4,0] to [8,4] (centroid at 6.0, 2.0)
        registry.insert_room("room_a", [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0]])
        registry.insert_room("room_b", [[4.0, 0.0], [8.0, 0.0], [8.0, 4.0], [4.0, 4.0]])
        
        # Point slightly inside Room A
        assert registry.match_room_by_pose(3.9, 2.0) == "room_a"
        # Point slightly inside Room B
        assert registry.match_room_by_pose(4.1, 2.0) == "room_b"
        # Point exactly on shared wall (4.0, 2.0) resolves deterministically without exception
        res = registry.match_room_by_pose(4.0, 2.0)
        assert res in ("room_a", "room_b")


# ==============================================================================
# Test Group 5: Centroid Calculation vs Theoretical Values
# ==============================================================================
class TestCentroidTheoretical:
    def test_centroid_rectangle(self, temp_db):
        """Rectangle [0,0] to [6,4] -> theoretical centroid (3.0, 2.0)."""
        poly = [[0.0, 0.0], [6.0, 0.0], [6.0, 4.0], [0.0, 4.0]]
        c = compute_polygon_centroid(poly)
        assert pytest.approx(c[0], abs=1e-6) == 3.0
        assert pytest.approx(c[1], abs=1e-6) == 2.0

    def test_centroid_right_triangle(self, temp_db):
        """Right triangle [0,0], [6,0], [0,9] -> theoretical centroid (2.0, 3.0)."""
        poly = [[0.0, 0.0], [6.0, 0.0], [0.0, 9.0]]
        c = compute_polygon_centroid(poly)
        assert pytest.approx(c[0], abs=1e-6) == 2.0
        assert pytest.approx(c[1], abs=1e-6) == 3.0

    def test_centroid_l_shaped_composite(self, temp_db):
        """
        L-shape polygon: [0,0]->[4,0]->[4,2]->[2,2]->[2,4]->[0,4].
        Composite of R1=[0,0]x[2,4] (A=8, C=(1,2)) and R2=[2,0]x[4,2] (A=4, C=(3,1)).
        Total Area = 12.
        Cx = (8*1 + 4*3)/12 = 20/12 = 1.666667
        Cy = (8*2 + 4*1)/12 = 20/12 = 1.666667
        """
        l_poly = [[0.0, 0.0], [4.0, 0.0], [4.0, 2.0], [2.0, 2.0], [2.0, 4.0], [0.0, 4.0]]
        c = compute_polygon_centroid(l_poly)
        assert pytest.approx(c[0], abs=1e-5) == 1.666667
        assert pytest.approx(c[1], abs=1e-5) == 1.666667

    def test_centroid_vertex_orientation_invariance(self, temp_db):
        """Verifies that counter-clockwise and clockwise vertex orderings yield identical centroids."""
        ccw_poly = [[0.0, 0.0], [5.0, 0.0], [5.0, 3.0], [2.0, 4.0], [0.0, 3.0]]
        cw_poly = list(reversed(ccw_poly))
        
        c_ccw = compute_polygon_centroid(ccw_poly)
        c_cw = compute_polygon_centroid(cw_poly)
        
        assert pytest.approx(c_ccw[0], abs=1e-6) == c_cw[0]
        assert pytest.approx(c_ccw[1], abs=1e-6) == c_cw[1]


# ==============================================================================
# Test Group 6: Thread Safety and WAL Concurrency (PRAGMA busy_timeout)
# ==============================================================================
class TestWALConcurrency:
    def test_multithreaded_wal_access(self, temp_db):
        """10 concurrent threads (5 readers, 5 writers) executing without database is locked error."""
        registry = MAGRoomRegistry(db_path=temp_db)
        poly = [[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0]]
        registry.insert_room("salotto", poly, map_name="casa_piano1")
        
        errors = []
        stop_event = threading.Event()
        
        def reader_task():
            reg = MAGRoomRegistry(db_path=temp_db)
            while not stop_event.is_set():
                try:
                    reg.match_room_by_pose(2.5, 2.0)
                    reg.get_room("salotto")
                    reg.get_room_signatures("salotto")
                except Exception as e:
                    errors.append(f"Reader error: {e}")
                    break
                time.sleep(0.005)

        def writer_task(idx):
            reg = MAGRoomRegistry(db_path=temp_db)
            counter = 0
            while not stop_event.is_set():
                try:
                    vpr = np.random.randn(512).astype(np.float32)
                    reg.register_room_signatures("salotto", [vpr], np.zeros(360, dtype=np.float32))
                    # Occasionally insert a temporary room
                    if counter % 5 == 0:
                        reg.insert_room(f"room_dyn_{idx}_{counter}", [[10.0, 10.0], [12.0, 10.0], [12.0, 12.0], [10.0, 12.0]])
                    counter += 1
                except Exception as e:
                    errors.append(f"Writer {idx} error: {e}")
                    break
                time.sleep(0.01)

        threads = [threading.Thread(target=reader_task) for _ in range(5)] + \
                  [threading.Thread(target=writer_task, args=(i,)) for i in range(5)]
                  
        for t in threads:
            t.start()
        time.sleep(1.5)
        stop_event.set()
        for t in threads:
            t.join()
            
        assert len(errors) == 0, f"Concurrency errors encountered: {errors}"
        
        # Verify database integrity check
        conn = sqlite3.connect(temp_db)
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        assert check == "ok"


# ==============================================================================
# Test Group 7: CAG Environment Snapshot Updates & Token Budget
# ==============================================================================
class TestCAGIntegration:
    def test_cag_snapshot_formatting_and_budget(self):
        """Verifies exact string formatting and compact token budget (< 25 tokens)."""
        snapshot = EnvironmentSnapshot()
        snapshot.update_location(
            room_name="salotto",
            location=(2.1, 1.8),
            map_name="casa_piano1",
            covariance_trace=0.042,
            dist_to_centroid=0.4
        )
        
        text = snapshot.to_text()
        assert text == "[ENV] Room: salotto (near centroid 0.4m) | Map: casa_piano1 | AMCL cov: 0.042"
        # Total characters ~74 chars (< 100 chars, ~18 tokens)
        assert len(text) <= 100
        approx_tokens = len(text) // 4
        assert approx_tokens < 25

    def test_cag_room_bridge_integration(self, temp_db):
        """Verifies that CAGRoomBridge correctly updates EnvironmentSnapshot based on pose."""
        registry = MAGRoomRegistry(db_path=temp_db)
        # Cucina at [5.5, 0] to [9.2, 3.8] (centroid 7.35, 1.9)
        cucina_poly = [[5.5, 0.0], [9.2, 0.0], [9.2, 3.8], [5.5, 3.8]]
        registry.insert_room("cucina", cucina_poly, map_name="casa_piano1")
        
        snapshot = EnvironmentSnapshot()
        bridge = CAGRoomBridge(
            environment_snapshot=snapshot,
            room_registry=registry,
            map_name="casa_piano1"
        )
        
        # 1. Pose inside cucina
        matched = bridge.feed_pose(7.35, 1.9, cov_trace=0.035, map_name="casa_piano1")
        assert matched == "cucina"
        assert snapshot.room_name == "cucina"
        assert snapshot.map_name == "casa_piano1"
        assert snapshot.covariance_trace == 0.035
        assert snapshot.dist_to_centroid is not None
        assert pytest.approx(snapshot.dist_to_centroid, abs=1e-3) == 0.0
        
        # 2. Pose outside all rooms
        matched_out = bridge.feed_pose(20.0, 20.0, cov_trace=0.15, map_name="casa_piano1")
        assert "Unknown" in matched_out
        assert "cucina" in matched_out  # Reports nearest room
        assert snapshot.room_name == matched_out

    def test_cag_aggregator_and_fusion_integration(self):
        """Verifies that MetapromptFusion includes CAG environment within token budget."""
        aggregator = ContextAggregator()
        aggregator.environment.update_location(
            room_name="salotto",
            location=(2.1, 1.8),
            map_name="casa_piano1",
            covariance_trace=0.042,
            dist_to_centroid=0.4
        )
        
        fusion = MetapromptFusion()
        prompt = fusion.build_prompt(
            user_text="Dove ti trovi?",
            system_prompt="Sei Marcus.",
            cag_environment=aggregator.environment.to_text()
        )
        
        assert "[CONTESTO ATTUALE (CAG)]" in prompt
        assert "[ENV] Room: salotto (near centroid 0.4m) | Map: casa_piano1 | AMCL cov: 0.042" in prompt
