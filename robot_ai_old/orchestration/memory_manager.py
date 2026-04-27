import asyncio
import hashlib
import time
from robot_ai.utils import get_logger
from robot_ai.rag.base_memory_store import BaseMemoryStore, MemoryType, SearchResult
from robot_ai.services.embedding_service import EmbeddingService

class MemoryManager:
    """
    High-level memory manager that delegates to a BaseMemoryStore backend.
    
    Handles background memory storage via an async queue and provides
    a simple search() interface for conversation context retrieval.
    """

    def __init__(self, memory_store: BaseMemoryStore, embedding_service: EmbeddingService, max_queue=1000):
        self.memory_store = memory_store
        self.embedding_service = embedding_service
        self._max_queue = max_queue
        # Queue will be initialized in start() to ensure it binds to the correct event loop
        self._queue = None
        self._worker_task = None
        self._shutdown = False
        self._logger = get_logger("memory_manager")

    def start(self):
        # Called from _async_init inside self._loop, ensuring thread-safety
        self._queue = asyncio.Queue(maxsize=self._max_queue)
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
        if self._shutdown or self._queue is None:
            return
        content = f"User: {user_text}\nRobot: {robot_text}"
        try:
            self._queue.put_nowait((content, mem_type))
        except asyncio.QueueFull:
            self._logger.warning("Memory queue full, dropping memory")

    async def _worker(self):
        while not self._shutdown and self._queue is not None:
            try:
                content, mem_type_str = await self._queue.get()

                # Store via the async BaseMemoryStore contract
                metadata = {
                    "memory_type": mem_type_str,
                    "timestamp": time.time(),
                    "created_at": time.time(),
                }
                await self.memory_store.add(content, metadata)
                self._logger.debug(f"Stored background memory: {mem_type_str}")
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Memory worker error: {e}")

    async def search(self, query: str, limit: int = 5):
        """Search memories via the BaseMemoryStore contract."""
        if not self.memory_store:
            return []
        try:
            return await self.memory_store.search(query, top_k=limit)
        except Exception as e:
            self._logger.error(f"Memory search error: {e}")
            return []
