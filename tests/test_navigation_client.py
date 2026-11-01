"""
Unit Test Suite for NavigationClient & Test Stationary Guard (Milestone 4)
==========================================================================
Validates that:
1. `test_stationary_guard` is controlled via `MARCUS_TEST_STATIONARY_GUARD=1` (or ROS param).
2. When active, room navigation targets log `🛑 [TEST STATIONARY GUARD] Nav2 goal suppressed`,
   return success, and suppress Nav2 action goal dispatch for bench safety.
3. `NavigationSkill` emits natural spoken voice confirmation without physical driving.
4. Relative micro-motions (`move_distance: 0.30m`) remain active and are not suppressed.
5. The sub-10ms emergency stop fast-path halts motion immediately.
6. Both import paths (`robopy_controller.navigation.navigation_client` and
   `robopy_controller.robot_ai.integrations.navigation`) are fully functional.
"""

import asyncio
import logging
import os
import sys
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for _p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from robopy_controller.navigation.navigation_client import (
    NavigationClient as ForwardedNavClient,
    NavigationStatus as ForwardedNavStatus,
    Waypoint as ForwardedWaypoint,
)
from robopy_controller.robot_ai.integrations.navigation import (
    NavigationClient,
    NavigationStatus,
    Waypoint,
)
from robopy_controller.robot_ai.skills.builtin.navigation_skill import NavigationSkill


def test_import_compatibility():
    """Verify both navigation.navigation_client and integrations.navigation import cleanly."""
    assert ForwardedNavClient is NavigationClient
    assert ForwardedNavStatus is NavigationStatus
    assert ForwardedWaypoint is Waypoint


def test_stationary_guard_activation_via_environment():
    """Requirement 3: test_stationary_guard controlled via MARCUS_TEST_STATIONARY_GUARD."""
    with patch.dict(os.environ, {"MARCUS_TEST_STATIONARY_GUARD": "1"}):
        client_active = NavigationClient()
        assert client_active.test_stationary_guard is True

    with patch.dict(os.environ, {"MARCUS_TEST_STATIONARY_GUARD": "0"}):
        client_inactive = NavigationClient()
        assert client_inactive.test_stationary_guard is False


def test_stationary_guard_activation_via_ros_parameter():
    """Requirement 3: test_stationary_guard controlled via ROS parameter."""
    mock_node = MagicMock()
    mock_node.has_parameter.return_value = True
    param_mock = MagicMock()
    param_mock.get_parameter_value().bool_value = True
    mock_node.get_parameter.return_value = param_mock

    with patch.dict(os.environ, {"MARCUS_TEST_STATIONARY_GUARD": "0"}):
        client = NavigationClient(node=mock_node)
        assert client.test_stationary_guard is True


@pytest.mark.asyncio
async def test_stationary_guard_suppresses_nav2_goal_and_logs(caplog):
    """
    Requirement 3: When active, for room navigation targets (e.g. "Vai in cucina"),
    log '🛑 [TEST STATIONARY GUARD] Nav2 goal suppressed' and return success.
    """
    client = NavigationClient()
    client.test_stationary_guard = True

    # Mock action client to verify it is NEVER called
    mock_action_client = MagicMock()
    client._action_client = mock_action_client

    with caplog.at_level(logging.WARNING):
        success = await client.navigate_to_pose(2.5, 1.0, 0.0, destination_name="cucina")

    assert success is True
    assert client.status == NavigationStatus.SUCCEEDED
    mock_action_client.send_goal_async.assert_not_called()

    # Verify exact mandatory log string
    assert any("🛑 [TEST STATIONARY GUARD] Nav2 goal suppressed" in record.message for record in caplog.records), \
        "Expected log string '🛑 [TEST STATIONARY GUARD] Nav2 goal suppressed' not found"


@pytest.mark.asyncio
async def test_stationary_guard_with_navigation_skill(caplog):
    """
    Requirement 3: Allow NavigationSkill to emit the natural spoken voice confirmation
    ("Ricevuto! Vado subito verso la cucina") without physically driving off the test bench.
    """
    client = NavigationClient()
    client.test_stationary_guard = True

    skill = NavigationSkill(nav_client=client)

    with caplog.at_level(logging.WARNING):
        result = await skill.execute("Vai in cucina", context={"action": "goto", "destination": "cucina"})

    assert result.success is True
    assert result.speak is not None
    assert "cucina" in result.speak.lower()
    assert any("🛑 [TEST STATIONARY GUARD] Nav2 goal suppressed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_relative_micro_motions_remain_active_under_stationary_guard():
    """
    Requirement 3: Ensure relative micro-motions (move_distance: 0.30m)
    remain active for physical wheel odometry testing (not suppressed).
    """
    client = NavigationClient()
    client.test_stationary_guard = True

    client.motion_manager = MagicMock()
    client.motion_manager.execute_primitive = AsyncMock()

    await client.move_relative(direction="avanti", distance_m=0.30)

    client.motion_manager.execute_primitive.assert_called_once()
    primitive = client.motion_manager.execute_primitive.call_args[0][0]
    assert primitive.direction == "avanti"
    assert primitive.distance_m == 0.30


def test_emergency_stop_fast_path_latency_and_safety():
    """
    Requirement 3: Maintain the sub-10ms emergency stop fast-path.
    """
    mock_pub = MagicMock()
    mock_goal = MagicMock()

    client = NavigationClient(cmd_vel_pub=mock_pub)
    client._current_goal_handle = mock_goal
    client._status = NavigationStatus.NAVIGATING

    t0 = time.perf_counter()
    client.emergency_stop()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Verify latency < 10ms
    assert elapsed_ms < 10.0, f"Emergency stop took {elapsed_ms:.2f}ms, expected < 10ms"

    # Verify status canceled
    assert client.status == NavigationStatus.CANCELED
    assert client._current_goal is None
    assert client._current_goal_handle is None

    # Verify twist zero published
    mock_pub.publish.assert_called_once()
    published_twist = mock_pub.publish.call_args[0][0]
    assert published_twist.linear.x == 0.0
    assert published_twist.angular.z == 0.0
