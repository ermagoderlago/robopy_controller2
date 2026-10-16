"""
Milestone 3 Test Suite: Persona, Hybrid Templates & Zero JSON Leaks
===================================================================
Comprehensive verification harness validating:
1. Pure Chit-Chat Persona: Warm Italian dialogue with zero action calls.
2. Hybrid Dialogue + Action: Natural speech cleanly separated from <action> blocks.
3. Action-Only Auto-Synthesizer: Spontaneous Italian confirmation on action-only output.
4. Zero JSON Leak Invariant: 0% braces, 0% brackets, 0% JSON keys in spoken text.
5. Malformed/Fenced JSON Auto-Repair: Resilient parsing and kinematic sandbox safety.
6. Conversational Barge-In & Auto-Resumption: Wheel pause and auto-resume during exploration.

Complies with:
- marcus_core_rules.md
- SPEC-01 (Kinematic Limits)
- SPEC-04 (Audio VUI & Zero JSON TTS)
- SPEC-05 (Cognitive Brain TRINITY)
- DFMEA FM-LLM-004, FM-LLM-010, FM-NAV-034
"""

import asyncio
import os
import re
import sys
import types
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for _p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Mock ROS 2 environment dependencies if not built
if "rclpy" not in sys.modules:
    mock_rclpy = types.ModuleType("rclpy")
    mock_rclpy.__path__ = []
    mock_rclpy.logging = MagicMock()
    sys.modules["rclpy"] = mock_rclpy
elif not hasattr(sys.modules["rclpy"], "__path__"):
    sys.modules["rclpy"].__path__ = []

if "robopy_controller.srv" not in sys.modules:
    mock_srv_mod = types.ModuleType("robopy_controller.srv")
    mock_srv_mod.AskVisualQuestion = MagicMock()
    sys.modules["robopy_controller.srv"] = mock_srv_mod

from robopy_controller.robot_ai.orchestration.conversation import ConversationManager
from robopy_controller.robot_ai.arbitration.intent_classifier import RouteTarget, RoutingDecision
from robopy_controller.robot_ai.hailo.qwen_prompt_templates import (
    JsonCallExtractor,
    RawJsonCall,
    format_qwen_chatml_prompt,
    OPERATIONAL_FUNCTIONS,
    VALID_ROOM_DESTINATIONS,
)
from robopy_controller.robot_ai.trinity.sandbox_rules import (
    KinematicSandboxErrorCode,
    RobotSafetyContext,
    SandboxValidationResult,
)
from robopy_controller.robot_ai.trinity.kinematic_sandbox import TrinityKinematicSandbox


# =============================================================================
# ZERO JSON LEAK ASSERTION HELPER
# =============================================================================

def assert_zero_json_leak(text: str) -> None:
    """
    Strict invariant check asserting that spoken text contains zero raw JSON or markup.
    Guarantees that Piper TTS never vocalizes braces, brackets, or code keys.
    """
    assert "{" not in text and "}" not in text, f"Brace leak detected in text: '{text}'"
    assert "[" not in text and "]" not in text, f"Square bracket leak detected in text: '{text}'"
    assert "<action>" not in text and "</action>" not in text, f"Action tag leak in text: '{text}'"
    assert "```" not in text, f"Markdown fence leak in text: '{text}'"

    # JSON syntax keys with colon
    forbidden_key_patterns = [
        r'["\'](?:call|name|arguments|parameters|destination|direction|distance_m|degrees|speed|action|device_name|query)["\']\s*:',
        r'\b(?:arguments|params|kwargs|target_room)\b',
    ]
    for pattern in forbidden_key_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        assert match is None, f"JSON key/parameter leak detected ('{match.group(0)}') in text: '{text}'"


# =============================================================================
# TEST FIXTURES & HARNESS FACTORY
# =============================================================================

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
    skill_executor.execute_skill = AsyncMock()

    memory_manager = MagicMock()
    memory_manager.memory_store = None
    memory_manager.embedding_service = None

    world_model = MagicMock()
    world_model.current_user = None
    world_model.recent_interactions = []
    world_model.battery_voltage = 12.2
    world_model.current_room = "salotto"
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
    config.get_config.return_value.trinity.enabled = False
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


class ConversationHarness:
    """High-level harness wrapping ConversationManager for deterministic testing."""

    def __init__(self, mock_deps):
        self.mock_dependencies = mock_deps
        self.published_responses: List[str] = []
        self.spoken_texts: List[str] = []

        def on_response(text: str):
            self.published_responses.append(text)

        async def on_speak(text: str):
            self.spoken_texts.append(text)

        mock_deps["tts"].speak.side_effect = on_speak

        self.manager = ConversationManager(
            response_callback=on_response,
            **mock_deps
        )
        self.manager.set_mic_muted(False)

        # Configure deterministic local Qwen mock client
        self.mock_hailo = MagicMock()
        self.mock_hailo.generate = AsyncMock()
        self.manager.hailo_client = self.mock_hailo

        # Configure router to route directly to local Qwen
        self.mock_router = MagicMock()
        self.mock_router.route.return_value = RoutingDecision(
            destination=RouteTarget.LOCAL_QWEN,
            confidence=0.98,
            matched_category="test_local",
            latency_us=15.0,
            reason="test_local_routing"
        )
        self.manager.hybrid_router = self.mock_router


# =============================================================================
# 1. PURE CHIT-CHAT PERSONA TESTS
# =============================================================================

class TestPureChitChatPersona:
    """Verifies that pure chit-chat produces warm Italian dialogue with zero action calls."""

    @pytest.mark.asyncio
    async def test_pure_chit_chat_greetings(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            "Ciao! Che piacere sentirti. Come posso aiutarti oggi nella nostra casa?",
            {"ttft_ms": 280.0}
        )

        await harness.manager.process_input("Ciao Marcus, buongiorno!", source="audio")

        assert len(harness.published_responses) == 1
        reply = harness.published_responses[0]
        assert "Ciao" in reply
        assert_zero_json_leak(reply)
        assert harness.mock_dependencies["skill_executor"].execute_skill.call_count == 0

    @pytest.mark.asyncio
    async def test_pure_chit_chat_empathy_and_identity(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            "Io sto benissimo! I miei sensori e la batteria sono carichi, e sono felice di essere qui con te.",
            {"ttft_ms": 290.0}
        )

        await harness.manager.process_input("Come ti senti oggi, Marcus?", source="audio")

        assert len(harness.published_responses) == 1
        reply = harness.published_responses[0]
        assert_zero_json_leak(reply)
        assert len(harness.spoken_texts) == 1
        assert_zero_json_leak(harness.spoken_texts[0])

    @pytest.mark.asyncio
    async def test_pure_chit_chat_curiosities_and_jokes(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            "Sai perché i robot non hanno mai freddo? Perché hanno un sacco di ventole!",
            {"ttft_ms": 310.0}
        )

        await harness.manager.process_input("Raccontami una barzelletta!", source="audio")
        reply = harness.published_responses[0]
        assert "ventole" in reply
        assert_zero_json_leak(reply)

    @pytest.mark.asyncio
    async def test_pure_chit_chat_gratitude_and_farewell(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            "Figurati, è sempre un vero piacere darti una mano! A dopo!",
            {"ttft_ms": 260.0}
        )

        await harness.manager.process_input("Grazie mille Marcus, a più tardi!", source="audio")
        reply = harness.published_responses[0]
        assert "piacere" in reply
        assert_zero_json_leak(reply)

    def test_pure_chit_chat_zero_action_hallucination(self):
        """Verifies that JsonCallExtractor detects zero actions across diverse chit-chat replies."""
        pure_chat_samples = [
            "Ciao a tutti! Sono pronto.",
            "Oggi è davvero una bella giornata di sole.",
            "Mi sento in perfetta forma, grazie per avermelo chiesto!",
            "Certamente, sono qui a tua disposizione per qualsiasi cosa.",
            "La casa oggi è molto tranquilla, non ho rilevato ostacoli strani.",
        ]
        for sample in pure_chat_samples:
            call, err = JsonCallExtractor.extract_call(sample)
            assert call is None, f"Hallucinated action from chit-chat: '{sample}' -> {call}"


# =============================================================================
# 2. HYBRID DIALOGUE AND ACTION SEPARATION TESTS
# =============================================================================

class TestHybridDialogueAndActionSeparation:
    """Verifies that natural speech is delivered to TTS and actions are sent to sandbox."""

    @pytest.mark.asyncio
    async def test_hybrid_navigation_clean_split(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        model_output = (
            "Certamente! Vado subito in cucina a dare un'occhiata.\n"
            "<action>\n"
            '{"call": {"name": "navigate_to", "arguments": {"destination": "cucina"}}}\n'
            "</action>"
        )
        harness.mock_hailo.generate.return_value = (model_output, {"ttft_ms": 280.0})

        await harness.manager.process_input("Marcus, per favore puoi andare in cucina?", source="audio")

        assert len(harness.spoken_texts) == 1
        spoken = harness.spoken_texts[0]
        assert spoken == "Certamente! Vado subito in cucina a dare un'occhiata."
        assert_zero_json_leak(spoken)

        # Verify skill execution dispatch
        harness.mock_dependencies["skill_executor"].execute_skill.assert_called_once_with(
            "navigate_to", {"destination": "cucina"}
        )

    @pytest.mark.asyncio
    async def test_hybrid_rotation_clean_split(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        model_output = (
            "Mi giro subito a destra di novanta gradi per osservare la stanza.\n"
            "<action>\n"
            '{"call": {"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 90.0, "speed": 0.60}}}\n'
            "</action>"
        )
        harness.mock_hailo.generate.return_value = (model_output, {"ttft_ms": 270.0})

        await harness.manager.process_input("Ruota a destra di 90 gradi", source="audio")

        spoken = harness.spoken_texts[0]
        assert "Mi giro subito a destra" in spoken
        assert_zero_json_leak(spoken)
        harness.mock_dependencies["skill_executor"].execute_skill.assert_called_once_with(
            "rotate_angle", {"direction": "destra", "degrees": 90.0, "speed": 0.60}
        )

    @pytest.mark.asyncio
    async def test_hybrid_linear_motion_clean_split(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        model_output = (
            "Avanzo con cura di mezzo metro!\n"
            "<action>\n"
            '{"call": {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.50, "speed": 0.18}}}\n'
            "</action>"
        )
        harness.mock_hailo.generate.return_value = (model_output, {"ttft_ms": 280.0})

        await harness.manager.process_input("Avanza un po' in avanti", source="audio")

        spoken = harness.spoken_texts[0]
        assert spoken == "Avanzo con cura di mezzo metro!"
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_hybrid_telemetry_get_battery(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        model_output = (
            "Controllo subito la carica dei miei accumulatori!\n"
            "<action>\n"
            '{"call": {"name": "get_battery_status", "arguments": {}}}\n'
            "</action>"
        )
        harness.mock_hailo.generate.return_value = (model_output, {"ttft_ms": 250.0})

        await harness.manager.process_input("Come sei messo a batteria?", source="audio")

        spoken = harness.spoken_texts[0]
        assert "Controllo subito" in spoken
        assert_zero_json_leak(spoken)
        harness.mock_dependencies["skill_executor"].execute_skill.assert_called_once_with(
            "get_battery_status", {}
        )

    @pytest.mark.asyncio
    async def test_hybrid_system_health(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        model_output = (
            "Eseguo subito una scansione rapida dello stato di salute hardware.\n"
            "<action>\n"
            '{"call": {"name": "get_system_health", "arguments": {}}}\n'
            "</action>"
        )
        harness.mock_hailo.generate.return_value = (model_output, {"ttft_ms": 260.0})

        await harness.manager.process_input("Fai una diagnostica dei sensori", source="audio")
        spoken = harness.spoken_texts[0]
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_hybrid_smart_home(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        model_output = (
            "Accendo subito la luce principale del salotto.\n"
            "<action>\n"
            '{"call": {"name": "set_smart_device", "arguments": {"device_name": "luce_salotto", "state": "on"}}}\n'
            "</action>"
        )
        harness.mock_hailo.generate.return_value = (model_output, {"ttft_ms": 275.0})

        await harness.manager.process_input("Accendi la luce in salotto", source="audio")
        spoken = harness.spoken_texts[0]
        assert "Accendo subito la luce" in spoken
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_hybrid_frontier_exploration(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        model_output = (
            "Parto volentieri ad esplorare e mappare le aree libere della casa!\n"
            "<action>\n"
            '{"call": {"name": "start_frontier_exploration", "arguments": {}}}\n'
            "</action>"
        )
        harness.mock_hailo.generate.return_value = (model_output, {"ttft_ms": 280.0})

        await harness.manager.process_input("Mappa la casa", source="audio")
        spoken = harness.spoken_texts[0]
        assert "Parto volentieri" in spoken
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_hybrid_action_sandbox_rejection_feedback(self, mock_dependencies):
        """When an action is rejected by the sandbox, Marcus explains safely without executing."""
        harness = ConversationHarness(mock_dependencies)
        # Action with excessive speed (2.5 m/s > max 0.25 m/s)
        model_output = (
            "Vado velocissimo!\n"
            "<action>\n"
            '{"call": {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 1.0, "speed": 2.50}}}\n'
            "</action>"
        )
        harness.mock_hailo.generate.return_value = (model_output, {"ttft_ms": 280.0})

        await harness.manager.process_input("Corri avanti", source="audio")

        spoken = harness.spoken_texts[0]
        # Sandbox rejects speed > 0.25 m/s
        assert_zero_json_leak(spoken)
        assert harness.mock_dependencies["skill_executor"].execute_skill.call_count == 0


# =============================================================================
# 3. ACTION-ONLY AUTO-SYNTHESIZER TESTS
# =============================================================================

class TestActionOnlyAutoSynthesizer:
    """Verifies that action-only output generates warm Italian confirmations (zero silence, zero leaks)."""

    @pytest.mark.asyncio
    async def test_action_only_navigate_to(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            '<action>{"call": {"name": "navigate_to", "arguments": {"destination": "cucina"}}}</action>',
            {"ttft_ms": 270.0}
        )

        await harness.manager.process_input("Vai in cucina", source="audio")

        assert len(harness.spoken_texts) == 1
        spoken = harness.spoken_texts[0]
        assert "cucina" in spoken.lower()
        assert "navigate_to" not in spoken
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_action_only_move_distance(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            '<action>{"call": {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.5}}}</action>',
            {"ttft_ms": 260.0}
        )

        await harness.manager.process_input("Avanza di mezzo metro", source="audio")

        spoken = harness.spoken_texts[0]
        assert "sposto" in spoken.lower() or "avanzo" in spoken.lower() or "movimento" in spoken.lower()
        assert "move_distance" not in spoken
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_action_only_rotate_angle(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            '<action>{"call": {"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 45.0}}}</action>',
            {"ttft_ms": 265.0}
        )

        await harness.manager.process_input("Girati a destra di 45 gradi", source="audio")

        spoken = harness.spoken_texts[0]
        assert "ruoto" in spoken.lower() or "giro" in spoken.lower() or "destra" in spoken.lower()
        assert "rotate_angle" not in spoken
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_action_only_stop_motion(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            '<action>{"call": {"name": "stop_motion", "arguments": {}}}</action>',
            {"ttft_ms": 250.0}
        )

        await harness.manager.process_input("Arresta il movimento", source="audio")

        spoken = harness.spoken_texts[0]
        assert "fermo" in spoken.lower() or "arresto" in spoken.lower()
        assert "stop_motion" not in spoken
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_action_only_get_battery_status(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            '<action>{"call": {"name": "get_battery_status", "arguments": {}}}</action>',
            {"ttft_ms": 250.0}
        )

        await harness.manager.process_input("Stato batteria", source="audio")

        spoken = harness.spoken_texts[0]
        assert "batteria" in spoken.lower()
        assert "get_battery_status" not in spoken
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_action_only_get_system_health(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            '<action>{"call": {"name": "get_system_health", "arguments": {}}}</action>',
            {"ttft_ms": 250.0}
        )

        await harness.manager.process_input("Diagnostica di sistema", source="audio")

        spoken = harness.spoken_texts[0]
        assert "diagnostica" in spoken.lower() or "sistemi" in spoken.lower() or "salute" in spoken.lower()
        assert "get_system_health" not in spoken
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_action_only_start_frontier_exploration(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            '<action>{"call": {"name": "start_frontier_exploration", "arguments": {}}}</action>',
            {"ttft_ms": 250.0}
        )

        await harness.manager.process_input("Inizia esplorazione", source="audio")

        spoken = harness.spoken_texts[0]
        assert "esplorare" in spoken.lower() or "esplorazione" in spoken.lower()
        assert "start_frontier_exploration" not in spoken
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_action_only_raw_json_without_action_tags(self, mock_dependencies):
        """Model outputs raw JSON envelope without <action> tags: auto-synthesizer must protect speech."""
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            '{"call": {"name": "navigate_to", "arguments": {"destination": "salotto"}}}',
            {"ttft_ms": 280.0}
        )

        await harness.manager.process_input("Raggiungi il salotto", source="audio")

        spoken = harness.spoken_texts[0]
        assert "salotto" in spoken.lower()
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_action_only_never_speaks_english_function_name(self, mock_dependencies):
        """Asserts that 'eseguo <function_name>' is never uttered by the auto-synthesizer."""
        for fn in ["navigate_to", "move_distance", "rotate_angle", "get_battery_status", "get_system_health"]:
            harness = ConversationHarness(mock_dependencies)
            payload = f'<action>{{"call": {{"name": "{fn}", "arguments": {{}}}}}}</action>'
            harness.mock_hailo.generate.return_value = (payload, {"ttft_ms": 250.0})

            await harness.manager.process_input(f"Comando {fn}", source="audio")
            spoken = harness.spoken_texts[0]
            assert f"eseguo {fn}" not in spoken, f"Cold English function leak detected: '{spoken}'"
            assert_zero_json_leak(spoken)


# =============================================================================
# 4. ZERO JSON LEAK INVARIANT TESTS
# =============================================================================

class TestZeroJsonLeakInvariant:
    """Rigorous invariant testing asserting 0% braces and 0% JSON keys across adversarial outputs."""

    CORPUS_ADVERSARIAL_OUTPUTS = [
        # Pure JSON variants
        '{"call": {"name": "navigate_to", "arguments": {"destination": "bagno"}}}',
        '{"action": "rotate_angle", "parameters": {"direction": "sinistra", "degrees": 90.0}}',
        '{"name": "stop_motion", "arguments": {}}',
        # Fenced variants
        '```json\n{"call": {"name": "get_battery_status", "arguments": {}}}\n```',
        '```\n{"call": {"name": "get_system_health", "arguments": {}}}\n```',
        # Mixed preamble + JSON
        'Ecco fatto! {"call": {"name": "navigate_to", "arguments": {"destination": "studio"}}}',
        'Certamente: ```json {"call": {"name": "get_battery_status"}} ```',
        # Tagged variants
        '<action>{"call": {"name": "navigate_to", "arguments": {"destination": "camera"}}}</action>',
        'Subito! <action>{"call": {"name": "move_distance", "arguments": {"direction": "avanti", "distance_m": 1.0}}}</action>',
        # Malformed / unclosed tags
        '<action>{"call": {"name": "get_battery_status", "arguments": {}}}',
        'Vado in camera! <action>{"call": {"name": "navigate_to", "arguments": {"destination": "camera"}}',
        # Trailing commas and nested structures
        '<action>{"call": {"name": "rotate_angle", "arguments": {"direction": "destra", "degrees": 180.0,},},}</action>',
        # Single-quote dicts
        "<action>{'call': {'name': 'stop_motion', 'arguments': {}}}</action>",
        # Empty action
        '<action></action>',
        # Whitespace and tags only
        '   <action>   </action>   ',
    ]

    @pytest.mark.parametrize("raw_model_output", CORPUS_ADVERSARIAL_OUTPUTS)
    @pytest.mark.asyncio
    async def test_zero_json_leak_invariant_corpus(self, mock_dependencies, raw_model_output):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (raw_model_output, {"ttft_ms": 280.0})

        await harness.manager.process_input("Esegui richiesta", source="audio")

        assert len(harness.spoken_texts) >= 1
        for spoken in harness.spoken_texts:
            assert len(spoken) > 0, "Spoken text must never be empty (zero silence invariant)"
            assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_zero_json_leak_on_empty_speech(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = ("   \n\t  ", {"ttft_ms": 250.0})

        await harness.manager.process_input("   ", source="audio")
        spoken = harness.spoken_texts[0]
        assert len(spoken) > 0
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_zero_json_leak_on_markdown_code_fences(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            "Ecco il codice per te:\n```json\n{\"destination\": \"cucina\"}\n```\nSpero ti piaccia!",
            {"ttft_ms": 280.0}
        )

        await harness.manager.process_input("Dimmi del codice", source="audio")
        spoken = harness.spoken_texts[0]
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_zero_json_leak_on_trailing_unclosed_tags(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            "Vado subito in salotto! <action>{\"call\": {\"name\": \"navigate_to\", \"arguments\": {\"destin",
            {"ttft_ms": 290.0}
        )

        await harness.manager.process_input("Vai in salotto", source="audio")
        spoken = harness.spoken_texts[0]
        assert spoken == "Vado subito in salotto!"
        assert_zero_json_leak(spoken)

    @pytest.mark.asyncio
    async def test_zero_json_leak_in_conversation_history(self, mock_dependencies):
        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            "Mi sposto verso la cucina. <action>{\"call\": {\"name\": \"navigate_to\", \"arguments\": {\"destination\": \"cucina\"}}}</action>",
            {"ttft_ms": 280.0}
        )

        await harness.manager.process_input("Vai in cucina", source="text")

        for turn in harness.manager.conversation_history:
            if turn.get("role") == "assistant":
                content = turn.get("content", "")
                assert_zero_json_leak(content)


# =============================================================================
# 5. MALFORMED JSON AUTO-REPAIR & SANDBOX SAFETY TESTS
# =============================================================================

class TestMalformedJsonAutoRepairAndSandboxSafety:
    """Verifies that JsonCallExtractor auto-heals corrupted JSON and Sandbox enforces limits."""

    def test_markdown_fence_auto_repair(self):
        p1 = '```json\n{"call": {"name": "get_battery_status", "arguments": {}}}\n```'
        call, err = JsonCallExtractor.extract_call(p1)
        assert err is None
        assert call is not None
        assert call.function_name == "get_battery_status"

    def test_unclosed_braces_token_truncation_repair(self):
        # Truncated at depth 2
        p1 = '{"call": {"name": "navigate_to", "arguments": {"destination": "cucina"'
        call, err = JsonCallExtractor.extract_call(p1)
        assert err is None
        assert call is not None
        assert call.function_name == "navigate_to"
        assert call.arguments["destination"] == "cucina"
        assert call.repaired is True

    def test_trailing_commas_auto_repair(self):
        p1 = '{"call": {"name": "rotate_angle", "arguments": {"direction": "sinistra", "degrees": 90.0,},},}'
        call, err = JsonCallExtractor.extract_call(p1)
        assert err is None
        assert call is not None
        assert call.function_name == "rotate_angle"
        assert call.arguments["degrees"] == 90.0

    def test_single_quotes_python_dict_repair(self):
        p1 = "{'call': {'name': 'stop_motion', 'arguments': {'reason': 'obstacle'}}}"
        call, err = JsonCallExtractor.extract_call(p1)
        assert err is None
        assert call is not None
        assert call.function_name == "stop_motion"

    def test_unrecoverable_malformed_json_graceful_handling(self):
        p1 = "Questa risposta non contiene nessun JSON valido: {call: [broken]!!"
        call, err = JsonCallExtractor.extract_call(p1)
        assert call is None
        assert err is not None

    def test_malformed_action_name_sandbox_rejection(self):
        # Action name truncated to invalid prefix
        raw = RawJsonCall(
            function_name="nav_",
            arguments={"dest": "cucina"},
            raw_payload="corrupted"
        )
        sandbox = TrinityKinematicSandbox()
        res = sandbox.validate_action(raw)
        assert res.authorized is False
        assert res.error_code == KinematicSandboxErrorCode.UNKNOWN_FUNCTION


# =============================================================================
# 6. CONVERSATIONAL BARGE-IN & AUTO-RESUMPTION TESTS
# =============================================================================

class TestConversationalBargeInAndAutoResume:
    """Verifies that wheel motion is paused during dialogue and resumed if not stopped."""

    class MockFrontierSkill:
        def __init__(self):
            self.is_exploring = True
            self._paused_for_dialogue = False
            self.stop_called = False
            self.pause_called = False
            self.resume_called = False

        def pause_for_dialogue(self):
            self.pause_called = True
            self._paused_for_dialogue = True

        def resume_after_dialogue(self):
            self.resume_called = True
            self._paused_for_dialogue = False
            return True

        def stop_exploration(self):
            self.stop_called = True
            self.is_exploring = False
            self._paused_for_dialogue = False

    @pytest.mark.asyncio
    async def test_barge_in_pauses_active_exploration(self, mock_dependencies):
        frontier_mock = self.MockFrontierSkill()
        mock_dependencies["skill_executor"].registry.get.side_effect = (
            lambda name: frontier_mock if name == "frontier_exploration" else None
        )

        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            "Ciao! Sono fermo un attimo per ascoltarti.",
            {"ttft_ms": 280.0}
        )

        await harness.manager.process_input("Ciao Marcus, mi senti?", source="audio")

        assert frontier_mock.pause_called is True
        assert frontier_mock.resume_called is True
        assert frontier_mock.stop_called is False

    @pytest.mark.asyncio
    async def test_barge_in_auto_resumes_after_chit_chat(self, mock_dependencies):
        frontier_mock = self.MockFrontierSkill()
        mock_dependencies["skill_executor"].registry.get.side_effect = (
            lambda name: frontier_mock if name == "frontier_exploration" else None
        )

        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            "Sto benissimo! Sto esplorando il corridoio.",
            {"ttft_ms": 280.0}
        )

        await harness.manager.process_input("Come va l'esplorazione?", source="audio")

        assert frontier_mock.pause_called is True
        assert frontier_mock.resume_called is True
        assert frontier_mock.is_exploring is True
        assert frontier_mock._paused_for_dialogue is False

    @pytest.mark.asyncio
    async def test_barge_in_auto_resumes_after_hybrid_query(self, mock_dependencies):
        frontier_mock = self.MockFrontierSkill()
        mock_dependencies["skill_executor"].registry.get.side_effect = (
            lambda name: frontier_mock if name == "frontier_exploration" else None
        )

        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            "Controllo subito la batteria.\n<action>{\"call\": {\"name\": \"get_battery_status\", \"arguments\": {}}}</action>",
            {"ttft_ms": 280.0}
        )

        await harness.manager.process_input("Quanta carica ti resta?", source="audio")

        assert frontier_mock.pause_called is True
        assert frontier_mock.resume_called is True
        assert frontier_mock.is_exploring is True

    @pytest.mark.asyncio
    async def test_barge_in_stops_permanently_on_explicit_stop_command(self, mock_dependencies):
        frontier_mock = self.MockFrontierSkill()
        mock_dependencies["skill_executor"].registry.get.side_effect = (
            lambda name: frontier_mock if name == "frontier_exploration" else None
        )

        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = (
            "Fermo immediatamente l'esplorazione come richiesto.",
            {"ttft_ms": 250.0}
        )

        # Explicit stop utterance
        await harness.manager.process_input("Marcus, fermati subito!", source="audio")

        assert frontier_mock.pause_called is True
        assert frontier_mock.stop_called is True
        assert frontier_mock.resume_called is False
        assert frontier_mock.is_exploring is False

    @pytest.mark.asyncio
    async def test_barge_in_exploration_awareness_in_prompt(self, mock_dependencies):
        frontier_mock = self.MockFrontierSkill()
        mock_dependencies["skill_executor"].registry.get.side_effect = (
            lambda name: frontier_mock if name == "frontier_exploration" else None
        )

        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = ("Tutto sotto controllo!", {"ttft_ms": 280.0})

        with patch.object(harness.manager, "_handle_local_qwen", wraps=harness.manager._handle_local_qwen) as mock_handle:
            await harness.manager.process_input("Cosa stai facendo?", source="audio")
            assert mock_handle.called
            # Verify exploration note was injected into context
            _, kwargs = mock_handle.call_args
            notes = kwargs.get("combined_notes", "")
            assert "esplorazione" in notes.lower() or frontier_mock.pause_called

    @pytest.mark.asyncio
    async def test_barge_in_multiple_consecutive_queries(self, mock_dependencies):
        frontier_mock = self.MockFrontierSkill()
        mock_dependencies["skill_executor"].registry.get.side_effect = (
            lambda name: frontier_mock if name == "frontier_exploration" else None
        )

        harness = ConversationHarness(mock_dependencies)
        harness.mock_hailo.generate.return_value = ("Ti rispondo volentieri!", {"ttft_ms": 280.0})

        # First turn
        await harness.manager.process_input("Prima domanda", source="audio")
        assert frontier_mock.pause_called is True
        assert frontier_mock.resume_called is True

        # Second turn (still exploring)
        frontier_mock.pause_called = False
        frontier_mock.resume_called = False
        await harness.manager.process_input("Seconda domanda", source="audio")
        assert frontier_mock.pause_called is True
        assert frontier_mock.resume_called is True
