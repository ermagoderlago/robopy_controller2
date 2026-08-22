"""
Zettelkasten Semantic Fact Store for MAG.
"""

from typing import List, Dict, Any, Optional
import threading
from robot_ai.utils import get_logger

logger = get_logger(__name__)

class SemanticFactStore:
    def __init__(self, db: Any):
        self._db = db
        self._lock = threading.RLock()
        self.categories = [
            "HARDWARE_CONFIG",
            "USER_PREFERENCE",
            "API_DETAIL",
            "BUG_RESOLUTION",
            "CAPABILITY",
            "RELATIONSHIP"
        ]

    def add_fact(self, fact_text: str, fact_type: str, source_episode_id: Optional[str] = None, 
                 confidence: float = 0.5, embedding: Optional[List[float]] = None) -> str:
        with self._lock:
            if fact_type not in self.categories:
                logger.warning(f"Fact type '{fact_type}' is not a standard category.")
                
            # Semantic deduplication mock
            similar_facts = self._db.search_similar_facts(fact_text, threshold=0.85) if hasattr(self._db, 'search_similar_facts') else []
            
            if similar_facts:
                existing_fact = similar_facts[0]
                new_confidence = min(1.0, existing_fact.get('confidence', 0.5) + 0.1)
                fact_id = existing_fact.get('id', 'unknown')
                if hasattr(self._db, 'update_fact_confidence'):
                    self._db.update_fact_confidence(fact_id, new_confidence)
                return fact_id

            if hasattr(self._db, 'insert_fact'):
                return self._db.insert_fact(fact_text, fact_type, source_episode_id, confidence, embedding)
            return "mock_fact_id"

    def search_facts(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        with self._lock:
            if hasattr(self._db, 'search_facts'):
                return self._db.search_facts(query, top_k)
            return []

    def to_prompt_section(self, facts: List[Dict[str, Any]], max_tokens: int = 250) -> str:
        lines = ["ZETTELKASTEN FACTS:"]
        for fact in facts:
            lines.append(f"- [{fact.get('fact_type', 'FACT')}] {fact.get('fact_text', '')} (conf: {fact.get('confidence', 0.0):.2f})")
        
        section = "\n".join(lines)
        if len(section) > max_tokens * 4: # rough approximation
            section = section[:max_tokens * 4] + "...\n"
        return section
