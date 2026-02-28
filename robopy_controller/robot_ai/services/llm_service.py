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
import logging

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


def retry_with_backoff(max_retries: int = 3, initial_delay: float = 1.0, backoff_factor: float = 2.0):
    """Decorator to retry an async function with exponential backoff on failure."""
    def decorator(func):
        import functools

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            delay = initial_delay
            last_err = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_err = e
                    # Non-retryable errors
                    if "PERMISSION_DENIED" in str(e) or "API_KEY_INVALID" in str(e):
                        raise
                    
                    logging.getLogger("llm_service").warning(
                        f"Attempt {attempt + 1}/{max_retries} failed for {func.__name__}: {e}. Retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff_factor
            raise last_err
        return wrapper
    return decorator


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
        self._live_model = "gemini-2.5-flash"
        self._live_lock = asyncio.Lock()
        
        # Session resumption tokens
        self._session_id: Optional[str] = None
        self._resumption_token: Optional[str] = None
        self._last_live_tools = None
        self._last_live_system_prompt = None
        
        # Circuit breaker
        self._breaker = CircuitBreakerRegistry().get_or_create(
            "llm",
            failure_threshold=self.ai_config.circuit_breaker.llm_failure_threshold,
            recovery_timeout=self.ai_config.circuit_breaker.recovery_timeout
        )
        
        # Statistics
        self._total_tokens = 0
        self._total_requests = 0
        self._receive_task: Optional[asyncio.Task] = None
        
        # Live Response Tracking
        self._live_response_future: Optional[asyncio.Future] = None
        self._current_live_response: Dict[str, Any] = {"text": "", "actions": []}
        
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
    
    @retry_with_backoff(max_retries=3, initial_delay=2.0)
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
        async with self._live_lock:
            await self._connect_live_unsafe(tools)

    async def _connect_live_unsafe(self, tools: List[types.FunctionDeclaration] = None):
        """Internal connect without locking (caller must hold lock)."""
        if not self._client:
            raise LLMError("LLM not configured - API key missing")
        
        # If we need to update tools, we might need to reconnect (omitted for now, assuming static tools)
        if self._live_session:
            return  # Already connected
        
        # Configure tools if provided
        live_tools = [types.Tool(function_declarations=tools)] if tools else None

        # [RECONNECT LOGIC] Check if configuration changed
        config_changed = (tools != self._last_live_tools) or (self._system_prompt != self._last_live_system_prompt)
        
        if self._live_session:
            if config_changed:
                self.logger.info("Live configuration changed, reconnecting session...")
                await self._disconnect_live_unsafe()
            else:
                return  # Already connected and config matches

        # If config changed, we cannot resume the previous session state safely with 1007 errors
        if config_changed:
            self._resumption_token = None
            self.logger.debug("Clearing resumption token due to config change")

        # Build resumption config if we have a token
        resumption_config = None
        if self._resumption_token:
            resumption_config = types.SessionResumptionConfig(
                handle=self._resumption_token
            )
            self.logger.info("Reconnecting to Live session with resumption token")
        else:
            # Enable resumption for the current session to get a token
            resumption_config = types.SessionResumptionConfig()

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            tools=live_tools,
            session_resumption=resumption_config
        )
        
        if self._system_prompt:
            # Explicitly enforce Italian in the system instruction for Live API
            enforced_prompt = f"RISPONDI SEMPRE IN ITALIANO. NON USARE MAI L'INGLESE.\n\n{self._system_prompt}"
            config.system_instruction = types.Content(parts=[types.Part.from_text(text=enforced_prompt)])
        
        try:
            # connect() returns async context manager — enter manually
            self._live_ctx = self._client.aio.live.connect(
                model=self._live_model,
                config=config
            )
            self._live_session = await self._live_ctx.__aenter__()
            self._last_live_tools = tools
            self._last_live_system_prompt = self._system_prompt
            self.logger.info("Live API session connected", model=self._live_model, tools=bool(live_tools))
        except Exception as e:
            error_str = str(e)
            self.logger.error(f"Failed to connect Live session: {error_str}")
            self._live_session = None
            self._live_ctx = None
            if "1008" in error_str or "leaked" in error_str.lower() or "permission_denied" in error_str.lower() or "403" in error_str:
                raise LLMError(f"API_KEY_INVALID: {error_str[:100]}")
            raise LLMError(f"Live session connect failed: {error_str}")
    
    async def _disconnect_live(self):
        """Disconnect Live API session."""
        async with self._live_lock:
            await self._disconnect_live_unsafe()

    async def _disconnect_live_unsafe(self):
        """Internal disconnect without locking."""
        if self._receive_task:
            self._receive_task.cancel()
            self._receive_task = None
            
        if self._live_response_future and not self._live_response_future.done():
            self._live_response_future.set_exception(LLMError("Session disconnected"))
            self._live_response_future = None

        if self._live_ctx:
            try:
                await self._live_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._live_session = None
            self._live_ctx = None
            self.logger.info("Live API session disconnected")

    async def start_persistent_live(self, tools: List[types.FunctionDeclaration] = None):
        """Start a persistent Live session with a background listener."""
        async with self._live_lock:
            if self._live_session:
                return
            
            await self._connect_live_unsafe(tools=tools)
            if self._live_session:
                self._receive_task = asyncio.create_task(self._receive_loop())

    async def _receive_loop(self):
        """Background loop to receive transcripts and audio from Gemini Live."""
        self.logger.info("Gemini Live background receiver started")
        try:
            async for msg in self._live_session.receive():
                await self._handle_live_message(msg)
                            
        except asyncio.CancelledError:
            self.logger.info("Gemini Live receiver cancelled")
        except Exception as e:
            self.logger.error(f"Gemini Live receiver error: {e}")
            self._live_session = None
            self._live_ctx = None
            if self._live_response_future and not self._live_response_future.done():
                self._live_response_future.set_exception(e)

    async def _handle_live_message(self, msg):
        """Process a single message from the Live API."""
        if not msg.server_content:
            return
        
        sc = msg.server_content
        
        # 0. Setup Complete (Capture resumption token)
        if sc.setup_complete:
            # In the latest SDK, the token might be in a different field or handled automatically,
            # but we'll try to capture it if present in the message.
            self.logger.debug("Live session setup complete")
        
        # Capture resumption token if sent in any server content
        # Note: the actual field name depends on the proto version, usually 'resumption_token'
        if hasattr(msg, 'resumption_token') and msg.resumption_token:
            self._resumption_token = msg.resumption_token
            self.logger.info("Captured Gemini Live resumption token")

        # 1. Audio and Tool Calls (Model Turn)
        if sc.model_turn:
            for part in sc.model_turn.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    # Play audio via TTS service or direct event
                    self.event_bus.publish("llm_audio_chunk", {"data": part.inline_data.data})
                
                if hasattr(part, 'text') and part.text:
                    self._current_live_response["text"] += part.text
                
                if hasattr(part, 'function_call') and part.function_call:
                    fc = part.function_call
                    action = {
                        "action_type": fc.name,
                        "args": dict(fc.args)
                    }
                    self._current_live_response["actions"].append(action)
                    # For real-time, we might want to publish actions immediately
                    # but here we also collect them for the blocking generate_live call
        
        # 2. Transcriptions (User or Model)
        if hasattr(sc, 'output_transcription') and sc.output_transcription:
            text = getattr(sc.output_transcription, 'text', '')
            is_final = getattr(sc.output_transcription, 'is_final', False)
            if text:
                self.event_bus.publish("llm_transcript_chunk", {
                    "text": text,
                    "is_final": is_final
                })
                if is_final:
                    self.logger.info(f"Gemini Live transcribed: {text}")
                    # Trigger the orchestrator command processing ONLY if it's user speech
                    # Actually, Gemini Live returns transcripts for BOTH. 
                    # Usually user transcripts come first.
                    self.event_bus.publish(EventType.VOICE_COMMAND_RECOGNIZED, {
                        "text": text,
                        "confidence": 1.0,
                        "source": "gemini_live"
                    })
        
        # 3. Turn Complete
        if getattr(sc, 'turn_complete', False):
            if self._live_response_future and not self._live_response_future.done():
                self._live_response_future.set_result(self._current_live_response.copy())
                # Reset for next call
                self._current_live_response = {"text": "", "actions": []}


    
    @retry_with_backoff(max_retries=3, initial_delay=2.0)
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
        Protected by lock to prevent concurrent usage errors.
        """
        if not self._client:
            raise LLMError("LLM not configured - API key missing")
        
        async with self._live_lock:
            return await self._generate_live_locked(prompt, context, functions, images)

    async def _generate_live_locked(
        self,
        prompt: str,
        context: List[Dict[str, str]] = None,
        functions: List[types.FunctionDeclaration] = None,
        images: List[bytes] = None,
    ) -> LLMResponse:
        start_time = time.perf_counter()
        
        # Ensure session is connected
        if not self._live_session:
            await self.start_persistent_live(tools=functions)
        
        # Setup the future and buffer to wait for response
        self._live_response_future = asyncio.get_running_loop().create_future()
        self._current_live_response = {"text": "", "actions": []}
        
        # Build message parts
        parts = []
        full_text = prompt
        if context:
            text_parts = []
            for msg_item in context:
                role = msg_item.get("role", "user")
                content = msg_item.get("content", "")
                text_parts.append(f"[{role.capitalize()}]: {content}")
            text_parts.append(f"[User]: {prompt}")
            full_text = "\n".join(text_parts)
        
        parts.append(types.Part.from_text(text=full_text))
        
        if images:
            for img_bytes in images:
                # Assuming JPEG for now
                parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))

        try:
            # Send content via Live session
            await self._live_session.send_client_content(
                turns=[types.Content(
                    role="user",
                    parts=parts
                )]
            )
            
            # Wait for background loop to collect the full response (turn_complete)
            try:
                # 30 seconds global timeout for the whole turn
                result = await asyncio.wait_for(self._live_response_future, timeout=30.0)
                text = result["text"]
                actions = result["actions"]
            except asyncio.TimeoutError:
                self.logger.error("Live session timeout waiting for response completion.")
                # Fallback: maybe we got SOME text
                text = self._current_live_response["text"] or "Timeout dal cloud."
                actions = self._current_live_response["actions"]
            
            latency_ms = (time.perf_counter() - start_time) * 1000
            self._total_requests += 1
            
            return LLMResponse(
                text=text.strip(),
                actions=actions,
                latency_ms=latency_ms,
                model=self._live_model
            )
            
        except Exception as e:
            self.logger.error(f"Live API generation error: {e}")
            raise LLMError(f"Errore Live API: {e}")
        finally:
            self._live_response_future = None

    async def send_audio_chunk(self, audio_data: bytes):
        """Send raw audio chunk to the active Live session (Stream-to-Cloud)."""
        if not self._live_session:
            # Auto-start session if it's dead
            asyncio.create_task(self.start_persistent_live())
            return
            
        try:
            await self._live_session.send_client_content(
                turns=[types.Content(
                    role="user",
                    parts=[types.Part.from_bytes(data=audio_data, mime_type="audio/pcm;rate=16000")]
                )]
            )
        except Exception as e:
            self.logger.debug(f"Failed to send audio chunk: {e}")


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
                    if isinstance(img_bytes, str):
                        data = base64.b64decode(img_bytes)
                    else:
                        data = img_bytes
                    
                    parts.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
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
