#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_nav2_deadlock_and_worlds.py
================================
Suite di validazione della risoluzione del deadlock Nav2 (FM-NAV-041) e dei 3
mondi progressivi Gazebo Harmonic:
  - Livello 1 (Semplice):       world_01_simple_room.sdf (6m x 6m)
  - Livello 2 (Più Complicato): world_02_medium_apartment.sdf (10m x 8m, 4 stanze, porte 0.90m)
  - Livello 3 (Complicatissimo): world_03_extreme_labyrinth.sdf (12m x 10m, chicane, U-trap, voragine 3D -45cm)
"""

import asyncio
import math
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from robopy_controller.robot_ai.services.resource_governor import (
    KinematicState,
    ResourceGovernorEngine,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
SIM_DIR = WORKSPACE_ROOT / "robot_simulation"


def test_three_progressive_sdf_worlds_structure_and_3d_stairwell():
    """
    Verifica che tutti e 3 i mondi Gazebo SDF 1.9 siano sintatticamente validi e
    contengano gli elementi fisici progressivi richiesti (inclusa la voragine 3D
    reale a Z < -0.15m per FM-NAV-009 nel Livello 3).
    """
    w1 = SIM_DIR / "worlds" / "world_01_simple_room.sdf"
    w2 = SIM_DIR / "worlds" / "world_02_medium_apartment.sdf"
    w3 = SIM_DIR / "worlds" / "world_03_extreme_labyrinth.sdf"

    for wp in (w1, w2, w3):
        assert wp.exists(), f"Mondo SDF mancante: {wp}"
        root = ET.parse(str(wp)).getroot()
        assert root.tag == "sdf"
        world = root.find("world")
        assert world is not None

    # Livello 1: stanza semplice con casse distanziate e cuccia
    w1_models = {m.attrib.get("name") for m in ET.parse(str(w1)).getroot().find("world").findall("model")}
    assert {"wall_north", "wall_south", "charging_dock", "crate_alpha", "crate_beta"}.issubset(w1_models)

    # Livello 2: appartamento 4 stanze con porte 0.90m, mobili e 2 attori umani
    w2_models = {m.attrib.get("name") for m in ET.parse(str(w2)).getroot().find("world").findall("model")}
    assert {
        "wall_west_div_top",
        "wall_west_div_mid",
        "wall_studio_left",
        "sofa_salotto",
        "table_cucina",
        "workbench_studio",
        "bed_camera",
        "human_luca",
        "human_guest",
    }.issubset(w2_models)

    # Livello 3: labirinto estremo con chicane, U-trap e voragine 3D reale (Z = -0.50m)
    w3_world = ET.parse(str(w3)).getroot().find("world")
    w3_models = {m.attrib.get("name"): m for m in w3_world.findall("model")}
    assert {
        "floor_slab_north",
        "floor_slab_south",
        "floor_slab_west",
        "floor_slab_east",
        "stairwell_sunken_pit",
        "chicane_baffle_1",
        "chicane_baffle_2",
        "utrap_back",
        "utrap_north_arm",
        "utrap_south_arm",
    }.issubset(set(w3_models.keys()))

    pit_pose = [float(v) for v in w3_models["stairwell_sunken_pit"].find("pose").text.split()]
    assert pit_pose[2] <= -0.45, f"La voragine 3D deve essere profonda oltre -15cm (trovato z={pit_pose[2]})"


def test_xacro_gz_frame_ids_and_depth_focal_alignment():
    """
    Verifica che `marcus_sim.xacro` esponga `<gz_frame_id>` espliciti per evitare
    frame Gazebo prefissati non risolti in TF2 e allinei `horizontal_fov` a `fx = width * 0.8`.
    """
    xacro_text = (SIM_DIR / "urdf" / "marcus_sim.xacro").read_text(encoding="utf-8")
    assert "<gz_frame_id>laser</gz_frame_id>" in xacro_text
    assert "<gz_frame_id>camera_optical_frame</gz_frame_id>" in xacro_text
    assert "<gz_frame_id>imu_link</gz_frame_id>" in xacro_text

    # Verifica allineamento focale pinhole per semantic_costmap_injector (fx = 640 * 0.8 = 512.0)
    tree = ET.parse(str(SIM_DIR / "urdf" / "marcus_sim.xacro"))
    hfov_nodes = tree.getroot().findall(".//sensor[@name='oak_rgbd_camera']//horizontal_fov")
    assert len(hfov_nodes) == 1
    hfov = float(hfov_nodes[0].text)
    fx_gz = 640.0 / (2.0 * math.tan(hfov / 2.0))
    assert fx_gz == pytest.approx(512.0, abs=0.5)


def test_nav2_sim_and_slam_configs_comply_with_spec01_spec02():
    """
    Verifica i vincoli SPEC-01 / SPEC-02 in `nav2_sim_params.yaml`, `slam_toolbox_sim.yaml`
    e `nav2_survival_bt.xml`:
      - Nessun layer STVL 3D
      - combination_method == 1 (Maximum) su local_costmap e global_costmap
      - vx_max <= 0.40 m/s e wz_max <= 1.80 rad/s
      - Nessuna rotazione cieca Spin nel BehaviorTree di sopravvivenza
    """
    nav2_cfg = yaml.safe_load((SIM_DIR / "config" / "nav2_sim_params.yaml").read_text(encoding="utf-8"))
    slam_cfg = yaml.safe_load((SIM_DIR / "config" / "slam_toolbox_sim.yaml").read_text(encoding="utf-8"))
    bt_xml = (SIM_DIR / "config" / "nav2_survival_bt.xml").read_text(encoding="utf-8")

    raw_nav2_str = (SIM_DIR / "config" / "nav2_sim_params.yaml").read_text(encoding="utf-8")
    assert "SpatioTemporalVoxelLayer" not in raw_nav2_str
    assert "VoxelLayer" not in raw_nav2_str

    mppi = nav2_cfg["controller_server"]["ros__parameters"]["FollowPath"]
    assert mppi["vx_max"] <= 0.40
    assert mppi["wz_max"] <= 1.80
    assert nav2_cfg["controller_server"]["ros__parameters"]["controller_frequency"] >= 10.0

    local_obs = nav2_cfg["local_costmap"]["local_costmap"]["ros__parameters"]["obstacle_layer"]
    global_obs = nav2_cfg["global_costmap"]["global_costmap"]["ros__parameters"]["obstacle_layer"]
    assert local_obs["combination_method"] == 1
    assert global_obs["combination_method"] == 1
    assert "hailo_semantic_objects" in local_obs["observation_sources"]
    assert "hailo_semantic_objects" in global_obs["observation_sources"]

    # Verifica che PolygonSlowdown usi min_points (e non max_points deprecato in Jazzy)
    poly_slow = nav2_cfg["collision_monitor"]["ros__parameters"]["PolygonSlowdown"]
    assert "min_points" in poly_slow
    assert "max_points" not in poly_slow

    # Verifica slam_toolbox rate /map in [1.0, 2.0]s e risoluzione 0.05m
    slam_p = slam_cfg["slam_toolbox"]["ros__parameters"]
    assert slam_p["resolution"] == pytest.approx(0.05)
    assert 0.5 <= slam_p["map_update_interval"] <= 1.0

    # Verifica assenza Spin cieco in nav2_survival_bt.xml (FM-NAV-019)
    assert "<Spin" not in bt_xml
    assert "<BackUp" in bt_xml


def test_fm_nav_041_governor_wakes_from_standstill_lidar_sleep_on_nav_request():
    """
    Regressione FM-NAV-041:
    Anche quando `ResourceGovernorEngine` ha spento il LiDAR per inattività prolungata
    (FM-BIO-005), una chiamata a `request_navigation()` (scatenata da /plan, /cmd_vel_nav,
    /goal_pose o /robot/nav_intent) DEVE riattivare istantaneamente `lidar_active=True`
    e portare il robot in `MOVING` con `motion_gate_open=True`.
    """
    gov = ResourceGovernorEngine(standstill_lidar_timeout_s=45.0)
    gov.kinematic_state = KinematicState.STATIONARY
    gov.current_speed = 0.0
    gov._last_velocity_zero_time = time.monotonic() - 60.0

    p_sleep = gov.evaluate_cycle()
    assert p_sleep.lidar_active is False
    assert p_sleep.motion_gate_open is False

    # Arriva pianificazione / missione Nav2 -> request_navigation()
    gov.request_navigation()
    assert gov.kinematic_state == KinematicState.PREP_NAV
    p_prep = gov.evaluate_cycle()
    assert gov.kinematic_state == KinematicState.MOVING
    assert p_prep.lidar_active is True
    assert p_prep.motion_gate_open is True


def test_fm_nav_041_governor_preserves_moving_and_motion_gate_during_in_place_spin():
    """
    Regressione Live Gazebo Discovery #1 (FM-NAV-041):
    Durante una rotazione sul posto prolungata (`linear_speed=0.0`, `angular_speed=0.6 rad/s`
    per > 5s), `ResourceGovernorEngine` NON deve scambiare lo spin per `stalled_standstill`
    e NON deve chiudere `/robot/motion_gate`.
    """
    gov = ResourceGovernorEngine()
    gov.request_navigation()
    gov.evaluate_cycle()
    assert gov.kinematic_state == KinematicState.MOVING

    # Simula 10 secondi di rotazione sul posto pura (linear_speed=0.0, angular_speed=0.65)
    gov._last_velocity_zero_time = time.monotonic() - 10.0
    gov.update_telemetry(linear_speed=0.0, is_charging_or_docked=False, angular_speed=0.65)
    p_spin = gov.evaluate_cycle()
    assert gov.kinematic_state == KinematicState.MOVING
    assert p_spin.motion_gate_open is True
    assert p_spin.lidar_active is True


def test_navigation_skill_reports_failure_when_nav_client_rejects_goal():
    """
    Regressione Bug 4 (FM-NAV-041):
    `NavigationSkill._handle_goto` non deve restituire `success_result` se
    `nav_client.navigate_to_pose(...)` restituisce `False` (server non disponibile o goal rifiutato).
    """
    from robopy_controller.robot_ai.skills.builtin.navigation_skill import NavigationSkill

    mock_nav_client = MagicMock()
    mock_nav_client.navigate_to_pose.return_value = False

    skill = NavigationSkill(nav_client=mock_nav_client)
    res = asyncio.run(skill.execute("vai in cucina"))
    assert res.success is False


def test_zero_utf8_bom_on_all_new_simulation_files():
    """Verifica SPEC-07 Zona Rossa: zero BOM UTF-8 e line-ending LF su start_gazebo_marcus.sh."""
    files = [
        SIM_DIR / "worlds" / "world_01_simple_room.sdf",
        SIM_DIR / "worlds" / "world_02_medium_apartment.sdf",
        SIM_DIR / "worlds" / "world_03_extreme_labyrinth.sdf",
        SIM_DIR / "config" / "nav2_sim_params.yaml",
        SIM_DIR / "config" / "slam_toolbox_sim.yaml",
        SIM_DIR / "config" / "nav2_survival_bt.xml",
        SIM_DIR / "launch" / "full_marcus_gazebo.launch.py",
        SIM_DIR / "scripts" / "start_gazebo_marcus.sh",
        SIM_DIR / "scripts" / "run_live_gazebo_nav2_suite.py",
    ]
    for fp in files:
        assert fp.exists(), f"File mancante: {fp}"
        raw = fp.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM rilevato in {fp}!"

    sh_bytes = (SIM_DIR / "scripts" / "start_gazebo_marcus.sh").read_bytes()
    assert b"\r\n" not in sh_bytes, "start_gazebo_marcus.sh contiene CRLF!"
