"""
Robot AI RAG - ChromaDB Memory Store
=====================================
Async-first ChromaDB backend implementing BaseMemoryStore.
Embeds text internally via EmbeddingService — callers pass plain text.
"""

import asyncio
import hashlib
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False

from .base_memory_store import (
    BaseMemoryStore,
    MemoryStoreError,
    EmbeddingTimeoutError,
    MemoryType,
    SearchResult,
)
from ..utils.logging_utils import get_logger


# ---------------------------------------------------------------------------
# Legacy dataclasses kept for backward compat with VisualMemoryService
# ---------------------------------------------------------------------------
from dataclasses import dataclass, field


@dataclass
class Memory:
    """Legacy Memory dataclass — kept for backward compatibility."""
    id: str
    content: str
    memory_type: MemoryType
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    importance: float = 0.5
    access_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "importance": self.importance,
            "access_count": self.access_count,
            **self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], embedding: List[float] = None) -> "Memory":
        core_fields = {
            "id", "content", "memory_type", "created_at",
            "updated_at", "importance", "access_count",
        }
        metadata = {k: v for k, v in data.items() if k not in core_fields}
        return cls(
            id=data["id"],
            content=data.get("content", ""),
            memory_type=MemoryType(data.get("memory_type", "conversation")),
            embedding=embedding,
            metadata=metadata,
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            importance=data.get("importance", 0.5),
            access_count=data.get("access_count", 0),
        )


class ChromaMemoryStore(BaseMemoryStore):
    """
    Async ChromaDB-based memory storage implementing BaseMemoryStore.

    Key differences from the old MemoryStore:
      - All methods are async (wraps sync ChromaDB via asyncio.to_thread).
      - Accepts an EmbeddingService to embed text internally.
      - Callers pass plain text, not pre-computed embeddings.
    """

    def __init__(
        self,
        embedding_service,
        persist_dir: str = "/home/robopy/ChromaDB",
        collection_name: str = "robot_memories",
        embedding_dimension: int = 3072,
    ):
        self.logger = get_logger("memory_store")
        self.persist_dir = Path(persist_dir)
        self.collection_name = collection_name
        self.embedding_dimension = embedding_dimension
        self._embedding_service = embedding_service
        self._enabled = HAS_CHROMADB
        self._lock = threading.RLock()

        if not self._enabled:
            self.logger.warning("ChromaDB not found. Memory store disabled.")
            self._client = None
            self._collection = None
            return

        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self.logger.info(
            f"ChromaMemoryStore initialized (count={self._collection.count()})"
        )

    # ----- helpers ----------------------------------------------------------

    async def _embed(self, text: str) -> List[float]:
        """Embed text using the injected EmbeddingService (with timeout)."""
        try:
            return await asyncio.wait_for(
                self._embedding_service.embed(text), timeout=15.0
            )
        except asyncio.TimeoutError:
            raise EmbeddingTimeoutError(f"Embedding timed out for: {text[:60]}…")

    @staticmethod
    def _generate_id(content: str) -> str:
        ts = str(time.time())
        return f"mem_{hashlib.sha256(f'{content}{ts}'.encode()).hexdigest()[:12]}"

    def _to_search_result(
        self, doc_id: str, content: str, metadata: Dict[str, Any], score: float = 0.0
    ) -> SearchResult:
        return SearchResult(
            content=content,
            score=score,
            metadata=metadata,
            timestamp=metadata.get("created_at", 0.0),
            memory_id=doc_id,
            memory_type=MemoryType(metadata.get("memory_type", "conversation")),
        )

    # ----- BaseMemoryStore contract ----------------------------------------

    async def add(self, text: str, metadata: Dict[str, Any]) -> bool:
        if not self._enabled:
            return False
        mem_type = metadata.get("memory_type", MemoryType.CONVERSATION.value)
        mem_id = metadata.pop("id", None) or self._generate_id(text)

        embedding = metadata.pop("embedding", None)
        if embedding is None:
            embedding = await self._embed(text)

        store_meta = {
            "memory_type": mem_type if isinstance(mem_type, str) else mem_type.value,
            "created_at": metadata.get("created_at", time.time()),
            "updated_at": time.time(),
            "importance": metadata.get("importance", 0.5),
            "access_count": 0,
        }
        # Merge remaining metadata (only JSON-serialisable scalars)
        for k, v in metadata.items():
            if isinstance(v, (str, int, float, bool)):
                store_meta[k] = v

        def _sync_add():
            with self._lock:
                self._collection.add(
                    ids=[mem_id],
                    embeddings=[embedding],
                    metadatas=[store_meta],
                    documents=[text],
                )

        await asyncio.to_thread(_sync_add)
        self.logger.debug(f"Added memory: {mem_id}")
        return True

    async def search(self, query: str, top_k: int = 3) -> List[SearchResult]:
        if not self._enabled:
            return []

        embedding = await self._embed(query)

        def _sync_search():
            with self._lock:
                return self._collection.query(
                    query_embeddings=[embedding],
                    n_results=top_k,
                    include=["metadatas", "documents", "distances"],
                )

        results = await asyncio.to_thread(_sync_search)

        out: List[SearchResult] = []
        for i, doc_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i]
            score = max(0.0, 1.0 - distance)
            meta = results["metadatas"][0][i]
            content = results["documents"][0][i]
            out.append(self._to_search_result(doc_id, content, meta, score))

        out.sort(key=lambda r: r.score, reverse=True)
        return out

    async def get(self, memory_id: str) -> Optional[SearchResult]:
        if not self._enabled:
            return None

        def _sync_get():
            with self._lock:
                return self._collection.get(
                    ids=[memory_id],
                    include=["metadatas", "documents"],
                )

        results = await asyncio.to_thread(_sync_get)
        if not results["ids"]:
            return None

        meta = results["metadatas"][0]
        content = results["documents"][0]
        return self._to_search_result(memory_id, content, meta)

    async def update(self, memory_id: str, text: str, metadata: Dict[str, Any]) -> bool:
        if not self._enabled:
            return False

        existing = await self.get(memory_id)
        if existing is None:
            return False

        embedding = await self._embed(text)
        metadata["updated_at"] = time.time()

        store_meta = {}
        for k, v in metadata.items():
            if isinstance(v, (str, int, float, bool)):
                store_meta[k] = v

        def _sync_update():
            with self._lock:
                self._collection.update(
                    ids=[memory_id],
                    embeddings=[embedding],
                    metadatas=[store_meta],
                    documents=[text],
                )

        await asyncio.to_thread(_sync_update)
        self.logger.debug(f"Updated memory: {memory_id}")
        return True

    async def get_recent(self, limit: int = 5) -> List[SearchResult]:
        if not self._enabled:
            return []

        def _sync_get_recent():
            with self._lock:
                return self._collection.get(
                    include=["metadatas", "documents"],
                )

        results = await asyncio.to_thread(_sync_get_recent)
        if not results["ids"]:
            return []

        items: List[SearchResult] = []
        for i, doc_id in enumerate(results["ids"]):
            meta = results["metadatas"][i]
            content = results["documents"][i]
            items.append(self._to_search_result(doc_id, content, meta))

        items.sort(key=lambda r: r.timestamp, reverse=True)
        return items[:limit]

    async def count(self) -> int:
        if not self._enabled:
            return 0

        def _sync_count():
            return self._collection.count()

        return await asyncio.to_thread(_sync_count)

    # ----- Legacy compat shims (VisualMemoryService) -----------------------

    def add_sync(self, memory: "Memory") -> str:
        """Sync shim for VisualMemoryService — prefer async add()."""
        if not self._enabled:
            return ""
        if not memory.id:
            memory.id = self._generate_id(memory.content)
        if not memory.embedding:
            self.logger.warning("add_sync called without embedding — skipping")
            return ""

        meta = memory.to_dict()
        del meta["id"]
        # Filter to JSON-serialisable scalars only
        clean_meta = {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))}

        with self._lock:
            self._collection.add(
                ids=[memory.id],
                embeddings=[memory.embedding],
                metadatas=[clean_meta],
                documents=[memory.content],
            )
        return memory.id

    def get_sync(self, memory_id: str) -> Optional["Memory"]:
        """Sync shim for VisualMemoryService — prefer async get()."""
        if not self._enabled:
            return None
        with self._lock:
            results = self._collection.get(
                ids=[memory_id],
                include=["embeddings", "metadatas", "documents"],
            )
        if not results["ids"]:
            return None
        meta = results["metadatas"][0]
        return Memory.from_dict(
            {"id": memory_id, "content": results["documents"][0], **meta},
            embedding=results["embeddings"][0] if results["embeddings"] else None,
        )

    def update_sync(self, memory: "Memory") -> None:
        """Sync shim for VisualMemoryService — prefer async update()."""
        if not self._enabled:
            return
        memory.updated_at = time.time()
        meta = memory.to_dict()
        del meta["id"]
        clean_meta = {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))}
        update_kwargs = {
            "ids": [memory.id],
            "metadatas": [clean_meta],
            "documents": [memory.content],
        }
        if memory.embedding:
            update_kwargs["embeddings"] = [memory.embedding]

        with self._lock:
            self._collection.update(**update_kwargs)


# Alias for backward compatibility with imports
MemoryStore = ChromaMemoryStore
