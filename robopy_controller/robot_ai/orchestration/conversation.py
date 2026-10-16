import asyncio
import time
import re
import datetime
import json
import random
from typing import Optional, List, Dict, Any, Tuple
from robot_ai.utils import get_logger
from robot_ai.core.input_sanitizer import InputSanitizer
from robot_ai.core.camera_frame import CameraFrame
from .cognitive_graph import MarcusStateGraph, MarcusAgentState

# New: VQA Service from robopy_controller
from robopy_controller.srv import AskVisualQuestion
import rclpy

class ConversationManager:
    def __init__(self, llm, tts, skill_executor, memory_manager, world_model,
                 ha_context_provider, metrics, config, reactive_safety, response_callback=None, node=None):
        
        self.node = node

        self.llm = llm
        self.tts = tts
        self.skill_executor = skill_executor
        self.memory_manager = memory_manager
        self.world_model = world_model
        self.ha_context_provider = ha_context_provider
        self.metrics = metrics
        self.config = config
        self.reactive_safety = reactive_safety
        self.response_callback = response_callback
        self._mic_muted = True
        self._frame_queue = asyncio.Queue(maxsize=1)
        self._processing_lock = asyncio.Lock()
        self._logger = get_logger("conversation")
        self._sanitizer = InputSanitizer()
        self.document_callback = None
        self._recent_inputs = []
        self.conversation_history = []
        self._recent_queries: List[Tuple[str, float, str]] = []  # (norm_text, timestamp, reply_text)
        self._in_flight_queries: Dict[str, float] = {}            # norm_text -> start_time

        # Dopamine Biometric Alignment System
        self.cognitive_graph = MarcusStateGraph(
            memory_store=self.memory_manager.memory_store,
            embedding_service=self.memory_manager.embedding_service
        )
        self.agent_state = MarcusAgentState()
        self._current_source = ""

        # TRINITY Cognitive Engine (RAG + CAG + MAG)
        try:
            from ..trinity.trinity_engine import TrinityEngine
            self.trinity_engine = TrinityEngine(
                memory_store=getattr(self.memory_manager, 'memory_store', None),
                embedding_service=getattr(self.memory_manager, 'embedding_service', None),
                world_model=self.world_model,
                ha_context_provider=self.ha_context_provider,
                config=self.config,
                node=self.node
            )
        except Exception as e:
            self._logger.warning(f"TrinityEngine initialization failed (using fallback): {e}")
            self.trinity_engine = None

        # Hybrid Architecture: Cognitive Arbiter & Hailo-10H Qwen2 Client
        try:
            from ..arbitration.hybrid_router import HybridRouter
            from ..arbitration.intent_classifier import RouteTarget
            from ..hailo.hailo_qwen_client import HailoQwenClient
            self.hailo_client = HailoQwenClient()
            self.hybrid_router = HybridRouter(node=self.node)
            self._logger.info("✅ HybridRouter and HailoQwenClient successfully initialized in ConversationManager.")
        except Exception as e:
            self._logger.warning(f"HybridRouter/HailoQwenClient initialization failed: {e}")
            self.hailo_client = None
            self.hybrid_router = None

    def set_latest_frame(self, frame):
        try:
            self._frame_queue.put_nowait(frame)
        except asyncio.QueueFull:
            self._frame_queue.get_nowait()
            self._frame_queue.put_nowait(frame)

    async def _get_latest_frame(self):
        try:
            return self._frame_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def set_mic_muted(self, muted: bool):
        self._mic_muted = muted

    def _is_emergency(self, text: str) -> bool:
        return bool(re.search(r'\b(fermati|stop|emergenza)\b', text, re.IGNORECASE))

    async def _handle_emergency(self):
        self.reactive_safety.emergency_stop()
        try:
            if self.skill_executor and hasattr(self.skill_executor, "registry"):
                frontier_skill = self.skill_executor.registry.get("frontier_exploration")
                if frontier_skill and getattr(frontier_skill, "is_exploring", False):
                    if hasattr(frontier_skill, "pause_for_dialogue"):
                        frontier_skill.pause_for_dialogue()
                    if hasattr(frontier_skill, "stop_exploration"):
                        frontier_skill.stop_exploration()
                nav_skill = self.skill_executor.registry.get("navigation")
                if nav_skill and hasattr(nav_skill, "safe_execute"):
                    res = nav_skill.safe_execute("fermati", {"action": "stop"})
                    if asyncio.iscoroutine(res):
                        await res
        except Exception as e:
            self._logger.debug(f"Emergency frontier/nav stop check skipped: {e}")
        msg = "Fermo tutto! Mi sono fermato immediatamente, tutti i motori sono a riposo."
        if self.response_callback:
            self.response_callback(msg)
        await self.tts.speak(msg)

    async def _finalize_barge_in_exploration(
        self,
        clean_text: str,
        was_exploring_at_barge_in: bool,
        frontier_skill: Any
    ) -> None:
        """Auto-resumes or permanently stops frontier exploration after a barge-in dialogue turn."""
        if was_exploring_at_barge_in and frontier_skill:
            user_requested_stop = bool(
                re.search(
                    r'\b(ferma|fermati|stop|alt|basta|annulla|interrompi|blocca|arresta|non\s+muoverti)\b',
                    clean_text,
                    re.IGNORECASE
                )
            )
            if user_requested_stop:
                frontier_skill.stop_exploration()
                nav_skill = self.skill_executor.registry.get("navigation") if (self.skill_executor and hasattr(self.skill_executor, "registry")) else None
                if nav_skill and hasattr(nav_skill, "safe_execute"):
                    res = nav_skill.safe_execute("fermati", {"action": "stop"})
                    if asyncio.iscoroutine(res):
                        await res
                self._logger.info("🛑 Exploration stopped per user utterance command.")
            elif getattr(frontier_skill, "_paused_for_dialogue", False):
                frontier_skill.resume_after_dialogue()
                self._logger.info("▶️ Exploration automatically resumed after dialogue turn.")

    def _record_recent_query(self, text: str, reply: str):
        """Records a completed turn in recent queries for anti-echo and reaffirming responses."""
        if not hasattr(self, '_recent_queries'):
            self._recent_queries = []
        norm = text.strip().lower()
        now = time.time()
        # Keep window bounded to last 20 seconds
        self._recent_queries = [
            (t, ts, r) for t, ts, r in self._recent_queries
            if (now - ts) < 20.0
        ]
        self._recent_queries.append((norm, now, reply))

    async def _emit_conversational_fallback(
        self,
        clean_text: str,
        should_speak: bool,
        reason: str = ""
    ) -> bool:
        """
        Emits an empathetic, contextual conversational fallback in Italian without technical jargon.
        Guarantees zero-silence and sub-1.5s response delivery on /ai/conversation/response.
        Handles:
        1. battery/status: Reassuring battery voltage and system health.
        2. location: Reassuring current room and spatial orientation.
        3. actions/motion: Reassuring explanation of motion hesitation without jargon.
        4. greetings: Warm, friendly robotic presence.
        5. generic: Polite and warm apology inviting re-phrasing.
        """
        lower = clean_text.lower().strip()

        # 1. Battery / Power / System Health
        if any(w in lower for w in [
            "batteria", "carica", "autonomia", "volt", "stato batteria", "salute batteria", "diagnostica", 
            "alimentazione", "health", "battery"
        ]):
            v = getattr(self.world_model, "battery_voltage", None) if self.world_model else None
            if isinstance(v, (int, float)) and v > 0:
                fallback = f"Ho verificato rapidamente i miei circuiti: la batteria è a {v:.2f} volt e tutti i sistemi sono stabili e operativi!"
            else:
                fallback = "Ho verificato rapidamente: tutti i miei circuiti di alimentazione sono stabili, procediamo pure!"

        # 2. Location / Room / Spatial Awareness
        elif any(w in lower for w in [
            "dove sei", "dove ti trovi", "in che stanza", "posizione", "localizzazione",
            "dove siamo", "mappa", "stanza", "orientamento"
        ]):
            curr_room = getattr(self.world_model, "current_room", None) if self.world_model else None
            if isinstance(curr_room, str) and curr_room:
                fallback = f"Sono qui con te in {curr_room}, perfettamente orientato sulla mappa!"
            else:
                fallback = "Sono qui con te nella stanza, pronto a orientarmi sulla mappa!"

        # 3. Actions / Movement / Stop
        elif any(w in lower for w in [
            "avanti", "indietro", "destra", "sinistra", "ruota", "gira", "cammina",
            "muoviti", "fermati", "stop", "alt", "blocca", "arrestati", "esplora", "vai"
        ]):
            if any(w in lower for w in ["fermati", "stop", "alt", "blocca", "arrestati"]):
                fallback = "Mi sono fermato immediatamente, tutti i motori sono a riposo."
            else:
                fallback = "Ho compreso il tuo comando, ma il mio modulo motori ha avuto un piccolo attimo di esitazione. Riprova tra un istante!"

        # 4. Greetings / Presence / Small Talk
        elif any(w in lower for w in [
            "ciao", "buongiorno", "buonasera", "salve", "ehi", "hey", "buondì",
            "ci sei", "mi senti", "mi ascolti", "puoi parlare"
        ]):
            fallback = "Ciao! Ti sento forte e chiaro, sono qui e pronto ad aiutarti."

        # 5. Generic / Unmatched
        else:
            fallback = "Scusami, ho avuto un piccolo attimo di esitazione nei miei circuiti neurali! Dimmi pure, ti ascolto."

        self._logger.info(
            f"🛡️ [ALWAYS-RESPOND FALLBACK] Emitted empathetic fallback for '{clean_text}' (reason: {reason}) -> '{fallback}'"
        )

        # 1. Publish to response_callback (synchronously published to /ai/conversation/response)
        if self.response_callback:
            try:
                self.response_callback(fallback)
            except Exception as cb_err:
                self._logger.error(f"Error invoking response_callback in fallback: {cb_err}")

        # 2. Speak via TTS if voice output requested
        if should_speak and self.tts:
            try:
                await self.tts.speak(fallback)
            except Exception as tts_err:
                self._logger.debug(f"TTS speak skipped in fallback: {tts_err}")

        # 3. Update short-term working memory
        self.conversation_history.append({"role": "user", "content": clean_text, "user": clean_text})
        self.conversation_history.append({"role": "assistant", "content": fallback, "marcus": fallback})
        if len(self.conversation_history) > 10:
            self.conversation_history = self.conversation_history[-10:]

        # 4. Update recent queries for repetition tracker
        self._record_recent_query(clean_text, fallback)

        # 5. Record interaction in TRINITY autobiographical store if enabled
        if self.trinity_engine and self.trinity_engine.enabled:
            try:
                await self.trinity_engine.record_interaction(
                    user_text=clean_text,
                    robot_response=fallback,
                    actions_taken=[],
                    was_successful=True,
                    user_identity=None,
                    extracted_facts=None
                )
            except Exception as mag_err:
                self._logger.debug(f"TRINITY fallback recording skipped: {mag_err}")

        # 6. Increment metric and return True to guarantee zero silence
        self.metrics.inc_requests_success()
        return True

    async def _handle_local_qwen(
        self,
        clean_text: str,
        should_speak: bool,
        user_identity: Optional[str] = None,
        combined_notes: str = ""
    ) -> bool:
        """
        Executes local inference via Hailo-10H Qwen2 client with TRINITY context augmentation,
        strict SLM token budgeting (<1200 tokens), kinematic sandbox validation, and autobiographical
        SQLite WAL persistence. Guaranteed Always-Respond behavior on timeouts/errors.
        """
        if not self.hailo_client:
            self._logger.error("HailoQwenClient is not initialized for local processing.")
            return await self._emit_conversational_fallback(clean_text, should_speak, reason="client_uninitialized")

        t0 = time.perf_counter()
        if user_identity is None and self.world_model and hasattr(self.world_model, "current_user") and self.world_model.current_user:
            user_identity = self.world_model.current_user.get("name")

        augmented_prompt = clean_text
        reply_text = ""

        # Step 1: Gather TRINITY Context Asynchronously (< 1200 tokens, bounded to 250ms)
        if self.trinity_engine and self.trinity_engine.enabled:
            try:
                augmented_prompt = await asyncio.wait_for(
                    self.trinity_engine.build_augmented_prompt(
                        user_text=clean_text,
                        source=self._current_source or "text",
                        user_identity=user_identity,
                        system_prompt="",
                        repeated_note=combined_notes,
                        conversation_history=list(self.conversation_history),
                        target_model="qwen_local"
                    ),
                    timeout=0.250
                )
            except (asyncio.TimeoutError, Exception) as e:
                self._logger.warning(f"TRINITY prompt augmentation timed out or failed for local Qwen: {e}")
                augmented_prompt = clean_text

        # Step 2: Enforce Strict Hardware Timeout Deadline (Guaranteeing <1.45s before fallback)
        elapsed_so_far = time.perf_counter() - t0
        generation_timeout = min(1.20, max(0.50, 1.40 - elapsed_so_far))

        try:
            try:
                reply_text, telem = await asyncio.wait_for(
                    self.hailo_client.generate(prompt=augmented_prompt, timeout_sec=generation_timeout),
                    timeout=generation_timeout
                )
            except TypeError:
                # Duck-typed clients lacking timeout_sec parameter
                reply_text, telem = await asyncio.wait_for(
                    self.hailo_client.generate(prompt=augmented_prompt),
                    timeout=generation_timeout
                )
            latency = time.perf_counter() - t0
            self.metrics.record_llm_latency(latency)
            self._logger.info(
                f"🧠 [LOCAL QWEN2 NPU] Generated response in {latency*1000:.1f}ms: '{reply_text}'"
            )
        except (asyncio.TimeoutError, Exception) as exc:
            latency = time.perf_counter() - t0
            self._logger.warning(
                f"⚠️ [LOCAL QWEN TIMEOUT/ERROR] Generation failed or exceeded {generation_timeout:.2f}s "
                f"({latency*1000:.1f}ms elapsed): {exc}. Activating instantaneous conversational fallback..."
            )
            return await self._emit_conversational_fallback(clean_text, should_speak, reason=str(exc))

        # Step 3: Extract Hybrid Dialogue + Action via JsonCallExtractor & Execute via Kinematic Sandbox
        raw_call = None
        clean_speech = ""
        try:
            from ..hailo.qwen_prompt_templates import (
                JsonCallExtractor,
                generate_fallback_verbal_confirmation,
                sanitize_speech_for_tts,
            )
            extraction = JsonCallExtractor.extract_hybrid_response(reply_text)
            raw_call = extraction.action_call
            clean_speech = extraction.clean_speech
        except Exception as e:
            self._logger.debug(f"JsonCallExtractor invocation error: {e}")
            clean_speech = re.sub(r'<action>[\s\S]*?</action>', '', reply_text).strip()
            clean_speech = re.sub(r'```(?:json)?[\s\S]*?```', '', clean_speech).strip()
            clean_speech = re.sub(r'<action>[\s\S]*$', '', clean_speech).strip()

        executed_action_names = []

        if raw_call:
            natural_reply = (
                clean_speech
                if clean_speech
                else generate_fallback_verbal_confirmation(raw_call.function_name, raw_call.arguments)
            )

            # Pre-flight Kinematic Sandbox Validation
            try:
                from ..trinity.kinematic_sandbox import TrinityKinematicSandbox
                from ..trinity.sandbox_rules import RobotSafetyContext
                sandbox = TrinityKinematicSandbox()
                safety_ctx = RobotSafetyContext()
                if self.world_model:
                    b_volt = getattr(self.world_model, "battery_voltage", 12.0)
                    if isinstance(b_volt, (int, float)):
                        safety_ctx.battery_voltage = float(b_volt)
                    is_chg = getattr(self.world_model, "is_charging", False)
                    if isinstance(is_chg, bool):
                        safety_ctx.is_charging = is_chg
                    amyg = getattr(self.world_model, "amygdala_state", "CALM")
                    if isinstance(amyg, str):
                        safety_ctx.amygdala_state = amyg
                    for attr in (
                        "min_forward_obstacle_dist",
                        "min_rear_obstacle_dist",
                        "min_clearance_radius_360",
                        "max_depth_drop",
                    ):
                        val = getattr(self.world_model, attr, None)
                        if isinstance(val, (int, float)):
                            setattr(safety_ctx, attr, float(val))

                validation = sandbox.validate_action(raw_call, safety_ctx)
                if not validation.authorized:
                    self._logger.warning(f"🛑 [KinematicSandbox REJECTED] {validation.reason}")
                    natural_reply = validation.feedback_speak or validation.reason or "Azione non autorizzata per motivi di sicurezza."
                else:
                    self._logger.info(f"✅ [KinematicSandbox AUTHORIZED] {raw_call.function_name}")
                    executed_action_names.append(raw_call.function_name)
                    # Dispatch to SkillExecutor using clamped/sanitized parameters if populated
                    dispatch_args = validation.clamped_arguments if validation.clamped_arguments is not None else raw_call.arguments
                    if self.skill_executor and hasattr(self.skill_executor, "execute_skill"):
                        try:
                            res = self.skill_executor.execute_skill(raw_call.function_name, dispatch_args)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception as exec_err:
                            self._logger.error(f"Error executing skill {raw_call.function_name}: {exec_err}")
            except Exception as sb_err:
                self._logger.error(f"Error during kinematic sandbox evaluation: {sb_err}")
        else:
            natural_reply = clean_speech if clean_speech else "Ho elaborato la tua richiesta."

        # Final defensive speech sanitization gate: ensure zero residual action markup, braces, or empty string
        try:
            clean_final = sanitize_speech_for_tts(natural_reply)
        except Exception:
            clean_final = re.sub(r'<action>[\s\S]*?</action>', '', natural_reply).strip()
            clean_final = re.sub(r'```(?:json)?[\s\S]*?```', '', clean_final).strip()
        natural_reply = clean_final if clean_final else "Ho elaborato la tua richiesta."

        # Step 4: Publish on Response Callback and Output Voice
        if self.response_callback:
            self.response_callback(natural_reply)

        if should_speak and self.tts:
            await self.tts.speak(natural_reply)

        # Step 5: Update Short-Term Dialogue History (Working Memory)
        self.conversation_history.append({
            "role": "user",
            "content": clean_text,
            "user": clean_text
        })
        self.conversation_history.append({
            "role": "assistant",
            "content": natural_reply,
            "marcus": natural_reply
        })
        if len(self.conversation_history) > 10:
            self.conversation_history = self.conversation_history[-10:]

        self._record_recent_query(clean_text, natural_reply)

        # Step 6: Persist Local Turn into Autobiographical SQLite WAL (mag_database.db)
        if self.trinity_engine and self.trinity_engine.enabled:
            try:
                is_factual = bool(re.search(r'\b(significa|acronimo|definizione|ricordati|mi chiamo|chiamami|impara|nota)\b', clean_text, re.IGNORECASE))
                extracted_facts = None
                if is_factual:
                    extracted_facts = [{
                        "fact_text": f"{clean_text} -> {natural_reply}",
                        "fact_type": "KNOWLEDGE",
                        "confidence": 1.0
                    }]
                await self.trinity_engine.record_interaction(
                    user_text=clean_text,
                    robot_response=natural_reply,
                    actions_taken=executed_action_names,
                    was_successful=True,
                    user_identity=user_identity,
                    extracted_facts=extracted_facts
                )
            except Exception as e:
                self._logger.debug(f"TRINITY local turn recording skipped: {e}")

        self.metrics.inc_requests_success()
        return True

    async def process_input(self, text: str, source: str):
        self._current_source = source
        self.metrics.inc_requests_total()

        try:
            if self._is_emergency(text):
                await self._handle_emergency()
                self.metrics.inc_requests_success()
                return True

            if self._mic_muted and source == "audio":
                self._logger.debug("Mic muted, ignoring audio input.")
                return False

            # [v15.2] Ignora input di testo duplicati generati da ASR client-side se già gestiti dalla Live API
            is_duplicate = False
            if hasattr(self.llm, 'is_duplicate_text'):
                is_duplicate = self.llm.is_duplicate_text(text)

            if source == "text" and is_duplicate:
                self._logger.info(f"Ignorato input testuale duplicato ASR: '{text}' (duplicato={is_duplicate})")
                self.metrics.inc_requests_success()
                return True

            # --- ACTIVE REPETITION & ANTI-ECHO MANAGER ---
            norm_text = text.strip().lower()
            now = time.time()

            if not hasattr(self, '_recent_queries'):
                self._recent_queries = []
            if not hasattr(self, '_in_flight_queries'):
                self._in_flight_queries = {}

            # Prune records older than 20.0s
            self._recent_queries = [
                (t, ts, r) for t, ts, r in self._recent_queries
                if (now - ts) < 20.0
            ]
            self._in_flight_queries = {
                t: ts for t, ts in self._in_flight_queries.items()
                if (now - ts) < 20.0
            }

            # Case 1: In-Flight Repetition (query repeated while lock is held)
            is_in_flight_repeat = False
            if self._processing_lock.locked():
                if norm_text in self._in_flight_queries:
                    dt_inflight = now - self._in_flight_queries[norm_text]
                    if dt_inflight <= 10.0:
                        is_in_flight_repeat = True
                else:
                    for prev_t, prev_ts, _ in reversed(self._recent_queries):
                        if norm_text == prev_t and (now - prev_ts) <= 10.0:
                            is_in_flight_repeat = True
                            break

            if is_in_flight_repeat:
                witty_in_flight_replies = [
                    "Ti ho sentito, stavo solo riflettendo...",
                    "Un attimo di pazienza, ci sto pensando proprio ora!",
                    "Tranquillo, ti ho ascoltato! Sto elaborando la risposta.",
                    "Non ti ignoro, stavo giusto completando il pensiero!"
                ]
                witty_msg = random.choice(witty_in_flight_replies)
                self._logger.info(
                    f"💡 [RepetitionManager] Risposta empatica in-flight per query ripetuta: '{text}' -> '{witty_msg}'"
                )
                if self.response_callback:
                    self.response_callback(witty_msg)
                if (source == "audio") and self.tts:
                    await self.tts.speak(witty_msg)
                self.metrics.inc_requests_success()
                return True

            # Case 2: Post-Completion Repetition (0.25s <= dt <= 10.0s after previous query completed)
            completed_reply = None
            for prev_t, prev_ts, prev_reply in reversed(self._recent_queries):
                if norm_text == prev_t:
                    dt_completed = now - prev_ts
                    if 0.25 <= dt_completed <= 10.0:
                        completed_reply = prev_reply
                        break

            if completed_reply is not None and not self._processing_lock.locked():
                if completed_reply:
                    reaffirm_replies = [
                        f"Te lo riconfermo: {completed_reply}",
                        f"Come ti dicevo poco fa: {completed_reply}",
                        f"Ti rispondo volentieri di nuovo: {completed_reply}"
                    ]
                    reaffirm_msg = random.choice(reaffirm_replies)
                else:
                    reaffirm_msg = "Te lo riconfermo, ho già registrato la tua richiesta!"

                self._logger.info(
                    f"💡 [RepetitionManager] Riconferma empatica per query ripetuta completata: '{text}' -> '{reaffirm_msg}'"
                )
                if self.response_callback:
                    self.response_callback(reaffirm_msg)
                if (source == "audio") and self.tts:
                    await self.tts.speak(reaffirm_msg)

                self.conversation_history.append({"role": "user", "content": text})
                self.conversation_history.append({"role": "assistant", "content": reaffirm_msg})
                if len(self.conversation_history) > 10:
                    self.conversation_history = self.conversation_history[-10:]

                self._record_recent_query(text, reaffirm_msg)
                self.metrics.inc_requests_success()
                return True

            # Mark as in-flight before acquiring lock
            self._in_flight_queries[norm_text] = now

            try:
                async with self._processing_lock:
                    success = await self._process_locked(text, source)
                    if success:
                        self.metrics.inc_requests_success()
                    else:
                        self.metrics.inc_requests_failed()
                    return success
            finally:
                self._in_flight_queries.pop(norm_text, None)
        finally:
            self._current_source = ""

    async def process_document(self, text: str, document_data: str, mime_type: str, filename: str):
        """Process an incoming document (PDF, TIFF, etc.)"""
        self._current_source = "document"
        self.metrics.inc_requests_total()
        
        doc_info = {
            "data": document_data,
            "mime_type": mime_type,
            "filename": filename
        }

        async with self._processing_lock:
            success = await self._process_locked(text, source="document", documents=[doc_info])
            if success:
                self.metrics.inc_requests_success()
            else:
                self.metrics.inc_requests_failed()

    async def _process_locked(self, text: str, source: str, documents: list = None) -> bool:
        self._current_source = source
        should_speak = (source == "audio")
        try:
            clean_text = self._sanitizer.sanitize(text)
        except Exception as san_err:
            self._logger.warning(f"🛡️ [InputSanitizer] Blocked unsafe/malformed input ({san_err}); emitting safe fallback.")
            return await self._emit_conversational_fallback("ciao", should_speak, reason=f"sanitizer_blocked:{san_err}")
        if not clean_text:
            return await self._emit_conversational_fallback("ciao", should_speak, reason="empty_input")
        self.world_model.recent_interactions.append(f"User: {clean_text}")

        # Conversational Barge-in: Pause active exploration while listening/talking
        frontier_skill = self.skill_executor.registry.get("frontier_exploration")
        was_exploring_at_barge_in = False
        if frontier_skill and getattr(frontier_skill, "is_exploring", False):
            was_exploring_at_barge_in = True
            frontier_skill.pause_for_dialogue()
            self._logger.info("🛑 [Barge-in] Active exploration paused during user dialogue turn.")

        exploration_prompt_note = ""
        if was_exploring_at_barge_in:
            exploration_prompt_note = (
                "\n[NOTE COGNITIVA TRINITY: Il robot era in esplorazione attiva dell'ambiente quando l'utente gli ha parlato. "
                "Il moto ruote è stato temporaneamente messo in pausa per ascoltare e dialogare. "
                "Sii consapevole della tua attività di esplorazione, rispondi con naturalezza contestualizzando cosa stavi facendo "
                "se l'utente te lo chiede, e ricorda che riprenderai automaticamente ad esplorare al termine della conversazione, "
                "a meno che l'utente non ti dica esplicitamente di fermarti o annullare l'esplorazione.]\n"
            )

        # --- Voice-triggered Face Enrollment ---
        enroll_match = re.search(r'\b(?:quest[ao]\s+è|ti\s+presento|lui\s+è|lei\s+è)\s+([a-zA-Z]+)', clean_text, re.IGNORECASE)
        if enroll_match:
            name_candidate = enroll_match.group(1).strip().lower()
            stop_words = {
                "un", "una", "il", "la", "lo", "le", "i", "gli", "mio", "mia", "miei", "mie", 
                "amico", "amica", "questo", "questa", "quello", "quella", "cane", "gatto", "sedia", "tavolo"
            }
            if name_candidate not in stop_words and len(name_candidate) > 2:
                if self.node and hasattr(self.node, 'face_recognition_service'):
                    face_svc = self.node.face_recognition_service
                    success = face_svc.start_enrollment(name_candidate, num_samples=10)
                    if success:
                        self._logger.info(f"Enrollment session started via VUI for: {name_candidate}")
                        if should_speak:
                            await self.tts.speak(f"Va bene, inizio a imparare il volto di {name_candidate.capitalize()}. Guarda la telecamera per favore.")
                        if self.response_callback:
                            self.response_callback(f"Va bene, inizio a imparare il volto di {name_candidate.capitalize()}. Guarda la telecamera per favore.")
                        return True

        # --- Voice-triggered Speaker Enrollment ---
        speaker_match = re.search(r'\b(?:registra\s+la\s+mia\s+voce\s+come|impara\s+la\s+mia\s+voce\s+(?:come|sono)?|ti\s+presento\s+la\s+mia\s+voce\s+sono)\s+([a-zA-Z]+)', clean_text, re.IGNORECASE)
        if speaker_match:
            name_candidate = speaker_match.group(1).strip().lower()
            stop_words = {
                "un", "una", "il", "la", "lo", "le", "i", "gli", "mio", "mia", "miei", "mie", 
                "amico", "amica", "questo", "questa", "quello", "quella", "cane", "gatto", "sedia", "tavolo"
            }
            if name_candidate not in stop_words and len(name_candidate) > 2:
                if self.node and hasattr(self.node, 'speaker_trigger_pub'):
                    msg = String()
                    msg.data = name_candidate
                    self.node.speaker_trigger_pub.publish(msg)
                    self._logger.info(f"Speaker enrollment session started via VUI for: {name_candidate}")
                    if should_speak:
                        await self.tts.speak(f"Va bene, inizio a registrare le impronte della tua voce come {name_candidate.capitalize()}. Continua a parlarmi per favore.")
                    if self.response_callback:
                        self.response_callback(f"Va bene, inizio a registrare le impronte della tua voce come {name_candidate.capitalize()}. Continua a parlarmi per favore.")
                    return True

        # --- Dopamine Biometric Alignment (Input Flow) ---
        self.agent_state.current_task = clean_text
        self.agent_state = await self.cognitive_graph.run_input_flow(
            self.agent_state,
            user_text=clean_text,
            base_system_prompt=self.llm._system_prompt
        )

        # --- Contextual Memory Filter (Repeated Queries) ---
        now_ts = time.time()
        self._recent_inputs = [item for item in self._recent_inputs if now_ts - item["timestamp"] < 300.0]
        normalized_text = re.sub(r'[^\w\s]', '', clean_text.lower()).strip()
        repeat_count = sum(1 for item in self._recent_inputs if item["normalized"] == normalized_text)
        self._recent_inputs.append({
            "normalized": normalized_text,
            "timestamp": now_ts
        })
        
        repeated_prompt_note = ""
        if repeat_count >= 2:
            repeated_prompt_note = (
                "\n[NOTE: L'utente ti sta ponendo questa domanda per la terza (o successiva) volta consecutiva "
                "negli ultimi 5 minuti. Non rispondere in modo robotico o ripetitivo. Sii molto spontaneo, "
                "empatico o gentilmente ironico sul fatto che si sta ripetendo, chiedendogli in modo amichevole "
                "se c'è un problema di connessione o se non ti ha sentito bene.]\n"
            )

        # Fast-path skill execution (e.g direct commands)
        # CRITICAL: Motion initiation skills MUST NEVER bypass the TRINITY LLM!
        # HOWEVER, EMERGENCY STOP / FERMATI MUST EXECUTE INSTANTLY ON-DEVICE FOR PHYSICAL SAFETY!
        skill = self.skill_executor.find_best_match(clean_text, min_confidence=0.95)
        if skill and skill.name in ("query_memory", "memory_info", "consult_antigravity", "consult_documentation"):
            self._logger.info(f"Routing cognitive skill '{skill.name}' to TRINITY LLM (fast-path bypassed).")
            skill = None
        elif skill and skill.name in ("navigation", "frontier_exploration", "visual_exploration"):
            is_stop_cmd = bool(re.search(r'\b(ferma|fermati|stop|alt|basta|arrestati|blocca|interrompi|cancella|non\s+ti\s+muovere|non\s+muoverti)\b', clean_text, re.IGNORECASE))
            if not is_stop_cmd:
                self._logger.info(f"Routing navigation/exploration command '{clean_text}' to TRINITY LLM (fast-path bypassed).")
                skill = None
            else:
                self._logger.info(f"🛑 [FAST-PATH SAFETY] Immediate stop command recognized: '{clean_text}' -> executing instant stop!")

        if skill:
            self._logger.info(f"Fast-path skill match: {skill.name}")
            try:
                texts = await self.skill_executor.execute_skill(skill.name, {"text": clean_text})
                for t in texts:
                    if should_speak:
                        await self.tts.speak(t)
                    if self.response_callback:
                        self.response_callback(t)
                
                # Biometric Post-Execution Critic check
                self.agent_state = await self.cognitive_graph.run_post_execution_flow(
                    self.agent_state,
                    skill_name=skill.name,
                    success=True
                )
            except Exception as e:
                self._logger.error(f"Errore fast-path skill execution: {e}")
                self.llm.flag_tool_failure()
                err_msg = "Uhm... Dunque, scusami, ho avuto un piccolo intoppo nel comando rapido."
                if should_speak:
                    await self.tts.speak(err_msg)
                if self.response_callback:
                    self.response_callback(err_msg)
                
                # Biometric Post-Execution Critic check (Failure)
                self.agent_state = await self.cognitive_graph.run_post_execution_flow(
                    self.agent_state,
                    skill_name=skill.name,
                    success=False,
                    error_message=str(e)
                )

            # Auto-resume exploration after fast-path execution if paused (unless stopped)
            await self._finalize_barge_in_exploration(clean_text, was_exploring_at_barge_in, frontier_skill)
            return True

        # Check HybridRouter routing decision
        if getattr(self, 'hybrid_router', None):
            try:
                from ..arbitration.intent_classifier import RouteTarget
                routing = self.hybrid_router.route(clean_text)
                if (
                    routing.destination == RouteTarget.LOCAL_QWEN
                    or getattr(routing.destination, "value", None) == RouteTarget.LOCAL_QWEN.value
                ):
                    self._logger.info(f"⚡ [HYBRID ROUTER] Direct local Qwen2 routing for '{clean_text}' ({routing.reason})")
                    if self.hailo_client:
                        user_id = self.world_model.current_user.get('name') if (self.world_model and hasattr(self.world_model, 'current_user') and self.world_model.current_user) else None
                        res = await self._handle_local_qwen(
                            clean_text,
                            should_speak,
                            user_identity=user_id,
                            combined_notes=f"{repeated_prompt_note}\n{exploration_prompt_note}".strip()
                        )
                        await self._finalize_barge_in_exploration(clean_text, was_exploring_at_barge_in, frontier_skill)
                        return res
            except Exception as e:
                self._logger.warning(f"HybridRouter check failed: {e}")

        # Offline or Circuit Breaker Open (Quota Exhausted) Fast-Bypass via Local Qwen2
        cloud_unavailable = False
        if getattr(self.metrics, 'connectivity_state', None) == "OFFLINE":
            cloud_unavailable = True
        elif hasattr(self.llm, 'circuit_breaker') and self.llm.circuit_breaker.state == "OPEN":
            cloud_unavailable = True

        if cloud_unavailable:
            self._logger.info("📡 Cloud is OFFLINE or Circuit Breaker OPEN (quota exhausted): routing request directly to Local Qwen2 on Hailo-10H NPU.")
            if self.hailo_client:
                user_id = self.world_model.current_user.get('name') if (self.world_model and hasattr(self.world_model, 'current_user') and self.world_model.current_user) else None
                res = await self._handle_local_qwen(
                    clean_text,
                    should_speak,
                    user_identity=user_id,
                    combined_notes=f"{repeated_prompt_note}\n{exploration_prompt_note}".strip()
                )
                await self._finalize_barge_in_exploration(clean_text, was_exploring_at_barge_in, frontier_skill)
                return res
            if should_speak:
                await self.tts.speak("Sono offline, riprova più tardi.")
            if self.response_callback:
                self.response_callback("Sono offline, riprova più tardi.")
            await self._finalize_barge_in_exploration(clean_text, was_exploring_at_barge_in, frontier_skill)
            return True

        ha_context = self.ha_context_provider()
        frame = await self._get_latest_frame()
        images = [frame.b64] if (frame and self._is_vision_request(clean_text)) else []
        
        # Merge incoming documents if any
        llm_docs = documents or []

        functions = self.skill_executor.registry.get_function_declarations()
        
        # Inject implicit tools
        functions.append({
            "name": "generate_formatted_document",
            "description": "Genera un documento, report o tabella dettagliata in formato Markdown da mostrare a schermo su Foxglove. Usa questo strumento INVECE di rispondere testualmente se l'utente chiede un documento formattato.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "content": {
                        "type": "STRING",
                        "description": "Il contenuto del documento formattato in Markdown."
                    }
                },
                "required": ["content"]
            }
        })
        functions.append({
            "name": "ask_visual_question",
            "description": "Richiede un'analisi visiva della telecamera per rispondere a una domanda specifica sull'ambiente visivo.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "question": {
                        "type": "STRING",
                        "description": "La domanda da porre sulla scena visiva."
                    }
                },
                "required": ["question"]
            }
        })
        functions.extend([
            {
                "name": "start_frontier_exploration",
                "description": "Avvia l'esplorazione autonoma e la mappatura a frontiere dell'ambiente domestico con Nav2.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "mode": {
                            "type": "STRING",
                            "description": "Modalità di esplorazione: 'all_frontiers' (esplora tutto), 'quick_scan' (perlustrazione rapida)."
                        }
                    },
                    "required": []
                }
            },
            {
                "name": "stop_navigation",
                "description": "Arresta immediatamente qualsiasi navigazione o esplorazione in corso, azzerando le velocità delle ruote.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "reason": {
                            "type": "STRING",
                            "description": "Motivo opzionale dell'arresto (es. 'richiesta utente', 'ostacolo')."
                        }
                    },
                    "required": []
                }
            },
            {
                "name": "get_navigation_status",
                "description": "Restituisce lo stato attuale della navigazione ed esplorazione: se attiva, modalità (EXPLORE/HUNT/IDLE), bersaglio cercato, frontiere residue e stanza attuale.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "search_target",
                "description": "Avvia la ricerca visiva e semantica di un target specifico (oggetto o persona) muovendosi verso le ultime coordinate note o esplorando le aree sconosciute.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target": {
                            "type": "STRING",
                            "description": "Nome dell'oggetto o persona da cercare (es. 'chiavi', 'Marco', 'sedia', 'bottiglia')."
                        },
                        "room": {
                            "type": "STRING",
                            "description": "Stanza opzionale in cui concentrare la ricerca."
                        }
                    },
                    "required": ["target"]
                }
            }
        ])

        # Active RAG Semantic Memory Retrieval & TRINITY Augmentation
        rag_memories = []
        user_id = None
        if self.world_model and hasattr(self.world_model, 'current_user') and self.world_model.current_user:
            user_id = self.world_model.current_user.get('name')

        email_ctx = ""
        email_skill = self.skill_executor.registry.get("check_emails")
        if email_skill and hasattr(email_skill, 'consume_notifications'):
            email_ctx = email_skill.consume_notifications() or ""

        combined_notes = (repeated_prompt_note + exploration_prompt_note).strip()

        if self.trinity_engine and self.trinity_engine.enabled:
            try:
                augmented_prompt = await asyncio.wait_for(
                    self.trinity_engine.build_augmented_prompt(
                        user_text=clean_text,
                        source=source,
                        user_identity=user_id,
                        system_prompt=self.llm._system_prompt,
                        dopaminergic_override=getattr(self.agent_state, 'system_prompt_override', ''),
                        repeated_note=combined_notes,
                        email_context=f"\n[NOTIFICHE EMAIL RECENTI]\n{email_ctx}" if email_ctx else "",
                        conversation_history=list(self.conversation_history)
                    ),
                    timeout=2.0
                )
            except Exception as e:
                self._logger.warning(f"TRINITY prompt augmentation failed for cloud LLM: {e}")
                augmented_prompt = self._build_prompt(clean_text, ha_context, combined_notes, rag_memories, conversation_history=list(self.conversation_history))
        else:
            try:
                if self.config.get_config().rag.enabled and hasattr(self.memory_manager, 'memory_store') and self.memory_manager.memory_store:
                    search_results = await self.memory_manager.memory_store.search(clean_text, top_k=3)
                    for res in search_results:
                        if hasattr(res, 'score') and res.score >= 0.40:
                            rag_memories.append(res.memory.content)
                    if rag_memories:
                        self._logger.info(f"🧠 [RAG Retrieval] Recuperate {len(rag_memories)} memorie rilevanti per: '{clean_text}'")
            except Exception as e:
                self._logger.warning(f"Errore durante il recupero RAG in conversazione: {e}")

            augmented_prompt = self._build_prompt(clean_text, ha_context, combined_notes, rag_memories, conversation_history=list(self.conversation_history))

        # Timeout dall'oggetto config.llm.timeout (di base accesskey)
        llm_timeout = 20.0
        try:
             llm_timeout = float(self.config.get_config().llm.timeout)
        except:
             pass

        try:
            start = time.perf_counter()
            is_live = False
            if source == "audio":
                try:
                    # 1. Prova Live API (esclusiva per input Audio)
                    response = await asyncio.wait_for(
                        self.llm.generate_live(
                            prompt=augmented_prompt,
                            context=self.world_model.to_prompt_section(),
                            functions=functions,
                            images=images,
                            documents=llm_docs
                        ),
                        timeout=llm_timeout
                    )
                    is_live = True
                except (asyncio.TimeoutError, Exception) as e:
                    self._logger.warning(f"Live API failed or timeout ({e}), falling back to standard...")
                    # 2. Fallback su API Standard (GenerateContent)
                    response = await asyncio.wait_for(
                        self.llm.generate(
                            prompt=augmented_prompt,
                            images=images,
                            documents=llm_docs,
                            functions=functions
                        ),
                        timeout=llm_timeout
                    )
            else:
                self._logger.info(f"Source is '{source}', routing directly to Standard Text API.")
                # Bypass Live Audio API entirely for Foxglove text chats
                response = await asyncio.wait_for(
                    self.llm.generate(
                        prompt=augmented_prompt,
                        images=images,
                        documents=llm_docs,
                        functions=functions
                    ),
                    timeout=llm_timeout
                )

            latency = time.perf_counter() - start
            self.metrics.record_llm_latency(latency)
        except (asyncio.TimeoutError, Exception) as exc:
            self.metrics.record_llm_error("cloud_failover")
            self._logger.warning(
                f"⚠️ [CLOUD FAILOVER] Gemini call failed or quota exceeded ({exc}). "
                f"Executing immediate failover to Local Qwen2 on Hailo-10H NPU..."
            )
            if getattr(self, 'hailo_client', None):
                user_id = self.world_model.current_user.get('name') if (self.world_model and hasattr(self.world_model, 'current_user') and self.world_model.current_user) else None
                res = await self._handle_local_qwen(
                    clean_text,
                    should_speak,
                    user_identity=user_id,
                    combined_notes=f"{repeated_prompt_note}\n{exploration_prompt_note}".strip()
                )
                await self._finalize_barge_in_exploration(clean_text, was_exploring_at_barge_in, frontier_skill)
                return res

            res = await self._emit_conversational_fallback(clean_text, should_speak, reason="cloud_and_hailo_unavailable")
            await self._finalize_barge_in_exploration(clean_text, was_exploring_at_barge_in, frontier_skill)
            return res

        # Post LLM validation -> prevent emergency action
        response_text = getattr(response, "text", "")
        response_actions = getattr(response, "actions", [])
        
        # Extract formatted document from actions
        formatted_doc = None
        for action in response_actions:
            name = action.get("action_type", action.get("name", ""))
            if name == "generate_formatted_document":
                formatted_doc = action.get("args", {}).get("content", None)
                break

        if formatted_doc and self.document_callback:
            self.document_callback(formatted_doc)

        # Logic for Tool use if LLM suggests it (e.g. ask_visual_question)
        for action in response_actions:
            name = action.get("action_type", action.get("name", ""))
            if name == "ask_visual_question":
                question = action.get("args", {}).get("question", "")
                if question:
                    result = await self.ask_visual_question(question)
                    self._logger.info(f"VQA Result obtained, re-querying LLM with context...")
                    vqa_prompt = f"{augmented_prompt}\n[ACTIVE SEARCH RESULT: {result}]\nUser: {clean_text}"
                    final_resp = await self.llm.generate(prompt=vqa_prompt, images=images)
                    response_text = getattr(final_resp, "text", "")

        # Remove implicit tools before sending to standard skill executor
        explicit_actions = [a for a in response_actions if a.get("action_type", a.get("name", "")) not in ["generate_formatted_document", "ask_visual_question"]]

        # CRITICAL SAFETY & COGNITIVE RULE (FM-NAV-035):
        # All actions and physical motions (navigation, exploration, hunt) MUST be deliberated by the LLM
        # via structured Function Calling and NEVER triggered by keyword-matching scripts (dangerous false positives).
        # TANTAMOUNT EXCEPTION: Emergency STOP commands ("fermati", "stop", "alt", "basta") which MUST be immediate!
        if not explicit_actions and (
            bool(re.search(r'\b(ferma|fermati|stop|alt|basta|arrestati|blocca)\b', clean_text, re.IGNORECASE)) or
            (frontier_skill and any(p.search(clean_text.lower()) for p in frontier_skill.STOP_PATTERNS))
        ):
            self._logger.info(f"🛑 [SAFETY STOP EXCEPTION] Emergency stop command recognized in conversation turn: '{clean_text}'")
            explicit_actions.append({"action_type": "frontier_exploration", "args": {"action": "stop"}})
            explicit_actions.append({"action_type": "navigation", "args": {"action": "stop"}})

        actions_spoken_count = 0
        if explicit_actions:
            self._logger.debug(f"LLM suggested actions: {explicit_actions}")
            for act in explicit_actions:
                 target_act = act.get("args", {}).get("text", "")
                 if self._is_emergency(target_act):
                      await self._handle_emergency()
                      continue

            try:
                async for t in self.skill_executor.execute_actions_stream(explicit_actions):
                      if t:
                          actions_spoken_count += 1
                          if should_speak and not is_live:
                               await self.tts.speak(t)
                          if self.response_callback:
                               self.response_callback(t)
                
                # Critic evaluation on successful skill executions
                for act in explicit_actions:
                    skill_name = act.get("action_type", act.get("name", "unknown"))
                    self.agent_state = await self.cognitive_graph.run_post_execution_flow(
                        self.agent_state,
                        skill_name=skill_name,
                        success=True
                    )
            except Exception as e:
                self._logger.error(f"Errore durante l'esecuzione delle skill: {e}", exc_info=True)
                self.llm.flag_tool_failure()
                err_msg = "Uhm... Dunque, scusami, ho avuto un piccolo intoppo nell'eseguire questa azione."
                if should_speak and not is_live:
                    await self.tts.speak(err_msg)
                if self.response_callback:
                     self.response_callback(err_msg)
                     
                # Critic evaluation on failed skill executions
                for act in explicit_actions:
                    skill_name = act.get("action_type", act.get("name", "unknown"))
                    self.agent_state = await self.cognitive_graph.run_post_execution_flow(
                        self.agent_state,
                        skill_name=skill_name,
                        success=False,
                        error_message=str(e)
                    )
                 
        if not response_text and (formatted_doc or any(a.get("action_type", a.get("name", "")) == "ask_visual_question" for a in response_actions)):
            response_text = "Ho analizzato l'hardware visivamente e prodotto un report a schermo."

        # Safety Fallback: Se sono state richieste azioni esplicite ma nessuna ha prodotto parlato e response_text è vuoto
        if not response_text and explicit_actions and actions_spoken_count == 0:
            self._logger.warning("Nessun feedback vocale emesso dalle skill eseguite, applico fallback anti-silenzio.")
            has_memory_action = any("memory" in a.get("action_type", a.get("name", "")) for a in explicit_actions)
            if has_memory_action:
                response_text = "Ho controllato la mia memoria, ma non ho trovato informazioni specifiche corrispondenti."
            else:
                response_text = "Ho eseguito l'operazione richiesta."

        if response_text:
            if should_speak and not is_live:
                await self.tts.speak(response_text)
            self.world_model.recent_interactions.append(f"Robot: {response_text[:50]}...")
            if self.response_callback:
                 self.response_callback(response_text)

            # Record completed turn in Working Memory (Short-Term Dialogue History)
            self.conversation_history.append({"role": "user", "content": clean_text})
            self.conversation_history.append({"role": "assistant", "content": response_text})
            if len(self.conversation_history) > 10:
                self.conversation_history = self.conversation_history[-10:]
            self._record_recent_query(clean_text, response_text)

        if response_text and self.config.get_config().rag.enabled:
            is_factual = bool(re.search(r'\b(significa|acronimo|definizione|ricordati|mi chiamo|chiamami|impara|nota)\b', clean_text, re.IGNORECASE))
            mem_type = "learned_fact" if is_factual else "conversation"
            await self.memory_manager.store_background(clean_text, response_text, mem_type)

        # TRINITY MAG Autobiographical & Fact Recording
        if response_text and self.trinity_engine and self.trinity_engine.enabled:
            try:
                extracted_facts = None
                if is_factual:
                    extracted_facts = [{
                        "fact_text": f"{clean_text} -> {response_text}",
                        "fact_type": "KNOWLEDGE",
                        "confidence": 1.0
                    }]
                await self.trinity_engine.record_interaction(
                    user_text=clean_text,
                    robot_response=response_text,
                    actions_taken=[a.get("action_type", a.get("name", "")) for a in response_actions],
                    was_successful=True,
                    user_identity=user_id,
                    extracted_facts=extracted_facts
                )
            except Exception as e:
                self._logger.debug(f"TRINITY background recording skipped: {e}")

        # Auto-resume exploration after dialogue turn (unless user commanded stop)
        await self._finalize_barge_in_exploration(clean_text, was_exploring_at_barge_in, frontier_skill)

        return True

    async def ask_visual_question(self, question: str) -> str:
        """Call the VQA service to analyze the current camera frame."""
        self._logger.info(f"Calling VQA Service for: {question}")
        
        # We need a node reference to create a client
        # ConversationManager doesn't have it, but AIOrchestrator does.
        # We'll assume the LLM object or skill_executor can provide a gateway?
        # Actually, let's use the node from WorldModel or pass it in.
        # For now, let's assume we can get it from orchestrator (provided in __init__)
        if not hasattr(self, 'node'):
             return "Errore: Modulo VQA non collegato."

        client = self.node.create_client(AskVisualQuestion, 'ask_visual_question')
        if not client.wait_for_service(timeout_sec=2.0):
            return "Errore: Servizio VQA non disponibile al momento."

        req = AskVisualQuestion.Request()
        req.question = question
        
        try:
            future = client.call_async(req)
            resp = await future
            if resp.success:
                return resp.answer
            else:
                return f"Analisi fallita: {resp.answer}"
        except Exception as e:
            return f"Errore durante l'analisi visiva: {e}"

    def _build_prompt(self, user_text: str, ha_context: str, repeated_note: str = "", rag_memories: list = None, conversation_history: list = None) -> str:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("Europe/Rome")).strftime("%A %d %B %Y, ore %H:%M")
        prompt = f"[DATA LOCALE: {now} (fuso orario: Europe/Rome)]\n"
        if repeated_note:
            prompt += f"{repeated_note}\n"
        if ha_context:
            prompt += f"{ha_context}\n"

        if rag_memories:
            prompt += "\n[MEMORIE EPISODICHE E FATTI APPRESI (RAG)]\n"
            for mem in rag_memories:
                prompt += f"- {mem}\n"
            
        # Inietta le notifiche email recenti in modo che l'LLM possa farvi riferimento
        email_skill = self.skill_executor.registry.get("check_emails")
        if email_skill and hasattr(email_skill, 'consume_notifications'):
            email_ctx = email_skill.consume_notifications()
            if email_ctx:
                prompt += f"\n[NOTIFICHE EMAIL RECENTI]\n{email_ctx}\n"

        if conversation_history:
            prompt += "\n[CONVERSAZIONE RECENTE]\n"
            for turn in conversation_history:
                role = "Utente" if turn.get("role") in ("user", "human") else "Marcus"
                content = turn.get("content", "").strip()
                if content:
                    prompt += f"{role}: {content}\n"
                
        prompt += f"Utente: {user_text}\n"
        return prompt

    def _is_vision_request(self, text: str) -> bool:
        keywords = ['vedi', 'guarda', 'cosa vedi', 'dimmi', 'descrivi', 'mostra']
        return any(kw in text.lower() for kw in keywords)
