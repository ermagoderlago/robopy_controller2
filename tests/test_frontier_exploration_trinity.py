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
from robot_ai.skills.builtin.nomad_exploration_skill import NomadExplorationSkill
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


def test_nomad_exploration_skill_backward_compatibility(mock_ros_node):
    node, pub_enable, pub_target = mock_ros_node
    legacy_skill = NomadExplorationSkill(ros_node=node)

    assert legacy_skill.get_metadata().name == "nomad_exploration"
    assert legacy_skill.match("avvia nomad") > 0.8
    assert legacy_skill.match("esplora la casa") > 0.8
