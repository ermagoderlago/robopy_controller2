import asyncio
import time
import re
import datetime
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

        # Dopamine Biometric Alignment System
        self.cognitive_graph = MarcusStateGraph(
            memory_store=self.memory_manager.memory_store,
            embedding_service=self.memory_manager.embedding_service
        )
        self.agent_state = MarcusAgentState()
        self._current_source = ""

        # Dynamically wrap speak to respect chat silencing
        original_speak = self.tts.speak
        async def wrapped_speak(text, *args, **kwargs):
            if getattr(self, '_current_source', '') == "text":
                self._logger.info(f"[MUTE] Chat silenziata: non riproduco '{text}' vocalmente")
                return
            return await original_speak(text, *args, **kwargs)
        self.tts.speak = wrapped_speak

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
        await self.tts.speak("Fermo tutto!")

    async def process_input(self, text: str, source: str):
        self._current_source = source
        self.metrics.inc_requests_total()

        if self._is_emergency(text):
            await self._handle_emergency()
            self.metrics.inc_requests_success()
            return

        if self._mic_muted and source == "audio":
            self._logger.debug("Mic muted, ignoring audio input.")
            return

        # [v15.2] Ignora input di testo duplicati generati da ASR client-side se già gestiti dalla Live API
        is_duplicate = False
        if hasattr(self.llm, 'is_duplicate_text'):
            is_duplicate = self.llm.is_duplicate_text(text)

        if source == "text" and is_duplicate:
            self._logger.info(f"Ignorato input testuale duplicato ASR: '{text}' (duplicato={is_duplicate})")
            self.metrics.inc_requests_success()
            return

        async with self._processing_lock:
            success = await self._process_locked(text, source)
            if success:
                self.metrics.inc_requests_success()
            else:
                self.metrics.inc_requests_failed()

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
        clean_text = self._sanitizer.sanitize(text)
        self.world_model.recent_interactions.append(f"User: {clean_text}")

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
                        await self.tts.speak(f"Va bene, inizio a imparare il volto di {name_candidate.capitalize()}. Guarda la telecamera per favore.")
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
                    await self.tts.speak(f"Va bene, inizio a registrare le impronte della tua voce come {name_candidate.capitalize()}. Continua a parlarmi per favore.")
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
        skill = self.skill_executor.find_best_match(clean_text, min_confidence=0.95)
        if skill:
            self._logger.info(f"Fast-path skill match: {skill.name}")
            try:
                texts = await self.skill_executor.execute_skill(skill.name, {"text": clean_text})
                for t in texts:
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
            return True

        # Offline fallback
        if self.metrics.connectivity_state == "OFFLINE":
            await self.tts.speak("Sono offline, riprova più tardi.")
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

        # Active RAG Semantic Memory Retrieval
        rag_memories = []
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

        augmented_prompt = self._build_prompt(clean_text, ha_context, repeated_prompt_note, rag_memories)

        # Timeout dall'oggetto config.llm.timeout (di base accesskey)
        llm_timeout = 20.0
        try:
             llm_timeout = float(self.config.get_config().llm.timeout)
        except:
             pass

        # Temporarily inject biomimetic prompt override into LLM
        original_sys_prompt = self.llm._system_prompt
        self.llm.set_system_prompt(self.agent_state.system_prompt_override)

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
        except asyncio.TimeoutError:
            self.metrics.record_llm_error("timeout")
            self._logger.error("LLM timeout both Live and Standard.")
            await self.tts.speak("Non riesco a connettermi al cervello.")
            return False
        except Exception as e:
            self.metrics.record_llm_error("unexpected")
            self._logger.error(f"LLM call failed: {e}", exc_info=True)
            return False
        finally:
            # Restore the original system prompt
            self.llm.set_system_prompt(original_sys_prompt)

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

        if explicit_actions:
            self._logger.debug(f"LLM suggested actions: {explicit_actions}")
            for act in explicit_actions:
                 target_act = act.get("args", {}).get("text", "")
                 if self._is_emergency(target_act):
                      await self._handle_emergency()
                      continue

            try:
                async for t in self.skill_executor.execute_actions_stream(explicit_actions):
                      if not is_live:
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
                if not is_live:
                    err_msg = "Uhm... Dunque, scusami, ho avuto un piccolo intoppo nell'eseguire questa azione."
                    await self.tts.speak(err_msg)
                if self.response_callback:
                     err_msg = "Uhm... Dunque, scusami, ho avuto un piccolo intoppo nell'eseguire questa azione."
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

        if response_text:
            if not is_live:
                await self.tts.speak(response_text)
            self.world_model.recent_interactions.append(f"Robot: {response_text[:50]}...")
            if self.response_callback:
                 self.response_callback(response_text)

        if response_text and self.config.get_config().rag.enabled:
            is_factual = bool(re.search(r'\b(significa|acronimo|definizione|ricordati|mi chiamo|chiamami|impara|nota)\b', clean_text, re.IGNORECASE))
            mem_type = "learned_fact" if is_factual else "conversation"
            await self.memory_manager.store_background(clean_text, response_text, mem_type)

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

    def _build_prompt(self, user_text: str, ha_context: str, repeated_note: str = "", rag_memories: list = None) -> str:
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
                
        prompt += f"Utente: {user_text}\n"
        return prompt

    def _is_vision_request(self, text: str) -> bool:
        keywords = ['vedi', 'guarda', 'cosa vedi', 'dimmi', 'descrivi', 'mostra']
        return any(kw in text.lower() for kw in keywords)
