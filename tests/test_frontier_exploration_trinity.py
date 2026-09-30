"""
Unit & Integration Tests for Frontier Exploration & TRINITY Memory Loop
========================================================================
Verifies:
1. FrontierExplorationSkill & NomadExplorationSkill intent matching and execution.
2. HUNT mode vs EXPLORE mode dispatching.
3. CAG Environment real-time telemetry updates.
4. MAG Semantic Landmark persistence upon /exploration/target_event.
5. Target lookup in MAG memory before frontier dispatch.
"""

import json
import pytest
import asyncio
from unittest.mock import MagicMock

from robot_ai.skills.builtin.frontier_exploration_skill import FrontierExplorationSkill
from robot_ai.trinity.cag_environment import EnvironmentSnapshot
from robot_ai.trinity.cag_aggregator import ContextAggregator
from robot_ai.trinity.mag_database import MAGDatabase
from robot_ai.trinity.mag_zettelkasten import SemanticFactStore
from robot_ai.trinity.trinity_engine import TrinityEngine


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_trinity_explore.db"
    db = MAGDatabase(db_path=str(db_file))
    db.initialize()
    return db


@pytest.fixture
def mock_ros_node():
    node = MagicMock()
    # Mock publishers
    pub_enable = MagicMock()
    pub_target = MagicMock()
    node.create_publisher.side_effect = lambda msg_type, topic, qos: (
        pub_enable if "enable" in topic else pub_target
    )
    return node, pub_enable, pub_target


def test_cag_environment_exploration_tracking():
    env = EnvironmentSnapshot()
    assert env.exploration_active is False
    assert env.exploration_mode == "IDLE"
    assert "Exploration" not in env.to_text()

    # Update to EXPLORE mode
    env.update_exploration(active=True, mode="EXPLORE", remaining_frontiers=5)
    assert env.exploration_active is True
    assert env.exploration_mode == "EXPLORE"
    text = env.to_text()
    assert "Exploration: ACTIVE(EXPLORE, frontiers=5)" in text

    # Update to HUNT mode with target
    env.update_exploration(active=True, mode="HUNT", target="persona", remaining_frontiers=2)
    assert env.exploration_target == "persona"
    text_hunt = env.to_text()
    assert "Exploration: HUNT(persona, frontiers=2)" in text_hunt

    # Deactivate
    env.update_exploration(active=False)
    assert env.exploration_active is False
    assert "Exploration" not in env.to_text()


def test_frontier_exploration_skill_intents(mock_ros_node):
    async def _run():
        node, pub_enable, pub_target = mock_ros_node
        skill = FrontierExplorationSkill(ros_node=node)

        # 1. Match exploration
        assert skill.match("Marcus, esplora la casa per favore") > 0.8
        assert skill.match("fai una ricognizione della stanza") > 0.8

        # 2. Match hunt
        assert skill.match("trova Marco") > 0.8
        assert skill.match("cerca le chiavi") > 0.8
        assert skill.match("dov'è la bottiglia?") > 0.8

        # 3. Match stop
        assert skill.match("ferma l'esplorazione") > 0.9
        assert skill.match("basta cercare") > 0.9
        assert skill.match("ferma la navigazione") > 0.9
        assert skill.match("fermati") > 0.9
        assert skill.match("stop") > 0.9
        assert skill.match("blocca tutto") > 0.9

        # 4. Execute Explore
        res_explore = await skill.execute("esplora la casa")
        assert res_explore.success is True
        assert res_explore.data["mode"] == "EXPLORE"
        assert skill.is_exploring is True

        # 5. Execute Stop
        res_stop = await skill.execute("fermati")
        assert res_stop.success is True
        assert res_stop.data["mode"] == "IDLE"
        assert skill.is_exploring is False

    asyncio.run(_run())


def test_frontier_exploration_hunt_with_mag_lookup(mock_ros_node, temp_db):
    async def _run():
        node, pub_enable, pub_target = mock_ros_node
        fact_store = SemanticFactStore(temp_db)

        # Pre-populate MAG with a known location
        fact_store.add_fact(
            fact_text="Bersaglio 'chiavi' individuato a coordinate (2.45, -1.30) nella stanza 'salotto'",
            fact_type="SEMANTIC_LANDMARK",
            confidence=0.95
        )

        trinity_mock = MagicMock()
        trinity_mock.find_target_location.side_effect = lambda t: (
            {"target": "chiavi", "coordinates": (2.45, -1.30), "confidence": 0.95}
            if "chiavi" in t else None
        )

        skill = FrontierExplorationSkill(ros_node=node, trinity_engine=trinity_mock)

        # Search for known target (chiavi)
        res_known = await skill.execute("trova le chiavi")
        assert res_known.success is True
        assert res_known.data["mode"] == "HUNT_KNOWN_LOCATION"
        assert res_known.data["coordinates"] == [2.45, -1.30]
        assert "2.45" in res_known.speak

        # Search for unknown target (gatto) -> falls back to autonomous hunt on frontiers
        res_unknown = await skill.execute("cerca il gatto")
        assert res_unknown.success is True
        assert res_unknown.data["mode"] == "HUNT"
        assert res_unknown.data["target"] == "gatto"
        assert "ricerca attiva" in res_unknown.speak

    asyncio.run(_run())


def test_trinity_engine_exploration_events(temp_db):
    node = MagicMock()
    trinity = TrinityEngine(node=node, db_path=temp_db.db_path)

    # 1. Simulate /frontier_exploration/status reception
    status_msg = MagicMock()
    status_msg.data = json.dumps({
        "timestamp": 123456.0,
        "status": "EXPLORING",
        "is_active": True,
        "cluster_count": 4,
        "mode": "HUNT",
        "search_target": "sedia"
    })
    trinity._on_exploration_status(status_msg)

    assert trinity.cag.environment.exploration_active is True
    assert trinity.cag.environment.exploration_mode == "HUNT"
    assert trinity.cag.environment.exploration_target == "sedia"
    assert trinity.cag.environment.remaining_frontiers == 4

    # 2. Simulate /exploration/target_event (TARGET_ACQUIRED)
    target_event_msg = MagicMock()
    target_event_msg.data = json.dumps({
        "event": "TARGET_ACQUIRED",
        "target": "sedia",
        "target_coordinates": [1.85, 3.20],
        "approach_coordinates": [1.25, 2.80],
        "timestamp": 123457.0
    })
    trinity.cag.environment.room_name = "studio"
    trinity._on_exploration_target_event(target_event_msg)

    # Verify target was recorded in MAG facts
    found = trinity.find_target_location("sedia")
    assert found is not None
    assert found["target"] == "sedia"
    assert found["coordinates"] == (1.85, 3.20)
    assert found["confidence"] >= 0.80


def test_auto_relocalize_charging_guard_and_clearance():
    """Verifica guardie di sicurezza FM-NAV-035 in AutoLocalizerNode."""
    import sys
    import os
    from unittest.mock import MagicMock, patch
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    from auto_relocalize import AutoLocalizerNode
    from sensor_msgs.msg import BatteryState, LaserScan

    with patch.object(AutoLocalizerNode, '__init__', return_value=None):
        node = AutoLocalizerNode()
        node.is_charging = False
        node.latest_battery = None
        node.latest_scan = None
        node.map_grid = MagicMock()
        node.is_empty_map = False
        node.check_only = False
        node.inject_only = False
        node.force_global = False
        node.call_global_localization = MagicMock(return_value=True)
        node.get_logger = MagicMock(return_value=MagicMock())

        # 1. Test Guardia di Carica
        bat_msg = MagicMock(spec=BatteryState)
        bat_msg.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_CHARGING
        bat_msg.voltage = 12.80
        node.battery_callback(bat_msg)
        assert node.is_charging is True

        # In carica: run_routine deve inibire lo spin a 360°
        node.compute_alignment_quality = MagicMock(return_value=(0.20, 0.50, 100))  # disallineato
        with patch.object(node, 'inject_pose_from_file', return_value=True):
            success = node.run_routine()
            assert success is False, "La rotazione a 360° deve essere inibita quando il robot è in carica!"

        # 2. Test Guardia Spazio Libero (Clearance < 0.28m)
        node.is_charging = False
        scan_msg = MagicMock(spec=LaserScan)
        scan_msg.ranges = [0.15, 0.20, 0.22, 1.5, 2.0]  # Ostacolo a 0.20m (< 0.28m)
        node.latest_scan = scan_msg
        with patch.object(node, 'inject_pose_from_file', return_value=True):
            success = node.run_routine()
            assert success is False, "La rotazione a 360° deve essere annullata se c'è un ostacolo a < 0.28m!"

        # 3. Test Allineamento Già Valido (Nessuno spin)
        node.compute_alignment_quality = MagicMock(return_value=(0.75, 0.04, 100))
        with patch.object(node, 'inject_pose_from_file', return_value=True):
            success = node.run_routine()
            assert success is True, "Se già allineato, run_routine deve terminare con successo senza spin!"


