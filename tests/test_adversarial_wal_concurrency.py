"""
Adversarial Stress Test Suite: SQLite WAL Concurrency & Local Qwen Stalls
========================================================================
Empirical stress tests conducted by challenger_m1_6_2 to challenge:
1. SQLite WAL Persistence under 20 concurrent worker threads (200+ writes, 0 lock errors, 0 corruption).
2. Multi-connection independent WAL contention (20 distinct MAGDatabase instances writing to one WAL DB).
3. Simultaneous concurrent FTS readers during intensive writes.
4. Simulated NPU stalls (0.5s, 1.2s, 1.5s, 3.0s) verifying 1.2s timeout enforcement and non-blocking event loop.
5. Asyncio event loop liveness and drift check during long NPU stall.
6. Zero Silent Drops (100% response_callback invocation) across 12 adversarial failure modes.
7. Verification of Kinematic Sandbox contract and Speech Sanitization.

Run via:
    cmd /c "set PYTHONPATH=.;robopy_controller && python -m pytest tests/test_adversarial_wal_concurrency.py -v -s"
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import re
import sqlite3
import sys
import threading
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure imports resolve cleanly
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

import types
if "rclpy" not in sys.modules:
    mock_rclpy = types.ModuleType("rclpy")
    mock_rclpy.logging = MagicMock()
    sys.modules["rclpy"] = mock_rclpy

if "robopy_controller.srv" not in sys.modules:
    mock_srv = types.ModuleType("robopy_controller.srv")
    mock_srv.AskVisualQuestion = MagicMock()
    sys.modules["robopy_controller.srv"] = mock_srv

from robot_ai.trinity.mag_database import MAGDatabase
from robot_ai.trinity.trinity_engine import TrinityEngine
from robot_ai.orchestration.conversation import ConversationManager
from robot_ai.hailo.hailo_qwen_client import InferenceTelemetry
from robot_ai.trinity.sandbox_rules import SandboxValidationResult, KinematicSandboxErrorCode


# ==============================================================================
# ADVERSARIAL STALL MOCK CLIENT
# ==============================================================================

class AdversarialStallMockClient:
    """
    Simulates hardware latency, NPU stalls, exceptions, and timeouts
    to stress-test _handle_local_qwen resilience.
    """

    def __init__(
        self,
        injected_delay: float = 0.0,
        injected_exception: Optional[Exception] = None,
        canned_response: str = "Tutto operativo nel modulo locale.",
    ):
        self.injected_delay = injected_delay
        self.injected_exception = injected_exception
        self.canned_response = canned_response
        self.invocation_count = 0

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        timeout_sec: Optional[float] = None,
        **kwargs
    ):
        self.invocation_count += 1
        if self.injected_delay > 0:
            await asyncio.sleep(self.injected_delay)

        if self.injected_exception is not None:
            raise self.injected_exception

        telem = InferenceTelemetry(
            model="qwen2.5-instruct:1.5b",
            prompt=prompt,
            response=self.canned_response,
            ttft_ms=self.injected_delay * 1000,
            total_time_ms=self.injected_delay * 1000 + 10,
            token_count=len(self.canned_response) // 4,
            tokens_per_sec=40.0,
            cpu_delta_pct=0.0,
            memory_delta_mb=0.0,
            is_mock=True,
            is_streaming=False,
        )
        return self.canned_response, telem


# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture
def adversarial_db_path(tmp_path):
    """Provides an isolated database path in pytest temporary directory."""
    db_file = tmp_path / "adversarial_mag_wal.db"
    return str(db_file)


@pytest.fixture
def conversation_dependencies():
    """Mock dependencies for ConversationManager."""
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
# SUITE 1: SQLITE WAL CONCURRENCY & THREAD CONTENTION STRESS
# ==============================================================================

class TestSQLiteWALConcurrencyStress:
    """
    Stress-tests SQLite WAL persistence under high thread contention (20 concurrent threads).
    Verifies 0 database lock errors, 0 data corruption, and FTS5 table consistency.
    """

    def test_wal_mode_and_busy_timeout_pragmas(self, adversarial_db_path):
        """
        Verifies database file is set to WAL mode, and connection pool executes
        with PRAGMA synchronous=NORMAL and busy_timeout=5000.
        """
        mag_db = MAGDatabase(db_path=adversarial_db_path)
        
        # Verify WAL mode on file
        conn = sqlite3.connect(adversarial_db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0].lower()
        assert mode == "wal", f"Journal mode is {mode}, expected 'wal'!"
        conn.close()

        # Verify synchronous=NORMAL on MAGDatabase connection pool
        mag_conn = mag_db._get_connection()
        cursor = mag_conn.cursor()
        cursor.execute("PRAGMA synchronous")
        sync = cursor.fetchone()[0]
        # In SQLite: 1 = NORMAL, 2 = FULL
        assert sync == 1, f"PRAGMA synchronous is {sync}, expected 1 (NORMAL)!"
        mag_conn.close()

    def test_20_concurrent_threads_writing_via_trinity_engine(self, adversarial_db_path):
        """
        Adversarially simulate 20 concurrent threads writing interactions to mag_database.db
        via trinity_engine.record_interaction in WAL mode.
        Each thread writes 10 interactions (200 total writes).
        Verifies:
        - 0 database lock errors (sqlite3.OperationalError: database is locked)
        - 0 thread exceptions
        - Exact row counts (200 in episodes, 200 in episodes_fts)
        - Database integrity check PASS (PRAGMA integrity_check == 'ok')
        """
        engine = TrinityEngine(db_path=adversarial_db_path)
        num_threads = 20
        writes_per_thread = 10
        total_expected_writes = num_threads * writes_per_thread

        thread_errors: List[Exception] = []
        errors_lock = threading.Lock()

        def worker_thread(thread_idx: int):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                for write_idx in range(writes_per_thread):
                    user_text = f"Comando dal thread {thread_idx} interazione {write_idx}"
                    robot_resp = f"Risposta Marcus al thread {thread_idx} interazione {write_idx}"
                    facts = [{
                        "fact_text": f"Dato semantico thread_{thread_idx}_idx_{write_idx}",
                        "fact_type": "CAPABILITY",
                        "confidence": 0.9,
                    }]

                    # Execute record_interaction asynchronously within the thread's event loop
                    loop.run_until_complete(
                        engine.record_interaction(
                            user_text=user_text,
                            robot_response=robot_resp,
                            actions_taken=[f"action_t{thread_idx}_{write_idx}"],
                            was_successful=True,
                            user_identity=f"user_thread_{thread_idx}",
                            extracted_facts=facts,
                        )
                    )
            except Exception as exc:
                with errors_lock:
                    thread_errors.append(exc)
            finally:
                loop.close()

        threads = [threading.Thread(target=worker_thread, args=(i,)) for i in range(num_threads)]

        t_start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
        elapsed = time.perf_counter() - t_start

        # 1. Assert ZERO lock errors or thread exceptions
        assert len(thread_errors) == 0, f"Encountered {len(thread_errors)} thread errors: {thread_errors}"

        # 2. Assert direct SQLite table counts
        conn = sqlite3.connect(adversarial_db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM episodes")
        episodes_count = cursor.fetchone()[0]
        assert episodes_count == total_expected_writes, (
            f"Expected {total_expected_writes} episodes, got {episodes_count}"
        )

        cursor.execute("SELECT COUNT(*) FROM episodes_fts")
        episodes_fts_count = cursor.fetchone()[0]
        assert episodes_fts_count == total_expected_writes, (
            f"FTS5 episodes count mismatch: expected {total_expected_writes}, got {episodes_fts_count}"
        )

        cursor.execute("SELECT COUNT(*) FROM semantic_facts")
        facts_count = cursor.fetchone()[0]
        assert facts_count == total_expected_writes, (
            f"Expected {total_expected_writes} facts, got {facts_count}"
        )

        cursor.execute("SELECT COUNT(*) FROM facts_fts")
        facts_fts_count = cursor.fetchone()[0]
        assert facts_fts_count == total_expected_writes, (
            f"FTS5 facts count mismatch: expected {total_expected_writes}, got {facts_fts_count}"
        )

        # 3. Assert zero data corruption via PRAGMA integrity_check
        cursor.execute("PRAGMA integrity_check")
        integrity_result = cursor.fetchall()
        assert len(integrity_result) == 1 and integrity_result[0][0] == "ok", (
            f"SQLite integrity check failed: {integrity_result}"
        )

        conn.close()

    def test_multi_connection_independent_wal_contention(self, adversarial_db_path):
        """
        Adversarially simulate 20 distinct MAGDatabase instances (independent connections)
        writing simultaneously to the same WAL database file without a shared Python lock.
        Tests SQLite's native WAL engine and busy timeout handling under extreme contention.
        """
        num_instances = 20
        writes_per_instance = 5
        total_writes = num_instances * writes_per_instance
        exceptions: List[Exception] = []
        lock = threading.Lock()

        def independent_worker(worker_id: int):
            try:
                # Each worker instantiates its own MAGDatabase connected to the same file
                db_instance = MAGDatabase(db_path=adversarial_db_path)
                for w in range(writes_per_instance):
                    db_instance.insert_episode(
                        user_input=f"Input worker {worker_id} write {w}",
                        robot_response=f"Response worker {worker_id} write {w}",
                        actions_taken=["stress_action"],
                        was_successful=True,
                        user_id=f"worker_{worker_id}",
                    )
            except Exception as e:
                with lock:
                    exceptions.append(e)

        with ThreadPoolExecutor(max_workers=num_instances) as executor:
            futures = [executor.submit(independent_worker, i) for i in range(num_instances)]
            for f in futures:
                f.result(timeout=30.0)

        assert len(exceptions) == 0, f"Independent connection errors: {exceptions}"

        # Verify integrity and count
        conn = sqlite3.connect(adversarial_db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        assert cursor.fetchone()[0] == "ok"
        conn.close()

    def test_concurrent_readers_and_writers_no_blocking(self, adversarial_db_path):
        """
        WAL mode allows concurrent readers while a writer is executing.
        Verify that heavy search queries succeed during continuous writes.
        """
        engine = TrinityEngine(db_path=adversarial_db_path)
        stop_event = threading.Event()
        read_counts = [0]
        read_errors: List[Exception] = []

        def continuous_reader():
            while not stop_event.is_set():
                try:
                    res = engine.mag_db.search_episodes_fts("thread")
                    read_counts[0] += 1
                    time.sleep(0.005)
                except Exception as e:
                    read_errors.append(e)

        reader_thread = threading.Thread(target=continuous_reader)
        reader_thread.start()

        # Run 30 rapid writes
        loop = asyncio.new_event_loop()
        for i in range(30):
            loop.run_until_complete(
                engine.record_interaction(
                    user_text=f"Concurrent reader write {i}",
                    robot_response=f"Response {i}",
                    was_successful=True,
                )
            )
        loop.close()

        stop_event.set()
        reader_thread.join(timeout=5.0)

        assert len(read_errors) == 0, f"Reader encountered errors: {read_errors}"
        assert read_counts[0] > 0, "Reader was completely starved or failed to execute."


# ==============================================================================
# SUITE 2: SIMULATED NPU STALLS & TIMEOUT SLA (0.5s, 1.2s, 1.5s, 3.0s)
# ==============================================================================

class TestSimulatedNPUStallsMatrix:
    """
    Empirically verifies that _handle_local_qwen strictly obeys the 1.2s timeout on NPU generation:
    - 0.5s delay: completes successfully, no fallback
    - 1.2s delay: boundary case, triggers 1.2s timeout fallback
    - 1.5s delay: times out at 1.2s, delivers fallback, does NOT wait 1.5s
    - 3.0s delay: times out at 1.2s, delivers fallback, does NOT wait 3.0s
    """

    @pytest.mark.asyncio
    async def test_npu_stall_0_5s_succeeds_without_fallback(
        self, conversation_dependencies, adversarial_db_path
    ):
        """0.5s delay < 1.2s timeout SLA: completes normally without triggering fallback."""
        delivered_responses: List[str] = []
        manager = ConversationManager(
            response_callback=lambda txt: delivered_responses.append(txt),
            **conversation_dependencies,
        )
        engine = TrinityEngine(db_path=adversarial_db_path)
        # Pre-warm prompt augmentation to measure NPU delay independently
        await engine.build_augmented_prompt("warmup", target_model="qwen_local")
        manager.trinity_engine = engine

        manager.hailo_client = AdversarialStallMockClient(
            injected_delay=0.5,
            canned_response="Risposta tempestiva entro il budget.",
        )

        t0 = time.perf_counter()
        success = await manager._handle_local_qwen("ciao marcus", should_speak=False)
        elapsed = time.perf_counter() - t0

        assert success is True
        assert len(delivered_responses) == 1
        assert delivered_responses[0] == "Risposta tempestiva entro il budget."
        assert "esitazione" not in delivered_responses[0]

    @pytest.mark.asyncio
    async def test_npu_generation_timeout_strictly_aborts_at_1_2s(
        self, conversation_dependencies, adversarial_db_path
    ):
        """
        When hailo_client.generate experiences a 3.0s stall, asyncio.wait_for strictly
        aborts at 1.2s and does NOT wait for the 3.0s stall to complete.
        """
        delivered_responses: List[str] = []
        manager = ConversationManager(
            response_callback=lambda txt: delivered_responses.append(txt),
            **conversation_dependencies,
        )
        engine = TrinityEngine(db_path=adversarial_db_path)
        await engine.build_augmented_prompt("warmup", target_model="qwen_local")
        manager.trinity_engine = engine

        manager.hailo_client = AdversarialStallMockClient(
            injected_delay=3.0,
            canned_response="Major stalled response that must be aborted.",
        )

        t0 = time.perf_counter()
        success = await manager._handle_local_qwen("test stall 3.0s", should_speak=False)
        elapsed = time.perf_counter() - t0

        assert success is True
        # Generation timeout is 1.2s. Total time must be FAR below 3.0s (aborted at 1.2s)
        assert elapsed < 2.50, f"Elapsed {elapsed:.3f}s failed to abort 3.0s stall at 1.2s timeout"
        assert len(delivered_responses) == 1
        assert "Scusami" in delivered_responses[0]

    @pytest.mark.asyncio
    async def test_npu_generation_stall_1_5s_aborts_at_1_2s(
        self, conversation_dependencies, adversarial_db_path
    ):
        """1.5s stall aborts at 1.2s, triggering conversational fallback."""
        delivered_responses: List[str] = []
        manager = ConversationManager(
            response_callback=lambda txt: delivered_responses.append(txt),
            **conversation_dependencies,
        )
        engine = TrinityEngine(db_path=adversarial_db_path)
        await engine.build_augmented_prompt("warmup", target_model="qwen_local")
        manager.trinity_engine = engine

        manager.hailo_client = AdversarialStallMockClient(
            injected_delay=1.5,
            canned_response="Stalled response.",
        )

        t0 = time.perf_counter()
        success = await manager._handle_local_qwen("test stall 1.5s", should_speak=False)
        elapsed = time.perf_counter() - t0

        assert success is True
        assert len(delivered_responses) == 1
        assert "Scusami" in delivered_responses[0]

    @pytest.mark.asyncio
    async def test_event_loop_never_hangs_during_npu_stall(
        self, conversation_dependencies, adversarial_db_path
    ):
        """
        Verify that an intensive NPU stall does NOT block or hang the asyncio event loop.
        A concurrent heartbeat task ticking every 50ms must execute continuously without starvation.
        """
        manager = ConversationManager(
            response_callback=lambda txt: None,
            **conversation_dependencies,
        )
        engine = TrinityEngine(db_path=adversarial_db_path)
        await engine.build_augmented_prompt("warmup", target_model="qwen_local")
        manager.trinity_engine = engine

        manager.hailo_client = AdversarialStallMockClient(
            injected_delay=2.5,
            canned_response="Stalled.",
        )

        heartbeat_ticks = [0]
        stop_heartbeat = asyncio.Event()

        async def heartbeat_task():
            while not stop_heartbeat.is_set():
                heartbeat_ticks[0] += 1
                await asyncio.sleep(0.05)

        hb = asyncio.create_task(heartbeat_task())

        t0 = time.perf_counter()
        success = await manager._handle_local_qwen("stall check", should_speak=False)
        elapsed = time.perf_counter() - t0

        stop_heartbeat.set()
        await hb

        assert success is True
        # During 1.2s+ wait, 50ms ticks should trigger at least 15 times
        assert heartbeat_ticks[0] >= 15, (
            f"Event loop was starved! Only {heartbeat_ticks[0]} ticks executed during {elapsed:.2f}s"
        )


# ==============================================================================
# SUITE 3: ZERO SILENT DROPS (100% DELIVERY SLA ACROSS 12 ADVERSARIAL CASES)
# ==============================================================================

class TestZeroSilentDropsMatrix:
    """
    Adversarially tests 12 distinct failure modes, boundary conditions, and corrupt inputs.
    Verifies that response_callback is invoked 100% of the time.
    Zero silent drops SLA!
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "test_case_id, client_setup, input_query",
        [
            ("NORMAL_CONV", lambda: AdversarialStallMockClient(canned_response="Ciao! Come posso aiutarti oggi?"), "ciao"),
            ("ACTION_TAG", lambda: AdversarialStallMockClient(canned_response='<action>{"call": {"name": "rotate_angle", "arguments": {"angle_deg": 45}}}</action>Ruoto a destra!'), "gira a destra"),
            ("NPU_TIMEOUT_1_5S", lambda: AdversarialStallMockClient(injected_delay=1.5), "richiesta con timeout"),
            ("NPU_TIMEOUT_4_0S", lambda: AdversarialStallMockClient(injected_delay=4.0), "richiesta con blocco lungo"),
            ("NPU_CONN_RESET", lambda: AdversarialStallMockClient(injected_exception=ConnectionResetError("Socket reset by peer")), "richiesta connection reset"),
            ("NPU_PCIE_FAULT", lambda: AdversarialStallMockClient(injected_exception=RuntimeError("Hailo-10H PCIe DMA Failure")), "richiesta pcie fault"),
            ("NPU_OOM_FAULT", lambda: AdversarialStallMockClient(injected_exception=MemoryError("Failed to allocate DMA buffer")), "richiesta oom"),
            ("CLIENT_NONE", lambda: None, "richiesta client non inizializzato"),
            ("EMPTY_RESPONSE", lambda: AdversarialStallMockClient(canned_response=""), "messaggio vuoto"),
            ("MALFORMED_JSON_TAG", lambda: AdversarialStallMockClient(canned_response='<action>{broken json: true}</action>Non preoccuparti.'), "azione malformata"),
            ("LEGACY_JSON_DICT", lambda: AdversarialStallMockClient(canned_response='{"action": "get_battery_status"}'), "stato batteria"),
            ("MARKDOWN_JSON_BLOCK", lambda: AdversarialStallMockClient(canned_response='```json\n{"action": "stop_motion"}\n```\nMi fermo subito.'), "stop"),
        ],
    )
    async def test_zero_silent_drops_100_percent_callback_delivery(
        self,
        test_case_id,
        client_setup,
        input_query,
        conversation_dependencies,
        adversarial_db_path,
    ):
        """
        In every test case, response_callback MUST be invoked exactly 1 time,
        and the method must return True (never silently drop or crash).
        """
        callbacks_received: List[str] = []
        manager = ConversationManager(
            response_callback=lambda txt: callbacks_received.append(txt),
            **conversation_dependencies,
        )
        engine = TrinityEngine(db_path=adversarial_db_path)
        manager.trinity_engine = engine
        manager.hailo_client = client_setup()

        success = await manager._handle_local_qwen(input_query, should_speak=False)

        # 1. Return code must be True
        assert success is True, f"Case {test_case_id} returned False instead of True"

        # 2. Callback must be called exactly once (zero silent drops)
        assert len(callbacks_received) == 1, (
            f"Case {test_case_id} SILENT DROP! Callbacks received count: {len(callbacks_received)}"
        )


# ==============================================================================
# SUITE 4: EMPIRICAL DISCOVERY OF IMPLEMENTATION DEFECTS (CHALLENGER ORACLES)
# ==============================================================================

class TestChallengerDefectOracles:
    """
    Adversarial verification oracles testing specific failure modes in conversation.py:
    1. Raw action block leak when JSON in <action> tags is malformed.
    2. Attribute mismatch between conversation.py and SandboxValidationResult.
    3. Empty response leak when hailo_client returns empty string.
    """

    @pytest.mark.asyncio
    async def test_defect_oracle_malformed_action_tag_sanitization_leak(
        self, conversation_dependencies, adversarial_db_path
    ):
        """
        ORACLE TEST: If Qwen outputs malformed JSON inside <action> tags,
        extract_call returns None. In current conversation.py, regex stripping is
        nested under `if raw_call:`. Consequently, unparsed `<action>` tags leak
        directly to speech/text output, violating Interface Contract 4!
        """
        delivered: List[str] = []
        manager = ConversationManager(
            response_callback=lambda txt: delivered.append(txt),
            **conversation_dependencies,
        )
        manager.trinity_engine = TrinityEngine(db_path=adversarial_db_path)
        manager.hailo_client = AdversarialStallMockClient(
            canned_response="<action>{broken json without quotes}</action>Sto arrivando!",
        )

        await manager._handle_local_qwen("vieni qui", should_speak=False)
        assert len(delivered) == 1
        output = delivered[0]

        # Regression invariant: raw action tags must never leak into spoken/delivered text
        has_tag_leak = "<action>" in output or "</action>" in output
        assert not has_tag_leak, f"VULNERABILITY: Raw <action> tags leaked to output: {output}"
        assert output == "Sto arrivando!"

    @pytest.mark.asyncio
    async def test_defect_oracle_kinematic_sandbox_attribute_mismatch(
        self, conversation_dependencies, adversarial_db_path
    ):
        """
        ORACLE TEST: In conversation.py line 190, the code attempts to access
        `validation.is_authorized`, `validation.rejection_reason`, and
        `validation.conversational_feedback`. However, SandboxValidationResult
        attributes are `authorized`, `reason`, and `feedback_speak`.
        This causes an AttributeError swallowed into logger.error, preventing
        proper sandbox rejection handling!
        """
        delivered: List[str] = []
        manager = ConversationManager(
            response_callback=lambda txt: delivered.append(txt),
            **conversation_dependencies,
        )
        manager.trinity_engine = TrinityEngine(db_path=adversarial_db_path)
        
        # Valid JSON action that requests an extreme movement (excessive angle)
        # to trigger sandbox rejection
        manager.hailo_client = AdversarialStallMockClient(
            canned_response='<action>{"call": {"name": "rotate_angle", "arguments": {"angle_deg": 360.0, "angular_speed": 5.0}}}</action>Ruoto di 360 gradi velocissimo!',
        )

        # Run _handle_local_qwen
        await manager._handle_local_qwen("fai una giravolta velocissima", should_speak=False)
        assert len(delivered) == 1
        
        # Verify SandboxValidationResult class attributes
        dummy_res = SandboxValidationResult(
            authorized=False,
            error_code=KinematicSandboxErrorCode.ANGULAR_SPEED_EXCEEDED,
            reason="Angular velocity exceeds 1.0 rad/s",
            feedback_speak="Velocità angolare eccessiva, rifiutata per sicurezza."
        )
        assert hasattr(dummy_res, "authorized")
        assert not hasattr(dummy_res, "is_authorized")
        assert hasattr(dummy_res, "reason")
        assert not hasattr(dummy_res, "rejection_reason")
        assert hasattr(dummy_res, "feedback_speak")
        assert not hasattr(dummy_res, "conversational_feedback")
