"""
Robot AI RAG - LlamaIndex Memory Store
======================================
Integration of LlamaIndex with ChromaDB for advanced semantic retrieval.
Supports UUID-based persistence and state-aware object memory.
"""

import os
import time
import uuid
import logging
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path

def check_gemini_key(api_key: str) -> bool:
    """Verifica se la chiave API è valida per evitare crash in LlamaIndex."""
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        # Una semplice chiamata per testare la validità
        genai.get_model("models/gemini-2.0-flash")
        return True
    except Exception:
        return False

try:
    import chromadb
    from llama_index.core import VectorStoreIndex, StorageContext, Document, Settings
    from llama_index.vector_stores.chroma import ChromaVectorStore
    from llama_index.llms.gemini import Gemini
    from llama_index.core.embeddings import BaseEmbedding
    from llama_index.embeddings.gemini import GeminiEmbedding as GoogleGenerativeAIEmbedding
    HAS_LLAMA = True
except ImportError:
    HAS_LLAMA = False
    BaseEmbedding = object # Fallback to avoid NameError
    GoogleGenerativeAIEmbedding = None

from .memory_store import Memory, MemoryType, SearchResult
from ..utils.logging_utils import get_logger

class RobopyEmbedding(BaseEmbedding):
    """Bridge between Robopy's EmbeddingService and LlamaIndex."""
    def __init__(self, embedding_service, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._service = embedding_service

    def _get_query_embedding(self, query: str) -> List[float]:
        return [] # Fallback, LlamaIndex uses async primarily

    def _get_text_embedding(self, text: str) -> List[float]:
        return []

    async def _aget_query_embedding(self, query: str) -> List[float]:
        return await self._service.embed(query)

    async def _aget_text_embedding(self, text: str) -> List[float]:
        return await self._service.embed(text)

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        return await self._service.embed_batch(texts)


class LlamaIndexMemoryStore:
    """
    Advanced memory store using LlamaIndex for RAG and semantic search.
    """
    def __init__(
        self, 
        persist_dir: str = "/home/robopy/ChromaDB_Llama", 
        collection_name: str = "robot_memories",
        embedding_service: Any = None
    ):
        self.logger = get_logger("llama_store")
        self.persist_dir = Path(persist_dir)
        self.collection_name = collection_name
        self._enabled = HAS_LLAMA
        
        if not self._enabled:
            self.logger.warning("LlamaIndex dependencies not found. Memory store will be basic.")
            return

        # Initialize ChromaDB persistent client
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.db = chromadb.PersistentClient(path=str(self.persist_dir))
        self.chroma_collection = self.db.get_or_create_collection(collection_name)
        
        # Setup LlamaIndex components
        self.vector_store = ChromaVectorStore(chroma_collection=self.chroma_collection)
        self.storage_context = StorageContext.from_defaults(vector_store=self.vector_store)
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key and check_gemini_key(api_key):
            try:
                # Configure Global Settings
                Settings.embed_model = GoogleGenerativeAIEmbedding(
                    model_name="models/embedding-001",
                    api_key=api_key
                )
                Settings.llm = Gemini(model_name="models/gemini-2.0-flash", api_key=api_key)
                
                # Initialize the index from existing vector store
                try:
                    self.index = VectorStoreIndex.from_vector_store(
                        self.vector_store, 
                        storage_context=self.storage_context
                    )
                except Exception as e:
                    self.logger.warning(f"Empty or corrupt index, creating fresh: {e}")
                    self.index = VectorStoreIndex([], storage_context=self.storage_context)
            except Exception as e:
                self.logger.error(f"⚠️ LlamaIndex Settings failure: {e}")
                self._enabled = False
        else:
            if not api_key:
                self.logger.error("❌ GEMINI_API_KEY non trovata!")
            else:
                self.logger.error("❌ GEMINI_API_KEY scaduta o non valida! Modulo RAG disabilitato.")
            self._enabled = False

        if self._enabled:
            self.logger.info(f"✅ LlamaIndex Memory Store inizializzato con successo.")
        else:
            self.logger.warning("🧱 LlamaIndex disabilitato. Funzionalità degradata.")

    def add(self, memory: Memory):
        """Add or update a memory node in LlamaIndex."""
        if not self._enabled:
            return
            
        # Meta-data mapping
        metadata = memory.metadata.copy()
        metadata.update({
            "memory_type": memory.memory_type.value,
            "created_at": memory.created_at,
            "uuid": memory.id
        })
        
        doc_id = memory.id if memory.id else str(uuid.uuid4())[:12]
        
        # Manual Upsert: check if exists in Chroma and delete before re-inserting
        # This ensures LlamaIndex correctly indexed the latest content/embeddings
        try:
            if memory.id and self.get_by_id(memory.id):
                self.chroma_collection.delete(ids=[memory.id])
                self.logger.debug(f"Updated existing memory node: {memory.id}")
            
            doc = Document(
                text=memory.content,
                id_=doc_id,
                metadata=metadata
            )
            
            self.index.insert(doc)
            self.logger.debug(f"Inserted LlamaIndex node: {doc.id_}")
        except Exception as e:
            self.logger.error(f"Failed to add memory to LlamaIndex: {e}")

    async def search(self, query: str, top_k: int = 5) -> List[SearchResult]:
        """Search similar memories using LlamaIndex retriever."""
        if not self._enabled:
            return []
            
        retriever = self.index.as_retriever(similarity_top_k=top_k)
        nodes = await retriever.aretrieve(query)
        
        results = []
        for node in nodes:
            meta = node.node.metadata
            mem = Memory(
                id=node.node.id_,
                content=node.node.text,
                memory_type=MemoryType(meta.get("memory_type", "visual_observation")),
                metadata=meta,
                created_at=meta.get("created_at", time.time())
            )
            results.append(SearchResult(memory=mem, score=node.score or 0.0))
            
        return results

    def get_by_id(self, doc_id: str) -> bool:
        """Check if a document ID exists in the vector store."""
        if not self._enabled:
            return False
        res = self.chroma_collection.get(ids=[doc_id])
        return len(res['ids']) > 0

    def get_recent(self, limit: int = 10, memory_type: MemoryType = None) -> List[Memory]:
        """Get most recent memories from ChromaDB collection."""
        if not self._enabled:
            return []
            
        where_filter = {}
        if memory_type:
            where_filter["memory_type"] = memory_type.value if hasattr(memory_type, "value") else memory_type
            
        try:
            results = self.chroma_collection.get(
                where=where_filter if where_filter else None,
                include=["metadatas", "documents"]
            )
            
            if not results.get("ids"):
                return []
                
            memories = []
            for i, memory_id in enumerate(results["ids"]):
                meta = results["metadatas"][i] or {}
                memories.append(Memory(
                    id=memory_id,
                    content=results["documents"][i],
                    memory_type=MemoryType(meta.get("memory_type", "conversation")),
                    metadata=meta,
                    created_at=meta.get("created_at", time.time())
                ))
                
            memories.sort(key=lambda m: m.created_at, reverse=True)
            return memories[:limit]
        except Exception as e:
            self.logger.error(f"Error getting recent memories: {e}")
            return []
