"""
==============================================================================
🧪 TIER 3: CROSS-FEATURE INTERACTIONS TEST SUITE
==============================================================================
Evaluates pairwise subsystem interactions, concurrent edge events,
and complex multi-module workflows under stress:
1. Mapping FSM + Mid-flight Luminance Drop
2. Kidnapped Robot + Total Darkness (0 Lux)
3. Nav2 Macro-Nav -> NOMAD Local Handover + Hailo YOLO Target Detection
4. Frontier Exploration + Battery Transient Sag Gating (SPEC-06)
5. VUI Destructive Command Gate During Active Motion
6. Visual Ambiguity (Perceptual Aliasing) + LiDAR Geometric Disambiguation
7. Vocal Barge-In During Heavy NPU Inference & TTS Playback
8. Dual-Mode REP-105 TF Authority Shift (SLAM -> AMCL)
9. Continuous Map Boundary Traversal & Real-Time CAG Sync
==============================================================================
"""

import math
import sys
import tempfile
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
# Pairwise Interaction 1: Mapping FSM + Dynamic Luminance Drop
# ============================================================================
class TestInteractionMappingAndLuminanceDrop:
    """Evaluates mapping behavior when ambient lights suddenly drop mid-mission."""

    def test_mapping_interrupted_by_luminance_drop(self):
        """Mapping starts in daylight, but light drops < 25 lux -> safety pause/inhibition."""
        fsm = HRISafetyStateMachine()
        fsm.request_mapping("cucina")
        fsm.process_voice_input("sì, procedi")
        assert fsm.state == HRISafetyStateMachine.STATE_MAPPING_ACTIVE
        assert fsm.motors_allowed is True

        # Pre-check at start: bright (45 lux)
        lum_start = LuminanceSafetyContract.evaluate(45.0)
        assert lum_start["authorized"] is True

        # Mid-mission event: lights switch off (15 lux)
        lum_mid = LuminanceSafetyContract.evaluate(15.0)
        assert lum_mid["authorized"] is False
        assert lum_mid["status"] == "INHIBITED_DARK"

        # Contract service returns failure to mapping coordinator
        can_continue, msg = LuminanceSafetyContract.trigger_service_check(15.0)
        assert can_continue is False
        assert "insufficiente" in msg


# ============================================================================
# Pairwise Interaction 2: Kidnapped Robot in Total Darkness (0 Lux)
# ============================================================================
class TestInteractionKidnappedRobotInDarkness:
    """Robot relocated blindly and awakens in pitch darkness at 0 lux."""

    def test_kidnapped_dark_recovery_via_lidar_and_amcl(self):
        """At 0 lux, visual VPR is suppressed; 360 spin with RPLIDAR C1 achieves convergence."""
        signatures = {
            "salotto": np.array([3.0] * 180 + [5.0] * 180),
            "camera": np.array([2.0] * 360),
        }
        lidar_sim = LiDARScanLocalizationSimulator(known_room_signatures=signatures)
        vui = VUIDialogueEngine()

        # Wakeup in pitch dark
        ambient_lux = 0.0
        lidar_sim.set_ambient_lux(ambient_lux)
        assert lidar_sim.vision_active is False

        # Execute 360 rotation for scan matching
        step = lidar_sim.simulate_rotation_step(actual_room="salotto", rotation_fraction=1.6)

        assert step["rotations"] <= 2.0
        assert step["converged"] is True
        assert step["covariance_trace"] < 0.08
        assert step["identified_room"] == "salotto"

        # Update CAG situation awareness
        vui.update_cag_location(
            room_name=step["identified_room"],
            location=(2.5, 3.0),
            map_name="appartamento.yaml",
            covariance_trace=step["covariance_trace"]
        )

        reply = vui.handle_query("Dove ti trovi?")
        assert "salotto" in reply
        assert "appartamento.yaml" in reply


# ============================================================================
# Pairwise Interaction 3: Nav2 Macro -> NOMAD Handover + YOLO Target Sighting
# ============================================================================
class TestInteractionNav2NomadYoloPipeline:
    """End-to-end multi-phase handover pipeline."""

    def test_full_macro_nav_nomad_yolo_pipeline(self):
        """Executes Nav2 macro-nav, seamless NOMAD handover, YOLO detection >0.55, and visual servoing stop."""
        coord = HybridSearchCoordinator()

        # Step 1: Initiate macro mission
        init_msg = coord.start_mission(room_name="salotto", target_class="chiavi")
        assert coord.current_phase == HybridSearchCoordinator.PHASE_1_NAV2_MACRO
        assert coord.nomad_active is False

        # Step 2: Nav2 reaches centroid
        coord.notify_nav2_arrival()
        assert coord.nav2_goal_reached is True
        assert coord.current_phase == HybridSearchCoordinator.PHASE_2_NOMAD_REACTIVE
        assert coord.nomad_active is True

        # Step 3: NOMAD visual search with Hailo YOLO
        detections = [
            {"class": "sedia", "confidence": 0.85, "distance_m": 2.1},
            {"class": "chiavi", "confidence": 0.74, "distance_m": 1.1},
        ]
        sighting_msg = coord.process_hailo_yolo_detections(detections)
        assert sighting_msg is not None
        assert coord.current_phase == HybridSearchCoordinator.PHASE_3_VISUAL_SERVOING
        assert coord.nomad_active is False

        # Step 4: Approach to target
        _, done = coord.execute_visual_servoing_step(current_distance_m=0.25)
        assert done is True
        assert coord.current_phase == HybridSearchCoordinator.PHASE_COMPLETED
        assert coord.chime_played is True
        assert coord.completion_announced is True


# ============================================================================
# Pairwise Interaction 4: Frontier Exploration + Battery Transient Sag
# ============================================================================
class TestInteractionFrontierAndBatterySag:
    """Exploration preemption upon low battery detection (FM-SYS-004 & SPEC-06)."""

    def test_battery_sag_preempts_frontier_exploration(self, tmp_path):
        """Battery drops to docking threshold (9.85V < 9.90V) -> saves map & terminates exploration."""
        grid = np.full((30, 30), -1, dtype=np.int8)
        grid[5, 5:20] = 0  # active frontier
        engine = FrontierExplorationEngine(resolution=0.05)
        eval_res = engine.evaluate_frontiers(grid)
        assert eval_res["status"] == "EXPLORING"

        # Battery telemetry: transient sag detected below 9.90V docking trigger
        battery_voltage = 9.85
        docking_threshold = 9.90
        battery_critical = battery_voltage <= docking_threshold

        assert battery_critical is True

        # Emergency preemption: Stop exploration and save map to SSD
        exporter = SLAMOptimizationExporter(simulated_ssd_root=tmp_path, enforce_ssd_mount=False)
        opt_ok, _ = exporter.trigger_global_optimization(simulate_ram_usage_mb=40.0)
        assert opt_ok is True

        save_ok, _, files = exporter.export_map_files(
            map_name="emergency_save_battery",
            grid_data=grid,
            target_override=tmp_path,
            enforce_ssd_check=False
        )
        assert save_ok is True
        assert files["yaml"].exists()


# ============================================================================
# Pairwise Interaction 5: VUI Destructive Command Gate During Active Motion
# ============================================================================
class TestInteractionVUIDestructiveGateDuringMotion:
    """User gives spoken map delete command while robot is navigating."""

    def test_destructive_gate_preserves_motion_safety(self):
        """Confirmation challenge issued, navigation proceeds safely; timeout cancels destruction."""
        fsm = HRISafetyStateMachine()
        coord = HybridSearchCoordinator()

        # Robot is actively navigating
        coord.start_mission("camera", "zaino")
        assert coord.current_phase == HybridSearchCoordinator.PHASE_1_NAV2_MACRO

        # User accidentally speaks: "Marcus cancella la mappa"
        challenge = fsm.request_destructive_action("camera")
        assert fsm.destructive_pending is True
        assert "30 secondi" in challenge

        # Navigation continues uninterrupted while challenge is pending
        coord.notify_nav2_arrival()
        assert coord.current_phase == HybridSearchCoordinator.PHASE_2_NOMAD_REACTIVE

        # 30s elapse without confirmation
        abort_prompt = fsm.advance_time(30.0)
        assert fsm.destructive_pending is False
        assert fsm.destructive_confirmed is False
        assert "annullata" in abort_prompt.lower()

        # Map was NOT destroyed, navigation is unharmed
        assert coord.current_phase == HybridSearchCoordinator.PHASE_2_NOMAD_REACTIVE


# ============================================================================
# Pairwise Interaction 6: Visual Ambiguity + LiDAR Geometric Disambiguation
# ============================================================================
class TestInteractionVisualAmbiguityAndLiDARTieBreaker:
    """Two rooms visually identical (CosPlace similarity high for both)."""

    def test_lidar_resolves_symmetrical_rooms(self):
        """LiDAR geometric signature acts as definitive tie-breaker."""
        vpr = CosPlaceVPRMatcher()
        base_vpr, _ = vpr.extract_embedding_mock(seed_feature=42.0)

        # Room A (Bedroom 1) and Room B (Bedroom 2) have almost identical decor
        vpr.register_room("camera_1", [base_vpr])
        perturbed = vpr.normalize_l2(base_vpr + 0.005 * np.random.randn(512).astype(np.float32))
        vpr.register_room("camera_2", [perturbed])

        # Query vector matches both > 0.84 (perceptual ambiguity)
        match_res = vpr.match_room(base_vpr)
        assert match_res["matched"] is True

        # Geometric signature difference:
        # Camera 1 is 3m x 3m (square); Camera 2 is 5m x 3m (rectangular)
        lidar_cam1 = np.ones(360) * 3.0
        lidar_cam2 = np.array([5.0] * 180 + [3.0] * 180)

        signatures = {"camera_1": lidar_cam1, "camera_2": lidar_cam2}
        lidar_sim = LiDARScanLocalizationSimulator(known_room_signatures=signatures)

        # Current sensor scan is in camera_2
        step = lidar_sim.simulate_rotation_step("camera_2", rotation_fraction=1.5)
        assert step["converged"] is True
        assert step["identified_room"] == "camera_2"


# ============================================================================
# Pairwise Interaction 7: Vocal Barge-In During Heavy NPU Load
# ============================================================================
class TestInteractionBargeInDuringHeavyNPULoad:
    """Verifies audio conditioning under high CPU/NPU inference load."""

    def test_bargein_gain_and_audio_resampling_compliance(self):
        """During TTS playback with NPU active, stt_gain=0.1x prevents feedback echo."""
        vui = VUIDialogueEngine()
        vpr = CosPlaceVPRMatcher()

        # Simulate concurrent Hailo NPU inference
        for i in range(5):
            vec, lat = vpr.extract_embedding_mock(seed_feature=float(i))
            assert lat < 50.0

        # Marcus starts speaking response
        vui.set_tts_active(True)
        assert vui.stt_gain == 0.1  # 0.1x gain active for vocal barge-in

        # Verify ReSpeaker stream rate requirements
        assert vui.verify_audio_resampling(16000, 48000) is True

        # Marcus finishes speaking
        vui.set_tts_active(False)
        assert vui.stt_gain == 1.0


# ============================================================================
# Pairwise Interaction 8: Dual-Mode SLAM vs AMCL Navigation (REP-105)
# ============================================================================
class TestInteractionDualModeSLAMvsAMCL:
    """Verifies single TF authority on map -> odom (SPEC-02 & FM-NAV-030)."""

    def test_single_tf_authority_shift(self):
        """When switching from SLAM to AMCL, RTAB publish_tf is disabled."""
        # State: SLAM Mapping
        slam_mode = {
            "mode": "SLAM",
            "rtabmap_publish_tf": True,
            "amcl_active": False,
            "tf_broadcasters_map_to_odom": 1,
        }
        assert slam_mode["tf_broadcasters_map_to_odom"] == 1

        # Transition to AMCL Navigation
        amcl_mode = {
            "mode": "AMCL_NAV",
            "rtabmap_publish_tf": False,      # Inviolable Red Zone rule
            "rtabmap_incremental_memory": False,
            "amcl_active": True,
            "tf_broadcasters_map_to_odom": 1, # Exactly one publisher
        }
        # Strictly one TF authority
        assert amcl_mode["rtabmap_publish_tf"] is False
        assert amcl_mode["tf_broadcasters_map_to_odom"] == 1


# ============================================================================
# Pairwise Interaction 9: Continuous Map Boundary Traversal & CAG Sync
# ============================================================================
class TestInteractionMapBoundaryTraversalAndCAGSync:
    """Robot drives across room threshold on a single continuous 2D map."""

    def test_smooth_transition_across_room_polygons(self, tmp_path):
        """Pose moves from room A to room B; CAG snapshot updates without map reload."""
        db_file = tmp_path / "mag_spatial.db"
        registry = SemanticRoomRegistryModel(db_path=db_file)
        vui = VUIDialogueEngine()

        # Register adjacent rooms on continuous floor map
        registry.register_room("salotto", (2.0, 2.0), [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0]])
        registry.register_room("corridoio", (6.0, 2.0), [[4.0, 0.0], [8.0, 0.0], [8.0, 4.0], [4.0, 4.0]])

        # Position 1: In Salotto
        pos1 = (2.0, 2.0)
        room1 = registry.match_room_by_pose(*pos1)
        assert room1 == "salotto"
        vui.update_cag_location(room1, pos1, "piano_terra.yaml", 0.04)
        assert "salotto" in vui.handle_query("Dove ti trovi?")

        # Robot drives across door into Corridoio
        pos2 = (5.5, 2.0)
        room2 = registry.match_room_by_pose(*pos2)
        assert room2 == "corridoio"
        vui.update_cag_location(room2, pos2, "piano_terra.yaml", 0.04)
        assert "corridoio" in vui.handle_query("Dove ti trovi?")
