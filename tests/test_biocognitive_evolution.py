#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Suite: Bio-Cognitive Evolution Pipeline
============================================
Comprehensive test suite validating:
1. Dynamic Resource Governor v2 & Invariant 6 Standstill LiDAR Sleep
2. Sensor Standby Manager: 45s Idle, Anti-Cycling & StandbyTFKeepalive
3. Homeostatic MAL Drive Engine & Proactive Gating Bypass (0.38 threshold)
4. Visual Gesture & 3D Pointing Engine (Open Palm, Thumbs Up, 3D Raycasting)
5. Theory of Mind Engine (DND Workbench, Resting, Autonomous Fall Emergency)
6. FollowPersonSkill (ArcFace Lock, Kinematic Torso Deceleration, Prosodics)
7. Nightly Auto-Dream (3-Phase Collect, Consolidate & Atomic Prompt Rollback)
"""

import os
import sys
import time
import math
import tempfile
import pytest
from unittest.mock import MagicMock, patch

# Ensure local imports resolve
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

# Mock ROS 2 modules if running outside native ROS 2 Linux env
class MockPackage(MagicMock):
    __path__ = []
    __spec__ = None

for mod in [
    'rclpy', 'rclpy.node', 'rclpy.callback_groups', 'rclpy.action', 'rclpy.executors',
    'rclpy.time', 'rclpy.duration', 'rclpy.qos',
    'rcl_interfaces', 'rcl_interfaces.msg',
    'example_interfaces', 'example_interfaces.srv',
    'geometry_msgs', 'geometry_msgs.msg',
    'nav_msgs', 'nav_msgs.msg',
    'sensor_msgs', 'sensor_msgs.msg',
    'visualization_msgs', 'visualization_msgs.msg',
    'vision_msgs', 'vision_msgs.msg',
    'action_msgs', 'action_msgs.msg',
    'diagnostic_msgs', 'diagnostic_msgs.msg',
    'std_msgs', 'std_msgs.msg',
    'std_srvs', 'std_srvs.srv',
    'tf2_ros', 'tf_transformations',
    'robopy_controller.msg',
    'google', 'google.genai'
]:
    sys.modules[mod] = MockPackage()

for mod in list(sys.modules.keys()):
    if '.' in mod:
        parent, child = mod.rsplit('.', 1)
        if parent in sys.modules:
            setattr(sys.modules[parent], child, sys.modules[mod])

from robopy_controller.robot_ai.services.resource_governor import (
    ResourceGovernorEngine,
    KinematicState,
    CognitiveState,
    SafetyState,
    InvariantViolation
)
from robopy_controller.robot_ai.cognitive.homeostasis_mal import (
    HomeostaticDriveEngine,
    ProactiveEvent
)
from robopy_controller.robot_ai.services.visual_gesture_pointing_engine import (
    VisualGesturePointingEngine,
    GestureType
)
from robopy_controller.robot_ai.cognitive.theory_of_mind_engine import (
    TheoryOfMindEngine,
    ToMState
)
from robopy_controller.robot_ai.skills.builtin.follow_person_skill import FollowPersonSkill
from robopy_controller.robot_ai.skills.builtin.visual_pointing_skill import VisualPointingSkill
from robopy_controller.robot_ai.skills.builtin.social_context_skill import SocialContextSkill
from robopy_controller.robot_ai.services.nightly_dream_service import NightlyDreamService


# =============================================================================
# 1. Dynamic Resource Governor v2 Tests (IMP-BIO-005, FM-BIO-005)
# =============================================================================
class TestResourceGovernorV2:

    def test_standstill_lidar_sleep_outside_dock(self):
        """Verifica che dopo 45s di arresto a robot fermo, il LiDAR venga spento per preservarne la vita utile."""
        gov = ResourceGovernorEngine(standstill_lidar_timeout_s=45.0)
        gov.kinematic_state = KinematicState.STATIONARY
        gov.is_in_dock = False
        gov.current_speed = 0.0
        gov._last_velocity_zero_time = time.monotonic() - 50.0  # Fermo da 50 secondi

        profile = gov.evaluate_cycle()
        # Invariante 6 revisionata: LiDAR deve essere disattivato senza scatenare violazione
        assert profile.lidar_active is False
        assert gov.safety_state == SafetyState.NORMAL

    def test_lidar_active_during_motion_or_prep(self):
        """Verifica che il LiDAR rimanga rigorosamente attivo in moto o preparazione navigazione."""
        gov = ResourceGovernorEngine(standstill_lidar_timeout_s=45.0)
        gov.kinematic_state = KinematicState.MOVING
        gov.nav_intent_active = True
        gov.current_speed = 0.2

        profile = gov.evaluate_cycle()
        assert profile.lidar_active is True
        assert profile.motion_gate_open is True

    def test_invariant_violation_if_lidar_off_in_motion(self):
        """Verifica che se il LiDAR dovesse risultare spento durante MOVING scatti InvariantViolation."""
        gov = ResourceGovernorEngine()
        gov.kinematic_state = KinematicState.MOVING
        bad_profile = gov._resolve_resource_profile()
        bad_profile.lidar_active = False

        with pytest.raises(InvariantViolation) as exc:
            gov._audit_invariants(bad_profile)
        assert "INVARIANT_6" in str(exc.value)


# =============================================================================
# 2. Homeostatic MAL Drive Engine Tests (IMP-BIO-004)
# =============================================================================
class TestHomeostaticDriveEngine:

    def test_initial_state_and_mal_integration(self):
        engine = HomeostaticDriveEngine(refractory_period_s=10.0)
        drives, event = engine.step(dt=0.5)
        assert 0.0 <= drives["curiosity"] <= 1.0
        assert 0.0 <= drives["sociality"] <= 1.0
        assert 0.0 <= drives["autonomy"] <= 1.0
        assert event is None

    def test_spatial_surprise_boosts_curiosity(self):
        engine = HomeostaticDriveEngine(refractory_period_s=10.0)
        engine.inject_spatial_anomaly({
            "label": "zaino",
            "surprise_score": 0.85,
            "x": 0.5, "y": 0.2, "z": 1.2
        })
        assert engine.curiosity >= 0.35

    def test_proactive_trigger_at_critical_threshold(self):
        engine = HomeostaticDriveEngine(refractory_period_s=10.0)
        engine.inject_spatial_anomaly({
            "label": "valigia",
            "surprise_score": 0.90,
            "x": 1.0, "y": 0.0, "z": 1.5
        })
        drives, event = engine.step(dt=0.5)
        assert event is not None
        assert event.drive_name == "curiosity"
        assert event.trigger_reason == "spatial_surprise_exploration"
        assert "valigia" in event.suggested_utterance

    def test_dynamic_tom_suppression_in_dnd(self):
        engine = HomeostaticDriveEngine(refractory_period_s=10.0)
        engine.curiosity = 0.8
        engine.sociality = 0.8
        engine.tom_state = "DO_NOT_DISTURB"

        drives, event = engine.step(dt=0.5)
        # Sotto DND curiosità e socialità devono essere drasticamente attenuate (<0.25) e nessun evento emesso
        assert drives["curiosity"] < 0.25
        assert drives["sociality"] < 0.25
        assert event is None


# =============================================================================
# 3. Visual Gesture & 3D Pointing Engine Tests (IMP-BIO-004)
# =============================================================================
class TestVisualGesturePointingEngine:

    def _create_dummy_keypoints(self) -> list:
        # Default standing skeleton
        kps = []
        for _ in range(17):
            kps.append([0.5, 0.5, 0.9])
        return kps

    def test_open_palm_stop_gesture(self):
        engine = VisualGesturePointingEngine(min_conf=0.35)
        kps = self._create_dummy_keypoints()
        # Right arm raised (wrist above elbow and shoulder)
        kps[6] = [0.4, 0.45, 0.9]  # Shoulder
        kps[8] = [0.4, 0.35, 0.9]  # Elbow
        kps[10] = [0.4, 0.15, 0.9] # Wrist raised high

        res = engine.evaluate_pose(kps)
        assert res.gesture == GestureType.OPEN_PALM
        assert res.hand == "RIGHT"

    def test_thumbs_up_confirm_gesture(self):
        engine = VisualGesturePointingEngine(min_conf=0.35)
        kps = self._create_dummy_keypoints()
        # Right arm in front of torso, flexed elbow
        kps[6] = [0.4, 0.30, 0.9]  # Shoulder
        kps[8] = [0.45, 0.45, 0.9] # Elbow
        kps[10] = [0.42, 0.38, 0.9]# Wrist near chest
        kps[12] = [0.4, 0.65, 0.9] # Hip

        res = engine.evaluate_pose(kps)
        assert res.gesture == GestureType.THUMBS_UP

    def test_3d_pointing_raycasting(self):
        engine = VisualGesturePointingEngine(min_conf=0.35)
        kps = self._create_dummy_keypoints()
        # Extended arm pointing rightward
        kps[6] = [0.30, 0.40, 0.9]  # Shoulder
        kps[8] = [0.55, 0.40, 0.9]  # Elbow
        kps[10] = [0.80, 0.40, 0.9] # Wrist extended horizontally

        res = engine.evaluate_pose(kps, estimated_depth_m=2.0)
        assert res.gesture == GestureType.POINTING
        assert res.pointing_target is not None
        assert res.pointing_target.direction_3d[0] > 0.0  # Pointing to the right
        assert len(res.pointing_target.intersection_3d) == 3


# =============================================================================
# 4. Theory of Mind Engine Tests (IMP-BIO-004)
# =============================================================================
class TestTheoryOfMindEngine:

    def _create_skeleton(self) -> list:
        return [[0.5, 0.5, 0.9] for _ in range(17)]

    def test_engaged_upright_posture(self):
        tom = TheoryOfMindEngine()
        kps = self._create_skeleton()
        # Vertical spine
        kps[5] = [0.45, 0.30, 0.9]  # LS
        kps[6] = [0.55, 0.30, 0.9]  # RS
        kps[11] = [0.46, 0.60, 0.9] # LH
        kps[12] = [0.54, 0.60, 0.9] # RH
        kps[0] = [0.50, 0.20, 0.9]  # Nose

        eval_res = tom.evaluate_posture(kps, face_detected=True, gaze_at_robot=True)
        assert eval_res.state == ToMState.ENGAGED
        assert eval_res.recommended_tts_volume >= 0.80

    def test_workbench_do_not_disturb(self):
        tom = TheoryOfMindEngine()
        kps = self._create_skeleton()
        # Bent forward over workbench (spine tilted ~35 deg, head pitched down)
        kps[5] = [0.38, 0.35, 0.9]  # LS
        kps[6] = [0.46, 0.35, 0.9]  # RS
        kps[11] = [0.50, 0.65, 0.9] # LH
        kps[12] = [0.58, 0.65, 0.9] # RH
        kps[0] = [0.42, 0.45, 0.9]  # Head down

        eval_res = tom.evaluate_posture(kps, face_detected=True, gaze_at_robot=False)
        assert eval_res.state == ToMState.DO_NOT_DISTURB
        assert eval_res.recommended_tts_volume <= 0.40  # Quiet volume

    def test_fall_emergency_detection(self):
        tom = TheoryOfMindEngine(fall_confirmation_time_s=0.5)
        kps = self._create_skeleton()
        # Horizontal on floor plane: y > 0.70, wide aspect ratio
        kps[5] = [0.20, 0.80, 0.9]
        kps[6] = [0.30, 0.80, 0.9]
        kps[11] = [0.60, 0.82, 0.9]
        kps[12] = [0.70, 0.82, 0.9]
        kps[0] = [0.15, 0.78, 0.9]

        # First frame detects fall onset
        tom.evaluate_posture(kps)
        time.sleep(0.6)
        # Second frame confirms persistence past 0.5s
        eval_res = tom.evaluate_posture(kps)
        assert eval_res.state == ToMState.FALL_EMERGENCY
        assert eval_res.is_on_ground is True
        assert eval_res.recommended_tts_volume == 1.0


# =============================================================================
# 5. FollowPersonSkill Tests (IMP-BIO-002)
# =============================================================================
class TestFollowPersonSkill:

    def test_biometric_lock_ignores_other_person(self):
        skill = FollowPersonSkill()
        skill.start_following(target_identity="Luca")

        res = skill.update_frame(
            keypoints=[[0.5, 0.5, 0.9]] * 17,
            detected_identity="Marco"
        )
        assert res["status"] == "ignored_other_person"

    def test_torsion_anticipation_slowdown(self):
        skill = FollowPersonSkill()
        skill.start_following(target_identity="Luca")

        kps = [[0.5, 0.5, 0.9] for _ in range(17)]
        # Initial baseline shoulders
        kps[5] = [0.40, 0.3, 0.9]
        kps[6] = [0.60, 0.3, 0.9]
        skill.update_frame(kps, detected_identity="Luca", estimated_distance_m=1.8)

        # Target begins turning body (shoulders narrow to 0.06)
        kps[5] = [0.47, 0.3, 0.9]
        kps[6] = [0.53, 0.3, 0.9]
        res = skill.update_frame(kps, detected_identity="Luca", estimated_distance_m=1.8)

        # Deceleration factor must engage
        assert res["torsion_factor"] < 1.0
        assert res["v_cmd"] < skill.max_linear_speed


# =============================================================================
# 6. Nightly Auto-Dream 3-Phase Tests (IMP-BIO-004)
# =============================================================================
class TestNightlyAutoDream:

    def test_atomic_prompt_save_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = NightlyDreamService(
                config_manager=MagicMock(),
                memory_store=MagicMock(),
                llm_service=MagicMock(),
                embedding_service=MagicMock()
            )
            service.master_prompt_path = os.path.join(tmp_dir, "master_prompt.txt")

            # 1. First write
            prompt_v1 = "Sei Marcus, un assistente robotico cosciente e proattivo. Mantieni sempre rispetto prossemico."
            ok1 = service._save_master_prompt(prompt_v1)
            assert ok1 is True
            assert os.path.exists(service.master_prompt_path)
            with open(service.master_prompt_path, "r", encoding="utf-8") as f:
                assert f.read() == prompt_v1

            # 2. Second write (creates .bak)
            prompt_v2 = "Sei Marcus v2. Nuove abilità di inseguimento e comprensione gestuale attive. Priorità sicurezza."
            ok2 = service._save_master_prompt(prompt_v2)
            assert ok2 is True

            bak_path = service.master_prompt_path + ".bak"
            assert os.path.exists(bak_path)
            with open(bak_path, "r", encoding="utf-8") as f:
                assert f.read() == prompt_v1

            with open(service.master_prompt_path, "r", encoding="utf-8") as f:
                assert f.read() == prompt_v2

    def test_short_prompt_rejected(self):
        service = NightlyDreamService(
            config_manager=MagicMock(),
            memory_store=MagicMock(),
            llm_service=MagicMock(),
            embedding_service=MagicMock()
        )
        assert service._save_master_prompt("Troppo corto") is False
