"""
Robot AI Services Package
==========================
Service wrappers for external APIs.
"""

try:
    from robopy_controller.robot_ai.services.llm_service import (
        LLMService,
        LLMResponse,
        FunctionDeclaration,
    )
except ImportError:
    LLMService = None
    LLMResponse = None
    FunctionDeclaration = None

try:
    from robopy_controller.robot_ai.services.live_connection_bridge_node import LiveConnectionBridgeNode
except ImportError:
    LiveConnectionBridgeNode = None

try:
    from robopy_controller.robot_ai.services.llm_circuit_breaker import CircuitBreaker
except ImportError:
    CircuitBreaker = None

try:
    from robopy_controller.robot_ai.services.embedding_service import EmbeddingService
except ImportError:
    EmbeddingService = None

try:
    from robopy_controller.robot_ai.services.tts_service import TTSService
except ImportError:
    TTSService = None

try:
    from robopy_controller.robot_ai.services.asr_service import ASRService
except ImportError:
    ASRService = None

try:
    from robopy_controller.robot_ai.services.face_recognition_service import FaceRecognitionService, FaceRecognitionResult, UserProfile
except ImportError:
    FaceRecognitionService, FaceRecognitionResult, UserProfile = None, None, None

try:
    from robopy_controller.robot_ai.services.speaker_recognition_service import SpeakerRecognitionService, SpeakerRecognitionResult
except ImportError:
    SpeakerRecognitionService, SpeakerRecognitionResult = None, None

try:
    from robopy_controller.robot_ai.services.visual_memory_service import VisualMemoryService
except ImportError:
    VisualMemoryService = None

try:
    from robopy_controller.robot_ai.services.deepseek_service import DeepSeekService
except ImportError:
    DeepSeekService = None

from robopy_controller.robot_ai.services.audio_buffer_manager import AudioBufferManager
from robopy_controller.robot_ai.services.curiosity_evolution_engine import CuriosityEvolutionEngine
from robopy_controller.robot_ai.services.robot_documentation_service import RobotDocumentationService

__all__ = [
    "LLMService",
    "LLMResponse",
    "FunctionDeclaration",
    "EmbeddingService",
    "TTSService",
    "ASRService",
    "FaceRecognitionService",
    "FaceRecognitionResult",
    "UserProfile",
    "SpeakerRecognitionService",
    "SpeakerRecognitionResult",
    "VisualMemoryService",
    "DeepSeekService",
    "AudioBufferManager",
    "LiveConnectionBridgeNode",
    "CuriosityEvolutionEngine",
    "RobotDocumentationService",
]
