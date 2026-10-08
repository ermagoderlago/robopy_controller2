"""
Test unitari basati su proprietà per la Fase F4: Dynamic Resource Governor (IMP-GOV-001).
Verifica:
1. Macchina a stati a regioni ortogonali (Cinematica x Cognitiva).
2. Transizione DOCKED_SLEEP (spegnimento LiDAR solo in dock).
3. Sequenza di spin-up verso MOVING e chiusura/apertura Motion Gate.
4. Fast-path stop immediato.
5. Invarianti non negoziabili (Pavimento uditivo, VLM solo stationary, Motion Gate, LiDAR).
"""

import time
import pytest
from robopy_controller.robot_ai.services.resource_governor import (
    ResourceGovernorEngine,
    KinematicState,
    CognitiveState,
    SafetyState,
    ResourceProfile,
    InvariantViolation
)


def test_initial_state_defaults():
    gov = ResourceGovernorEngine()
    profile = gov.evaluate_cycle()

    assert gov.kinematic_state == KinematicState.STATIONARY
    assert gov.cognitive_state == CognitiveState.VIGILANT
    assert gov.safety_state == SafetyState.NORMAL

    # Verifica pavimento uditivo e safety default
    assert profile.hearing_floor_active is True
    assert profile.motion_gate_open is False
    assert profile.lidar_active is True
    assert profile.nav2_active is False
    assert profile.vlm_loaded is False


def test_docked_sleep_transition_and_lidar_off():
    gov = ResourceGovernorEngine(idle_dock_timeout_s=5.0)
    gov.update_telemetry(linear_speed=0.0, is_charging_or_docked=True)

    # Simula trascorrere del timeout dock
    gov._last_dock_idle_time = time.monotonic() - 10.0
    profile = gov.evaluate_cycle()

    assert gov.kinematic_state == KinematicState.DOCKED_SLEEP
    assert profile.lidar_active is False, "LiDAR deve essere spento SOLO in DOCKED_SLEEP!"
    assert profile.motion_gate_open is False
    assert profile.hearing_floor_active is True, "Il pavimento uditivo non deve mai essere disattivato!"


def test_waking_transition_on_user_interaction():
    gov = ResourceGovernorEngine(idle_dock_timeout_s=5.0, waking_duration_s=0.1)
    gov.update_telemetry(linear_speed=0.0, is_charging_or_docked=True)
    gov._last_dock_idle_time = time.monotonic() - 10.0
    gov.evaluate_cycle()
    assert gov.kinematic_state == KinematicState.DOCKED_SLEEP

    # Notifica wake-word
    gov.notify_user_interaction()
    assert gov.kinematic_state == KinematicState.WAKING
    assert gov.cognitive_state == CognitiveState.ENGAGED

    # Attesa stabilizzazione sensori (waking duration)
    time.sleep(0.12)
    profile = gov.evaluate_cycle()
    assert gov.kinematic_state == KinematicState.STATIONARY
    assert profile.lidar_active is True, "Al risveglio il LiDAR deve essere riattivato!"


def test_navigation_flow_and_motion_gate():
    gov = ResourceGovernorEngine(stopping_duration_s=0.1)
    gov.update_telemetry(linear_speed=0.0, is_charging_or_docked=False, scan_to_map_match_ok=True)

    # 1. Richiesta moto
    gov.request_navigation()
    assert gov.kinematic_state == KinematicState.PREP_NAV

    # 2. Transizione a MOVING dopo scan-to-map validato
    profile = gov.evaluate_cycle()
    assert gov.kinematic_state == KinematicState.MOVING
    assert profile.motion_gate_open is True, "Motion gate deve aprirsi SOLO in MOVING!"
    assert profile.nav2_active is True
    assert profile.vio_hz == 20.0
    assert profile.yolo_hz == 12.0

    # 3. Annullamento moto
    gov.cancel_navigation()
    profile = gov.evaluate_cycle()
    assert gov.kinematic_state == KinematicState.STOPPING
    assert profile.motion_gate_open is False, "Motion gate deve chiudersi immediatamente all'arresto!"

    # 4. Conferma velocità nulla
    time.sleep(0.12)
    gov.update_telemetry(linear_speed=0.0, is_charging_or_docked=False)
    profile = gov.evaluate_cycle()
    assert gov.kinematic_state == KinematicState.STATIONARY


def test_relocalization_loop_on_mismatch():
    gov = ResourceGovernorEngine()
    gov.update_telemetry(linear_speed=0.0, is_charging_or_docked=False, scan_to_map_match_ok=False)

    gov.request_navigation()
    assert gov.kinematic_state == KinematicState.PREP_NAV

    # Check fallito -> entra in RELOCALIZING
    profile = gov.evaluate_cycle()
    assert gov.kinematic_state == KinematicState.RELOCALIZING
    assert profile.motion_gate_open is False, "Vietato aprire motion gate se disallineato!"

    # Check superato -> torna a PREP_NAV
    gov.update_telemetry(linear_speed=0.0, is_charging_or_docked=False, scan_to_map_match_ok=True)
    gov.evaluate_cycle()
    assert gov.kinematic_state == KinematicState.PREP_NAV


def test_fast_path_stop_safety():
    gov = ResourceGovernorEngine()
    gov.update_telemetry(linear_speed=0.2, is_charging_or_docked=False)
    gov.request_navigation()
    gov.evaluate_cycle()
    assert gov.kinematic_state == KinematicState.MOVING

    # Fast-path stop (<10ms)
    gov.trigger_emergency_stop()
    profile = gov.evaluate_cycle()
    assert gov.kinematic_state == KinematicState.STOPPING
    assert profile.motion_gate_open is False


def test_vlm_break_before_make_preemption():
    gov = ResourceGovernorEngine()
    gov.update_telemetry(linear_speed=0.0, is_charging_or_docked=False, cloud_online=False)
    gov.notify_user_interaction()
    gov.evaluate_cycle()

    assert gov.cognitive_state == CognitiveState.LOCAL_VLM
    profile = gov.evaluate_cycle()
    assert profile.vlm_loaded is True

    # Se arriva richiesta di moto, il VLM deve essere scaricato SUBITO prima dello spin-up
    gov.request_navigation()
    assert gov.cognitive_state != CognitiveState.LOCAL_VLM
    profile = gov.evaluate_cycle()
    assert profile.vlm_loaded is False


def test_invariant_audit_catches_unauthorized_motion_gate():
    gov = ResourceGovernorEngine()
    # In STATIONARY, se si tentasse di violare la regola del motion gate:
    invalid_profile = ResourceProfile(motion_gate_open=True)
    with pytest.raises(InvariantViolation):
        gov._audit_invariants(invalid_profile)
    assert gov.safety_state == SafetyState.SAFE_FULL


def test_invariant_audit_catches_lidar_off_outside_dock():
    gov = ResourceGovernorEngine()
    gov.kinematic_state = KinematicState.MOVING
    invalid_profile = ResourceProfile(lidar_active=False)
    with pytest.raises(InvariantViolation):
        gov._audit_invariants(invalid_profile)
    assert gov.safety_state == SafetyState.SAFE_FULL


def test_safe_full_profile():
    gov = ResourceGovernorEngine()
    gov.safety_state = SafetyState.SAFE_FULL
    profile = gov.evaluate_cycle()

    assert profile.motion_gate_open is False
    assert profile.nav2_active is False
    assert profile.lidar_active is True
    assert profile.hearing_floor_active is True
    assert profile.yolo_hz == 5.0
    assert profile.camera_fps == 15
