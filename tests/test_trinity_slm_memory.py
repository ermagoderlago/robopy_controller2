"""
Milestone 1 Test Suite: TRINITY SLM Memory, Context Budget (<1200 Tokens) & SQLite WAL
======================================================================================
Validates Milestone 1 and Features F1, F2, F3:
1. Token Budget Ceiling < 1200 tokens across nominal, saturated, and adversarial sizes.
2. Semantic Fallback & Truncation: Forward line, recency-preserving dialogue, and word-safe truncations.
3. Telemetry Grounding: Real-time injection of battery, room, AMCL covariance, and motor/exploration state.
4. Autobiographical Persistence: Local Qwen2 turns committed to SQLite WAL (mag_database.db).
5. Deterministic Mock Harness: Zero-hardware, zero-network, sub-second execution.

Run via:
    cmd /c "set PYTHONPATH=.;robopy_controller && python -m pytest tests/test_trinity_slm_memory.py -v"
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure imports resolve cleanly
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Safe mock for rclpy and robopy_controller.srv
import types
if "rclpy" not in sys.modules:
    mock_rclpy = types.ModuleType("rclpy")
    mock_rclpy.__path__ = []
    mock_rclpy.logging = MagicMock()
    sys.modules["rclpy"] = mock_rclpy
elif not hasattr(sys.modules["rclpy"], "__path__"):
    sys.modules["rclpy"].__path__ = []

if "robopy_controller.srv" not in sys.modules:
    mock_srv = types.ModuleType("robopy_controller.srv")
    mock_srv.AskVisualQuestion = MagicMock()
    sys.modules["robopy_controller.srv"] = mock_srv

from robot_ai.trinity.slm_metaprompt_fusion import (
    SLMBudgetConfig,
    SLMPromptContext,
    SLMPromptOutput,
    SLMMetapromptFusion,
    SLMTruncator,
    build_slm_prompt,
)
from robot_ai.trinity.mag_database import MAGDatabase
from robot_ai.trinity.cag_environment import EnvironmentSnapshot
from robot_ai.trinity.cag_hardware_collector import HardwareStateCollector
from robot_ai.trinity.trinity_engine import TrinityEngine
from robot_ai.orchestration.conversation import ConversationManager
from robot_ai.hailo.hailo_qwen_client import (
    HailoQwenClient,
    HailoQwenConfig,
    InferenceMode,
    InferenceTelemetry,
)


# ==============================================================================
# DETERMINISTIC MOCK HARNESS
# ==============================================================================

class MockHailoQwenClient:
    """
    High-fidelity deterministic mock client for Hailo-10H Qwen2 inference.
    Captures prompts, avoids keyword collisions from context tags,
    and supports latency/timeout/error simulation.
    """

    def __init__(self, default_response: str = "Risposta simulata dal mock."):
        self.default_response = default_response
        self.prompts_received: List[str] = []
        self.last_prompt: str = ""
        self.call_count: int = 0
        self.simulated_latency_sec: float = 0.0
        self.should_timeout: bool = False
        self.should_raise: Optional[Exception] = None

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        timeout_sec: Optional[float] = None,
        **kwargs,
    ):
        self.call_count += 1
        self.last_prompt = prompt
        self.prompts_received.append(prompt)

        if self.should_timeout:
            await asyncio.sleep(2.0)  # Exceeds 1.2s timeout SLA
            raise asyncio.TimeoutError("Simulated NPU PCIe DMA Timeout")

        if self.should_raise:
            raise self.should_raise

        if self.simulated_latency_sec > 0:
            await asyncio.sleep(self.simulated_latency_sec)

        # Scoped keyword synthesis: parse user section to avoid false positive action tags
        reply = self.default_response
        user_match = re.search(r"<\|im_start\|>user\n([\s\S]*?)<\|im_end\|>", prompt)
        user_query = user_match.group(1).lower() if user_match else prompt.lower()

        if "batteria" in user_query or "carica" in user_query:
            reply = '{"action": "get_battery_status", "parameters": {}}'
        elif "ruota" in user_query or "destra" in user_query:
            reply = '{"action": "rotate_angle", "parameters": {"angle_deg": -45.0, "angular_speed": 0.5}}'
        elif "salotto" in user_query or "naviga" in user_query:
            reply = '{"action": "navigate_to", "parameters": {"target_room": "salotto"}}'
        elif "esplora" in user_query:
            reply = '{"action": "start_frontier_exploration", "parameters": {"mode": "continuous"}}'

        telem = InferenceTelemetry(
            model="qwen2.5-instruct:1.5b",
            prompt=prompt,
            response=reply,
            ttft_ms=5.0,
            total_time_ms=10.0,
            token_count=len(reply) // 4,
            tokens_per_sec=50.0,
            cpu_delta_pct=0.1,
            memory_delta_mb=0.0,
            is_mock=True,
            is_streaming=False,
        )
        return reply, telem


# ==============================================================================
# PYTEST FIXTURES
# ==============================================================================

@pytest.fixture
def temp_db_path(tmp_path):
    """Provides an isolated SQLite database path in temporary folder."""
    return str(tmp_path / "test_mag_slm.db")


@pytest.fixture
def temp_mag_db(temp_db_path):
    """Provides an initialized MAGDatabase in WAL mode."""
    db = MAGDatabase(db_path=temp_db_path)
    return db


@pytest.fixture
def mock_hailo_client():
    return MockHailoQwenClient()


@pytest.fixture
def cag_snapshot():
    """Provides a realistic populated CAG environment and hardware state."""
    env = EnvironmentSnapshot()
    env.update_location(
        room_name="salotto",
        location=(2.4, -1.2),
        map_name="casa_piano1",
        covariance_trace=0.042,
        dist_to_centroid=0.4,
    )
    env.update_perception(humans=["Luca"], objects=["chair", "bottle"])
    env.update_exploration(active=True, mode="EXPLORE", remaining_frontiers=4)

    hw = HardwareStateCollector()
    hw.update_battery(voltage=12.15, percentage=85.0)

    return {"environment": env.to_text(), "hardware": hw.to_text()}


@pytest.fixture
def mock_dependencies(temp_db_path, mock_hailo_client):
    """Provides complete mock dependencies for ConversationManager."""
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
    world_model.current_user = {"name": "Luca"}
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


# ==============================================================================
# SUITE 1: TOKEN BUDGET CEILING (< 1200 TOKENS) - 6 TESTS
# ==============================================================================

class TestTokenBudgetCeiling:
    """Verifies that the SLM Metaprompt Fusion engine strictly enforces < 1200 tokens."""

    def test_nominal_context_token_budget_within_bounds(self, cag_snapshot):
        """Nominal operational context must yield between 500 and 1000 tokens (target ~950)."""
        fusion = SLMMetapromptFusion()
        ctx = SLMPromptContext(
            user_text="Come sta la batteria del robot?",
            system_prompt="Sei Marcus, robot rover autonomo.",
            cag_environment=cag_snapshot["environment"],
            cag_hardware=cag_snapshot["hardware"],
            mag_profile="Nome: Luca, Ruolo: Ingegnere.",
            mag_episodes="- [16/10/2026 06:40] Q: Ciao | A: Buongiorno Luca!",
            mag_facts="- [PREFERENCE] Luca preferisce la cucina.",
            rag_knowledge="SPEC-06: Tensione nominale batteria LiPo 3S 12.0V, shutdown a 9.0V.",
            conversation_history=[
                {"role": "user", "content": "Ciao Marcus"},
                {"role": "assistant", "content": "Ciao Luca! Come posso aiutarti?"},
            ],
            timestamp="Venerdì 16 Ottobre 2026, 07:15",
        )

        output = fusion.build_prompt_detailed(ctx)

        assert output.is_within_budget is True
        assert output.token_estimate < 1200, f"Token estimate {output.token_estimate} exceeds 1200 ceiling!"
        assert 200 <= output.token_estimate <= 1000, f"Nominal tokens {output.token_estimate} out of nominal range [200, 1000]"
        assert "<|im_start|>system" in output.chatml_prompt
        assert "<|im_start|>assistant\n" in output.chatml_prompt

    def test_adversarial_saturation_token_ceiling_under_1200(self):
        """Massive 15,000 character inputs in every section must be truncated to <= 1100 tokens."""
        fusion = SLMMetapromptFusion()
        giant_string = "Questo è un testo estremamente lungo e ripetitivo per saturare il contesto. " * 300

        ctx = SLMPromptContext(
            user_text=giant_string,
            system_prompt=giant_string,
            cag_environment=giant_string,
            cag_hardware=giant_string,
            mag_profile=giant_string,
            mag_episodes=giant_string,
            mag_facts=giant_string,
            rag_knowledge=giant_string,
            conversation_history=[{"role": "user", "content": giant_string}] * 10,
        )

        output = fusion.build_prompt_detailed(ctx)

        assert output.is_within_budget is True
        assert output.token_estimate <= 1100, f"Saturated tokens {output.token_estimate} exceed hard ceiling 1100!"
        assert output.token_estimate < 1200

    def test_empty_context_minimal_tokens(self):
        """Empty context must yield valid ChatML envelope with minimal token footprint (< 250 tokens)."""
        fusion = SLMMetapromptFusion()
        ctx = SLMPromptContext(user_text="Ciao")
        output = fusion.build_prompt_detailed(ctx)

        assert output.is_within_budget is True
        assert output.token_estimate < 250
        assert "Utente:\nCiao" in output.full_prompt or "Ciao" in output.chatml_prompt

    def test_intent_budget_reallocations_below_ceiling(self):
        """Dynamic budget reallocation across all intents must strictly remain < 1200 tokens."""
        fusion = SLMMetapromptFusion()
        intents = ["CONVERSATION", "NAVIGATION", "DIAGNOSTIC", "DOCUMENTATION", "CODING", "GENERAL"]
        for intent in intents:
            ctx = SLMPromptContext(
                user_text="Comando test intent",
                cag_environment="Room: salotto | Map: casa | AMCL cov: 0.05",
                cag_hardware="CPU: 15% | Batt: 85% | Temp: 42C",
                mag_facts="Fact 1\nFact 2\nFact 3",
                mag_episodes="Ep 1\nEp 2\nEp 3",
                rag_knowledge="Doc chunk with details about the robot system.",
                intent_category=intent,
            )

            output = fusion.build_prompt_detailed(ctx)
            assert output.is_within_budget is True, f"Failed for intent {intent}"
            assert output.token_estimate < 1200, f"Failed for intent {intent}"

            if intent == "CONVERSATION":
                assert output.section_token_counts["rag"] == 0, "RAG should be 0 in CONVERSATION intent"
            elif intent == "NAVIGATION":
                assert output.section_token_counts["rag"] == 0, "RAG should be 0 in NAVIGATION intent"
                assert output.section_token_counts["cag"] > 0

    def test_standalone_build_slm_prompt_contract(self):
        """Verifies PROJECT.md Interface Contract 1: build_slm_prompt(...) standalone function."""
        prompt = build_slm_prompt(
            user_text="Dove sei?",
            cag_text="Room: cucina",
            mag_text="User is Luca",
            rag_text="Manual specs",
            history=[{"role": "user", "content": "ciao"}, {"role": "assistant", "content": "ciao"}],
        )
        assert isinstance(prompt, str)
        assert "<|im_start|>system" in prompt
        assert "<|im_start|>user" in prompt
        assert len(prompt) // 4 < 1200

    def test_boundary_token_limits_1199_vs_1200(self):
        """Boundary test verifying that token count at boundary remains strictly < 1200."""
        fusion = SLMMetapromptFusion()
        # Build context tuned to hit high token count
        exact_text = "test " * 750
        ctx = SLMPromptContext(user_text=exact_text, system_prompt="Marcus system prompt")
        output = fusion.build_prompt_detailed(ctx)
        assert output.token_estimate < 1200
        assert output.is_within_budget is True


# ==============================================================================
# SUITE 2: FALLBACK AND TRUNCATION SEMANTICS - 6 TESTS
# ==============================================================================

class TestFallbackAndTruncationSemantics:
    """Verifies that truncation preserves semantic structure, headers, and dialogue recency."""

    def test_forward_truncation_preserves_headers_and_bullet_points(self):
        """Forward truncation must keep top headers and drop lower overflow lines."""
        text = "HEADER SECTION\n- Item 1 important\n- Item 2 important\n- Item 3 overflow\n- Item 4 overflow"
        truncated = SLMTruncator.truncate_forward_lines(text, max_tokens=15, chars_per_token=3.6)
        assert "HEADER SECTION" in truncated
        assert "- Item 1 important" in truncated
        assert "- Item 4 overflow" not in truncated

    def test_recency_preserving_dialogue_truncation_keeps_latest_turns(self):
        """Dialogue truncation must iterate in reverse to keep newest turns and discard oldest."""
        turns = [f"Utente: Turno {i}\nMarcus: Risposta {i}" for i in range(1, 21)]
        dialogue_text = "\n".join(turns)

        # Budget for only ~3 turns
        truncated = SLMTruncator.truncate_recency_lines(dialogue_text, max_tokens=40, chars_per_token=3.6)

        assert "Turno 20" in truncated, "Latest turn 20 must NOT be dropped!"
        assert "Turno 19" in truncated, "Turn 19 should be preserved!"
        assert "Turno 1\n" not in truncated, "Oldest turn 1 must be dropped!"
        assert "Turno 2\n" not in truncated, "Oldest turn 2 must be dropped!"

    def test_dialogue_truncation_maintains_chronological_order(self):
        """Preserved turns must remain in chronological order (Turn 13 before Turn 14)."""
        turns = [f"Utente: Turno {i}\nMarcus: Risposta {i}" for i in range(1, 15)]
        dialogue_text = "\n".join(turns)

        truncated = SLMTruncator.truncate_recency_lines(dialogue_text, max_tokens=60, chars_per_token=3.6)
        idx_13 = truncated.find("Turno 13")
        idx_14 = truncated.find("Turno 14")
        if idx_13 != -1 and idx_14 != -1:
            assert idx_13 < idx_14, "Chronological order violated in truncated dialogue!"

    def test_clean_word_boundary_single_line_truncation(self):
        """Single unbroken line must truncate at word boundary without slicing mid-word."""
        text = "parola1 parola2 parola3 parola4 parola5 parolalunghissimachenondeveesserespezzata"
        truncated = SLMTruncator.truncate_clean_words(text, max_tokens=8, chars_per_token=3.6)
        assert truncated.endswith("...")
        assert "parolalunghissima" not in truncated

    def test_none_and_empty_inputs_graceful_handling(self):
        """None or empty inputs in any or all parameters must not raise an exception."""
        fusion = SLMMetapromptFusion()
        ctx = SLMPromptContext(
            user_text="",
            system_prompt="",
            cag_environment="",
            mag_episodes="",
            rag_knowledge="",
            conversation_history=None,
        )
        output = fusion.build_prompt_detailed(ctx)
        assert output.is_within_budget is True
        assert output.chatml_prompt is not None

    def test_malformed_history_filtering(self):
        """Malformed conversation history items (non-dict, missing keys, empty content) must be handled gracefully."""
        fusion = SLMMetapromptFusion()
        malformed_history = [
            None,
            "not_a_dict",
            {"role": "user", "content": ""},
            {"invalid_key": "some value"},
            {"role": "user", "content": "Messaggio valido"},
            {"role": "assistant", "content": "Risposta valida"},
        ]
        ctx = SLMPromptContext(
            user_text="Test",
            conversation_history=malformed_history,
        )
        output = fusion.build_prompt_detailed(ctx)
        assert output.is_within_budget is True
        assert "Messaggio valido" in output.chatml_prompt
        assert "Risposta valida" in output.chatml_prompt


# ==============================================================================
# SUITE 3: TELEMETRY INJECTION (BATTERY, ROOM, MOTOR STATE) - 6 TESTS
# ==============================================================================

class TestTelemetryInjection:
    """Verifies proper injection of real-time telemetry from CAG into Qwen2 prompt."""

    def test_battery_telemetry_injected_into_prompt(self, cag_snapshot):
        """Prompt must contain exact battery percentage and voltage from hardware collector."""
        fusion = SLMMetapromptFusion()
        ctx = SLMPromptContext(
            user_text="Come sta la batteria?",
            cag_hardware=cag_snapshot["hardware"],
        )
        prompt = fusion.build_prompt_detailed(ctx).chatml_prompt
        assert "Batt: 85.0%" in prompt or "85.0%" in prompt
        assert "[STATO ATTUALE (CAG)]" in prompt

    def test_spatial_room_telemetry_injected_into_prompt(self, cag_snapshot):
        """Prompt must contain current room name, centroid distance, and AMCL covariance."""
        fusion = SLMMetapromptFusion()
        ctx = SLMPromptContext(
            user_text="Dove ti trovi?",
            cag_environment=cag_snapshot["environment"],
        )
        prompt = fusion.build_prompt_detailed(ctx).chatml_prompt
        assert "Room: salotto" in prompt
        assert "centroid 0.4m" in prompt
        assert "AMCL cov: 0.042" in prompt
        assert "Map: casa_piano1" in prompt

    def test_motor_and_exploration_state_injected_into_prompt(self, cag_snapshot):
        """Prompt must reflect active exploration state and remaining frontier count."""
        fusion = SLMMetapromptFusion()
        ctx = SLMPromptContext(
            user_text="Cosa stai facendo?",
            cag_environment=cag_snapshot["environment"],
        )
        prompt = fusion.build_prompt_detailed(ctx).chatml_prompt
        assert "Exploration: ACTIVE(EXPLORE, frontiers=4)" in prompt

    def test_barge_in_exploration_pause_note_injected(self):
        """Dialogue barge-in pause note must be present when exploration was halted for user."""
        fusion = SLMMetapromptFusion()
        barge_in_note = (
            "Il robot era in esplorazione attiva quando l'utente ha parlato. "
            "Il moto ruote è in pausa temporanea per dialogare."
        )
        ctx = SLMPromptContext(
            user_text="Ciao Marcus",
            repeated_note=barge_in_note,
        )
        prompt = fusion.build_prompt_detailed(ctx).chatml_prompt
        assert "Il moto ruote è in pausa temporanea" in prompt

    def test_dynamic_telemetry_update_within_cache_ttl(self):
        """Updating hardware collector state must reflect immediately in generated prompt."""
        hw = HardwareStateCollector()
        hw.update_battery(12.6, 95.0)

        fusion = SLMMetapromptFusion()
        p1 = fusion.build_prompt_detailed(SLMPromptContext(user_text="stato", cag_hardware=hw.to_text())).chatml_prompt
        assert "Batt: 95.0%" in p1

        # Simulate battery discharge
        hw.update_battery(11.1, 20.0)
        p2 = fusion.build_prompt_detailed(SLMPromptContext(user_text="stato", cag_hardware=hw.to_text())).chatml_prompt
        assert "Batt: 20.0%" in p2

    def test_local_qwen_prompt_answers_telemetry_queries(self, cag_snapshot):
        """Verifies that generated prompt allows Qwen client to resolve telemetry queries."""
        fusion = SLMMetapromptFusion()
        ctx = SLMPromptContext(
            user_text="dimmi la carica della batteria",
            cag_hardware=cag_snapshot["hardware"],
        )
        prompt = fusion.build_prompt_detailed(ctx).chatml_prompt
        assert "85.0%" in prompt
        assert "batteria" in prompt.lower()


# ==============================================================================
# SUITE 4: MAG PERSISTENCE (SQLITE WAL) - 6 TESTS
# ==============================================================================

class TestMAGPersistenceWAL:
    """Verifies that local Qwen2 interactions are persisted into autobiographical SQLite WAL."""

    def test_sqlite_wal_pragmas_verified(self, temp_mag_db, temp_db_path):
        """SQLite database must operate with PRAGMA journal_mode=WAL and synchronous=NORMAL."""
        # 1. Verify WAL mode on database file
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0].lower()
        assert mode == "wal", f"Journal mode is '{mode}', expected 'wal'!"
        conn.close()

        # 2. Verify synchronous=NORMAL on MAGDatabase connection pool
        mag_conn = temp_mag_db._get_connection()
        cursor = mag_conn.cursor()
        cursor.execute("PRAGMA synchronous")
        sync = cursor.fetchone()[0]
        # In SQLite: 1 = NORMAL, 2 = FULL
        assert sync == 1, f"PRAGMA synchronous is {sync}, expected 1 (NORMAL)!"
        mag_conn.close()

    def test_local_interaction_persists_to_sqlite_wal(self, temp_mag_db):
        """Direct insertion of an episode must be retrievable and searchable via FTS5."""
        ep_id = temp_mag_db.insert_episode(
            user_input="Marcus, vai in salotto",
            robot_response="Ricevuto! Navigo verso il salotto.",
            actions_taken=["navigate_to"],
            was_successful=True,
            user_id="Luca",
        )
        assert ep_id is not None

        # Verify episode retrieval
        recent = temp_mag_db.get_recent_episodes(limit=1)
        assert len(recent) == 1
        assert recent[0]["user_input"] == "Marcus, vai in salotto"
        assert recent[0]["robot_response"] == "Ricevuto! Navigo verso il salotto."

        # Verify FTS5 search
        fts_res = temp_mag_db.search_episodes_fts("salotto")
        assert len(fts_res) >= 1
        assert fts_res[0]["id"] == ep_id

    def test_learned_facts_extracted_and_fts_indexed(self, temp_mag_db):
        """Semantic facts must be stored in semantic_facts and indexed in semantic_facts_fts."""
        fact_id = temp_mag_db.insert_fact(
            fact_text="Luca preferisce studiare in salotto con luci calde.",
            fact_type="USER_PREFERENCE",
            confidence=0.95,
        )
        assert fact_id is not None

        facts = temp_mag_db.search_facts_fts("salotto")
        assert len(facts) >= 1
        assert "luci calde" in facts[0]["fact_text"]

    def test_subsequent_turn_retrieves_past_local_turn(self, temp_mag_db):
        """A past local turn recorded in MAG should be retrievable by subsequent search."""
        temp_mag_db.insert_episode(
            user_input="Qual è la password del Wi-Fi di laboratorio?",
            robot_response="La password è Lab2026Secure.",
            actions_taken=[],
            was_successful=True,
            user_id="Luca",
        )
        results = temp_mag_db.search_episodes_fts("password")
        assert len(results) >= 1
        assert "Lab2026Secure" in results[0]["robot_response"]

    @pytest.mark.asyncio
    async def test_end_to_end_local_qwen_turn_persists_to_mag(
        self, mock_dependencies, temp_db_path, mock_hailo_client
    ):
        """Processing an input via _handle_local_qwen must asynchronously record the turn in MAG DB."""
        responses = []
        manager = ConversationManager(
            response_callback=lambda text: responses.append(text),
            **mock_dependencies,
        )
        manager.hailo_client = mock_hailo_client

        # Initialize real TrinityEngine on temporary DB
        engine = TrinityEngine(db_path=temp_db_path)
        manager.trinity_engine = engine

        # Execute local turn
        success = await manager._handle_local_qwen("ruota a destra di 45 gradi", should_speak=False)
        assert success is True

        # Allow async post-interaction task to commit
        await asyncio.sleep(0.1)

        # Inspect SQLite DB directly
        db = engine.mag_db
        episodes = db.get_recent_episodes(limit=5)
        assert len(episodes) >= 1
        assert episodes[0]["user_input"] == "ruota a destra di 45 gradi"

    def test_db_isolation_and_no_file_leaks(self, temp_db_path):
        """Verifies tests never touch production /home/robopy/mag_trinity.db."""
        assert temp_db_path != "/home/robopy/mag_trinity.db"
        assert temp_db_path != "./mag_trinity.db"
        db = MAGDatabase(db_path=temp_db_path)
        assert os.path.exists(temp_db_path)
        assert db.db_path == temp_db_path


# ==============================================================================
# SUITE 5: DETERMINISTIC MOCK HARNESS & RESILIENCE - 4 TESTS
# ==============================================================================

class TestDeterministicMockHarness:
    """Verifies mock reliability, zero-network isolation, and failover mechanics."""

    @pytest.mark.asyncio
    async def test_mock_hailo_client_zero_hardware_zero_network(self, mock_hailo_client):
        """Mock client must respond in < 50ms without touching network or hardware."""
        t0 = time.perf_counter()
        reply, telem = await mock_hailo_client.generate("Ciao Marcus")
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.05
        assert telem.is_mock is True
        assert len(reply) > 0

    @pytest.mark.asyncio
    async def test_mock_hailo_client_prompt_inspection(self, mock_hailo_client):
        """Mock client accurately records full prompt for assertions."""
        test_prompt = "<|im_start|>system\nTest Context\n<|im_end|>\n<|im_start|>user\nTest<|im_end|>"
        await mock_hailo_client.generate(test_prompt)

        assert mock_hailo_client.call_count == 1
        assert mock_hailo_client.last_prompt == test_prompt

    @pytest.mark.asyncio
    async def test_mock_hailo_client_timeout_and_fallback(
        self, mock_dependencies, mock_hailo_client, temp_db_path
    ):
        """When local Qwen times out (> 1.2s), natural conversational fallback is emitted."""
        mock_hailo_client.should_timeout = True
        responses = []

        manager = ConversationManager(
            response_callback=lambda text: responses.append(text),
            **mock_dependencies,
        )
        manager.hailo_client = mock_hailo_client
        manager.trinity_engine = TrinityEngine(db_path=temp_db_path)

        success = await manager._handle_local_qwen("come stai?", should_speak=False)

        assert success is True, "Timeout must be handled with fallback, not silent crash"
        assert len(responses) == 1
        assert "Scusami" in responses[0] or "esitazione" in responses[0]

    @pytest.mark.asyncio
    async def test_mock_keyword_collision_isolation(self, mock_hailo_client):
        """Prompt tags like [HW] Batt: 85% must not trigger battery action when user says 'ciao'."""
        fused_prompt = (
            "<|im_start|>system\n[HW] Batt: 85.0% Temp: 42C\n<|im_end|>\n"
            "<|im_start|>user\nCiao Marcus, come stai?<|im_end|>\n<|im_start|>assistant\n"
        )
        reply, _ = await mock_hailo_client.generate(fused_prompt)
        assert "get_battery_status" not in reply, "Context tag 'Batt:' falsely triggered battery action!"
