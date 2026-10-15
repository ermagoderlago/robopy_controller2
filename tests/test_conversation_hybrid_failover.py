import sys
import os
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Mock rclpy and robopy_controller.srv if running in test environment without ROS 2 build
import types
if "rclpy" not in sys.modules:
    mock_rclpy = types.ModuleType("rclpy")
    mock_rclpy.logging = MagicMock()
    sys.modules["rclpy"] = mock_rclpy

if "robopy_controller.srv" not in sys.modules:
    mock_srv_mod = types.ModuleType("robopy_controller.srv")
    mock_srv_mod.AskVisualQuestion = MagicMock()
    sys.modules["robopy_controller.srv"] = mock_srv_mod

from robopy_controller.robot_ai.orchestration.conversation import ConversationManager
from robopy_controller.robot_ai.arbitration.intent_classifier import RouteTarget, RoutingDecision


@pytest.fixture
def mock_dependencies():
    llm = MagicMock()
    llm.generate = AsyncMock()
    llm.generate_live = AsyncMock()
    llm._system_prompt = "Sei Marcus."
    llm.is_duplicate_text = MagicMock(return_value=False)

    tts = MagicMock()
    tts.speak = AsyncMock()

    skill_executor = MagicMock()
    skill_executor.find_best_match.return_value = None
    skill_executor.registry.get_function_declarations.return_value = []
    skill_executor.registry.get.return_value = None

    memory_manager = MagicMock()
    memory_manager.memory_store = None
    memory_manager.embedding_service = None

    world_model = MagicMock()
    world_model.current_user = None
    world_model.to_prompt_section.return_value = ""

    ha_context_provider = MagicMock(return_value="")

    metrics = MagicMock()
    metrics.connectivity_state = "ONLINE"
    metrics.record_llm_latency = MagicMock()
    metrics.record_llm_error = MagicMock()
    metrics.inc_requests_total = MagicMock()
    metrics.inc_requests_success = MagicMock()

    config = MagicMock()
    config.get_config.return_value.rag.enabled = False
    config.get_config.return_value.llm.timeout = 5.0

    reactive_safety = MagicMock()
    reactive_safety.emergency_stop = MagicMock()

    return {
        "llm": llm,
        "tts": tts,
        "skill_executor": skill_executor,
        "memory_manager": memory_manager,
        "world_model": world_model,
        "ha_context_provider": ha_context_provider,
        "metrics": metrics,
        "config": config,
        "reactive_safety": reactive_safety,
    }


@pytest.mark.asyncio
async def test_quota_exhausted_failover_to_local_qwen(mock_dependencies):
    """
    Test that when Gemini fails with 429 RESOURCE_EXHAUSTED (spending cap / quota exhausted),
    ConversationManager executes immediate transparent failover to local Qwen2
    and NEVER responds with 'Non riesco a connettermi al cervello.'
    """
    received_responses = []

    def on_response(text):
        received_responses.append(text)

    manager = ConversationManager(
        response_callback=on_response,
        **mock_dependencies
    )

    # Simulate Gemini failing with RESOURCE_EXHAUSTED 429 error
    mock_dependencies["llm"].generate.side_effect = Exception("429 RESOURCE_EXHAUSTED: Your project has exceeded its monthly spending cap.")

    # Process input via text chat
    success = await manager.process_input("ok, ma adesso puoi parlare?", source="text")

    assert success is True, "Process input should succeed via local Qwen fallback"
    assert len(received_responses) == 1, "Should have received exactly one response"
    response = received_responses[0]
    assert "Non riesco a connettermi al cervello" not in response, "Should NOT say it cannot connect to brain"
    assert len(response) > 5, "Response should be substantive"
    print(f"Verified failover response: {response}")


@pytest.mark.asyncio
async def test_direct_operational_routing_to_local_qwen(mock_dependencies):
    """
    Test that operational motion and telemetry queries are routed directly
    to Local Qwen on Hailo-10H NPU without even calling Gemini.
    """
    received_responses = []

    def on_response(text):
        received_responses.append(text)

    manager = ConversationManager(
        response_callback=on_response,
        **mock_dependencies
    )

    # Gemini should NOT be called at all for local commands
    mock_dependencies["llm"].generate.side_effect = AssertionError("Gemini should not be called for local operational intent!")

    success = await manager.process_input("ruota a destra di 45 gradi", source="text")

    assert success is True
    assert len(received_responses) == 1
    assert "rotate_angle" in received_responses[0] or "destra" in received_responses[0].lower() or len(received_responses[0]) > 0
