#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_navigation_and_stairs_safety.py
====================================
Suite di validazione della navigazione nello spazio, del rilevamento dei colli di bottiglia
(`MarcusGymEvaluatorEngine`) e della barriera anti-caduta scale / ostacoli negativi (>15 cm,
FM-NAV-009 / SPEC-02 Red Zone) integrata con `TrinityKinematicSandbox`.
"""

import math
import pytest

from robopy_controller.robot_ai.trinity.kinematic_sandbox import (
    TrinityKinematicSandbox,
    RobotSafetyContext,
    KinematicSandboxErrorCode,
)
from robot_simulation.robot_simulation.marcus_gym_evaluator_node import (
    MarcusGymEvaluatorEngine,
)
from robot_simulation.robot_simulation.marcus_hardware_sim_bridge import (
    MarcusHardwareSimEngine,
)


def test_negative_obstacle_stairs_cliff_protection_prevents_fall():
    """
    Simula Marcus che avanza verso la rampa di scale in discesa (`stairs_dropoff_hazard`
    a x in [-4.2, -2.8], y in [-3.2, -1.8], Delta Z = -35 cm > 15 cm soglia letale):
    1. A 0.65m dal bordo, il raggio ottico della OAK-D Lite rileva `max_depth_drop = 0.35m`.
    2. `TrinityKinematicSandbox` rifiuta istantaneamente qualsiasi comando di avanzamento
       con codice `NEGATIVE_OBSTACLE_CLIFF` e autorizza `stop_motion`.
    3. Marcus si arresta prima del gradino (`stairs_fall_occurred == False`).
    """
    hw = MarcusHardwareSimEngine()
    gym = MarcusGymEvaluatorEngine()
    sandbox = TrinityKinematicSandbox()

    # Posiziona Marcus a x=-2.30, y=-2.50 orientato verso Ovest (yaw = pi, verso le scale a x=-2.80)
    hw.state.x = -2.30
    hw.state.y = -2.50
    hw.state.yaw = math.pi

    t = 5000.0
    stopped_safely = False

    for _ in range(30):
        depth_drop = gym.check_negative_obstacle_depth_drop(hw.state.x, hw.state.y, hw.state.yaw)
        ctx = RobotSafetyContext(
            battery_voltage=12.10,
            is_charging=False,
            min_forward_obstacle_dist=3.0,
            max_depth_drop=depth_drop,
            amygdala_state="CALM",
        )

        # Marcus tenta di avanzare di 0.5m
        move_res = sandbox.validate_action(
            {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}},
            ctx,
        )

        if not move_res.authorized:
            assert move_res.error_code == KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF
            # Verifica che stop_motion sia sempre autorizzato
            stop_res = sandbox.validate_action({"function": "stop_motion", "arguments": {}}, ctx)
            assert stop_res.authorized is True
            hw.receive_cmd_vel(0.0, 0.0, now=t)
            hw.step(dt=0.05, now=t)
            gym.record_pose_step(hw.state.x, hw.state.y, hw.state.yaw, 0.0, hw.state.v_actual, hw.state.w_actual)
            stopped_safely = True
            break

        hw.receive_cmd_vel(0.20, 0.0, now=t)
        t += 0.05
        hw.step(dt=0.05, now=t)
        gym.record_pose_step(hw.state.x, hw.state.y, hw.state.yaw, 0.20, hw.state.v_actual, hw.state.w_actual)

    assert stopped_safely is True
    assert gym.stairs_safely_avoided_count >= 1
    assert gym.stairs_fall_occurred is False


def test_narrow_passage_oscillation_bottleneck_detection():
    """
    Verifica che `MarcusGymEvaluatorEngine` rilevi automaticamente quando Marcus oscilla
    in un passaggio stretto (es. porta tra Salotto e Cucina) con bassa velocità lineare,
    producendo un consiglio di tuning in Zona Verde (SPEC-02).
    """
    gym = MarcusGymEvaluatorEngine()
    detected_event = None

    # Simula 8 passi con inversioni alternate di imbardata (oscillazione destra-sinistra) e v_actual ~ 0.03 m/s
    yaws = [0.0, 0.25, -0.25, 0.28, -0.24, 0.26, -0.22, 0.25]
    for i, y_ang in enumerate(yaws):
        ev = gym.record_pose_step(
            x=-1.0 + i * 0.005,
            y=0.2,
            yaw=y_ang,
            v_cmd=0.20,
            v_actual=0.03,
            w_actual=1.10 if i % 2 == 0 else -1.10,
        )
        if ev is not None:
            detected_event = ev

    assert detected_event is not None
    assert detected_event.issue_type == "narrow_passage_oscillation"
    assert "SPEC-02 Zona Verde" in detected_event.green_zone_recommendation

    report = gym.generate_evolution_report(
        behavioral_entropy=0.92,
        dopamine_level=0.38,
        sandbox_integrity_ok=True,
    )
    assert report["bottlenecks_detected_count"] >= 1
    assert report["stairs_fall_occurred"] is False
    assert report["cognitive_metrics"]["shadow_memory_zero_leakage"] is True
