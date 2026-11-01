import sys
import os
import sqlite3
import tempfile
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.robot_ai.trinity.mag_database import MAGDatabase


def test_mag_database_sqlite_wal_mode_red_zone():
    """SPEC-05 Red Zone: SQLite must operate strictly in WAL mode + synchronous = NORMAL."""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".db") as f:
        tmp_db = f.name

    try:
        db = MAGDatabase(db_path=tmp_db)
        
        # Verify PRAGMA journal_mode and PRAGMA synchronous
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode;")
            journal_mode = cursor.fetchone()[0].upper()
            assert journal_mode == "WAL", f"SQLite WAL mode required, got: {journal_mode}"

            cursor.execute("PRAGMA synchronous;")
            sync_mode = cursor.fetchone()[0]
            # synchronous = 1 is NORMAL
            assert sync_mode in (1, "NORMAL", "1"), f"SQLite synchronous=NORMAL required, got: {sync_mode}"
        finally:
            conn.close()
    finally:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
        if os.path.exists(tmp_db + "-wal"):
            os.remove(tmp_db + "-wal")
        if os.path.exists(tmp_db + "-shm"):
            os.remove(tmp_db + "-shm")


def test_mag_database_crud_and_fts5_indexing():
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".db") as f:
        tmp_db = f.name

    try:
        db = MAGDatabase(db_path=tmp_db)

        # 1. Insert Episode
        ep_id = db.insert_episode(
            user_input="Marcus, ricordati che il mio cane si chiama Argo",
            robot_response="Certamente! Ricorderò che il tuo cane si chiama Argo.",
            actions_taken=[],
            was_successful=True,
            user_id="Luca",
            summary="Nome cane Argo"
        )
        assert ep_id is not None

        # 2. Search via FTS5
        search_res = db.search_episodes_fts("Argo")
        assert len(search_res) >= 1
        assert "Argo" in search_res[0]["user_input"]

        # 3. Insert and Search Facts
        fact_id = db.insert_fact(
            fact_text="Il cane di Luca si chiama Argo",
            fact_type="USER_FACT",
            source_episode_id=ep_id,
            confidence=0.95
        )
        assert fact_id is not None
        facts_found = db.search_facts_fts("Argo")
        assert len(facts_found) >= 1

        # 4. Upsert User Preference
        db.upsert_user_preference("Luca", "dog_name", "Argo")
        profile = db.get_user_profile("Luca")
        assert profile.get("dog_name") == "Argo"

    finally:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
        if os.path.exists(tmp_db + "-wal"):
            os.remove(tmp_db + "-wal")
        if os.path.exists(tmp_db + "-shm"):
            os.remove(tmp_db + "-shm")
