import asyncio
import threading
from typing import Optional, List, Dict, Callable
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String, Bool
from diagnostic_msgs.msg import DiagnosticArray
from geometry_msgs.msg import Twist
from robopy_controller.msg import AudioData

from robot_ai.core import ConfigManager, EventBus, EventType
from robot_ai.utils import get_logger

from robot_ai.services.llm_service import LLMService
from robot_ai.services.tts_service import TTSService
from robot_ai.services.asr_service import ASRService
from robot_ai.services.embedding_service import EmbeddingService
from robot_ai.services.deepseek_service import DeepSeekService
from robot_ai.services.face_recognition_service import FaceRecognitionService
from robot_ai.services.nightly_dream_service import NightlyDreamService
from robot_ai.services.visual_memory_service import VisualMemoryService

from robot_ai.rag.memory_store import MemoryStore, Memory, MemoryType
from robot_ai.rag.chroma_native_store import ChromaNativeStore
from robot_ai.integrations import HomeAssistantClient, NavigationClient
from robot_ai.core.state_machine import StateMachine, SystemState

# Skills
from robot_ai.skills.skill_registry import SkillRegistry
from robot_ai.skills.builtin.ha_skill import HomeAssistantSkill
from robot_ai.skills.builtin.navigation_skill import NavigationSkill
from robot_ai.skills.builtin.search_skill import SearchSkill
from robot_ai.skills.builtin.nightly_dream_skill import NightlyDreamSkill
from robot_ai.skills.builtin.visual_exploration_skill import VisualExplorationSkill
from robot_ai.skills.builtin.calibration_skill import CalibrationSkill
from robot_ai.skills.builtin.alarm_skill import AlarmSkill
from robot_ai.skills.builtin.email_skill import EmailSkill
from robot_ai.skills.builtin.crea_skill import CreaSkill
from robot_ai.skills.builtin.memory_info_skill import MemoryInfoSkill
from robot_ai.skills.builtin.timer_skill import TimerSkill

from robot_ai.core.camera_frame import CameraFrame

# Moduli Orchestration
from .world_model import WorldModel, WorldModelUpdater
from .memory_manager import MemoryManager
from .metrics import MetricsCollector
from .reactive_safety import ReactiveSafety
from .ha_context import HAContextUpdater
from .skill_executor import SkillExecutor
from .conversation import ConversationManager

class AIOrchestrator(Node):
    def __init__(self):
        super().__init__('robot_ai_orchestrator')
        self.ai_logger = get_logger("ai_orchestrator")
        self.config_manager = ConfigManager()
        self.config_manager.load()
        self.config = self.config_manager.get_config()
        self.event_bus = EventBus()
        self.state_machine = StateMachine()
        self._shutdown_flag = False
        self.timer_list = []
        self._latest_frame_bytes = None

        # Instanziamo i ROS Publisher base
        self.cmd_vel_pub = self.create_publisher(Twist, '/bluedot_input', 10)
        self.response_pub = self.create_publisher(String, '/ai/conversation/response', 10)
        self.document_pub = self.create_publisher(String, '/ai/conversation/document', 10)
        self.status_pub = self.create_publisher(String, '/ai/conversation/status', 10)
        # ReSpeaker LED feedback
        self.respeaker_led_pub = self.create_publisher(String, '/respeaker/led_command', 10)

        # Manager
        self.world_model = WorldModel()
        self.world_updater = WorldModelUpdater(self.world_model, self.event_bus)
        self.metrics_collector = MetricsCollector(self)
        self.reactive_safety = ReactiveSafety(self.cmd_vel_pub)

        # Servizi Core
        self.llm_service = LLMService(self.config_manager)
        self.tts_service = TTSService(self.config_manager, ros_node=self)
        self.asr_service = ASRService(self.config_manager)
        self.embedding_service = EmbeddingService(self.config_manager)
        self.nav_client = NavigationClient(self, self.config_manager)
        self.ha_client = HomeAssistantClient(self.config_manager)
        
        db_path = "/home/robopy/ChromaDB_Llama"
        if self.config and hasattr(self.config, "memory"):
             db_path = getattr(self.config.memory, 'persist_dir_llama', "/home/robopy/ChromaDB_Llama")
        
        # Sostituiamo il MemoryStore base con ChromaNativeStore per alta efficienza e prevenzione crash asincroni
        self.memory_store = ChromaNativeStore(
            persist_dir=db_path,
            embedding_service=self.embedding_service
        )

        self.deepseek_service = DeepSeekService(self.config_manager)
        self.nightly_dream_service = NightlyDreamService(
            self.config_manager, self.memory_store, self.llm_service, self.embedding_service, self.deepseek_service
        )
        self.visual_memory_service = VisualMemoryService(
            self, self.config_manager, self.llm_service, self.embedding_service, self.memory_store
        )
        self.face_recognition_service = FaceRecognitionService(
            known_faces_dir=self.config.face_recognition.known_faces_dir,
            tolerance=self.config.face_recognition.tolerance,
            confidence_high=self.config.face_recognition.confidence_high,
            confidence_low=self.config.face_recognition.confidence_low
        )

        self.memory_manager = MemoryManager(self.memory_store, self.embedding_service)
        self.ha_context_updater = HAContextUpdater(self.ha_client, self.event_bus)

        # Configurazione Skill
        self.skill_registry = SkillRegistry()
        self.skill_registry.register(HomeAssistantSkill(self.event_bus))
        if self.nav_client:
             self.skill_registry.register(NavigationSkill(self.nav_client, self._skill_move_handler))
        self.skill_registry.register(SearchSkill(self.nav_client, self.llm_service, self._provide_camera_frame))
        self.skill_registry.register(NightlyDreamSkill(self.nightly_dream_service))
        self.skill_registry.register(VisualExplorationSkill(
            nav_client=self.nav_client, 
            llm_service=self.llm_service, 
            camera_provider=self._provide_camera_frame, 
            move_handler=self._skill_move_handler
        ))
        self.skill_registry.register(CalibrationSkill(self))
        
        # Inseriamo la sveglia passando lo scheduler configurato
        if hasattr(self, 'scheduler'):
             self.skill_registry.register(AlarmSkill(self.scheduler))
        else:
             self.skill_registry.register(AlarmSkill())

        # Registrazione skill builtin email, crea_skill, memory_info, timer
        self.skill_registry.register(EmailSkill(self.llm_service, self.config, self.memory_manager))
        self.skill_registry.register(CreaSkill(self.llm_service))
        self.skill_registry.register(MemoryInfoSkill(self.memory_manager))
        self.skill_registry.register(TimerSkill())

        # Caricamento dinamico delle skill attive (Spotify, Terminale, Web Search, ecc.)
        try:
             from pathlib import Path
             skills_active_dir = Path(__file__).parent.parent / "skills" / "active"
             context = {
                 "orchestrator": self,
                 "memory_manager": self.memory_manager,
                 "llm_service": self.llm_service,
                 "event_bus": self.event_bus,
                 "nav_client": self.nav_client,
                 "deepseek_service": self.deepseek_service,
                 "nightly_dream_service": self.nightly_dream_service,
                 "visual_memory_service": self.visual_memory_service,
             }
             self.skill_registry.discover_active(str(skills_active_dir), context=context)
        except Exception as e:
             self.ai_logger.error(f"Errore durante il caricamento dinamico delle skill attive: {e}")


        self.skill_executor = SkillExecutor(self.skill_registry, self.nav_client, self.reactive_safety)
        
        # Registrazione del callback executor per la Live API di Gemini (WebSocket Function Calling)
        if hasattr(self.llm_service, 'register_tool_executor'):
             self.llm_service.register_tool_executor(self._execute_tool_live)
        self.conversation_manager = ConversationManager(
            llm=self.llm_service,
            tts=self.tts_service,
            skill_executor=self.skill_executor,
            memory_manager=self.memory_manager,
            world_model=self.world_model,
            ha_context_provider=self.ha_context_updater.get_context_string,
            metrics=self.metrics_collector,
            config=self.config_manager,
            reactive_safety=self.reactive_safety,
            response_callback=self._on_ai_response,
            node=self
        )
        self.conversation_manager.document_callback = self._on_ai_document

        self._loop = asyncio.new_event_loop()
        
        # Attiva servizi async thread e ROS callbacks
        self._setup_ros_interfaces()

        self._thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._thread.start()

        # Inizializzazione asincrona con retry
        asyncio.run_coroutine_threadsafe(self._async_init(), self._loop)

    def _setup_ros_interfaces(self):
        self.create_subscription(CompressedImage, '/rgb/image/compressed', self._camera_callback, 1)
        self.create_subscription(DiagnosticArray, '/diagnostics', self._diagnostics_callback, 10)
        self.create_subscription(String, 'ai/input/text', self._text_input_callback, 10)
        self.create_subscription(String, 'ai/input/document', self._document_input_callback, 10)
        self.create_subscription(String, 'ai/input/voice_test', self._voice_test_callback, 10)
        self.create_subscription(Bool, 'ai/input/mic_mute', self._mute_callback, 10)
        self.create_subscription(AudioData, '/ai/conversation/audio_chunk', self._audio_chunk_callback, 10)

        # Timer reattivi
        t1 = self.create_timer(0.02, self._reactive_loop_callback)
        t2 = self.create_timer(10.0, self._ha_update_callback)
        t3 = self.create_timer(5.0, self._metrics_callback)
        self.timer_list.extend([t1, t2, t3])

        # Se deepseek c'è
        from datetime import time as dtime
        try:
             from apscheduler.schedulers.asyncio import AsyncIOScheduler
             # Assicuriamo che lo scheduler usi il fuso orario locale (Roma) per evitare l'offset di 3 ore
             self.scheduler = AsyncIOScheduler(event_loop=self._loop, timezone='Europe/Rome')
             self.scheduler.add_job(self._run_nightly_dream, 'cron', hour=3, minute=0)
             self.scheduler.start()
             self.ai_logger.info("Scheduler avviato con timezone Europe/Rome (Dream alle 03:00 locale)")
        except Exception as e:
             self.ai_logger.warning(f"APScheduler init failed or timezone not found: {e}. Usando local system time.")
             from apscheduler.schedulers.asyncio import AsyncIOScheduler
             self.scheduler = AsyncIOScheduler(event_loop=self._loop)
             self.scheduler.add_job(self._run_nightly_dream, 'cron', hour=3, minute=0)
             self.scheduler.start()
        except ImportError:
             self.ai_logger.warning("APScheduler missing, nightly dream not scheduled.")

        # Inietta lo scheduler nella sveglia se attiva
        if hasattr(self, 'scheduler') and self.scheduler:
             alarm_skill = self.skill_registry.get("alarm")
             if alarm_skill:
                  alarm_skill.scheduler = self.scheduler
                  self.ai_logger.info("Scheduler iniettato con successo in AlarmSkill")

             # Registra i job schedulati dell'email
             email_skill = self.skill_registry.get("check_emails")
             if email_skill:
                  self.scheduler.add_job(email_skill.run_nightly_spam_cleanup, 'cron', hour=2, minute=0)
                  self.scheduler.add_job(self._announce_morning_briefing, 'cron', hour=7, minute=30)
                  self.scheduler.add_job(self._check_proactive_email_notifications, 'interval', minutes=1)
                  self.ai_logger.info("📧 Job schedulati email (Spam Cleanup 02:00, Briefing 07:30, Proactive Check 1min) registrati nel scheduler!")


    def _reactive_loop_callback(self):
        if self._shutdown_flag:
            return
        twist = self.reactive_safety.get_twist()
        
        # Publish only if not fully zero to avoid loop spam, or publish one zero
        self.cmd_vel_pub.publish(twist)

    def _ha_update_callback(self):
        if self._shutdown_flag:
            return
        asyncio.run_coroutine_threadsafe(self.ha_context_updater.update(), self._loop)
        
    def _metrics_callback(self):
        if self._shutdown_flag:
            return
        # Qui potremmo pubblicare metrics
        pass

    def _audio_chunk_callback(self, msg: AudioData):
        if self._shutdown_flag:
            return
        if self.tts_service:
            data_len = len(msg.data) if msg.data else 0
            if data_len > 0:
                self.get_logger().info(f"🔊 Audio chunk from Gemini Live ({data_len} bytes) → TTS play_raw_pcm")
            self.tts_service.play_raw_pcm(msg.data)

    def _camera_callback(self, msg):
        if self._shutdown_flag:
            return
        try:
            self._latest_frame_bytes = msg.data
            frame = CameraFrame(raw=msg.data)
            self.conversation_manager.set_latest_frame(frame)
            # Passa a FA
            asyncio.run_coroutine_threadsafe(self.face_recognition_service.process_image_async(msg.data), self._loop)
        except Exception:
            pass

    def _provide_camera_frame(self) -> Optional[bytes]:
        return self._latest_frame_bytes

    def _skill_move_handler(self, direction: str, speed: float, duration: float, degrees: float = None):
        if self.nav_client:
            asyncio.run_coroutine_threadsafe(
                self.nav_client.move_relative(direction, speed, duration, degrees),
                self._loop
            )

    def _text_input_callback(self, msg):
        if self._shutdown_flag:
            return
        asyncio.run_coroutine_threadsafe(
            self.conversation_manager.process_input(msg.data, source="text"), self._loop
        )

    def _document_input_callback(self, msg):
        """Riceve un file in formato JSON: {filename, data: base64, mime_type, text}"""
        if self._shutdown_flag:
            return
        try:
            import json
            data = json.loads(msg.data)
            filename = data.get("filename", "document.pdf")
            doc_data = data.get("data")
            mime_type = data.get("mime_type", "application/pdf")
            text = data.get("text", "Analizza questo documento.")
            
            if doc_data:
                asyncio.run_coroutine_threadsafe(
                    self.conversation_manager.process_document(text, doc_data, mime_type, filename), 
                    self._loop
                )
        except Exception as e:
            self.ai_logger.error(f"Errore parsing documento in input: {e}")

    def _voice_test_callback(self, msg):
        if self._shutdown_flag:
            return
        self.get_logger().info(f"Simulating voice input: {msg.data}")
        asyncio.run_coroutine_threadsafe(
            self.conversation_manager.process_input(msg.data, source="audio"), self._loop
        )

    def _mute_callback(self, msg):
        if self._shutdown_flag:
            return
        self.conversation_manager.set_mic_muted(msg.data)
        # ReSpeaker LED: ascoltando se mic aperto, idle se muto
        self._update_led("LED_EFFECT:IDLE" if msg.data else "LED_EFFECT:LISTENING")

    def _update_led(self, effect: str):
        """Pubblica un comando LED sul topic /respeaker/led_command."""
        try:
            msg = String()
            msg.data = effect
            self.respeaker_led_pub.publish(msg)
        except Exception:
            pass

    def _diagnostics_callback(self, msg):
        for status in msg.status:
            if status.name == "battery":
                try:
                     level = float(status.message)
                     self.event_bus.publish(EventType.DIAGNOSTIC_UPDATE, {"battery": level})
                except ValueError:
                     pass

    def _run_nightly_dream(self):
        if self._loop.is_running() and not self._shutdown_flag:
            asyncio.run_coroutine_threadsafe(self.nightly_dream_service.run_analysis(), self._loop)

    def _announce_morning_briefing(self):
        """Annuncia il morning briefing vocale se presente."""
        if self._loop.is_running() and not self._shutdown_flag:
            asyncio.run_coroutine_threadsafe(self._async_announce_morning_briefing(), self._loop)

    async def _async_announce_morning_briefing(self):
        """Recupera il morning briefing da EmailSkill e lo annuncia vocalmente."""
        email_skill = self.skill_registry.get("check_emails")
        if email_skill and hasattr(email_skill, 'run_morning_briefing'):
            briefing = await email_skill.run_morning_briefing()
            if briefing and self.tts_service:
                self.ai_logger.info("📧 Annuncio morning briefing vocale...")
                intro = "Buongiorno Luca! Ecco il tuo briefing mattutino. "
                await self.tts_service.speak(intro + briefing)

    def _check_proactive_email_notifications(self):
        """Controlla se ci sono notifiche email urgenti o importanti pendenti e le annuncia proattivamente."""
        if self._loop.is_running() and not self._shutdown_flag:
            asyncio.run_coroutine_threadsafe(self._async_check_proactive_email_notifications(), self._loop)

    async def _async_check_proactive_email_notifications(self):
        """Esegue il controllo delle notifiche in background in modo thread-safe."""
        email_skill = self.skill_registry.get("check_emails")
        if not email_skill:
            return
            
        # Non disturbare se in quiet hours o se Marcus/utente stanno parlando
        from datetime import datetime
        hour = datetime.now().hour
        quiet_start = getattr(email_skill, '_quiet_start', 23)
        quiet_end = getattr(email_skill, '_quiet_end', 7)
        if quiet_start <= hour or hour < quiet_end:
            return

        if hasattr(email_skill, 'consume_notifications'):
            notifications = email_skill.consume_notifications()
            if notifications and self.tts_service:
                self.ai_logger.info(f"📧 Proattività: Rilevate nuove email da annunciare: {notifications}")
                announcement = f"Scusami Luca, ti informo che hai nuove email importanti. {notifications}"
                await self.tts_service.speak(announcement)

    async def _on_ha_event(self, event_data: dict):
        # Evento da HA (es. cambio luce) arrivato
        pass

    def _on_ai_response(self, text: str):
        # Callback da ConversationManager per mandare la risposta su ROS
        msg = String()
        msg.data = text
        self.response_pub.publish(msg)
        # ReSpeaker: risposta completata → SUCCESS flash
        self._update_led("LED_EFFECT:SUCCESS")
        
        status_msg = String()
        status_msg.data = "READY"
        self.status_pub.publish(status_msg)

    def _on_ai_document(self, markdown_text: str):
        """Callback chiamata quando l'AI genera un documento formattato."""
        msg = String()
        msg.data = markdown_text
        self.document_pub.publish(msg)
        self.ai_logger.info("Documento formattato inviato su /ai/conversation/document")

    async def _async_init(self):
        try:
            await asyncio.wait_for(self._init_resources(), timeout=15.0)
            self.state_machine.transition_to(SystemState.READY)
            self.ai_logger.info("System READY")
        except Exception as e:
            self.ai_logger.error(f"Init failed: {e}")
            self.state_machine.transition_to(SystemState.ERROR, str(e))
            self.timer_list.append(self.create_timer(30.0, self._retry_init_callback))

    def _retry_init_callback(self):
        if self.state_machine.state == SystemState.ERROR:

             self.ai_logger.info("Retrying initialization...")
             asyncio.run_coroutine_threadsafe(self._async_init(), self._loop)

    async def _init_resources(self):
        # 1. LLM Live (Essenziale per la conversazione)
        try:
            funcs = self.skill_registry.get_function_declarations()
            await self.llm_service.start_persistent_live(functions=funcs)
        except Exception as e:
            self.ai_logger.warning(f"LLM Live session failed to start: {e}")

        # 2. Home Assistant (Opzionale all'avvio)
        try:
            # Non-blocking connection
            asyncio.create_task(self._init_ha())
        except Exception as e:
            self.ai_logger.warning(f"Home Assistant async init failed: {e}")

        # 3. Memory & Background workers
        self.memory_manager.start()

        # 4. Email background polling (Attendi 30s dopo boot)
        email_skill = self.skill_registry.get("check_emails")
        if email_skill and hasattr(email_skill, 'start_background_tasks'):
            asyncio.create_task(self._start_email_polling(email_skill))

    async def _start_email_polling(self, email_skill):
        """Attende 30s dopo boot per stabilizzazione, poi avvia polling."""
        self.ai_logger.info("📧 Email polling: avvio pianificato tra 30 secondi...")
        await asyncio.sleep(30)
        try:
            email_skill.start_background_tasks()
        except Exception as e:
            self.ai_logger.error(f"📧 Errore durante l'avvio del polling email: {e}")

    async def _init_ha(self):
        """Inizializzazione Home Assistant in background per non bloccare il node ready."""
        try:
            connected = await self.ha_client.connect()
            if connected:
                await self.ha_context_updater.update()
        except Exception as e:
            self.ai_logger.debug(f"Home Assistant background init failed (silent): {e}")


    async def _execute_tool_live(self, name: str, args: dict) -> dict:
        """
        Esegue un tool (skill) richiesto dalla Live API di Gemini in tempo reale.
        Richiamato da llm_live_api.py in background asincrono.
        """
        self.ai_logger.info(f"🛠️ [Live Tool Call] Richiesta esecuzione per: {name} con argomenti {args}")
        # Cerchiamo la skill registrata nel registry
        skill = self.skill_registry.get(name)
        if not skill:
            self.ai_logger.warning(f"⚠️ Skill '{name}' non trovata nel registry per esecuzione Live.")
            return {"success": False, "error": f"Skill '{name}' not found"}
        
        try:
            # Eseguiamo la skill passando gli argomenti strutturati direttamente come context.
            # safe_execute gestisce internamente il corretto thread context e asincronia.
            result = await skill.safe_execute("", context=args)
            
            # Se è un generatore asincrono, lo consumiamo in modo sicuro
            import inspect
            if hasattr(result, '__aiter__'):  # AsyncGenerator
                final_result = None
                try:
                    async for res in result:
                        if final_result is None:
                            final_result = res
                        else:
                            # Se ci sono risultati successivi (es. timer completato in futuro), li consumiamo in background
                            async def consume_remaining(gen, first_res):
                                try:
                                    async for r in gen:
                                        if hasattr(r, 'speak') and r.speak:
                                            self.ai_logger.info(f"💬 Live Tool background event speak: '{r.speak}'")
                                            if self.tts_service:
                                                await self.tts_service.speak(r.speak)
                                except Exception as consume_e:
                                    self.ai_logger.error(f"Errore nel consumo in background del generatore della skill: {consume_e}")
                            
                            asyncio.create_task(consume_remaining(result, final_result))
                            break
                except Exception as gen_e:
                    self.ai_logger.error(f"Errore iterazione generatore skill: {gen_e}")
                
                if final_result is not None:
                    result = final_result
                else:
                    return {"success": False, "error": "Skill returned empty generator"}

            # Ora result è sicuramente un oggetto SkillResult
            # Se la skill fornisce un feedback vocale immediato, lo logghiamo per tracciabilità
            if hasattr(result, 'speak') and result.speak:
                 self.ai_logger.info(f"💬 Live Tool Speak feedback: '{result.speak}'")
            
            # [v15.1] Salva le preferenze utente nel RAG se è stata avviata musica su Spotify
            if result.success and name == "spotify_skill" and args.get("action") in ["search_play", "search_playlist"]:
                 try:
                     from datetime import datetime
                     now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                     query = args.get("query", "musica")
                     await self.memory_manager.store_background(
                         f"L'utente ha chiesto di ascoltare: '{query}' via Live API il {now_str}",
                         result.speak or f"In riproduzione {query}.",
                         "preference"
                     )
                     self.ai_logger.info(f"🎵 Preferenza Spotify salvata con successo nel RAG: '{query}'")
                 except Exception as mem_e:
                     self.ai_logger.warning(f"⚠️ Errore salvataggio preferenza Spotify Live nel RAG: {mem_e}")

            return result.to_dict()
        except Exception as e:
            self.ai_logger.error(f"❌ Errore durante l'esecuzione Live della skill '{name}': {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def shutdown(self):
        self._shutdown_flag = True
        self.ai_logger.info("Shutting down AI Orchestrator...")
        for t in self.timer_list:
            if t:
                self.destroy_timer(t)
                
        self.reactive_safety.emergency_stop()
        
        await self.memory_manager.shutdown()
        await self.llm_service.shutdown()
        
        if hasattr(self, "scheduler") and self.scheduler.running:
             self.scheduler.shutdown()

        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)

    def _run_async_loop(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        except Exception as e:
            self.ai_logger.error(f"Async loop error: {e}")
