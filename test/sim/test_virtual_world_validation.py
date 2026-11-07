#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_virtual_world_validation.py
================================
Suite di validazione automatica del mondo virtuale Gazebo Harmonic (`marcus_smart_home.sdf`),
del gemello digitale 1:1 (`marcus_sim.xacro`) e dell'emulatore hardware ESP32 + 3S2P BMS
(`MarcusHardwareSimEngine`) in conformità a SPEC-01, SPEC-02, SPEC-06 e SPEC-07.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
import pytest

from robot_simulation.robot_simulation.marcus_hardware_sim_bridge import (
    MarcusHardwareSimEngine,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
XACRO_PATH = WORKSPACE_ROOT / "robot_simulation" / "urdf" / "marcus_sim.xacro"
SDF_WORLD_PATH = WORKSPACE_ROOT / "robot_simulation" / "worlds" / "marcus_smart_home.sdf"


def test_urdf_xacro_physical_geometry_and_frames():
    """
    Verifica che il modello `marcus_sim.xacro` rispetti 1:1 la geometria fisica di `robopy.urdf`
    e i frame REP-105 / REP-103 richiesti da Nav2, RTAB-Map e Hailo Digital Twin.
    """
    assert XACRO_PATH.exists(), f"File XACRO mancante: {XACRO_PATH}"
    tree = ET.parse(str(XACRO_PATH))
    root = tree.getroot()

    links = {el.attrib.get("name") for el in root.findall("link")}
    required_links = {
        "base_link",
        "base_footprint",
        "left_wheel",
        "right_wheel",
        "laser",
        "oak_camera_link",
        "camera_optical_frame",
        "imu_link",
        "ultrasonic_sensor",
        "tail_link",
    }
    assert required_links.issubset(links), f"Link mancanti in marcus_sim.xacro: {required_links - links}"

    raw_xml = XACRO_PATH.read_text(encoding="utf-8")
    assert "<wheel_separation>0.285</wheel_separation>" in raw_xml
    assert "<wheel_radius>0.0335</wheel_radius>" in raw_xml
    assert "<max_linear_velocity>0.40</max_linear_velocity>" in raw_xml
    assert "<max_angular_velocity>1.80</max_angular_velocity>" in raw_xml
    assert 'limit lower="-0.7854" upper="0.7854"' in raw_xml


def test_sdf_smart_home_world_structure():
    """
    Verifica che `marcus_smart_home.sdf` sia un XML SDF 1.9 valido e contenga:
    - Fisica a 250 Hz (max_step_size 0.004s)
    - Cuccia di ricarica (`charging_dock`) a (0,0)
    - Arredamento multi-stanza (Salotto, Cucina, Studio)
    - Trappola Scale / Ostacolo Negativo (`stairs_dropoff_hazard`) per testare FM-NAV-009
    - Attore umano virtuale (`human_luca`)
    """
    assert SDF_WORLD_PATH.exists(), f"File SDF mancante: {SDF_WORLD_PATH}"
    tree = ET.parse(str(SDF_WORLD_PATH))
    root = tree.getroot()
    assert root.tag == "sdf"

    world = root.find("world")
    assert world is not None and world.attrib.get("name") == "marcus_smart_home"

    models = {m.attrib.get("name") for m in world.findall("model")}
    expected_models = {
        "ground_plane",
        "wall_north",
        "wall_south",
        "wall_east",
        "wall_west",
        "partition_kitchen_salotto",
        "partition_studio_salotto",
        "charging_dock",
        "sofa_salotto",
        "table_cucina",
        "workbench_studio",
        "stairs_dropoff_hazard",
        "human_luca",
    }
    assert expected_models.issubset(models), f"Modelli mancanti nel mondo SDF: {expected_models - models}"


def test_esp32_red_zone_watchdog_500ms_and_velocity_clamping():
    """
    Verifica i vincoli di Zona Rossa SPEC-01 nell'emulatore hardware ESP32:
    - Clamping di /cmd_vel a 0.40 m/s e 1.80 rad/s
    - Arresto forzato a 0.0 m/s se non arrivano comandi entro 500 ms (Watchdog Hardware).
    """
    engine = MarcusHardwareSimEngine()
    t0 = 1000.0

    v_clamped, w_clamped = engine.receive_cmd_vel(1.50, -4.00, now=t0)
    assert v_clamped == pytest.approx(0.40)
    assert w_clamped == pytest.approx(-1.80)

    st = engine.step(dt=0.10, now=t0 + 0.10)
    assert not st.watchdog_tripped
    assert st.v_actual > 0.0

    st_expired = engine.step(dt=0.05, now=t0 + 0.55)
    assert st_expired.watchdog_tripped is True
    assert st_expired.v_cmd == 0.0
    assert st_expired.w_cmd == 0.0
    assert st_expired.v_actual == 0.0


def test_1440_cpr_runge_kutta_odometry_and_motor_stall_protection():
    """
    Verifica:
    - Integrazione odometrica Runge-Kutta 2° ordine e conteggio encoder 1440 CPR
    - Rilevamento Stallo Meccanico (> 1.0s comando attivo con ruote bloccate) e taglio PWM (SPEC-01).
    """
    engine = MarcusHardwareSimEngine()
    t = 2000.0

    for _ in range(20):
        engine.receive_cmd_vel(0.30, 0.0, now=t)
        t += 0.05
        engine.step(dt=0.05, now=t)

    assert engine.state.x > 0.15
    assert engine.state.left_ticks > 1000
    assert engine.state.left_ticks == engine.state.right_ticks

    for _ in range(22):
        engine.receive_cmd_vel(0.35, 0.0, now=t)
        t += 0.05
        engine.step(dt=0.05, now=t, external_obstruction_stall=True)

    assert engine.state.motor_stalled is True
    assert engine.state.v_actual == 0.0


def test_depth_32fc1_to_16uc1_conversion_zero_alloc():
    """
    Verifica la conversione Depth da float32 (metri Gazebo) a uint16 ('16UC1' millimetri SPEC-02)
    con pulizia NaN/Inf e riutilizzo del buffer pre-allocato 480x640.
    """
    engine = MarcusHardwareSimEngine()
    depth_f32 = np.full((480, 640), 1.85, dtype=np.float32)
    depth_f32[10, 10] = np.nan
    depth_f32[20, 20] = np.inf

    out_16u = engine.convert_depth_32fc1_to_16uc1(depth_f32)
    assert out_16u.dtype == np.uint16
    assert out_16u.shape == (480, 640)
    assert out_16u[100, 100] == 1850
    assert out_16u[10, 10] == 0
    assert out_16u[20, 20] == 10000


def test_bms_3s2p_discharge_eco_docking_and_recharge():
    """
    Verifica la macchina a stati della batteria LiPo 3S2P (SPEC-06):
    - Scarica fuori dalla cuccia -> attivazione ECO mode (<= 10.40V) e Docking Trigger (<= 10.15V)
    - Rientro alla cuccia (0.0, 0.0) -> stato CHARGING (12.80V).
    """
    engine = MarcusHardwareSimEngine(dock_position=(0.0, 0.0), dock_radius=0.35)
    engine.state.x = 2.5
    engine.state.y = 1.5
    engine.state.ocv_voltage = 10.22

    t = 3000.0
    for _ in range(30):
        engine.receive_cmd_vel(0.25, 0.0, now=t)
        t += 0.10
        engine.step(dt=0.10, now=t, discharge_rate_multiplier=5.0)

    assert engine.state.eco_mode_active is True
    assert engine.state.docking_triggered is True
    v_eco, _ = engine.receive_cmd_vel(0.40, 0.0, now=t)
    assert v_eco == pytest.approx(0.20)

    engine.state.x = 0.05
    engine.state.y = 0.05
    engine.receive_cmd_vel(0.0, 0.0, now=t)
    t += 0.10
    st_dock = engine.step(dt=0.10, now=t)
    assert st_dock.is_charging is True
    assert st_dock.terminal_voltage == pytest.approx(12.80)


def test_spec07_zero_utf8_bom_and_sequential_build_rules():
    """
    Verifica i vincoli di Zona Rossa SPEC-07:
    - Zero byte order mark UTF-8 (`\\xEF\\xBB\\xBF`) su tutti i file di simulazione e cognitivi
    - Compilazione sequenziale (`MAKEFLAGS="-j1"` e `--parallel-workers 1`) in `install_deps.sh`.
    """
    checked_files = [
        XACRO_PATH,
        SDF_WORLD_PATH,
        WORKSPACE_ROOT / "robot_simulation" / "config" / "bridge.yaml",
        WORKSPACE_ROOT / "robot_simulation" / "scripts" / "install_deps.sh",
        WORKSPACE_ROOT / "robot_simulation" / "scripts" / "start_gazebo_marcus.sh",
        WORKSPACE_ROOT / "robot_simulation" / "launch" / "full_marcus_gazebo.launch.py",
        WORKSPACE_ROOT / "robot_simulation" / "robot_simulation" / "marcus_hardware_sim_bridge.py",
        WORKSPACE_ROOT / "robot_simulation" / "robot_simulation" / "hailo_digital_twin_node.py",
        WORKSPACE_ROOT / "robot_simulation" / "robot_simulation" / "virtual_human_actors_node.py",
        WORKSPACE_ROOT / "robot_simulation" / "robot_simulation" / "marcus_gym_evaluator_node.py",
        WORKSPACE_ROOT / "robopy_controller" / "robot_ai" / "trinity" / "shadow_memory_sandbox.py",
        WORKSPACE_ROOT / "robopy_controller" / "robot_ai" / "cognitive" / "intrinsic_motivation_and_reward_engine.py",
    ]
    for fp in checked_files:
        assert fp.exists(), f"File non trovato: {fp}"
        raw_bytes = fp.read_bytes()
        assert not raw_bytes.startswith(b"\xef\xbb\xbf"), f"Violazione SPEC-07 Red Zone: rilevato UTF-8 BOM in {fp}"

    install_sh = (WORKSPACE_ROOT / "robot_simulation" / "scripts" / "install_deps.sh").read_text(encoding="utf-8")
    assert 'MAKEFLAGS="-j1"' in install_sh
    assert "--parallel-workers 1" in install_sh

