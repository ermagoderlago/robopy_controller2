"""
Robot AI RAG - Chroma Native Memory Store
=========================================
Direct integration with ChromaDB bypassing LlamaIndex to prevent async-only embedding crashes.
Implements a client singleton with thread safety, semantic queries, and temporal metadata filters.
"""

import os
import time
import uuid
import logging
import threading
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path

import chromadb
from chromadb.config import Settings

from .memory_store import Memory, MemoryType, SearchResult
from ..utils.logging_utils import get_logger

_chroma_client = None
_client_lock = threading.Lock()

def get_chroma_client(persist_dir: str) -> chromadb.PersistentClient:
    global _chroma_client
    with _client_lock:
        if _chroma_client is None:
            _chroma_client = chromadb.PersistentClient(
                path=persist_dir,
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )
        return _chroma_client


class ChromaNativeStore:
    """
    High-performance native ChromaDB memory store.
    Replaces LlamaIndexMemoryStore to avoid async errors and LlamaIndex overhead.
    """
    def __init__(
        self,
        persist_dir: str = "/home/robopy/ChromaDB_Llama",
        collection_name: str = "robot_memories",
        embedding_service: Any = None
    ):
        self.logger = get_logger("chroma_native_store")
        self.persist_dir = Path(persist_dir)
        self.collection_name = collection_name
        self.embedding_service = embedding_service
        self._enabled = True
        self._lock = threading.RLock()

        try:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self.client = get_chroma_client(str(self.persist_dir))
            
            # cosine space for semantic similarity
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            self.logger.info(f"✅ ChromaNativeStore inizializzato con successo. Memorie: {self.collection.count()}")
        except Exception as e:
            import traceback
            self.logger.error(f"❌ Inizializzazione ChromaNativeStore fallita: {e}\n{traceback.format_exc()}")
            self._enabled = False

    def add(self, memory: Memory) -> str:
        """Add or update a memory node in ChromaDB."""
        if not self._enabled:
            return ""

        with self._lock:
            if not memory.id:
                # generate ID
                import hashlib
                timestamp = str(time.time())
                hash_input = f"{memory.content}{timestamp}".encode()
                memory.id = f"mem_{hashlib.sha256(hash_input).hexdigest()[:12]}"

            # Verification of embedding dimension to avoid vector space corruption
            if memory.embedding is None:
                if self.embedding_service:
                    try:
                        import asyncio
                        try:
                            loop = asyncio.get_event_loop()
                        except RuntimeError:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                        
                        if loop.is_running():
                            future = asyncio.run_coroutine_threadsafe(
                                self.embedding_service.embed(memory.content), loop
                            )
                            memory.embedding = future.result(timeout=5.0)
                        else:
                            memory.embedding = loop.run_until_complete(
                                self.embedding_service.embed(memory.content)
                            )
                    except Exception as e:
                        self.logger.error(f"Impossibile generare embedding sincrono per la memoria: {e}")
                        return ""

            if memory.embedding is not None:
                if len(memory.embedding) != 768:
                    self.logger.error(
                        f"❌ Corruzione spazio vettoriale: embedding ha dimensione {len(memory.embedding)} anziché 768. Record scartato."
                    )
                    return ""

            # Prepare metadata
            metadata = memory.metadata.copy()
            metadata.update({
                "memory_type": memory.memory_type.value if hasattr(memory.memory_type, "value") else memory.memory_type,
                "created_at": memory.created_at,
                "updated_at": memory.updated_at,
                "importance": memory.importance,
                "access_count": memory.access_count,
                "uuid": memory.id
            })

            # Clean metadata
            cleaned_metadata = {}
            for k, v in metadata.items():
                if isinstance(v, (dict, list)):
                    import json
                    cleaned_metadata[k] = json.dumps(v)
                elif v is None:
                    cleaned_metadata[k] = ""
                else:
                    cleaned_metadata[k] = v

            try:
                # Upsert: check if exists, delete first to overwrite cleanly
                if self.get_by_id(memory.id):
                    self.collection.delete(ids=[memory.id])
                    self.logger.debug(f"Aggiornata memoria esistente: {memory.id}")

                self.collection.add(
                    ids=[memory.id],
                    embeddings=[memory.embedding],
                    metadatas=[cleaned_metadata],
                    documents=[memory.content]
                )
                self.logger.debug(f"Inserita memoria: {memory.id}")
                return memory.id
            except Exception as e:
                self.logger.error(f"Errore durante l'aggiunta a ChromaDB: {e}")
                return ""

    def update(self, memory: Memory) -> None:
        """Update an existing memory."""
        memory.updated_at = time.time()
        self.add(memory)

    def delete(self, memory_id: str) -> bool:
        """Delete a memory by ID."""
        if not self._enabled:
            return False
        with self._lock:
            try:
                self.collection.delete(ids=[memory_id])
                self.logger.debug(f"Eliminata memoria: {memory_id}")
                return True
            except Exception as e:
                self.logger.error(f"Errore cancellazione memoria {memory_id}: {e}")
                return False

    def get(self, memory_id: str) -> Optional[Memory]:
        """Get a memory by ID."""
        if not self._enabled:
            return None
        with self._lock:
            try:
                results = self.collection.get(
                    ids=[memory_id],
                    include=["embeddings", "metadatas", "documents"]
                )
                if not results["ids"]:
                    return None
                
                meta = results["metadatas"][0] or {}
                # Increment access count
                meta["access_count"] = meta.get("access_count", 0) + 1
                self.collection.update(ids=[memory_id], metadatas=[meta])

                return Memory.from_dict(
                    {"id": memory_id, "content": results["documents"][0], **meta},
                    embedding=results["embeddings"][0] if results["embeddings"] else None
                )
            except Exception as e:
                self.logger.error(f"Errore nel recupero della memoria {memory_id}: {e}")
                return None

    def get_by_id(self, doc_id: str) -> bool:
        """Check if a document ID exists in the vector store."""
        if not self._enabled:
            return False
        try:
            res = self.collection.get(ids=[doc_id])
            return len(res['ids']) > 0
        except Exception:
            return False

    def get_recent(self, limit: int = 10, memory_type: MemoryType = None) -> List[Memory]:
        """Get most recent memories from ChromaDB using temporal sorting."""
        if not self._enabled:
            return []

        where_filter = {}
        if memory_type:
            where_filter["memory_type"] = memory_type.value if hasattr(memory_type, "value") else memory_type

        try:
            results = self.collection.get(
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

            # Sort in Python by created_at desc (newest first)
            memories.sort(key=lambda m: m.created_at, reverse=True)
            return memories[:limit]
        except Exception as e:
            self.logger.error(f"Errore nel recupero delle memorie recenti: {e}")
            return []

    async def search(self, query: str, top_k: int = 5) -> List[SearchResult]:
        """Search similar memories using native query and embeddings."""
        if not self._enabled or not self.embedding_service:
            return []

        try:
            query_embedding = await self.embedding_service.embed(query)
            
            with self._lock:
                results = self.collection.query(
                    query_embeddings=[query_embedding],
                    n_results=top_k,
                    include=["embeddings", "metadatas", "documents", "distances"]
                )

                search_results = []
                if not results.get("ids") or not results["ids"][0]:
                    return []

                for i, memory_id in enumerate(results["ids"][0]):
                    distance = results["distances"][0][i]
                    base_score = 1.0 - distance

                    meta = results["metadatas"][0][i] or {}
                    mem = Memory(
                        id=memory_id,
                        content=results["documents"][0][i],
                        memory_type=MemoryType(meta.get("memory_type", "conversation")),
                        metadata=meta,
                        created_at=meta.get("created_at", time.time())
                    )
                    search_results.append(SearchResult(memory=mem, score=base_score, distance=distance))

                return search_results
        except Exception as e:
            self.logger.error(f"Errore durante la ricerca semantica Chroma: {e}")
            return []
