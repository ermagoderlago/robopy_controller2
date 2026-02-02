"""
Robot AI Services - LLM Service
================================
Gemini API wrapper with function calling, caching, and circuit breaker.
"""

import asyncio
import time
import json
from typing import Any, Callable, Dict, List, Optional, Union
from dataclasses import dataclass, field

try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError as e:
    import sys
    print(f"DEBUG: Failed to import google.generativeai: {e}", file=sys.stderr)
    HAS_GENAI = False

from ..core.config_manager import ConfigManager
from ..core.exceptions import LLMError
from ..core.circuit_breaker import CircuitBreakerRegistry, circuit_breaker
from ..core.event_bus import EventBus, EventType
from ..utils.logging_utils import get_logger, TimedOperation
from ..utils.validation import OutputValidator


@dataclass
class LLMResponse:
    """Response from LLM."""
    text: str
    actions: List[Dict[str, Any]] = field(default_factory=list)
    reasoning: Optional[str] = None
    tokens_used: int = 0
    latency_ms: float = 0
    cached: bool = False
    model: str = ""
    finish_reason: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "actions": self.actions,
            "reasoning": self.reasoning,
            "tokens_used": self.tokens_used,
            "latency_ms": self.latency_ms,
            "cached": self.cached,
            "model": self.model,
        }


@dataclass
class FunctionDeclaration:
    """Declaration for a callable function."""
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Optional[Callable] = None


class LLMService:
    """
    Gemini API service with advanced features.
    
    Features:
    - Native function calling
    - System prompt caching
    - Two-stage reasoning
    - Circuit breaker for resilience
    - Token tracking
    
    Usage:
        service = LLMService()
        
        # Simple generation
        response = await service.generate("Ciao, come stai?")
        
        # With function calling
        response = await service.generate(
            "Accendi la luce in cucina",
            functions=[light_function]
        )
    """
    
    def __init__(self, config_manager: ConfigManager = None):
        self.logger = get_logger("llm_service")
        
        if not HAS_GENAI:
            self.logger.error("google-generativeai is missing or failed to import. LLM disabled.")
        
        self.config = config_manager or ConfigManager()
        self.ai_config = self.config.get_config()
        self.event_bus = EventBus()
        self.validator = OutputValidator(
            allowed_actions=set(self.ai_config.security.allowed_action_types)
        )
        
        # Initialize Gemini
        api_key = self.ai_config.secrets.gemini_api_key
        if not api_key or not HAS_GENAI:
            self.logger.warning("Gemini API key not set or module missing - LLM will not work")
            self._model = None
        else:
            genai.configure(api_key=api_key)
            self._model = genai.GenerativeModel(
                model_name=self.ai_config.llm.model,
                generation_config={
                    "temperature": self.ai_config.llm.temperature,
                    "max_output_tokens": self.ai_config.llm.max_tokens,
                    "top_p": self.ai_config.llm.top_p,
                    "top_k": self.ai_config.llm.top_k,
                }
            )
        
        # System prompt cache
        self._system_prompt: Optional[str] = None
        self._cached_chat = None
        
        # Circuit breaker
        self._breaker = CircuitBreakerRegistry().get_or_create(
            "llm",
            failure_threshold=self.ai_config.circuit_breaker.llm_failure_threshold,
            recovery_timeout=self.ai_config.circuit_breaker.recovery_timeout
        )
        
        # Statistics
        self._total_tokens = 0
        self._total_requests = 0
        
        self.logger.info("LLM service initialized", model=self.ai_config.llm.model)
    
    def set_system_prompt(self, prompt: str) -> None:
        """
        Set system prompt (cached for efficiency).
        
        Args:
            prompt: System prompt text
        """
        self._system_prompt = prompt
        self._cached_chat = None  # Reset chat to use new prompt
        self.logger.debug("System prompt set", length=len(prompt))
    
    async def generate(
        self,
        prompt: str,
        context: List[Dict[str, str]] = None,
        functions: List[FunctionDeclaration] = None,
        images: List[bytes] = None,
        use_reasoning: bool = None,
        max_tokens: int = None
    ) -> LLMResponse:
        """
        Generate response from LLM.
        
        Args:
            prompt: User prompt
            context: Conversation history
            functions: Available functions for calling
            images: Images for multimodal prompts
            use_reasoning: Override two-stage reasoning setting
            max_tokens: Override max tokens
            
        Returns:
            LLMResponse with text and actions
        """
        if not self._model:
            raise LLMError("LLM not configured - API key missing")
        
        start_time = time.perf_counter()
        
        # Build content
        content = self._build_content(prompt, context, images)
        
        # Two-stage reasoning if enabled
        if use_reasoning is None:
            use_reasoning = self.ai_config.llm.two_stage_reasoning
        
        try:
            # Publish event
            self.event_bus.publish(EventType.LLM_REQUEST_STARTED, {
                "prompt_length": len(prompt),
                "has_images": bool(images),
                "has_functions": bool(functions)
            })
            
            # Call through circuit breaker
            response = await self._breaker.call_async(
                self._generate_internal,
                content, functions, max_tokens
            )
            
            latency_ms = (time.perf_counter() - start_time) * 1000
            
            # Parse response
            llm_response = self._parse_response(response, latency_ms)
            
            # Update stats
            self._total_requests += 1
            self._total_tokens += llm_response.tokens_used
            
            # Publish completion event
            self.event_bus.publish(EventType.LLM_REQUEST_COMPLETED, {
                "latency_ms": latency_ms,
                "tokens": llm_response.tokens_used,
                "has_actions": bool(llm_response.actions)
            })
            
            self.logger.info(
                "LLM generation completed",
                latency_ms=round(latency_ms, 2),
                tokens=llm_response.tokens_used
            )
            
            return llm_response
            
        except Exception as e:
            self.logger.error(f"LLM generation failed: {str(e)}", exc_info=True)
            raise LLMError(f"Generation failed: {str(e)}")
    
    async def _generate_internal(
        self,
        content: Union[str, List],
        functions: List[FunctionDeclaration] = None,
        max_tokens: int = None
    ):
        """Internal generation method."""
        generation_config = {}
        if max_tokens:
            generation_config["max_output_tokens"] = max_tokens
        
        # Prepare tools if functions provided
        tools = None
        if functions and self.ai_config.llm.enable_function_calling:
            tools = self._build_tools(functions)
        
        # Generate
        if tools:
            response = await asyncio.to_thread(
                self._model.generate_content,
                content,
                tools=tools,
                generation_config=generation_config if generation_config else None
            )
        else:
            response = await asyncio.to_thread(
                self._model.generate_content,
                content,
                generation_config=generation_config if generation_config else None
            )
        
        return response
    
    def _build_content(
        self,
        prompt: str,
        context: List[Dict[str, str]] = None,
        images: List[bytes] = None
    ) -> Union[str, List]:
        """Build content for generation."""
        parts = []
        
        # Add system prompt if set
        if self._system_prompt:
            parts.append(f"[System]\n{self._system_prompt}\n\n")
        
        # Add context (conversation history)
        if context:
            for msg in context:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                parts.append(f"[{role.capitalize()}]: {content}\n")
        
        # Add current prompt
        parts.append(f"[User]: {prompt}")
        
        # If we have images, return multimodal content
        if images:
            content = [{"text": "".join(parts)}]
            for img in images:
                content.append({"inline_data": {"mime_type": "image/jpeg", "data": img}})
            return content
        
        return "".join(parts)
    
    def _build_tools(self, functions: List[FunctionDeclaration]) -> List:
        """Build Gemini tools from function declarations."""
        tool_declarations = []
        
        for func in functions:
            tool_declarations.append({
                "name": func.name,
                "description": func.description,
                "parameters": func.parameters
            })
        
        return [{"function_declarations": tool_declarations}]
    
    def _parse_response(self, response, latency_ms: float) -> LLMResponse:
        """Parse Gemini response into LLMResponse."""
        text = ""
        actions = []
        tokens_used = 0
        finish_reason = ""
        
        try:
            # Extract text
            if hasattr(response, 'text'):
                text = response.text
            elif hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, 'content') and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, 'text'):
                            text += part.text
                        elif hasattr(part, 'function_call'):
                            # Handle function call
                            fc = part.function_call
                            actions.append({
                                "action_type": fc.name,
                                "args": dict(fc.args)
                            })
            
            # Get token count if available
            if hasattr(response, 'usage_metadata'):
                tokens_used = getattr(response.usage_metadata, 'total_token_count', 0)
            
            # Get finish reason
            if hasattr(response, 'candidates') and response.candidates:
                finish_reason = str(response.candidates[0].finish_reason)
            
            # Try to parse JSON from text if no function calls
            if not actions and text:
                try:
                    parsed = self.validator.parse_json_response(text)
                    if parsed.get("actions"):
                        actions = parsed["actions"]
                    if parsed.get("response_text"):
                        text = parsed["response_text"]
                except Exception:
                    pass
            
        except Exception as e:
            self.logger.warning(f"Error parsing response: {e}")
            text = str(response)
        
        return LLMResponse(
            text=text,
            actions=actions,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            model=self.ai_config.llm.model,
            finish_reason=finish_reason
        )
    
    async def generate_with_reasoning(
        self,
        prompt: str,
        context: List[Dict[str, str]] = None
    ) -> LLMResponse:
        """
        Two-stage generation with explicit reasoning.
        
        Stage 1: Generate reasoning/plan
        Stage 2: Generate final response based on reasoning
        """
        # Stage 1: Reasoning
        reasoning_prompt = f"""
Analizza questa richiesta dell'utente e crea un piano di azione.
Considera: cosa vuole l'utente? quali azioni devo eseguire? ci sono ambiguità?

Richiesta: {prompt}

Rispondi con:
1. Comprensione: cosa ha chiesto l'utente
2. Piano: quali azioni eseguire
3. Risposta: cosa dire all'utente
"""
        reasoning_response = await self.generate(reasoning_prompt, context, use_reasoning=False)
        
        # Stage 2: Final response
        final_prompt = f"""
Basandoti su questa analisi, genera la risposta finale:

Analisi: {reasoning_response.text}

Richiesta originale: {prompt}

Genera la risposta JSON con formato:
{{"response_text": "...", "actions": [...]}}
"""
        final_response = await self.generate(final_prompt, use_reasoning=False)
        final_response.reasoning = reasoning_response.text
        
        return final_response
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get service statistics."""
        return {
            "model": self.ai_config.llm.model,
            "total_requests": self._total_requests,
            "total_tokens": self._total_tokens,
            "circuit_breaker": self._breaker.get_status() if self._breaker else None,
            "configured": self._model is not None
        }
    
    def reset_statistics(self) -> None:
        """Reset statistics counters."""
        self._total_tokens = 0
        self._total_requests = 0
