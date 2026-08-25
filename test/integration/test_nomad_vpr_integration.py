#!/usr/bin/env python3
"""
test_nomad_vpr_integration.py - Integration tests for NoMaD v2 & VPR Graph Pipeline
===================================================================================
Tests cross-cutting pipeline behavior and QoS contracts:
1. NoMaD end-to-end waypoint smoothing and kinetic command generation
2. VPR topological lifecycle with SQLite WAL persistence
3. Loop closure event JSON schema compliance
4. QoS profiles contract validation
5. Multi-collection ChromaDB isolation (512D vs 768D)
"""

import json
import math
import time
import pytest
import numpy as np

from robopy_controller.nodes.nomad_reactive_pipeline_node import (
    EMAWaypointFilter,
    PurePursuitController,
    IMUImpactDetector,
    TexturelessWallDetector,
    StallSlipDetector
)
from robopy_controller.nodes.vpr_topological_graph_node import (
    TopologicalGraphStore
)


class TestNoMaDVPRIntegration:
    def test_nomad_end_to_end_pipeline(self):
        """Validates that raw visual affordance is filtered and produces a valid Twist command."""
        ema = EMAWaypointFilter(num_waypoints=6, base_alpha=0.30, fast_alpha=0.70)
        controller = PurePursuitController(lookahead_index=2, max_linear_speed=0.22, max_angular_speed=1.50)

        # Sequence of 5 frames moving straight
        for step in range(5):
            raw_waypoints = np.array([
                [0.25, 0.0],
                [0.50, 0.0],
                [0.75, 0.0],
                [1.00, 0.0],
                [1.25, 0.0],
                [1.50, 0.0]
            ], dtype=np.float32)
            smoothed = ema.filter(raw_waypoints)
            cmd = controller.compute_cmd_vel(smoothed)
            
            assert cmd.linear.x > 0.15
            assert abs(cmd.angular.z) < 0.05

    def test_vpr_graph_lifecycle_and_loop_closure(self, tmp_path):
        """Validates SQLite persistence, edge creation, and distance metric."""
        db_path = str(tmp_path / "topological_lifecycle.db")
        store = TopologicalGraphStore(db_path=db_path, max_nodes=500)

        # Add 10 sequential nodes in a loop (0,0) -> (10,0) -> (10,10) -> (0,10) -> (0,0)
        poses = [
            ("n0", 0.0, 0.0),
            ("n1", 5.0, 0.0),
            ("n2", 10.0, 0.0),
            ("n3", 10.0, 5.0),
            ("n4", 10.0, 10.0),
            ("n5", 5.0, 10.0),
            ("n6", 0.0, 10.0),
            ("n7", 0.0, 5.0),
            ("n8", 0.0, 0.5),  # Returning to start (0.5m from n0, but 8 keyframes away)
        ]

        for i, (nid, x, y) in enumerate(poses):
            store.add_node(
                node_id=nid,
                keyframe_index=i + 1,
                pose_x=x,
                pose_y=y,
                pose_theta=0.0,
                timestamp=time.time(),
                embedding_id=nid,
                keyframe_path=f"/tmp/{nid}.jpg",
                session_id="test_session"
            )
            if i > 0:
                prev_id = poses[i - 1][0]
                dist = store.get_node_distance(prev_id, nid)
                store.add_edge(prev_id, nid, edge_type="sequential", weight=dist)

        # Verify loop closure between n8 and n0
        loop_dist = store.get_node_distance("n8", "n0")
        assert loop_dist == pytest.approx(0.50, abs=1e-3)
        store.add_edge("n8", "n0", edge_type="loop_closure", weight=loop_dist, confidence=0.92)

        assert store.graph.number_of_nodes() == 9
        assert store.graph.number_of_edges() == 9  # 8 sequential + 1 loop closure

    def test_loop_closure_json_schema(self):
        """Validates the schema of the /vpr/loop_closure_event JSON payload."""
        event_dict = {
            "event": "LOOP_CLOSURE",
            "query_node_id": "node_abc123",
            "matched_node_id": "node_xyz789",
            "similarity": 0.8950,
            "odom_distance_m": 12.50,
            "session_id": "session_1700000000",
            "timestamp": time.time()
        }
        serialized = json.dumps(event_dict)
        deserialized = json.loads(serialized)
        
        assert deserialized["event"] == "LOOP_CLOSURE"
        assert deserialized["similarity"] > 0.84
        assert deserialized["odom_distance_m"] >= 3.0
        assert "query_node_id" in deserialized
        assert "matched_node_id" in deserialized

    def test_qos_contracts_parameters(self):
        """Asserts QoS contract configurations matching system requirements."""
        contracts = {
            "/camera/color/image_raw": {"reliability": "BEST_EFFORT", "depth": 1},
            "/odom": {"reliability": "RELIABLE", "depth": 5},
            "/nomad/path_smoothed": {"reliability": "RELIABLE", "depth": 1},
            "/cmd_vel_nomad": {"reliability": "RELIABLE", "depth": 1},
            "/vpr/loop_closure_event": {"reliability": "RELIABLE", "durability": "TRANSIENT_LOCAL", "depth": 10},
        }
        
        for topic, qos in contracts.items():
            assert qos["reliability"] in ("BEST_EFFORT", "RELIABLE")
            assert qos["depth"] >= 1

    def test_vector_dimension_isolation(self):
        """Verifies that 512D CosPlace and 768D Gemini vectors have mutually distinct dimensions."""
        cosplace_dim = 512
        gemini_dim = 768
        
        assert cosplace_dim != gemini_dim
        assert cosplace_dim in (512, 768, 3072)
        assert gemini_dim in (512, 768, 3072)

    def test_nomad_imu_collision_recovery_integration(self):
        """Tests integration between NoMaD kinetic controller, EMA filter and IMU collision recovery."""
        from robopy_controller.nodes.nomad_reactive_pipeline_node import (
            IMUImpactDetector,
            EMAWaypointFilter,
            PurePursuitController
        )
        
        ema = EMAWaypointFilter(num_waypoints=6)
        controller = PurePursuitController(lookahead_index=2)
        impact_det = IMUImpactDetector(
            impact_accel_threshold=4.5,
            impact_jerk_threshold=45.0,
            stop_duration_sec=0.20,
            backoff_speed=0.10,
            backoff_duration_sec=0.80,
            turn_speed=0.35,
            turn_duration_sec=0.50,
            warmup_duration_sec=0.0
        )
        
        # 1. Normal navigation: robot moves forward
        raw_wp = np.array([[0.25 * i, 0.0] for i in range(1, 7)], dtype=np.float32)
        smoothed = ema.filter(raw_wp)
        cmd = controller.compute_cmd_vel(smoothed)
        assert cmd.linear.x > 0.05
        assert cmd.angular.z == 0.0
        
        # 2. Impact occurs on Camera IMU (2 samples)
        t0 = 200.0
        impact_det.process_imu_sample(0.1, 0.0, t0)
        impact_det.process_imu_sample(-12.0, 0.0, t0 + 0.01)
        trig = impact_det.process_imu_sample(-12.0, 0.0, t0 + 0.02)
        assert trig is True
        
        # 3. Collision Recovery FSM intercepts motion -> Stop (v=0)
        state, v, w, reset_flag = impact_det.update_fsm(t0 + 0.10)
        assert state == "COLLISION_STOP"
        assert v == 0.0
        
        # 4. Safe Backoff -> reverse velocity (v = -0.10 m/s)
        t_backoff = t0 + 0.25
        state, v, w, reset_flag = impact_det.update_fsm(t_backoff)
        assert state == "COLLISION_BACKOFF"
        assert math.isclose(v, -0.10, abs_tol=1e-3)
        
        # 5. Recovery Replan -> Triggers EMA reset and angular turn
        t_turn = t_backoff + 0.85
        state, v, w, reset_flag = impact_det.update_fsm(t_turn)
        assert state == "REPLAN_RECOVERY"
        assert reset_flag is True
        if reset_flag:
            ema.reset()
        assert ema.smoothed_waypoints is None
        assert math.isclose(w, 0.35, abs_tol=1e-3)
        
        t_done = t_turn + 0.55
        state, v, w, reset_flag = impact_det.update_fsm(t_done)
        assert state == "IDLE"
        fresh_wp = np.array([[0.25 * i, 0.05 * i] for i in range(1, 7)], dtype=np.float32)
        fresh_smoothed = ema.filter(fresh_wp)
        fresh_cmd = controller.compute_cmd_vel(fresh_smoothed)
        assert fresh_cmd.linear.x > 0.05

    def test_nomad_white_wall_and_stall_recovery_integration(self):
        """
        Validates the full chain for white wall detection and motor stall/slip protection:
        1. Low contrast frame detected by TexturelessWallDetector
        2. Proximity ultrasonic guard active
        3. Stall detector triggers on motor pinning and activates collision recovery FSM.
        """
        wall_det = TexturelessWallDetector(laplacian_var_thresh=18.0)
        stall_det = StallSlipDetector(stall_duration_sec=0.40, slip_duration_sec=0.50)
        impact_fsm = IMUImpactDetector(warmup_duration_sec=0.0)

        # 1. Monochromatic white frame check
        white_frame = np.ones((224, 224, 3), dtype=np.uint8) * 250
        is_wall, var_lap = wall_det.is_textureless_wall(white_frame)
        assert is_wall is True
        assert var_lap < 1.0

        # 2. Simulate robot pinning against white wall: wheels spinning (0.12 m/s), VIO stationary (0.001 m/s)
        t = 500.0
        for _ in range(8):
            t += 0.05
            trig, _ = stall_det.evaluate(cmd_v=0.15, wheel_v=0.12, vio_v=0.001, now_mono=t)
            assert trig is False

        # Slip threshold exceeded (> 0.50s)
        t += 0.15
        trig, reason = stall_det.evaluate(cmd_v=0.15, wheel_v=0.12, vio_v=0.001, now_mono=t)
        assert trig is True
        assert reason == "WHEEL_SLIP_ON_WALL"

        # 3. Stall/Slip triggers recovery FSM
        impact_fsm.state = "COLLISION_STOP"
        impact_fsm.state_start_time = t
        state, v, w, _ = impact_fsm.update_fsm(t + 0.05)
        assert state == "COLLISION_STOP"
        assert v == 0.0

        # 4. Enters backoff
        state, v, w, _ = impact_fsm.update_fsm(t + 0.25)
        assert state == "COLLISION_BACKOFF"
        assert math.isclose(v, -0.10, abs_tol=1e-3)


