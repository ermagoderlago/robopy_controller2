"""
Hybrid Search Engine for MAG using Reciprocal Rank Fusion (RRF).
"""

from typing import List, Dict, Any, Optional
import threading
from robot_ai.utils import get_logger
from .mag_temporal_parser import MAGTemporalParser

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
            # 1. Temporal range check: if query specifies time/date (e.g. 'ieri', '2 ottobre', 'oggi')
            temporal_info = MAGTemporalParser.parse_time_range(query)
            if temporal_info and hasattr(self._db, 'get_episodes_by_timerange'):
                start_ts, end_ts, label = temporal_info
                range_episodes = self._db.get_episodes_by_timerange(start_ts, end_ts, limit=top_k * 3)
                if range_episodes:
                    clean_substantive = MAGTemporalParser.clean_temporal_tokens(query)
                    if clean_substantive and len(clean_substantive.split()) >= 1:
                        sub_tokens = set(clean_substantive.lower().split())
                        def score_ep(ep):
                            text = f"{ep.get('user_input', '')} {ep.get('robot_response', '')} {ep.get('summary', '')}".lower()
                            return sum(1 for tok in sub_tokens if tok in text)
                        range_episodes = sorted(range_episodes, key=score_ep, reverse=True)
                    return range_episodes[:top_k]

            # 2. Standard Hybrid search (FTS + Vector)
            fts_res = self._db.search_episodes_fts(query) if hasattr(self._db, 'search_episodes_fts') else []
            vec_res = self._db.search_episodes_vector(query) if hasattr(self._db, 'search_episodes_vector') else []
            
            merged = self.compute_rrf(fts_res, vec_res)
            # If search returns no results, check if broad/exploratory query or fallback to recent
            if not merged and hasattr(self._db, 'get_recent_episodes'):
                query_lower = query.lower()
                broad_keywords = [
                    "memoria", "ricord", "recent", "tutto", "interazion", "passat", 
                    "cosa sai", "dati", "accedi", "frequenza", "volte", "data", "date", "quando"
                ]
                if any(k in query_lower for k in broad_keywords) or len(query.strip().split()) <= 4:
                    return self._db.get_recent_episodes(limit=top_k)
            return merged[:top_k]

    def search_facts(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        with self._lock:
            # 1. Temporal range check
            temporal_info = MAGTemporalParser.parse_time_range(query)
            if temporal_info and hasattr(self._db, 'get_facts_by_timerange'):
                start_ts, end_ts, label = temporal_info
                range_facts = self._db.get_facts_by_timerange(start_ts, end_ts, limit=top_k * 3)
                if range_facts:
                    clean_substantive = MAGTemporalParser.clean_temporal_tokens(query)
                    if clean_substantive and len(clean_substantive.split()) >= 1:
                        sub_tokens = set(clean_substantive.lower().split())
                        def score_f(f):
                            text = f"{f.get('fact_text', '')}".lower()
                            return sum(1 for tok in sub_tokens if tok in text)
                        range_facts = sorted(range_facts, key=score_f, reverse=True)
                    return range_facts[:top_k]

            # 2. Standard Hybrid search
            fts_res = self._db.search_facts_fts(query) if hasattr(self._db, 'search_facts_fts') else []
            vec_res = self._db.search_facts_vector(query) if hasattr(self._db, 'search_facts_vector') else []
            
            merged = self.compute_rrf(fts_res, vec_res)
            # If search returns no results, check if broad/exploratory query or fallback to all facts
            if not merged and hasattr(self._db, 'get_all_facts'):
                query_lower = query.lower()
                broad_keywords = [
                    "memoria", "ricord", "fatti", "sai", "imparat", "appres", "tutto", 
                    "dati", "accedi", "profilo", "data", "date", "quando"
                ]
                if any(k in query_lower for k in broad_keywords) or len(query.strip().split()) <= 4:
                    facts = self._db.get_all_facts()
                    return facts[:top_k]
            return merged[:top_k]

