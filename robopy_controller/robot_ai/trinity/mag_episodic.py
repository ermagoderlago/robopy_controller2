"""
Episodic Memory Engine for MAG.
"""

from typing import List, Dict, Any, Optional
import threading
from robot_ai.utils import get_logger

logger = get_logger(__name__)

class EpisodicMemoryEngine:
    def __init__(
        self,
        db: Any,
        fact_store: Optional[Any] = None,
        user_profile: Optional[Any] = None,
        search_engine: Optional[Any] = None
    ):
        self._db = db
        self._lock = threading.RLock()
        
        if fact_store is None:
            from .mag_zettelkasten import SemanticFactStore
            self._fact_store = SemanticFactStore(self._db)
        else:
            self._fact_store = fact_store

        if user_profile is None:
            from .mag_user_profile import UserProfileEngine
            self._user_profile = UserProfileEngine(self._db)
        else:
            self._user_profile = user_profile

        if search_engine is None:
            from .mag_hybrid_search import HybridSearchEngine
            self._search_engine = HybridSearchEngine(self._db)
        else:
            self._search_engine = search_engine

    def record_episode(self, user_input: str, robot_response: str, actions: Optional[List[str]] = None, 
                       was_successful: bool = True, user_id: Optional[str] = None, 
                       summary: Optional[str] = None, extracted_facts: Optional[List[Dict[str, Any]]] = None) -> str:
        with self._lock:
            episode_id = "mock_ep_id"
            if hasattr(self._db, 'insert_episode'):
                episode_id = self._db.insert_episode(
                    user_input=user_input,
                    robot_response=robot_response,
                    actions_taken=actions or [],
                    was_successful=1 if was_successful else 0,
                    user_id=user_id,
                    summary=summary
                )
            
            if extracted_facts and self._fact_store:
                for fact in extracted_facts:
                    self._fact_store.add_fact(
                        fact_text=fact.get("fact_text", ""),
                        fact_type=fact.get("fact_type", "CAPABILITY"),
                        source_episode_id=episode_id,
                        confidence=fact.get("confidence", 0.5)
                    )
            return episode_id

    def retrieve_relevant_memory(self, query_text: str, user_id: Optional[str] = None, 
                                 top_k_episodes: int = 3, top_k_facts: int = 4) -> Dict[str, Any]:
        with self._lock:
            episodes = self._search_engine.search_episodes(query_text, top_k_episodes)
            facts = self._search_engine.search_facts(query_text, top_k_facts)
            
            profile = ""
            if user_id:
                profile = self._user_profile.get_profile_summary(user_id)
                
            return {
                "episodes": episodes,
                "facts": facts,
                "user_profile": profile
            }

    def to_prompt_sections(self, memory_data: Dict[str, Any], max_tokens: int = 600) -> Dict[str, str]:
        sections = {}
        
        episodes_text = "RECENT EPISODES:\n"
        for ep in memory_data.get("episodes", []):
            episodes_text += f"- Q: {ep.get('user_input', '')} | A: {ep.get('robot_response', '')}\n"
        sections["episodes"] = episodes_text

        facts_text = self._fact_store.to_prompt_section(memory_data.get("facts", []), max_tokens=250)
        sections["facts"] = facts_text
        
        if memory_data.get("user_profile"):
            sections["user_profile"] = memory_data["user_profile"]
            
        return sections
