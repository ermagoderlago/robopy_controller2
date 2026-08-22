import pytest
import os
import asyncio
from pathlib import Path
from datetime import datetime

from robot_ai.trinity.intent_router import IntentRouter, IntentCategory
from robot_ai.trinity.metaprompt_fusion import MetapromptFusion
from robot_ai.trinity.mag_database import MAGDatabase
from robot_ai.trinity.mag_episodic import EpisodicMemoryEngine
from robot_ai.trinity.cag_aggregator import ContextAggregator
from robot_ai.trinity.cag_error_tracker import ErrorContextTracker
from robot_ai.trinity.cag_hardware_collector import HardwareStateCollector
from robot_ai.trinity.cag_environment import EnvironmentSnapshot
from robot_ai.trinity.trinity_engine import TrinityEngine

@pytest.fixture
def temp_db_path(tmp_path):
    return tmp_path / "test_mag.db"

@pytest.fixture
def mag_db(temp_db_path):
    db = MAGDatabase(db_path=str(temp_db_path))
    db.initialize()
    return db

@pytest.fixture
def episodic_engine(mag_db):
    return EpisodicMemoryEngine(mag_db)

@pytest.fixture
def context_aggregator():
    return ContextAggregator()

def test_intent_router():
    router = IntentRouter()
    
    # Coding query
    intent = router.classify("Scrivi un nodo ROS2 in Python")
    assert intent == IntentCategory.CODING
    
    # Navigation query
    intent = router.classify("Go to the kitchen")
    assert intent == IntentCategory.NAVIGATION
    
    # Diagnostic query
    intent = router.classify("Perché la CPU è calda?")
    assert intent == IntentCategory.DIAGNOSTIC
    
    # Verify retrieval config weights
    config = router.get_retrieval_config(intent)
    assert hasattr(config, "cag_hardware")
    assert config.cag_hardware == 1.0

def test_metaprompt_fusion():
    fusion = MetapromptFusion()
    
    prompt = fusion.build_prompt(
        user_text="How does ROS2 work?",
        system_prompt="You are a helpful assistant.",
        mag_episodes="Recent episodes summary",
        cag_hardware="Hardware: CPU 50%",
        rag_knowledge="ROS2 uses DDS"
    )
    
    # Check structures
    assert "[RUOLO DEL ROBOT]" in prompt
    assert "[MEMORIA STORICA (MAG)]" in prompt
    assert "[CONTESTO ATTUALE (CAG)]" in prompt
    assert "[CONOSCENZA RECUPERATA (RAG)]" in prompt
    assert "Recent episodes summary" in prompt
    assert "Hardware: CPU 50%" in prompt
    assert "ROS2 uses DDS" in prompt
    assert "How does ROS2 work?" in prompt

def test_mag_database_and_episodic(temp_db_path, mag_db):
    from robot_ai.trinity.mag_zettelkasten import SemanticFactStore
    from robot_ai.trinity.mag_user_profile import UserProfileEngine
    from robot_ai.trinity.mag_hybrid_search import HybridSearchEngine
    
    fact_store = SemanticFactStore(mag_db)
    user_profile = UserProfileEngine(mag_db)
    search_engine = HybridSearchEngine(mag_db)
    
    episodic_engine = EpisodicMemoryEngine(
        db=mag_db,
        fact_store=fact_store,
        user_profile=user_profile,
        search_engine=search_engine
    )

    # Episodic
    episode_id = mag_db.insert_episode(
        user_input="Ciao",
        robot_response="Ciao Luca",
        actions_taken=[],
        was_successful=True,
        user_id="Luca",
        summary="A greeting"
    )
    assert episode_id is not None
    
    recent = mag_db.get_recent_episodes(limit=5)
    assert len(recent) == 1
    assert recent[0]["user_input"] == "Ciao"
    
    search_res = mag_db.search_episodes_fts("Ciao")
    assert len(search_res) > 0
    
    # Fact
    fact_id = mag_db.insert_fact(
        fact_text="Luca likes apples",
        fact_type="USER_PREFERENCE",
        source_episode_id=episode_id,
        confidence=0.9
    )
    assert fact_id is not None
    
    facts = mag_db.search_facts_fts("apples")
    assert len(facts) > 0
    
    # User Profile
    mag_db.upsert_user_preference("Luca", "theme", "dark")
    profile = mag_db.get_user_profile("Luca")
    assert profile.get("theme") == "dark"
    
    # Record and Retrieve via Engine
    episodic_engine.record_episode(
        user_input="Test episodic record",
        robot_response="Success",
        actions=[],
        was_successful=True,
        user_id="Luca"
    )
    rel_memories = episodic_engine.retrieve_relevant_memory("episodic record")
    assert "episodes" in rel_memories
    assert "facts" in rel_memories

def test_context_aggregator(context_aggregator):
    async def _test():
        # Error tracker
        context_aggregator.errors.record_error("ROS2 Error", "Node crashed")
        last_err = context_aggregator.errors.get_last_error_context()
        assert last_err is not None
        assert last_err["source"] == "ROS2 Error"
        
        # Gather context
        snapshot = context_aggregator.get_snapshot()
        
        # Hardware might be mock/fallback but shouldn't crash
        assert "hardware_text" in snapshot
        assert "error_text" in snapshot
        assert "env_text" in snapshot
    asyncio.run(_test())

def test_trinity_engine_integration(temp_db_path):
    async def _test():
        engine = TrinityEngine(db_path=str(temp_db_path))
        
        prompt = await engine.build_augmented_prompt(
            user_text="Scrivi un nodo ROS 2 per il lidar",
            source="text",
            user_identity="Luca"
        )
        
        assert "Scrivi un nodo ROS 2 per il lidar" in prompt
        assert "[RUOLO DEL ROBOT]" in prompt
        assert "[MEMORIA STORICA (MAG)]" in prompt
        
        await engine.record_interaction(
            user_text="Scrivi un nodo ROS 2 per il lidar",
            robot_response="Ecco il codice...",
            user_identity="Luca"
        )
        
        # Verify in DB
        db = engine.mag_db
        recent = db.get_recent_episodes(1)
        assert len(recent) == 1
        assert recent[0]["user_input"] == "Scrivi un nodo ROS 2 per il lidar"
    asyncio.run(_test())

