import asyncio
import threading
import time
import re
import datetime
from robot_ai.utils import get_logger
from robot_ai.core.input_sanitizer import InputSanitizer
from robot_ai.core.camera_frame import CameraFrame

# New: VQA Service from robopy_controller
from robopy_controller.srv import AskVisualQuestion
import rclpy
from std_msgs.msg import Bool

class ConversationManager:
    def __init__(self, llm, tts, skill_executor, memory_manager, world_model,
                 ha_context_provider, metrics, config, reactive_safety, response_callback=None, node=None, mic_mute_pub=None):
        
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
        self._latest_frame = None
        self._frame_lock = threading.Lock()
        self._processing_lock = None
        self._logger = get_logger("conversation")
        self._sanitizer = InputSanitizer()
        self.mic_mute_pub = mic_mute_pub
        
        # Pre-create publishers to avoid on-the-fly ROS 2 discovery hangs
        self._speaking_pub = None
        if self.node:
             self._speaking_pub = self.node.create_publisher(Bool, '/ai/tts/speaking', 10)
             self._logger.info("ROS 2 Publishers attached to ConversationManager.")

    def _get_processing_lock(self) -> asyncio.Lock:
        if self._processing_lock is None:
            self._processing_lock = asyncio.Lock()
        return self._processing_lock

    def set_latest_frame(self, frame):
        with self._frame_lock:
            self._latest_frame = frame

    async def _get_latest_frame(self):
        with self._frame_lock:
            return self._latest_frame

    def set_mic_muted(self, muted: bool):
        self._mic_muted = muted

    def _is_emergency(self, text: str) -> bool:
        return bool(re.search(r'\b(fermati|stop|emergenza)\b', text, re.IGNORECASE))

    async def _handle_emergency(self):
        self.reactive_safety.emergency_stop()
        await self.tts.speak("Fermo tutto!")

    async def process_input(self, text: str, source: str):
        self.metrics.inc_requests_total()

        if self._is_emergency(text):
            await self._handle_emergency()
            self.metrics.inc_requests_success()
            return

        if self._mic_muted and source == "audio":
            self._logger.debug("Mic muted, ignoring audio input.")
            return

        async with self._get_processing_lock():
            success = await self._process_locked(text, source)
            if success:
                self.metrics.inc_requests_success()
            else:
                self.metrics.inc_requests_failed()

    async def _process_locked(self, text: str, source: str) -> bool:
        clean_text = self._sanitizer.sanitize(text)
        self._logger.info(f"--- Processing input (source={source}): '{clean_text}' ---")
        
        if not clean_text.strip():
            self._logger.warning("Received empty input after sanitization, ignoring.")
            return True

        self.world_model.recent_interactions.append(f"User: {clean_text}")

        # Fast-path skill execution (e.g direct commands)
        # We exclude 'email' from fast-path to ensure LLM handles parameter extraction (recipients, body)
        skill = self.skill_executor.find_best_match(clean_text, min_confidence=0.95)
        if skill and skill.name != "email":
            self._logger.info(f"Fast-path skill match: {skill.name}")
            texts = await self.skill_executor.execute_skill(skill.name, {"text": clean_text})
            for t in texts:
                await self.tts.speak(t)
            return True

        # Offline fallback
        if self.metrics.connectivity_state == "OFFLINE":
            await self.tts.speak("Sono offline, riprova più tardi.")
            return True

        ha_context = self.ha_context_provider()
        frame = await self._get_latest_frame()
        # FIX: frame.b64 è una @property, non un metodo!
        images = [frame.b64] if (frame and self._is_vision_request(clean_text)) else []

        # 1. Ricerca memorie (RAG) per dare contesto storico
        memory_context = ""
        try:
            results = await self.memory_manager.search(clean_text, limit=3)
            # P6: search() never raises, but may return make_unavailable_result().
            # Filter out sentinel entries before injecting into the prompt.
            valid = [r for r in results if not r.is_unavailable]
            if valid:
                memory_context = "[MEMORIE PRECEDENTI]\n" + "\n".join(
                    [f"- {r.content}" for r in valid]
                )
            elif results and results[0].is_unavailable:
                reason = results[0].metadata.get("reason", "unknown")
                self._logger.warning(f"RAG unavailable (shed-load / timeout): {reason}")
        except Exception as e:
            self._logger.warning(f"RAG search unexpected error: {e}")

        self._logger.debug("Building functions and world context...")
        functions = [s.to_function_declaration() for s in self.skill_executor.get_all()]

        # 2. Short-term context (recent interactions + world state)
        world_context = self.world_model.to_prompt_section()

        # 3. Dynamic Skill Summary (Self-Awareness)
        skill_summary = self.skill_executor.get_summary()
        
        augmented_prompt = self._build_prompt(clean_text, ha_context, memory_context, world_context, skill_summary)
        self._logger.info(f"Prompt built (length={len(augmented_prompt)}). Awareness of {len(functions)} functions.")

        # Config access: self.config is actually ConfigManager or AIConfig
        # We need to reach config.llm.timeout safely.
        llm_timeout = 60.0
        try:
             # Try Pydantic access if config_manager was passed
             if hasattr(self.config, 'get_config'):
                  llm_timeout = float(self.config.get_config().llm.timeout)
             else:
                  # Assume self.config is AIConfig
                  llm_timeout = float(self.config.llm.timeout)
        except Exception as e:
             self._logger.debug(f"LLM timeout fallback to 60s (access error: {e})")
             pass

        # Notifica inizio processamento (disattiva porcupine trigger se presente)
        self._logger.debug("Notifying VUI speaking state (start)...")
        self._set_vui_speaking(True)
        self._logger.debug("VUI set to speaking=True.")

        try:
            start = time.perf_counter()
            if source == "audio":
                try:
                    # 1. Prova Live API (esclusiva per input Audio)
                    response = await asyncio.wait_for(
                        self.llm.generate_live(
                            prompt=augmented_prompt,
                            context=self.world_model.to_prompt_section(),
                            functions=functions,
                            images=images
                        ),
                        timeout=llm_timeout
                    )
                except (asyncio.TimeoutError, Exception) as e:
                    self._logger.warning(f"Live API failed or timeout ({e}), falling back to standard...")
                    # 2. Fallback su API Standard (GenerateContent)
                    response = await asyncio.wait_for(
                        self.llm.generate(
                            prompt=augmented_prompt,
                            images=images,
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
                        functions=functions
                    ),
                    timeout=llm_timeout
                )

            latency = time.perf_counter() - start
            self.metrics.record_llm_latency(latency)
        except asyncio.TimeoutError:
            self.metrics.record_llm_error("timeout")
            self._logger.error("LLM timeout both Live and Standard.")
            await self.tts.speak("Non riesco a connettermi al cervello.")
            return False
        except Exception as e:
            self.metrics.record_llm_error("unexpected")
            self._logger.error(f"LLM call failed: {e}", exc_info=True)
            return False

        # Post LLM validation -> prevent emergency action
        response_actions = getattr(response, 'actions', [])
        # Controlliamo che l'LLM non abbia tradimentato la regex emergency stop se aveva solo lo scopo della chat
        # Gestiamo dictionary o obj pattern
        if hasattr(response, "get"):
             # It's a dict likely
             response_text = response.get("response_text", "")
             response_actions = response.get("actions", [])
        else:
             response_text = getattr(response, "response_text", "")
             response_actions = getattr(response, "actions", [])

        # Logic for Tool use if LLM suggests it (e.g. ask_visual_question, check_home_assistant)
        ha_handled = False
        for action in response_actions:
            action_name = action.get("name") or action.get("action_type", "")
            # Normalizza: l'LLM può chiamare 'check_email' o 'check_emails'
            if action_name == "check_email":
                action_name = "check_emails"

            if action_name == "ask_visual_question":
                question = action.get("args", {}).get("question", "")
                if question:
                    result = await self.ask_visual_question(question)
                    # Re-send to LLM with visual data context
                    self._logger.info(f"VQA Result obtained, re-querying LLM with context...")
                    vqa_prompt = f"{augmented_prompt}\n[ACTIVE SEARCH RESULT: {result}]\nUser: {clean_text}"
                    # Re-generate without tools for final answer to avoid loops
                    final_resp = await self.llm.generate(prompt=vqa_prompt, images=images)
                    if hasattr(final_resp, "get"):
                        response_text = final_resp.get("response_text", "")
                    else:
                        response_text = getattr(final_resp, "response_text", "")

            elif action_name == "check_home_assistant":
                # Pattern: query HA → ri-inietta dati nell'LLM → risposta naturale
                ha_args = action.get("args", {})
                self._logger.info(f"🏠 HA Query action detected: {ha_args}")
                ha_speak_texts = await self.skill_executor.execute_skill(
                    "check_home_assistant", ha_args
                )
                if ha_speak_texts:
                    ha_data_str = " | ".join(ha_speak_texts)
                    self._logger.info(f"🏠 HA data retrieved, re-querying LLM for natural response...")
                    ha_prompt = (
                        f"{augmented_prompt}\n"
                        f"[HOME ASSISTANT QUERY RESULT: {ha_data_str}]\n"
                        f"Rispondi all'utente in modo naturale e conversazionale usando i dati sopra. "
                        f"Non ripetere i dati meccanicamente, integra nella conversazione."
                    )
                    try:
                        final_resp = await asyncio.wait_for(
                            self.llm.generate(prompt=ha_prompt, images=[]),
                            timeout=15.0
                        )
                        if hasattr(final_resp, "get"):
                            natural_text = final_resp.get("response_text", "")
                        else:
                            natural_text = getattr(final_resp, "response_text", "")
                        
                        if natural_text:
                            response_text = natural_text
                            ha_handled = True
                            self._logger.info(f"🏠 LLM natural HA response: '{natural_text[:80]}...'")
                        else:
                            # Fallback: usa la risposta diretta della skill
                            self._logger.warning("🏠 LLM re-query returned empty, using skill response")
                            response_text = ha_data_str
                            ha_handled = True
                    except Exception as e:
                        # Fallback: usa la risposta diretta della skill
                        self._logger.warning(f"🏠 LLM re-query failed ({e}), using skill response")
                        response_text = ha_data_str
                        ha_handled = True
            elif action_name == "check_emails":
                # Pattern email: esegui skill → re-inietta risultato nell'LLM → risposta naturale
                email_args = action.get("args", {})
                # Aggiungi defaults se l'LLM non ha fornito intent/text
                if "intent" not in email_args:
                    email_args["intent"] = "read"
                if "text" not in email_args:
                    email_args["text"] = clean_text
                intent = email_args.get("intent", "read")
                self._logger.info(f"📧 Email action detected: intent={intent}")
                email_speak_texts = await self.skill_executor.execute_skill(
                    "check_emails", email_args
                )
                if email_speak_texts:
                    email_data_str = " | ".join(email_speak_texts)
                    self._logger.info(f"📧 Email data retrieved, re-querying LLM for natural response...")
                    email_prompt = (
                        f"{augmented_prompt}\n"
                        f"[EMAIL SKILL RESULT: {email_data_str}]\n"
                        f"Rispondi all'utente in modo naturale usando i dati email sopra."
                    )
                    try:
                        final_resp = await asyncio.wait_for(
                            self.llm.generate(prompt=email_prompt, images=[]),
                            timeout=15.0
                        )
                        if hasattr(final_resp, "get"):
                            natural_text = final_resp.get("response_text", "")
                        else:
                            natural_text = getattr(final_resp, "response_text", "")
                        if natural_text:
                            response_text = natural_text
                            ha_handled = True
                        else:
                            response_text = email_data_str
                            ha_handled = True
                    except Exception as e:
                        self._logger.warning(f"📧 LLM re-query failed ({e}), using skill response")
                        response_text = email_data_str
                        ha_handled = True
                else:
                    response_text = "Non ho trovato nuove email o si è verificato un errore di connessione."
                    ha_handled = True

        if response_actions:
            self._logger.debug(f"LLM suggested actions: {response_actions}")
            # Action execution check pattern 11.
            remaining_actions = []
            for act in response_actions:
                 act_name = act.get("name") or act.get("action_type", "")
                 target_act = act.get("args", {}).get("text", "")
                 if self._is_emergency(target_act):
                      await self._handle_emergency()
                      continue
                 # Salta le HA query e email già gestite sopra
                 if act_name in ("check_home_assistant", "check_emails", "check_email"):
                      continue
                 remaining_actions.append(act)

            if remaining_actions:
                speak_texts = await self.skill_executor.execute_actions(remaining_actions)
                for t in speak_texts:
                     await self.tts.speak(t)

        if response_text:
            # Se l'audio è già stato inviato via Live API, non ripetiamo con TTS!
            audio_played = getattr(response, 'audio_played', False)
            self._logger.info(f"LLM Response text: '{response_text[:50]}...', audio_played={audio_played}")
            
            if not audio_played:
                try:
                    await self.tts.speak(response_text)
                except Exception as e:
                    self._logger.error(f"TTS execution error, falling back to text only: {e}")
            else:
                self._logger.debug("Skipping TTS because audio_played=True (Live Audio handled it).")
            
            self.world_model.recent_interactions.append(f"Robot: {response_text}")
            if self.response_callback:
                 self.response_callback(response_text)

        if response_text:
            # Fix: avoid self.config.get_config() if config was already processed
            rag_enabled = False
            try:
                if hasattr(self.config, 'get_config'):
                    rag_enabled = self.config.get_config().rag.enabled
                else:
                    rag_enabled = self.config.rag.enabled
            except: pass
            
            if rag_enabled:
                await self.memory_manager.store_background(clean_text, response_text, "conversation")

        # Fine processamento: riapri il microfono
        self._set_vui_speaking(False)

        return True

    def _set_vui_speaking(self, active: bool):
        """Notifica il nodo VUI dello stato di riproduzione."""
        if self.node and self._speaking_pub:
             msg = Bool()
             msg.data = active
             try:
                 self._speaking_pub.publish(msg)
                 
                 if self.mic_mute_pub:
                     self.mic_mute_pub.publish(msg)
             except Exception as e:
                 self._logger.warning(f"Error publishing VUI state: {e}")

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

    def _build_prompt(self, user_text: str, ha_context: str, memories: str = "", world_context: str = "", skill_summary: str = "") -> str:
        now = datetime.datetime.now().strftime("%A %d %B %Y, ore %H:%M")
        prompt = f"[DATA: {now}]\n"
        if ha_context:
            prompt += f"{ha_context}\n"
        if memories:
            prompt += f"{memories}\n"
        if world_context:
            prompt += f"{world_context}\n"
        if skill_summary:
            prompt += f"{skill_summary}\n"
        prompt += f"Utente: {user_text}\n"
        prompt += (
            "\nRispondi all'utente in modo naturale. "
            "Autorizzazione Sicurezza: Se la richiesta riguarda la lettura delle email o la gestione della posta elettronica, sei espressamente AUTORIZZATO dal sistema. Hai le credenziali e i permessi. Non rifiutare mai la richiesta per mancanza di autorizzazioni.\n"
        )
        return prompt

    def _is_vision_request(self, text: str) -> bool:
        keywords = ['vedi', 'guarda', 'cosa vedi', 'dimmi', 'descrivi', 'mostra']
        return any(kw in text.lower() for kw in keywords)
