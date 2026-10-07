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
import re
from typing import Any, Dict, List, Optional, Callable, Awaitable

from robopy_controller.robot_ai.services.llm_models import LLMResponse, types

class LiveConnectionManager:
    """
    Manages WebSocket bidi-streaming connection to Google Gemini Live API.
    Used by LLMServiceNode via composition.
    """

    @staticmethod
    def _is_noise_transcription(text: str) -> bool:
        """
        [FM-VUI-033] Determina se il testo trascritto da Gemini ASR è puro rumore,
        tosse, risate o tag non-linguistici (es. <noise>, <laughter>, <cough>, <sigh>).
        """
        if not text or not text.strip():
            return True
        cleaned = re.sub(r'<[^>]+>', '', text)
        cleaned = re.sub(r'\[[^\]]+\]', '', cleaned)
        cleaned = re.sub(r'\([^)]+\)', '', cleaned)
        cleaned = re.sub(r'[\s\.,\!\?\-_:;]+', '', cleaned)
        return len(cleaned) < 2

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
        history_getter: Callable[[], List[tuple]],
        on_fallback_needed: Optional[Callable[[str], None]] = None
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
        self.on_fallback_needed = on_fallback_needed

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
        self._turn_in_progress: bool = False  # True tra activity_end e turn_complete — blocca nuovi activity_start
        self._turn_in_progress_time: float = 0.0
        self._turn_started_within_window: bool = False  # [FIX-01] Gating valutato ad ActivityStart, non ad EOS
        
        # State timings updated by LLMService
        self.last_successful_turn_time = 0.0
        self.last_wakeword_time = 0.0
        self.turns_since_wakeword = 0
        self.active_session_timeout = 8.0
        self.recent_user_transcripts = []
        self._last_mic_audio_time = 0.0

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
    def on_wakeword_detected(self):
        """Triggered when wakeword 'Marcus' is detected: resets session timers, unblocks turn gate and drains queues."""
        self.last_wakeword_time = time.time()
        self.turns_since_wakeword = 0
        self._turn_in_progress = False
        self._activity_started = False
        self._turn_in_progress_time = 0.0
        self._turn_started_within_window = True  # [FM-VUI-035b] Inizializza il gate per il nuovo turno post-wakeword
        # Drena la coda audio per fare spazio all'audio fresco post-wake word
        drained = 0
        if self._audio_in_queue is not None:
            while not self._audio_in_queue.empty():
                try:
                    self._audio_in_queue.get_nowait()
                    drained += 1
                except asyncio.QueueEmpty:
                    break
        self.logger.info(
            f"⏰ [LiveConnectionManager] Wake word 'Marcus' registrata. Sessione aperta per {self.active_session_timeout:.0f}s (Turno 0). "
            f"Blocco turno resettato, drenati {drained} chunk stantii."
        )

    async def start_loop(self):
        """Starts the persistent connection manager loop."""
        asyncio.create_task(self._live_connection_manager_loop())
        asyncio.create_task(self._keepalive_loop())  # [FIX-07] Keepalive periodico WebSocket

    async def _keepalive_loop(self):
        """[FIX-07] Sends periodic keepalive pings to prevent idle WebSocket disconnection by Google/NAT."""
        KEEPALIVE_INTERVAL = 45  # [FM-VUI-036] Ridotto da 120s a 45s per anticipare timeout NAT/Google
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            try:
                session = self._live_session
                if session and not self._turn_in_progress and not self._activity_started:
                    elapsed = time.time() - self._last_mic_audio_time if self._last_mic_audio_time > 0 else 9999.0
                    if elapsed > KEEPALIVE_INTERVAL:
                        # Invio un client_content vuoto come keepalive (leggero, senza turno)
                        await session.send(
                            input=types.LiveClientContent(
                                turns=[], turn_complete=False
                            )
                        )
                        self.logger.debug(f"💓 [FIX-07] Keepalive WebSocket inviato (idle {elapsed:.0f}s).")
            except Exception as e:
                self.logger.warning(f"💔 [FIX-07] Keepalive fallito ({e}), trigger riconnessione.")
                asyncio.create_task(self._reconnect())

    def send_audio_chunk(self, chunk: bytes):
        """Thread-safe entrypoint to schedule audio chunk enqueueing."""
        if not self._loop or not self._loop.is_running():
            return
        self._loop.call_soon_threadsafe(self._enqueue_audio, chunk)

    def _enqueue_audio(self, chunk: bytes):
        """Enqueues audio chunk with a sliding window drop policy to prevent OOM."""
        if chunk and len(chunk) > 0:
            self._last_mic_audio_time = time.time()

        # Watchdog: se turn_in_progress è attivo da oltre 8.0s senza turn_complete dal server, forza il reset e riconnette
        if self._turn_in_progress:
            turn_elapsed = time.time() - getattr(self, '_turn_in_progress_time', 0.0)
            if turn_elapsed > 8.0:
                self.logger.warning(
                    f"⏱️ [LiveConnectionManager] Watchdog turn_in_progress scaduto ({turn_elapsed:.1f}s > 8s senza turn_complete). "
                    "Sblocco forzato del gate audio e riavvio sessione zombie."
                )
                self._turn_in_progress = False
                self._activity_started = False
                self._turn_in_progress_time = 0.0
                asyncio.create_task(self._reconnect())

        # Se non c'è una sessione Live attiva o se Gemini sta elaborando un turno precedente,
        # scarta subito il chunk (evita OOM, audio stantio e inutile accumulo offline)
        if not self._live_session or self._turn_in_progress:
            return

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

                full_sys_prompt = sys_prompt
                history = self.history_getter()
                if history:
                    history_str = (
                        "\n\n[CRONOLOGIA RECENTE DELLA CONVERSAZIONE (FONDAMENTALE)]\n"
                        "Di seguito trovi gli ultimi scambi della conversazione in corso. Usali come contesto per rispondere coerentemente:\n"
                    )
                    for usr, bot in history:
                        history_str += f"Utente: {usr}\nMarcus: {bot}\n"
                    full_sys_prompt = full_sys_prompt + history_str

                modalities = ["AUDIO"]
                
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
                            self._turn_in_progress = False  # reset su ogni nuova connessione
                            self._turn_in_progress_time = 0.0
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
                            receive_iter = session.receive().__aiter__()
                            while self._live_session == session:
                                timeout = 15.0 if self._turn_in_progress else 60.0
                                try:
                                    msg = await asyncio.wait_for(receive_iter.__anext__(), timeout=timeout)
                                    await self._handle_live_message(msg)
                                except asyncio.TimeoutError:
                                    if self._turn_in_progress:
                                        self.logger.warning("⏱️ Turn response timeout (>15s). Resetting turn and reconnecting session...")
                                        self._turn_in_progress = False
                                        self._activity_started = False
                                        break
                                    else:
                                        continue
                                except StopAsyncIteration:
                                    # [FIX-08] Non distruggere il socket a fine turno (RC6).
                                    # Gemini SDK chiude l'iteratore al turn_complete, ma il WS è ancora aperto.
                                    # Creiamo un nuovo iteratore per ascoltare il prossimo turno.
                                    receive_iter = session.receive().__aiter__()
                                    continue
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
                        self._turn_in_progress = False
                        self._turn_in_progress_time = 0.0
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
                    
                    # [FIX-06] Reset token di resumption su errore di sessione scaduta
                    err_lower = err_str.lower()
                    if any(kw in err_lower for kw in ("resume", "expired", "invalid", "handle", "1008")):
                        self._resumption_token = None
                        self.logger.info("🔄 [FIX-06] Token di resumption invalidato — prossima connessione sarà un fresh handshake.")
                    
                    if fail_count == 3 or "404" in err_str or "not found" in err_str.lower():
                        if self.on_fallback_needed:
                            try:
                                self.on_fallback_needed(err_str)
                            except Exception as fb_err:
                                self.logger.error(f"Errore durante l'esecuzione del callback di fallback: {fb_err}")
                                
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
                    # Empty chunk = End of speech signal from local VAD
                    self.logger.info("🎤 [Live] Ricevuto END OF SPEECH da VUI. Eseguo controlli di gating...")

                    # NOTA ARCHITETTURALE (lesson_learned): Con VAD manuale (activity_start/end),
                    # Gemini invia input_transcription DOPO activity_end, non durante lo streaming.
                    # Quindi _current_user_text è SEMPRE vuoto qui. NON filtrare su is_noise
                    # in questo punto — causerebbe lo scarto di tutti i turni validi.
                    # Il filtro rumore è delegato al meccanismo <IGNORE_TURN> nel prompt di Gemini.

                    # [FIX-01] Il check della finestra di conversazione ora avviene al momento dell'ActivityStart
                    # (quando l'utente INIZIA a parlare), non qui all'EOS (fine frase).
                    # Se il turno era stato iniziato dentro la finestra, è sempre valido.
                    last_turn_ago = time.time() - self.last_successful_turn_time if self.last_successful_turn_time > 0 else 9999.0
                    last_wakeword_ago = time.time() - self.last_wakeword_time if self.last_wakeword_time > 0 else 9999.0

                    self.logger.info(
                        f"[LLM Gate] wakeword_ago={last_wakeword_ago:.1f}s | turn_ago={last_turn_ago:.1f}s | "
                        f"turns_since_ww={self.turns_since_wakeword} | started_in_window={self._turn_started_within_window}"
                    )

                    if not self._turn_started_within_window:
                        # [FIX-01] Turno iniziato fuori finestra: scarta senza distruggere il WebSocket
                        self.logger.info(f"🔇 [Live] Turno ignorato: activity_start era fuori dalla finestra di conversazione attiva ({self.active_session_timeout:.0f}s).")
                        if self.on_mic_mute:
                            self.on_mic_mute(True)
                        self._current_user_text = ""
                        self._current_live_response = {"text": "", "actions": []}
                        # [FIX-01] NON chiamare _reconnect() — mantieni il WebSocket aperto e pronto
                        self._activity_started = False
                        self._turn_started_within_window = False
                        continue

                    # Finestra attiva: invia activity_end e lascia decidere a Gemini via <IGNORE_TURN>
                    self.logger.info("🎤 [Live] Finestra conversazione attiva. Invio activity_end a Gemini...")
                    try:
                        await session.send_realtime_input(activity_end=types.ActivityEnd())
                        self._activity_started = False
                        self._turn_in_progress = True  # blocca nuovi activity_start fino a turn_complete
                        self._turn_in_progress_time = time.time()
                        self.logger.info("✅ [Live] activity_end inviato. In attesa di turn_complete...")
                        # Drena la coda audio stantia per evitare activity_start spurii sulla sessione
                        drained = 0
                        while not self._audio_in_queue.empty():
                            try:
                                self._audio_in_queue.get_nowait()
                                drained += 1
                            except asyncio.QueueEmpty:
                                break
                        if drained > 0:
                            self.logger.info(f"🧹 [Live] Drenati {drained} chunk audio stantii dalla coda post-EOS.")
                    except Exception as e:
                        self.logger.error(f"❌ Errore invio activity_end: {e}")
                    continue

                try:
                    if not self._activity_started:
                        if self._turn_in_progress:
                            turn_elapsed = time.time() - getattr(self, '_turn_in_progress_time', 0.0)
                            if turn_elapsed > 8.0:
                                self.logger.warning(
                                    f"⏱️ [Live] Watchdog turn_in_progress scaduto nel sender loop ({turn_elapsed:.1f}s). Sblocco forzato e riavvio sessione zombie."
                                )
                                self._turn_in_progress = False
                                self._turn_in_progress_time = 0.0
                                asyncio.create_task(self._reconnect())
                            else:
                                # Gemini sta ancora processando il turno precedente: scarta il chunk
                                self.logger.debug("⏳ [Live] activity_start bloccato: turno precedente in attesa di turn_complete.")
                                continue

                        # [FIX-01] Valuta la finestra di conversazione QUI (inizio parlato), non all'EOS
                        last_turn_ago = time.time() - self.last_successful_turn_time if self.last_successful_turn_time > 0 else 9999.0
                        last_wakeword_ago = time.time() - self.last_wakeword_time if self.last_wakeword_time > 0 else 9999.0
                        is_active = (
                            self.last_wakeword_time > 0.0 and (
                                last_wakeword_ago < self.active_session_timeout or
                                last_turn_ago < self.active_session_timeout
                            )
                        )
                        self._turn_started_within_window = is_active
                        if not is_active:
                            self.logger.debug(f"🔇 [Live] Audio scartato: fuori finestra ({last_wakeword_ago:.1f}s dal wakeword, {last_turn_ago:.1f}s dall'ultimo turno).")
                            continue

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
                    # [FIX-02] Riconnessione immediata su socket morto (no 15s di attesa)
                    asyncio.create_task(self._reconnect())
                    break
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
            self._turn_in_progress = False  # libera il blocco su interrupt
            self._turn_in_progress_time = 0.0
            self._current_user_text = ""
            self._current_live_response = {"text": "", "actions": []}
            if self.on_interrupt:
                self.on_interrupt(True)

        if getattr(sc, 'input_transcription', None) and sc.input_transcription.text:
            transcription = sc.input_transcription.text
            self._current_user_text += " " + transcription
            self.logger.info(f"🎤 [Live ASR] Trascrizione utente: {transcription}")

            # Salva la trascrizione per il controllo dei duplicati
            clean_trans = self._current_user_text.strip()
            self.recent_user_transcripts.append((clean_trans, time.time()))
            self.recent_user_transcripts = [
                (t, ts) for t, ts in self.recent_user_transcripts[-10:]
                if time.time() - ts < 15.0
            ]

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
                self.logger.info("🤫 [Live Model] Rilevato <IGNORE_TURN> (conversazione non rivolta a Marcus). Soppressione risposta vocale e svuotamento buffer speaker.")
                if self.on_interrupt:
                    try:
                        self.on_interrupt(True)
                    except Exception:
                        pass
                self._turn_in_progress = False  # sblocca nuovi activity_start per i prossimi turni
                self._turn_in_progress_time = 0.0
                self._current_user_text = ""
                self._current_live_response = {"text": "", "actions": []}
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

            # NOTA: Con response_modalities=["AUDIO"], il campo text è sempre vuoto.
            # L'output arriva esclusivamente come audio PCM via inline_data (sopra).
            # NON triggerare fallback qui — il controllo avviene solo a turn_complete.

        if getattr(sc, 'turn_complete', False):
            self._turn_in_progress = False  # sblocca nuovi activity_start
            self._turn_in_progress_time = 0.0

            user_msg = self._current_user_text.strip()
            # [FM-VUI-033] Solo se l'utente ha pronunciato parole reali aggiorniamo il timestamp della sessione attiva
            is_valid_speech = bool(user_msg) and not self._is_noise_transcription(user_msg)

            if is_valid_speech:
                self.turns_since_wakeword += 1
                self.last_successful_turn_time = time.time()
                self.logger.info(f"✅ [Live] turn_complete valido (Turno #{self.turns_since_wakeword}) — sblocco nuovo ascolto.")
            else:
                self.logger.info(f"🔇 [Live] turn_complete su rumore/silenzio ('{user_msg}') — timeout sessione non rinnovato.")
                # Azzeriamo eventuali azioni allucinate su rumore
                self._current_live_response["actions"] = []

            # Drena i chunk accumulati durante la risposta (rumore ambientale stantio)
            drained = 0
            while not self._audio_in_queue.empty():
                try:
                    self._audio_in_queue.get_nowait()
                    drained += 1
                except asyncio.QueueEmpty:
                    break
            if drained > 0:
                self.logger.info(f"🧹 [Live] Drenati {drained} chunk stantii al turn_complete.")
            fut = self._live_response_future
            if fut is not None and not fut.done():
                fut.set_result(self._current_live_response.copy())
            elif fut is not None and fut.cancelled():
                self.logger.warning("Risposta Live ricevuta su future già cancellato — scartata.")
            
            model_msg = self._current_live_response["text"].strip()

            if is_valid_speech:
                self.recent_user_transcripts.append((user_msg, time.time()))
                self.recent_user_transcripts = [
                    (t, ts) for t, ts in self.recent_user_transcripts[-10:]
                    if time.time() - ts < 15.0
                ]
            
            if self.on_turn_complete:
                self.on_turn_complete(user_msg or "[Silenzio/Rumore]", model_msg or "[SILENZIO]")

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

    def is_duplicate_text(self, text: str) -> bool:
        """Returns True if the given text matches a recently processed live bidi voice turn."""
        now = time.time()
        # Clean up old entries
        self.recent_user_transcripts = [
            (t, ts) for t, ts in self.recent_user_transcripts
            if now - ts < 15.0
        ]
        
        def normalize(s: str) -> str:
            import re
            import unicodedata
            s_norm = unicodedata.normalize('NFKD', s)
            s_clean = "".join([c for c in s_norm if not unicodedata.combining(c)])
            return re.sub(r'[^a-z0-9\s]', '', s_clean.lower()).strip()
            
        norm_input = normalize(text)
        if not norm_input:
            return False
            
        for trans, ts in self.recent_user_transcripts:
            norm_trans = normalize(trans)
            if norm_input == norm_trans or norm_input in norm_trans or norm_trans in norm_input:
                self.logger.info(f"🚫 [Live bidi check] Rilevato input duplicato: '{text}' corrispondente a trascrizione live recente '{trans}'")
                return True
                
        if self._current_user_text:
            norm_current = normalize(self._current_user_text)
            if norm_input == norm_current or norm_input in norm_current or norm_current in norm_input:
                self.logger.info(f"🚫 [Live bidi check] Rilevato input duplicato: '{text}' corrispondente a trascrizione live parziale '{self._current_user_text}'")
                return True
                
        return False

    def get_last_mic_audio_time(self) -> float:
        """Returns the timestamp of the last non-empty audio chunk received from the mic."""
        return self._last_mic_audio_time
