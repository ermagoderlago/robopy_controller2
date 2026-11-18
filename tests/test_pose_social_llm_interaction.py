#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Suite: Embodied Pose + 3D Box + Map + Local Qwen Social Interaction
========================================================================
Verifica:
1. Rilevamento saluto WAVE_HELLO + fusione bbox vicino alla mano + minima energia MICRO_PRESENCE + Qwen 2.5
2. Rilevamento postura inginocchiata KNEELING_EYE_LEVEL + avvicinamento sicuro a 0.50 m
3. Following polimorfico: companion vs curious_observer con auto-stop temporizzato
4. Isteresi neuro-sociale: dialogo positivo (social_momentum) vs zittimento -> arretramento 0.25m + ritorno cuccia
5. Budget rigido SLMMetapromptFusion (<1150 token) con PoseScene iniettato in CAG
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import List, Tuple

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from robopy_controller.robot_ai.cognitive.homeostasis_mal import HomeostaticDriveEngine
from robopy_controller.robot_ai.cognitive.spatial_postural_scene import (
    DetectedBoxObject,
    MetabolicEffortTier,
    SpatialPosturalSceneBuilder,
)
from robopy_controller.robot_ai.cognitive.theory_of_mind_engine import TheoryOfMindEngine, ToMState
from robopy_controller.robot_ai.hailo.hailo_qwen_client import (
    HailoQwenClient,
    HailoQwenConfig,
)
from robopy_controller.robot_ai.orchestration.dialogue_arbiter import DialogueArbiter
from robopy_controller.robot_ai.services.visual_gesture_pointing_engine import (
    GestureType,
    VisualGesturePointingEngine,
)
from robopy_controller.robot_ai.skills.builtin.embodied_social_interaction_skill import (
    EmbodiedSocialInteractionSkill,
)
from robopy_controller.robot_ai.skills.builtin.follow_person_skill import FollowPersonSkill
from robopy_controller.robot_ai.trinity.cag_environment import EnvironmentSnapshot
from robopy_controller.robot_ai.trinity.kinematic_sandbox import (
    RobotSafetyContext,
    TrinityKinematicSandbox,
)
from robopy_controller.robot_ai.trinity.slm_metaprompt_fusion import (
    SLMMetapromptFusion,
    SLMPromptContext,
)


def _base_skeleton() -> List[Tuple[float, float, float]]:
    kps = [(0.50, 0.50, 0.90) for _ in range(17)]
    kps[0] = (0.50, 0.20, 0.92)   # Nose
    kps[5] = (0.44, 0.32, 0.92)   # Left Shoulder
    kps[6] = (0.56, 0.32, 0.92)   # Right Shoulder
    kps[11] = (0.45, 0.60, 0.92)  # Left Hip
    kps[12] = (0.55, 0.60, 0.92)  # Right Hip
    return kps


def _mock_qwen_client() -> HailoQwenClient:
    cfg = HailoQwenConfig(
        enable_mock_fallback=True,
        mock_ttft_ms=5.0,
        mock_inter_token_ms=0.1,
    )
    return HailoQwenClient(config=cfg)


def test_wave_hello_dynamic_and_minimal_energy_qwen():
    gesture_engine = VisualGesturePointingEngine(min_conf=0.35)
    gesture_engine.wave_debounce_sec = 0.0

    # Sequenza dinamica di 3 frame della mano destra che saluta
    kps = _base_skeleton()
    kps[6] = (0.56, 0.34, 0.95)  # Right shoulder
    kps[8] = (0.64, 0.28, 0.95)  # Right elbow
    for wx in (0.60, 0.68, 0.61):
        kps[10] = (wx, 0.16, 0.95)
        g_res = gesture_engine.evaluate_pose(kps, estimated_depth_m=1.40)

    assert g_res.gesture == GestureType.WAVE_HELLO

    # Fonde posa + oggetto 'tazza' vicino alla mano + contesto mappa
    builder = SpatialPosturalSceneBuilder()
    tazza_box = DetectedBoxObject(
        label="tazza",
        bbox_2d=(0.55, 0.12, 0.66, 0.24),
        centroid_3d=(0.18, -0.35, 1.30),
        confidence=0.91,
    )
    scene = builder.build_scene(
        keypoints=kps,
        detected_objects=[tazza_box],
        person_name="Luca",
        person_distance_m=1.40,
        tom_state=ToMState.ENGAGED.value,
        gesture=g_res.gesture.value,
        gaze_at_robot=True,
        map_room="Salotto",
    )

    assert "tazza" in scene.near_hand_objects
    assert scene.recommended_effort_tier == MetabolicEffortTier.MICRO_PRESENCE

    skill = EmbodiedSocialInteractionSkill(
        qwen_client=_mock_qwen_client(),
        sandbox=TrinityKinematicSandbox(),
        drive_engine=HomeostaticDriveEngine(),
    )
    result = asyncio.run(
        skill.deliberate_and_execute_with_qwen(
            scene=scene,
            user_text="Ciao Marcus!",
            safety_ctx=RobotSafetyContext(min_forward_obstacle_dist=1.40),
        )
    )

    assert result.success is True
    assert result.data["effort_tier"] == MetabolicEffortTier.MICRO_PRESENCE.value
    assert result.actions == []  # Zero moto ruote: risparmio energetico massimo
    assert "tazza" in (result.speak or "").lower()
    assert result.data["tail_freq_hz"] > 0.0


def test_kneeling_eye_level_approach_to_0_50m():
    tom_engine = TheoryOfMindEngine()
    kps = _base_skeleton()
    # Busto eretto ma quota abbassata all'altezza della camera di Marcus (avg_body_y = 0.59)
    kps[0] = (0.50, 0.36, 0.95)
    kps[5] = (0.45, 0.46, 0.95)
    kps[6] = (0.55, 0.46, 0.95)
    kps[11] = (0.46, 0.72, 0.95)
    kps[12] = (0.54, 0.72, 0.95)

    tom_eval = tom_engine.evaluate_posture(
        kps, face_detected=True, gaze_at_robot=True, depth_m=1.30
    )
    assert tom_eval.state == ToMState.KNEELING_EYE_LEVEL
    assert tom_eval.target_proxemic_distance_m == 0.50
    assert tom_eval.recommended_tts_volume <= 0.70

    scene = SpatialPosturalSceneBuilder().build_scene(
        keypoints=kps,
        person_name="Luca",
        person_distance_m=1.30,
        tom_state=tom_eval.state.value,
        gesture=GestureType.NONE.value,
        gaze_at_robot=True,
        map_room="Salotto",
        target_proxemic_distance_m=tom_eval.target_proxemic_distance_m,
    )
    assert scene.recommended_effort_tier == MetabolicEffortTier.PROXEMIC_STEP

    skill = EmbodiedSocialInteractionSkill(
        qwen_client=_mock_qwen_client(),
        sandbox=TrinityKinematicSandbox(),
        drive_engine=HomeostaticDriveEngine(),
    )
    result = asyncio.run(
        skill.deliberate_and_execute_with_qwen(
            scene=scene,
            user_text="",
            safety_ctx=RobotSafetyContext(min_forward_obstacle_dist=1.30),
        )
    )

    assert result.success is True
    assert len(result.actions) == 1
    act = result.actions[0]
    assert act["function_name"] == "move_distance"
    assert act["arguments"]["direction"] == "avanti"
    # 1.30 m - 0.50 m = 0.80 m -> si ferma esattamente a 0.50 m dall'utente inginocchiato
    assert abs(act["arguments"]["distance_m"] - 0.80) < 1e-6


def test_polymorphic_follow_companion_vs_curious_auto_stop():
    ros_bridge = type(
        "DummyRosBridge",
        (),
        {"last_cmd": (0.0, 0.0), "publish_cmd_vel": lambda self, v, w: setattr(self, "last_cmd", (v, w))},
    )()
    follow_skill = FollowPersonSkill(ros_bridge=ros_bridge)
    kps = _base_skeleton()

    # Modalità companion su comando
    assert follow_skill.start_following(target_identity="Luca", mode="companion") is True
    step_comp = follow_skill.update_frame(kps, detected_identity="Luca", estimated_distance_m=1.80)
    assert step_comp["status"] == "following"
    assert step_comp["mode"] == "companion"
    assert step_comp["nominal_distance_m"] == 1.20
    assert step_comp["v_cmd"] > 0.0

    # Modalità curious_observer con durata limitata -> auto-stop dopo scadenza
    assert (
        follow_skill.start_following(
            target_identity="Luca",
            mode="curious_observer",
            max_duration_s=0.05,
        )
        is True
    )
    step_cur = follow_skill.update_frame(kps, detected_identity="Luca", estimated_distance_m=2.00)
    assert step_cur["status"] == "following"
    assert step_cur["nominal_distance_m"] == 1.40
    time.sleep(0.08)
    step_stopped = follow_skill.update_frame(kps, detected_identity="Luca", estimated_distance_m=2.00)
    assert step_stopped["status"] == "curiosity_timeout_stopped"
    assert follow_skill.is_following is False


def test_neuro_social_momentum_vs_shush_two_step_retreat_to_dock():
    drives = HomeostaticDriveEngine(refractory_period_s=0.0)
    arbiter = DialogueArbiter()
    follow_skill = FollowPersonSkill()
    follow_skill.start_following(target_identity="Luca", mode="companion")

    # Dialogo positivo aumenta desiderio sociale (social_momentum)
    drives.notify_positive_dialogue(intensity=0.35)
    assert drives.social_momentum >= 0.35
    assert drives.social_inhibition == 0.0

    scene = SpatialPosturalSceneBuilder().build_scene(
        keypoints=_base_skeleton(),
        detected_objects=[DetectedBoxObject(label="libro", centroid_3d=(0.1, 0.0, 1.1))],
        person_name="Luca",
        person_distance_m=1.10,
        tom_state=ToMState.ENGAGED.value,
        gesture=GestureType.NONE.value,
        gaze_at_robot=True,
        map_room="Salotto",
        social_momentum=drives.social_momentum,
        social_inhibition=drives.social_inhibition,
    )

    skill = EmbodiedSocialInteractionSkill(
        qwen_client=_mock_qwen_client(),
        sandbox=TrinityKinematicSandbox(),
        drive_engine=drives,
        follow_skill=follow_skill,
        dialogue_arbiter=arbiter,
    )

    # Utente zittisce ripetutamente Marcus / lo manda in cuccia
    res = asyncio.run(
        skill.deliberate_and_execute_with_qwen(
            scene=scene,
            user_text="Basta stai zitto, vai a cuccia!",
            safety_ctx=RobotSafetyContext(
                battery_voltage=11.8,
                is_charging=False,
                min_forward_obstacle_dist=1.10,
                min_rear_obstacle_dist=1.50,
            ),
        )
    )

    assert res.success is True
    assert res.data["strategy"] == "inhibited_retreat_to_dock"
    assert res.data["social_inhibition"] >= 0.45
    assert res.data["tail_angle_deg"] < 0.0
    assert follow_skill.is_following is False

    # Doppia azione: 1) passo indietro 0.25m, 2) ritorno alla base ('base')
    assert len(res.actions) == 2
    assert res.actions[0]["function_name"] == "move_distance"
    assert res.actions[0]["arguments"]["direction"] == "indietro"
    assert res.actions[0]["arguments"]["distance_m"] == 0.25
    assert res.actions[1]["function_name"] == "navigate_to"
    assert res.actions[1]["arguments"]["destination"] == "base"

    # DialogueArbiter sopprime annunci proattivi non urgenti finché social_inhibition >= 0.50
    drives.notify_user_shush(severity=0.40)
    arbiter.record_user_shush(inhibition=drives.social_inhibition, momentum=drives.social_momentum)
    arbiter.enqueue_proactive("curiosity", "Hai visto questo libro?", requires_presence=False)
    assert arbiter.pop_ready_proactive() is None

    # Fallback sicuro se c'è un muro dietro: salta move_distance indietro ma autorizza navigate_to base
    blocked_rear_plan = skill.plan_interaction(
        scene,
        text="vai a cuccia",
        safety_ctx=RobotSafetyContext(
            battery_voltage=11.8,
            is_charging=False,
            min_forward_obstacle_dist=1.10,
            min_rear_obstacle_dist=0.10,
        ),
    )
    assert blocked_rear_plan is not None
    assert [a["function_name"] for a in blocked_rear_plan.validated_actions] == ["navigate_to"]


def test_slm_metaprompt_fusion_token_budget_with_pose_scene():
    tazza = DetectedBoxObject(
        label="tazza",
        bbox_2d=(0.54, 0.14, 0.65, 0.26),
        centroid_3d=(0.15, -0.25, 1.25),
    )
    scene = SpatialPosturalSceneBuilder().build_scene(
        keypoints=_base_skeleton(),
        detected_objects=[tazza],
        person_name="Luca",
        person_distance_m=1.25,
        tom_state=ToMState.KNEELING_EYE_LEVEL.value,
        gesture=GestureType.WAVE_HELLO.value,
        gaze_at_robot=True,
        map_room="Salotto",
        target_proxemic_distance_m=0.50,
    )
    env = EnvironmentSnapshot()
    env.update_location(room_name="Salotto", location=(1.2, 0.4), map_name="casa")
    env.update_postural_scene(scene.to_compact_cag_string())

    fusion = SLMMetapromptFusion()
    out = fusion.build_prompt_detailed(
        SLMPromptContext(
            user_text="Ciao Marcus, cosa stai guardando vicino alla mia mano?",
            cag_environment=env.to_text(),
            intent_category="SOCIAL",
        )
    )

    assert out.is_within_budget is True
    assert out.token_estimate <= 1150
    assert "PoseScene:" in out.chatml_prompt
    assert "tazza" in out.chatml_prompt
