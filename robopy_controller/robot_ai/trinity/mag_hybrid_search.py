"""
Hybrid Search Engine for MAG using Reciprocal Rank Fusion (RRF).
"""

from typing import List, Dict, Any
import threading
from robot_ai.utils import get_logger

logger = get_logger(__name__)

class HybridSearchEngine:
    def __init__(self, db: Any):
        self._db = db
        self._lock = threading.RLock()
        self.rrf_k = 60

    def compute_rrf(self, fts_results: List[Dict[str, Any]], vec_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Computes RRF(d) = sum(1 / (60 + rank_r(d)))
        """
        scores: Dict[str, float] = {}
        items: Dict[str, Dict[str, Any]] = {}

        for rank, item in enumerate(fts_results):
            doc_id = item.get("id")
            if not doc_id: continue
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (self.rrf_k + rank + 1)
            items[doc_id] = item

        for rank, item in enumerate(vec_results):
            doc_id = item.get("id")
            if not doc_id: continue
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (self.rrf_k + rank + 1)
            if doc_id not in items:
                items[doc_id] = item

        sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        
        merged_results = []
        for doc_id, score in sorted_docs:
            doc = items[doc_id].copy()
            doc["rrf_score"] = score
            merged_results.append(doc)
            
        return merged_results

    def search_episodes(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        with self._lock:
            fts_res = self._db.search_episodes_fts(query) if hasattr(self._db, 'search_episodes_fts') else []
            vec_res = self._db.search_episodes_vector(query) if hasattr(self._db, 'search_episodes_vector') else []
            
            merged = self.compute_rrf(fts_res, vec_res)
            return merged[:top_k]

    def search_facts(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        with self._lock:
            fts_res = self._db.search_facts_fts(query) if hasattr(self._db, 'search_facts_fts') else []
            vec_res = self._db.search_facts_vector(query) if hasattr(self._db, 'search_facts_vector') else []
            
            merged = self.compute_rrf(fts_res, vec_res)
            return merged[:top_k]
