import asyncio
import time
import re
import datetime
from robot_ai.utils import get_logger
from robot_ai.core.input_sanitizer import InputSanitizer
from robot_ai.core.camera_frame import CameraFrame

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
        self.metrics.inc_requests_total()

        if self._is_emergency(text):
            await self._handle_emergency()
            self.metrics.inc_requests_success()
            return

        if self._mic_muted and source == "audio":
            self._logger.debug("Mic muted, ignoring audio input.")
            return

        async with self._processing_lock:
            success = await self._process_locked(text, source)
            if success:
                self.metrics.inc_requests_success()
            else:
                self.metrics.inc_requests_failed()

    async def process_document(self, text: str, document_data: str, mime_type: str, filename: str):
        """Process an incoming document (PDF, TIFF, etc.)"""
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
        clean_text = self._sanitizer.sanitize(text)
        self.world_model.recent_interactions.append(f"User: {clean_text}")

        # Fast-path skill execution (e.g direct commands)
        skill = self.skill_executor.find_best_match(clean_text, min_confidence=0.95)
        if skill:
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
        images = [frame.b64()] if (frame and self._is_vision_request(clean_text)) else []
        
        # Merge incoming documents if any
        llm_docs = documents or []

        functions = [s.to_function_declaration() for s in self.skill_executor.get_all()]
        augmented_prompt = self._build_prompt(clean_text, ha_context)

        # Timeout dall'oggetto config.llm.timeout (di base accesskey)
        llm_timeout = 20.0
        try:
             llm_timeout = float(self.config.get("llm", {}).get("timeout", 20.0))
        except:
             pass

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
                            images=images,
                            documents=llm_docs
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
                            documents=llm_docs
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
                        documents=llm_docs
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
        
        formatted_doc = getattr(response, "formatted_document", None)
        if formatted_doc and self.document_callback:
            self.document_callback(formatted_doc)

        # Logic for Tool use if LLM suggests it (e.g. ask_visual_question)
        for action in response_actions:
            if action.get("name") == "ask_visual_question":
                question = action.get("args", {}).get("question", "")
                if question:
                    result = await self.ask_visual_question(question)
                    # Re-send to LLM with visual data context
                    self._logger.info(f"VQA Result obtained, re-querying LLM with context...")
                    vqa_prompt = f"{augmented_prompt}\n[ACTIVE SEARCH RESULT: {result}]\nUser: {user_text}"
                    # Re-generate without tools for final answer to avoid loops
                    final_resp = await self.llm.generate(prompt=vqa_prompt, images=images)
                    if hasattr(final_resp, "get"):
                        response_text = final_resp.get("response_text", "")
                    else:
                        response_text = getattr(final_resp, "response_text", "")

        if response_actions:
            self._logger.debug(f"LLM suggested actions: {response_actions}")
            # Action exection check pattern 11.
            for act in response_actions:
                 target_act = act.get("args", {}).get("text", "")
                 if self._is_emergency(target_act):
                      await self._handle_emergency()
                      continue

            speak_texts = await self.skill_executor.execute_actions(response_actions)
            for t in speak_texts:
                 await self.tts.speak(t)

        if response_text:
            await self.tts.speak(response_text)
            self.world_model.recent_interactions.append(f"Robot: {response_text[:50]}...")
            if self.response_callback:
                 self.response_callback(response_text)

        if response_text and self.config.get("rag", {}).get("enabled", False):
            await self.memory_manager.store_background(clean_text, response_text, "conversation")

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

    def _build_prompt(self, user_text: str, ha_context: str) -> str:
        # Usiamo l'ora locale del sistema (che deve essere sincronizzata su Europe/Rome)
        now = datetime.datetime.now().strftime("%A %d %B %Y, ore %H:%M")
        prompt = f"[DATA LOCALE: {now} (fuso orario: Europe/Rome)]\n"
        if ha_context:
            prompt += f"{ha_context}\n"
        prompt += f"Utente: {user_text}\n"
        prompt += "\nDevi generare un JSON strettamente strutturato con `response_text` e opzionale array di `actions` se le funzioni sono evocate.\n"
        prompt += "Se l'utente ti chiede di generare un rapporto, un documento, una tabella o una risposta formattata, usa il campo `formatted_document` nel JSON inserendo il contenuto in formato Markdown.\n"
        prompt += "Se hai bisogno di analizzare l'ambiente in dettaglio per rispondere a una domanda specifica, usa l'azione `ask_visual_question` con l'argomento `question`.\n"
        prompt += "Esempio azione: {\"name\": \"ask_visual_question\", \"args\": {\"question\": \"C'è una sedia rossa nella stanza?\"}}\n"
        return prompt

    def _is_vision_request(self, text: str) -> bool:
        keywords = ['vedi', 'guarda', 'cosa vedi', 'dimmi', 'descrivi', 'mostra']
        return any(kw in text.lower() for kw in keywords)
