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
        self._logger = get_logger("memory_manager")

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
        if self._shutdown:
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
