import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock

from robot_ai.orchestration.reactive_safety import ReactiveSafety
from robot_ai.orchestration.memory_manager import MemoryManager
from robot_ai.rag.memory_store import MemoryType
from geometry_msgs.msg import Twist

@pytest.fixture
def mock_publisher():
    pub = MagicMock()
    return pub

@pytest.mark.asyncio
async def test_reactive_safety_emergency_stop(mock_publisher):
    safety = ReactiveSafety(mock_publisher)
    
    # Start a movement
    await safety.move_relative("avanti", 0.5, 2.0)
    assert safety.get_twist().linear.x == 0.5
    assert not safety._move_task.done()

    # Trigger emergency stop
    safety.emergency_stop()
    await asyncio.sleep(0.01)
    
    # Movement should be cancelled, twist reset, and publisher called
    assert safety.get_twist().linear.x == 0.0
    mock_publisher.publish.assert_called()
    assert safety._move_task.cancelled() or safety._move_task.done()

@pytest.mark.asyncio
async def test_memory_manager_queue_saturation():
    mock_store = MagicMock()
    mock_embedding = AsyncMock()
    # Fast saturate config
    manager = MemoryManager(mock_store, mock_embedding, max_queue=2)
    manager.start()
    
    # Fill queue
    await manager.store_background("1", "1", MemoryType.CONVERSATION)
    await manager.store_background("2", "2", MemoryType.CONVERSATION)
    await manager.store_background("3", "3", MemoryType.CONVERSATION) # Should be dropped or logged as full
    
    # Wait for processing
    await asyncio.sleep(0.1)
    
    # Shutdown safely
    await manager.shutdown()
    
    # Store should be called at least 2 times
    assert mock_store.add.call_count >= 2
    
