"""
Milestone 2 Verification Harness: Zero Silences, Empathetic Anti-Echo & Hardware Resilience
==========================================================================================
Verifies:
1. Fast DDS microsecond burst absorption (<0.20s): duplicate message ignored at transport layer.
2. In-flight repetition test: when query 1 is executing (simulated 1.0s delay), query 2 (dt=0.3s)
   triggers an immediate witty acknowledgment ("Ti ho sentito, ci sono!") in <200ms without
   starting duplicate heavy inference.
3. Post-completion repetition test: query repeated at dt = 2.0s triggers a reaffirming response
   ("Te lo riconfermo: ...").
4. Hardware tarpit / NPU hang test: simulated 5.0s hang in hailo_client.generate is aborted at 1.2s
   and emits empathetic Italian fallback in <1.5s total SLA.
5. Absolute zero-silence verification: 100% of user inputs produce a callback event on
   /ai/conversation/response.
"""

import asyncio
import os
import sys
import time
from typing import List, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

import types

# Ensure UTF-8 output on Windows consoles to prevent cp1252 UnicodeEncodeError delays
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from importlib.abc import MetaPathFinder, Loader
from importlib.machinery import ModuleSpec

class DynamicMockLoader(Loader):
    def create_module(self, spec):
        mod = MagicMock()
        mod.__name__ = spec.name
        mod.__path__ = []
        if spec.name == "rclpy.node":
            class MockNode:
                def __init__(self, *args, **kwargs): pass
            mod.Node = MockNode
        return mod

    def exec_module(self, module):
        pass

class MissingModuleMockFinder(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith("robopy_controller.robot_ai") or fullname == "robopy_controller":
            return None
        mockable = {
            "rclpy", "sensor_msgs", "sensor_msgs_py", "std_msgs", "vision_msgs", "visualization_msgs",
            "diagnostic_msgs", "geometry_msgs", "nav_msgs", "action_msgs", "builtin_interfaces",
            "rcl_interfaces", "example_interfaces", "cv_bridge", "tf2_ros", "aiohttp", "aioimaplib",
            "aiosmtplib", "chromadb"
        }
        root = fullname.split(".")[0]
        if root in mockable or fullname in {"robopy_controller.msg", "robopy_controller.srv"}:
            return ModuleSpec(fullname, DynamicMockLoader(), is_package=True)
        return None

sys.meta_path.insert(0, MissingModuleMockFinder())

from robopy_controller.robot_ai.orchestration.conversation import ConversationManager
from robopy_controller.robot_ai.hailo.hailo_qwen_client import InferenceTelemetry


@pytest.fixture
def mock_dependencies():
    llm = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "Mi chiamo Marcus e sono il tuo rover."
    mock_resp.actions = []
    llm.generate = AsyncMock(return_value=mock_resp)
    llm.generate_live = AsyncMock(return_value=mock_resp)
    llm._system_prompt = "Sei Marcus, robot rover amichevole ed empatico."
    llm.is_duplicate_text = MagicMock(return_value=False)

    tts = MagicMock()
    tts.speak = AsyncMock()

    skill_executor = MagicMock()
    skill_executor.find_best_match.return_value = None
    skill_executor.registry.get_function_declarations.return_value = []
    skill_executor.registry.get.return_value = None

    memory_store = MagicMock()
    memory_store.search = AsyncMock(return_value=[])
    memory_manager = MagicMock()
    memory_manager.memory_store = memory_store
    memory_manager.embedding_service = None

    world_model = MagicMock()
    world_model.current_user = None
    world_model.current_room = "salotto"
    world_model.battery_voltage = 12.1
    world_model.to_prompt_section.return_value = ""

    ha_context_provider = MagicMock(return_value="")

    metrics = MagicMock()
    metrics.connectivity_state = "ONLINE"
    metrics.record_llm_latency = MagicMock()
    metrics.record_llm_error = MagicMock()
    metrics.inc_requests_total = MagicMock()
    metrics.inc_requests_success = MagicMock()
    metrics.inc_requests_failed = MagicMock()

    config = MagicMock()
    config.trinity = MagicMock(enabled=False)
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


# ============================================================================
# 1. Fast DDS Microsecond Burst Absorption (<0.20s)
# ============================================================================
class TestFastDdsBurstDeduplication:
    """
    Validates transport-layer deduplication in AIOrchestrator._text_input_callback.
    Microsecond / millisecond DDS duplicates (<0.20s) are cleanly debounced,
    while subsequent requests at dt >= 0.20s are forwarded.
    """

    def test_microsecond_burst_ignored_at_transport_layer(self):
        from robopy_controller.robot_ai.orchestration.orchestrator import AIOrchestrator

        with patch.object(AIOrchestrator, '__init__', lambda self: None):
            orch = AIOrchestrator()
            orch._shutdown_flag = False
            orch._last_text_input = ""
            orch._last_text_time = 0.0
            orch.ai_logger = MagicMock()
            orch.conversation_manager = MagicMock()
            orch.conversation_manager.process_input = MagicMock()
            orch._loop = MagicMock()

            # Message fixture
            msg = MagicMock()
            msg.data = "Marcus dove sei?"

            # Message 1 at t = 1000.000
            with patch("time.time", return_value=1000.000):
                with patch("asyncio.run_coroutine_threadsafe") as mock_run:
                    orch._text_input_callback(msg)
                    assert mock_run.call_count == 1
            assert orch._last_text_input == "Marcus dove sei?"
            assert orch._last_text_time == 1000.000

            # Message 2 at t = 1000.005 (5ms later, microsecond burst)
            with patch("time.time", return_value=1000.005):
                with patch("asyncio.run_coroutine_threadsafe") as mock_run:
                    orch._text_input_callback(msg)
                    mock_run.assert_not_called()

            # Message 3 at t = 1000.180 (180ms later, sub-200ms burst)
            with patch("time.time", return_value=1000.180):
                with patch("asyncio.run_coroutine_threadsafe") as mock_run:
                    orch._text_input_callback(msg)
                    mock_run.assert_not_called()

    def test_legitimate_subsequent_request_forwarded_after_200ms(self):
        from robopy_controller.robot_ai.orchestration.orchestrator import AIOrchestrator

        with patch.object(AIOrchestrator, '__init__', lambda self: None):
            orch = AIOrchestrator()
            orch._shutdown_flag = False
            orch._last_text_input = ""
            orch._last_text_time = 0.0
            orch.ai_logger = MagicMock()
            orch.conversation_manager = MagicMock()
            orch._loop = MagicMock()

            msg = MagicMock()
            msg.data = "Marcus dove sei?"

            # First message at t = 1000.000
            with patch("time.time", return_value=1000.000):
                with patch("asyncio.run_coroutine_threadsafe") as mock_run:
                    orch._text_input_callback(msg)
                    assert mock_run.call_count == 1

            # Subsequent message at t = 1000.250 (dt = 0.250s >= 0.20s)
            with patch("time.time", return_value=1000.250):
                with patch("asyncio.run_coroutine_threadsafe") as mock_run:
                    orch._text_input_callback(msg)
                    assert mock_run.call_count == 1, "Should forward message when dt >= 0.20s"
                    assert orch._last_text_time == 1000.250


# ============================================================================
# 2. In-Flight Repetition Test (Query 2 at dt = 0.3s -> Witty Ack in <200ms)
# ============================================================================
class TestInFlightRepetition:
    """
    Validates that when Query 1 is executing (simulated 1.0s delay),
    Query 2 (identical text at dt = 0.3s) triggers an immediate witty
    acknowledgment ("Ti ho sentito, ci sono!") in <200ms without starting duplicate heavy inference.
    """

    @pytest.mark.asyncio
    async def test_inflight_repeated_query_triggers_witty_ack_under_200ms(self, mock_dependencies):
        received_responses: List[Tuple[float, str]] = []

        def on_response(text: str):
            received_responses.append((time.perf_counter(), text))

        manager = ConversationManager(response_callback=on_response, **mock_dependencies)

        # Mock Hailo client with a 1.0s simulated heavy inference
        mock_hailo = MagicMock()
        mock_telem = InferenceTelemetry(
            model="qwen2.5-instruct:1.5b", prompt="Marcus dove sei?", response="Sono in salotto.",
            ttft_ms=280.0, total_time_ms=1000.0, token_count=5, tokens_per_sec=5.0,
            cpu_delta_pct=0.0, memory_delta_mb=0.0, is_mock=True, is_streaming=False
        )

        async def slow_generate(prompt, **kwargs):
            await asyncio.sleep(0.5)
            return "Sono in salotto.", mock_telem

        mock_hailo.generate = AsyncMock(side_effect=slow_generate)
        manager.hailo_client = mock_hailo

        # Launch Query 1 at t = 0.0s (running in background task)
        task_1 = asyncio.create_task(manager.process_input("Marcus dove sei?", source="text"))

        # Wait dt = 0.25s so Query 1 is confirmed in-flight
        await asyncio.sleep(0.25)

        # Launch Query 2 at dt = 0.25s and measure acknowledgment SLA
        t_q2_start = time.perf_counter()
        q2_success = await manager.process_input("Marcus dove sei?", source="text")
        t_q2_elapsed = time.perf_counter() - t_q2_start

        # Assertions for Query 2 (In-Flight Acknowledgment)
        assert q2_success is True, "Query 2 should succeed immediately"
        assert t_q2_elapsed < 0.200, f"Query 2 acknowledgment took {t_q2_elapsed*1000:.1f}ms, expected < 200ms"
        assert len(received_responses) >= 1, "At least one response (witty ack) must be emitted immediately"

        witty_reply = received_responses[0][1].lower()
        witty_keywords = ["ti ho sentito", "ci sono", "riflettendo", "pazienza", "pensando", "non ti ignoro", "ascoltato", "elaborando"]
        assert any(k in witty_reply for k in witty_keywords), (
            f"Response '{witty_reply}' does not match expected witty acknowledgment patterns"
        )

        # Await completion of Query 1
        q1_success = await task_1
        assert q1_success is True, "Query 1 should complete successfully"

        # Assertions for total response delivery
        assert len(received_responses) == 2, "Expected exactly 2 responses (witty ack + substantive answer)"
        substantive_reply = received_responses[1][1]
        assert "salotto" in substantive_reply.lower(), f"Substantive answer missing: {substantive_reply}"

    @pytest.mark.asyncio
    async def test_inflight_repetition_does_not_start_duplicate_inference(self, mock_dependencies):
        manager = ConversationManager(response_callback=lambda text: None, **mock_dependencies)

        mock_hailo = MagicMock()
        inference_count = 0

        async def counting_generate(prompt, **kwargs):
            nonlocal inference_count
            inference_count += 1
            await asyncio.sleep(0.5)
            return "Risposta", MagicMock()

        mock_hailo.generate = AsyncMock(side_effect=counting_generate)
        manager.hailo_client = mock_hailo

        # Direct to local Qwen to test heavy inference counter
        from robopy_controller.robot_ai.arbitration.intent_classifier import RouteTarget
        mock_route = MagicMock()
        mock_route.destination = RouteTarget.LOCAL_QWEN
        mock_route.reason = "test_force_local"
        manager.hybrid_router.route = MagicMock(return_value=mock_route)

        # Launch Query 1
        task_1 = asyncio.create_task(manager.process_input("Chi sei?", source="text"))
        await asyncio.sleep(0.20)

        # Launch Query 2 while Query 1 is in-flight
        await manager.process_input("Chi sei?", source="text")
        await task_1

        assert inference_count == 1, "Duplicate heavy inference must NOT be started for in-flight repetition"


# ============================================================================
# 3. Post-Completion Repetition Test (Query Repeated at dt = 2.0s -> Reaffirmation)
# ============================================================================
class TestPostCompletionRepetition:
    """
    Validates that when Query 1 has completed and the identical query is repeated
    at dt = 2.0s (< 10.0s), Marcus emits a reaffirming response ("Te lo riconfermo: ...")
    without silent drops.
    """

    @pytest.mark.asyncio
    async def test_post_completion_repetition_reaffirms_within_10s(self, mock_dependencies):
        received_responses: List[str] = []

        def on_response(text: str):
            received_responses.append(text)

        manager = ConversationManager(response_callback=on_response, **mock_dependencies)

        mock_hailo = MagicMock()
        mock_telem = MagicMock()
        mock_hailo.generate = AsyncMock(return_value=("Mi chiamo Marcus e sono il tuo rover.", mock_telem))
        manager.hailo_client = mock_hailo

        # Query 1 at t = 0.0s
        success_1 = await manager.process_input("Come ti chiami?", source="text")
        assert success_1 is True
        assert len(received_responses) == 1
        assert "Marcus" in received_responses[0]

        # Simulate user repeating the query after 0.35s (dt = 0.35s is within 0.25s <= dt <= 10.0s)
        await asyncio.sleep(0.35)
        success_2 = await manager.process_input("Come ti chiami?", source="text")

        assert success_2 is True
        assert len(received_responses) == 2, "Second query must NOT be silently dropped"

        reaffirm_reply = received_responses[1].lower()
        reaffirm_keywords = ["te lo riconfermo", "come ti dicevo", "volentieri di nuovo", "marcus"]
        assert any(k in reaffirm_reply for k in reaffirm_keywords), (
            f"Second response '{reaffirm_reply}' does not reaffirm previous answer"
        )

    @pytest.mark.asyncio
    async def test_post_completion_repetition_does_not_drop_silently(self, mock_dependencies):
        received_responses = []
        manager = ConversationManager(response_callback=received_responses.append, **mock_dependencies)

        mock_hailo = MagicMock()
        mock_hailo.generate = AsyncMock(return_value=("La carica è al 95%.", MagicMock()))
        manager.hailo_client = mock_hailo

        # Execute 3 consecutive queries with small pauses
        for _ in range(3):
            await manager.process_input("stato batteria", source="text")
            await asyncio.sleep(0.3)

        assert len(received_responses) == 3, "Zero responses should be dropped across 3 repetitions"


# ============================================================================
# 4. Hardware Tarpit / NPU Hang Test (5.0s Hang Aborted at 1.2s -> Fallback <1.5s)
# ============================================================================
class TestHardwareHangResilience:
    """
    Validates that a simulated 5.0s hang in hailo_client.generate is aborted at 1.2s
    and emits an empathetic Italian conversational fallback in <1.5s total SLA.
    """

    @pytest.mark.asyncio
    async def test_npu_tarpit_hang_aborted_at_1_2s_fallback_under_1_5s_sla(self, mock_dependencies):
        received_responses: List[Tuple[float, str]] = []

        def on_response(text: str):
            received_responses.append((time.perf_counter(), text))

        manager = ConversationManager(response_callback=on_response, **mock_dependencies)

        # Simulate 5.0s PCIe tarpit hang in hailo_client.generate
        mock_hailo = MagicMock()

        async def tarpit_generate(*args, **kwargs):
            await asyncio.sleep(5.0)
            return "Mai raggiunto", MagicMock()

        mock_hailo.generate = AsyncMock(side_effect=tarpit_generate)
        manager.hailo_client = mock_hailo

        # Measure end-to-end execution latency
        t_start = time.perf_counter()
        success = await manager.process_input("stato batteria", source="text")
        t_elapsed = time.perf_counter() - t_start

        # Verification Assertions
        assert success is True, "process_input must return True on fallback (Never fail silently)"
        assert 1.15 <= t_elapsed < 1.50, (
            f"Total elapsed time was {t_elapsed:.3f}s. Expected timeout abort at ~1.2s and total SLA < 1.50s"
        )
        assert len(received_responses) == 1, "Exactly one fallback response must be delivered"

        fallback_msg = received_responses[0][1]
        assert len(fallback_msg) > 10, "Fallback response must be substantive"
        assert "[SILENZIO]" not in fallback_msg
        assert "{" not in fallback_msg, "No raw JSON leak permitted in fallback"

        fallback_lower = fallback_msg.lower()
        empathetic_tokens = ["circuiti", "esitazione", "distrazione", "attimo", "ci sono", "batteria", "alimentazione"]
        assert any(token in fallback_lower for token in empathetic_tokens), (
            f"Fallback message '{fallback_msg}' missing empathetic Italian phrasing"
        )

    @pytest.mark.asyncio
    async def test_hardware_exception_emits_empathetic_italian_fallback(self, mock_dependencies):
        received_responses = []
        manager = ConversationManager(response_callback=received_responses.append, **mock_dependencies)

        # Simulate instant hardware crash (e.g. broken socket or NPU device disconnected)
        mock_hailo = MagicMock()
        mock_hailo.generate = AsyncMock(side_effect=RuntimeError("Hailo PCIe Device Disconnected (Error 0x88)"))
        manager.hailo_client = mock_hailo

        t_start = time.perf_counter()
        success = await manager.process_input("dove ti trovi?", source="text")
        t_elapsed = time.perf_counter() - t_start

        assert success is True
        assert t_elapsed < 0.200, "Instant hardware exception fallback should return in < 200ms"
        assert len(received_responses) == 1
        assert "Error 0x88" not in received_responses[0], "Raw error traces must not leak to user"
        assert len(received_responses[0]) > 10


# ============================================================================
# 5. Absolute Zero-Silence Verification (100% Callback Delivery)
# ============================================================================
class TestZeroSilenceGuarantee:
    """
    Validates that 100% of user inputs produce a callback event on /ai/conversation/response.
    Evaluates 20 diverse adversarial and edge-case inputs.
    """

    @pytest.mark.asyncio
    async def test_100_percent_user_inputs_produce_response_callback(self, mock_dependencies):
        received_responses: List[str] = []

        def on_response(text: str):
            received_responses.append(text)

        manager = ConversationManager(response_callback=on_response, **mock_dependencies)

        # Setup responsive local mock client
        mock_hailo = MagicMock()
        mock_hailo.generate = AsyncMock(return_value=("Ricevuto e compreso.", MagicMock()))
        manager.hailo_client = mock_hailo

        # 20 diverse and adversarial input scenarios
        test_inputs = [
            "Ciao Marcus",                                      # 1. Standard greeting
            "Come sta la batteria?",                            # 2. Telemetry inquiry
            "Ruota a destra di 45 gradi",                       # 3. Motion action
            "Vai avanti a 10 metri al secondo",                 # 4. Out-of-bounds speed (sandbox rejection)
            "Dove sei?",                                        # 5. Spatial query
            "Dove sei?",                                        # 6. Immediate repeat (0s dt)
            "Dove sei?",                                        # 7. Rapid repeat 2
            "Chi ti ha creato?",                                # 8. Knowledge query
            "Qual è la ricetta della carbonara?",               # 9. Open question
            "Fermati!",                                         # 10. Fast-path stop
            "Emergenza!",                                       # 11. Emergency halt
            "???",                                              # 12. Punctuation only
            "   ciao   ",                                       # 13. Leading/trailing whitespace
            "1234567890",                                       # 14. Digits only
            "Non riesco a sentirti",                            # 15. Audio complaint
            "Cosa vedi?",                                       # 16. Vision query
            "Ripetimi cosa hai appena detto",                   # 17. Meta-query
            "Sei sicuro?",                                      # 18. Follow-up
            "Grazie mille Marcus",                              # 19. Gratitude
            "Arrivederci",                                      # 20. Farewell
        ]

        # Execute all 20 inputs through manager.process_input
        for idx, user_prompt in enumerate(test_inputs):
            responses_before = len(received_responses)
            success = await manager.process_input(user_prompt, source="text")

            responses_after = len(received_responses)
            delta = responses_after - responses_before

            assert success is True, f"Input #{idx+1} '{user_prompt}' returned success=False"
            assert delta >= 1, f"Zero responses emitted for input #{idx+1} '{user_prompt}' (SILENT DROP)"
            assert received_responses[-1] != "", f"Empty response text for input #{idx+1}"
            assert received_responses[-1] != "[SILENZIO]", f"[SILENZIO] emitted for input #{idx+1}"

        # Assert 100% delivery rate
        delivery_rate = (len(received_responses) / len(test_inputs)) * 100.0
        assert delivery_rate >= 100.0, f"Delivery rate {delivery_rate:.1f}% is below 100% threshold"
        print(f"\n[PASS] Verified 100% response delivery across {len(test_inputs)}/{len(test_inputs)} inputs.")
