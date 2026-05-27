import pytest
import asyncio
import time
import os
import shutil
from unittest.mock import MagicMock, AsyncMock

from robot_ai.rag.chroma_native_store import ChromaNativeStore
from robot_ai.rag.memory_store import Memory, MemoryType

@pytest.fixture
def temp_db_path(tmp_path):
    return str(tmp_path / "ChromaDB_Test")

@pytest.fixture
def mock_embedding_service():
    service = MagicMock()
    # Mocking embed to return a standard 768-dimensional vector
    service.embed = AsyncMock(return_value=[0.1] * 768)
    return service

def test_chroma_native_store_basic_flow(temp_db_path, mock_embedding_service):
    store = ChromaNativeStore(
        persist_dir=temp_db_path,
        collection_name="test_collection",
        embedding_service=mock_embedding_service
    )
    
    # Create a memory with a valid 768-dimensional embedding
    mem = Memory(
        id="test_mem_1",
        content="Marcus is in the testing room.",
        memory_type=MemoryType.CONVERSATION,
        embedding=[0.5] * 768,
        metadata={"author": "tester"}
    )
    
    # 1. Test add
    mem_id = store.add(mem)
    assert mem_id == "test_mem_1"
    assert store.get_by_id("test_mem_1") is True
    
    # 2. Test get
    retrieved = store.get("test_mem_1")
    assert retrieved is not None
    assert retrieved.content == "Marcus is in the testing room."
    assert retrieved.metadata["author"] == "tester"
    
    # 3. Test get_recent
    recent = store.get_recent(limit=5, memory_type=MemoryType.CONVERSATION)
    assert len(recent) == 1
    assert recent[0].id == "test_mem_1"

def test_chroma_native_store_vector_dimension_assertion(temp_db_path, mock_embedding_service):
    store = ChromaNativeStore(
        persist_dir=temp_db_path,
        collection_name="test_collection",
        embedding_service=mock_embedding_service
    )
    
    # Create memory with a corrupt/incorrectly dimensioned embedding (e.g. 512 instead of 768)
    mem_invalid = Memory(
        id="invalid_mem",
        content="This vector is corrupt.",
        memory_type=MemoryType.CONVERSATION,
        embedding=[0.2] * 512
    )
    
    mem_id = store.add(mem_invalid)
    # The record should be discarded (empty string returned), preventing vector space corruption
    assert mem_id == ""
    assert store.get_by_id("invalid_mem") is False

def test_chroma_native_store_temporal_sorting(temp_db_path, mock_embedding_service):
    store = ChromaNativeStore(
        persist_dir=temp_db_path,
        collection_name="test_collection_sort",
        embedding_service=mock_embedding_service
    )
    
    now = time.time()
    
    mem_old = Memory(
        id="old_mem",
        content="Old memory.",
        memory_type=MemoryType.CONVERSATION,
        embedding=[0.1] * 768,
        created_at=now - 3600
    )
    
    mem_new = Memory(
        id="new_mem",
        content="New memory.",
        memory_type=MemoryType.CONVERSATION,
        embedding=[0.1] * 768,
        created_at=now
    )
    
    store.add(mem_old)
    store.add(mem_new)
    
    recent = store.get_recent(limit=10, memory_type=MemoryType.CONVERSATION)
    assert len(recent) == 2
    # The newest memory should be first in the list
    assert recent[0].id == "new_mem"
    assert recent[1].id == "old_mem"
