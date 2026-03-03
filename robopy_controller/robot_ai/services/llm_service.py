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
import concurrent.futures
import functools
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import rclpy
from rclpy.node import Node
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rcl_interfaces.msg import SetParametersResult
from std_msgs.msg import String
from robopy_controller.msg import AudioData
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
        self.declare_parameter('model_name',               'gemini-2.5-flash')
        self.declare_parameter('live_model_name',          'gemini-2.5-flash-native-audio-latest')
        self.declare_parameter('temperature',              0.7)
        self.declare_parameter('max_tokens',               2048)
        self.declare_parameter('circuit_breaker_failures', 5)
        self.declare_parameter('circuit_breaker_timeout',  60.0)
        self.declare_parameter('timeout_standard',         60.0)
        self.declare_parameter('timeout_live',             30.0)
        self.declare_parameter('system_prompt',
            'Sei un robot autonomo amichevole, conciso e preciso. Rispondi in italiano.')

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
        # 6. Stato Live API — i lock asyncio vengono creati in
        #    _init_async_resources() per essere nel loop corretto.
        # ------------------------------------------------------------------
        self._live_session:          Optional[Any]            = None
        self._live_connecting:       bool                     = False
        self._live_lock:             Optional[asyncio.Lock]   = None   # init lazy
        self._resumption_token:      Optional[str]            = None
        self._live_response_future:  Optional[asyncio.Future] = None
        self._current_live_response: Dict[str, Any]           = {"text": "", "actions": []}

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
    # Guard: verifica che _live_lock sia stato inizializzato
    # -----------------------------------------------------------------------
    def _assert_live_lock(self):
        """
        Solleva RuntimeError se _live_lock è None.
        Converte un errore criptico (TypeError su NoneType async context manager)
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
                elif p.name == 'timeout_standard': self._timeout_std     = p.value
                elif p.name == 'timeout_live':     self._timeout_live    = p.value
                elif p.name == 'system_prompt':
                    self._system_prompt = p.value
                    needs_reconnect     = True

        if needs_reconnect and self._loop and self._loop.is_running():
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

    async def generate(self, prompt: str, images=None, max_tokens=None):
        """
        Bridge verso _async_generate per compatibilità con VisualMemoryService.
        """
        class TempRequest:
            def __init__(self, p, img, mt):
                self.prompt = p
                self.images = img or []
                self.max_tokens = mt or 2048

        coro = self._async_generate(TempRequest(prompt, images, max_tokens))
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
        class TempRequest:
            def __init__(self, p):
                self.prompt = p
        
        coro = self._async_generate_live(TempRequest(prompt))
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
        self.pub_text_response = self.create_publisher(String,    '~/text_response', 10)
        self.pub_audio_chunk   = self.create_publisher(AudioData, '~/audio_chunk',   10)

    def _create_subscriptions(self):
        self.sub_audio = self.create_subscription(
            AudioData, '~/audio_input', self.audio_callback_ros, 10)

    # -----------------------------------------------------------------------
    # Callback ROS 2 sincrone
    # -----------------------------------------------------------------------
    def audio_callback_ros(self, msg: AudioData):
        if not self._client:
            return
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
    async def _async_generate(self, request) -> LLMResponse:
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

        contents       = self._build_contents(request.prompt, context, images=None)
        req_max_tokens = getattr(request, 'max_tokens', None)

        raw = await self._breaker.call_async(
            self._generate_internal,
            contents, req_max_tokens, model_used, sys_prompt,
        )

        latency_ms   = (time.perf_counter() - start) * 1000
        llm_response = self._parse_response(raw, latency_ms, model_used)

        with self._stats_lock:
            self._total_requests += 1
            self._total_tokens   += llm_response.tokens_used

        self.pub_text_response.publish(String(data=llm_response.text))
        return llm_response

    @retry_with_backoff(max_retries=3)
    async def _generate_internal(
        self,
        contents,
        max_tokens: Optional[int],
        model_used: str,
        sys_prompt: str,
    ):
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
        )

        def _blocking():
            return self._client.models.generate_content(
                model=model_used,
                contents=contents,
                config=config,
            )

        return await self._loop.run_in_executor(self._thread_pool, _blocking)

    # -----------------------------------------------------------------------
    # Logica asincrona: Live API (WebSocket)
    # -----------------------------------------------------------------------
    async def _live_connection_manager(self):
        """Loop supervisore: riavvia la connessione dopo ogni interruzione."""
        while rclpy.ok():
            try:
                await self._live_connection_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.get_logger().error(f"Live API interrotta: {e}")
            await asyncio.sleep(5.0)

    async def _live_connection_loop(self):
        """
        Apre la sessione WebSocket con Gemini Live.

        TOCTOU fix: _live_connecting viene impostato atomicamente dentro il lock
        prima di rilasciarlo, impedendo a coroutine concorrenti di aprire sessioni
        duplicate pur trovando _live_session == None.
        """
        self._assert_live_lock()

        # -- Sezione critica: lettura config + set flag atomici --
        async with self._live_lock:
            if self._live_session is not None or self._live_connecting:
                return
            self._live_connecting = True

            with self._cfg_lock:
                live_model_used = self._live_model
                sys_prompt      = self._system_prompt

            resumption = (
                types.SessionResumptionConfig(handle=self._resumption_token)
                if self._resumption_token
                else types.SessionResumptionConfig()
            )
            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                output_audio_transcription=types.AudioTranscriptionConfig(),
                session_resumption=resumption,
            )
            if sys_prompt:
                config.system_instruction = types.Content(
                    parts=[types.Part.from_text(
                        text=f"RISPONDI IN ITALIANO.\n\n{sys_prompt}"
                    )]
                )
        # -- Fine sezione critica --

        try:
            self.get_logger().info("Avvio connessione Live WebSocket...")
            async with self._client.aio.live.connect(
                model=live_model_used, config=config
            ) as session:
                self.get_logger().info("Sessione Live WebSocket CONNESSA.")

                async with self._live_lock:
                    self._live_session    = session
                    self._live_connecting = False   # connessione riuscita

                async for msg in session.receive():
                    await self._handle_live_message(msg)

        except gemini_errors.APIError as e:
            if "1000" in str(e):
                self.get_logger().info("Connessione Live chiusa normalmente (codice 1000).")
            else:
                self.get_logger().error(f"Errore API Gemini Live: {e}")
        finally:
            async with self._live_lock:
                self._live_session    = None
                self._live_connecting = False   # reset in ogni caso

            if (
                self._live_response_future
                and not self._live_response_future.done()
            ):
                self._live_response_future.set_exception(
                    Exception("Sessione Live disconnessa improvvisamente")
                )

    async def _async_audio_handler(self, msg: AudioData):
        """Invia un chunk audio PCM alla sessione Live attiva."""
        self._assert_live_lock()

        async with self._live_lock:
            session = self._live_session

        if not session:
            return

        try:
            await session.send_client_content(
                turns=[types.Content(
                    role="user",
                    parts=[types.Part.from_bytes(
                        data=msg.data,
                        mime_type="audio/pcm;rate=16000",
                    )],
                )]
            )
        except Exception as e:
            self.get_logger().debug(f"Errore invio audio stream: {e}")

    async def _handle_live_message(self, msg):
        """
        Gestisce un messaggio in arrivo dalla Live API.
        Pubblica chunk audio, accumula testo/actions, segnala turn_complete.
        """
        if not msg.server_content:
            return
        sc = msg.server_content

        if hasattr(msg, 'resumption_token') and msg.resumption_token:
            self._resumption_token = msg.resumption_token

        if sc.model_turn:
            for part in sc.model_turn.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    audio_msg      = AudioData()
                    audio_msg.data = part.inline_data.data
                    self.pub_audio_chunk.publish(audio_msg)

                if hasattr(part, 'text') and part.text:
                    self._current_live_response["text"] += part.text

                if hasattr(part, 'function_call') and part.function_call:
                    self._current_live_response["actions"].append({
                        "action_type": part.function_call.name,
                        "args":        dict(part.function_call.args),
                    })

        if getattr(sc, 'turn_complete', False):
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
            self._current_live_response = {"text": "", "actions": []}

    async def _async_generate_live(self, request) -> LLMResponse:
        """
        Invia un prompt testuale alla Live API e attende la risposta completa.

        RACE FIX: il check 'Live API occupata' + la creazione del future sono
        ora atomici sotto _live_lock. Due richieste concorrenti non possono più
        passare entrambe il check e sovrascriversi il future a vicenda.
        """
        self._assert_live_lock()

        start = time.perf_counter()

        # -- Sezione critica atomica: verifica stato + crea future --
        async with self._live_lock:
            session = self._live_session

            if not session:
                raise TimeoutError("Connessione Live WebSocket non attiva.")

            if (
                self._live_response_future
                and not self._live_response_future.done()
            ):
                raise RuntimeError(
                    "Live API è occupata con un'altra richiesta. Riprova tra poco."
                )

            # Creazione atomica: nessun'altra coroutine può inserirsi qui
            self._live_response_future  = self._loop.create_future()
            self._current_live_response = {"text": "", "actions": []}
        # -- Fine sezione critica --

        await session.send_client_content(
            turns=[types.Content(
                role="user",
                parts=[types.Part.from_text(text=request.prompt)],
            )]
        )

        with self._cfg_lock:
            timeout_val     = self._timeout_live
            live_model_used = self._live_model

        try:
            result = await asyncio.wait_for(
                self._live_response_future,
                timeout=timeout_val,
            )
        except asyncio.TimeoutError:
            # Cancella il future interno per non lasciare oggetti sospesi.
            # Il warning in _handle_live_message ci avviserà se la risposta
            # arriva comunque in ritardo.
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
        )

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
                data = base64.b64decode(img) if isinstance(img, str) else img
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