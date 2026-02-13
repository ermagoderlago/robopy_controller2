"""
Robot AI Services - LLM Service
================================
Gemini API wrapper with function calling, caching, and circuit breaker.
"""

import asyncio
import time
import json
import base64
from typing import Any, Callable, Dict, List, Optional, Union
from dataclasses import dataclass, field

try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError as e:
    import sys
    print(f"DEBUG: Failed to import google.genai: {e}", file=sys.stderr)
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
        
        # Initialize Gemini with new SDK
        api_key = self.ai_config.secrets.gemini_api_key
        if not api_key or not HAS_GENAI:
            self.logger.warning("Gemini API key not set or module missing - LLM will not work")
            self._client = None
            self._model_name = None
        else:
            self._client = genai.Client(api_key=api_key)
            self._model_name = self.ai_config.llm.model
            self._generation_config = types.GenerateContentConfig(
                temperature=self.ai_config.llm.temperature,
                max_output_tokens=self.ai_config.llm.max_tokens,
                top_p=self.ai_config.llm.top_p,
                top_k=self.ai_config.llm.top_k,
            )
        
        # System prompt cache
        self._system_prompt: Optional[str] = None
        self._cached_chat = None
        
        # Live API session
        self._live_session = None
        self._live_ctx = None  # async context manager
        self._live_model = "gemini-2.5-flash-native-audio-preview-12-2025"
        self._live_lock = asyncio.Lock()
        
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
        Set system prompt. Forces Live session reconnect to apply.
        
        Args:
            prompt: System prompt text
        """
        if self._system_prompt != prompt:
            self._system_prompt = prompt
            self._cached_chat = None
            # Disconnect live session so it reconnects with new prompt
            if self._live_session:
                asyncio.ensure_future(self._disconnect_live())
            self.logger.debug("System prompt updated", length=len(prompt))
    
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
        if not self._client:
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
            
        except LLMError:
            raise
        except Exception as e:
            error_str = str(e)
            self.logger.error(f"LLM generation failed: {error_str}")
            
            # User-friendly error messages
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                raise LLMError("rate_limit")
            elif "API_KEY_INVALID" in error_str or "expired" in error_str.lower():
                raise LLMError("Chiave API Gemini non valida o scaduta. Controlla GEMINI_API_KEY.")
            else:
                raise LLMError(f"Errore LLM: {error_str[:100]}")
    
    # =========================================================================
    # Live API (persistent WebSocket session — Native Audio Dialog)
    # =========================================================================
    
    async def _connect_live(self, tools: List[types.FunctionDeclaration] = None):
        """Connect to Gemini Live API session with native audio + transcription + tools."""
        if not self._client:
            raise LLMError("LLM not configured - API key missing")
        
        async with self._live_lock:
            # If we need to update tools, we might need to reconnect (omitted for now, assuming static tools)
            if self._live_session:
                return  # Already connected
            
            # Configure tools if provided
            live_tools = [types.Tool(function_declarations=tools)] if tools else None

            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                output_audio_transcription=types.AudioTranscriptionConfig(),
                tools=live_tools,
            )
            if self._system_prompt:
                config.system_instruction = types.Content(parts=[types.Part.from_text(text=self._system_prompt)])
            
            try:
                # connect() returns async context manager — enter manually
                self._live_ctx = self._client.aio.live.connect(
                    model=self._live_model,
                    config=config
                )
                self._live_session = await self._live_ctx.__aenter__()
                self.logger.info("Live API session connected", model=self._live_model, tools=bool(live_tools))
            except Exception as e:
                self.logger.error(f"Failed to connect Live session: {e}")
                self._live_session = None
                self._live_ctx = None
                raise LLMError(f"Live session connect failed: {e}")
    
    async def _disconnect_live(self):
        """Disconnect Live API session."""
        async with self._live_lock:
            if self._live_ctx:
                try:
                    await self._live_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                self._live_session = None
                self._live_ctx = None
                self.logger.info("Live API session disconnected")
    
    async def generate_live(
        self,
        prompt: str,
        context: List[Dict[str, str]] = None,
        functions: List[types.FunctionDeclaration] = None,
        images: List[bytes] = None,
    ) -> LLMResponse:
        """
        Generate response via Live API (Native Audio Dialog).
        Uses persistent WebSocket — no RPM/daily request limits.
        
        Args:
            prompt: User prompt
            context: Conversation context
            functions: Available tools/skills
            images: List of image bytes (jpeg/png)
            
        Returns:
            LLMResponse with text (transcript), audio_data, and actions
        """
        if not self._client:
            raise LLMError("LLM not configured - API key missing")
        
        start_time = time.perf_counter()
        
        # Ensure session is connected (pass tools if connecting)
        if not self._live_session:
            await self._connect_live(tools=functions)
        
        # Build message parts
        parts = []
        
        # Add context and prompt
        full_text = prompt
        if context:
            text_parts = []
            for msg in context:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                text_parts.append(f"[{role.capitalize()}]: {content}")
            text_parts.append(prompt)
            full_text = "\n".join(text_parts)
        
        parts.append(types.Part.from_text(text=full_text))
        
        # Add images
        if images:
            for img_bytes in images:
                parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))

        try:
            # Send content via Live session
            await self._live_session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=parts
                )
            )
            
            # Receive response: transcript + audio + tools
            transcript = ""
            audio_chunks = []
            actions = []
            
            async for msg in self._live_session.receive():
                if not msg.server_content:
                    continue
                sc = msg.server_content
                
                # 1. Audio
                if sc.model_turn:
                    for part in sc.model_turn.parts:
                        # Audio
                        if hasattr(part, 'inline_data') and part.inline_data:
                            if isinstance(part.inline_data.data, bytes):
                                audio_chunks.append(part.inline_data.data)
                        
                        # Function Calls inside model_turn
                        if hasattr(part, 'function_call') and part.function_call:
                            fc = part.function_call
                            actions.append({
                                "action_type": fc.name,
                                "args": dict(fc.args)
                            })
                
                # 2. Transcription
                if hasattr(sc, 'output_transcription') and sc.output_transcription:
                    t = getattr(sc.output_transcription, 'text', '')
                    if t:
                        transcript += t
                
                # 3. Explicit Tool Call (if separate field)
                if hasattr(sc, 'tool_call') and sc.tool_call:
                     if hasattr(sc.tool_call, 'function_calls'):
                         for fc in sc.tool_call.function_calls:
                             actions.append({
                                 "action_type": fc.name,
                                 "args": dict(fc.args)
                             })

                # Done
                if sc.turn_complete:
                    break
            
            latency_ms = (time.perf_counter() - start_time) * 1000
            self._total_requests += 1
            
            # Merge audio chunks
            audio_data = b"".join(audio_chunks) if audio_chunks else None
            
            self.logger.info(
                "Live API generation completed",
                latency_ms=round(latency_ms, 2),
                transcript_len=len(transcript),
                audio_bytes=len(audio_data) if audio_data else 0,
                actions=len(actions)
            )
            
            return LLMResponse(
                text=transcript.strip(),
                latency_ms=latency_ms,
                model=self._live_model,
                actions=actions
            )
            
        except Exception as e:
            error_str = str(e)
            self.logger.error(f"Live API error: {error_str}")
            
            # Session may have expired — force reconnect on next call
            self._live_session = None
            self._live_ctx = None
            
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                raise LLMError("rate_limit")
            elif "API_KEY_INVALID" in error_str or "expired" in error_str.lower():
                raise LLMError("Chiave API Gemini non valida o scaduta.")
            else:
                raise LLMError(f"Live API error: {error_str[:100]}")
    
    async def _generate_internal(
        self,
        content: Union[str, List],
        functions: List[FunctionDeclaration] = None,
        max_tokens: int = None
    ):
        """Internal generation method with retry for rate limits."""
        config = types.GenerateContentConfig(
            temperature=self.ai_config.llm.temperature,
            top_p=self.ai_config.llm.top_p,
            top_k=self.ai_config.llm.top_k,
            max_output_tokens=max_tokens or self.ai_config.llm.max_tokens,
        )
        
        max_retries = 3
        retry_delays = [5, 15, 30]  # seconds
        
        for attempt in range(max_retries):
            try:
                response = await asyncio.to_thread(
                    self._client.models.generate_content,
                    model=self._model_name,
                    contents=content,
                    config=config
                )
                return response
            except Exception as e:
                error_str = str(e)
                is_rate_limit = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str
                
                if is_rate_limit and attempt < max_retries - 1:
                    delay = retry_delays[attempt]
                    self.logger.warning(f"Rate limit hit, retry {attempt+1}/{max_retries} in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    raise
    
    def _build_content(
        self,
        prompt: str,
        context: List[Dict[str, str]] = None,
        images: List[bytes] = None
    ) -> Union[str, List]:
        """Build content for generation using google.genai types."""
        
        # Build prompt parts
        text_parts = []
        
        # Add system prompt if set
        if self._system_prompt:
            text_parts.append(f"[System Instructions]\n{self._system_prompt}\n\n")
        
        # Add context (conversation history)
        if context:
            for msg in context:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                text_parts.append(f"[{role.capitalize()}]: {content}\n")
        
        # Add current prompt
        text_parts.append(f"[User]: {prompt}")
        full_text = "".join(text_parts)
        
        # If we have images, return multimodal content
        if images:
            parts = [types.Part.from_text(text=full_text)]
            for img_bytes in images:
                try:
                    # Provide image as bytes
                    parts.append(types.Part.from_bytes(data=base64.b64decode(img_bytes), mime_type="image/jpeg"))
                except Exception as e:
                    self.logger.error(f"Failed to process image part: {e}")
            
            return parts
        
        return full_text
    
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
            "configured": self._client is not None
        }
    
    def reset_statistics(self) -> None:
        """Reset statistics counters."""
        self._total_tokens = 0
        self._total_requests = 0
