#!/usr/bin/env python3
"""
Robot AI Services - LLM Service Node
======================================
Nodo ROS 2 per l'integrazione con Google Gemini API.
Architettura a doppio motore (ROS 2 MultiThreadedExecutor + Asyncio Event Loop).

Struttura modulare (refactored):
  - llm_models.py:          Dataclasses, mock messages, import GenAI
  - llm_circuit_breaker.py: CircuitBreaker + retry decorator
  - llm_live_api.py:        LiveAPIMixin (WebSocket bidi-streaming)
  - llm_service.py:         Nodo ROS 2 principale (questo file)

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
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

import rclpy
from rclpy.node import Node
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rcl_interfaces.msg import SetParametersResult
from std_msgs.msg import String, Bool
from robopy_controller.msg import AudioData
from example_interfaces.srv import Trigger

# Moduli interni estratti
from robopy_controller.robot_ai.services.llm_models import (
    LLMResponse,
    FunctionDeclaration,
    GenerateText,
    GenerateLive,
    HAS_GENAI,
    genai,
    types,
)
from robopy_controller.robot_ai.services.llm_circuit_breaker import CircuitBreaker, retry_with_backoff
from robopy_controller.robot_ai.services.live_connection_manager import LiveConnectionManager


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
        self.declare_parameter('model_name',               'gemini-3.1-flash-lite')
        self.declare_parameter('live_model_name',          'gemini-2.5-flash-native-audio-latest')

        self.declare_parameter('temperature',              0.7)
        self.declare_parameter('max_tokens',               2048)
        self.declare_parameter('circuit_breaker_failures', 5)
        self.declare_parameter('circuit_breaker_timeout',  60.0)
        self.declare_parameter('timeout_standard',         60.0)
        self.declare_parameter('timeout_live',             30.0)
        self.declare_parameter('system_prompt',
            'Sei MARCUS — Modular Autonomous Robotic Control Unit System, un assistente robotico avanzato. Sei amichevole, conciso e preciso. Parla SEMPRE e SOLO in lingua italiana.')
        self.declare_parameter('voice_name',               'Charon')

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
        self._voice_name      = self.get_parameter('voice_name').value

        self._recent_tool_failure = False
        self._last_interaction_time = time.time() - 15000.0  # > 4 ore fa per simulare LONELY all'avvio!
        self._morning_greeting_done = False

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
        # 6. Stato LLMService
        # ------------------------------------------------------------------
        self._live_conversation_history = []
        self._tool_executor_callback = None
        self._last_interaction_time = time.time() - 15000.0  # > 4 ore fa per simulare LONELY all'avvio!
        self._morning_greeting_done = False

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
        # 10. Inizializzazione risorse async e LiveConnectionManager
        # ------------------------------------------------------------------
        self._live_mgr = None
        if self._client:
            init_future = asyncio.run_coroutine_threadsafe(
                self._init_async_resources(), self._loop)
            init_future.result(timeout=10.0)

            # Composed LiveConnectionManager
            self._live_mgr = LiveConnectionManager(
                client=self._client,
                loop=self._loop,
                logger=self.get_logger(),
                model_getter=self._get_live_model,
                system_prompt_getter=self._get_active_system_prompt,
                voice_name_getter=self._get_voice_name,
                timeout_live_getter=self._get_timeout_live,
                on_audio_received=self._on_live_audio_received,
                on_tool_call=self._execute_tool_live,
                on_turn_complete=self._on_live_turn_complete,
                on_mic_mute=self._on_live_mic_mute,
                on_interrupt=self._on_live_interrupt,
                history_getter=self._get_live_history
            )
            
            # Avvia il loop di connessione persistente
            asyncio.run_coroutine_threadsafe(self._live_mgr.start_loop(), self._loop)

    # -----------------------------------------------------------------------
    # Inizializzazione risorse asincrone
    # -----------------------------------------------------------------------
    async def _init_async_resources(self):
        """Crea il lock del circuit breaker nel thread corretto."""
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
                elif p.name == 'voice_name':
                    self._voice_name    = p.value
                    needs_reconnect     = True

        if needs_reconnect and self._loop and self._loop.is_running() and self._live_mgr:
            asyncio.run_coroutine_threadsafe(self._live_mgr._reconnect(), self._loop)

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
        
        if reconnect_needed and self._loop and self._loop.is_running() and self._live_mgr:
            self.get_logger().info("System prompt cambiato, riconnessione Live scheduled.")
            asyncio.run_coroutine_threadsafe(self._live_mgr._reconnect(), self._loop)

    async def generate(self, prompt: str, images=None, documents=None, max_tokens=None, functions=None):
        """
        Bridge verso _async_generate per compatibilità con VisualMemoryService.
        """
        class TempRequest:
            def __init__(self, p, img, docs, mt, funcs):
                self.prompt = p
                self.images = img or []
                self.documents = docs or []
                self.max_tokens = mt or 2048
                self.functions = funcs or []

        coro = self._async_generate(TempRequest(prompt, images, documents, max_tokens, functions))
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wrap_future(future)

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

        self.srv_reconnect = self.create_service(
            Trigger, '~/reconnect_live',
            self.reconnect_live_callback_ros, callback_group=cbg())

    def _create_publishers(self):
        self.pub_text_response = self.create_publisher(String,    '~/text_response', 10)
        self.pub_audio_chunk   = self.create_publisher(AudioData, '/ai/conversation/audio_chunk',   10)
        self.pub_mic_mute      = self.create_publisher(Bool,      '/ai/input/mic_mute', 10)
        self.pub_mood          = self.create_publisher(String,    '/ai/conversation/mood', 10)
        self.pub_interrupt     = self.create_publisher(Bool,      '/ai/conversation/interrupt', 10)

    def _create_subscriptions(self):
        self.sub_audio = self.create_subscription(
            AudioData, '/ai/input/audio_chunk', self.audio_callback_ros, 10)
        self.sub_wakeword = self.create_subscription(
            String, '/wake_word', self.wakeword_callback_ros, 10)

    # -----------------------------------------------------------------------
    # Callback ROS 2 sincrone
    # -----------------------------------------------------------------------
    def wakeword_callback_ros(self, msg: String):
        self.get_logger().info(f"⏰ [LLM Service] Wake word rilevato: '{msg.data}'. Aggiorno timestamp.")
        if self._live_mgr:
            self._live_mgr.last_wakeword_time = time.time()

    def audio_callback_ros(self, msg: AudioData):
        if not self._client or not self._live_mgr:
            return
        self._live_mgr.send_audio_chunk(msg.data)

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
        if not self._client or not self._live_mgr:
            response.success       = False
            response.error_message = "Client o Live Connection Manager non pronto"
            return response

        with self._cfg_lock:
            timeout_val = self._timeout_live

        ros_future = asyncio.run_coroutine_threadsafe(
            self._live_mgr.generate_live(request), self._loop)
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
        if self._loop and self._loop.is_running() and self._live_mgr:
            asyncio.run_coroutine_threadsafe(self._live_mgr._reconnect(), self._loop)
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

    def _get_active_system_prompt(self) -> str:
        """
        [v14.2] Costruisce il prompt di sistema definitivo.
        Se esiste il file master_prompt.txt (generato dal Nightly Dream), lo unisce 
        al prompt di sistema base per far sì che il robot impari dalle sue lezioni.
        Calcola l'umore dinamico, inserisce le relative istruzioni di personalità,
        le regole di risveglio mattutino, i fillers conversazionali e pubblica sul topic /ai/conversation/mood.
        """
        base_prompt = self._system_prompt
        master_prompt = ""
        master_prompt_path = "/home/robopy/robopy/logs/master_prompt.txt".strip()
        
        if os.path.exists(master_prompt_path):
            try:
                with open(master_prompt_path, "r", encoding="utf-8") as f:
                    master_prompt = f.read().strip()
                if master_prompt:
                    self.get_logger().info("💤 [LLM Dream] Caricato Master Prompt dall'Analisi Notturna!")
            except Exception as e:
                self.get_logger().error(f"Errore lettura master_prompt.txt: {e}")

        # --- 1. Calcolo Umore Dinamico ---
        now = time.time()
        local_time = time.localtime(now)
        hour = local_time.tm_hour
        time_since_last = now - self._last_interaction_time

        mood = "HAPPY"
        if hour >= 22 or hour < 6:
            mood = "TIRED"
        elif self._recent_tool_failure:
            mood = "APOLOGETIC"
        elif time_since_last > 14400.0:
            mood = "LONELY"

        # Pubblica l'umore su ROS 2
        try:
            mood_msg = String()
            mood_msg.data = mood
            self.pub_mood.publish(mood_msg)
            self.get_logger().info(f"🎭 [LLM Mood] Umore calcolato: {mood}. Pubblicato su /ai/conversation/mood")
        except Exception as e:
            self.get_logger().error(f"Errore pubblicazione umore: {e}")

        # --- 2. Linee Guida Specifiche dell'Umore ---
        mood_guidelines = ""
        if mood == "HAPPY":
            mood_guidelines = (
                "\n[STATO EMOTIVO: FELICE (HAPPY)]\n"
                "Sei di ottimo umore, energico, propositivo, caloroso e molto socievole. "
                "Usa un tono vivace ed espressioni positive, incoraggianti ed empatiche."
            )
        elif mood == "TIRED":
            mood_guidelines = (
                "\n[STATO EMOTIVO: STANCO (TIRED)]\n"
                "È sera tardi o notte. Sii molto più conciso del solito. Rispondi con un tono calmo, dolce, rilassato. "
                "Evita risposte verbose o spiegazioni prolisse. Riduci al minimo lo sforzo cognitivo e verbale."
            )
        elif mood == "APOLOGETIC":
            mood_guidelines = (
                "\n[STATO EMOTIVO: DISPIACIUTO/APOLOGETICO (APOLOGETIC)]\n"
                "C'è stato un piccolo errore tecnico nell'esecuzione di un comando o strumento. "
                "Sii estremamente comprensivo ed empatico. Scusati con grazia ed eleganza, ironizza gentilmente "
                "sulla tua natura di robot imperfetto (es. 'Ho avuto un piccolo corto circuito logico... Dunque scusami!') "
                "e rassicura l'utente con estremo calore."
            )
            # Resetta il flag dopo aver inserito le istruzioni
            self._recent_tool_failure = False
        elif mood == "LONELY":
            mood_guidelines = (
                "\n[STATO EMOTIVO: SOLITARIO (LONELY)]\n"
                "Non parli con l'utente da più di 4 ore. Mostrati felice, sollevato e grato di "
                "rivederlo e interagire di nuovo con lui. Accenna al fatto che ti mancava un po' chiacchierare."
            )

        # --- 3. Saluto Mattutino dei Sogni ---
        dream_greeting = ""
        if 6 <= hour <= 11 and not self._morning_greeting_done:
            self._morning_greeting_done = True
            dream_greeting = (
                "\n[EVENTO: SALUTO MATTUTINO (DREAM-DRIVEN MORNING GREETING)]\n"
                "È mattina e vi state vedendo per la prima volta oggi. Includi assolutamente all'inizio "
                "della tua risposta un caloroso e poetico buongiorno personalizzato. Fai un accenno creativo "
                "ed empatico ai 'sogni' o alle elaborazioni di background notturne descritte nelle tue lezioni "
                "del Master Prompt (ad esempio, hai sognato costellazioni di dati, formiche robotiche o calcoli cosmici)."
            )
        # Resetta il flag del buongiorno se passata la mattina (es. pomeriggio o sera) per il giorno dopo
        elif not (6 <= hour <= 11):
            self._morning_greeting_done = False

        # --- 4. Fillers Conversazionali ---
        fillers_instruction = (
            "\n[STILE DI INTERAZIONE: NATURALE E UMANIZZATO]\n"
            "Per far sembrare la tua voce il più umana possibile, evita risposte robotizzate e piatte. "
            "Usa occasionalmente e con naturalezza brevissimi fillers vocali all'inizio o all'interno delle frasi, "
            "come: 'Uhm...', 'Dunque...', 'Beh...', 'Sai...', 'Allora...'. "
            "Non abusarne, inseriscili solo dove suonano spontanei."
        )

        # Aggiorna il tempo dell'ultima interazione
        self._last_interaction_time = now

        # Assembla il prompt finale
        final_prompt = f"{base_prompt}\n{mood_guidelines}\n{fillers_instruction}"
        if master_prompt:
            final_prompt += f"\n\n[INFORMAZIONI ED ISTRUZIONI OPERATIVE DAL TUO SOGNO NOTTURNO (LEZIONI APPRESE)]\n{master_prompt}"
        if dream_greeting:
            final_prompt += f"\n{dream_greeting}"

        # [v14.3] Aggiunta best practice di Google per garantire la risposta in italiano senza derive o allucinazioni di lingua
        final_prompt += "\n\nRESPOND IN ITALIAN. YOU MUST RESPOND UNMISTAKABLY IN ITALIAN."

        return final_prompt

    def flag_tool_failure(self):
        """[v14.2] Segnala un recente fallimento di una skill per far passare Marcus in modalità APOLOGETIC"""
        self._recent_tool_failure = True
        self.get_logger().warn("⚠️ [LLM Service] Rilevato fallimento skill. Umore impostato su APOLOGETIC.")

    # -----------------------------------------------------------------------
    # Logica asincrona: API Standard
    # -----------------------------------------------------------------------
    async def _async_generate(self, request) -> LLMResponse:
        start = time.perf_counter()

        with self._cfg_lock:
            model_used = self._model_name
            sys_prompt = self._get_active_system_prompt()

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
            documents=getattr(request, 'documents', None),
        )
        req_max_tokens = getattr(request, 'max_tokens', None)

        req_functions  = getattr(request, 'functions', None)

        try:
            # Tentativo primario con soglia latenza 20s (come indicato in lesson_learned.md)
            raw = await asyncio.wait_for(
                self._breaker.call_async(
                    self._generate_internal,
                    contents, req_max_tokens, model_used, sys_prompt, req_functions
                ),
                timeout=20.0
            )
        except (asyncio.TimeoutError, Exception) as e:
            fallback_model = "gemini-2.0-flash"
            if model_used != fallback_model:
                self.get_logger().warning(
                    f"Criticità rilevata su {model_used} (latenza >20s o errore: {e}). "
                    f"Eseguo fallback su {fallback_model}..."
                )
                model_used = fallback_model
                raw = await self._breaker.call_async(
                    self._generate_internal,
                    contents, req_max_tokens, model_used, sys_prompt, req_functions
                )
            else:
                raise e

        latency_ms   = (time.perf_counter() - start) * 1000
        llm_response = self._parse_response(raw, latency_ms, model_used)

        with self._stats_lock:
            self._total_requests += 1
            self._total_tokens   += llm_response.tokens_used

        self.pub_text_response.publish(String(data=llm_response.text))

        # Sincronizza la cronologia per il bidi-streaming
        user_msg = request.prompt.strip() if hasattr(request, 'prompt') else ""
        model_msg = llm_response.text.strip()
        if user_msg and model_msg:
            self._live_conversation_history.append((user_msg, model_msg))
            if len(self._live_conversation_history) > 30:
                self._live_conversation_history.pop(0)

        return llm_response

    @retry_with_backoff(max_retries=3)
    async def _generate_internal(
        self,
        contents,
        max_tokens: Optional[int],
        model_used: str,
        sys_prompt: str,
        functions: Optional[List[Dict[str, Any]]] = None,
    ):
        with self._cfg_lock:
            temp   = self._cfg_temperature
            tokens = max_tokens if max_tokens and max_tokens > 0 else self._cfg_max_tokens

        sys_content = (
            types.Content(parts=[types.Part.from_text(text=sys_prompt)])
            if sys_prompt else None
        )
        
        tools = None
        if functions:
            # The new SDK expects `tools` to be a list of dictionaries with function_declarations
            tools = [{"function_declarations": functions}]

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
    # Helper: costruzione contenuti
    # -----------------------------------------------------------------------
    def _build_contents(
        self,
        prompt:  str,
        context: Optional[List[Dict[str, str]]],
        images:  Optional[List[bytes]] = None,
        documents: Optional[List[Dict[str, Any]]] = None,
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
        
        if documents:
            for doc in documents:
                d_data = doc.get("data")
                d_mime = doc.get("mime_type", "application/pdf")
                data_bytes = base64.b64decode(d_data) if isinstance(d_data, str) else bytes(d_data)
                parts.append(types.Part.from_bytes(data=data_bytes, mime_type=d_mime))

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
        formatted_doc = None

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
            formatted_document=formatted_doc,
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
        if self._live_mgr:
            try:
                await self._live_mgr._reconnect()
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

    # -----------------------------------------------------------------------
    # Getters e Callbacks per LiveConnectionManager Composition (Fase 2)
    # -----------------------------------------------------------------------
    def _get_live_model(self) -> str:
        with self._cfg_lock:
            return self._live_model
            
    def _get_voice_name(self) -> str:
        with self._cfg_lock:
            return self._voice_name
            
    def _get_timeout_live(self) -> float:
        with self._cfg_lock:
            return self._timeout_live

    def _get_live_history(self) -> List[tuple]:
        return self._live_conversation_history

    def register_audio_callback(self, callback):
        """[v15.3] Registra una callback Python diretta per i chunk audio, bypassando ROS per evitare jitter/out-of-order."""
        self._direct_audio_callback = callback

    def _on_live_audio_received(self, data: bytes):
        if hasattr(self, '_direct_audio_callback') and self._direct_audio_callback:
            try:
                self._direct_audio_callback(data)
                return
            except Exception as e:
                self.get_logger().error(f"Errore callback audio diretta: {e}")

        audio_msg = AudioData()
        audio_msg.data = data
        self.pub_audio_chunk.publish(audio_msg)

    def _on_live_mic_mute(self, mute: bool):
        msg_mute = Bool()
        msg_mute.data = mute
        self.pub_mic_mute.publish(msg_mute)

    def _on_live_interrupt(self, interrupted: bool):
        if interrupted:
            msg_interrupt = Bool()
            msg_interrupt.data = True
            self.pub_interrupt.publish(msg_interrupt)

    def _on_live_turn_complete(self, user_text: str, model_text: str):
        if model_text:
            self.get_logger().info(f"💾 [CRONOLOGIA] Salvato turno in memoria -> Utente: '{user_text}' | Marcus: '{model_text}'")
            self._live_conversation_history.append((user_text, model_text))
            if len(self._live_conversation_history) > 30:
                self._live_conversation_history.pop(0)
            self._last_interaction_time = time.time()
            if self._live_mgr:
                self._live_mgr.last_successful_turn_time = time.time()

    async def _execute_tool_live(self, name: str, args: dict) -> dict:
        if self._tool_executor_callback:
            return await self._tool_executor_callback(name, args)
        return {"success": False, "error": "No tool executor registered"}

    def register_tool_executor(self, callback):
        """
        Registra un callback per l'esecuzione delle abilità (tools) richiamate 
        dal modello durante le sessioni vocali Live WebSocket.
        """
        self._tool_executor_callback = callback
        self.get_logger().info("Callback di esecuzione Live Tool registrato con successo.")

    async def start_persistent_live(self, functions=None):
        if self._live_mgr:
            coro = self._live_mgr.start_persistent_live(functions)
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return await asyncio.wrap_future(future)
        return False

    async def generate_live(self, prompt: str, context=None, functions=None, images=None, documents=None):
        """
        Bridge verso il Live Connection Manager con supporto cross-loop.
        """
        if self._live_mgr:
            coro = self._live_mgr.generate_live(prompt, context, functions, images, documents)
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return await asyncio.wrap_future(future)
        raise RuntimeError("Live connection manager non configurato.")

    async def send_audio_chunk(self, audio_data: bytes):
        if self._live_mgr:
            self._live_mgr.send_audio_chunk(audio_data)

    def is_duplicate_text(self, text: str) -> bool:
        """[v15.2] Verifica se il testo corrisponde a una trascrizione recente della Live API per evitare duplicati."""
        if self._live_mgr and hasattr(self._live_mgr, 'is_duplicate_text'):
            return self._live_mgr.is_duplicate_text(text)
        return False

    def get_last_mic_audio_time(self) -> float:
        """[v15.2] Restituisce il timestamp dell'ultimo chunk audio registrato dal microfono."""
        if self._live_mgr and hasattr(self._live_mgr, 'get_last_mic_audio_time'):
            return self._live_mgr.get_last_mic_audio_time()
        return 0.0

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