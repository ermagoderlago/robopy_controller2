#!/usr/bin/env python3
"""
Robot AI Services - LLM Service Node
======================================
Nodo ROS 2 per l'integrazione con Google Gemini API.
Architettura a doppio motore (ROS 2 MultiThreadedExecutor + Asyncio Event Loop).

Fix rispetto alla versione precedente:
  - [CRITICO]  Race su _live_response_future: check+set ora atomici sotto _live_lock
  - [MEDIO]    Timeout ROS chiama future.cancel() per non lasciare oggetti sospesi nel loop
  - [MEDIO]    Circuit breaker HALF_OPEN canonico: una sola chiamata di test, le altre bloccate
  - [BASSO]    Guard esplicita (_assert_lock) su tutti i metodi async che usano _live_lock
  - [BASSO]    Messaggi 'system' nel context loggati a WARNING (educano i nodi upstream)
"""

import asyncio
import base64
import audioop
import concurrent.futures
import functools
import json
import os
import threading
import time
import pickle
import sys
import numpy as np
sys.modules['pickle5'] = pickle
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rcl_interfaces.msg import SetParametersResult
from std_msgs.msg import String
from severus.msg import AudioData
from example_interfaces.srv import Trigger


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
    reasoning:     Optional[str]        = None
    tokens_used:   int                  = 0
    latency_ms:    float                = 0.0
    cached:        bool                 = False
    model:         str                  = ""
    finish_reason: str                  = ""
    audio_played:  bool                 = False


@dataclass
class FunctionDeclaration:
    name:        str
    description: str
    parameters:  Dict[str, Any]
    handler:     Optional[Callable] = None


# ---------------------------------------------------------------------------
# Decoratore retry con classificazione degli errori
# ---------------------------------------------------------------------------
def retry_with_backoff(
    max_retries:    int   = 3,
    initial_delay:  float = 1.0,
    backoff_factor: float = 2.0,
):
    """
    Ritenta la chiamata in caso di errori transitori.

    - PERMISSION_DENIED / API_KEY_INVALID / INVALID_ARGUMENT → fail-fast immediato
    - RESOURCE_EXHAUSTED / 429                               → backoff 3× aggiuntivo
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            delay    = initial_delay
            last_err = None
            for attempt in range(max_retries):
                try:
                    return await func(self, *args, **kwargs)
                except Exception as e:
                    last_err = e
                    str_e    = str(e)

                    if any(tag in str_e for tag in [
                        "PERMISSION_DENIED",
                        "API_KEY_INVALID",
                        "INVALID_ARGUMENT",
                    ]):
                        raise

                    if "RESOURCE_EXHAUSTED" in str_e or "429" in str_e:
                        delay *= 3

                    self.get_logger().warning(
                        f"Retry {attempt + 1}/{max_retries} fallito: {e}. "
                        f"Riprovo in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff_factor

            raise last_err
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Circuit Breaker asincrono — HALF_OPEN canonico enterprise-grade
# ---------------------------------------------------------------------------
class CircuitBreaker:
    """
    Tre stati: CLOSED → OPEN → HALF_OPEN → CLOSED.

    Comportamento HALF_OPEN canonico:
      - Solo UNA chiamata di test (probe) viene lasciata passare.
      - Tutte le altre vengono bloccate finché la probe non decide il destino.
      - Probe OK  → CLOSED, failures = 0.
      - Probe KO  → OPEN, nuovo recovery_timeout.

    Il lock asyncio viene assegnato via _init_async_resources() per evitare
    il bug di associazione al loop sbagliato (Python < 3.10).
    """

    def __init__(
        self,
        name:              str,
        failure_threshold: int   = 5,
        recovery_timeout:  float = 60.0,
    ):
        self.name              = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout
        self.failures          = 0
        self.last_failure_time: float = 0.0

        self._state           = "CLOSED"
        self._state_lock      = threading.Lock()          # lettura thread-safe da ROS
        self._probe_in_flight = False                     # flag HALF_OPEN canonico
        self._lock: Optional[asyncio.Lock] = None         # assegnato da _init_async_resources

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    @state.setter
    def state(self, value: str):
        with self._state_lock:
            self._state = value

    async def call_async(self, func, *args, **kwargs):
        if self._lock is None:
            raise RuntimeError(
                f"CircuitBreaker[{self.name}] non inizializzato: "
                "chiama _init_async_resources() prima dell'uso."
            )

        async with self._lock:
            current = self._state

            if current == "OPEN":
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    self._state           = "HALF_OPEN"
                    self._probe_in_flight = False
                    current               = "HALF_OPEN"
                else:
                    raise Exception(
                        f"Circuit breaker [{self.name}] OPEN — troppe chiamate fallite"
                    )

            if current == "HALF_OPEN":
                if self._probe_in_flight:
                    raise Exception(
                        f"Circuit breaker [{self.name}] HALF_OPEN — "
                        "probe già in volo, attendi l'esito prima di riprovare"
                    )
                self._probe_in_flight = True   # una sola probe alla volta

        try:
            result = await func(*args, **kwargs)

            async with self._lock:
                if self._state == "HALF_OPEN":
                    self._state           = "CLOSED"
                    self.failures         = 0
                    self._probe_in_flight = False

            return result

        except Exception as e:
            async with self._lock:
                self.failures         += 1
                self.last_failure_time  = time.time()
                self._probe_in_flight   = False

                if self.failures >= self.failure_threshold or self._state == "HALF_OPEN":
                    self._state = "OPEN"
            raise


# ---------------------------------------------------------------------------
# Nodo ROS 2 Principale
# ---------------------------------------------------------------------------
class LLMServiceNode(Node):
    """
    Nodo ROS 2 per l'integrazione con Google Gemini API.
    Gestisce sia il motore Asyncio per il Live API che il motore ROS 2 sincrono.
    """

    def __init__(self, config_manager=None):
        super().__init__('llm_service_node')
        self.config_manager = config_manager
        self.get_logger().info('Inizializzazione LLM Service Node')

        # ------------------------------------------------------------------
        # 1. Parametri ROS 2
        # ------------------------------------------------------------------
        self.declare_parameter('gemini_api_key',           '')
        self.declare_parameter('model_name',               'gemini-3.1-flash-lite-preview')
        self.declare_parameter('live_model_name',          'gemini-2.5-flash-native-audio-latest')

        self.declare_parameter('temperature',              0.7)
        self.declare_parameter('max_tokens',               2048)
        self.declare_parameter('circuit_breaker_failures', 5)
        self.declare_parameter('circuit_breaker_timeout',  60.0)
        self.declare_parameter('timeout_standard',         60.0)
        self.declare_parameter('timeout_live',             30.0)
        self.declare_parameter('voice_name',               'Aoede')
        self.declare_parameter('system_prompt',
            'Sei Marcus, un assistente robotico avanzato. Parla SEMPRE e SOLO in lingua italiana. '
            'REGOLE DI VOCALIZZAZIONE MANDATORIE PER LA TUA SINTESI VOCALE: '
            '1. PARLA MOLTO LENTAMENTE. Scandisci ogni singola sillaba. '
            '2. Inserisci lunghe PAUSE (1-2 secondi) dopo ogni punto e virgola. '
            '3. Non correre mai. La tua voce deve essere calma, riflessiva e molto pacata. '
            '4. Aggiungi espressioni di pausa come "uhm...", "fammi pensare...", "dunque..." per rallentare il ritmo. '
            'Sei un\'entità fisica, non un acceleratore di particelle. Sii umano.')


        # ------------------------------------------------------------------
        # 2. Cache thread-safe dei parametri
        # ------------------------------------------------------------------
        self._cfg_lock        = threading.Lock()
        self._cfg_temperature = self.get_parameter('temperature').value
        self._cfg_max_tokens  = self.get_parameter('max_tokens').value
        self._model_name      = self.get_parameter('model_name').value
        self._live_model      = self.get_parameter('live_model_name').value
        self._timeout_std     = self.get_parameter('timeout_standard').value
        self._timeout_live    = self.get_parameter('timeout_live').value
        self._system_prompt   = self.get_parameter('system_prompt').value
        self._voice_name     = 'Charon'
        self._audio_volume    = 0.2  # Ripristinato al 20%

        self.add_on_set_parameters_callback(self._parameter_callback)

        # ------------------------------------------------------------------
        # 3. Statistiche thread-safe
        # ------------------------------------------------------------------
        self._stats_lock     = threading.Lock()
        self._total_tokens   = 0
        self._total_requests = 0

        # ------------------------------------------------------------------
        # 4. Client Gemini
        # ------------------------------------------------------------------
        api_key = ''
        if self.config_manager:
             api_key = self.config_manager.get_config().secrets.gemini_api_key

        if not api_key:
             api_key = self.get_parameter('gemini_api_key').value or os.environ.get('GEMINI_API_KEY')
             
        if not api_key or not HAS_GENAI:
            self.get_logger().error(
                'Gemini API key mancante o libreria genai non installata. Nodo inattivo.')
            self._client = None
        else:
            self._client = genai.Client(api_key=api_key)



        # ------------------------------------------------------------------
        # 5. Circuit Breaker
        # ------------------------------------------------------------------
        self._breaker = CircuitBreaker(
            name="llm",
            failure_threshold=self.get_parameter('circuit_breaker_failures').value,
            recovery_timeout=self.get_parameter('circuit_breaker_timeout').value,
        )

        # ------------------------------------------------------------------
        # 6. Tools (Functions)
        # ------------------------------------------------------------------
        self._current_tools: Optional[List[Dict[str, Any]]] = None


        # ------------------------------------------------------------------
        # 6. Stato Live API — i lock asyncio vengono creati in
        #    _init_async_resources() per essere nel loop corretto.
        # ------------------------------------------------------------------
        self._live_session:          Optional[Any]            = None
        self._live_connecting:       bool                     = False
        self._live_lock:             Optional[asyncio.Lock]   = None   # init lazy
        self._resumption_token:      Optional[str]            = None
        self._live_response_future:  Optional[asyncio.Future] = None
        self._current_live_response: Dict[str, Any]           = {"text": "", "actions": [], "audio_chunks": 0}
        self.audio_cb_group = MutuallyExclusiveCallbackGroup()
        self._ratecv_state = None  # <--- Memoria per il resampler audio

        # ------------------------------------------------------------------
        # 7. ThreadPool per le chiamate bloccanti all'API standard
        # ------------------------------------------------------------------
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        # ------------------------------------------------------------------
        # 8. Interfacce ROS 2
        # ------------------------------------------------------------------
        self._create_publishers()
        self._create_subscriptions()
        self._create_services()
        self.pub_stats = self.create_publisher(String, '~/stats', 10)
        self.create_timer(60.0, self._publish_stats)

        # ------------------------------------------------------------------
        # 9. Event loop asincrono dedicato
        # ------------------------------------------------------------------
        self._loop = asyncio.new_event_loop()
        self._async_thread = threading.Thread(
            target=self._run_async_loop,
            daemon=True,
            name="llm_async_loop",
        )
        self._async_thread.start()

        # ------------------------------------------------------------------
        # 10. Inizializzazione risorse async (blocca finché non sono pronte)
        # ------------------------------------------------------------------
        if self._client:
            init_future = asyncio.run_coroutine_threadsafe(
                self._init_async_resources(), self._loop)
            init_future.result(timeout=10.0)

            asyncio.run_coroutine_threadsafe(
                self._live_connection_manager(), self._loop)

    # -----------------------------------------------------------------------
    # Guard: verifica che _live_lock sia disponibile
    # -----------------------------------------------------------------------
    def _assert_live_lock(self):
        """
        Trasforma un errore criptico (TypeError su NoneType async context manager)
        in un messaggio diagnostico chiaro e azionabile.
        """
        if self._live_lock is None:
            raise RuntimeError(
                "_live_lock non inizializzato: "
                "_init_async_resources() non è stata completata correttamente."
            )

    # -----------------------------------------------------------------------
    # Inizializzazione risorse asincrone
    # -----------------------------------------------------------------------
    async def _init_async_resources(self):
        """
        Crea asyncio.Lock nel thread dell'event loop dedicato.
        Necessario per evitare il bug di associazione al loop sbagliato
        presente in Python < 3.10.
        """
        self._live_lock     = asyncio.Lock()
        self._breaker._lock = asyncio.Lock()

    # -----------------------------------------------------------------------
    # Parametri
    # -----------------------------------------------------------------------
    def _parameter_callback(self, params) -> SetParametersResult:
        """
        Aggiorna la cache in modo thread-safe.
        La reconnessione Live viene schedulata FUORI dal _cfg_lock per non
        tenere il mutex mentre si interagisce con l'event loop asincrono.
        """
        needs_reconnect = False
        with self._cfg_lock:
            for p in params:
                if   p.name == 'temperature':     self._cfg_temperature = p.value
                elif p.name == 'max_tokens':       self._cfg_max_tokens  = p.value
                elif p.name == 'model_name':       self._model_name      = p.value
                elif p.name == 'live_model_name':
                    self._live_model    = p.value
                    needs_reconnect     = True
                elif p.name == 'timeout_standard': self._timeout_std     = p.value
                elif p.name == 'timeout_live':     self._timeout_live    = p.value
                elif p.name == 'voice_name':
                    self._voice_name    = p.value
                    needs_reconnect     = True
                elif p.name == 'system_prompt':
                    self._system_prompt = p.value
                    needs_reconnect     = True

        if needs_reconnect and self._loop and self._loop.is_running():
            self.get_logger().info("Parametri Live cambiati: richiesta riconnessione...")
            asyncio.run_coroutine_threadsafe(self._reconnect_live(), self._loop)

        return SetParametersResult(successful=True)

    def set_system_prompt(self, prompt: str):
        """
        Aggiorna il prompt di sistema.
        Se la Live API è attiva, forza una riconnessione SOLO se il prompt è cambiato.
        """
        reconnect_needed = False
        with self._cfg_lock:
            if self._system_prompt != prompt:
                self._system_prompt = prompt
                reconnect_needed = True
        
        if reconnect_needed and self._loop and self._loop.is_running():
            self.get_logger().info("System prompt cambiato, riconnessione Live scheduled.")
            asyncio.run_coroutine_threadsafe(self._reconnect_live(), self._loop)

    async def generate(self, prompt: str, images=None, max_tokens=None, functions=None):
        """
        Bridge verso _async_generate per compatibilità con VisualMemoryService.
        """
        self.get_logger().info(f"generate() invoked (prompt_len={len(prompt)})")
        class TempRequest:
            def __init__(self, p, img, mt):
                self.prompt = p
                self.images = img or []
                self.max_tokens = mt or 2048

        coro = self._async_generate(TempRequest(prompt, images, max_tokens), functions=functions)
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wrap_future(future)

    async def start_persistent_live(self):
        """
        Metodo richiesto dall'orchestratore per segnalare l'avvio della sessione.
        Il loop di connessione è già gestito internamente.
        """
        self.get_logger().info("Persistent Live session enabled.")
        return True

    async def generate_live(self, prompt: str, context=None, functions=None, images=None):
        """
        Bridge verso _async_generate_live con supporto cross-loop.
        """
        coro = self._async_generate_live(prompt, context, functions, images)

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wrap_future(future)


    async def send_audio_chunk(self, audio_data: bytes):
        """
        Invia audio alla Live API.
        """
        if not self._client:
            return
        
        msg = AudioData()
        msg.data = audio_data
        
        await self._async_audio_handler(msg)

    # -----------------------------------------------------------------------
    # Event loop
    # -----------------------------------------------------------------------
    def _run_async_loop(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        except Exception as e:
            self.get_logger().error(f"Errore fatale nell'event loop asincrono: {e}")

    # -----------------------------------------------------------------------
    # Interfacce ROS 2
    # -----------------------------------------------------------------------
    def _create_services(self):
        def cbg():
            return MutuallyExclusiveCallbackGroup()

        # self.srv_generate = self.create_service(
        #     GenerateText, '~/generate',
        #     self.generate_callback_ros, callback_group=cbg())
        # self.srv_generate_live = self.create_service(
        #     GenerateLive, '~/generate_live',
        #     self.generate_live_callback_ros, callback_group=cbg())
        self.srv_reconnect = self.create_service(
            Trigger, '~/reconnect_live',
            self.reconnect_live_callback_ros, callback_group=cbg())

    def _create_publishers(self):
        self.pub_text_response = self.create_publisher(String,    '/ai/conversation/response', 10)
        self.pub_audio_chunk   = self.create_publisher(AudioData, '/ai/conversation/audio_chunk',   10)

    def _create_subscriptions(self):
        qos_profile_audio = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        self.sub_audio = self.create_subscription(
            AudioData, '/ai/input/audio_chunk', self.audio_callback_ros, qos_profile_audio,
            callback_group=self.audio_cb_group)

    # -----------------------------------------------------------------------
    # Callback ROS 2 sincrone
    # -----------------------------------------------------------------------
    def audio_callback_ros(self, msg: AudioData):
        if not self._client:
            return
            
        # Heartbeat every 100 chunks (~3s)
        if not hasattr(self, '_audio_input_count'): self._audio_input_count = 0
        self._audio_input_count += 1
        if self._audio_input_count % 100 == 0:
            self.get_logger().info(f"🎤 [LLM] Receiving audio from /ai/input/audio_chunk (total: {self._audio_input_count})")

        asyncio.run_coroutine_threadsafe(
            self._async_audio_handler(msg), self._loop)

    def generate_callback_ros(self, request, response):
        if not self._client:
            response.success       = False
            response.error_message = "Client non configurato"
            return response

        with self._cfg_lock:
            timeout_val = self._timeout_std

        ros_future = asyncio.run_coroutine_threadsafe(
            self._async_generate(request), self._loop)
        try:
            llm_resp = ros_future.result(timeout=timeout_val)
            response.success     = True
            response.text        = llm_resp.text
            response.tokens_used = llm_resp.tokens_used
            response.latency_ms  = llm_resp.latency_ms
        except concurrent.futures.TimeoutError:
            ros_future.cancel()           # non lasciare future sospesi nel loop
            response.success       = False
            response.error_message = f"Timeout ({timeout_val}s)"
        except Exception as e:
            self.get_logger().error(f"Errore servizio generate: {e}")
            response.success       = False
            response.error_message = str(e)

        return response

    def generate_live_callback_ros(self, request, response):
        if not self._client:
            response.success       = False
            response.error_message = "Client non configurato"
            return response

        with self._cfg_lock:
            timeout_val = self._timeout_live

        ros_future = asyncio.run_coroutine_threadsafe(
            self._async_generate_live(request), self._loop)
        try:
            llm_resp = ros_future.result(timeout=timeout_val)
            response.success    = True
            response.text       = llm_resp.text
            response.latency_ms = llm_resp.latency_ms
        except concurrent.futures.TimeoutError:
            ros_future.cancel()           # non lasciare future sospesi nel loop
            response.success       = False
            response.error_message = f"Timeout Live ({timeout_val}s)"
        except RuntimeError as e:
            # Live API occupata o lock non inizializzato
            response.success       = False
            response.error_message = str(e)
        except Exception as e:
            response.success       = False
            response.error_message = str(e)

        return response

    def reconnect_live_callback_ros(self, request, response):
        """Forza disconnessione e riconnessione della Live API."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._reconnect_live(), self._loop)
        response.success = True
        response.message = "Riconnessione Live API schedulata."
        return response

    # -----------------------------------------------------------------------
    # Statistiche
    # -----------------------------------------------------------------------
    def _publish_stats(self):
        """Pubblica metriche aggregate su ~/stats ogni 60s."""
        with self._stats_lock:
            req = self._total_requests
            tok = self._total_tokens

        stats = {
            "total_requests":        req,
            "total_tokens":          tok,
            "circuit_breaker_state": self._breaker.state,  # property con threading.Lock
        }
        self.pub_stats.publish(String(data=json.dumps(stats)))

    # -----------------------------------------------------------------------
    # Logica asincrona: API Standard
    # -----------------------------------------------------------------------
    async def _async_generate(self, request, functions=None) -> LLMResponse:
        num_images = len(request.images)
        self.get_logger().info(f"Ricevuta richiesta di generazione testo: {request.prompt[:50]}... (Immagini: {num_images})")
        start = time.perf_counter()

        with self._cfg_lock:
            model_used = self._model_name
            sys_prompt = self._system_prompt

        context = None
        if hasattr(request, 'context') and request.context:
            context = [
                {"role": m.role, "content": m.content}
                for m in request.context
            ]

        contents       = self._build_contents(
            request.prompt,
            context,
            images=getattr(request, 'images', None),
        )
        req_max_tokens = getattr(request, 'max_tokens', None)

        try:
            # Tentativo primario con soglia latenza 20s (come indicato in lesson_learned.md)
            raw = await asyncio.wait_for(
                self._breaker.call_async(
                    self._generate_internal,
                    contents, req_max_tokens, model_used, sys_prompt, functions
                ),
                timeout=20.0
            )
        except (asyncio.TimeoutError, Exception) as e:
            fallback_model = "gemini-2.5-flash-lite"
            if model_used != fallback_model:
                self.get_logger().warning(
                    f"Criticità rilevata su {model_used} (latenza >20s o errore: {e}). "
                    f"Eseguo fallback su {fallback_model}..."
                )
                model_used = fallback_model
                raw = await self._breaker.call_async(
                    self._generate_internal,
                    contents, req_max_tokens, model_used, sys_prompt, functions
                )
            else:
                raise e

        latency_ms   = (time.perf_counter() - start) * 1000
        llm_response = self._parse_response(raw, latency_ms, model_used)

        with self._stats_lock:
            self._total_requests += 1
            self._total_tokens   += llm_response.tokens_used

        self.pub_text_response.publish(String(data=llm_response.text))
        return llm_response

    def set_tools(self, tools: List[Dict[str, Any]]):
        """
        Aggiorna la lista dei tool (functions) disponibili.
        Se la sessione Live è attiva, triggera una riconnessione per applicarli.
        """
        self.get_logger().info(f"Aggiornamento tools LLM ({len(tools)} funzioni caricate)")
        
        # Semplice check per evitare loop di riconnessione se i tool sono identici
        if self._current_tools == tools:
            return

        self._current_tools = tools
        
        # Se la sessione Live è già attiva, dobbiamo riavviarla per passare il nuovo LiveConnectConfig
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._reconnect_live(), self._loop)

    @retry_with_backoff(max_retries=3)
    async def _generate_internal(
        self,
        contents,
        max_tokens: Optional[int],
        model_used: str,
        sys_prompt: str,
        tools:      Optional[List[Dict]] = None,
    ):
        self.get_logger().info(f"Chiamata API Gemini ({model_used})...")
        with self._cfg_lock:
            temp   = self._cfg_temperature
            tokens = max_tokens if max_tokens and max_tokens > 0 else self._cfg_max_tokens

        sys_content = (
            types.Content(parts=[types.Part.from_text(text=sys_prompt)])
            if sys_prompt else None
        )
        config = types.GenerateContentConfig(
            temperature=temp,
            max_output_tokens=tokens,
            system_instruction=sys_content,
            tools=tools
        )

        def _blocking():
            return self._client.models.generate_content(
                model=model_used,
                contents=contents,
                config=config,
            )

        return await self._loop.run_in_executor(self._thread_pool, _blocking)

    # -----------------------------------------------------------------------
    # Logica asincrona: Live API
    # -----------------------------------------------------------------------
    async def _live_connection_manager(self):
        """
        Mantiene una connessione Live persistente con reconnessione automatica.
        Usa backoff esponenziale per evitare log-spam in caso di errori persistenti.
        """
        self._assert_live_lock()
        backoff = 2.0
        max_backoff = 60.0
        fail_count = 0

        while rclpy.ok():
            try:
                async with self._live_lock:
                    if self._live_session or self._live_connecting:
                        await asyncio.sleep(0.5)
                        continue
                    self._live_connecting = True

                with self._cfg_lock:
                    model_used = self._live_model
                    sys_prompt = self._system_prompt

                modalities = ["AUDIO"] if "native-audio" in model_used else ["TEXT", "AUDIO"]
                
                # Google SDK v0.3 compatibility check:
                # If sys_prompt is empty or None, omit the field, otherwise 1007 can happen
                ws_kwargs = {"response_modalities": modalities}
                if sys_prompt:
                    ws_kwargs["system_instruction"] = sys_prompt
                
                # Attivazione compressione per permettere sessioni audio/testuali prolungate all'infinito
                ws_kwargs["context_window_compression"] = types.ContextWindowCompressionConfig(
                    sliding_window=types.SlidingWindow()
                )
                    
                # [v3.0 #1] Expressive Voice Configuration (Puck/Charon/...)
                speech_config = types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=self._voice_name
                        )
                    )
                )
                ws_kwargs["speech_config"] = speech_config

                ws_config = types.LiveConnectConfig(**ws_kwargs)
                
                # [v3.1] Tool Integration for Live API
                if self._current_tools:
                    self.get_logger().info(f"Adding {len(self._current_tools)} tools to Live session.")
                    ws_config.tools = [types.Tool(function_declarations=self._current_tools)]

                if self._resumption_token:
                    ws_config.session_resumption = types.SessionResumptionConfig(
                        handle=self._resumption_token,
                    )
                    
                self.get_logger().info(f"Connecting to {model_used} with modalities {modalities} ...")

                async with self._client.aio.live.connect(
                    model=model_used,
                    config=ws_config,
                ) as session:
                    async with self._live_lock:
                        self._live_session = session
                        self._live_connecting = False

                    self.get_logger().info("Live API connessa con successo. Sessione pronta.")
                    # Inizializziamo il turno come completato per permettere a futuri messaggi (es. saluto) di triggerare risposta
                    # La sessione resterà comunque aperta per l'audio incoming grazie al loop receive().
                    backoff = 2.0      # reset backoff on success
                    fail_count = 0

                    async for msg in session.receive():
                        await self._handle_live_message(msg)
                    
                    # Se arriviamo qui, la sessione è finita normalmente
                    async with self._live_lock:
                        self._live_session = None
                        self._live_connecting = False

            except Exception as e:
                err_str = str(e)
                # 1000 = chiusura pulita (normale per native-audio dopo ogni turn)
                is_clean_close = '1000' in err_str
                async with self._live_lock:
                    self._live_session = None
                    self._live_connecting = False
                if is_clean_close:
                    # Riconnessione immediata senza backoff
                    self.get_logger().debug(
                        "Sessione Live chiusa normalmente, riconnessione..."
                    )
                    await asyncio.sleep(0.2)
                else:
                    fail_count += 1
                    if fail_count == 1 or fail_count % 5 == 0:
                        self.get_logger().warning(
                            f"Errore connessione Live API (tentativo {fail_count}): {e}"
                        )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 1.5, max_backoff)

    async def _async_audio_handler(self, msg: AudioData):
        """Invia un chunk audio PCM alla sessione Live attiva."""
        self._assert_live_lock()

        async with self._live_lock:
            session = self._live_session

        if not session:
            return

        try:
            # Sblocca l'invio convertendo l'array ROS in bytes puri richiesti da Google
            await session.send_realtime_input(
                media=types.Blob(
                    data=bytes(msg.data),
                    mime_type="audio/pcm;rate=16000"
                )
            )
        except Exception as e:
            self.get_logger().warning(f"Errore invio audio stream: {e}")

    async def _handle_live_message(self, msg):
        """
        Gestisce un messaggio in arrivo dalla Live API.
        Pubblica chunk audio, accumula testo/actions, segnala turn_complete.
        """
        # Diagnostic: Log raw message structure
        self.get_logger().info(f"🎤 [LIVE API] Messaggio ricevuto: {type(msg)}")
        
        # Handle session resumption update (top-level, outside server_content)
        sru = getattr(msg, 'session_resumption_update', None)
        if sru and getattr(sru, 'resumable', False) and getattr(sru, 'new_handle', None):
            self._resumption_token = sru.new_handle
            self.get_logger().info("Ricevuto nuovo token di ripresa sessione The handle will be retained.")

        if not msg.server_content:
            # Log control messages like 'interrupted' if present
            if getattr(msg, 'interrupted', None):
                self.get_logger().warning("⚠️ Gemini Live: Sessione INTERROTTA (possibile VAD o interruzione utente)")
            return
            
        sc = msg.server_content
        if getattr(sc, 'interrupted', False):
            self.get_logger().warning("⚠️ Gemini Live: Turno del modello INTERROTTO")
            self._ratecv_state = None # <--- Reset resampler on interruption to avoid glitches

        if sc.model_turn:
            for part in sc.model_turn.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    # Carica i dati grezzi a 24kHz
                    raw_audio = np.frombuffer(part.inline_data.data, dtype=np.int16)
                    
                    # Applica volume e clip
                    scaled_audio = np.clip(
                        raw_audio.astype(np.float32) * self._audio_volume,
                        -32768, 32767
                    ).astype(np.int16)
                    # --- MODIFICA QUI: Mantieni lo stato del filtro audio ---
                    audio_16k, self._ratecv_state = audioop.ratecv(
                        scaled_audio.tobytes(), 2, 1, 24000, 16000, self._ratecv_state
                    )
                    # --------------------------------------------------------
                    
                    # Pubblica il chunk a 16kHz
                    audio_msg = AudioData()
                    audio_msg.data = audio_16k
                    self.pub_audio_chunk.publish(audio_msg)
                    self._current_live_response["audio_chunks"] += 1

                if hasattr(part, 'text') and part.text:
                    self._current_live_response["text"] += part.text

                if hasattr(part, 'function_call') and part.function_call:
                    self._current_live_response["actions"].append({
                        "action_type": part.function_call.name,
                        "args":        dict(part.function_call.args),
                    })

        if getattr(sc, 'turn_complete', False):
            self._ratecv_state = None # <--- Resetta l'audio per la prossima frase
            fut = self._live_response_future
            if fut is not None and not fut.done():
                fut.set_result(self._current_live_response.copy())
            elif fut is not None and fut.cancelled():
                # Risposta arrivata su future già cancellato (tipicamente dopo timeout ROS).
                # Loggato a WARNING: indica un ritardo eccessivo o timeout troppo aggressivo.
                self.get_logger().warning(
                    "Risposta Live ricevuta su future già cancellato — scartata. "
                    "Possibile timeout lato ROS o richiesta abbandonata."
                )
            self._current_live_response = {"text": "", "actions": [], "audio_chunks": 0}

    async def _async_generate_live(self, prompt: str, context=None, functions=None, images=None) -> LLMResponse:
        """
        Invia un prompt testuale alla Live API e attende la risposta completa.
        Supporta anche immagini e funzioni.
        """

        self._assert_live_lock()

        max_attempts = 3
        last_error = None

        for attempt in range(max_attempts):
            start = time.perf_counter()

            # -- Attendi sessione disponibile (polling) --
            session = None
            for _ in range(25):  # max 5s (25 x 0.2s)
                async with self._live_lock:
                    session = self._live_session
                    if session:
                        if (
                            self._live_response_future
                            and not self._live_response_future.done()
                        ):
                            raise RuntimeError(
                                "Live API è occupata con un'altra richiesta. Riprova tra poco."
                            )
                        # Creazione atomica del future
                        self._live_response_future  = self._loop.create_future()
                        self._current_live_response = {"text": "", "actions": [], "audio_chunks": 0}
                        break
                await asyncio.sleep(0.2)

            if not session:
                raise TimeoutError("Connessione Live WebSocket non attiva.")

            try:
                content_parts = [types.Part.from_text(text=prompt)]
                if images:
                    for img in images:
                        data = base64.b64decode(img) if isinstance(img, str) else bytes(img)
                        content_parts.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))

                    await session.send(
                        input=types.LiveClientContent(
                            turns=[types.Content(role="user", parts=content_parts)],
                            turn_complete=True
                        )
                    )
                else:
                    await session.send(input=prompt, end_of_turn=True)


                with self._cfg_lock:
                    timeout_val     = self._timeout_live
                    live_model_used = self._live_model

                try:
                    result = await asyncio.wait_for(
                        self._live_response_future,
                        timeout=timeout_val,
                    )
                except asyncio.TimeoutError:
                    if self._live_response_future and not self._live_response_future.done():
                        self._live_response_future.cancel()
                    raise
                finally:
                    self._live_response_future = None

                return LLMResponse(
                    text=result["text"].strip(),
                    actions=result["actions"],
                    latency_ms=(time.perf_counter() - start) * 1000,
                    model=live_model_used,
                    audio_played=result.get("audio_chunks", 0) > 0 # Native audio actually received?
                )

            except Exception as e:
                self._live_response_future = None
                last_error = e
                err_str = str(e)
                if '1000' in err_str and attempt < max_attempts - 1:
                    self.get_logger().info(
                        f"Sessione Live chiusa (turn {attempt+1}), "
                        "attendo riconnessione..."
                    )
                    # Invalida la sessione locale: il connection manager riconnetterà
                    async with self._live_lock:
                        self._live_session = None
                    continue
                raise

        raise last_error or TimeoutError("Connessione Live WebSocket non attiva.")

    async def _reconnect_live(self):
        """
        Chiude la sessione Live corrente in modo pulito.
        _live_connection_manager si occuperà di riaprire la connessione.
        """
        self._assert_live_lock()

        async with self._live_lock:
            if self._live_session:
                try:
                    await self._live_session.close()
                except Exception as e:
                    self.get_logger().warning(f"Errore chiusura sessione Live: {e}")
                finally:
                    self._live_session    = None
                    self._live_connecting = False

    # -----------------------------------------------------------------------
    # Helper: costruzione contenuti
    # -----------------------------------------------------------------------
    def _build_contents(
        self,
        prompt:  str,
        context: Optional[List[Dict[str, str]]],
        images:  Optional[List[bytes]],
    ) -> List:
        """
        Costruisce la lista di Content nativa Gemini rispettando i ruoli.

        Mappatura ruoli:
          - 'user'      → 'user'
          - 'assistant' → 'model'
          - 'model'     → 'model'
          - 'system'    → FILTRATO con WARNING a livello WARNING
                          Il system prompt va in GenerateContentConfig.system_instruction,
                          non come turn di conversazione. Logghiamo a WARNING (non DEBUG)
                          per educare attivamente i nodi upstream che sbagliano modello
                          mentale: "il sistema funziona ma stai sbagliando".
        """
        contents = []

        if context:
            for msg in context:
                raw_role = msg.get('role', 'user').lower()

                if raw_role == 'system':
                    self.get_logger().warning(
                        "Messaggio con ruolo 'system' ricevuto nel context e ignorato. "
                        "Il system prompt va configurato via parametro ROS 'system_prompt', "
                        "non come turn di conversazione. "
                        "Controlla il nodo upstream che ha generato questa richiesta."
                    )
                    continue

                role = 'model' if raw_role in ('assistant', 'model') else 'user'
                contents.append(
                    types.Content(
                        role=role,
                        parts=[types.Part.from_text(text=msg.get('content', ''))],
                    )
                )

        parts = []
        if images:
            for img in images:
                data = base64.b64decode(img) if isinstance(img, str) else bytes(img)
                parts.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
        parts.append(types.Part.from_text(text=prompt))

        contents.append(types.Content(role="user", parts=parts))
        return contents

    # -----------------------------------------------------------------------
    # Helper: parsing risposta
    # -----------------------------------------------------------------------
    def _parse_response(self, response, latency_ms: float, model: str) -> LLMResponse:
        """Estrae testo, token e function_calls dalla risposta standard Gemini."""
        text    = ""
        actions = []
        tokens  = 0

        try:
            if hasattr(response, 'text') and response.text:
                text = response.text

            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, 'content') and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, 'function_call') and part.function_call:
                            actions.append({
                                "action_type": part.function_call.name,
                                "args":        dict(part.function_call.args),
                            })

            if hasattr(response, 'usage_metadata'):
                tokens = getattr(response.usage_metadata, 'total_token_count', 0)

        except Exception as e:
            self.get_logger().warning(f"Errore parsing risposta LLM: {e}")

        return LLMResponse(
            text=text,
            actions=actions,
            tokens_used=tokens,
            latency_ms=latency_ms,
            model=model,
        )

    # -----------------------------------------------------------------------
    # Shutdown graceful
    # -----------------------------------------------------------------------
    async def shutdown(self):
        """Metodo di shutdown asincrono chiamato dall'orchestratore."""
        self.get_logger().info("Richiesto shutdown asincrono della sessione Live...")
        await self._shutdown_async()

    async def _shutdown_async(self):
        """
        Sequenza di shutdown ordinata:
          1. Chiudi la sessione Live in modo pulito (prima dei task)
          2. Cancella tutti i task rimanenti
          3. Ferma l'event loop
        """
        self.get_logger().info("Shutdown asincrono: chiusura sessione Live...")
        try:
            await self._reconnect_live()
        except Exception as e:
            self.get_logger().warning(f"Errore chiusura Live durante shutdown: {e}")

        tasks = [
            t for t in asyncio.all_tasks(self._loop)
            if t is not asyncio.current_task()
        ]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loop.stop()

    def destroy_node(self):
        self.get_logger().info("Inizio shutdown grazioso del nodo LLM...")

        if self._loop and self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._shutdown_async(), self._loop)
                future.result(timeout=10.0)
                self._async_thread.join(timeout=5.0)
            except Exception as e:
                self.get_logger().error(f"Errore durante lo shutdown asincrono: {e}")

        if self._loop and not self._loop.is_closed():
            self._loop.close()

        if hasattr(self, '_thread_pool'):
            self._thread_pool.shutdown(wait=False)

        super().destroy_node()


# Alias per compatibilità con altri nodi che si aspettano LLMService
LLMService = LLMServiceNode


# ---------------------------------------------------------------------------
# Entry point ROS 2
# ---------------------------------------------------------------------------
def main(args=None):
    rclpy.init(args=args)
    node = LLMServiceNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()