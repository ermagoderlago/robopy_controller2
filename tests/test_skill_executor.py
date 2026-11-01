"""
Unit Test Suite for SkillExecutor & Action Routing (Milestone 4)
================================================================
Validates that:
1. `SkillExecutor.skill_mapping` properly maps `move_distance`, `rotate_angle`,
   `navigate_to`, and `stop_motion` to `NavigationSkill`.
2. Kinematic Sandbox approved actions are dispatched to NavigationSkill instead
   of failing with "Skill not found".
3. `execute_action()`, `execute_skill()`, and `execute_actions_stream()` work
   end-to-end with genuine arguments.
4. Both import paths (`robopy_controller.trinity.skill_executor` and
   `robopy_controller.robot_ai.orchestration.skill_executor`) are fully functional.
"""

import asyncio
import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for _p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from robopy_controller.trinity.skill_executor import (
    SkillExecutor as TrinitySkillExecutor,
    SkillExecutionResult as TrinitySkillExecutionResult,
)
from robopy_controller.robot_ai.orchestration.skill_executor import (
    SkillExecutor,
    SkillExecutionResult,
)
from robopy_controller.robot_ai.skills.skill_registry import SkillRegistry
from robopy_controller.robot_ai.skills.builtin.navigation_skill import NavigationSkill
from robopy_controller.robot_ai.skills.base_skill import SkillResult


class MockSkill:
    def __init__(self, name: str):
        self.name = name
        self.last_execution_text = None
        self.last_context = None

    async def safe_execute(self, execution_text: str, context: dict):
        self.last_execution_text = execution_text
        self.last_context = context
        return SkillResult(
            success=True,
            message=f"Executed {self.name}",
            speak=f"Confermato {self.name}",
            actions=[{"action": self.name, "context": context}],
        )


@pytest.fixture
def mock_registry():
    registry = MagicMock(spec=SkillRegistry)
    skills = {
        "navigation": MockSkill("navigation"),
        "frontier_exploration": MockSkill("frontier_exploration"),
    }
    registry.get.side_effect = lambda name: skills.get(name)
    return registry, skills


def test_import_compatibility():
    """Verify both trinity.skill_executor and orchestration.skill_executor import cleanly."""
    assert TrinitySkillExecutor is SkillExecutor
    assert TrinitySkillExecutionResult is SkillExecutionResult


def test_skill_mapping_contains_mandatory_actions():
    """Requirement 2: skill_mapping must contain move_distance, rotate_angle, navigate_to, stop_motion."""
    mapping = SkillExecutor.skill_mapping
    assert "move_distance" in mapping, "move_distance missing from skill_mapping"
    assert "rotate_angle" in mapping, "rotate_angle missing from skill_mapping"
    assert "navigate_to" in mapping, "navigate_to missing from skill_mapping"
    assert "stop_motion" in mapping, "stop_motion missing from skill_mapping"

    # Verify they target navigation skill
    assert mapping["move_distance"][0] == "navigation"
    assert mapping["rotate_angle"][0] == "navigation"
    assert mapping["navigate_to"][0] == "navigation"
    assert mapping["stop_motion"][0] == "navigation"


@pytest.mark.asyncio
async def test_execute_action_move_distance_dispatched(mock_registry):
    registry, skills = mock_registry
    executor = SkillExecutor(registry=registry)

    args = {"direction": "avanti", "distance_m": 0.30}
    result = await executor.execute_action("move_distance", args)

    assert result.success is True
    nav_skill = skills["navigation"]
    assert nav_skill.last_context is not None
    assert nav_skill.last_context.get("action") == "move_relative"
    assert nav_skill.last_context.get("direction") == "avanti"
    assert nav_skill.last_context.get("distance_m") == 0.30
    assert "30 centimetri" in nav_skill.last_execution_text


@pytest.mark.asyncio
async def test_execute_action_rotate_angle_dispatched(mock_registry):
    registry, skills = mock_registry
    executor = SkillExecutor(registry=registry)

    args = {"direction": "destra", "degrees": 90.0}
    result = await executor.execute_action("rotate_angle", args)

    assert result.success is True
    nav_skill = skills["navigation"]
    assert nav_skill.last_context is not None
    assert nav_skill.last_context.get("action") == "move_relative"
    assert nav_skill.last_context.get("direction") == "destra"
    assert nav_skill.last_context.get("degrees") == 90.0
    assert "90 gradi" in nav_skill.last_execution_text


@pytest.mark.asyncio
async def test_execute_action_navigate_to_dispatched(mock_registry):
    registry, skills = mock_registry
    executor = SkillExecutor(registry=registry)

    args = {"destination": "cucina"}
    result = await executor.execute_action("navigate_to", args)

    assert result.success is True
    nav_skill = skills["navigation"]
    assert nav_skill.last_context is not None
    assert nav_skill.last_context.get("action") == "goto"
    assert nav_skill.last_context.get("destination") == "cucina"
    assert "vai in cucina" in nav_skill.last_execution_text


@pytest.mark.asyncio
async def test_execute_action_stop_motion_dispatched(mock_registry):
    registry, skills = mock_registry
    executor = SkillExecutor(registry=registry)

    result = await executor.execute_action("stop_motion", {})

    assert result.success is True
    nav_skill = skills["navigation"]
    assert nav_skill.last_context is not None
    assert nav_skill.last_context.get("action") == "stop"
    assert "fermati" in nav_skill.last_execution_text


@pytest.mark.asyncio
async def test_execute_skill_and_stream_with_mapped_actions(mock_registry):
    registry, skills = mock_registry
    executor = SkillExecutor(registry=registry)

    # 1. Test execute_skill
    texts = await executor.execute_skill("move_distance", {"direction": "avanti", "distance_m": 0.30})
    assert len(texts) > 0
    assert "Confermato navigation" in texts[0]

    # 2. Test execute_actions_stream
    actions = [
        {"action_type": "move_distance", "args": {"direction": "avanti", "distance_m": 0.30}},
        {"action_type": "navigate_to", "args": {"destination": "cucina"}},
        {"action_type": "stop_motion", "args": {}},
    ]
    streamed_texts = []
    async for t in executor.execute_actions_stream(actions):
        streamed_texts.append(t)

    assert len(streamed_texts) == 3


@pytest.mark.asyncio
async def test_unknown_skill_returns_clean_error(mock_registry):
    registry, _ = mock_registry
    executor = SkillExecutor(registry=registry)

    result = await executor.execute_action("non_existent_skill_xyz", {})
    assert result.success is False
    assert result.error_code == "SKILL_NOT_FOUND"
    assert "non trovata" in result.message


@pytest.mark.asyncio
async def test_integration_with_real_navigation_skill():
    """Verify execution with real NavigationSkill instance."""
    nav_client = MagicMock()
    nav_client.test_stationary_guard = True
    nav_client.navigate_to_pose = AsyncMock(return_value=True)

    real_nav_skill = NavigationSkill(nav_client=nav_client)
    registry = MagicMock(spec=SkillRegistry)
    registry.get.side_effect = lambda name: real_nav_skill if name == "navigation" else None

    executor = SkillExecutor(registry=registry)

    # 1. navigate_to cucina
    res = await executor.execute_action("navigate_to", {"destination": "cucina"})
    assert res.success is True
    assert "cucina" in (res.speak or res.message)
    nav_client.navigate_to_pose.assert_called_once()

    # 2. move_distance with mock move_handler
    move_handler_called = []
    real_nav_skill.move_handler = lambda direction, speed, duration, degrees, distance_m=None: move_handler_called.append(
        (direction, distance_m)
    )
    res_move = await executor.execute_action("move_distance", {"direction": "avanti", "distance_m": 0.30})
    assert res_move.success is True
    assert len(move_handler_called) == 1
    assert move_handler_called[0] == ("avanti", 0.30)
