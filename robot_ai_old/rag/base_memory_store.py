"""
Robot AI RAG - Base Memory Store (Abstract Contract)
=====================================================
Single source of truth for the MemoryStore interface.
All backends (ChromaDB, LlamaIndex) MUST inherit from this.

Optimized for async-first operation on Raspberry Pi 5.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# =============================================================================
# Domain Types
# =============================================================================

class MemoryType(str, Enum):
    """Types of memories stored in the system."""
    CONVERSATION = "conversation"
    USER_PREFERENCE = "user_preference"
    VISUAL_OBSERVATION = "visual_observation"
    SYSTEM_EVENT = "system_event"
    LEARNED_FACT = "learned_fact"
    ROUTINE = "routine"
    LOCATION = "location"
    PERSON = "person"
    TASK = "task"
    SUMMARY = "summary"


@dataclass
class SearchResult:
    """
    Unified search result returned by all MemoryStore backends.
    Consumers MUST use this type — never raw dicts.

    The 'memory_status' metadata key signals special states:
      - "ok"          : normal result
      - "unavailable" : backend was unreachable (shed-load / timeout sentinel)
    """
    content: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    memory_id: str = ""
    memory_type: MemoryType = MemoryType.CONVERSATION

    @property
    def is_unavailable(self) -> bool:
        """True when this is a sentinel result (memory was shed / timed-out)."""
        return self.metadata.get("memory_status") == "unavailable"


# =============================================================================
# Helpers
# =============================================================================

def make_unavailable_result(reason: str = "timeout") -> List[SearchResult]:
    """
    Return a structured fallback result list.

    Callers (ConversationManager, MemoryManager) MUST check
    `result[0].is_unavailable` before injecting into prompts.
    """
    return [
        SearchResult(
            content="",
            score=0.0,
            metadata={"memory_status": "unavailable", "reason": reason},
            timestamp=time.time(),
        )
    ]


# =============================================================================
# Custom Exceptions
# =============================================================================

class MemoryStoreError(Exception):
    """Base exception for all memory store operations."""
    pass


class EmbeddingTimeoutError(MemoryStoreError):
    """Raised when an embedding operation exceeds the timeout budget."""
    pass


class MemoryNotFoundError(MemoryStoreError):
    """Raised when a memory ID does not exist in the store."""
    pass


class MemoryShedError(MemoryStoreError):
    """Raised when a write is dropped due to CPU/resource shed-load."""
    pass


# =============================================================================
# Abstract Contract
# =============================================================================

class BaseMemoryStore(ABC):
    """
    Abstract base for all memory store backends.

    Contract rules:
      1. All I/O methods are async (ROS 2 executor-safe).
      2. Embedding model is NEVER hardcoded — injected via config/service.
      3. Methods accept plain text, not pre-computed embeddings.
         The store is responsible for calling the embedding service internally.
      4. Implementations MUST be safe for concurrent access.
      5. search() MUST never raise — return make_unavailable_result() on failure.
      6. add()    MAY raise MemoryShedError when resources are constrained.

    Minimal API surface:
      - add()        → store a new memory
      - search()     → semantic similarity search
      - get()        → retrieve by ID
      - update()     → update existing memory
      - get_recent() → chronological retrieval
      - count()      → total entries
    """

    @abstractmethod
    async def add(self, text: str, metadata: Dict[str, Any]) -> bool:
        """
        Store a new memory.

        Args:
            text: Human-readable content to store.
            metadata: Arbitrary key-value pairs (must include 'memory_type').

        Returns:
            True on success, False when silently skipped.

        Raises:
            MemoryStoreError: On storage failure.
            EmbeddingTimeoutError: If embedding generation times out.
            MemoryShedError: If CPU budget exceeded (shed-load).
        """
        ...

    @abstractmethod
    async def search(self, query: str, top_k: int = 3) -> List[SearchResult]:
        """
        Search for memories semantically similar to *query*.

        NEVER raises. Returns make_unavailable_result() on failure.

        Args:
            query: Natural-language search query.
            top_k: Maximum number of results.

        Returns:
            List of SearchResult sorted by descending score, or
            make_unavailable_result() on error/timeout.
        """
        ...

    @abstractmethod
    async def get(self, memory_id: str) -> Optional[SearchResult]:
        """
        Retrieve a single memory by ID.

        Returns:
            SearchResult or None if not found.
        """
        ...

    @abstractmethod
    async def update(self, memory_id: str, text: str, metadata: Dict[str, Any]) -> bool:
        """
        Update an existing memory's content and metadata.

        Returns:
            True on success, False if not found.

        Raises:
            MemoryStoreError: On update failure.
        """
        ...

    @abstractmethod
    async def get_recent(self, limit: int = 5) -> List[SearchResult]:
        """
        Retrieve the most recent memories in chronological order (newest first).

        Args:
            limit: Maximum number of results.

        Returns:
            List of SearchResult sorted by descending timestamp.
        """
        ...

    @abstractmethod
    async def count(self) -> int:
        """Return total number of memories in the store."""
        ...
