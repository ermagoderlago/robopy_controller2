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

def test_metaprompt_fusion_dialogue_working_memory():
    fusion = MetapromptFusion()
    recent_dialogue = "Utente: cerca le mie chiavi\nMarcus: Inizio a cercare le chiavi in salotto."
    prompt = fusion.build_prompt(
        user_text="ti ricordi cosa ti ho detto di cercare?",
        system_prompt="Sei un assistente robotico.",
        recent_dialogue=recent_dialogue
    )

    assert "[CONVERSAZIONE RECENTE]" in prompt
    assert "Utente: cerca le mie chiavi" in prompt
    assert "Marcus: Inizio a cercare le chiavi in salotto." in prompt
    assert "ti ricordi cosa ti ho detto di cercare?" in prompt
    # Check that estimated token count remains strictly under 2500 ceiling
    token_est = fusion._estimate_tokens(prompt)
    assert token_est < 2200

def test_trinity_engine_conversation_history_propagation(temp_db_path):
    async def _test():
        engine = TrinityEngine(db_path=str(temp_db_path))
        conv_hist = [
            {"role": "user", "content": "trova lo zaino blu"},
            {"role": "assistant", "content": "Sto cercando lo zaino blu."}
        ]
        prompt = await engine.build_augmented_prompt(
            user_text="l'hai trovato?",
            conversation_history=conv_hist
        )
        assert "[CONVERSAZIONE RECENTE]" in prompt
        assert "Utente: trova lo zaino blu" in prompt
        assert "Marcus: Sto cercando lo zaino blu." in prompt
        assert "Utente: l'hai trovato?" in prompt
    asyncio.run(_test())

def test_query_memory_skill_not_hijacking_conversational_queries():
    from robot_ai.skills.builtin.query_memory_skill import QueryMemorySkill
    skill = QueryMemorySkill()

    # Conversational memory queries MUST NOT be hijacked with confidence >= 0.95
    conv_queries = [
        "ti ricordi cosa ti ho detto di cercare?",
        "cosa ti ricordi?",
        "ti ricordi di ieri?",
        "cosa abbiamo fatto?",
        "chi sono io?",
        "cosa sai di me?"
    ]
    for q in conv_queries:
        conf = skill.match(q)
        assert conf < 0.95, f"Query '{q}' returned confidence {conf} >= 0.95 (would trigger fast-path hijacking!)"

    # Explicit administrative memory dump can return <= 0.85 (below fast-path threshold 0.95)
    admin_conf = skill.match("accedi ai dati della memoria")
    assert admin_conf <= 0.85
    assert admin_conf > 0.0

def test_mag_temporal_parser():
    from robot_ai.trinity.mag_temporal_parser import MAGTemporalParser
    ref_dt = datetime(2026, 10, 3, 14, 30, 0)
    
    # 1. "oggi"
    res_oggi = MAGTemporalParser.parse_time_range("cosa abbiamo fatto oggi?", ref_dt=ref_dt)
    assert res_oggi is not None
    start, end, label = res_oggi
    assert label == "oggi"
    assert start == datetime(2026, 10, 3, 0, 0, 0).timestamp()

    # 2. "ieri"
    res_ieri = MAGTemporalParser.parse_time_range("cosa ti ho detto ieri sera?", ref_dt=ref_dt)
    assert res_ieri is not None
    start, end, label = res_ieri
    assert label == "ieri"
    assert start == datetime(2026, 10, 2, 0, 0, 0).timestamp()

    # 3. "2 ottobre"
    res_date = MAGTemporalParser.parse_time_range("cosa è successo il 2 ottobre?", ref_dt=ref_dt)
    assert res_date is not None
    start, end, label = res_date
    assert "2 ottobre" in label
    assert start == datetime(2026, 10, 2, 0, 0, 0).timestamp()

    # 4. "ultimi 5 giorni"
    res_5d = MAGTemporalParser.parse_time_range("riassumi gli ultimi 5 giorni", ref_dt=ref_dt)
    assert res_5d is not None
    assert "ultimi 5 giorni" in res_5d[2]

    # 5. Frequency & stats detection
    assert MAGTemporalParser.is_frequency_or_stats_query("quante volte abbiamo cercato le chiavi?")
    assert MAGTemporalParser.is_frequency_or_stats_query("mostrami la frequenza e statistiche dei ricordi")
    assert not MAGTemporalParser.is_frequency_or_stats_query("ciao come stai?")

    # 6. Cleaning temporal tokens
    clean = MAGTemporalParser.clean_temporal_tokens("cosa ti ho detto di cercare ieri?")
    assert "ieri" not in clean
    assert "cercare" in clean

def test_mag_database_temporal_range_and_stats(mag_db):
    now = datetime(2026, 10, 3, 12, 0, 0).timestamp()
    yesterday = datetime(2026, 10, 2, 12, 0, 0).timestamp()
    two_days_ago = datetime(2026, 10, 1, 12, 0, 0).timestamp()

    mag_db.insert_episode(user_input="Episodio oggi", robot_response="Oggi", timestamp=now)
    mag_db.insert_episode(user_input="Episodio ieri", robot_response="Ieri", timestamp=yesterday)
    mag_db.insert_episode(user_input="Episodio 2 giorni fa", robot_response="2gg", timestamp=two_days_ago)

    # Query range for yesterday
    start_y = datetime(2026, 10, 2, 0, 0, 0).timestamp()
    end_y = datetime(2026, 10, 2, 23, 59, 59).timestamp()
    eps_yesterday = mag_db.get_episodes_by_timerange(start_y, end_y)
    assert len(eps_yesterday) == 1
    assert eps_yesterday[0]["user_input"] == "Episodio ieri"

    # Query stats
    stats = mag_db.get_episodes_frequency_stats(days=7)
    assert stats["total_episodes_period"] == 3
    assert stats["average_per_day"] > 0

def test_prompt_sections_date_formatting(mag_db):
    from robot_ai.trinity.mag_zettelkasten import SemanticFactStore
    from robot_ai.trinity.mag_episodic import EpisodicMemoryEngine
    
    ts = datetime(2026, 10, 2, 15, 30, 0).timestamp()
    ep_id = mag_db.insert_episode(
        user_input="Cerca le mie scarpe",
        robot_response="Inizio la ricerca",
        timestamp=ts
    )
    fact_id = mag_db.insert_fact(
        fact_text="Luca possiede scarpe da ginnastica",
        fact_type="USER_PREFERENCE",
        source_episode_id=ep_id,
        confidence=0.9
    )
    # Manually backdate fact for test
    conn = mag_db._get_connection()
    conn.execute("UPDATE semantic_facts SET created_at = ? WHERE id = ?", (ts, fact_id))
    conn.commit()
    conn.close()

    engine = EpisodicMemoryEngine(mag_db)
    mem_data = engine.retrieve_relevant_memory("scarpe")
    sections = engine.to_prompt_sections(mem_data)

    # Verify episodic line contains date and time
    assert "02/10/2026" in sections["episodes"]
    assert "15:30" in sections["episodes"]
    assert "Cerca le mie scarpe" in sections["episodes"]

    # Verify fact contains date
    assert "02/10/2026" in sections["facts"]
    assert "Luca possiede scarpe da ginnastica" in sections["facts"]

def test_query_memory_skill_temporal_and_date_mapping(mag_db):
    from robot_ai.skills.builtin.query_memory_skill import QueryMemorySkill
    skill = QueryMemorySkill(trinity_engine=lambda: None)
    skill._mag_db = mag_db

    ts = datetime(2026, 10, 2, 10, 0, 0).timestamp()
    mag_db.insert_episode(
        user_input="ricordati che vado a correre",
        robot_response="Ricevuto, ti piace correre",
        timestamp=ts
    )

    async def _test():
        # 1. Date mapping query test (The exact screenshot issue)
        res_mapping = await skill.execute("la tua memoria mappa le date in cui crei i ricordi?")
        assert res_mapping.success
        assert "mappa con precisione" in res_mapping.speak.lower() or "registra" in res_mapping.speak.lower()

        # 2. Stats query
        res_stats = await skill.execute("quante volte abbiamo parlato questa settimana?")
        assert res_stats.success
        assert "frequenza" in res_stats.speak.lower() or "registrato" in res_stats.speak.lower()

    asyncio.run(_test())


