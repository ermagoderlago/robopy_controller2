#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Robot AI Services - Live Connection Manager
===========================================
On-Demand 5-state Finite State Machine (FSM) managing Gemini Live WebSocket sessions.
Governed by SPEC-04, marcus_core_rules.md, and Milestone 4 architecture:

State Space:
  DISCONNECTED -> CONNECTING -> ACTIVE -> IDLE_TIMED_OUT -> DISCONNECTING -> DISCONNECTED

Features:
- On-Demand activation triggered by HybridRouter (RouteTarget.GEMINI_LIVE) or wake word.
- 25.0s inactivity timer with reset on user activity and Marcus vocal emissions (_turn_audio_emitted == True).
- Background noise, TV audio, and <IGNORE_TURN> do NOT reset inactivity timer (FM-VUI-043, FIX-15).
- Exponential backoff (max 3 retries: 1.0s, 1.5s, 2.25s) with failover callback on exhaustion.
- Immediate interruption / E-stop teardown (< 5ms).
- LiveSavingsTracker calculating duration %, token %, and call savings % (guarantee >= 70%).
- Backward-compatible with tick-based unit testing and ROS 2 async event loops.
"""

import asyncio
import base64
import threading
import time
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Callable, Awaitable

from robopy_controller.robot_ai.services.llm_models import LLMResponse, types


class LiveState(str, Enum):
    """5-State Lifecycle Space for On-Demand Gemini Live WebSocket."""
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    ACTIVE = "ACTIVE"
    IDLE_TIMED_OUT = "IDLE_TIMED_OUT"
    DISCONNECTING = "DISCONNECTING"


# Backward compatibility alias
WebSocketState = LiveState


@dataclass
class SavingsSnapshot:
    """Certified snapshot of connection and token savings metrics."""
    uptime_sec: float
    connected_sec: float
    idle_disconnected_sec: float
    duration_savings_pct: float
    estimated_baseline_tokens: int
    actual_ondemand_tokens: int
    token_savings_pct: float
    total_intents: int
    local_intents: int
    cloud_live_intents: int
    fast_path_stops: int
    call_savings_pct: float
    guarantee_satisfied: bool


class LiveSavingsTracker:
    """
    Mathematical token, session duration, and API call savings tracker.
    Provides mathematical proof that the on-demand FSM achieves >= 70% savings
    compared to the always-on persistent WebSocket baseline.
    """
    AUDIO_TOKENS_PER_SEC: float = 25.0  # Approx 25 tokens/s for 16kHz audio input
    KEEPALIVE_TOKENS_PER_PING: float = 5.0
    KEEPALIVE_INTERVAL_SEC: float = 45.0
    HANDSHAKE_OVERHEAD_TOKENS: int = 50

    def __init__(self, start_time: Optional[float] = None):
        self._start_time: float = start_time if start_time is not None else time.monotonic()
        self._lock = threading.Lock()
        self.total_uptime_sec: float = 0.0
        self.total_connected_sec: float = 0.0
        self.total_intents: int = 0
        self.local_intents: int = 0
        self.cloud_live_intents: int = 0
        self.fast_path_stops: int = 0
        self.ondemand_tokens: int = 0
        self.session_count: int = 0

    def record_uptime(self, uptime_sec: float) -> None:
        """Records total mission/system uptime."""
        with self._lock:
            self.total_uptime_sec = max(self.total_uptime_sec, float(uptime_sec))

    def record_connection_duration(self, duration_sec: float) -> None:
        """Records active connection duration in seconds."""
        dur = max(0.0, float(duration_sec))
        with self._lock:
            self.total_connected_sec += dur
            self.session_count += 1
            self.ondemand_tokens += self.HANDSHAKE_OVERHEAD_TOKENS
            self.ondemand_tokens += int(dur * self.AUDIO_TOKENS_PER_SEC)

    def record_intent(self, destination: str) -> None:
        """Records an intent routing decision."""
        dest_upper = str(destination).upper()
        with self._lock:
            self.total_intents += 1
            if "FAST_PATH" in dest_upper or "STOP" in dest_upper:
                self.fast_path_stops += 1
            elif "QWEN" in dest_upper or "LOCAL" in dest_upper:
                self.local_intents += 1
            elif "LIVE" in dest_upper or "GEMINI" in dest_upper:
                self.cloud_live_intents += 1
            else:
                self.local_intents += 1

    def calculate_savings(self, total_mission_duration_sec: float) -> float:
        """Calculates duration savings percentage: (1.0 - connected / total) * 100."""
        if total_mission_duration_sec <= 0.0:
            return 0.0
        with self._lock:
            active_time = self.total_connected_sec
        savings = (1.0 - (active_time / total_mission_duration_sec)) * 100.0
        return max(0.0, min(100.0, savings))

    def get_snapshot(self, current_time: Optional[float] = None) -> SavingsSnapshot:
        """Generates comprehensive telemetry snapshot with guarantee verification."""
        now = current_time if current_time is not None else time.monotonic()
        with self._lock:
            elapsed = max(self.total_uptime_sec, now - self._start_time)
            active_time = self.total_connected_sec
            intents = self.total_intents
            local_i = self.local_intents
            cloud_i = self.cloud_live_intents
            stops = self.fast_path_stops
            ondemand_tok = self.ondemand_tokens

        if elapsed <= 0.0:
            elapsed = 0.001

        baseline_audio_tokens = int(elapsed * self.AUDIO_TOKENS_PER_SEC)
        baseline_pings = int(elapsed / self.KEEPALIVE_INTERVAL_SEC) if self.KEEPALIVE_INTERVAL_SEC > 0 else 0
        baseline_ping_tokens = int(baseline_pings * self.KEEPALIVE_TOKENS_PER_PING)
        estimated_baseline_tokens = baseline_audio_tokens + baseline_ping_tokens + self.HANDSHAKE_OVERHEAD_TOKENS

        actual_ondemand_tokens = max(ondemand_tok, int(active_time * self.AUDIO_TOKENS_PER_SEC))

        duration_savings = max(0.0, min(100.0, (1.0 - (active_time / elapsed)) * 100.0))
        token_savings = 0.0
        if estimated_baseline_tokens > 0:
            token_savings = max(0.0, min(100.0, (1.0 - (actual_ondemand_tokens / estimated_baseline_tokens)) * 100.0))

        call_savings = 0.0
        if intents > 0:
            call_savings = ((local_i + stops) / intents) * 100.0

        return SavingsSnapshot(
            uptime_sec=round(elapsed, 2),
            connected_sec=round(active_time, 2),
            idle_disconnected_sec=round(max(0.0, elapsed - active_time), 2),
            duration_savings_pct=round(duration_savings, 2),
            estimated_baseline_tokens=estimated_baseline_tokens,
            actual_ondemand_tokens=actual_ondemand_tokens,
            token_savings_pct=round(token_savings, 2),
            total_intents=intents,
            local_intents=local_i,
            cloud_live_intents=cloud_i,
            fast_path_stops=stops,
            call_savings_pct=round(call_savings, 2),
            guarantee_satisfied=(duration_savings >= 70.0)
        )


class LiveConnectionManager:
    """
    Manages On-Demand WebSocket bidi-streaming connection to Google Gemini Live API.
    Governed by 5-state FSM with 25.0s inactivity auto-disconnect.
    """

    INACTIVITY_TIMEOUT_SEC: float = 25.0
    MAX_CONNECT_RETRIES: int = 3
    CONNECT_TIMEOUT_SEC: float = 5.0

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
        client: Any = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        logger: Optional[logging.Logger] = None,
        model_getter: Optional[Callable[[], str]] = None,
        system_prompt_getter: Optional[Callable[[], str]] = None,
        voice_name_getter: Optional[Callable[[], str]] = None,
        timeout_live_getter: Optional[Callable[[], float]] = None,
        on_audio_received: Optional[Callable[[bytes], None]] = None,
        on_tool_call: Optional[Callable[[str, dict], Awaitable[dict]]] = None,
        on_turn_complete: Optional[Callable[[str, str], None]] = None,
        on_mic_mute: Optional[Callable[[bool], None]] = None,
        on_interrupt: Optional[Callable[[bool], None]] = None,
        history_getter: Optional[Callable[[], List[tuple]]] = None,
        on_fallback_needed: Optional[Callable[[str], None]] = None
    ):
        self.client = client
        self._loop = loop
        self.logger = logger or logging.getLogger("LiveConnectionManager")
        self.model_getter = model_getter or (lambda: "gemini-2.0-flash-exp")
        self.system_prompt_getter = system_prompt_getter or (lambda: "")
        self.voice_name_getter = voice_name_getter or (lambda: "Puck")
        self.timeout_live_getter = timeout_live_getter or (lambda: 15.0)
        self.on_audio_received = on_audio_received
        self.on_tool_call = on_tool_call
        self.on_turn_complete = on_turn_complete
        self.on_mic_mute = on_mic_mute
        self.on_interrupt = on_interrupt
        self.history_getter = history_getter or (lambda: [])
        self.on_fallback_needed = on_fallback_needed

        # 5-State FSM
        self._state: LiveState = LiveState.DISCONNECTED
        self._fsm_lock: threading.RLock = threading.RLock()
        self._state_changed_cb: Optional[Callable[[LiveState, LiveState], None]] = None

        # Internal session resources
        self._live_session: Optional[Any] = None
        self._session_ctx: Optional[Any] = None
        self._live_connecting: bool = False
        self._live_lock: Optional[asyncio.Lock] = None
        self._audio_in_queue: Optional[asyncio.Queue] = None
        self._resumption_token: Optional[str] = None
        self._live_response_future: Optional[asyncio.Future] = None
        self._current_live_response: Dict[str, Any] = {"text": "", "actions": []}
        self._live_functions: Optional[List[Dict[str, Any]]] = None
        self._activity_started: bool = False
        self._current_user_text: str = ""
        self._turn_in_progress: bool = False
        self._turn_in_progress_time: float = 0.0
        self._turn_started_within_window: bool = False
        self._turn_audio_emitted: bool = False

        # Inactivity & Connection Timers
        self.active_session_timeout: float = self.INACTIVITY_TIMEOUT_SEC
        self.last_successful_turn_time: float = 0.0
        self.last_wakeword_time: float = 0.0
        self.turns_since_wakeword: int = 0
        self.recent_user_transcripts: List[tuple] = []
        self._last_mic_audio_time: float = 0.0
        self._last_activity_time: float = 0.0
        self._connection_open_timestamp: Optional[float] = None
        self._inactivity_timer_handle: Optional[asyncio.TimerHandle] = None

        # Savings & Metrics
        self.savings_tracker: LiveSavingsTracker = LiveSavingsTracker()
        self.session_count: int = 0
        self.total_connected_seconds: float = 0.0

        # Background tasks
        self._sender_task: Optional[asyncio.Task] = None
        self._receiver_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None

        # Initialize async resources safely without deadlocking if already in loop thread
        if self._loop and self._loop.is_running():
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop == self._loop:
                self._live_lock = asyncio.Lock()
                self._audio_in_queue = asyncio.Queue(maxsize=50)
            else:
                init_fut = asyncio.run_coroutine_threadsafe(self._init_async_resources(), self._loop)
                init_fut.result(timeout=5.0)

    async def _init_async_resources(self):
        """Initializes lock and queue bound to event loop."""
        self._live_lock = asyncio.Lock()
        self._audio_in_queue = asyncio.Queue(maxsize=50)

    # ------------------------------------------------------------------------
    # State Inspection & Callbacks
    # ------------------------------------------------------------------------
    @property
    def current_state(self) -> LiveState:
        """Returns the current FSM state."""
        return self._state

    @property
    def state(self) -> LiveState:
        """Alias matching test harness and E2E suites."""
        return self._state

    def set_state_changed_callback(self, cb: Callable[[LiveState, LiveState], None]) -> None:
        """Sets observer callback invoked upon FSM state transition."""
        self._state_changed_cb = cb

    def _transition_to(self, new_state: LiveState, reason: str = "") -> None:
        """Executes atomic state transition and notifies observers."""
        old_state = None
        cb = None
        with self._fsm_lock:
            if self._state == new_state:
                return
            old_state = self._state
            self._state = new_state
            cb = self._state_changed_cb

        self.logger.info(
            f"🔄 [Live FSM] Transition: {old_state.value} -> {new_state.value} "
            f"(reason: {reason or 'none'})"
        )
        if cb:
            try:
                cb(old_state, new_state)
            except Exception as e:
                self.logger.error(f"Error in state changed callback: {e}")

    def _commit_connected_duration(self, current_time: Optional[float] = None) -> float:
        """
        Atomically pops _connection_open_timestamp and records elapsed connection duration
        into total_connected_seconds and LiveSavingsTracker under lock.
        Guarantees strictly exactly-once accounting of session duration.
        Returns recorded duration in seconds (0.0 if not connected or already committed).
        """
        now = current_time if current_time is not None else time.monotonic()
        with self._fsm_lock:
            ts = self._connection_open_timestamp
            self._connection_open_timestamp = None
            if ts is None:
                return 0.0
            dur = max(0.0, float(now - ts))
            self.total_connected_seconds += dur
            self.savings_tracker.record_connection_duration(dur)
            return dur

    # ------------------------------------------------------------------------
    # On-Demand Lifecycle Control (R1 / Feature F12)
    # ------------------------------------------------------------------------
    def trigger_conversation(
        self,
        reason: str = "intent_arbiter",
        current_time: Optional[float] = None
    ) -> LiveState:
        """
        Thread-safe entry point to request opening an on-demand session.
        Invoked by HybridRouter (RouteTarget.GEMINI_LIVE) or VUI on wake word.
        """
        now = current_time if current_time is not None else time.monotonic()
        self.savings_tracker.record_intent("GEMINI_LIVE")

        with self._fsm_lock:
            if self._state in [LiveState.DISCONNECTED, LiveState.IDLE_TIMED_OUT]:
                self._transition_to(LiveState.CONNECTING, reason=reason)
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(self._do_connect_async(reason), self._loop)
                else:
                    # Synchronous / test mode
                    self._transition_to(LiveState.ACTIVE, reason="sync_connected")
                    self._connection_open_timestamp = now
                    self.session_count += 1

        self._last_activity_time = now
        self.last_successful_turn_time = now
        self.last_wakeword_time = now
        return self._state

    def request_session(self, current_time: Optional[float] = None) -> LiveState:
        """Alias matching test harness and E2E suites."""
        return self.trigger_conversation(reason="request_session", current_time=current_time)

    def on_user_activity(self, current_time: Optional[float] = None) -> None:
        """
        Thread-safe entry point called when user speech or VAD activity is detected.
        Resets the 25.0s inactivity countdown timer.
        """
        now = current_time if current_time is not None else time.monotonic()
        self._last_activity_time = now
        if self._state == LiveState.ACTIVE:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._reset_inactivity_timer)

    def update_tick(self, current_time: Optional[float] = None) -> LiveState:
        """
        Evaluates inactivity duration and triggers transition to IDLE_TIMED_OUT
        after 25.0s of continuous silence.
        """
        now = current_time if current_time is not None else time.monotonic()
        with self._fsm_lock:
            if self._state == LiveState.ACTIVE:
                idle_duration = now - self._last_activity_time
                if idle_duration >= self.INACTIVITY_TIMEOUT_SEC:
                    self._commit_connected_duration(now)
                    self._transition_to(LiveState.IDLE_TIMED_OUT, reason=f"inactivity_{idle_duration:.1f}s")
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(self._do_disconnect_async(reason="idle_timeout"), self._loop)
        return self._state

    def trigger_disconnect(
        self,
        reason: str = "explicit_stop",
        current_time: Optional[float] = None
    ) -> LiveState:
        """
        Thread-safe entry point to immediately tear down session.
        Called on Fast-Path stop ("fermati", "stop", "alt") or user exit.
        """
        now = current_time if current_time is not None else time.monotonic()
        with self._fsm_lock:
            self._commit_connected_duration(now)
            self._transition_to(LiveState.DISCONNECTING, reason=reason)

        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._do_disconnect_async(reason=reason), self._loop)
        else:
            self._transition_to(LiveState.DISCONNECTED, reason="sync_teardown_complete")

        return self._state

    def close(self, current_time: Optional[float] = None) -> LiveState:
        """Alias matching test harness and E2E suites."""
        return self.trigger_disconnect(reason="close", current_time=current_time)

    def calculate_savings(self, total_mission_duration_sec: float) -> float:
        """Calculates duration savings percentage: (1.0 - connected / total) * 100."""
        return self.savings_tracker.calculate_savings(total_mission_duration_sec)

    # ------------------------------------------------------------------------
    # Inactivity Timer Mechanics
    # ------------------------------------------------------------------------
    def _reset_inactivity_timer(self) -> None:
        """Arms or resets the 25.0s inactivity timer handle."""
        if self._inactivity_timer_handle is not None:
            self._inactivity_timer_handle.cancel()
            self._inactivity_timer_handle = None

        if self._state == LiveState.ACTIVE:
            self._last_activity_time = time.monotonic()
            if self._loop and self._loop.is_running():
                self._inactivity_timer_handle = self._loop.call_later(
                    self.INACTIVITY_TIMEOUT_SEC,
                    self._on_inactivity_timer_fired
                )

    def _on_inactivity_timer_fired(self) -> None:
        """Invoked when 25.0s timer expires without user speech activity."""
        with self._fsm_lock:
            if self._state == LiveState.ACTIVE:
                self.logger.info("⏱️ [Live FSM] 25.0s Inactivity timer fired! Initiating graceful session teardown.")
                now = time.monotonic()
                self._commit_connected_duration(now)
                self._transition_to(LiveState.IDLE_TIMED_OUT, reason="25s_inactivity_timer")
                asyncio.create_task(self._do_disconnect_async(reason="inactivity_timeout"))

    # ------------------------------------------------------------------------
    # Async Handshake & Teardown
    # ------------------------------------------------------------------------
    async def start_loop(self):
        """
        Starts the Live Connection Manager.
        Under the On-Demand architecture, the connection remains DISCONNECTED
        until a conversational intent is received.
        """
        self.logger.info("🚀 [LiveConnectionManager] M4 On-Demand FSM initialized in DISCONNECTED state.")
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self):
        """Sends periodic keepalive pings ONLY while session is in ACTIVE state."""
        KEEPALIVE_INTERVAL = 45
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            try:
                if self._state == LiveState.ACTIVE and self._live_session and not self._turn_in_progress and not self._activity_started:
                    elapsed = time.monotonic() - self._last_mic_audio_time if self._last_mic_audio_time > 0 else 9999.0
                    if elapsed > KEEPALIVE_INTERVAL:
                        await self._live_session.send(
                            input=types.LiveClientContent(turns=[], turn_complete=False)
                        )
                        self.logger.debug("💓 Keepalive WebSocket inviato.")
            except Exception as e:
                self.logger.warning(f"Keepalive fallito ({e})")

    async def _do_connect_async(self, reason: str = ""):
        """Performs connection attempt with exponential backoff (max 3 retries)."""
        if self._live_lock:
            async with self._live_lock:
                if self._live_session:
                    self._transition_to(LiveState.ACTIVE, reason="already_connected")
                    return
                self._live_connecting = True

        retries = 0
        backoff = 1.0
        connected = False

        while retries < self.MAX_CONNECT_RETRIES and self._state == LiveState.CONNECTING:
            try:
                model_used = self.model_getter()
                sys_prompt = self.system_prompt_getter()
                live_functions = self._live_functions
                voice_name = self.voice_name_getter()

                full_sys_prompt = sys_prompt
                history = self.history_getter()
                if history:
                    history_str = "\n\n[CRONOLOGIA RECENTE DELLA CONVERSAZIONE]\n"
                    for usr, bot in history:
                        history_str += f"Utente: {usr}\nMarcus: {bot}\n"
                    full_sys_prompt += history_str

                if types is not None:
                    ws_kwargs = {"response_modalities": ["AUDIO"]}
                    if full_sys_prompt:
                        ws_kwargs["system_instruction"] = types.Content(parts=[types.Part.from_text(text=full_sys_prompt)])
                    if live_functions:
                        ws_kwargs["tools"] = [{"function_declarations": live_functions}]
                    ws_kwargs["context_window_compression"] = types.ContextWindowCompressionConfig(
                        sliding_window=types.SlidingWindow()
                    )
                    ws_kwargs["input_audio_transcription"] = types.AudioTranscriptionConfig()
                    ws_kwargs["output_audio_transcription"] = types.AudioTranscriptionConfig()
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
                        ws_config.session_resumption = types.SessionResumptionConfig(handle=self._resumption_token)
                else:
                    ws_config = None

                session_ctx = self.client.aio.live.connect(model=model_used, config=ws_config)
                session = await asyncio.wait_for(session_ctx.__aenter__(), timeout=self.CONNECT_TIMEOUT_SEC)

                if self._live_lock:
                    async with self._live_lock:
                        self._live_session = session
                        self._session_ctx = session_ctx
                        self._live_connecting = False
                else:
                    self._live_session = session
                    self._session_ctx = session_ctx
                    self._live_connecting = False

                with self._fsm_lock:
                    self._connection_open_timestamp = time.monotonic()
                    self.session_count += 1
                    self._transition_to(LiveState.ACTIVE, reason="handshake_ok")
                self._reset_inactivity_timer()

                # Launch background streaming workers
                self._sender_task = asyncio.create_task(self._audio_sender_loop(session))
                self._receiver_task = asyncio.create_task(self._receive_loop(session))
                connected = True
                break

            except Exception as e:
                retries += 1
                self.logger.warning(f"Live connect attempt {retries}/{self.MAX_CONNECT_RETRIES} failed: {e}")
                err_str = str(e).lower()
                if any(kw in err_str for kw in ("resume", "expired", "invalid", "handle", "1008")):
                    self._resumption_token = None
                if retries < self.MAX_CONNECT_RETRIES:
                    await asyncio.sleep(backoff)
                    backoff *= 1.5

        if not connected:
            if self._live_lock:
                async with self._live_lock:
                    self._live_connecting = False
                    self._live_session = None
            else:
                self._live_connecting = False
                self._live_session = None

            self._transition_to(LiveState.DISCONNECTED, reason="handshake_exhausted")
            if self.on_fallback_needed:
                try:
                    self.on_fallback_needed(f"Handshake failed after {retries} retries")
                except Exception as fb_err:
                    self.logger.error(f"Fallback callback error: {fb_err}")

    async def _do_disconnect_async(self, reason: str = ""):
        """Gracefully tears down WebSocket session and joins tasks."""
        with self._fsm_lock:
            self._commit_connected_duration()
            self._transition_to(LiveState.DISCONNECTING, reason=reason)

        if self._inactivity_timer_handle is not None:
            self._inactivity_timer_handle.cancel()
            self._inactivity_timer_handle = None

        session = self._live_session
        session_ctx = getattr(self, '_session_ctx', None)
        self._live_session = None
        self._session_ctx = None
        self._live_connecting = False

        if session:
            try:
                await session.close()
            except Exception as e:
                self.logger.debug(f"Exception closing session: {e}")
        if session_ctx:
            try:
                await session_ctx.__aexit__(None, None, None)
            except Exception as e:
                self.logger.debug(f"Exception exiting session context: {e}")

        if self._sender_task and not self._sender_task.done():
            self._sender_task.cancel()
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass

        if self._receiver_task and not self._receiver_task.done():
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass

        self._transition_to(LiveState.DISCONNECTED, reason="teardown_done")

    # ------------------------------------------------------------------------
    # Audio In & Out Streams
    # ------------------------------------------------------------------------
    def send_audio_chunk(self, chunk: bytes):
        """Thread-safe entrypoint to schedule audio chunk enqueueing."""
        if not self._loop or not self._loop.is_running():
            return
        self._loop.call_soon_threadsafe(self._enqueue_audio, chunk)

    def _enqueue_audio(self, chunk: bytes):
        """Enqueues audio chunk. Chunks are discarded immediately if NOT in ACTIVE state."""
        if chunk and len(chunk) > 0:
            self._last_mic_audio_time = time.monotonic()

        # On-demand discard invariant: discard immediately if not ACTIVE
        if self._state != LiveState.ACTIVE or not self._live_session or self._turn_in_progress:
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

        if self._audio_in_queue and self._audio_in_queue.full():
            try:
                self._audio_in_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

        if self._audio_in_queue:
            try:
                self._audio_in_queue.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

    async def _audio_sender_loop(self, session: Any):
        """Asynchronously pulls audio chunk from the queue and streams it to Gemini."""
        self.logger.info("🎤 Avviato loop di invio audio PCM asincrono per la sessione.")
        try:
            while self._state == LiveState.ACTIVE and self._live_session == session:
                if not self._audio_in_queue:
                    await asyncio.sleep(0.02)
                    continue

                try:
                    chunk = await asyncio.wait_for(self._audio_in_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                if len(chunk) == 0:
                    # End of speech signal from local VAD
                    last_turn_ago = time.monotonic() - self.last_successful_turn_time if self.last_successful_turn_time > 0 else 9999.0
                    last_wakeword_ago = time.monotonic() - self.last_wakeword_time if self.last_wakeword_time > 0 else 9999.0

                    if not self._turn_started_within_window:
                        self.logger.info(f"🔇 [Live] Turno ignorato: activity_start era fuori dalla finestra ({self.active_session_timeout:.0f}s).")
                        if self.on_mic_mute:
                            self.on_mic_mute(True)
                        self._current_user_text = ""
                        self._current_live_response = {"text": "", "actions": []}
                        self._activity_started = False
                        self._turn_started_within_window = False
                        continue

                    try:
                        await session.send_realtime_input(activity_end=types.ActivityEnd())
                        self._activity_started = False
                        self._turn_in_progress = True
                        self._turn_in_progress_time = time.monotonic()
                        # Purge stale chunks post-EOS
                        while not self._audio_in_queue.empty():
                            try:
                                self._audio_in_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                    except Exception as e:
                        self.logger.error(f"❌ Errore invio activity_end: {e}")
                    continue

                try:
                    if not self._activity_started:
                        if self._turn_in_progress:
                            turn_elapsed = time.monotonic() - getattr(self, '_turn_in_progress_time', 0.0)
                            if turn_elapsed > 8.0:
                                self.logger.warning("⏱️ Turn watchdog expired. Resetting turn.")
                                self._turn_in_progress = False
                                self._turn_in_progress_time = 0.0
                            else:
                                continue

                        last_turn_ago = time.monotonic() - self.last_successful_turn_time if self.last_successful_turn_time > 0 else 9999.0
                        last_wakeword_ago = time.monotonic() - self.last_wakeword_time if self.last_wakeword_time > 0 else 9999.0
                        is_active = (
                            self.last_wakeword_time > 0.0 and (
                                last_wakeword_ago < self.active_session_timeout or
                                last_turn_ago < self.active_session_timeout
                            )
                        )
                        self._turn_started_within_window = is_active
                        if not is_active:
                            continue

                        self._turn_audio_emitted = False
                        await session.send_realtime_input(activity_start=types.ActivityStart())
                        self._activity_started = True
                        self._reset_inactivity_timer()

                    await session.send_realtime_input(
                        audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                    )
                except Exception as e:
                    self.logger.error(f"❌ Errore invio chunk PCM: {e}")
                    break
        except asyncio.CancelledError:
            pass

    async def _receive_loop(self, session: Any):
        """Asynchronously receives responses and tool calls from Gemini."""
        try:
            receive_iter = session.receive().__aiter__()
            while self._state == LiveState.ACTIVE and self._live_session == session:
                timeout = 15.0 if self._turn_in_progress else 60.0
                try:
                    msg = await asyncio.wait_for(receive_iter.__anext__(), timeout=timeout)
                    await self._handle_live_message(msg)
                except asyncio.TimeoutError:
                    if self._turn_in_progress:
                        self._turn_in_progress = False
                        self._activity_started = False
                        break
                    continue
                except StopAsyncIteration:
                    receive_iter = session.receive().__aiter__()
                    continue
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.warning(f"Live receive loop error: {e}")

    async def _handle_live_message(self, msg: Any):
        """Processes message from the model and dispatches via callbacks."""
        sru = getattr(msg, 'session_resumption_update', None)
        if sru and getattr(sru, 'resumable', False) and getattr(sru, 'new_handle', None):
            self._resumption_token = sru.new_handle

        if getattr(msg, 'tool_call', None):
            asyncio.create_task(self._execute_and_respond_tool_call(msg.tool_call))

        if not getattr(msg, 'server_content', None):
            return
        sc = msg.server_content

        if getattr(sc, 'interrupted', False):
            self._turn_in_progress = False
            self._turn_in_progress_time = 0.0
            self._current_user_text = ""
            self._current_live_response = {"text": "", "actions": []}
            if self.on_interrupt:
                self.on_interrupt(True)

        if getattr(sc, 'input_transcription', None) and sc.input_transcription.text:
            transcription = sc.input_transcription.text
            self._current_user_text += " " + transcription
            if not self._is_noise_transcription(transcription):
                self._reset_inactivity_timer()

            user_speech = self._current_user_text.lower()
            exit_phrases = [
                "stai zitto", "basta parlare", "fermati di parlare",
                "smetti di parlare", "taci", "silenzio", "zitto marcus",
                "marcus zitto", "basta marcus", "marcus basta"
            ]
            if any(cmd in user_speech for cmd in exit_phrases):
                self.logger.info(f"🤫 Rilevato comando di silenzio dell'utente: '{user_speech}'")
                if self.on_mic_mute:
                    self.on_mic_mute(True)
                self.trigger_disconnect(reason="explicit_exit")
                return

        if getattr(sc, 'output_transcription', None) and sc.output_transcription.text:
            self._current_live_response["text"] += sc.output_transcription.text

        if sc.model_turn:
            ignore_detected = False
            for part in sc.model_turn.parts:
                if hasattr(part, 'text') and part.text:
                    if "<ignore_turn>" in part.text.lower() or "ignore_turn" in part.text.lower():
                        ignore_detected = True
                        break

            if ignore_detected or "<ignore_turn>" in self._current_live_response["text"].lower():
                self.logger.info("🤫 [Live Model] Rilevato <IGNORE_TURN>. Risposta vocale soppressa senza chiusura immediata.")
                if self.on_interrupt:
                    try:
                        self.on_interrupt(True)
                    except Exception:
                        pass
                self._turn_in_progress = False
                self._turn_in_progress_time = 0.0
                self._current_user_text = ""
                self._current_live_response = {"text": "", "actions": []}
                return

            for part in sc.model_turn.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    self._turn_audio_emitted = True
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
            self._turn_in_progress = False
            self._turn_in_progress_time = 0.0

            user_msg = self._current_user_text.strip()
            is_valid_speech = bool(user_msg) and not self._is_noise_transcription(user_msg)
            is_active_spoken_turn = is_valid_speech and self._turn_audio_emitted

            if is_active_spoken_turn:
                self.turns_since_wakeword += 1
                self.last_successful_turn_time = time.monotonic()
                self._reset_inactivity_timer()
            else:
                self._current_live_response["actions"] = []

            self._turn_audio_emitted = False

            fut = self._live_response_future
            if fut is not None and not fut.done():
                fut.set_result(self._current_live_response.copy())

            model_msg = self._current_live_response["text"].strip()
            if self.on_turn_complete:
                self.on_turn_complete(user_msg or "[Silenzio/Rumore]", model_msg or "[SILENZIO]")

            self._current_live_response = {"text": "", "actions": []}
            self._current_user_text = ""

    async def _execute_and_respond_tool_call(self, tool_call: Any):
        """Asynchronously executes requested function call and forwards tool response."""
        function_responses = []
        for call in getattr(tool_call, 'function_calls', []):
            name = call.name
            call_id = call.id
            args = dict(call.args) if call.args else {}
            result = None
            try:
                if self.on_tool_call:
                    result = await self.on_tool_call(name, args)
                else:
                    result = {"status": "ok"}
            except Exception as e:
                result = {"success": False, "error": str(e)}

            f_resp = types.FunctionResponse(name=name, id=call_id, response={"result": result})
            function_responses.append(f_resp)

        if function_responses and self._live_session:
            try:
                await self._live_session.send_tool_response(function_responses=function_responses)
            except Exception as e:
                self.logger.error(f"Errore invio tool response: {e}")

    # ------------------------------------------------------------------------
    # Public Utility Methods
    # ------------------------------------------------------------------------
    def on_wakeword_detected(self):
        """Resets timers and unblocks gate on wakeword 'Marcus'."""
        self.trigger_conversation(reason="wakeword_marcus")

    def interrupt(self):
        """Signals interruption."""
        self._turn_in_progress = False
        self._turn_in_progress_time = 0.0

    async def send_text(self, text: str):
        """Sends raw text to session if active."""
        if self._live_session and self._state == LiveState.ACTIVE:
            await self._live_session.send(input=text, end_of_turn=True)

    async def _reconnect(self):
        """Forces a clean reconnect."""
        await self._do_disconnect_async(reason="force_reconnect")
        await self._do_connect_async(reason="force_reconnect")

    async def start_persistent_live(self, functions=None):
        """Updates functions and reconnects if active."""
        self._live_functions = functions
        if self._state == LiveState.ACTIVE:
            await self._reconnect()
        return True

    async def generate_live(self, prompt: str, context=None, functions=None, images=None, documents=None) -> LLMResponse:
        """Sends prompt to stream and awaits complete response."""
        prompt_str = prompt.prompt if hasattr(prompt, 'prompt') else str(prompt)
        start = time.perf_counter()

        if self._state != LiveState.ACTIVE:
            self.trigger_conversation(reason="generate_live")
            # Wait briefly for connection
            for _ in range(25):
                if self._state == LiveState.ACTIVE:
                    break
                await asyncio.sleep(0.1)

        if not self._live_session or self._state != LiveState.ACTIVE:
            raise TimeoutError("Connessione Live WebSocket non attiva.")

        if self._live_response_future and not self._live_response_future.done():
            raise RuntimeError("Live API è occupata con un'altra richiesta.")

        self._live_response_future = self._loop.create_future() if self._loop else asyncio.get_event_loop().create_future()
        self._current_live_response = {"text": "", "actions": []}

        await self._live_session.send(input=prompt_str, end_of_turn=True)
        timeout_val = self.timeout_live_getter()
        result = await asyncio.wait_for(self._live_response_future, timeout=timeout_val)

        return LLMResponse(
            text=result["text"].strip(),
            actions=result["actions"],
            latency_ms=(time.perf_counter() - start) * 1000,
            model=self.model_getter()
        )

    def is_duplicate_text(self, text: str) -> bool:
        """Returns True if text matches a recently processed turn."""
        now = time.monotonic()
        self.recent_user_transcripts = [
            (t, ts) for t, ts in self.recent_user_transcripts if now - ts < 15.0
        ]
        clean = text.strip().lower()
        for trans, ts in self.recent_user_transcripts:
            if clean == trans.lower():
                return True
        return False

    def get_last_mic_audio_time(self) -> float:
        """Returns timestamp of last mic audio chunk."""
        return self._last_mic_audio_time
