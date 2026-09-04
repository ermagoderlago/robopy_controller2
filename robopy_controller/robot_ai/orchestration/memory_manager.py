import asyncio
import hashlib
import time
from robot_ai.utils import get_logger
from robot_ai.rag.memory_store import MemoryStore, Memory, MemoryType
from robot_ai.services.embedding_service import EmbeddingService

class MemoryManager:
    def __init__(self, memory_store: MemoryStore, embedding_service: EmbeddingService, max_queue=1000):
        self.memory_store = memory_store
        self.embedding_service = embedding_service
        self._queue = asyncio.Queue(maxsize=max_queue)
        self._worker_task = None
        self._embedding_cache = {}
        self._max_cache = 100
        self._shutdown = False
        self._frozen = False
        self._logger = get_logger("memory_manager")

    def freeze_embeddings(self):
        """Freeze new embedding generation to relieve memory pressure."""
        self._frozen = True
        self._logger.warning("[MEMORY_MANAGER] Embeddings FROZEN by Memory Pressure Sentinel.")

    def unfreeze_embeddings(self):
        """Resume nominal embedding generation."""
        self._frozen = False
        self._logger.info("[MEMORY_MANAGER] Embeddings UNFROZEN.")

    def clear_transient_buffers(self):
        """Evicts transient embedding caches and clears processing queue under emergency."""
        cleared_items = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                cleared_items += 1
            except Exception:
                break
        self._embedding_cache.clear()
        self._logger.warning(f"[MEMORY_MANAGER] Evicted {cleared_items} queued memories and flushed embedding cache.")

    def start(self):
        self._worker_task = asyncio.create_task(self._worker())

    async def shutdown(self):
        self._shutdown = True
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def store_background(self, user_text: str, robot_text: str, mem_type: str):
        if self._shutdown or self._frozen:
            if self._frozen:
                self._logger.debug("[MEMORY_MANAGER] Dropping memory storage because embeddings are frozen under memory pressure.")
            return
        content = f"User: {user_text}\nRobot: {robot_text}"
        try:
            self._queue.put_nowait((content, mem_type))
        except asyncio.QueueFull:
            self._logger.warning("Memory queue full, dropping memory")

    async def _worker(self):
        while not self._shutdown:
            try:
                content, mem_type_str = await self._queue.get()
                mem_type = MemoryType(mem_type_str)
                # embedding con cache
                cache_key = hashlib.md5(content.encode()).hexdigest()
                if cache_key in self._embedding_cache:
                    embedding = self._embedding_cache[cache_key]
                else:
                    embedding = await self.embedding_service.embed(content)
                    if len(self._embedding_cache) < self._max_cache:
                        self._embedding_cache[cache_key] = embedding

                metadata = {"timestamp": time.time()}
                importance = 0.5
                if mem_type in (MemoryType.LEARNED_FACT, MemoryType.USER_PREFERENCE):
                    importance = 1.0
                    metadata["amygdala_protected"] = "true"
                    metadata["synaptic_strength"] = 100.0

                memory = Memory(
                    id="", content=content,
                    memory_type=mem_type,
                    embedding=embedding,
                    metadata=metadata,
                    importance=importance
                )
                self.memory_store.add(memory)
                self._logger.debug(f"Stored background memory: {mem_type}")
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Memory worker error: {e}")

    async def get_stats(self) -> dict:
        """Returns memory statistics for skills and diagnostics."""
        try:
            if hasattr(self.memory_store, 'get_statistics'):
                stats = self.memory_store.get_statistics()
                return {
                    "total_chunks": stats.get("total_memories", 0),
                    "status": stats.get("status", "ready"),
                    "by_type": stats.get("by_type", {})
                }
            elif hasattr(self.memory_store, 'count'):
                return {
                    "total_chunks": self.memory_store.count(),
                    "status": "ready"
                }
        except Exception as e:
            self._logger.warning(f"Error getting memory stats: {e}")
        return {"total_chunks": 0, "status": "unknown"}

    async def list_loaded_documents(self) -> list:
        """Returns list of distinct loaded documents."""
        try:
            if hasattr(self.memory_store, '_collection'):
                results = self.memory_store._collection.get(include=["metadatas"])
                docs = set()
                for m in results.get("metadatas", []) or []:
                    if m and "document_name" in m:
                        docs.add(m["document_name"])
                    elif m and "filename" in m:
                        docs.add(m["filename"])
                return sorted(list(docs))
        except Exception as e:
            self._logger.warning(f"Error listing documents: {e}")
        return []
