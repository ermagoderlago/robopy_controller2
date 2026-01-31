"""
Robot AI RAG - Memory Store
============================
ChromaDB-based memory storage with HNSW indexing.
Supports float16 embeddings and hybrid search.
"""

import time
import hashlib
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum

try:
    import chromadb
    from chromadb.config import Settings
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False

from ..core.exceptions import MemoryError as AIMemoryError
from ..utils.logging_utils import get_logger


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
class Memory:
    """A single memory entry."""
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
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "importance": self.importance,
            "access_count": self.access_count,
            **self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], embedding: List[float] = None) -> 'Memory':
        """Create from dictionary."""
        core_fields = {"id", "content", "memory_type", "created_at", 
                       "updated_at", "importance", "access_count"}
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
            access_count=data.get("access_count", 0)
        )


@dataclass
class SearchResult:
    """Result from memory search."""
    memory: Memory
    score: float
    distance: float = 0.0


class MemoryStore:
    """
    ChromaDB-based memory storage.
    
    Features:
    - Persistent ChromaDB storage
    - HNSW indexing for fast search
    - Float16 embedding support
    - Metadata filtering
    - Temporal decay scoring
    
    Usage:
        store = MemoryStore(persist_dir="/home/robopy/ChromaDB")
        
        # Add memory
        store.add(Memory(
            id="mem_001",
            content="User likes classical music",
            memory_type=MemoryType.USER_PREFERENCE,
            embedding=embedding_vector
        ))
        
        # Search
        results = store.search(query_embedding, top_k=5)
    """
    
    def __init__(
        self,
        persist_dir: str = "/home/robopy/ChromaDB",
        collection_name: str = "robot_memories",
        embedding_dimension: int = 768,
        use_hnsw: bool = True,
        hnsw_ef_construction: int = 200,
        hnsw_m: int = 16
    ):
        if not HAS_CHROMADB:
            raise ImportError("chromadb is required: pip install chromadb")
        
        self.logger = get_logger("memory_store")
        self.persist_dir = Path(persist_dir)
        self.collection_name = collection_name
        self.embedding_dimension = embedding_dimension
        
        # Ensure directory exists
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize ChromaDB client
        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True
            )
        )
        
        # Get or create collection with HNSW settings
        hnsw_params = {}
        if use_hnsw:
            hnsw_params = {
                "hnsw:construction_ef": hnsw_ef_construction,
                "hnsw:M": hnsw_m,
                "hnsw:search_ef": 100
            }
        
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine", **hnsw_params}
        )
        
        self._lock = threading.RLock()
        self.logger.info(
            f"Memory store initialized",
            persist_dir=str(self.persist_dir),
            collection=collection_name,
            count=self.count()
        )
    
    def add(self, memory: Memory) -> str:
        """
        Add a memory to the store.
        
        Args:
            memory: Memory to add
            
        Returns:
            Memory ID
        """
        with self._lock:
            if not memory.id:
                memory.id = self._generate_id(memory.content)
            
            if not memory.embedding:
                raise AIMemoryError("Memory must have an embedding")
            
            # Prepare metadata
            metadata = memory.to_dict()
            del metadata["id"]  # ID is stored separately
            
            # Store
            self._collection.add(
                ids=[memory.id],
                embeddings=[memory.embedding],
                metadatas=[metadata],
                documents=[memory.content]
            )
            
            self.logger.debug(f"Added memory: {memory.id}", type=memory.memory_type.value)
            return memory.id
    
    def add_batch(self, memories: List[Memory]) -> List[str]:
        """Add multiple memories in batch."""
        with self._lock:
            ids = []
            embeddings = []
            metadatas = []
            documents = []
            
            for memory in memories:
                if not memory.id:
                    memory.id = self._generate_id(memory.content)
                
                if not memory.embedding:
                    raise AIMemoryError(f"Memory {memory.id} must have an embedding")
                
                ids.append(memory.id)
                embeddings.append(memory.embedding)
                
                metadata = memory.to_dict()
                del metadata["id"]
                metadatas.append(metadata)
                documents.append(memory.content)
            
            self._collection.add(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=documents
            )
            
            self.logger.info(f"Added {len(memories)} memories in batch")
            return ids
    
    def update(self, memory: Memory) -> None:
        """Update an existing memory."""
        with self._lock:
            memory.updated_at = time.time()
            
            metadata = memory.to_dict()
            del metadata["id"]
            
            update_kwargs = {
                "ids": [memory.id],
                "metadatas": [metadata],
                "documents": [memory.content]
            }
            
            if memory.embedding:
                update_kwargs["embeddings"] = [memory.embedding]
            
            self._collection.update(**update_kwargs)
            self.logger.debug(f"Updated memory: {memory.id}")
    
    def delete(self, memory_id: str) -> bool:
        """Delete a memory by ID."""
        with self._lock:
            try:
                self._collection.delete(ids=[memory_id])
                self.logger.debug(f"Deleted memory: {memory_id}")
                return True
            except Exception:
                return False
    
    def delete_by_type(self, memory_type: MemoryType) -> int:
        """Delete all memories of a specific type."""
        with self._lock:
            results = self._collection.get(
                where={"memory_type": memory_type.value}
            )
            
            if results["ids"]:
                self._collection.delete(ids=results["ids"])
                self.logger.info(f"Deleted {len(results['ids'])} memories of type {memory_type.value}")
                return len(results["ids"])
            return 0
    
    def get(self, memory_id: str) -> Optional[Memory]:
        """Get a memory by ID."""
        with self._lock:
            results = self._collection.get(
                ids=[memory_id],
                include=["embeddings", "metadatas", "documents"]
            )
            
            if not results["ids"]:
                return None
            
            # Update access count
            metadata = results["metadatas"][0]
            metadata["access_count"] = metadata.get("access_count", 0) + 1
            self._collection.update(ids=[memory_id], metadatas=[metadata])
            
            return Memory.from_dict(
                {"id": memory_id, "content": results["documents"][0], **metadata},
                embedding=results["embeddings"][0] if results["embeddings"] else None
            )
    
    def search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        memory_type: MemoryType = None,
        min_score: float = 0.0,
        time_weighted: bool = True,
        temporal_decay_rate: float = 0.1
    ) -> List[SearchResult]:
        """
        Search for similar memories.
        
        Args:
            query_embedding: Query embedding vector
            top_k: Number of results to return
            memory_type: Filter by type (optional)
            min_score: Minimum similarity score
            time_weighted: Apply temporal decay
            temporal_decay_rate: Rate of decay (higher = faster decay)
            
        Returns:
            List of SearchResult ordered by relevance
        """
        with self._lock:
            where_filter = {}
            if memory_type:
                where_filter["memory_type"] = memory_type.value
            
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k * 2,  # Get more for filtering
                where=where_filter if where_filter else None,
                include=["embeddings", "metadatas", "documents", "distances"]
            )
            
            search_results = []
            current_time = time.time()
            
            for i, memory_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i]
                # Convert distance to similarity score (for cosine: 1 - distance)
                base_score = 1 - distance
                
                if base_score < min_score:
                    continue
                
                metadata = results["metadatas"][0][i]
                
                # Apply temporal decay
                score = base_score
                if time_weighted:
                    created_at = metadata.get("created_at", current_time)
                    age_hours = (current_time - created_at) / 3600
                    decay = 1.0 / (1.0 + temporal_decay_rate * age_hours)
                    score = base_score * decay
                
                # Boost by importance
                importance = metadata.get("importance", 0.5)
                score = score * (0.7 + 0.3 * importance)
                
                memory = Memory.from_dict(
                    {"id": memory_id, "content": results["documents"][0][i], **metadata},
                    embedding=results["embeddings"][0][i] if results["embeddings"] else None
                )
                
                search_results.append(SearchResult(
                    memory=memory,
                    score=score,
                    distance=distance
                ))
            
            # Sort by score and return top_k
            search_results.sort(key=lambda x: x.score, reverse=True)
            return search_results[:top_k]
    
    def get_recent(self, limit: int = 10, memory_type: MemoryType = None) -> List[Memory]:
        """Get most recent memories."""
        with self._lock:
            where_filter = {}
            if memory_type:
                where_filter["memory_type"] = memory_type.value
            
            results = self._collection.get(
                where=where_filter if where_filter else None,
                include=["metadatas", "documents"]
            )
            
            if not results["ids"]:
                return []
            
            # Sort by created_at
            memories = []
            for i, memory_id in enumerate(results["ids"]):
                memories.append(Memory.from_dict(
                    {"id": memory_id, "content": results["documents"][i], 
                     **results["metadatas"][i]}
                ))
            
            memories.sort(key=lambda m: m.created_at, reverse=True)
            return memories[:limit]
    
    def count(self, memory_type: MemoryType = None) -> int:
        """Count memories, optionally filtered by type."""
        if memory_type:
            results = self._collection.get(
                where={"memory_type": memory_type.value}
            )
            return len(results["ids"])
        return self._collection.count()
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get memory store statistics."""
        with self._lock:
            stats = {
                "total_memories": self.count(),
                "by_type": {},
                "persist_dir": str(self.persist_dir)
            }
            
            for memory_type in MemoryType:
                count = self.count(memory_type)
                if count > 0:
                    stats["by_type"][memory_type.value] = count
            
            return stats
    
    def _generate_id(self, content: str) -> str:
        """Generate a unique ID from content."""
        timestamp = str(time.time())
        hash_input = f"{content}{timestamp}".encode()
        return f"mem_{hashlib.sha256(hash_input).hexdigest()[:12]}"
    
    def clear(self) -> None:
        """Clear all memories. Use with caution!"""
        with self._lock:
            self._client.delete_collection(self.collection_name)
            self._collection = self._client.create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            self.logger.warning("All memories cleared!")
    
    def backup(self, backup_path: str) -> None:
        """Create a backup of the memory store."""
        import shutil
        shutil.copytree(self.persist_dir, backup_path, dirs_exist_ok=True)
        self.logger.info(f"Backup created at {backup_path}")
    
    def close(self) -> None:
        """Close the memory store."""
        # ChromaDB handles this internally
        self.logger.info("Memory store closed")
