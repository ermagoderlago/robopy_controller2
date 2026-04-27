"""
Robot AI RAG - LlamaIndex Memory Store  (Sprint 2+3 - P1/P2)
=============================================================
LlamaIndex + ChromaDB backend implementing BaseMemoryStore.

Sprint 2 (P1):
  P5 - RobopyEmbedding: bridge to EmbeddingService (no double pipeline).
  P6 - Deterministic fallback: search() never raises.
  P7 - Shed-load + timeouts (CPU gate, 3s embed, 2s retrieve).

Sprint 3 (P2):
  P9  - L1 Hot Cache: deque(maxlen=20) of SearchResult, asyncio.Lock-protected.
        get_recent() serves from L1 first, then fills from Chroma.
  P10 - Hash-dedup in add(): asyncio.Lock-protected dict[sha256→ts],
        max 1024 entries, TTL 10min. Duplicate writes are silently dropped.
  P11 - Observability: add_ok / add_dropped / search_ok / search_fail counters.
        Dump to logger every 5 min OR every 200 cumulative ops (first wins).
"""

import asyncio
import hashlib
import collections
import os
import time
import uuid
from typing import Any, Dict, List, Optional
from pathlib import Path

from .base_memory_store import (
    BaseMemoryStore,
    MemoryStoreError,
    EmbeddingTimeoutError,
    MemoryShedError,
    MemoryType,
    SearchResult,
    make_unavailable_result,
)
from ..utils.logging_utils import get_logger

# ---------------------------------------------------------------------------
# Optional LlamaIndex imports
# ---------------------------------------------------------------------------
try:
    import chromadb
    from llama_index.core import VectorStoreIndex, StorageContext, Document, Settings
    from llama_index.vector_stores.chroma import ChromaVectorStore
    from llama_index.llms.gemini import Gemini
    from llama_index.core.embeddings import BaseEmbedding

    try:
        from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
    except ImportError:
        from llama_index.embeddings.gemini import GeminiEmbedding as GoogleGenAIEmbedding

    HAS_LLAMA = True
    LLAMA_IMPORT_ERROR = None
except ImportError as e:
    HAS_LLAMA = False
    LLAMA_IMPORT_ERROR = str(e)
    BaseEmbedding = object  # avoid NameError

# ---------------------------------------------------------------------------
# psutil for CPU-gate (graceful degradation if missing)
# ---------------------------------------------------------------------------
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ---------------------------------------------------------------------------
# Sprint 2 P7 — hard timeouts
# ---------------------------------------------------------------------------
_EMBED_TIMEOUT_S    = 3.0   # embedding generation
_RETRIEVE_TIMEOUT_S = 2.0   # vector-db aretrieve

# ---------------------------------------------------------------------------
# Sprint 3 P10 — dedup registry parameters
# ---------------------------------------------------------------------------
_DEDUP_MAX_SIZE = 1024      # max entries in RAM hash registry
_DEDUP_TTL_S    = 600.0     # 10 minutes TTL per entry

# ---------------------------------------------------------------------------
# Sprint 3 P11 — observability dump triggers
# ---------------------------------------------------------------------------
_OBS_DUMP_OPS_THRESHOLD = 200    # cumulative ops
_OBS_DUMP_TIME_INTERVAL = 300.0  # 5 minutes


# =============================================================================
# Sprint 2 P5 — RobopyEmbedding: Single-Pipeline Bridge
# =============================================================================

class RobopyEmbedding(BaseEmbedding):
    """
    Bridge between Robopy's EmbeddingService and LlamaIndex.
    Sync stubs raise NotImplementedError — async is the only supported path.
    """

    def __init__(self, embedding_service, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, '_service', embedding_service)
        # Sprint 2/3 fix: explicitly set model_name to avoid fallback on text-embedding-004
        object.__setattr__(self, 'model_name', "gemini-embedding-2-preview")

    # ---- Sync stubs: intentionally not implemented -------------------------
    def _get_query_embedding(self, query: str) -> List[float]:
        raise NotImplementedError(
            "RobopyEmbedding is async-only. "
            "Ensure LlamaIndex is called from an async context."
        )

    def _get_text_embedding(self, text: str) -> List[float]:
        raise NotImplementedError(
            "RobopyEmbedding is async-only. "
            "Ensure LlamaIndex is called from an async context."
        )

    # ---- Async paths: delegating to EmbeddingService with timeout ----------
    async def _aget_query_embedding(self, query: str) -> List[float]:
        return await asyncio.wait_for(
            self._service.embed(query),
            timeout=_EMBED_TIMEOUT_S,
        )

    async def _aget_text_embedding(self, text: str) -> List[float]:
        return await asyncio.wait_for(
            self._service.embed(text),
            timeout=_EMBED_TIMEOUT_S,
        )

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        return await asyncio.wait_for(
            self._service.embed_batch(texts),
            timeout=_EMBED_TIMEOUT_S * max(len(texts), 1),
        )


# =============================================================================
# LlamaIndexMemoryStore
# =============================================================================

class LlamaIndexMemoryStore(BaseMemoryStore):
    """
    LlamaIndex + ChromaDB backend with Sprint 2+3 optimisations.

    Thread-safety contract: all mutable state is protected by asyncio.Lock.
    No threading.Lock, no blocking calls inside lock sections.
    """

    _DEFAULT_MAX_CPU   = 95.0

    def __init__(
        self,
        config_manager,
        embedding_service=None,
        persist_dir: str = "/home/robopy/ChromaDB",
        collection_name: str = "robot_memories",
    ):
        self.logger         = get_logger("llama_store")
        self.persist_dir    = Path(persist_dir)
        self.collection_name = collection_name
        self._embedding_service = embedding_service
        self._enabled       = HAS_LLAMA
        self._config_manager = config_manager

        # ---- Sprint 3 P10: dedup registry (hash → insert_timestamp) --------
        self._dedup: Dict[str, float] = {}         # sha256[:16] → ts
        self._dedup_lock: Optional[asyncio.Lock] = None

        # ---- Sprint 3 P9: L1 hot cache --------------------------------------
        self._l1: collections.deque = collections.deque(maxlen=20)
        self._l1_lock: Optional[asyncio.Lock] = None

        # ---- Sprint 3 P11: observability counters ---------------------------
        self._stats: Dict[str, int] = {
            "add_ok":      0,
            "add_dropped": 0,
            "search_ok":   0,
            "search_fail": 0,
        }
        self._total_ops:      int   = 0
        self._last_dump_time: float = time.monotonic()
        self._stats_lock: Optional[asyncio.Lock] = None

        if not self._enabled:
            self.logger.warning(
                f"LlamaIndex dependencies not found: {LLAMA_IMPORT_ERROR}. "
                "Memory store disabled."
            )
            return

        # ---- ChromaDB backend -----------------------------------------------
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.db               = chromadb.PersistentClient(path=str(self.persist_dir))
        self.chroma_collection = self.db.get_or_create_collection(collection_name)
        self.vector_store     = ChromaVectorStore(chroma_collection=self.chroma_collection)
        self.storage_context  = StorageContext.from_defaults(vector_store=self.vector_store)

        # ---- Config-driven settings -----------------------------------------
        config = config_manager.get_config()
        api_key = config.secrets.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
        embedding_model_name = "models/gemini-embedding-2-preview"
        llm_model_name = getattr(config.llm, "model", "gemini-3.1-flash-lite-preview")

        mem_cfg = getattr(config, "memory", None)
        self._max_cpu_percent: float = float(
            getattr(mem_cfg, "max_cpu_percent", self._DEFAULT_MAX_CPU)
        )

        if not api_key:
            self.logger.error("❌ GEMINI_API_KEY non trovata!")
            self._enabled = False
        else:
            try:
                if embedding_service is not None:
                    Settings.embed_model = RobopyEmbedding(embedding_service)
                    self.logger.info("✅ Using RobopyEmbedding bridge — no double embedding pipeline.")
                else:
                    Settings.embed_model = GoogleGenAIEmbedding(
                        model_name=embedding_model_name, api_key=api_key,
                    )
                    self.logger.warning(
                        "⚠️  EmbeddingService not injected — using GoogleGenAIEmbedding (double pipeline)."
                    )

                Settings.llm = Gemini(
                    model_name=f"models/{llm_model_name}", api_key=api_key,
                )

                try:
                    self.index = VectorStoreIndex.from_vector_store(
                        self.vector_store, storage_context=self.storage_context,
                    )
                except Exception as e:
                    self.logger.warning(f"Empty/corrupt index, creating fresh: {e}")
                    self.index = VectorStoreIndex([], storage_context=self.storage_context)

            except Exception as e:
                self.logger.error(f"⚠️ LlamaIndex Settings failure: {e}")
                self._enabled = False

        if self._enabled:
            self.logger.info(
                f"✅ LlamaIndex Memory Store ready "
                f"(cpu_limit={self._max_cpu_percent}%, "
                f"embed_to={_EMBED_TIMEOUT_S}s, retrieve_to={_RETRIEVE_TIMEOUT_S}s, "
                f"l1_cap=20, dedup_max={_DEDUP_MAX_SIZE}, dedup_ttl={_DEDUP_TTL_S}s)."
            )
        else:
            self.logger.warning("🧱 LlamaIndex disabilitato.")

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _get_lock(self, name: str) -> asyncio.Lock:
        attr = f"_{name}_lock"
        if getattr(self, attr, None) is None:
            setattr(self, attr, asyncio.Lock())
        return getattr(self, attr)

    def _is_cpu_shed(self) -> bool:
        """Blocking probe — must be run via asyncio.to_thread."""
        if not HAS_PSUTIL:
            return False
        cpu = psutil.cpu_percent(interval=0.1)
        if cpu > self._max_cpu_percent:
            self.logger.warning(
                f"⚡ Shed-load triggered: CPU={cpu:.0f}% > {self._max_cpu_percent:.0f}%. Dropping write."
            )
            return True
        return False

    @staticmethod
    def _content_hash(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    async def _dedup_check_and_register(self, text: str) -> bool:
        """
        Returns True if the content is a duplicate (should be dropped).
        Evicts expired entries and maintains max-size invariant.
        All mutations are under asyncio.Lock.
        """
        key = self._content_hash(text)
        now = time.monotonic()

        async with self._get_lock("dedup"):
            # Evict expired entries (TTL check)
            expired = [k for k, ts in self._dedup.items() if now - ts > _DEDUP_TTL_S]
            for k in expired:
                del self._dedup[k]

            # Check duplicate
            if key in self._dedup:
                return True  # duplicate

            # Evict oldest if at capacity
            if len(self._dedup) >= _DEDUP_MAX_SIZE:
                oldest_key = min(self._dedup, key=lambda k: self._dedup[k])
                del self._dedup[oldest_key]

            # Register new
            self._dedup[key] = now
            return False

    async def _l1_push(self, result: SearchResult) -> None:
        """Prepend to L1 cache (most-recent-first)."""
        async with self._get_lock("l1"):
            self._l1.appendleft(result)

    async def _l1_snapshot(self) -> List[SearchResult]:
        """Return a stable snapshot of the L1 cache."""
        async with self._get_lock("l1"):
            return list(self._l1)

    async def _record_stat(self, key: str) -> None:
        """Increment counter and trigger observability dump if threshold reached."""
        async with self._get_lock("stats"):
            self._stats[key] = self._stats.get(key, 0) + 1
            self._total_ops += 1
            elapsed = time.monotonic() - self._last_dump_time

            if self._total_ops >= _OBS_DUMP_OPS_THRESHOLD or elapsed >= _OBS_DUMP_TIME_INTERVAL:
                self.logger.info(
                    f"[MemoryStore Obs] "
                    f"add_ok={self._stats['add_ok']} "
                    f"add_dropped={self._stats['add_dropped']} "
                    f"search_ok={self._stats['search_ok']} "
                    f"search_fail={self._stats['search_fail']} "
                    f"| ops_since_last={self._total_ops} elapsed={elapsed:.0f}s"
                )
                # Reset
                for k in self._stats:
                    self._stats[k] = 0
                self._total_ops    = 0
                self._last_dump_time = time.monotonic()

    # =========================================================================
    # BaseMemoryStore contract
    # =========================================================================

    async def add(self, text: str, metadata: Dict[str, Any]) -> bool:
        """
        Sprint 2 P7: CPU shed-load gate + embedding timeout.
        Sprint 3 P10: hash-dedup before any I/O.
        Sprint 3 P9: push to L1 cache on success.
        Sprint 3 P11: increment add_ok / add_dropped.
        """
        if not self._enabled:
            return False

        # ---- P10: Dedup check (before any I/O) ------------------------------
        is_dup = await self._dedup_check_and_register(text)
        if is_dup:
            self.logger.debug(f"[dedup] Dropped duplicate write (hash={self._content_hash(text)})")
            await self._record_stat("add_dropped")
            return False

        # ---- P7: Shed-load CPU gate -----------------------------------------
        shed = await asyncio.to_thread(self._is_cpu_shed)
        if shed:
            await self._record_stat("add_dropped")
            return False

        mem_type = metadata.get("memory_type", MemoryType.CONVERSATION.value)
        mem_id   = metadata.pop("id", None) or str(uuid.uuid4())[:12]
        doc_meta = {
            "memory_type": mem_type if isinstance(mem_type, str) else mem_type.value,
            "created_at":  metadata.get("created_at", time.time()),
            "uuid":        mem_id,
        }
        for k, v in metadata.items():
            if isinstance(v, (str, int, float, bool)):
                doc_meta[k] = v

        try:
            doc = Document(text=text, id_=mem_id, metadata=doc_meta)
            await asyncio.wait_for(
                self.index.ainsert(doc),
                timeout=_EMBED_TIMEOUT_S + 2.0,
            )
            self.logger.debug(f"Inserted LlamaIndex node: {mem_id}")

            # ---- P9: Push to L1 hot cache on success -----------------------
            sr = SearchResult(
                content=text,
                score=1.0,
                metadata=dict(doc_meta),
                timestamp=doc_meta["created_at"],
                memory_id=mem_id,
                memory_type=MemoryType(
                    doc_meta["memory_type"] if isinstance(doc_meta["memory_type"], str)
                    else doc_meta["memory_type"].value
                ),
            )
            await self._l1_push(sr)

            await self._record_stat("add_ok")
            return True

        except asyncio.TimeoutError:
            raise EmbeddingTimeoutError(
                f"add() timed out after {_EMBED_TIMEOUT_S + 1.0}s for node {mem_id}"
            )
        except Exception as e:
            self.logger.error(f"Failed to add memory to LlamaIndex: {e}")
            await self._record_stat("add_dropped")
            return False

    async def search(self, query: str, top_k: int = 3) -> List[SearchResult]:
        """
        Sprint 2 P6/P7: Never raises; 2 s timeout; returns make_unavailable_result() on failure.
        Sprint 3 P11: increments search_ok / search_fail.
        """
        if not self._enabled:
            await self._record_stat("search_fail")
            return make_unavailable_result("store_disabled")

        start_time = time.monotonic()
        self.logger.info(f"🔍 [MEMORY] Searching for: '{query[:40]}...'")
        
        try:
            retriever = self.index.as_retriever(similarity_top_k=top_k)
            nodes = await asyncio.wait_for(
                retriever.aretrieve(query),
                timeout=_RETRIEVE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            self.logger.warning(
                f"search() timed out after {_RETRIEVE_TIMEOUT_S}s for query='{query[:40]}'"
            )
            await self._record_stat("search_fail")
            return make_unavailable_result("retrieval_timeout")
        except Exception as e:
            self.logger.error(f"LlamaIndex search failed: {e}")
            await self._record_stat("search_fail")
            return make_unavailable_result(str(e)[:80])

        results: List[SearchResult] = []
        for node in nodes:
            meta = node.node.metadata
            results.append(
                SearchResult(
                    content=node.node.text,
                    score=node.score or 0.0,
                    metadata=meta,
                    timestamp=meta.get("created_at", 0.0),
                    memory_id=node.node.id_,
                    memory_type=MemoryType(meta.get("memory_type", "conversation")),
                )
            )

        duration = time.monotonic() - start_time
        self.logger.info(f"✅ [MEMORY] Retrieval complete: found {len(results)} items in {duration:.3f}s")
        await self._record_stat("search_ok")
        return results

    async def get(self, memory_id: str) -> Optional[SearchResult]:
        if not self._enabled:
            return None

        def _sync_get():
            return self.chroma_collection.get(ids=[memory_id])

        try:
            res = await asyncio.to_thread(_sync_get)
            if not res["ids"]:
                return None
            meta    = res["metadatas"][0] if res["metadatas"] else {}
            content = res["documents"][0] if res["documents"] else ""
            return SearchResult(
                content=content,
                score=0.0,
                metadata=meta,
                timestamp=meta.get("created_at", 0.0),
                memory_id=memory_id,
                memory_type=MemoryType(meta.get("memory_type", "conversation")),
            )
        except Exception:
            return None

    async def update(self, memory_id: str, text: str, metadata: Dict[str, Any]) -> bool:
        if not self._enabled:
            return False
        existing = await self.get(memory_id)
        if existing is None:
            return False
        try:
            self.chroma_collection.delete(ids=[memory_id])
            metadata["id"] = memory_id
            return await self.add(text, metadata)
        except Exception as e:
            self.logger.error(f"Failed to update memory in LlamaIndex: {e}")
            return False

    async def get_recent(self, limit: int = 5) -> List[SearchResult]:
        """
        Sprint 3 P9: Serve from L1 first; fill remainder from ChromaDB.
        Signature unchanged — transparent to callers.
        """
        if not self._enabled:
            return []

        # ---- L1 first -------------------------------------------------------
        l1_items = await self._l1_snapshot()
        if len(l1_items) >= limit:
            return l1_items[:limit]

        # ---- Fill remainder from Chroma -------------------------------------
        needed          = limit - len(l1_items)
        l1_ids          = {r.memory_id for r in l1_items}

        try:
            def _sync_get():
                # Fetch more than needed to absorb overlaps with L1
                return self.chroma_collection.get(
                    limit=limit + len(l1_ids),
                    include=["metadatas", "documents"],
                )

            results = await asyncio.to_thread(_sync_get)
            if not results or not results.get("ids"):
                return l1_items

            warm: List[SearchResult] = []
            for i, mem_id in enumerate(results["ids"]):
                if mem_id in l1_ids:
                    continue  # already in L1 — skip duplicate
                meta    = results["metadatas"][i]
                content = results["documents"][i]
                warm.append(SearchResult(
                    content=content,
                    score=0.0,
                    metadata=meta,
                    timestamp=meta.get("created_at", 0.0),
                    memory_id=mem_id,
                    memory_type=MemoryType(meta.get("memory_type", "conversation")),
                ))

            warm.sort(key=lambda r: r.timestamp, reverse=True)
            combined = l1_items + warm[:needed]
            return combined[:limit]

        except Exception as e:
            self.logger.error(f"Failed to get recent from Chroma: {e}")
            return l1_items  # degrade gracefully to L1-only

    async def count(self) -> int:
        if not self._enabled:
            return 0
        return await asyncio.to_thread(self.chroma_collection.count)

    # =========================================================================
    # Legacy sync shims (VisualMemoryService compat)
    # =========================================================================

    def add_sync(self, memory) -> str:
        """Sync shim for VisualMemoryService — prefer async add()."""
        if not self._enabled:
            return ""
        from .memory_store import Memory  # noqa
        if not memory.id:
            memory.id = str(uuid.uuid4())[:12]
        if not memory.embedding:
            self.logger.warning("add_sync called without embedding — skipping")
            return ""
        meta = memory.to_dict()
        del meta["id"]
        clean_meta = {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))}
        self.chroma_collection.add(
            ids=[memory.id],
            embeddings=[memory.embedding],
            metadatas=[clean_meta],
            documents=[memory.content],
        )
        return memory.id

    def get_sync(self, memory_id: str):
        """Sync shim for VisualMemoryService — prefer async get()."""
        if not self._enabled:
            return None
        from .memory_store import Memory  # noqa
        results = self.chroma_collection.get(
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

    def update_sync(self, memory) -> None:
        """Sync shim for VisualMemoryService — prefer async update()."""
        if not self._enabled:
            return
        import time as _time
        memory.updated_at = _time.time()
        meta = memory.to_dict()
        del meta["id"]
        clean_meta = {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))}
        update_kwargs: dict = {
            "ids":       [memory.id],
            "metadatas": [clean_meta],
            "documents": [memory.content],
        }
        if memory.embedding:
            update_kwargs["embeddings"] = [memory.embedding]
        self.chroma_collection.update(**update_kwargs)
