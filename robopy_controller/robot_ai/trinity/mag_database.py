"""
MAG (Memory-Augmented Generation) Database for TRINITY system.
"""

import sqlite3
import threading
import uuid
import struct
import json
import time
from typing import List, Optional, Dict, Any, Tuple

from robot_ai.utils.logging_utils import get_logger

logger = get_logger(__name__)

class MAGDatabase:
    def __init__(self, db_path: str = "/home/robopy/mag_trinity.db"):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Creates and returns a SQLite connection."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initializes the database schema with WAL mode and FTS5 tables."""
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                
                # PRAGMA for performance and crash protection (Pi 5)
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                
                # 1. episodes table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS episodes (
                        id TEXT PRIMARY KEY,
                        timestamp REAL NOT NULL,
                        user_input TEXT NOT NULL,
                        robot_response TEXT NOT NULL,
                        summary TEXT,
                        embedding BLOB,
                        user_id TEXT,
                        session_id TEXT,
                        emotion_tag TEXT,
                        importance REAL DEFAULT 0.5,
                        recall_count INTEGER DEFAULT 0,
                        actions_taken TEXT,
                        was_successful INTEGER DEFAULT 1
                    )
                ''')
                
                # 2. semantic_facts table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS semantic_facts (
                        id TEXT PRIMARY KEY,
                        fact_text TEXT NOT NULL,
                        fact_type TEXT NOT NULL,
                        source_episode_id TEXT REFERENCES episodes(id),
                        confidence REAL DEFAULT 0.5,
                        created_at REAL NOT NULL,
                        last_accessed REAL,
                        embedding BLOB,
                        recall_count INTEGER DEFAULT 0
                    )
                ''')
                
                # 3. user_profiles table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_profiles (
                        id TEXT PRIMARY KEY,
                        user_name TEXT NOT NULL,
                        preference_key TEXT NOT NULL,
                        preference_value TEXT NOT NULL,
                        source TEXT,
                        confidence REAL DEFAULT 0.5,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        UNIQUE(user_name, preference_key)
                    )
                ''')
                
                # 4. FTS5 Virtual Tables
                cursor.execute('''
                    CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                        user_input, 
                        robot_response, 
                        summary, 
                        content='episodes', 
                        content_rowid='rowid'
                    )
                ''')
                
                cursor.execute('''
                    CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                        fact_text,
                        content='semantic_facts',
                        content_rowid='rowid'
                    )
                ''')
                
                # Triggers to keep FTS updated
                cursor.execute('''
                    CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
                        INSERT INTO episodes_fts(rowid, user_input, robot_response, summary) 
                        VALUES (new.rowid, new.user_input, new.robot_response, new.summary);
                    END;
                ''')
                
                cursor.execute('''
                    CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON semantic_facts BEGIN
                        INSERT INTO facts_fts(rowid, fact_text) 
                        VALUES (new.rowid, new.fact_text);
                    END;
                ''')

                conn.commit()
                conn.close()
                logger.info(f"MAG Database initialized at {self.db_path} with WAL mode.")
            except Exception as e:
                logger.error(f"Error initializing MAG database: {e}")
                raise

    def initialize(self) -> None:
        """Initializes or ensures database schema is ready."""
        self._init_db()

    def _pack_embedding(self, embedding: Optional[List[float]]) -> Optional[bytes]:
        """Pack a list of floats into a float16 BLOB for storage."""
        if not embedding:
            return None
        # Use 'e' for float16
        return struct.pack(f'{len(embedding)}e', *embedding)
        
    def _unpack_embedding(self, blob: Optional[bytes]) -> Optional[List[float]]:
        """Unpack a float16 BLOB into a list of floats."""
        if not blob:
            return None
        num_floats = len(blob) // 2
        return list(struct.unpack(f'{num_floats}e', blob))

    def insert_episode(
        self,
        user_input: str = "",
        robot_response: str = "",
        timestamp: Optional[float] = None,
        summary: Optional[str] = None,
        embedding: Optional[List[float]] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        emotion_tag: Optional[str] = None,
        importance: float = 0.5,
        actions_taken: Optional[List[Any]] = None,
        actions: Optional[List[Any]] = None,
        was_successful: Any = 1
    ) -> str:
        """Insert a new episode into the MAG database."""
        ep_id = str(uuid.uuid4())
        if timestamp is None:
            timestamp = time.time()
        
        final_actions = actions_taken if actions_taken is not None else actions
        actions_json = json.dumps(final_actions) if final_actions else None
        blob_embedding = self._pack_embedding(embedding)
        success_int = 1 if was_successful else 0
        
        with self._lock:
            try:
                conn = self._get_connection()
                conn.execute('''
                    INSERT INTO episodes (
                        id, timestamp, user_input, robot_response, summary, 
                        embedding, user_id, session_id, emotion_tag, 
                        importance, actions_taken, was_successful
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    ep_id, timestamp, user_input, robot_response, summary,
                    blob_embedding, user_id, session_id, emotion_tag,
                    importance, actions_json, success_int
                ))
                conn.commit()
                conn.close()
                return ep_id
            except Exception as e:
                logger.error(f"Failed to insert episode: {e}")
                raise

    def insert_fact(
        self,
        fact_text: str,
        fact_type: str,
        source_episode_id: Optional[str] = None,
        confidence: float = 0.5,
        embedding: Optional[List[float]] = None
    ) -> str:
        """Insert a new semantic fact into the MAG database."""
        fact_id = str(uuid.uuid4())
        blob_embedding = self._pack_embedding(embedding)
        now = time.time()
        
        with self._lock:
            try:
                conn = self._get_connection()
                conn.execute('''
                    INSERT INTO semantic_facts (
                        id, fact_text, fact_type, source_episode_id,
                        confidence, created_at, embedding
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    fact_id, fact_text, fact_type, source_episode_id,
                    confidence, now, blob_embedding
                ))
                conn.commit()
                conn.close()
                return fact_id
            except Exception as e:
                logger.error(f"Failed to insert fact: {e}")
                raise

    def update_fact_confidence(self, fact_id: str, new_confidence: float) -> None:
        """Updates the confidence of an existing fact."""
        with self._lock:
            try:
                conn = self._get_connection()
                conn.execute("UPDATE semantic_facts SET confidence = ?, last_accessed = ? WHERE id = ?",
                             (new_confidence, time.time(), fact_id))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Failed to update fact confidence: {e}")

    def upsert_user_preference(
        self,
        user_name: str,
        preference_key: str,
        preference_value: str,
        source: Optional[str] = None,
        confidence: float = 0.5
    ) -> str:
        """Insert or update a user preference."""
        prof_id = str(uuid.uuid4())
        now = time.time()
        
        with self._lock:
            try:
                conn = self._get_connection()
                conn.execute('''
                    INSERT INTO user_profiles (
                        id, user_name, preference_key, preference_value,
                        source, confidence, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_name, preference_key) DO UPDATE SET
                        preference_value=excluded.preference_value,
                        source=excluded.source,
                        confidence=excluded.confidence,
                        updated_at=excluded.updated_at
                ''', (
                    prof_id, user_name, preference_key, preference_value,
                    source, confidence, now, now
                ))
                conn.commit()
                # Get the actual ID if it was an update
                cursor = conn.execute(
                    "SELECT id FROM user_profiles WHERE user_name=? AND preference_key=?", 
                    (user_name, preference_key)
                )
                row = cursor.fetchone()
                conn.close()
                return row['id'] if row else prof_id
            except Exception as e:
                logger.error(f"Failed to upsert user preference: {e}")
                raise

    def _sanitize_fts_query(self, query: str) -> str:
        """Sanitizes input string for FTS5 queries."""
        import re
        tokens = re.findall(r'\w+', query)
        if not tokens:
            return ""
        # Join words with OR for high recall
        return " OR ".join(tokens)

    def search_episodes_fts(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search episodes using Full-Text Search."""
        clean_q = self._sanitize_fts_query(query)
        if not clean_q:
            return []
            
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.execute('''
                    SELECT e.* FROM episodes_fts f
                    JOIN episodes e ON e.rowid = f.rowid
                    WHERE episodes_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                ''', (clean_q, limit))
                rows = [dict(row) for row in cursor.fetchall()]
                conn.close()
                return rows
            except Exception as e:
                logger.error(f"Failed to search episodes FTS: {e}")
                return []

    def search_facts_fts(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search semantic facts using Full-Text Search."""
        clean_q = self._sanitize_fts_query(query)
        if not clean_q:
            return []
            
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.execute('''
                    SELECT f.* FROM facts_fts ft
                    JOIN semantic_facts f ON f.rowid = ft.rowid
                    WHERE facts_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                ''', (clean_q, limit))
                rows = [dict(row) for row in cursor.fetchall()]
                conn.close()
                return rows
            except Exception as e:
                logger.error(f"Failed to search facts FTS: {e}")
                return []

    def search_facts(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Alias for search_facts_fts."""
        return self.search_facts_fts(query, limit=limit)

    def search_similar_facts(self, fact_text: str, threshold: float = 0.85) -> List[Dict[str, Any]]:
        """Searches for existing similar facts via FTS keyword overlap."""
        return self.search_facts_fts(fact_text, limit=3)

    def get_user_profile(self, user_name: str) -> Dict[str, Any]:
        """Get all preferences for a given user."""
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.execute(
                    "SELECT * FROM user_profiles WHERE user_name = ?", 
                    (user_name,)
                )
                rows = [dict(row) for row in cursor.fetchall()]
                conn.close()
                profile = {row['preference_key']: row['preference_value'] for row in rows}
                return profile
            except Exception as e:
                logger.error(f"Failed to get user profile for {user_name}: {e}")
                return {}

    def get_user_preferences(self, user_name: str) -> Dict[str, Any]:
        """Alias for get_user_profile."""
        return self.get_user_profile(user_name)

    def update_user_preference(self, user_name: str, key: str, value: str, source: Optional[str] = "conversation") -> str:
        """Alias for upsert_user_preference."""
        return self.upsert_user_preference(user_name=user_name, preference_key=key, preference_value=value, source=source)

    def get_recent_episodes(self, user_id: Optional[Any] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """Get the most recent episodes, optionally filtered by user_id."""
        if isinstance(user_id, int):
            limit = user_id
            user_id = None
            
        with self._lock:
            try:
                conn = self._get_connection()
                if user_id:
                    cursor = conn.execute(
                        "SELECT * FROM episodes WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?", 
                        (user_id, limit)
                    )
                else:
                    cursor = conn.execute(
                        "SELECT * FROM episodes ORDER BY timestamp DESC LIMIT ?", 
                        (limit,)
                    )
                rows = [dict(row) for row in cursor.fetchall()]
                conn.close()
                return rows
            except Exception as e:
                logger.error(f"Failed to get recent episodes: {e}")
                return []

    def increment_recall_count(self, table: str, record_id: str) -> None:
        """Increment the recall_count of a record in the specified table."""
        if table not in ["episodes", "semantic_facts"]:
            logger.error(f"Invalid table for increment_recall_count: {table}")
            return
            
        with self._lock:
            try:
                conn = self._get_connection()
                conn.execute(
                    f"UPDATE {table} SET recall_count = recall_count + 1 WHERE id = ?",
                    (record_id,)
                )
                
                # Also update last_accessed for semantic_facts
                if table == "semantic_facts":
                    conn.execute(
                        "UPDATE semantic_facts SET last_accessed = ? WHERE id = ?",
                        (time.time(), record_id)
                    )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Failed to increment recall count for {table}:{record_id}: {e}")

    def get_stats(self) -> Dict[str, int]:
        """Get database statistics."""
        with self._lock:
            try:
                conn = self._get_connection()
                episodes_count = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
                facts_count = conn.execute("SELECT COUNT(*) FROM semantic_facts").fetchone()[0]
                profiles_count = conn.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]
                conn.close()
                return {
                    "total_episodes": episodes_count,
                    "total_facts": facts_count,
                    "total_profiles": profiles_count
                }
            except Exception as e:
                logger.error(f"Failed to get stats: {e}")
                return {}

    def close(self) -> None:
        """Optional cleanup logic, as connections are handled per-operation."""
        pass
