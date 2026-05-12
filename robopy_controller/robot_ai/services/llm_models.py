#!/usr/bin/env python3
"""
Robot AI Services - LLM Models & Types
========================================
Dataclasses, mock service definitions e import condizionali per Google GenAI.

Estratto da llm_service.py per migliorare la leggibilità e separazione dei concern.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Mock servizi ROS 2 custom (sostituire con i pkg reali in produzione)
# ---------------------------------------------------------------------------
class MockGenerateText:
    class Request:
        def __init__(self):
            self.prompt     = ""
            self.context    = []
            self.max_tokens = 0

    class Response:
        def __init__(self):
            self.success       = False
            self.text          = ""
            self.tokens_used   = 0
            self.latency_ms    = 0.0
            self.error_message = ""


class MockGenerateLive:
    class Request:
        def __init__(self):
            self.prompt  = ""
            self.context = []

    class Response:
        def __init__(self):
            self.success       = False
            self.text          = ""
            self.latency_ms    = 0.0
            self.error_message = ""


GenerateText = MockGenerateText
GenerateLive = MockGenerateLive


# ---------------------------------------------------------------------------
# Import Google GenAI con mock di fallback completo
# ---------------------------------------------------------------------------
try:
    from google import genai
    from google.genai import types
    from google.genai import errors as gemini_errors
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False
    genai = None
    types = None

    class gemini_errors:  # noqa: N801
        class APIError(Exception):
            pass


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class LLMResponse:
    text:          str
    actions:       List[Dict[str, Any]] = field(default_factory=list)
    formatted_document: Optional[str]   = None
    reasoning:     Optional[str]        = None
    tokens_used:   int                  = 0
    latency_ms:    float                = 0.0
    cached:        bool                 = False
    model:         str                  = ""
    finish_reason: str                  = ""


@dataclass
class FunctionDeclaration:
    name:        str
    description: str
    parameters:  Dict[str, Any]
    handler:     Optional[Callable] = None
