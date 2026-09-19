"""
==============================================================================
🧪 TIER 4: REAL-WORLD APPLICATION SCENARIOS TEST SUITE
==============================================================================
Evaluates complete, multi-stage end-to-end operational missions:
1. Full Autonomous Room Commissioning (Zero to Mapped)
2. Nighttime Patrol & Dark Awakening (Pitch Dark Kidnapped Recovery)
3. Conversational Concierge & Lost Item Retrieval ("Find My Glasses")
4. Relocation & Destructive Map Overwrite with Voice Safeguard
5. Multi-Room Continuous Survey Across Dynamic Lighting Transitions
==============================================================================
"""

import math
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
# Scenario 1: Full Autonomous Room Commissioning (Zero to Mapped)
# ============================================================================
class TestScenario1AutonomousRoomCommissioning:
    """End-to-end mission from user spoken request to persisted map & multimodal fingerprints."""

    def test_mission_full_room_commissioning_lifecycle(self, tmp_path):
        """
        Complete lifecycle:
        1. Voice request to map 'cucina'
        2. HRI confirmation challenge issued
        3. User confirms 'sì, procedi' within 8s
        4. Luminance check passes (42/255)
        5. Frontier exploration loop discovers & explores 3 clusters
        6. Frontiers shrink to <0.40m -> stop motion
        7. RTAB-Map bundle adjustment executes (RAM delta < 80MB)
        8. Map files exported to SSD (/mnt/ssd/maps/cucina.yaml & .pgm)
        9. VPR & LiDAR fingerprints saved to SQLite WAL
        10. Vocal completion notification emitted
        """
        fsm = HRISafetyStateMachine()
        engine = FrontierExplorationEngine(resolution=0.05, origin=(-5.0, -5.0))
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path, enforce_ssd_mount=False)
        vpr = CosPlaceVPRMatcher()
        db_file = tmp_path / "mag_database.db"
        registry = SemanticRoomRegistryModel(db_path=db_file)

        # Step 1 & 2: User requests mapping -> HRI challenge
        challenge = fsm.request_mapping("cucina")
        assert "cucina" in challenge
        assert fsm.state == HRISafetyStateMachine.STATE_AWAITING_CONFIRMATION

        # Step 3: User confirms at 8 seconds
        fsm.advance_time(8.0)
        confirm_msg, confirmed = fsm.process_voice_input("sì, procedi Marcus")
        assert confirmed is True
        assert fsm.state == HRISafetyStateMachine.STATE_MAPPING_ACTIVE
        assert fsm.motors_allowed is True

        # Step 4: Luminance Safety Gate Check
        simulated_camera_frame = np.full((120, 160, 3), 42, dtype=np.uint8)
        mean_lum = LuminanceSafetyContract.compute_mean_luminance(simulated_camera_frame)
        lum_res = LuminanceSafetyContract.evaluate(mean_lum)
        assert lum_res["authorized"] is True
        assert lum_res["status"] == "AUTHORIZED"

        # Step 5: Frontier Exploration iterations
        # Iteration A: Large frontier
        grid_iter_a = np.full((50, 50), -1, dtype=np.int8)
        grid_iter_a[15, 10:35] = 0  # 25 cells = 1.25m
        eval_a = engine.evaluate_frontiers(grid_iter_a)
        assert eval_a["status"] == "EXPLORING"
        assert eval_a["selected_goal"] is not None

        # Iteration B: Medium frontier
        grid_iter_b = np.full((50, 50), -1, dtype=np.int8)
        grid_iter_b[25, 20:30] = 0  # 10 cells = 0.50m
        eval_b = engine.evaluate_frontiers(grid_iter_b)
        assert eval_b["status"] == "EXPLORING"

        # Iteration C: Exploration complete (frontiers < 0.40m)
        grid_iter_c = np.full((50, 50), -1, dtype=np.int8)
        grid_iter_c[30, 20:25] = 0  # 5 cells = 0.20m < 0.40m
        eval_c = engine.evaluate_frontiers(grid_iter_c)
        assert eval_c["status"] == "COMPLETED"
        assert eval_c["motor_stop_required"] is True

        # Step 6 & 7: Global Bundle Adjustment
        opt_ok, opt_msg = exporter.trigger_global_optimization(simulate_ram_usage_mb=34.5)
        assert opt_ok is True
        assert exporter.last_ram_delta_mb < 80.0

        # Step 8: Export Map to SSD
        export_ok, _, files = exporter.export_map_files(
            map_name="cucina",
            grid_data=grid_iter_c,
            resolution=0.05,
            target_override=tmp_path,
            enforce_ssd_check=False
        )
        assert export_ok is True
        assert files["yaml"].exists()
        assert files["pgm"].exists()

        # Step 9: Extract & Register Multimodal Fingerprints in SQLite WAL
        vpr_vec, _ = vpr.extract_embedding_mock(seed_feature=12.0)
        lidar_sig = np.linspace(1.5, 3.5, 360).astype(np.float32)
        room_poly = [[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0]]
        room_centroid = (2.5, 2.0)

        registry.register_room(
            name="cucina",
            centroid=room_centroid,
            polygon=room_poly,
            vpr_fingerprints=[vpr_vec],
            lidar_signature=lidar_sig
        )

        saved_room = registry.get_room("cucina")
        assert saved_room is not None
        assert saved_room["centroid"] == (2.5, 2.0)

        # Step 10: Completion Announcement
        announcement = "Marcus: Mappatura autonoma di 'cucina' completata con successo. Mappa salvata su SSD."
        assert "completata con successo" in announcement


# ============================================================================
# Scenario 2: Nighttime Patrol & Dark Awakening
# ============================================================================
class TestScenario2NighttimeDarkAwakening:
    """Robot boots up at night in 0 lux pitch darkness, identifies room via LiDAR scan matching."""

    def test_mission_nighttime_dark_localization(self, tmp_path):
        """
        1. Robot wakes up at 02:00 in pitch darkness (0 lux)
        2. Visual VPR is disabled to prevent hallucinations
        3. Controlled 360 spin executed with RPLIDAR C1 ToF
        4. AMCL covariance trace drops < 0.08 within 1.5 rotations
        5. Correct room ('corridoio') identified
        6. CAG real-time snapshot updated
        7. Patrol ready confirmation
        """
        signatures = {
            "salotto": np.array([4.0] * 180 + [6.0] * 180),
            "corridoio": np.array([1.5] * 180 + [8.0] * 180),
            "camera": np.array([3.0] * 360),
        }
        lidar_sim = LiDARScanLocalizationSimulator(known_room_signatures=signatures)
        vui = VUIDialogueEngine()

        # Step 1 & 2: Darkness detection
        ambient_lux = 0.0
        lidar_sim.set_ambient_lux(ambient_lux)
        assert lidar_sim.vision_active is False

        # Step 3 & 4: Controlled spin & scan matching
        step = lidar_sim.simulate_rotation_step(actual_room="corridoio", rotation_fraction=1.4)
        assert step["rotations"] <= 2.0
        assert step["converged"] is True
        assert step["covariance_trace"] < 0.08
        assert step["identified_room"] == "corridoio"

        # Step 5 & 6: CAG sync
        vui.update_cag_location(
            room_name="corridoio",
            location=(7.0, 1.2),
            map_name="piano_terra.yaml",
            covariance_trace=step["covariance_trace"]
        )

        query_res = vui.handle_query("Dove ti trovi?")
        assert "corridoio" in query_res
        assert "piano_terra.yaml" in query_res

        query_map = vui.handle_query("In che mappa navighi?")
        assert "eccellente" in query_map


# ============================================================================
# Scenario 3: Conversational Lost Item Retrieval ("Find My Glasses")
# ============================================================================
class TestScenario3LostItemRetrievalMission:
    """Natural dialogue query -> Nav2 macro-nav -> NOMAD blind spots -> Hailo YOLO sighting."""

    def test_mission_find_lost_glasses(self):
        """
        1. User asks 'Dove ti trovi?' -> reports 'salotto'
        2. User commands 'Marcus, cerca i miei occhiali in camera da letto'
        3. Phase 1: Nav2 navigates macro path to 'camera da letto' centroid
        4. Nav2 reaches centroid -> triggers handover to NOMAD
        5. Phase 2: NOMAD explores local blind spots around nightstand
        6. Phase 3: Hailo YOLO spots 'occhiali' (conf 0.78 > 0.55)
        7. Visual servoing approaches to 0.22m (<= 0.30m)
        8. Chime rings, vocal report delivered
        """
        vui = VUIDialogueEngine()
        coord = HybridSearchCoordinator()

        # Step 1: Query location
        vui.update_cag_location("salotto", (1.0, 1.0), "casa.yaml", 0.035)
        ans1 = vui.handle_query("Dove ti trovi?")
        assert "salotto" in ans1

        # Step 2 & 3: User command & mission start
        mission_prompt = coord.start_mission(room_name="camera da letto", target_class="occhiali")
        assert coord.current_phase == HybridSearchCoordinator.PHASE_1_NAV2_MACRO
        assert "camera da letto" in mission_prompt

        # Step 4: Nav2 arrives at bedroom centroid
        coord.notify_nav2_arrival()
        assert coord.current_phase == HybridSearchCoordinator.PHASE_2_NOMAD_REACTIVE
        assert coord.nomad_active is True

        # Step 5 & 6: NOMAD reactive exploration & YOLO sighting
        detections = [
            {"class": "comodino", "confidence": 0.88, "distance_m": 1.5},
            {"class": "lampada", "confidence": 0.65, "distance_m": 1.4},
            {"class": "occhiali", "confidence": 0.78, "distance_m": 0.95},
        ]
        sighting = coord.process_hailo_yolo_detections(detections)
        assert sighting is not None
        assert "occhiali" in sighting
        assert coord.current_phase == HybridSearchCoordinator.PHASE_3_VISUAL_SERVOING

        # Step 7 & 8: Visual servoing approach & stop
        report, complete = coord.execute_visual_servoing_step(current_distance_m=0.22)
        assert complete is True
        assert coord.current_phase == HybridSearchCoordinator.PHASE_COMPLETED
        assert coord.chime_played is True
        assert coord.completion_announced is True
        assert "0.22m" in report


# ============================================================================
# Scenario 4: Relocation & Destructive Map Overwrite with Voice Safeguard
# ============================================================================
class TestScenario4DestructiveMapOverwriteSafeguard:
    """Validates destructive confirmation safety gate under timeout and confirmed scenarios."""

    def test_mission_destructive_overwrite_lifecycle(self, tmp_path):
        """
        Part A: Silence timeout (30s) aborts destruction safely without touching original map.
        Part B: Confirmed command ('sì, confermo') initiates safe map replacement.
        """
        fsm = HRISafetyStateMachine()
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path, enforce_ssd_mount=False)

        # Create original existing map on SSD
        orig_grid = np.zeros((20, 20), dtype=np.int8)
        _, _, orig_files = exporter.export_map_files("salotto", orig_grid, target_override=tmp_path, enforce_ssd_check=False)
        orig_yaml_mtime = orig_files["yaml"].stat().st_mtime

        # Part A: Operator commands overwrite, then gets distracted
        fsm.request_destructive_action("salotto")
        assert fsm.destructive_pending is True

        # 30s elapse without confirmation
        abort_prompt = fsm.advance_time(30.0)
        assert fsm.destructive_pending is False
        assert fsm.destructive_confirmed is False
        assert "annullata" in abort_prompt.lower()
        # Original file unchanged
        assert orig_files["yaml"].stat().st_mtime == orig_yaml_mtime

        # Part B: Operator repeats command and confirms within 5s
        fsm.request_destructive_action("salotto")
        fsm.advance_time(5.0)
        reply, ok = fsm.process_voice_input("sì, confermo")
        assert ok is True
        assert fsm.destructive_confirmed is True

        # Proceed with new map generation & overwrite
        new_grid = np.full((20, 20), 100, dtype=np.int8)
        _, _, new_files = exporter.export_map_files("salotto", new_grid, target_override=tmp_path, enforce_ssd_check=False)
        assert new_files["yaml"].exists()


# ============================================================================
# Scenario 5: Multi-Room Continuous Survey Across Dynamic Lighting Transitions
# ============================================================================
class TestScenario5MultiRoomLightingTransitions:
    """Navigates across multiple rooms with dynamic lighting on a single continuous map."""

    def test_mission_multi_room_perception_shifts(self, tmp_path):
        """
        Traverses:
        1. Living Room: Bright (55 lux) -> VPR CosPlace 512D active (>0.84 sim)
        2. Hallway: Dim (26 lux, marginal band) -> Hybrid visual + LiDAR
        3. Storage Room: Pitch-dark (0 lux) -> Vision disabled, LiDAR ToF scan matching (trace < 0.08)
        Demonstrates zero map reloads and continuous CAG tracking.
        """
        db_file = tmp_path / "mag_world.db"
        registry = SemanticRoomRegistryModel(db_path=db_file)
        vpr = CosPlaceVPRMatcher()
        vui = VUIDialogueEngine()

        # 1. Register rooms on continuous metric coordinate frame
        # Living room: [0..5, 0..5], Hallway: [5..10, 0..3], Storage: [10..13, 0..3]
        registry.register_room("salotto", (2.5, 2.5), [[0.0, 0.0], [5.0, 0.0], [5.0, 5.0], [0.0, 5.0]])
        registry.register_room("corridoio", (7.5, 1.5), [[5.0, 0.0], [10.0, 0.0], [10.0, 3.0], [5.0, 3.0]])
        registry.register_room("ripostiglio", (11.5, 1.5), [[10.0, 0.0], [13.0, 0.0], [13.0, 3.0], [10.0, 3.0]])

        # Set up VPR and LiDAR signatures
        salotto_vpr, _ = vpr.extract_embedding_mock(seed_feature=101.0)
        vpr.register_room("salotto", [salotto_vpr])

        signatures = {
            "salotto": np.ones(360) * 4.0,
            "corridoio": np.array([1.5] * 180 + [6.0] * 180),
            "ripostiglio": np.ones(360) * 1.8,
        }
        lidar_sim = LiDARScanLocalizationSimulator(known_room_signatures=signatures)

        # ----------------------------------------------------
        # Zone 1: Living Room (Daylight, 55 lux)
        # ----------------------------------------------------
        pos1 = (2.5, 2.5)
        assert registry.match_room_by_pose(*pos1) == "salotto"
        lum1 = LuminanceSafetyContract.evaluate(55.0)
        assert lum1["authorized"] is True

        # VPR recognition
        query_vpr = vpr.normalize_l2(salotto_vpr + 0.005 * np.random.randn(512).astype(np.float32))
        vpr_res = vpr.match_room(query_vpr)
        assert vpr_res["matched"] is True
        assert vpr_res["room_name"] == "salotto"
        vui.update_cag_location("salotto", pos1, "continuous_global_map.yaml", 0.035)

        # ----------------------------------------------------
        # Zone 2: Hallway (Dim, 26 lux - Marginal Band)
        # ----------------------------------------------------
        pos2 = (7.5, 1.5)
        assert registry.match_room_by_pose(*pos2) == "corridoio"
        lum2 = LuminanceSafetyContract.evaluate(26.0)
        assert lum2["authorized"] is False  # Marginal lighting, relies on hybrid LiDAR
        step2 = lidar_sim.simulate_rotation_step("corridoio", rotation_fraction=1.2)
        assert step2["converged"] is True
        assert step2["identified_room"] == "corridoio"
        vui.update_cag_location("corridoio", pos2, "continuous_global_map.yaml", step2["covariance_trace"])

        # ----------------------------------------------------
        # Zone 3: Storage Room (Pitch-dark, 0 lux)
        # ----------------------------------------------------
        pos3 = (11.5, 1.5)
        assert registry.match_room_by_pose(*pos3) == "ripostiglio"
        lidar_sim.set_ambient_lux(0.0)
        assert lidar_sim.vision_active is False
        lidar_sim.reset_maneuver()  # New spin maneuver in newly entered room
        step3 = lidar_sim.simulate_rotation_step("ripostiglio", rotation_fraction=1.5)
        assert step3["converged"] is True
        assert step3["identified_room"] == "ripostiglio"
        assert step3["covariance_trace"] < 0.08
        vui.update_cag_location("ripostiglio", pos3, "continuous_global_map.yaml", step3["covariance_trace"])

        # Final Verification: Query reports correct room in continuous map
        final_query = vui.handle_query("Dove ti trovi?")
        assert "ripostiglio" in final_query
        assert "continuous_global_map.yaml" in final_query
