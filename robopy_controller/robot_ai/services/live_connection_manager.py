#!/usr/bin/env python3
"""
Robot AI Services - Live Connection Manager
===========================================
Decoupled class managing persistent Gemini Live WebSocket bidi-streaming sessions.
Implements compositions, asyncio callbacks, background PCM audio queues with oldest-drop policies,
and GIL contention mitigation via non-blocking dispatch.
"""

import asyncio
import base64
import time
import logging
from typing import Any, Dict, List, Optional, Callable, Awaitable

from robopy_controller.robot_ai.services.llm_models import LLMResponse, types

class LiveConnectionManager:
    """
    Manages WebSocket bidi-streaming connection to Google Gemini Live API.
    Used by LLMServiceNode via composition.
    """
    def __init__(
        self,
        client: Any,
        loop: asyncio.AbstractEventLoop,
        logger: logging.Logger,
        model_getter: Callable[[], str],
        system_prompt_getter: Callable[[], str],
        voice_name_getter: Callable[[], str],
        timeout_live_getter: Callable[[], float],
        on_audio_received: Callable[[bytes], None],
        on_tool_call: Callable[[str, dict], Awaitable[dict]],
        on_turn_complete: Callable[[str, str], None],
        on_mic_mute: Callable[[bool], None],
        on_interrupt: Callable[[bool], None],
        history_getter: Callable[[], List[tuple]]
    ):
        self.client = client
        self._loop = loop
        self.logger = logger
        self.model_getter = model_getter
        self.system_prompt_getter = system_prompt_getter
        self.voice_name_getter = voice_name_getter
        self.timeout_live_getter = timeout_live_getter
        self.on_audio_received = on_audio_received
        self.on_tool_call = on_tool_call
        self.on_turn_complete = on_turn_complete
        self.on_mic_mute = on_mic_mute
        self.on_interrupt = on_interrupt
        self.history_getter = history_getter

        # Internal state
        self._live_session: Optional[Any] = None
        self._live_connecting: bool = False
        self._live_lock: Optional[asyncio.Lock] = None
        self._audio_in_queue: Optional[asyncio.Queue] = None
        self._resumption_token: Optional[str] = None
        self._live_response_future: Optional[asyncio.Future] = None
        self._current_live_response: Dict[str, Any] = {"text": "", "actions": []}
        self._live_functions: Optional[List[Dict[str, Any]]] = None
        self._activity_started: bool = False
        self._current_user_text: str = ""
        
        # State timings updated by LLMService
        self.last_successful_turn_time = 0.0
        self.last_wakeword_time = 0.0

        # Initialize lock and queues safely within the event loop
        init_fut = asyncio.run_coroutine_threadsafe(self._init_async_resources(), self._loop)
        init_fut.result(timeout=5.0)

    async def _init_async_resources(self):
        self._live_lock = asyncio.Lock()
        self._audio_in_queue = asyncio.Queue(maxsize=50)

    def _is_text_noise_or_empty(self, text: str) -> bool:
        """Helper to classify empty or noisy ASR transcriptions to avoid blank prompts."""
        clean = text.strip().lower().replace(".", "").replace(",", "").replace("?", "").replace("!", "")
        if not clean:
            return True
        noise_words = {
            "ah", "eh", "oh", "uh", "hm", "m", "he", "um", "uhm", "mh", "er", "o", "a",
            "sì", "no", "ciao", "ok", "ma", "e", "di", "per"
        }
        words = clean.split()
        if len(words) == 0:
            return True
        if len(words) == 1 and (words[0] in noise_words or len(words[0]) < 2):
            return True
        if len(words) == 2 and all(w in noise_words for w in words):
            return True
        return False

    async def start_loop(self):
        """Starts the persistent connection manager loop."""
        asyncio.create_task(self._live_connection_manager_loop())

    def send_audio_chunk(self, chunk: bytes):
        """Thread-safe entrypoint to schedule audio chunk enqueueing."""
        if not self._loop or not self._loop.is_running():
            return
        self._loop.call_soon_threadsafe(self._enqueue_audio, chunk)

    def _enqueue_audio(self, chunk: bytes):
        """Enqueues audio chunk with a sliding window drop policy to prevent OOM."""
        if not isinstance(chunk, bytes):
            try:
                if hasattr(chunk, 'tobytes'):
                    chunk = chunk.tobytes()
                else:
                    chunk = bytes(chunk)
            except Exception as e:
                self.logger.error(f"Errore conversione chunk audio in bytes: {e}")
                return

        if self._audio_in_queue.full():
            try:
                self._audio_in_queue.get_nowait()
                self.logger.warning("⚠️ OOM Prevention: Coda PCM piena. Scarto il chunk più vecchio.")
            except asyncio.QueueEmpty:
                pass
        try:
            self._audio_in_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            pass

    async def start_persistent_live(self, functions=None):
        """Updates tools and forces a clean reconnection to inject them."""
        self._live_functions = functions
        self.logger.info(f"Aggiornate {len(functions) if functions else 0} funzioni per la Live API.")
        await self._reconnect()
        return True

    async def _reconnect(self):
        """Cleanly disconnects the active session to trigger reconnection by connection manager loop."""
        async with self._live_lock:
            if self._live_session:
                try:
                    await self._live_session.close()
                except Exception as e:
                    self.logger.warning(f"Errore chiusura sessione Live durante reconnect: {e}")
                finally:
                    self._live_session = None
                    self._live_connecting = False

    async def generate_live(self, prompt: str, context=None, functions=None, images=None, documents=None) -> LLMResponse:
        """Sends text context or multimedia input to bidi stream and awaits full text/action response."""
        prompt_str = prompt.prompt if hasattr(prompt, 'prompt') else str(prompt)
        
        # Check if functions updated and force reconnect if so
        reconnect_needed = False
        async with self._live_lock:
            if functions and functions != self._live_functions:
                self._live_functions = functions
                reconnect_needed = True
        
        if reconnect_needed:
            self.logger.info("Funzioni Live cambiate in generate_live, riconnessione...")
            await self._reconnect()

        max_attempts = 3
        last_error = None

        for attempt in range(max_attempts):
            start = time.perf_counter()

            # Poll until a session is active
            session = None
            for _ in range(25):  # 5 seconds max
                async with self._live_lock:
                    session = self._live_session
                    if session:
                        if self._live_response_future and not self._live_response_future.done():
                            raise RuntimeError("Live API è occupata con un'altra richiesta.")
                        self._live_response_future = self._loop.create_future()
                        self._current_live_response = {"text": "", "actions": []}
                        break
                await asyncio.sleep(0.2)

            if not session:
                raise TimeoutError("Connessione Live WebSocket non attiva.")

            try:
                content_parts = [types.Part.from_text(text=prompt_str)]
                if images or documents:
                    if images:
                        for img in images:
                            data = base64.b64decode(img) if isinstance(img, str) else bytes(img)
                            content_parts.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
                    if documents:
                        for doc in documents:
                            d_data = doc.get("data")
                            d_mime = doc.get("mime_type", "application/pdf")
                            data_bytes = base64.b64decode(d_data) if isinstance(d_data, str) else bytes(d_data)
                            content_parts.append(types.Part.from_bytes(data=data_bytes, mime_type=d_mime))

                    await session.send(
                        input=types.LiveClientContent(
                            turns=[types.Content(role="user", parts=content_parts)],
                            turn_complete=True
                        )
                    )
                else:
                    await session.send(input=prompt_str, end_of_turn=True)

                timeout_val = self.timeout_live_getter()
                try:
                    result = await asyncio.wait_for(self._live_response_future, timeout=timeout_val)
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
                    model=self.model_getter()
                )

            except Exception as e:
                self._live_response_future = None
                last_error = e
                if '1000' in str(e) and attempt < max_attempts - 1:
                    self.logger.info(f"Sessione Live chiusa (tentativo {attempt+1}), attendo riconnessione...")
                    async with self._live_lock:
                        self._live_session = None
                    continue
                raise

        raise last_error or TimeoutError("Errore invio generativo Live.")

    async def _live_connection_manager_loop(self):
        """Keeps connection active with auto resumption and exponential backoff."""
        backoff = 2.0
        max_backoff = 60.0
        fail_count = 0

        while True:
            try:
                async with self._live_lock:
                    if self._live_session or self._live_connecting:
                        await asyncio.sleep(0.5)
                        continue
                    self._live_connecting = True

                model_used = self.model_getter()
                sys_prompt = self.system_prompt_getter()
                live_functions = self._live_functions
                voice_name = self.voice_name_getter()

                # Gating rules for voice filtering
                voice_gating_instructions = (
                    "\n\n[REGOLE DI INTERAZIONE VOCALE E GATING (CRITICHE)]\n"
                    "1. Se l'input dell'utente sembra essere rumore di fondo, silenzio, un frammento di discorso origliato o conversazione tra terzi non rivolta a te, e non c'è una chiara correlazione con il contesto recente della nostra conversazione, NON rispondere normalmente.\n"
                    "In questo caso, rispondi ESCLUSIVAMENTE con la parola chiave speciale '<IGNORE_TURN>' e nient'altro (nessun parlato, nessun suono, nessun testo).\n"
                    "2. Rispondi normalmente solo se l'utente ti chiama direttamente per nome ('Marcus') o se la frase è una continuazione coerente, diretta e logica della conversazione recente.\n"
                )
                full_sys_prompt = sys_prompt + voice_gating_instructions
                history = self.history_getter()
                if history:
                    history_str = (
                        "\n\n[CRONOLOGIA RECENTE DELLA CONVERSAZIONE (FONDAMENTALE)]\n"
                        "Di seguito trovi gli ultimi scambi della conversazione in corso. Usali come contesto per rispondere coerentemente:\n"
                    )
                    for usr, bot in history:
                        history_str += f"Utente: {usr}\nMarcus: {bot}\n"
                    full_sys_prompt = full_sys_prompt + history_str

                modalities = ["AUDIO"] if "native-audio" in model_used else ["TEXT", "AUDIO"]
                
                ws_kwargs = {"response_modalities": modalities}
                if full_sys_prompt:
                    ws_kwargs["system_instruction"] = types.Content(parts=[types.Part.from_text(text=full_sys_prompt)])
                
                if live_functions:
                    ws_kwargs["tools"] = [{"function_declarations": live_functions}]
                
                ws_kwargs["context_window_compression"] = types.ContextWindowCompressionConfig(
                    sliding_window=types.SlidingWindow()
                )

                # Enabled input and output audio transcriptions
                ws_kwargs["input_audio_transcription"] = types.AudioTranscriptionConfig()
                ws_kwargs["output_audio_transcription"] = types.AudioTranscriptionConfig()

                # Disable automatic VAD since we do local VUI node gating
                ws_kwargs["realtime_input_config"] = types.RealtimeInputConfig(
                    automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
                )

                ws_kwargs["speech_config"] = types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                    )
                )
                    
                ws_config = types.LiveConnectConfig(**ws_kwargs)

                if self._resumption_token:
                    ws_config.session_resumption = types.SessionResumptionConfig(
                        handle=self._resumption_token,
                    )
                    
                was_connected = False
                try:
                    async with self.client.aio.live.connect(model=model_used, config=ws_config) as session:
                        async with self._live_lock:
                            self._live_session = session
                            self._live_connecting = False
                            self._activity_started = False
                            was_connected = True

                        self.logger.info("Live API connessa con successo.")
                        backoff = 2.0
                        fail_count = 0

                        # Purge stale audio chunks from queue
                        while not self._audio_in_queue.empty():
                            try:
                                self._audio_in_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break

                        # Launch non-blocking background consumer for outgoing audio (mitigates GIL)
                        sender_task = asyncio.create_task(self._audio_sender_loop(session))

                        try:
                            async for msg in session.receive():
                                await self._handle_live_message(msg)
                        finally:
                            sender_task.cancel()
                            try:
                                await sender_task
                            except asyncio.CancelledError:
                                pass
                finally:
                    async with self._live_lock:
                        self._live_session = None
                        self._live_connecting = False
                        self._activity_started = False
                        if self._live_response_future and not self._live_response_future.done():
                            self._live_response_future.cancel()
                        if was_connected:
                            self.logger.info("Sessione Live disconnessa o conclusa, pulizia completata.")
            except Exception as e:
                err_str = str(e)
                is_clean_close = '1000' in err_str
                async with self._live_lock:
                    self._live_session = None
                    self._live_connecting = False
                if is_clean_close:
                    self.logger.debug("Sessione Live chiusa normalmente (1000), riconnessione...")
                    await asyncio.sleep(0.2)
                else:
                    fail_count += 1
                    if fail_count == 1 or fail_count % 5 == 0:
                        self.logger.warning(f"Errore connessione Live API (tentativo {fail_count}): {e}")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 1.5, max_backoff)

    async def _audio_sender_loop(self, session: Any):
        """Asynchronously pulls audio chunk from the non-blocking queue and streams it to Gemini."""
        self.logger.info("🎤 Avviato loop di invio audio PCM asincrono per la sessione.")
        try:
            while self._live_session == session:
                try:
                    chunk = await asyncio.wait_for(self._audio_in_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                if len(chunk) == 0:
                    # Empty chunk = End of speech signal
                    self.logger.info("🎤 [Live] Ricevuto END OF SPEECH da VUI. Eseguo controlli di gating...")
                    
                    user_text = self._current_user_text.strip().lower()
                    is_noise = self._is_text_noise_or_empty(user_text)
                    has_direct_address = "marcus" in user_text
                    
                    last_turn_ago = time.time() - self.last_successful_turn_time
                    last_wakeword_ago = time.time() - self.last_wakeword_time
                    is_active = (
                        last_wakeword_ago < 60.0 or
                        last_turn_ago < 30.0
                    )
                    
                    self.logger.debug(
                        f"[LLM Gate] wakeword_ago={last_wakeword_ago:.1f}s | turn_ago={last_turn_ago:.1f}s | "
                        f"is_active={is_active} | is_noise={is_noise} | has_address={has_direct_address}"
                    )
                    
                    should_ignore = False
                    if is_noise:
                        self.logger.info(f"🔇 [Live] Turno ignorato: rilevato solo silenzio o rumore ('{user_text}').")
                        should_ignore = True
                    elif not is_active and not has_direct_address:
                        self.logger.info(f"🔇 [Live] Turno ignorato: nessun indirizzo diretto ('marcus') fuori dalla finestra di conversazione.")
                        should_ignore = True
                        
                    if should_ignore:
                        # Mute mic callback
                        if self.on_mic_mute:
                            self.on_mic_mute(True)
                        
                        self._current_user_text = ""
                        self._current_live_response = {"text": "", "actions": []}
                        asyncio.create_task(self._reconnect())
                        continue

                    # Valid turn, proceed with sending activity_end
                    self.logger.info(f"🎤 [Live] Turno valido ('{user_text}'). Invio activity_end a Gemini...")
                    try:
                        await session.send_realtime_input(activity_end=types.ActivityEnd())
                        self._activity_started = False
                        self.logger.info("✅ [Live] activity_end inviato.")
                    except Exception as e:
                        self.logger.error(f"❌ Errore invio activity_end: {e}")
                    continue

                try:
                    if not self._activity_started:
                        await session.send_realtime_input(activity_start=types.ActivityStart())
                        self._activity_started = True
                        self.logger.info("🎤 [Live] activity_start inviato — inizio turno vocale.")

                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=chunk,
                            mime_type="audio/pcm;rate=16000"
                        )
                    )
                except Exception as e:
                    self.logger.error(f"❌ Errore invio chunk PCM: {e}")
        except asyncio.CancelledError:
            self.logger.info("🎤 Loop di invio audio PCM cancellato.")
        except Exception as e:
            self.logger.error(f"❌ Errore nel loop di invio audio PCM: {e}")

    async def _handle_live_message(self, msg: Any):
        """Processes message from the model and dispatches it via callbacks to avoid blocking connection."""
        msg_fields = []
        if getattr(msg, 'server_content', None):
            msg_fields.append('server_content')
        if getattr(msg, 'session_resumption_update', None):
            msg_fields.append('session_resumption_update')
        if getattr(msg, 'tool_call', None):
            msg_fields.append('tool_call')
        if getattr(msg, 'tool_call_cancellation', None):
            msg_fields.append('tool_call_cancellation')
        if getattr(msg, 'setup_complete', None):
            msg_fields.append('setup_complete')
        
        self.logger.info(f"📩 [Live] Messaggio ricevuto: [{', '.join(msg_fields or ['empty'])}]")

        sru = getattr(msg, 'session_resumption_update', None)
        if sru and getattr(sru, 'resumable', False) and getattr(sru, 'new_handle', None):
            self._resumption_token = sru.new_handle
            self.logger.info("Ricevuto nuovo token di ripresa sessione.")

        # Non-blocking dispatch of tool calls using run_coroutine_threadsafe to mitigate GIL blocking
        if getattr(msg, 'tool_call', None):
            self.logger.info("🛠️ [Live] Ricevuta richiesta di Tool Call dal modello!")
            asyncio.create_task(self._execute_and_respond_tool_call(msg.tool_call))

        if not msg.server_content:
            return
        sc = msg.server_content

        if getattr(sc, 'interrupted', False):
            self.logger.warning("🤫 [Live] Interruzione rilevata dal server! Invio segnale di interrupt...")
            self._current_user_text = ""
            self._current_live_response = {"text": "", "actions": []}
            if self.on_interrupt:
                self.on_interrupt(True)

        if getattr(sc, 'input_transcription', None) and sc.input_transcription.text:
            transcription = sc.input_transcription.text
            self._current_user_text += " " + transcription
            self.logger.info(f"🎤 [Live ASR] Trascrizione utente: {transcription}")

            user_speech = self._current_user_text.lower()
            exit_phrases = ["stai zitto", "basta parlare", "fermati di parlare", "smetti di parlare", "taci", "silenzio", "zitto marcus", "marcus zitto", "basta marcus", "marcus basta"]
            if any(cmd in user_speech for cmd in exit_phrases):
                self.logger.info(f"🤫 [Live ASR] Rilevato comando di silenzio dell'utente: '{user_speech}'! Disattivazione...")
                if self.on_mic_mute:
                    self.on_mic_mute(True)
                
                self._current_user_text = ""
                self._current_live_response = {"text": "", "actions": []}
                asyncio.create_task(self._reconnect())
                return

        if getattr(sc, 'output_transcription', None) and sc.output_transcription.text:
            transcription = sc.output_transcription.text
            self._current_live_response["text"] += transcription
            self.logger.info(f"🔊 [Live Model ASR] Trascrizione Marcus: {transcription}")

        if sc.model_turn:
            ignore_detected = False
            for part in sc.model_turn.parts:
                if hasattr(part, 'text') and part.text:
                    if "<ignore_turn>" in part.text.lower() or "ignore_turn" in part.text.lower():
                        ignore_detected = True
                        break
            
            if ignore_detected or "<ignore_turn>" in self._current_live_response["text"].lower():
                self.logger.info("🤫 [Live Model] Rilevato <IGNORE_TURN>! Soppressione risposta...")
                if self.on_mic_mute:
                    self.on_mic_mute(True)
                
                self._current_user_text = ""
                self._current_live_response = {"text": "", "actions": []}
                asyncio.create_task(self._reconnect())
                return

            for part in sc.model_turn.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    # Dispatch audio to callback
                    if self.on_audio_received:
                        self.on_audio_received(part.inline_data.data)

                if hasattr(part, 'text') and part.text:
                    self._current_live_response["text"] += part.text

                if hasattr(part, 'function_call') and part.function_call:
                    self._current_live_response["actions"].append({
                        "action_type": part.function_call.name,
                        "args": dict(part.function_call.args),
                    })

        if getattr(sc, 'turn_complete', False):
            fut = self._live_response_future
            if fut is not None and not fut.done():
                fut.set_result(self._current_live_response.copy())
            elif fut is not None and fut.cancelled():
                self.logger.warning("Risposta Live ricevuta su future già cancellato — scartata.")
            
            user_msg = self._current_user_text.strip() or "[Ascolto Vocale]"
            model_msg = self._current_live_response["text"].strip()
            
            if model_msg and self.on_turn_complete:
                self.on_turn_complete(user_msg, model_msg)

            self._current_live_response = {"text": "", "actions": []}
            self._current_user_text = ""

    async def _execute_and_respond_tool_call(self, tool_call: Any):
        """Asynchronously executes requested function call and forwards tool response to session."""
        function_responses = []
        
        for call in getattr(tool_call, 'function_calls', []):
            name = call.name
            call_id = call.id
            args = dict(call.args) if call.args else {}
            
            self.logger.info(f"🛠️ [Live Tool] Esecuzione di '{name}' (ID: {call_id})")
            
            result = None
            try:
                result = await self.on_tool_call(name, args)
            except Exception as e:
                self.logger.error(f"❌ Errore esecuzione live skill '{name}': {e}")
                result = {"success": False, "error": str(e)}
                
            self.logger.info(f"✅ [Live Tool] '{name}' completato.")
            
            f_resp = types.FunctionResponse(
                name=name,
                id=call_id,
                response={"result": result}
            )
            function_responses.append(f_resp)
            
        if function_responses:
            async with self._live_lock:
                session = self._live_session
                
            if session:
                try:
                    await session.send_tool_response(function_responses=function_responses)
                    self.logger.info("✅ [Live Tool] Risposta/e inviate.")
                except Exception as e:
                    self.logger.error(f"❌ Errore invio tool response: {e}")
