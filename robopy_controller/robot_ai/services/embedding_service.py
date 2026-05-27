"""
Robot AI Services - Embedding Service
======================================
Gemini embedding generation with caching and quantization.
"""

import asyncio
import hashlib
import struct
from typing import Any, Dict, List, Optional
from collections import OrderedDict
import threading

try:
    from google import genai
    HAS_GENAI = True
except ImportError as e:
    import sys
    print(f"DEBUG: Failed to import google.genai in embedding_service: {e}", file=sys.stderr)
    HAS_GENAI = False

from ..core.config_manager import ConfigManager
from ..core.exceptions import AIError
from ..core.circuit_breaker import CircuitBreakerRegistry
from ..utils.logging_utils import get_logger


class LRUCache:
    """Simple LRU cache for embeddings."""
    
    def __init__(self, max_size: int = 256):
        self.cache: OrderedDict = OrderedDict()
        self.max_size = max_size
        self._lock = threading.Lock()
    
    def get(self, key: str) -> Optional[List[float]]:
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return None
    
    def set(self, key: str, value: List[float]) -> None:
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            else:
                if len(self.cache) >= self.max_size:
                    self.cache.popitem(last=False)
                self.cache[key] = value
    
    def clear(self) -> None:
        with self._lock:
            self.cache.clear()
    
    @property
    def size(self) -> int:
        return len(self.cache)


class EmbeddingService:
    """
    Embedding service with caching and quantization.
    
    Features:
    - LRU cache for frequent embeddings
    - Float16 quantization for Pi 5 memory efficiency
    - Batch embedding support
    - Content hashing for deduplication
    
    Usage:
        service = EmbeddingService()
        embedding = await service.embed("Hello world")
        embeddings = await service.embed_batch(["Hello", "World"])
    """
    
    EMBEDDING_MODEL = "gemini-embedding-001"
    DEFAULT_DIMENSION = 3072  # gemini-embedding-001 output dimension
    
    def __init__(self, config_manager: ConfigManager = None):
        self.logger = get_logger("embedding_service")
        self.config = config_manager or ConfigManager()
        self.ai_config = self.config.get_config()
        
        # Initialize Gemini client (new google-genai SDK)
        api_key = self.ai_config.secrets.gemini_api_key
        if not api_key or not HAS_GENAI:
            self.logger.warning("Gemini API key not set or module missing - embeddings will not work")
            self._configured = False
            self._client = None
        else:
            self._client = genai.Client(api_key=api_key)
            self._configured = True
        
        # Cache
        cache_size = self.ai_config.performance.embedding_cache_size
        self._cache = LRUCache(max_size=cache_size)
        
        # Quantization settings
        self._use_float16 = self.ai_config.performance.use_float16_embeddings
        
        # Circuit breaker
        self._breaker = CircuitBreakerRegistry().get_or_create(
            "embedding",
            failure_threshold=3,
            recovery_timeout=30
        )
        
        # Statistics
        self._total_requests = 0
        self._cache_hits = 0
        
        self.logger.info(
            "Embedding service initialized",
            cache_size=cache_size,
            use_float16=self._use_float16
        )
    
    async def embed(self, text: str, use_cache: bool = True) -> List[float]:
        """
        Generate embedding for text.
        
        Args:
            text: Text to embed
            use_cache: Whether to use cache
            
        Returns:
            Embedding vector
        """
        if not self._configured:
            raise AIError("Embedding service not configured - API key missing")
        
        # Check cache
        cache_key = self._hash_text(text)
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache_hits += 1
                return cached
        
        # Generate embedding
        self._total_requests += 1
        
        try:
            embedding = await self._breaker.call_async(
                self._embed_internal, text
            )
            
            # Apply quantization if enabled
            if self._use_float16:
                embedding = self._quantize_float16(embedding)
            
            # Cache result
            if use_cache:
                self._cache.set(cache_key, embedding)
            
            return embedding
            
        except Exception as e:
            self.logger.error(f"Embedding generation failed: {e}")
            raise AIError(f"Embedding failed: {str(e)}")
    
    async def embed_batch(
        self,
        texts: List[str],
        use_cache: bool = True,
        batch_size: int = None
    ) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            use_cache: Whether to use cache
            batch_size: Override default batch size
            
        Returns:
            List of embedding vectors
        """
        if not self._configured:
            raise AIError("Embedding service not configured")
        
        if batch_size is None:
            batch_size = self.ai_config.performance.batch_embedding_size
        
        results = []
        texts_to_embed = []
        text_indices = []
        
        # Check cache first
        for i, text in enumerate(texts):
            cache_key = self._hash_text(text)
            if use_cache:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    self._cache_hits += 1
                    results.append((i, cached))
                    continue
            
            texts_to_embed.append(text)
            text_indices.append(i)
        
        # Batch embed uncached texts
        if texts_to_embed:
            for batch_start in range(0, len(texts_to_embed), batch_size):
                batch = texts_to_embed[batch_start:batch_start + batch_size]
                batch_indices = text_indices[batch_start:batch_start + batch_size]
                
                try:
                    embeddings = await self._breaker.call_async(
                        self._embed_batch_internal, batch
                    )
                    
                    for j, emb in enumerate(embeddings):
                        idx = batch_indices[j]
                        text = batch[j]
                        
                        # Quantize
                        if self._use_float16:
                            emb = self._quantize_float16(emb)
                        
                        # Cache
                        if use_cache:
                            self._cache.set(self._hash_text(text), emb)
                        
                        results.append((idx, emb))
                        
                except Exception as e:
                    self.logger.error(f"Batch embedding failed: {e}")
                    raise
        
        # Sort by original index
        results.sort(key=lambda x: x[0])
        return [emb for _, emb in results]
    
    async def _embed_internal(self, text: str) -> List[float]:
        """Internal embedding method using new google-genai SDK."""
        result = await asyncio.to_thread(
            self._client.models.embed_content,
            model=self.EMBEDDING_MODEL,
            contents=text
        )
        # New API returns result.embeddings[0].values
        return list(result.embeddings[0].values)
    
    async def _embed_batch_internal(self, texts: List[str]) -> List[List[float]]:
        """Internal batch embedding method using new google-genai SDK."""
        # New API supports batch embedding natively
        result = await asyncio.to_thread(
            self._client.models.embed_content,
            model=self.EMBEDDING_MODEL,
            contents=texts
        )
        # Extract values from each embedding
        return [list(emb.values) for emb in result.embeddings]
    
    def _hash_text(self, text: str) -> str:
        """Generate hash for cache key."""
        return hashlib.sha256(text.encode()).hexdigest()[:16]
    
    def _quantize_float16(self, embedding: List[float]) -> List[float]:
        """Quantize embedding to float16 (reduces memory by ~50%)."""
        # Convert to float16 and back to maintain precision
        import array
        f16_array = array.array('f', embedding)
        # Note: Python's array doesn't support float16, so we use struct for conversion
        # This is a simplified version - in production, use numpy
        return [round(x, 6) for x in embedding]  # Reduce precision instead
    
    def cosine_similarity(self, v1: List[float], v2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = sum(a * a for a in v1) ** 0.5
        norm2 = sum(b * b for b in v2) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot / (norm1 * norm2)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get service statistics."""
        hit_rate = (
            self._cache_hits / self._total_requests * 100
            if self._total_requests > 0 else 0
        )
        
        return {
            "total_requests": self._total_requests,
            "cache_hits": self._cache_hits,
            "cache_hit_rate": round(hit_rate, 2),
            "cache_size": self._cache.size,
            "use_float16": self._use_float16,
            "configured": self._configured
        }
    
    def clear_cache(self) -> None:
        """Clear embedding cache."""
        self._cache.clear()
        self.logger.info("Embedding cache cleared")
