"""
Unit tests for QueryMemorySkill, MemoryInfoSkill hardening, and memory retrieval fallbacks.
Conforms to SPEC-05 and Book-to-Skill V2 guidelines.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
controller_dir = os.path.join(BASE_DIR, "robopy_controller")
if controller_dir not in sys.path:
    sys.path.insert(0, controller_dir)

from unittest.mock import MagicMock, AsyncMock

# Mock ROS 2 modules if running outside ROS 2 environment
if 'rclpy' not in sys.modules:
    sys.modules['rclpy'] = MagicMock()
    sys.modules['rclpy.node'] = MagicMock()
    sys.modules['rclpy.callback_groups'] = MagicMock()
    sys.modules['geometry_msgs.msg'] = MagicMock()
    sys.modules['std_msgs.msg'] = MagicMock()
    sys.modules['sensor_msgs.msg'] = MagicMock()
    sys.modules['vision_msgs.msg'] = MagicMock()

import pytest
import asyncio

from robot_ai.skills.builtin.query_memory_skill import QueryMemorySkill
from robot_ai.skills.builtin.memory_info_skill import MemoryInfoSkill
from robot_ai.skills.base_skill import SkillResult
from robot_ai.skills.skill_registry import SkillRegistry
from robot_ai.orchestration.skill_executor import SkillExecutor
from robot_ai.trinity.mag_hybrid_search import HybridSearchEngine


class MockMAGDatabase:
    """Mock SQLite MAG database simulating episodes and facts."""
    def __init__(self, episodes=None, facts=None, user_profiles=None):
        self.episodes = episodes or []
        self.facts = facts or []
        self.profiles = user_profiles or {}

    def get_recent_episodes(self, limit=5, user_id=None):
        return self.episodes[:limit]

    def get_all_facts(self):
        return self.facts

    def search_episodes_fts(self, query, limit=5):
        # simple keyword match
        res = [e for e in self.episodes if query.lower() in e.get("user_input", "").lower()]
        return res[:limit]

    def search_facts_fts(self, query, limit=5):
        res = [f for f in self.facts if query.lower() in f.get("fact_text", "").lower()]
        return res[:limit]

    def get_user_profile(self, user_name):
        return self.profiles.get(user_name, {})


class MockMemoryStore:
    def __init__(self, memories=None):
        self._memories = memories or []

    async def search(self, query, top_k=5):
        return []

    def get_recent(self, limit=5, memory_type=None):
        return self._memories[:limit]


class MockMemoryManager:
    def __init__(self, memory_store=None):
        self.memory_store = memory_store or MockMemoryStore()

    async def get_stats(self):
        return {"total_chunks": 12, "status": "ready"}

    async def list_loaded_documents(self):
        return ["manuale_marcus.pdf", "schema_motori.pdf"]


def test_query_memory_skill_matching():
    """Verify confidence score matching for direct memory access requests."""
    skill = QueryMemorySkill()

    # Direct triggers: should match >= 0.95
    assert skill.match("accedi ai dati della memoria") >= 0.95
    assert skill.match("cosa c'è nella memoria?") >= 0.95
    assert skill.match("cosa ti ricordi?") >= 0.95
    assert skill.match("cerca nella memoria") >= 0.95
    assert skill.match("fatti appresi") >= 0.95

    # General memory search request: should match >= 0.90
    assert skill.match("mostrami cosa hai imparato finora") >= 0.90

    # Irrelevant request
    assert skill.match("che ore sono?") == 0.0
    assert skill.match("accendi la luce") == 0.0


def test_query_memory_skill_exploratory_execution():
    """Verify exploratory query execution retrieves recent episodes and facts."""
    async def _run():
        episodes = [
            {"user_input": "Accendi la luce in cucina", "robot_response": "Ho acceso la luce in cucina."},
            {"user_input": "Come ti chiami?", "robot_response": "Sono MARCUS, il tuo robot autonomo."}
        ]
        facts = [
            {"fact_text": "L'utente preferisce la luce calda.", "confidence": 0.9}
        ]
        mock_db = MockMAGDatabase(episodes=episodes, facts=facts, user_profiles={"Luca": {"musica": "jazz"}})
        mock_engine = MagicMock()
        mock_engine.mag_db = mock_db

        mock_mgr = MockMemoryManager()
        skill = QueryMemorySkill(memory_manager=mock_mgr, trinity_engine=mock_engine)

        result = await skill.safe_execute("accedi ai dati della memoria", context={"query": "accedi ai dati della memoria"})

        assert isinstance(result, SkillResult)
        assert result.success is True
        assert result.speak is not None
        assert len(result.speak) > 0
        # Must mention facts or episodes in natural Italian
        assert "memoria" in result.speak.lower() or "ricordo" in result.speak.lower()
        assert result.data["total_found"] >= 2
        assert "L'utente preferisce la luce calda." in result.data["facts"]
    asyncio.run(_run())


def test_query_memory_skill_empty_database():
    """Verify that if database is completely empty, it provides a polite message without errors."""
    async def _run():
        mock_db = MockMAGDatabase()
        mock_engine = MagicMock()
        mock_engine.mag_db = mock_db
        mock_mgr = MockMemoryManager(memory_store=MockMemoryStore([]))

        skill = QueryMemorySkill(memory_manager=mock_mgr, trinity_engine=mock_engine)

        result = await skill.safe_execute("cerca nella memoria", context={"query": "cerca nella memoria"})

        assert isinstance(result, SkillResult)
        assert result.success is True
        assert result.speak is not None
        assert "non ho trovato informazioni" in result.speak.lower()
    asyncio.run(_run())


def test_memory_info_skill_no_type_error():
    """Verify that MemoryInfoSkill does not raise TypeError on formatted_document."""
    async def _run():
        mock_mgr = MockMemoryManager()
        skill = MemoryInfoSkill(memory_manager=mock_mgr)

        result = await skill.safe_execute("quanti documenti hai in memoria?", context={})

        assert isinstance(result, SkillResult)
        assert result.success is True
        assert "formatted_document" in result.data
        assert "manuale_marcus.pdf" in result.data["formatted_document"]
        assert "MARCUS" in result.data["formatted_document"]
    asyncio.run(_run())


def test_hybrid_search_fallback_on_exploratory_queries():
    """Verify HybridSearchEngine falls back to get_recent_episodes and get_all_facts on exploratory queries."""
    episodes = [
        {"id": "ep1", "user_input": "Ciao Marcus", "robot_response": "Ciao!"}
    ]
    facts = [
        {"id": "f1", "fact_text": "Marcus è operativo", "confidence": 0.95}
    ]
    mock_db = MockMAGDatabase(episodes=episodes, facts=facts)
    # FTS search for "accedi ai dati della memoria" will match nothing in mock_db
    engine = HybridSearchEngine(mock_db)

    # Calling search_episodes with broad query
    res_ep = engine.search_episodes("accedi ai dati della memoria", top_k=3)
    assert len(res_ep) > 0
    assert res_ep[0]["id"] == "ep1"

    # Calling search_facts with broad query
    res_fa = engine.search_facts("cosa c'è nella memoria", top_k=3)
    assert len(res_fa) > 0
    assert res_fa[0]["id"] == "f1"


def test_skill_executor_query_memory_stream():
    """Verify SkillExecutor correctly runs query_memory as a function call action and yields speech."""
    async def _run():
        registry = SkillRegistry()
        facts = [{"fact_text": "Marcus è stato costruito per aiutare a casa."}]
        mock_db = MockMAGDatabase(facts=facts)
        mock_engine = MagicMock()
        mock_engine.mag_db = mock_db
        skill = QueryMemorySkill(trinity_engine=mock_engine)
        registry.register(skill)

        # Check function declaration generation for LLM
        decls = registry.get_function_declarations()
        names = [d["name"] for d in decls]
        assert "query_memory" in names

        # Execute as simulated Gemini action
        executor = SkillExecutor(registry=registry, nav_client=None, reactive_safety=MagicMock())
        actions = [
            {"action_type": "query_memory", "args": {"query": "accedi ai dati della memoria"}}
        ]
        results = []
        async for t in executor.execute_actions_stream(actions):
            results.append(t)

        assert len(results) > 0
        assert any("memoria" in r.lower() or "ricordo" in r.lower() for r in results)
    asyncio.run(_run())
