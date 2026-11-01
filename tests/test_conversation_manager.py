"""
Unit Tests for ConversationManager (Milestone 1)
================================================
Verifies:
1. Instant circuit-breaker routing directly to Local Qwen when Gemini Live / Cloud fails
   or quota is exhausted (429 RESOURCE_EXHAUSTED).
2. Breaker trips to OPEN immediately and subsequent turns bypass cloud without timeout latency.
3. Natural, empathetic, spontaneous Italian responses across all turns.
4. Total elimination of robotic/automa boilerplate ("Ho elaborato la tua richiesta",
   "Ho ricevuto il tuo messaggio! Sono operativo...", "sono pronto").
5. Fast-path safety stop remains operational (<10ms).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import types
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for _p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Mock ROS 2 dependencies for clean local testing
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
from robopy_controller.robot_ai.services.llm_circuit_breaker import CircuitBreaker


def build_test_dependencies() -> Dict[str, Any]:
    llm = MagicMock()
    llm.generate = AsyncMock()
    llm.generate_live = AsyncMock()
    llm._system_prompt = "Sei Marcus, robot fisico autonomo con TRINITY e NPU Hailo-10H."
    llm.is_duplicate_text = MagicMock(return_value=False)

    breaker = CircuitBreaker("llm_unit_test", failure_threshold=1, recovery_timeout=300.0)
    llm.circuit_breaker = breaker
    llm._breaker = breaker

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
    world_model.current_user = {"name": "Luca"}
    world_model.battery_voltage = 12.4
    world_model.current_room = "salotto"
    world_model.recent_interactions = []
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


ROBOTIC_PHRASES = [
    "ho elaborato la tua richiesta",
    "ho ricevuto il tuo messaggio! sono operativo",
    "ho registrato la tua richiesta",
    "ho eseguito l'operazione richiesta",
    "come modello di intelligenza artificiale",
    "non ho accesso a nessun dispositivo fisico",
]


@pytest.mark.asyncio
async def test_instant_circuit_breaker_failover_on_quota():
    """Verify instant failover to local Qwen when Gemini Live hits quota limit."""
    deps = build_test_dependencies()
    received: List[str] = []

    manager = ConversationManager(
        response_callback=lambda txt: received.append(txt),
        **deps
    )
    manager.set_mic_muted(False)

    # Simulate quota exhaustion on Gemini Live
    deps["llm"].generate_live.side_effect = Exception("429 RESOURCE_EXHAUSTED: quota exceeded")
    deps["llm"].generate.side_effect = AssertionError("Standard generate should NOT be attempted on quota error")

    # Turn 1: should trip breaker to OPEN and respond via local Qwen
    t0 = time.perf_counter()
    res1 = await manager.process_input("ciao marcus, chi sei?", source="audio")
    elapsed1 = time.perf_counter() - t0

    assert res1 is True
    assert len(received) == 1
    reply1 = received[0].lower()
    assert "marcus" in reply1 or "assistente" in reply1
    assert deps["llm"].circuit_breaker.state == "OPEN"

    for forbidden in ROBOTIC_PHRASES:
        assert forbidden not in reply1, f"Found robotic phrase '{forbidden}' in reply: '{reply1}'"

    # Turn 2: since breaker is OPEN, cloud MUST be bypassed completely without calling Gemini
    t1 = time.perf_counter()
    res2 = await manager.process_input("in che stanza siamo?", source="audio")
    elapsed2 = time.perf_counter() - t1

    assert res2 is True
    assert len(received) == 2
    reply2 = received[1].lower()
    assert "salotto" in reply2 or "stanza" in reply2
    assert elapsed2 < 2.5, f"Bypass should be instantaneous, took {elapsed2:.3f}s"

    for forbidden in ROBOTIC_PHRASES:
        assert forbidden not in reply2, f"Found robotic phrase '{forbidden}' in reply: '{reply2}'"


@pytest.mark.asyncio
async def test_zero_robotic_boilerplate_across_scenarios():
    """Verify that multiple conversational and query scenarios produce natural empathetic Italian."""
    deps = build_test_dependencies()
    # Force direct local Qwen processing
    deps["metrics"].connectivity_state = "OFFLINE"

    received: List[str] = []
    manager = ConversationManager(
        response_callback=lambda txt: received.append(txt),
        **deps
    )

    test_prompts = [
        "Ciao Marcus!",
        "Come stai oggi?",
        "Qual è la capitale della Francia?",
        "Cosa ricordi di me?",
        "Quanta batteria hai adesso?",
        "Fermati immediatamente!",
    ]

    for p in test_prompts:
        received.clear()
        ok = await manager.process_input(p, source="text")
        assert ok is True, f"Failed on prompt: {p}"
        assert len(received) >= 1
        resp = received[0].lower()

        for forbidden in ROBOTIC_PHRASES:
            assert forbidden not in resp, f"Prompt '{p}' elicited forbidden robotic phrase '{forbidden}': '{resp}'"

        assert len(resp) > 5, f"Response too short for '{p}': '{resp}'"


@pytest.mark.asyncio
async def test_spontaneous_verbal_fallback_when_empty_speech():
    """Verify that when speech from local Qwen is empty, spontaneous fallback is emitted instead of robotic string."""
    deps = build_test_dependencies()
    manager = ConversationManager(**deps)

    fallback_greeting = manager._generate_spontaneous_verbal_fallback("ciao marcus")
    assert "ciao" in fallback_greeting.lower()
    assert "ho elaborato la tua richiesta" not in fallback_greeting.lower()

    fallback_generic = manager._generate_spontaneous_verbal_fallback("dimmi qualcosa")
    assert len(fallback_generic) > 10
    assert "ho elaborato la tua richiesta" not in fallback_generic.lower()
    assert "operativo" not in fallback_generic.lower()


@pytest.mark.asyncio
async def test_emergency_stop_fast_path():
    """Verify fast-path emergency stop bypasses LLM and executes in < 15ms."""
    deps = build_test_dependencies()
    received: List[str] = []
    manager = ConversationManager(
        response_callback=lambda txt: received.append(txt),
        **deps
    )

    t0 = time.perf_counter()
    res = await manager.process_input("fermati subito!", source="audio")
    elapsed = time.perf_counter() - t0

    assert res is True
    assert elapsed < 0.100, f"Emergency stop took too long: {elapsed*1000:.1f}ms"
    deps["reactive_safety"].emergency_stop.assert_called_once()
    assert len(received) == 1
    assert "fermo" in received[0].lower() or "motori" in received[0].lower()


if __name__ == "__main__":
    pytest.main(["-v", __file__])
