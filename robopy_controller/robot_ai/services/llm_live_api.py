#!/usr/bin/env python3
"""
Robot AI Services - Live API Mixin
=====================================
Gestione della connessione persistente Live (WebSocket bidi-streaming)
con Google Gemini, incluso audio streaming e session resumption.

Questa classe è un Mixin da usare con LLMServiceNode:
    class LLMServiceNode(LiveAPIMixin, Node): ...

Il mixin si aspetta che il nodo host fornisca:
  - self._client, self._loop, self._live_lock, self._breaker
  - self._cfg_lock, self._live_model, self._system_prompt
  - self._timeout_live
  - self.get_logger(), self.pub_audio_chunk, self.pub_text_response
  - self._assert_live_lock()

Estratto da llm_service.py per migliorare la leggibilità e separazione dei concern.
"""

import asyncio
import base64
import time
from typing import Any, Dict, List, Optional

import rclpy

from .llm_models import LLMResponse, types
from robopy_controller.msg import AudioData


class LiveAPIMixin:
    """
    Mixin che incapsula tutta la logica della Live API (WebSocket bidi-streaming).
    """

    # -----------------------------------------------------------------------
    # Inizializzazione stato Live (chiamata dal __init__ del nodo host)
    # -----------------------------------------------------------------------
    def _init_live_state(self):
        """Inizializza tutte le variabili di stato per la Live API."""
        self._live_session:          Optional[Any]            = None
        self._live_connecting:       bool                     = False
        self._live_lock:             Optional[asyncio.Lock]   = None   # init lazy
        self._resumption_token:      Optional[str]            = None
        self._live_response_future:  Optional[asyncio.Future] = None
        self._current_live_response: Dict[str, Any]           = {"text": "", "actions": []}

    # -----------------------------------------------------------------------
    # Bridge methods per l'orchestratore
    # -----------------------------------------------------------------------
    async def start_persistent_live(self):
        """
        Metodo richiesto dall'orchestratore per segnalare l'avvio della sessione.
        Il loop di connessione è già gestito internamente.
        """
        self.get_logger().info("Persistent Live session enabled.")
        return True

    async def generate_live(self, prompt: str, context=None, functions=None, images=None, documents=None):
        """
        Bridge verso _async_generate_live con supporto cross-loop.
        """
        coro = self._async_generate_live(prompt, context, functions, images, documents)
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
    # Connection Manager — mantiene la connessione WebSocket persistente
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
                    
                ws_config = types.LiveConnectConfig(**ws_kwargs)

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

                    self.get_logger().info("Live API connessa con successo.")
                    backoff = 2.0      # reset backoff on success
                    fail_count = 0

                    async for msg in session.receive():
                        await self._handle_live_message(msg)

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

    # -----------------------------------------------------------------------
    # Audio handler
    # -----------------------------------------------------------------------
    async def _async_audio_handler(self, msg: AudioData):
        """Invia un chunk audio PCM alla sessione Live attiva."""
        self._assert_live_lock()

        async with self._live_lock:
            session = self._live_session

        if not session:
            return

        try:
            await session.send_realtime_input(
                media=types.Blob(
                    data=msg.data,
                    mime_type="audio/pcm;rate=16000"
                )
            )
        except Exception as e:
            self.get_logger().debug(f"Errore invio audio stream: {e}")

    # -----------------------------------------------------------------------
    # Gestione messaggi Live in ingresso
    # -----------------------------------------------------------------------
    async def _handle_live_message(self, msg):
        """
        Gestisce un messaggio in arrivo dalla Live API.
        Pubblica chunk audio, accumula testo/actions, segnala turn_complete.
        """
        # Handle session resumption update (top-level, outside server_content)
        sru = getattr(msg, 'session_resumption_update', None)
        if sru and getattr(sru, 'resumable', False) and getattr(sru, 'new_handle', None):
            self._resumption_token = sru.new_handle
            self.get_logger().info("Ricevuto nuovo token di ripresa sessione The handle will be retained.")

        if not msg.server_content:
            return
        sc = msg.server_content

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

    # -----------------------------------------------------------------------
    # Generate Live (testo → Live API → risposta completa)
    # -----------------------------------------------------------------------
    async def _async_generate_live(self, prompt: str, context=None, functions=None, images=None, documents=None) -> LLMResponse:
        """
        Invia un prompt testuale alla Live API e attende la risposta completa.
        Supporta anche immagini, documenti e funzioni.
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
                        self._current_live_response = {"text": "", "actions": []}
                        break
                await asyncio.sleep(0.2)

            if not session:
                raise TimeoutError("Connessione Live WebSocket non attiva.")

            try:
                content_parts = [types.Part.from_text(text=prompt)]
                if images or documents:
                    if images:
                        for img in images:
                            data = base64.b64decode(img) if isinstance(img, str) else bytes(img)
                            content_parts.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
                    if documents:
                        for doc in documents:
                            # doc expected as {"data": base64 or bytes, "mime_type": "..."}
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

    # -----------------------------------------------------------------------
    # Reconnect Live
    # -----------------------------------------------------------------------
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
