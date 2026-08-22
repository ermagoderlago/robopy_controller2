import threading
import re
from typing import List, Dict, Any, Optional

from robot_ai.utils import get_logger

try:
    from robot_ai.rag.chroma_native_store import get_chroma_client
except ImportError:
    # Fallback to standard chromadb if not found
    import chromadb
    def get_chroma_client():
        return chromadb.PersistentClient(path="./chroma_db")

# For float16 embeddings
try:
    import torch
    from sentence_transformers import SentenceTransformer
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


logger = get_logger("trinity.rag_knowledge_query")


class KnowledgeQueryEngine:
    """
    Queries the TRINITY knowledge base with reranking and context formatting.
    """
    
    def __init__(self, embed_model_name: str = "all-MiniLM-L6-v2"):
        self._lock = threading.RLock()
        self.client = get_chroma_client()
        
        self.embed_model = None
        if HAS_TORCH:
            try:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                self.embed_model = SentenceTransformer(embed_model_name, device=device)
                if device != "cpu":
                    self.embed_model.half()
                logger.info(f"Initialized query embedding model {embed_model_name} on {device}")
            except Exception as e:
                logger.error(f"Failed to initialize query embedding model: {e}")
                
    def _generate_embedding(self, text: str) -> Optional[List[float]]:
        if not self.embed_model:
            return None
        try:
            with self._lock:
                embedding = self.embed_model.encode(text, convert_to_tensor=True)
                if embedding.is_floating_point():
                    embedding = embedding.half()
                return embedding.cpu().tolist()
        except Exception as e:
            logger.error(f"Error generating query embedding: {e}")
            return None
            
    def _compute_boost_score(self, query_text: str, document: str, metadata: Dict[str, Any], base_score: float) -> float:
        """
        Applies heuristic reranking weights. 
        For instance, boost python code snippets if query has code keywords.
        Note: ChromaDB returns distances (lower is better), so we'll invert to scores (higher is better).
        """
        # Convert distance to a similarity score between 0 and 1
        # Assumes cosine distance or L2 distance.
        sim_score = max(0.0, 1.0 - base_score) 
        
        code_keywords = ['def', 'class', 'import', 'function', 'code', 'script']
        query_lower = query_text.lower()
        
        has_code_intent = any(kw in query_lower for kw in code_keywords)
        is_code_file = metadata.get("extension", "") == ".py"
        
        if has_code_intent and is_code_file:
            sim_score += 0.15
            
        # Boost matches that contain exact keyword matches from the query
        keywords = set(re.findall(r'\w+', query_lower))
        doc_lower = document.lower()
        match_count = sum(1 for kw in keywords if kw in doc_lower and len(kw) > 3)
        sim_score += (match_count * 0.02)
        
        return min(sim_score, 1.0)

    def query_knowledge(self, query_text: str, top_k: int = 5, min_score: float = 0.35, collection_name: str = "marcus_knowledge_base") -> List[Dict[str, Any]]:
        """
        Queries the ChromaDB collection and returns formatted, reranked chunks.
        """
        with self._lock:
            try:
                collection = self.client.get_or_create_collection(
                    name=collection_name,
                    metadata={"hnsw:space": "cosine"}
                )
            except Exception as e:
                logger.debug(f"Collection {collection_name} error: {e}")
                return []
                
            if collection.count() == 0:
                return []

            query_embedding = self._generate_embedding(query_text)
            
            try:
                if query_embedding:
                    results = collection.query(
                        query_embeddings=[query_embedding],
                        n_results=min(top_k * 2, collection.count())
                    )
                else:
                    results = collection.query(
                        query_texts=[query_text],
                        n_results=min(top_k * 2, collection.count())
                    )
            except Exception as e:
                logger.debug(f"Error querying ChromaDB {collection_name}: {e}")
                return []
                
        if not results['documents'] or not results['documents'][0]:
            return []
            
        processed_results = []
        docs = results['documents'][0]
        metadatas = results['metadatas'][0]
        distances = results['distances'][0] if 'distances' in results and results['distances'] else [0.0] * len(docs)
        
        for doc, meta, dist in zip(docs, metadatas, distances):
            score = self._compute_boost_score(query_text, doc, meta, dist)
            if score >= min_score:
                processed_results.append({
                    "content": doc,
                    "metadata": meta,
                    "score": score
                })
                
        # Sort by score descending and take top_k
        processed_results.sort(key=lambda x: x["score"], reverse=True)
        return processed_results[:top_k]

    def format_for_prompt(self, results: List[Dict[str, Any]], max_tokens: int = 800) -> str:
        """
        Formats retrieved knowledge chunks into a concise string for LLM injection.
        """
        if not results:
            return "No relevant context found."
            
        formatted_text = "### RELEVANT CONTEXT ###\n"
        current_length = 0
        
        # Rough estimation: 1 token ~ 4 chars
        char_limit = max_tokens * 4
        
        for idx, res in enumerate(results):
            meta = res["metadata"]
            score = res["score"]
            content = res["content"].strip()
            
            file_path = meta.get("file_path", "unknown")
            start_line = meta.get("start_line", 0)
            end_line = meta.get("end_line", 0)
            
            chunk_header = f"\n[Source: {file_path}"
            if start_line and end_line:
                chunk_header += f" (Lines {start_line}-{end_line})"
            chunk_header += f", Relevance: {score:.2f}]\n"
            
            chunk_text = chunk_header + content + "\n"
            
            if current_length + len(chunk_text) > char_limit and idx > 0:
                formatted_text += "\n[Context truncated due to length limits...]\n"
                break
                
            formatted_text += chunk_text
            current_length += len(chunk_text)
            
        return formatted_text
