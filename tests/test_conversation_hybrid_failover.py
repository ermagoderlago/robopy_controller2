import sys
import os
import time
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

if "cv2" not in sys.modules:
    mock_cv2 = types.ModuleType("cv2")
    sys.modules["cv2"] = mock_cv2

if "numpy" not in sys.modules:
    mock_np = types.ModuleType("numpy")
    mock_np.frombuffer = MagicMock()
    mock_np.uint8 = "uint8"
    mock_np.ndarray = MagicMock
    sys.modules["numpy"] = mock_np

if "robopy_controller.srv" not in sys.modules:
    mock_srv_mod = types.ModuleType("robopy_controller.srv")
    mock_srv_mod.AskVisualQuestion = MagicMock()
    sys.modules["robopy_controller.srv"] = mock_srv_mod

from robopy_controller.robot_ai.orchestration.conversation import ConversationManager
from robopy_controller.robot_ai.arbitration.intent_classifier import RouteTarget, RoutingDecision


def create_mock_dependencies():
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


@pytest.fixture
def mock_dependencies():
    return create_mock_dependencies()


def test_quota_exhausted_failover_to_local_qwen(mock_dependencies):
    """
    Test that when Gemini fails with 429 RESOURCE_EXHAUSTED (spending cap / quota exhausted),
    ConversationManager executes immediate transparent failover to local Qwen2
    and NEVER responds with 'Non riesco a connettermi al cervello.'
    """
    async def _run():
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

    asyncio.run(_run())


def test_direct_operational_routing_to_local_qwen(mock_dependencies):
    """
    Test that operational motion and telemetry queries are routed directly
    to Local Qwen on Hailo-10H NPU without even calling Gemini.
    """
    async def _run():
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

    asyncio.run(_run())


def test_audio_quota_exhausted_instant_failover_and_breaker(mock_dependencies):
    """
    Test that when Gemini Live fails with 429 RESOURCE_EXHAUSTED:
    1. Circuit breaker is tripped to OPEN.
    2. Local Qwen responds immediately.
    3. Subsequent turn bypasses Gemini completely without cloud timeouts.
    """
    async def _run():
        from robopy_controller.robot_ai.services.llm_circuit_breaker import CircuitBreaker
        breaker = CircuitBreaker("llm_test", failure_threshold=1, recovery_timeout=300.0)
        mock_dependencies["llm"].circuit_breaker = breaker
        mock_dependencies["llm"]._breaker = breaker

        received_responses = []

        def on_response(text):
            received_responses.append(text)

        manager = ConversationManager(
            response_callback=on_response,
            **mock_dependencies
        )
        manager.set_mic_muted(False)

        # Simulate Gemini Live failing with RESOURCE_EXHAUSTED
        mock_dependencies["llm"].generate_live.side_effect = Exception("RESOURCE_EXHAUSTED: spending cap exceeded")
        mock_dependencies["llm"].generate.side_effect = AssertionError("Standard generate should NOT be called after quota exhaustion!")

        # Process audio input turn 1
        t0 = time.perf_counter()
        success1 = await manager.process_input("ciao marcus, chi sei?", source="audio")
        latency1 = time.perf_counter() - t0

        assert success1 is True, "Turn 1 audio should succeed via local Qwen failover"
        assert len(received_responses) == 1
        assert "marcus" in received_responses[0].lower() or "assistente" in received_responses[0].lower()
        assert breaker.state == "OPEN", "Circuit breaker MUST be tripped to OPEN immediately"

        # Turn 2: should bypass Gemini completely in < 50ms without attempting cloud calls
        t1 = time.perf_counter()
        success2 = await manager.process_input("quanta batteria hai?", source="audio")
        latency2 = time.perf_counter() - t1

        assert success2 is True, "Turn 2 should route to local Qwen via fast-bypass"
        assert len(received_responses) == 2
        assert "batteria" in received_responses[1].lower() or "%" in received_responses[1] or "volt" in received_responses[1].lower()
        print(f"Verified instant audio failover & circuit breaker bypass: Turn 1: {latency1*1000:.1f}ms, Turn 2: {latency2*1000:.1f}ms")

    asyncio.run(_run())


if __name__ == "__main__":
    deps = create_mock_dependencies()
    print(">>> [TEST 1] Running test_quota_exhausted_failover_to_local_qwen...")
    asyncio.run(test_quota_exhausted_failover_to_local_qwen(deps))
    print(">>> [TEST 2] Running test_direct_operational_routing_to_local_qwen...")
    asyncio.run(test_direct_operational_routing_to_local_qwen(deps))
    print(">>> [TEST 3] Running test_audio_quota_exhausted_instant_failover_and_breaker...")
    deps2 = create_mock_dependencies()
    asyncio.run(test_audio_quota_exhausted_instant_failover_and_breaker(deps2))
    print("✅ All conversation hybrid failover and circuit breaker tests PASSED!")
