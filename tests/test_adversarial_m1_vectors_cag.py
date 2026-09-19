"""
Empirical Adversarial Challenge Test Suite for Milestone 1.
Challenger 2: Adversarial Multimodal Vector & CAG Token Boundary Verifier.

Tests:
1. Multimodal Vector Integrity:
   - Boundary float serialization (zeros, subnormals/denormals, max float16 ~65504, negatives, infs, nans).
   - Array packing input types (numpy arrays, python lists, nested structures).
   - Dimension mismatches (256D, 768D, ragged dimension rejection).
   - Empty clusters and None lidar signatures.
   - Corrupted/truncated binary BLOB handling.
2. LRU Cache Boundary (SPEC-05 Red Zone Constraint <= 64):
   - Capacity strictly capped at <= 64 items under 100+ room accesses.
   - Strict LRU eviction and MRU promotion on access.
   - Cache invalidation upon signature registration and room deletion.
3. CAG Token Ceiling Stress:
   - Extreme room names (5,000+ characters).
   - Visual perception scaling (50,000 objects capped at [:3], 1,000 humans).
   - Integration with MetapromptFusion verifying strict BUDGET_CAG (400 tokens / 1600 chars).
   - Verification that assembled prompt remains strictly below 2500 token ceiling.
4. Crash-Resilience of YAML Sync:
   - Simulated partial write failure during safe_dump; target YAML remains 100% intact.
   - Atomic cleanup of .tmp files upon export failure.
   - Recovery from dirty leftover .tmp files from brownouts.
   - High-fidelity round-trip of boundary floats through YAML.
"""

import os
import math
import tempfile
import sqlite3
from unittest.mock import patch
import pytest
import numpy as np
import yaml

from robot_ai.trinity.mag_database import MAGDatabase
from robot_ai.trinity.mag_room_registry import MAGRoomRegistry
from robot_ai.trinity.cag_environment import EnvironmentSnapshot
from robot_ai.trinity.metaprompt_fusion import MetapromptFusion


@pytest.fixture
def temp_env(tmp_path):
    """Provides temporary paths for DB and YAML isolation."""
    db_path = str(tmp_path / "adversarial_mag.db")
    yaml_path = str(tmp_path / "rooms_metadata.yaml")
    return db_path, yaml_path


# ==============================================================================
# 1. Multimodal Vector Integrity & Boundary Floats
# ==============================================================================
class TestMultimodalVectorIntegrity:
    
    def test_boundary_floats_packing_and_unpacking(self):
        """
        Tests serialization/deserialization of float16 and float32 with:
        zeros, negative zeros, denormals (subnormals), max float16 (65504),
        negative boundaries (-65504), small negatives, infinities, and NaNs.
        """
        boundary_floats = [
            0.0,
            -0.0,
            5.960464477539063e-08,  # Minimum positive subnormal in float16
            3.0517578125e-05,       # Minimum positive normal in float16
            65504.0,                # Max finite float16
            -65504.0,               # Min finite negative float16
            -1.0,
            -0.0001,
            float("inf"),
            float("-inf"),
            float("nan")
        ]
        
        # 1. Float16 test
        arr16 = np.array(boundary_floats, dtype=np.float32)
        blob16 = MAGDatabase.pack_vector_array(arr16, dtype="float16")
        assert blob16 is not None
        assert len(blob16) == len(boundary_floats) * 2  # 2 bytes per float16
        
        unpacked16 = MAGDatabase.unpack_vector_array(blob16, dtype="float16")
        assert len(unpacked16) == len(boundary_floats)
        
        # Verify specific values
        assert unpacked16[0] == 0.0
        assert math.copysign(1.0, unpacked16[1]) == -1.0  # Negative zero preservation
        assert unpacked16[2] > 0.0  # Denormal preserved
        assert np.isclose(unpacked16[4], 65504.0)
        assert np.isclose(unpacked16[5], -65504.0)
        assert np.isinf(unpacked16[8]) and unpacked16[8] > 0
        assert np.isinf(unpacked16[9]) and unpacked16[9] < 0
        assert np.isnan(unpacked16[10])
        
        # 2. Float32 test (LiDAR signatures)
        blob32 = MAGDatabase.pack_vector_array(arr16, dtype="float32")
        assert blob32 is not None
        assert len(blob32) == len(boundary_floats) * 4  # 4 bytes per float32
        
        unpacked32 = MAGDatabase.unpack_vector_array(blob32, dtype="float32")
        assert len(unpacked32) == len(boundary_floats)
        assert np.isclose(unpacked32[4], 65504.0)
        assert np.isclose(unpacked32[5], -65504.0)

    def test_packing_input_types_and_fallbacks(self):
        """Tests that pack_vector_array accepts python lists, tuples, and numpy arrays."""
        py_list = [1.5, -2.5, 3.5]
        py_tuple = (1.5, -2.5, 3.5)
        np_arr = np.array([1.5, -2.5, 3.5], dtype=np.float32)
        
        blob_list = MAGDatabase.pack_vector_array(py_list, dtype="float16")
        blob_tuple = MAGDatabase.pack_vector_array(py_tuple, dtype="float16")
        blob_np = MAGDatabase.pack_vector_array(np_arr, dtype="float16")
        
        assert blob_list == blob_tuple == blob_np
        assert MAGDatabase.pack_vector_array(None) is None
        assert MAGDatabase.unpack_vector_array(None) is None
        assert MAGDatabase.unpack_vector_array(b"") is None

    def test_dimension_mismatches_adaptation(self, temp_env):
        """
        Verifies registry handles non-standard embedding dimensions (256D and 768D)
        dynamically tracking vpr_dim, count, and centroid shapes without crash.
        """
        db_path, yaml_path = temp_env
        reg = MAGRoomRegistry(db_path=db_path)
        
        # Register 256D signature
        reg.insert_room("room_256", [[0,0],[2,0],[2,2],[0,2]])
        vpr_256 = [np.random.randn(256).astype(np.float32) for _ in range(4)]
        res_256 = reg.register_room_signatures("room_256", vpr_256, np.random.rand(360))
        assert res_256 is True
        
        sig_256 = reg.get_room_signatures("room_256")
        assert sig_256["vpr_dim"] == 256
        assert sig_256["vpr_count"] == 4
        assert sig_256["vpr_cluster"].shape == (4, 256)
        assert sig_256["vpr_centroid"].shape == (256,)
        
        # Register 768D signature
        reg.insert_room("room_768", [[0,0],[2,0],[2,2],[0,2]])
        vpr_768 = [np.random.randn(768).astype(np.float32) for _ in range(2)]
        res_768 = reg.register_room_signatures("room_768", vpr_768, np.random.rand(180))
        assert res_768 is True
        
        sig_768 = reg.get_room_signatures("room_768")
        assert sig_768["vpr_dim"] == 768
        assert sig_768["vpr_count"] == 2
        assert sig_768["vpr_cluster"].shape == (2, 768)
        assert sig_768["vpr_centroid"].shape == (768,)
        assert len(sig_768["lidar_signature"]) == 180

    def test_ragged_dimension_rejection(self, temp_env):
        """
        Verifies that passing ragged vectors (e.g. one 512D and one 256D)
        is safely rejected with False and does not corrupt state.
        """
        db_path, _ = temp_env
        reg = MAGRoomRegistry(db_path=db_path)
        reg.insert_room("room_ragged", [[0,0],[1,0],[1,1],[0,1]])
        
        ragged_vpr = [np.random.randn(512).astype(np.float32), np.random.randn(256).astype(np.float32)]
        success = reg.register_room_signatures("room_ragged", ragged_vpr, np.random.rand(360))
        assert success is False
        
        # State should remain clean
        sig = reg.get_room_signatures("room_ragged")
        assert sig == {}

    def test_empty_clusters_and_none_lidar(self, temp_env):
        """Verifies handling of empty VPR clusters and None LiDAR signatures."""
        db_path, _ = temp_env
        reg = MAGRoomRegistry(db_path=db_path)
        reg.insert_room("room_empty", [[0,0],[1,0],[1,1],[0,1]])
        
        success = reg.register_room_signatures("room_empty", [], None)
        assert success is True
        
        sig = reg.get_room_signatures("room_empty")
        assert sig["vpr_embeddings"] == []
        assert sig["vpr_cluster"] is None
        assert sig["vpr_centroid"] is None
        assert sig["vpr_count"] == 0
        assert sig["lidar_signature"] is None
        assert sig["lidar_bins"] == 0


# ==============================================================================
# 2. LRU Cache Boundary (SPEC-05 Constraint <= 64)
# ==============================================================================
class TestLRUCacheBound:
    
    def test_lru_cache_capacity_strictly_bounded_at_64(self, temp_env):
        """
        SPEC-05 Red Zone: LRU cache strictly drops entries when exceeding 64 items.
        Inserts 100 rooms with signatures, queries each room sequentially,
        and asserts len(_signatures_cache) never exceeds 64.
        """
        db_path, _ = temp_env
        reg = MAGRoomRegistry(db_path=db_path)
        
        # Register 100 distinct rooms with 512D VPR and 360-bin LiDAR
        for i in range(100):
            r_name = f"room_{i:03d}"
            reg.insert_room(r_name, [[0,0],[1,0],[1,1],[0,1]])
            reg.register_room_signatures(
                r_name,
                [np.random.randn(512).astype(np.float32)],
                np.random.rand(360).astype(np.float32)
            )
            
        # Cache starts at 0 since registration invalidates cache
        assert len(reg._signatures_cache) == 0
        
        # Query first 64 rooms
        for i in range(64):
            reg.get_room_signatures(f"room_{i:03d}")
            assert len(reg._signatures_cache) == i + 1
            
        assert len(reg._signatures_cache) == 64
        assert "room_000" in reg._signatures_cache
        
        # Query 65th room: room_000 MUST be evicted
        reg.get_room_signatures("room_064")
        assert len(reg._signatures_cache) == 64
        assert "room_000" not in reg._signatures_cache, "Oldest room_000 was not evicted!"
        assert "room_064" in reg._signatures_cache
        
        # Query remaining rooms up to 99: cache must stay exactly 64
        for i in range(65, 100):
            reg.get_room_signatures(f"room_{i:03d}")
            assert len(reg._signatures_cache) == 64
            
        # Oldest 36 rooms (room_000 to room_035) must be evicted
        for i in range(36):
            assert f"room_{i:03d}" not in reg._signatures_cache
            
        # Remaining 64 rooms (room_036 to room_099) must be in cache
        for i in range(36, 100):
            assert f"room_{i:03d}" in reg._signatures_cache

    def test_lru_eviction_order_and_mru_promotion(self, temp_env):
        """
        Verifies that accessing an existing cached entry moves it to MRU (end),
        so that subsequent evictions drop the actual least recently accessed item.
        """
        db_path, _ = temp_env
        reg = MAGRoomRegistry(db_path=db_path)
        
        for i in range(70):
            r_name = f"room_{i:02d}"
            reg.insert_room(r_name, [[0,0],[1,0],[1,1],[0,1]])
            reg.register_room_signatures(r_name, [np.random.randn(512).astype(np.float32)], np.random.rand(360))
            
        # Load 64 rooms (0 to 63)
        for i in range(64):
            reg.get_room_signatures(f"room_{i:02d}")
            
        # Access room_00 (the oldest). This should promote it to MRU.
        reg.get_room_signatures("room_00")
        keys = list(reg._signatures_cache.keys())
        assert keys[-1] == "room_00", "room_00 was not promoted to MRU position!"
        assert keys[0] == "room_01", "room_01 should now be the LRU candidate!"
        
        # Now access room_64 (65th unique room).
        # Eviction should drop room_01, while room_00 remains in cache!
        reg.get_room_signatures("room_64")
        assert len(reg._signatures_cache) == 64
        assert "room_01" not in reg._signatures_cache, "room_01 should have been evicted!"
        assert "room_00" in reg._signatures_cache, "room_00 should have survived because it was accessed!"

    def test_lru_invalidation_on_mutation(self, temp_env):
        """Verifies cache entry is removed when signatures are re-registered or room is deleted."""
        db_path, _ = temp_env
        reg = MAGRoomRegistry(db_path=db_path)
        reg.insert_room("room_dyn", [[0,0],[1,0],[1,1],[0,1]])
        reg.register_room_signatures("room_dyn", [np.random.randn(512).astype(np.float32)], np.random.rand(360))
        
        # Cache the signature
        reg.get_room_signatures("room_dyn")
        assert "room_dyn" in reg._signatures_cache
        
        # Re-register signatures -> should pop from cache
        reg.register_room_signatures("room_dyn", [np.random.randn(512).astype(np.float32)], np.random.rand(360))
        assert "room_dyn" not in reg._signatures_cache
        
        # Re-cache and delete
        reg.get_room_signatures("room_dyn")
        assert "room_dyn" in reg._signatures_cache
        reg.delete_room("room_dyn")
        assert "room_dyn" not in reg._signatures_cache


# ==============================================================================
# 3. CAG Token Ceiling Stress
# ==============================================================================
class TestCAGTokenCeilingStress:
    
    def test_cag_environment_extreme_room_name(self):
        """Verifies to_text handles a 5,000-character room name without crash."""
        snap = EnvironmentSnapshot()
        huge_name = "MassiveChamber_" + ("X" * 5000)
        snap.update_location(
            room_name=huge_name,
            location=(1.0, 2.0),
            map_name="map_hq",
            covariance_trace=0.035,
            dist_to_centroid=0.5
        )
        text = snap.to_text()
        assert text.startswith("[ENV] Room: MassiveChamber_")
        assert "Map: map_hq" in text
        assert "AMCL cov: 0.035" in text
        assert len(text) > 5000

    def test_cag_environment_thousands_of_objects(self):
        """
        Verifies visual_objects caps at 3 items and adds (+N more) count,
        preventing string runaway even with 50,000 recognized entities.
        """
        snap = EnvironmentSnapshot()
        huge_objects = [f"item_{i:05d}" for i in range(50000)]
        snap.update_perception(humans=["Alice"], objects=huge_objects)
        text = snap.to_text()
        
        assert "Objs: item_00000,item_00001,item_00002 (+49997 more)" in text
        assert len(text) < 200

    def test_cag_fusion_token_ceiling_enforcement(self):
        """
        Verifies that even if CAG EnvironmentSnapshot produces a huge output (e.g. 20,000 chars),
        MetapromptFusion enforces BUDGET_CAG = 400 tokens (1600 characters).
        """
        snap = EnvironmentSnapshot()
        # 1,000 humans + huge room name produces ~18,000 characters
        huge_humans = [f"worker_{i:04d}" for i in range(1000)]
        snap.update_location("EnormousHall_" + ("Z" * 10000), (0, 0), "map_hq", 0.01)
        snap.update_perception(humans=huge_humans, objects=["chair", "table"])
        
        cag_env_text = snap.to_text()
        assert len(cag_env_text) > 15000
        
        fusion = MetapromptFusion()
        prompt = fusion.build_prompt(
            user_text="Dove sei?",
            system_prompt="Marcus VUI System",
            cag_environment=cag_env_text
        )
        
        # Find CAG block within prompt
        assert "[CONTESTO ATTUALE (CAG)]" in prompt
        cag_section = prompt.split("[CONTESTO ATTUALE (CAG)]")[1].split("Utente:")[0].strip()
        
        # Verify CAG block is bounded to BUDGET_CAG (400 tokens = 1600 chars + ellipsis)
        assert len(cag_section) <= 1605
        
        # Verify entire prompt is safely below 2500 tokens
        est_tokens = fusion._estimate_tokens(prompt)
        assert est_tokens <= 2500, f"Token ceiling violated: {est_tokens}"
        assert est_tokens < 600  # Should be well within budget


# ==============================================================================
# 4. Crash-Resilience of YAML Sync
# ==============================================================================
class TestYAMLAtomicCrashResilience:
    
    def test_simulated_write_failure_preserves_target_yaml(self, temp_env):
        """
        Simulates sudden disk write failure (e.g. disk full / brownout) during export_to_yaml.
        Asserts target YAML is 100% untouched and .tmp file is cleaned up.
        """
        db_path, yaml_path = temp_env
        temp_path = yaml_path + ".tmp"
        
        # 1. Create baseline target YAML with known original content
        reg = MAGRoomRegistry(db_path=db_path)
        reg.insert_room("original_room", [[0,0],[3,0],[3,3],[0,3]])
        reg.export_to_yaml(yaml_path)
        
        initial_content = open(yaml_path, "r", encoding="utf-8").read()
        assert "original_room" in initial_content
        
        # 2. Insert another room into SQLite (DB has 2 rooms now)
        reg.insert_room("new_room", [[4,4],[6,4],[6,6],[4,6]])
        
        # 3. Simulate failure during yaml.safe_dump (write partial data, then raise IOError)
        def mock_failing_dump(*args, **kwargs):
            stream = args[1]
            stream.write("HALF WRITTEN CORRUPTED GARBAGE")
            stream.flush()
            raise IOError("Simulated Disk Full: [Errno 28] No space left on device")
            
        with patch("yaml.safe_dump", side_effect=mock_failing_dump):
            with pytest.raises(IOError):
                reg.export_to_yaml(yaml_path)
                
        # 4. Assertions:
        # A. Target YAML was NOT overwritten or corrupted
        current_content = open(yaml_path, "r", encoding="utf-8").read()
        assert current_content == initial_content, "Target YAML content changed despite write failure!"
        
        # B. Target YAML is still 100% valid YAML
        loaded = yaml.safe_load(current_content)
        assert len(loaded["rooms"]) == 1
        assert loaded["rooms"][0]["room_name"] == "original_room"
        
        # C. Temporary file .tmp was removed
        assert not os.path.exists(temp_path), "Dangling .tmp file was not cleaned up!"

    def test_recovery_from_preexisting_dangling_tmp(self, temp_env):
        """
        Simulates an abrupt power-cut that previously left a dirty .tmp on disk.
        Verifies subsequent export_to_yaml safely overwrites .tmp and completes cleanly.
        """
        db_path, yaml_path = temp_env
        temp_path = yaml_path + ".tmp"
        
        # 1. Create existing valid target YAML
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write("VALID_ORIGINAL_CONTENT")
            
        # 2. Simulate dirty leftover from brownout
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write("DIRTY_PARTIAL_BYTES_FROM_HARD_RESET")
            
        assert os.path.exists(temp_path)
        
        # 3. Run export_to_yaml
        reg = MAGRoomRegistry(db_path=db_path)
        reg.insert_room("recovery_room", [[0,0],[2,0],[2,2],[0,2]])
        success = reg.export_to_yaml(yaml_path)
        
        assert success is True
        assert not os.path.exists(temp_path), "Temp file should be moved/replaced!"
        
        # 4. Verify target YAML is valid and contains updated room
        with open(yaml_path, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        assert doc["rooms"][0]["room_name"] == "recovery_room"

    def test_yaml_roundtrip_with_boundary_values(self, temp_env):
        """
        Verifies that boundary float values (65504.0, subnormals, zeros)
        survive export to YAML and re-import into a fresh database without distortion.
        """
        db_path, yaml_path = temp_env
        reg = MAGRoomRegistry(db_path=db_path)
        reg.insert_room("boundary_room", [[0,0],[4,0],[4,4],[0,4]])
        
        vpr_data = [65504.0, -65504.0, 0.0, 5.960464477539063e-08] + [0.5] * 508
        vpr_arr = np.array(vpr_data, dtype=np.float32)
        lidar_data = [65504.0, 0.0, 1.2345] + [1.0] * 357
        lidar_arr = np.array(lidar_data, dtype=np.float32)
        
        reg.register_room_signatures("boundary_room", [vpr_arr], lidar_arr)
        reg.export_to_yaml(yaml_path)
        
        # Import into fresh database
        db_path2 = db_path + ".2"
        reg2 = MAGRoomRegistry(db_path=db_path2)
        count = reg2.import_from_yaml(yaml_path)
        assert count == 1
        
        sig = reg2.get_room_signatures("boundary_room")
        vpr_cluster = sig["vpr_cluster"]
        assert np.isclose(vpr_cluster[0][0], 65504.0)
        assert np.isclose(vpr_cluster[0][1], -65504.0)
        assert np.isclose(vpr_cluster[0][2], 0.0)
        assert vpr_cluster[0][3] > 0.0
        assert np.isclose(sig["lidar_signature"][0], 65504.0)
        assert np.isclose(sig["lidar_signature"][1], 0.0)
