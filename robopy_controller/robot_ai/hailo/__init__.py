"""
Marcus Robot AI - Hailo-10H NPU Integration Package
===================================================
Provides asynchronous inference clients, prompt schemas, and telemetry
for executing local LLMs (Qwen2 0.5B/1.5B) on Hailo-10H NPU (40 TOPS).
"""

from .hailo_qwen_client import (
    HailoQwenClient,
    HailoQwenConfig,
    InferenceTelemetry,
    TokenChunk,
    InferenceMode,
)

__all__ = [
    "HailoQwenClient",
    "HailoQwenConfig",
    "InferenceTelemetry",
    "TokenChunk",
    "InferenceMode",
]
