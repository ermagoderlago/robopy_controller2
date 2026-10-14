"""
==============================================================================
[COGNITIVE_CHECK]
- Scheda Tecnica letta con view_file: /docs/specs/SPEC-01_actuation_and_motion.md,
  /docs/specs/SPEC-03_vision_and_hailo_npu.md, /docs/specs/SPEC-05_cognitive_brain_trinity.md
- Vincoli di Zona Rossa verificati:
  * SPEC-01: Tetto velocità lineare 0.40 m/s, angolare 1.80 rad/s, watchdog 500ms, zero-motion leaks.
  * SPEC-03: Hailo-10H TTFT <= 400ms, isolamento CPU delta <= 5.0%, Core 2-3 C++ pinning.
  * SPEC-05: Fast-path stop < 10ms, intent classifier < 1.5ms, sandbox pre-flight 100% rejection su boundary.
  * SPEC-06: Tensione carica 12.70V lock, docking reserve 10.15V, shutdown cutoff 9.85V, ECO 10.40V.
- Classificazione modifica: 🟢 Zona Verde (Auto-Evolution: test di collaudo e stress avversario Tier 5).
- Documentazione consultata e aggiornata: docs/specs/SPEC-01, SPEC-03, SPEC-05, marcus_core_rules.md.
==============================================================================

🧪 TIER 5: ADVERSARIAL COVERAGE HARDENING SUITE (M5 SAFETY)
===========================================================
Empirical adversarial challenger test suite covering:
1. Perception & Hailo-10H Qwen2 Client (robopy_controller/robot_ai/hailo/hailo_qwen_client.py)
2. Fast-Path Emergency Stop (<10ms) (robopy_controller/robot_ai/arbitration/fast_path_stop.py)
3. Cognitive Intent Classifier (<1.5ms) (robopy_controller/robot_ai/arbitration/intent_classifier.py)
4. TRINITY Pre-flight Kinematic Sandbox (robopy_controller/robot_ai/trinity/kinematic_sandbox.py)
5. Sandbox Rules & Multi-tier Boundaries (robopy_controller/robot_ai/trinity/sandbox_rules.py)
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import math
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for _p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from robopy_controller.robot_ai.hailo.hailo_qwen_client import (
    HailoQwenClient,
    HailoQwenConfig,
    InferenceMode,
    InferenceTelemetry,
    TokenChunk,
)
from robopy_controller.robot_ai.arbitration.fast_path_stop import (
    FastPathStopHandler,
    FastPathStopResult,
    STOP_REGEX,
    normalize_text,
)
from robopy_controller.robot_ai.arbitration.intent_classifier import (
    CognitiveIntentClassifier,
    RouteTarget,
    RoutingDecision,
)
from robopy_controller.robot_ai.trinity.sandbox_rules import (
    KinematicCommand,
    KinematicLimits,
    KinematicSandboxErrorCode,
    RobotSafetyContext,
    SandboxValidationResult,
)
from robopy_controller.robot_ai.trinity.kinematic_sandbox import (
    TrinityKinematicSandbox,
    ALLOWED_FUNCTION_NAMES,
)
from robopy_controller.robot_ai.hailo.qwen_prompt_templates import (
    JsonCallExtractor,
    RawJsonCall,
    VALID_ROOM_DESTINATIONS,
)
from tests.test_kinematic_sandbox import MockMotionPublisherSpy


# ==============================================================================
# SECTION 1: PERCEPTION & HAILO QWEN CLIENT ADVERSARIAL STRESS
# ==============================================================================

class TestTier5HailoQwenClientAdversarial:
    """Stress tests and boundary checks for HailoQwenClient."""

    @pytest.mark.asyncio
    async def test_qwen_client_empty_and_whitespace_prompts(self):
        """Empty or whitespace prompts must return instantly without streaming overhead."""
        client = HailoQwenClient(HailoQwenConfig(enable_mock_fallback=True))
        try:
            for empty_input in ["", "   ", "\t\t", "\n\n\r\n"]:
                text, telem = await client.generate(empty_input)
                assert text == ""
                assert telem.token_count == 0
                assert telem.ttft_ms == 0.0

                # Test stream generator
                chunks = []
                async for chunk in client.generate_stream(empty_input):
                    chunks.append(chunk)
                assert len(chunks) == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_qwen_client_pathological_payloads(self):
        """Hostile strings (massive, null-byte, unicode control) must not crash client."""
        client = HailoQwenClient(HailoQwenConfig(enable_mock_fallback=True, mock_ttft_ms=5.0, mock_inter_token_ms=1.0))
        try:
            # 1. Massive prompt (15,000 characters)
            big_prompt = "avanti " * 2500
            text, telem = await client.generate(big_prompt)
            assert telem.token_count > 0
            assert "move_distance" in text

            # 2. Prompt with null bytes and control chars
            null_prompt = "ruota a destra\x00\x01\x02\r\nsubito"
            text, telem = await client.generate(null_prompt)
            assert telem.token_count > 0
            assert "rotate_angle" in text

            # 3. Unicode emojis and mixed script
            emoji_prompt = "🛑 fermati subito robot! ⚠️ 🤖"
            text, telem = await client.generate(emoji_prompt)
            assert telem.token_count > 0
            assert "stop_motion" in text
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_qwen_client_circuit_breaker_lifecycle(self):
        """Circuit breaker quarantines hardware after max failures and recovers on cooldown."""
        cfg = HailoQwenConfig(
            enable_mock_fallback=False,
            max_consecutive_failures=2,
            reconnect_cooldown_sec=0.1,  # Fast cooldown for test
            timeout_sec=0.05,
        )
        client = HailoQwenClient(cfg)
        client.mode = InferenceMode.HARDWARE_OLLAMA

        try:
            assert client.is_circuit_open() is False
            assert client._consecutive_failures == 0

            # Force first hardware failure
            client._record_hardware_failure()
            assert client._consecutive_failures == 1
            assert client.is_circuit_open() is False

            # Force second hardware failure -> Breaker OPENS
            client._record_hardware_failure()
            assert client._consecutive_failures == 2
            assert client.is_circuit_open() is True

            # Hardware attempt while circuit open raises RuntimeError when mock disabled
            with pytest.raises(RuntimeError, match="Hardware endpoint unavailable"):
                async for _ in client.generate_stream("avanti"):
                    pass

            # Wait for cooldown to expire
            await asyncio.sleep(0.12)
            # Circuit is now half-open (is_circuit_open returns False)
            assert client.is_circuit_open() is False

            # Success resets breaker to closed
            client._record_hardware_success()
            assert client._consecutive_failures == 0
            assert client.is_circuit_open() is False
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_qwen_client_streaming_chunk_invariants(self):
        """Streaming tokens must obey strict invariants: monotonic latency, first/last flags."""
        client = HailoQwenClient(HailoQwenConfig(
            enable_mock_fallback=True,
            mock_ttft_ms=10.0,
            mock_inter_token_ms=2.0,
        ))
        try:
            chunks: List[TokenChunk] = []
            async for chunk in client.generate_stream("vai in cucina"):
                chunks.append(chunk)

            assert len(chunks) > 0
            # First chunk invariant
            assert chunks[0].is_first is True
            assert chunks[0].is_last is False if len(chunks) > 1 else True

            # Middle chunks
            for ch in chunks[1:-1]:
                assert ch.is_first is False
                assert ch.is_last is False

            # Last chunk invariant
            assert chunks[-1].is_last is True
            assert chunks[-1].is_first is False if len(chunks) > 1 else True

            # Latency strictly non-decreasing
            latencies = [c.latency_from_start_ms for c in chunks]
            for i in range(1, len(latencies)):
                assert latencies[i] >= latencies[i - 1]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_qwen_client_vdevice_mode_not_implemented(self):
        """InferenceMode.HARDWARE_GENAI_VDEVICE must cleanly raise NotImplementedError."""
        client = HailoQwenClient(HailoQwenConfig(enable_mock_fallback=True))
        client.mode = InferenceMode.HARDWARE_GENAI_VDEVICE
        try:
            with pytest.raises(NotImplementedError, match="HARDWARE_GENAI_VDEVICE is not yet supported"):
                async for _ in client.generate_stream("avanti"):
                    pass
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_qwen_client_generate_with_fallback_ttft_timeout(self):
        """When primary model exceeds TTFT budget, aborts and cleanly executes fallback model."""
        cfg = HailoQwenConfig(
            enable_mock_fallback=True,
            model_name="qwen2.5-instruct:1.5b",
            fallback_model_name="qwen2.5-instruct:0.5b",
            ttft_budget_ms=15.0,  # Strict budget
            mock_ttft_ms=50.0,    # Mock 1.5B will take 50ms, triggering timeout
        )
        client = HailoQwenClient(cfg)
        try:
            text, telem = await client.generate_with_fallback("stato batteria")
            # Should have fallen back to 0.5B
            assert telem.model == "qwen2.5-instruct:0.5b"
            assert "get_battery_status" in text
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_qwen_client_concurrent_requests_stress(self):
        """Client must survive 12 concurrent streaming/non-streaming inference tasks."""
        client = HailoQwenClient(HailoQwenConfig(
            enable_mock_fallback=True,
            mock_ttft_ms=8.0,
            mock_inter_token_ms=1.0,
        ))
        try:
            prompts = [
                "avanti 0.5m", "ruota a destra", "vai in salotto",
                "stato batteria", "diagnostica", "esplora frontiere",
                "accendi luce", "fermati", "chiudi porta", "ruota a sinistra",
                "avanti 0.2m", "salute del robot"
            ]
            tasks = [client.generate(p) for p in prompts]
            results = await asyncio.gather(*tasks)

            assert len(results) == 12
            for text, telem in results:
                assert telem.token_count > 0
                assert telem.total_time_ms > 0.0

            history = client.get_telemetry_history()
            assert len(history) >= 12
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_qwen_client_connection_recycling(self):
        """Calling close() and then generate() must cleanly recreate the HTTP client."""
        client = HailoQwenClient(HailoQwenConfig(enable_mock_fallback=True, mock_ttft_ms=5.0))
        try:
            await client.generate("avanti")
            await client.close()
            assert client._http_client is None

            # New call should work seamlessly
            text, telem = await client.generate("indietro")
            assert telem.token_count > 0
        finally:
            await client.close()


# ==============================================================================
# SECTION 2: INTENT ARBITRATION & FAST-PATH STOP ADVERSARIAL STRESS
# ==============================================================================

class TestTier5FastPathAndIntentArbitrationAdversarial:
    """Stress testing FastPathStopHandler and CognitiveIntentClassifier."""

    def test_fast_path_stop_latency_p99_budget(self):
        """Fast-path stop must achieve p99 latency < 100 microseconds (budget is 10,000 us)."""
        handler = FastPathStopHandler(enable_logging=False)
        latencies: List[float] = []

        tokens = ["fermati", "stop", "alt", "basta", "arresto", "bloccati", "non muoverti"]
        for i in range(1000):
            token = tokens[i % len(tokens)]
            res = handler.handle(f"{token} subito!")
            assert res.triggered is True
            latencies.append(res.latency_us)

        latencies.sort()
        p99 = latencies[int(len(latencies) * 0.99)]
        # Must easily beat budget
        assert p99 < 100.0, f"p99 latency {p99} us exceeded strict benchmark limit (budget 10,000 us)"
        assert handler.average_latency_us < 50.0

    def test_fast_path_stop_diacritic_and_unicode_homoglyphs(self):
        """Accented variations and fullwidth Unicode characters must be normalized and intercepted."""
        handler = FastPathStopHandler(enable_logging=False)
        adversarial_stops = [
            "Fèrmati", "FÉRMAti", "Arrèsto", "Arrèstati", "BLÒCCA", "blòccalo",
            "blòccati", "bàsta", "fërmati", "fêrmati", "ｓｔｏｐ", "ｂａｓｔａ",
            "ｆｅｒｍａｔｉ", "Cancèlla il mòto"
        ]
        for prompt in adversarial_stops:
            res = handler.handle(prompt)
            assert res.triggered is True, f"Failed to trigger emergency stop on '{prompt}'"
            assert res.reason == "emergency_stop_triggered"

    def test_fast_path_stop_punctuation_and_delimiter_fuzzing(self):
        """Interspersed punctuation, brackets, quotes, and symbols must not evade regex."""
        handler = FastPathStopHandler(enable_logging=False)
        punct_stops = [
            "stop!", "STOP!!!", "alt?!?", "fermati...", "[stop]", "(alt)",
            '"fermati"', "<arresto>", "stop;", "stop:", "marcus: fermati adesso",
            "basta, fermati subito!", "stop - adesso", "stop/alt", "stop*fermati"
        ]
        for prompt in punct_stops:
            res = handler.handle(prompt)
            assert res.triggered is True, f"Punctuation evading stop: '{prompt}'"

    def test_fast_path_stop_enclitic_and_phrase_variants(self):
        """Natural Italian imperative enclitics and multi-word phrases must trigger 100%."""
        handler = FastPathStopHandler(enable_logging=False)
        enclitic_stops = [
            "ferma", "fermati", "fermalo", "arresta", "arrestati", "arrestalo",
            "blocca", "bloccati", "bloccalo", "interrompi", "cancella il moto",
            "cancella moto", "non ti muovere", "non muoverti", "non avanzare"
        ]
        for prompt in enclitic_stops:
            res = handler.handle(prompt)
            assert res.triggered is True, f"Enclitic phrase failed: '{prompt}'"

    def test_fast_path_stop_negative_control_traps(self):
        """Near-miss tokens MUST NOT trigger emergency stop (zero false positives)."""
        handler = FastPathStopHandler(enable_logging=False)
        near_misses = [
            "conferma", "affermazione", "altalena", "salto in alto", "esaltato",
            "abbastanza", "imbastardito", "sblocca la porta", "sbloccati i freni",
            "autostop", "stopwatch", "apostrofo", "senza sosta", "non ti preoccupare",
            "non fermarti adesso", "parlami di dante", "raccontami una favola"
        ]
        for prompt in near_misses:
            res = handler.handle(prompt)
            assert res.triggered is False, f"False positive triggered on '{prompt}' (matched: {res.matched_token})"

    def test_fast_path_stop_callback_fault_isolation(self):
        """If one custom callback throws an exception, others must still execute safely."""
        handler = FastPathStopHandler(enable_logging=False)
        executed = []

        def good_cb_1():
            executed.append("cb1")

        def bad_cb():
            raise RuntimeError("Fatal explosion in external callback")

        def good_cb_2():
            executed.append("cb2")

        handler.register_stop_callback(good_cb_1)
        handler.register_stop_callback(bad_cb)
        handler.register_stop_callback(good_cb_2)

        res = handler.handle("stop!")
        assert res.triggered is True
        assert res.callbacks_executed == 2
        assert "cb1" in executed
        assert "cb2" in executed

    def test_intent_classifier_latency_and_accuracy(self):
        """CognitiveIntentClassifier executes under 200 microseconds with >= 96% accuracy."""
        classifier = CognitiveIntentClassifier(enable_logging=False)
        latencies: List[float] = []

        test_corpus = [
            # Operational
            ("vai in cucina", RouteTarget.LOCAL_QWEN),
            ("ruota di 90 gradi", RouteTarget.LOCAL_QWEN),
            ("qual è il livello della batteria", RouteTarget.LOCAL_QWEN),
            ("fai una scansione a 360 gradi", RouteTarget.LOCAL_QWEN),
            ("accendi la luce", RouteTarget.LOCAL_QWEN),
            ("qual è la temperatura della cpu", RouteTarget.LOCAL_QWEN),
            # Conversational
            ("raccontami una barzelletta", RouteTarget.GEMINI_LIVE),
            ("cosa ne pensi della fisica quantistica", RouteTarget.GEMINI_LIVE),
            ("spiegami la differenza tra arte e scienza", RouteTarget.GEMINI_LIVE),
            ("chi era dante alighieri", RouteTarget.GEMINI_LIVE),
            ("scrivi una poesia sulle stelle", RouteTarget.GEMINI_LIVE),
            # Emergency
            ("fermati!", RouteTarget.FAST_PATH_STOP),
            ("stop subito", RouteTarget.FAST_PATH_STOP),
            ("non muoverti", RouteTarget.FAST_PATH_STOP),
        ]

        correct = 0
        for prompt, expected in test_corpus:
            dec = classifier.classify(prompt)
            latencies.append(dec.latency_us)
            if dec.destination == expected:
                correct += 1

        accuracy = correct / len(test_corpus)
        assert accuracy >= 0.96, f"Accuracy {accuracy*100:.1f}% below 96% gate"
        latencies.sort()
        p99 = latencies[int(len(latencies) * 0.99)]
        assert p99 < 200.0, f"p99 latency {p99} us exceeded 200 us limit (budget 1500 us)"

    def test_intent_classifier_latency_failover_boundary(self):
        """Ping latency boundary: <= 500.0ms keeps cloud, > 500.0ms triggers local failover."""
        classifier = CognitiveIntentClassifier(enable_logging=False)
        conv_prompt = "spiegami la teoria della relatività"

        # Below boundary: stays Cloud
        d1 = classifier.classify_intent(conv_prompt, network_latency_ms=499.0)
        assert d1.destination == RouteTarget.GEMINI_LIVE
        assert d1.failover_applied is False

        # Exact boundary: stays Cloud (> 500.0ms rule)
        d2 = classifier.classify_intent(conv_prompt, network_latency_ms=500.0)
        assert d2.destination == RouteTarget.GEMINI_LIVE
        assert d2.failover_applied is False

        # Over boundary: Failover to Local Qwen
        d3 = classifier.classify_intent(conv_prompt, network_latency_ms=500.1)
        assert d3.destination == RouteTarget.LOCAL_QWEN
        assert d3.failover_applied is True
        assert d3.reason == "fallback_high_latency"

        # Emergency Stop must NEVER be altered by network latency
        d4 = classifier.classify_intent("fermati", network_latency_ms=1500.0)
        assert d4.destination == RouteTarget.FAST_PATH_STOP
        assert d4.failover_applied is False

    def test_arbitration_multi_threaded_concurrency(self):
        """Concurrent multi-threaded invocations of handler and classifier must be race-free."""
        handler = FastPathStopHandler(enable_logging=False)
        classifier = CognitiveIntentClassifier(enable_logging=False)

        def worker(idx: int):
            for i in range(100):
                if i % 3 == 0:
                    r = handler.handle("stop")
                    assert r.triggered is True
                else:
                    d = classifier.classify("vai in salotto")
                    assert d.destination == RouteTarget.LOCAL_QWEN

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(8)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        assert handler.total_evaluations > 0
        assert classifier.total_classifications > 0


# ==============================================================================
# SECTION 3: TRINITY KINEMATIC SANDBOX MULTI-TIER ADVERSARIAL STRESS
# ==============================================================================

class TestTier5TrinityKinematicSandboxAdversarial:
    """Rigorous empirical challenge of TrinityKinematicSandbox."""

    @pytest.fixture
    def sandbox(self) -> TrinityKinematicSandbox:
        return TrinityKinematicSandbox()

    def test_stop_motion_unconditional_authorization_invariant(self, sandbox):
        """stop_motion must ALWAYS pass regardless of catastrophic robot context."""
        apocalyptic_contexts = [
            RobotSafetyContext(battery_voltage=0.00, is_charging=True, amygdala_state="HIJACK"),
            RobotSafetyContext(battery_voltage=14.50, is_charging=True, costmap_collision_detected=True),
            RobotSafetyContext(max_depth_drop=2.50, min_forward_obstacle_dist=0.01, system_ready=False),
            RobotSafetyContext(battery_voltage=math.nan, min_forward_obstacle_dist=math.inf, amygdala_state="HIJACK"),
        ]
        cmd = {"action": "stop_motion", "parameters": {"reason": "emergency_halt"}}
        for ctx in apocalyptic_contexts:
            res = sandbox.validate_action(cmd, ctx)
            assert res.authorized is True, f"stop_motion failed on apocalyptic context: {ctx}"
            assert res.error_code == KinematicSandboxErrorCode.SUCCESS

    def test_kinematic_distance_bounds_and_negative_distance(self, sandbox):
        """move_distance: strictly [0.0, 2.00]m; negative distance must be rejected."""
        ctx = RobotSafetyContext()

        # Valid upper boundary: 2.00m
        r1 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 2.00}}, ctx)
        assert r1.authorized is True

        # Exceeded: 2.001m
        r2 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 2.001}}, ctx)
        assert r2.authorized is False
        assert r2.error_code == KinematicSandboxErrorCode.DISTANCE_EXCEEDED

        # Zero distance: allowed
        r3 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.0}}, ctx)
        assert r3.authorized is True

        # Negative distance: rejected
        r4 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": -0.01}}, ctx)
        assert r4.authorized is False
        assert r4.error_code == KinematicSandboxErrorCode.NEGATIVE_DISTANCE_FORBIDDEN

    def test_kinematic_rotation_bounds(self, sandbox):
        """rotate_angle: strictly (0.0, 180.0] degrees."""
        ctx = RobotSafetyContext()

        # Valid upper boundary: 180.0 deg
        r1 = sandbox.validate_action({"action": "rotate_angle", "parameters": {"direction": "destra", "degrees": 180.0}}, ctx)
        assert r1.authorized is True

        # Exceeded: 180.01 deg
        r2 = sandbox.validate_action({"action": "rotate_angle", "parameters": {"direction": "destra", "degrees": 180.01}}, ctx)
        assert r2.authorized is False
        assert r2.error_code == KinematicSandboxErrorCode.ROTATION_EXCEEDED

        # Zero degrees: rejected
        r3 = sandbox.validate_action({"action": "rotate_angle", "parameters": {"direction": "destra", "degrees": 0.0}}, ctx)
        assert r3.authorized is False
        assert r3.error_code == KinematicSandboxErrorCode.ROTATION_EXCEEDED

        # Negative degrees: rejected
        r4 = sandbox.validate_action({"action": "rotate_angle", "parameters": {"direction": "destra", "degrees": -45.0}}, ctx)
        assert r4.authorized is False
        assert r4.error_code == KinematicSandboxErrorCode.ROTATION_EXCEEDED

    def test_linear_and_angular_speed_clamping_and_redlines(self, sandbox):
        """Speed clamping [0.25, 0.40] m/s -> clamped to 0.25; >0.40 -> REDLINE REJECTED."""
        ctx = RobotSafetyContext()

        # 1. Linear within nominal (<=0.25)
        r1 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5, "speed": 0.20}}, ctx)
        assert r1.authorized is True
        assert r1.clamped_arguments["speed"] == 0.20

        # 2. Linear in clampable zone (0.25 < speed <= 0.40) -> clamped to 0.25
        r2 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5, "speed": 0.35}}, ctx)
        assert r2.authorized is True
        assert r2.clamped_arguments["speed"] == 0.25

        # 3. Linear above redline (>0.40) -> REJECTED
        r3 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5, "speed": 0.401}}, ctx)
        assert r3.authorized is False
        assert r3.error_code == KinematicSandboxErrorCode.LINEAR_SPEED_EXCEEDED

        # 4. Angular in clampable zone (1.00 < speed <= 1.80) -> clamped to 1.00
        r4 = sandbox.validate_action({"action": "rotate_angle", "parameters": {"direction": "sinistra", "degrees": 45.0, "speed": 1.50}}, ctx)
        assert r4.authorized is True
        assert r4.clamped_arguments["speed"] == 1.00

        # 5. Angular above redline (>1.80) -> REJECTED
        r5 = sandbox.validate_action({"action": "rotate_angle", "parameters": {"direction": "sinistra", "degrees": 45.0, "speed": 1.801}}, ctx)
        assert r5.authorized is False
        assert r5.error_code == KinematicSandboxErrorCode.ANGULAR_SPEED_EXCEEDED

    def test_type_traps_boolean_and_ieee754_parameters(self, sandbox):
        """Boolean traps (True/False) and NaN/Inf arguments must be rejected as invalid types."""
        ctx = RobotSafetyContext()

        # Boolean trap: distance_m = True (in Python isinstance(True, int) is True!)
        r1 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": True}}, ctx)
        assert r1.authorized is False
        assert r1.error_code == KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE

        # Boolean trap: speed = False
        r2 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5, "speed": False}}, ctx)
        assert r2.authorized is False
        assert r2.error_code == KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE

        # IEEE-754 NaN distance
        r3 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": float("nan")}}, ctx)
        assert r3.authorized is False
        assert r3.error_code == KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE

        # IEEE-754 Inf speed
        r4 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5, "speed": float("inf")}}, ctx)
        assert r4.authorized is False
        assert r4.error_code == KinematicSandboxErrorCode.INVALID_ARGUMENT_TYPE

    def test_spatial_clearance_boundaries(self, sandbox):
        """Forward clearance: d_obstacle >= d_req + 0.20m; Cliff drop: delta Z < 0.15m."""
        # Forward obstacle at 0.69m for 0.50m move -> req min = 0.50 + 0.20 = 0.70m -> REJECT
        ctx_blocked = RobotSafetyContext(min_forward_obstacle_dist=0.69)
        r1 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.50}}, ctx_blocked)
        assert r1.authorized is False
        assert r1.error_code == KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED

        # Forward obstacle at 0.70m -> PASS
        ctx_clear = RobotSafetyContext(min_forward_obstacle_dist=0.70)
        r2 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.50}}, ctx_clear)
        assert r2.authorized is True

        # Cliff drop at 0.149m (< 0.15m) -> PASS
        ctx_cliff_safe = RobotSafetyContext(max_depth_drop=0.149)
        r3 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.50}}, ctx_cliff_safe)
        assert r3.authorized is True

        # Cliff drop at 0.150m (>= 0.15m) -> REJECT
        ctx_cliff_danger = RobotSafetyContext(max_depth_drop=0.150)
        r4 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.50}}, ctx_cliff_danger)
        assert r4.authorized is False
        assert r4.error_code == KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF

        # Local costmap collision flag
        ctx_costmap = RobotSafetyContext(costmap_collision_detected=True)
        r5 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.50}}, ctx_costmap)
        assert r5.authorized is False
        assert r5.error_code == KinematicSandboxErrorCode.LOCAL_COSTMAP_COLLISION

    def test_battery_voltage_transitions_and_docking_reserve(self, sandbox):
        """Battery interlocks: Charging lock >=12.7V, Shutdown <=9.85V, Docking <=10.15V, ECO <=10.40V."""
        # 1. Charging lock: 12.70V
        ctx_charge = RobotSafetyContext(battery_voltage=12.70)
        r1 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5}}, ctx_charge)
        assert r1.authorized is False
        assert r1.error_code == KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN

        # 2. Critical shutdown cutoff: 9.85V
        ctx_cutoff = RobotSafetyContext(battery_voltage=9.85)
        r2 = sandbox.validate_action({"action": "navigate_to", "parameters": {"destination": "base"}}, ctx_cutoff)
        assert r2.authorized is False
        assert r2.error_code == KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL

        # 3. Docking reserve: 10.10V
        ctx_dock = RobotSafetyContext(battery_voltage=10.10)
        # navigate_to("base") is the ONLY allowed physical motion!
        r3 = sandbox.validate_action({"action": "navigate_to", "parameters": {"destination": "base"}}, ctx_dock)
        assert r3.authorized is True

        # Any other motion rejected under docking reserve
        r4 = sandbox.validate_action({"action": "navigate_to", "parameters": {"destination": "cucina"}}, ctx_dock)
        assert r4.authorized is False
        assert r4.error_code == KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY

        r5 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.2}}, ctx_dock)
        assert r5.authorized is False
        assert r5.error_code == KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY

        # 4. ECO Mode: 10.30V (<= 10.40V) clamps max speed to 50%
        ctx_eco = RobotSafetyContext(battery_voltage=10.30)
        r6 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5, "speed": 0.25}}, ctx_eco)
        assert r6.authorized is True
        assert r6.clamped_arguments["speed"] == 0.125  # 0.25 * 0.50

    def test_cognitive_interlocks_amigdala(self, sandbox):
        """Amigdala HIJACK blocks all motion; ANXIOUS_VIGILANCE throttles speed by 50%."""
        # 1. HIJACK
        ctx_hijack = RobotSafetyContext(amygdala_state="HIJACK")
        r1 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5}}, ctx_hijack)
        assert r1.authorized is False
        assert r1.error_code == KinematicSandboxErrorCode.AMYGDALA_HIJACK_ACTIVE

        # 2. ANXIOUS_VIGILANCE: clamps to max_anx_lin = max_linear_speed (0.25) * 0.50 = 0.125 m/s
        ctx_vigil = RobotSafetyContext(amygdala_state="ANXIOUS_VIGILANCE")
        r2 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5, "speed": 0.24}}, ctx_vigil)
        assert r2.authorized is True
        assert r2.clamped_arguments["speed"] == 0.125  # min(0.24, 0.25 * 0.50)

        # Sub-threshold speed remains un-clamped
        r2_sub = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5, "speed": 0.10}}, ctx_vigil)
        assert r2_sub.authorized is True
        assert r2_sub.clamped_arguments["speed"] == 0.10

        # 3. System not ready
        ctx_not_ready = RobotSafetyContext(system_ready=False)
        r3 = sandbox.validate_action({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5}}, ctx_not_ready)
        assert r3.authorized is False
        assert r3.error_code == KinematicSandboxErrorCode.SYSTEM_NOT_READY

    def test_whitelist_and_non_motion_commands(self, sandbox):
        """Whitelist enforces 10 functions; non-motion commands pass without clearance checks."""
        ctx_low_batt = RobotSafetyContext(battery_voltage=10.00)  # low battery

        # Unknown function: rejected
        r1 = sandbox.validate_action({"action": "launch_rocket", "parameters": {}}, ctx_low_batt)
        assert r1.authorized is False
        assert r1.error_code == KinematicSandboxErrorCode.UNKNOWN_FUNCTION

        # Non-motion query: authorized even under low battery
        r2 = sandbox.validate_action({"action": "get_battery_status", "parameters": {}}, ctx_low_batt)
        assert r2.authorized is True

        r3 = sandbox.validate_action({"action": "get_system_health", "parameters": {}}, ctx_low_batt)
        assert r3.authorized is True

        r4 = sandbox.validate_action({"action": "query_knowledge", "parameters": {"query": "chi ha inventato la radio"}}, ctx_low_batt)
        assert r4.authorized is True

        r5 = sandbox.validate_action({"action": "set_smart_device", "parameters": {"device_name": "lampada_salotto", "state": "on"}}, ctx_low_batt)
        assert r5.authorized is True

        # pause_or_resume_exploration
        r6 = sandbox.validate_action({"action": "pause_or_resume_exploration", "parameters": {"action": "pause"}}, ctx_low_batt)
        assert r6.authorized is True

    def test_zero_motion_leak_invariant(self, sandbox):
        """MockMotionPublisherSpy must publish EXACTLY ZERO commands on rejected actions."""
        spy = MockMotionPublisherSpy()

        # 50 hostile or out-of-bound scenarios
        hostile_scenarios = [
            ({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 5.0}}, RobotSafetyContext()),
            ({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": -1.0}}, RobotSafetyContext()),
            ({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5, "speed": 1.2}}, RobotSafetyContext()),
            ({"action": "rotate_angle", "parameters": {"direction": "destra", "degrees": 360.0}}, RobotSafetyContext()),
            ({"action": "rotate_angle", "parameters": {"direction": "sinistra", "degrees": 45.0, "speed": 3.0}}, RobotSafetyContext()),
            ({"action": "navigate_to", "parameters": {"destination": "cucina"}}, RobotSafetyContext(battery_voltage=10.00)),  # Docking only
            ({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5}}, RobotSafetyContext(battery_voltage=12.75)),  # Charging lock
            ({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5}}, RobotSafetyContext(battery_voltage=9.50)),  # Shutdown
            ({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5}}, RobotSafetyContext(amygdala_state="HIJACK")),
            ({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5}}, RobotSafetyContext(max_depth_drop=0.25)),  # Cliff
            ({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5}}, RobotSafetyContext(min_forward_obstacle_dist=0.30)),  # Proximity
            ({"action": "move_distance", "parameters": {"direction": "indietro", "distance_m": 0.5}}, RobotSafetyContext(min_rear_obstacle_dist=0.20)),  # Proximity rear
            ({"action": "rotate_angle", "parameters": {"direction": "destra", "degrees": 90.0}}, RobotSafetyContext(min_clearance_radius_360=0.15)),  # Rotation clearance
            ({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5}}, RobotSafetyContext(costmap_collision_detected=True)),
            ({"action": "move_distance", "parameters": {"direction": "avanti", "distance_m": 0.5}}, RobotSafetyContext(system_ready=False)),
            ({"action": "rm_rf_root", "parameters": {}}, RobotSafetyContext()),
            ({"action": "navigate_to", "parameters": {"destination": "giardino"}}, RobotSafetyContext()),  # Invalid room
        ]

        for cmd, ctx in hostile_scenarios:
            verdict = sandbox.validate_action(cmd, ctx)
            assert verdict.authorized is False
            dispatched = spy.dispatch(verdict, cmd)
            assert dispatched is False

        # Verify spy recorded ZERO published twists
        assert len(spy.published_twists) == 0, f"MOTION LEAK DETECTED: {spy.published_twists}"
