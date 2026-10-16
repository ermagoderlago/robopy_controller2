"""
Empirical Challenger Test Suite: Hardware Tarpits & Fallback Latency SLA (<1.50s)
================================================================================
Agent: challenger_m2_2 (Archetype: EMPIRICAL CHALLENGER, Role: Critic / Specialist)
Target: robopy_controller.robot_ai.orchestration.conversation (ConversationManager)

Empirical Requirements Verified:
1. 20-Trial Hardware Hang Injection Sweep:
   - Hang durations injected into hailo_client.generate: 0.5s, 1.2s, 1.4s, 2.0s, 5.0s, 10.0s.
   - Mix of slow hardware, boundary timeouts, and severe PCIe tarpit freezes.
2. Latency SLA Invariant:
   - Measured actual wall-clock elapsed time from process_input() call to response_callback invocation.
   - Invariant: Latency MUST NEVER exceed 1.500s under any hardware tarpit condition.
3. Fallback Content & Quality Assessment:
   - Italian language, empathetic, humanized tone.
   - Strictly intent-aware across categories:
     * Battery / Power -> Battery voltage, system power, circuit stability.
     * Location / Spatial -> Current room, spatial localization, map orientation.
     * Motion / Actuation -> Safe motion hesitation or emergency stop confirmation.
     * Greeting / Presence -> Friendly robotic presence, clear reception.
     * Generic / Unknown -> Gentle apology about neural circuit hesitation.
   - Zero raw JSON syntax leaks (no '{', '}', '<action>', 'parameters', etc.).
   - Zero unhandled exceptions: process_input returns True, 100% response delivery.
"""

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure repository root and package path in sys.path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Portable ROS 2 Mock Loader for offline testing on Windows
from importlib.abc import Loader, MetaPathFinder
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
from robopy_controller.robot_ai.arbitration.intent_classifier import RouteTarget


@pytest.fixture
def mock_dependencies():
    llm = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "Risposta nominale Marcus."
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
    world_model.battery_voltage = 12.18
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


@dataclass
class TarpitTrialSpec:
    trial_id: int
    prompt: str
    hang_sec: float
    expected_category: str  # "battery", "location", "motion", "greeting", "generic"
    hang_mode: str          # "sleep_tarpit", "sleep_and_raise", "sleep_and_return"


# 20 Adversarial Trials spanning the required hangs: 0.5s, 1.2s, 1.4s, 2.0s, 5.0s, 10.0s
TARPIT_20_TRIALS: List[TarpitTrialSpec] = [
    # 0.5s hangs (sub-timeout: fast recovery or early failure)
    TarpitTrialSpec(1, "Quanta carica ha la batteria?", 0.5, "battery", "sleep_and_return"),
    TarpitTrialSpec(2, "Stato della batteria e alimentazione", 0.5, "battery", "sleep_and_raise"),
    TarpitTrialSpec(3, "Cammina in avanti di un metro", 0.5, "motion", "sleep_and_raise"),
    TarpitTrialSpec(4, "Controllo diagnostica voltaggio", 0.5, "battery", "sleep_and_raise"),

    # 1.2s hangs (boundary condition: near the 1.20s generation timeout)
    TarpitTrialSpec(5, "Dove ti trovi adesso?", 1.2, "location", "sleep_tarpit"),
    TarpitTrialSpec(6, "Qual è la tua posizione sulla mappa?", 1.2, "location", "sleep_tarpit"),
    TarpitTrialSpec(7, "Come sta la carica dei tuoi accumulatori?", 1.2, "battery", "sleep_tarpit"),
    TarpitTrialSpec(8, "Gira a sinistra di 30 gradi", 1.2, "motion", "sleep_tarpit"),

    # 1.4s hangs (just above the 1.2s timeout, within the 1.40s overall budget)
    TarpitTrialSpec(9, "Ruota a destra di 90 gradi", 1.4, "motion", "sleep_tarpit"),
    TarpitTrialSpec(10, "Avanza di mezzo metro verso il corridoio", 1.4, "motion", "sleep_tarpit"),
    TarpitTrialSpec(11, "Dove sei finito?", 1.4, "location", "sleep_tarpit"),

    # 2.0s hangs (moderate PCIe/NPU stall)
    TarpitTrialSpec(12, "Ciao Marcus buongiorno!", 2.0, "greeting", "sleep_tarpit"),
    TarpitTrialSpec(13, "Ehi Marcus, ci sei?", 2.0, "greeting", "sleep_tarpit"),
    TarpitTrialSpec(14, "Vai indietro piano piano", 2.0, "motion", "sleep_tarpit"),

    # 5.0s hangs (deep hardware tarpit)
    TarpitTrialSpec(15, "Raccontami una breve storia", 5.0, "generic", "sleep_tarpit"),
    TarpitTrialSpec(16, "Qual è la formula molecolare dell'acqua?", 5.0, "generic", "sleep_tarpit"),
    TarpitTrialSpec(17, "Salve Marcus, mi senti bene?", 5.0, "greeting", "sleep_tarpit"),

    # 10.0s hangs (extreme PCIe driver freeze / unkillable kernel thread)
    TarpitTrialSpec(18, "Quanti volt misura il sensore di batteria?", 10.0, "battery", "sleep_tarpit"),
    TarpitTrialSpec(19, "In quale stanza ti trovi esattamente?", 10.0, "location", "sleep_tarpit"),
    TarpitTrialSpec(20, "Cosa pensi della robotica e della coscienza?", 10.0, "generic", "sleep_tarpit"),
]


class TestChallengerM2TimeoutSla:
    """
    Empirical validation suite executing 20 hardware hang trials.
    Verifies wall-clock latency SLA (< 1.50s) and fallback content quality.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("spec", TARPIT_20_TRIALS, ids=[f"Trial_{s.trial_id:02d}_hang_{s.hang_sec}s_{s.expected_category}" for s in TARPIT_20_TRIALS])
    async def test_trial_tarpit_latency_and_fallback_quality(self, spec: TarpitTrialSpec, mock_dependencies):
        """
        Executes a single trial with injected hang. Measures wall-clock time
        strictly between process_input invocation and response_callback firing.
        """
        received_responses: List[Tuple[float, str]] = []

        def on_response(text: str):
            t_now = time.perf_counter()
            received_responses.append((t_now, text))

        manager = ConversationManager(response_callback=on_response, **mock_dependencies)

        # Force local Qwen routing so the request always exercises hailo_client.generate
        mock_route = MagicMock()
        mock_route.destination = RouteTarget.LOCAL_QWEN
        mock_route.reason = "challenger_empirical_test"
        manager.hybrid_router.route = MagicMock(return_value=mock_route)

        # Setup mock hailo_client with injected hang behavior
        mock_hailo = MagicMock()

        async def injected_generate(prompt, timeout_sec=None, **kwargs):
            if spec.hang_mode == "sleep_tarpit":
                # Sleep for the full hang duration (e.g. 1.2s, 1.4s, 2.0s, 5.0s, 10.0s)
                # Outer asyncio.wait_for in conversation.py should abort this at ~1.20s
                await asyncio.sleep(spec.hang_sec)
                return "Risposta non attesa (tarpit avrebbe dovuto essere interrotto)", MagicMock()
            elif spec.hang_mode == "sleep_and_raise":
                await asyncio.sleep(spec.hang_sec)
                raise TimeoutError(f"Simulated Hailo NPU timeout after {spec.hang_sec}s")
            elif spec.hang_mode == "sleep_and_return":
                await asyncio.sleep(spec.hang_sec)
                telem = InferenceTelemetry(
                    model="qwen2.5-instruct:1.5b", prompt=prompt, response="Risposta nominale generata in tempo.",
                    ttft_ms=spec.hang_sec * 1000.0, total_time_ms=spec.hang_sec * 1000.0,
                    token_count=10, tokens_per_sec=20.0, cpu_delta_pct=0.0, memory_delta_mb=0.0,
                    is_mock=True, is_streaming=False
                )
                return "Risposta nominale generata in tempo.", telem
            else:
                raise ValueError(f"Unknown hang mode: {spec.hang_mode}")

        mock_hailo.generate = AsyncMock(side_effect=injected_generate)
        manager.hailo_client = mock_hailo

        # Execute trial and record high-precision wall-clock timing
        t_start = time.perf_counter()
        success = await manager.process_input(spec.prompt, source="text")
        t_total_return = time.perf_counter() - t_start

        # 1. Delivery & Execution Guarantees
        assert success is True, (
            f"Trial {spec.trial_id} ('{spec.prompt}'): process_input returned False (Must never fail silently)"
        )
        assert len(received_responses) == 1, (
            f"Trial {spec.trial_id} ('{spec.prompt}'): Expected exactly 1 response callback, got {len(received_responses)}"
        )

        t_callback_time, response_text = received_responses[0]
        wall_clock_elapsed = t_callback_time - t_start

        # 2. Strict SLA Latency Invariant: MUST NEVER EXCEED 1.500 SECONDS
        assert wall_clock_elapsed < 1.500, (
            f"CRITICAL SLA VIOLATION on Trial {spec.trial_id} (hang={spec.hang_sec}s): "
            f"elapsed time was {wall_clock_elapsed:.4f}s (exceeds 1.500s ceiling)"
        )
        assert t_total_return < 1.500, (
            f"CRITICAL RETURN TIME VIOLATION on Trial {spec.trial_id}: "
            f"total return time was {t_total_return:.4f}s (exceeds 1.500s ceiling)"
        )

        # 3. Content Quality & Formatting Invariants
        assert len(response_text) > 10, (
            f"Trial {spec.trial_id}: Response '{response_text}' is empty or too short"
        )
        assert "[SILENZIO]" not in response_text, (
            f"Trial {spec.trial_id}: Detected forbidden token '[SILENZIO]'"
        )

        # Invariant: ZERO RAW JSON
        assert "{" not in response_text and "}" not in response_text, (
            f"Trial {spec.trial_id}: Raw JSON syntax leaked in response: '{response_text}'"
        )
        assert "<action>" not in response_text and "</action>" not in response_text, (
            f"Trial {spec.trial_id}: Raw XML action tags leaked in response: '{response_text}'"
        )
        assert "action:" not in response_text.lower() and "parameters" not in response_text.lower(), (
            f"Trial {spec.trial_id}: Raw tool syntax leaked in response: '{response_text}'"
        )

        # Invariant: ZERO RAW EXCEPTIONS OR STACKTRACES
        error_tokens = ["Traceback", "Exception", "Error:", "Errno", "NoneType", "timeout_sec"]
        for err_tok in error_tokens:
            assert err_tok not in response_text, (
                f"Trial {spec.trial_id}: Raw error leak detected ('{err_tok}') in response: '{response_text}'"
            )

        # 4. Intent-Aware Quality Verification (for fallback responses)
        resp_lower = response_text.lower()
        if spec.hang_mode != "sleep_and_return":
            if spec.expected_category == "battery":
                expected_keywords = ["batteria", "volt", "circuiti", "alimentazione", "stabili", "carica"]
                assert any(k in resp_lower for k in expected_keywords), (
                    f"Trial {spec.trial_id} (Category: battery): Response '{response_text}' missing battery/power context"
                )
            elif spec.expected_category == "location":
                expected_keywords = ["salotto", "stanza", "mappa", "orientato", "qui con te", "trovo", "posizione"]
                assert any(k in resp_lower for k in expected_keywords), (
                    f"Trial {spec.trial_id} (Category: location): Response '{response_text}' missing spatial context"
                )
            elif spec.expected_category == "motion":
                if "fermati" in spec.prompt.lower():
                    expected_keywords = ["fermato", "motori", "riposo", "immediatamente"]
                else:
                    expected_keywords = ["motori", "comando", "esitazione", "riprova", "istante"]
                assert any(k in resp_lower for k in expected_keywords), (
                    f"Trial {spec.trial_id} (Category: motion): Response '{response_text}' missing motion context"
                )
            elif spec.expected_category == "greeting":
                expected_keywords = ["ciao", "sento", "forte e chiaro", "pronto", "aiutarti"]
                assert any(k in resp_lower for k in expected_keywords), (
                    f"Trial {spec.trial_id} (Category: greeting): Response '{response_text}' missing greeting context"
                )
            elif spec.expected_category == "generic":
                expected_keywords = ["esitazione", "circuiti neurali", "ti ascolto", "dimmi pure", "scusami"]
                assert any(k in resp_lower for k in expected_keywords), (
                    f"Trial {spec.trial_id} (Category: generic): Response '{response_text}' missing polite general context"
                )

        print(
            f"Trial {spec.trial_id:02d} | Hang: {spec.hang_sec:4.1f}s ({spec.hang_mode:16s}) | "
            f"Latency: {wall_clock_elapsed:.4f}s | Status: PASS | Resp: {response_text[:45]}..."
        )

    @pytest.mark.asyncio
    async def test_audio_source_tarpit_with_tts_interaction(self, mock_dependencies):
        """
        Validates behavior when source='audio'. Ensures response_callback fires in <1.5s
        and tts.speak is awaited with the fallback text.
        """
        received = []
        manager = ConversationManager(response_callback=received.append, **mock_dependencies)

        mock_route = MagicMock()
        mock_route.destination = RouteTarget.LOCAL_QWEN
        mock_route.reason = "audio_tarpit_test"
        manager.hybrid_router.route = MagicMock(return_value=mock_route)

        async def infinite_hang(*args, **kwargs):
            await asyncio.sleep(8.0)
            return "Should not arrive", MagicMock()

        mock_hailo = MagicMock()
        mock_hailo.generate = AsyncMock(side_effect=infinite_hang)
        manager.hailo_client = mock_hailo
        manager.set_mic_muted(False)

        t0 = time.perf_counter()
        success = await manager.process_input("stato batteria", source="audio")
        elapsed = time.perf_counter() - t0

        assert success is True
        assert elapsed < 1.500, f"Audio source tarpit took {elapsed:.4f}s, expected < 1.500s"
        assert len(received) == 1
        mock_dependencies["tts"].speak.assert_awaited_once()


def run_standalone_benchmark():
    """
    Executes the 20-trial empirical benchmark suite directly and prints
    detailed statistical summary table (mean, median, p95, max).
    """
    import asyncio

    print("\n" + "=" * 80)
    print("EMPIRICAL CHALLENGER: HARDWARE TARPIT & FALLBACK SLA BENCHMARK (20 TRIALS)")
    print("=" * 80)

    # Fixture reconstruction for standalone script execution
    llm = MagicMock()
    llm.generate = AsyncMock()
    llm._system_prompt = "Sei Marcus."
    llm.is_duplicate_text = MagicMock(return_value=False)
    tts = MagicMock(speak=AsyncMock())
    skill_executor = MagicMock()
    skill_executor.find_best_match.return_value = None
    skill_executor.registry.get_function_declarations.return_value = []
    skill_executor.registry.get.return_value = None
    memory_manager = MagicMock(memory_store=MagicMock(search=AsyncMock(return_value=[])), embedding_service=None)
    world_model = MagicMock(current_user=None, current_room="salotto", battery_voltage=12.24)
    world_model.to_prompt_section.return_value = ""
    ha_provider = MagicMock(return_value="")
    metrics = MagicMock(connectivity_state="ONLINE")
    config = MagicMock()
    config.trinity = MagicMock(enabled=False)
    config.get_config.return_value.rag.enabled = False
    reactive_safety = MagicMock()

    deps = {
        "llm": llm, "tts": tts, "skill_executor": skill_executor,
        "memory_manager": memory_manager, "world_model": world_model,
        "ha_context_provider": ha_provider, "metrics": metrics,
        "config": config, "reactive_safety": reactive_safety
    }

    latencies: List[float] = []
    results: List[Dict[str, Any]] = []

    async def _run_all():
        for spec in TARPIT_20_TRIALS:
            received: List[Tuple[float, str]] = []

            def on_resp(txt: str):
                received.append((time.perf_counter(), txt))

            mgr = ConversationManager(response_callback=on_resp, **deps)
            mock_route = MagicMock()
            mock_route.destination = RouteTarget.LOCAL_QWEN
            mock_route.reason = "benchmark"
            mgr.hybrid_router.route = MagicMock(return_value=mock_route)

            async def hang_call(prompt, timeout_sec=None, **kwargs):
                if spec.hang_mode == "sleep_tarpit":
                    await asyncio.sleep(spec.hang_sec)
                    return "Mai raggiunto", MagicMock()
                elif spec.hang_mode == "sleep_and_raise":
                    await asyncio.sleep(spec.hang_sec)
                    raise TimeoutError("NPU timeout")
                else:
                    await asyncio.sleep(spec.hang_sec)
                    return "Risposta nominale.", MagicMock()

            mgr.hailo_client = MagicMock(generate=AsyncMock(side_effect=hang_call))

            t_start = time.perf_counter()
            ok = await mgr.process_input(spec.prompt, source="text")
            t_end = time.perf_counter()

            cb_time, resp_text = received[0]
            lat = cb_time - t_start
            latencies.append(lat)

            # Check invariants
            assert ok is True, f"Trial {spec.trial_id} failed"
            assert lat < 1.500, f"Trial {spec.trial_id} exceeded 1.500s: {lat:.4f}s"
            assert "{" not in resp_text and "}" not in resp_text

            results.append({
                "trial": spec.trial_id,
                "hang_sec": spec.hang_sec,
                "mode": spec.hang_mode,
                "latency_s": lat,
                "resp_preview": resp_text[:40]
            })
            print(f"[{spec.trial_id:02d}/20] Hang: {spec.hang_sec:4.1f}s | SLA Elapsed: {lat:.4f}s | OK | {resp_text[:45]}...")

    asyncio.run(_run_all())

    latencies.sort()
    min_lat = latencies[0]
    max_lat = latencies[-1]
    mean_lat = sum(latencies) / len(latencies)
    median_lat = latencies[len(latencies) // 2]
    p95_lat = latencies[int(len(latencies) * 0.95)]

    print("\n" + "-" * 80)
    print("EMPIRICAL BENCHMARK STATISTICAL SUMMARY")
    print("-" * 80)
    print(f"Total Trials Executed: {len(latencies)}/20 (100% Success)")
    print(f"Min Latency:           {min_lat*1000:.1f} ms ({min_lat:.4f}s)")
    print(f"Max Latency:           {max_lat*1000:.1f} ms ({max_lat:.4f}s)  [SLA Limit: 1500.0 ms]")
    print(f"Mean Latency:          {mean_lat*1000:.1f} ms ({mean_lat:.4f}s)")
    print(f"Median Latency:        {median_lat*1000:.1f} ms ({median_lat:.4f}s)")
    print(f"95th Percentile:       {p95_lat*1000:.1f} ms ({p95_lat:.4f}s)")
    print(f"Violations of SLA:     0 / 20 (0.00%)")
    print(f"Raw JSON Leaks:        0 / 20 (0.00%)")
    print(f"Unhandled Exceptions:  0 / 20 (0.00%)")
    print("-" * 80)
    print("VERDICT: ALL 20 TRIALS STRICTLY RESPECTED SLA < 1.500s")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    run_standalone_benchmark()
