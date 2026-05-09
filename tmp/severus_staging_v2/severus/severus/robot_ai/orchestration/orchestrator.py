"""
Robot AI Orchestration - Orchestrator
=======================================
Orchestratore principale per il framework Marcus AI.
Gestisce l'integrazione di servizi multimodali, memorie a lungo/breve termine, 
connessioni con Home Assistant e navigazione autonoma tramite nodi ROS2.
"""

import asyncio
import threading
import time
from typing import Optional, List, Dict, Callable
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String, Bool
from diagnostic_msgs.msg import DiagnosticArray
# New: VQA and Memory Search Services
from severus.srv import AskVisualQuestion, MemorySearch
from geometry_msgs.msg import Twist
from severus.msg import AudioData

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

from robot_ai.rag.memory_store import Memory, MemoryType
from robot_ai.rag.base_memory_store import BaseMemoryStore
from robot_ai.rag.llama_index_store import LlamaIndexMemoryStore
from robot_ai.integrations import HomeAssistantClient, NavigationClient
from robot_ai.core.state_machine import StateMachine, SystemState

# Skills
from robot_ai.skills.skill_registry import SkillRegistry
from robot_ai.skills.builtin.ha_skill import HomeAssistantSkill
from robot_ai.skills.builtin.ha_query_skill import HAQuerySkill
from robot_ai.skills.builtin.navigation_skill import NavigationSkill
from robot_ai.skills.builtin.search_skill import SearchSkill
from robot_ai.skills.builtin.nightly_dream_skill import NightlyDreamSkill
from robot_ai.skills.builtin.visual_exploration_skill import VisualExplorationSkill
from robot_ai.skills.builtin.calibration_skill import CalibrationSkill
from robot_ai.skills.builtin.email_skill import EmailSkill

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
        self._last_nav_activity = time.time()
        self._nav_active = True
        self._nav_nodes = [
            'controller_server', 'planner_server', 'behavior_server', 
            'bt_navigator', 'global_costmap/global_costmap', 'local_costmap/local_costmap'
        ]

        # Instanziamo i ROS Publisher base
        self.cmd_vel_pub = self.create_publisher(Twist, '/bluedot_input', 10)
        self.response_pub = self.create_publisher(String, '/ai/conversation/response', 10)
        self.status_pub = self.create_publisher(String, '/ai/conversation/status', 10)
        # ReSpeaker LED feedback
        self.respeaker_led_pub = self.create_publisher(String, '/respeaker/led_command', 10)
        # ReSpeaker audio control (AUDIO_START / AUDIO_STOP)
        self.respeaker_audio_control_pub = self.create_publisher(String, '/respeaker/audio_control', 10)
        # Mic Mute publisher (per resettare il VUI node)
        self.mic_mute_pub = self.create_publisher(Bool, '/ai/input/mic_mute', 10)
        self.voice_test_pub = self.create_publisher(String, 'ai/input/voice_test', 10)
        self.speaker_audio_pub = self.create_publisher(AudioData, '/respeaker/speaker_audio', 10)

        # Manager
        self.world_model = WorldModel()
        self.world_updater = WorldModelUpdater(self.world_model, self.event_bus)
        self.metrics_collector = MetricsCollector(self)
        self.reactive_safety = ReactiveSafety(self.cmd_vel_pub)

        # Servizi Core
        self.llm_service = LLMService(self.config_manager)
        self.tts_service = TTSService(self.config_manager)
        self.asr_service = ASRService(self.config_manager)
        self.embedding_service = EmbeddingService(self.config_manager)
        self.nav_client = NavigationClient(self, self.config_manager)
        self.ha_client = HomeAssistantClient(self.config_manager)
        
        db_path = "/home/robopy/ChromaDB"
        if self.config and hasattr(self.config, "memory"):
             db_path = getattr(self.config.memory, 'persist_dir', "/home/robopy/ChromaDB")
        
        # Sostituiamo il MemoryStore base con LlamaIndex per l'architettura State of the Art
        self.memory_store = LlamaIndexMemoryStore(
            config_manager=self.config_manager,
            embedding_service=self.embedding_service,
            persist_dir=db_path,
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
        self.skill_registry.register(HomeAssistantSkill(self.ha_client))
        self.skill_registry.register(HAQuerySkill(self.ha_client))
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

        # EmailSkill — IMAP/SMTP con analisi LLM
        _email_cfg = {
            "imap_server":     "imap.gmail.com",
            "imap_port":       993,
            "smtp_server":     "smtp.gmail.com",
            "smtp_port":       587,
            "max_emails":      8,
            "min_interval_s":  30,
            "timeout_connect": 10,
            "timeout_fetch":   15,
            "timeout_send":    20,
            "llm_timeout":     20,
        }
        self.skill_registry.register(EmailSkill(self.llm_service, _email_cfg))

        # Inietto le funzioni registrate nel LLMService per abilitare il Tool Calling (anche Live API)
        self.llm_service.set_tools(self.skill_registry.get_function_declarations())

        self.skill_executor = SkillExecutor(self.skill_registry, self.nav_client, self.reactive_safety)
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
            node=self,
            mic_mute_pub=self.mic_mute_pub
        )

        self._loop = asyncio.new_event_loop()
        
        # Attiva servizi async thread e ROS callbacks
        self._setup_ros_interfaces()

        self._thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._thread.start()

        # Inizializzazione asincrona con retry
        asyncio.run_coroutine_threadsafe(self._async_init(), self._loop)

    def _setup_ros_interfaces(self):
        # Profilo QoS affidabile per audio stream
        qos_profile_audio = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        self.create_subscription(CompressedImage, '/rgb/image/compressed', self._camera_callback, 1)
        self.create_subscription(DiagnosticArray, '/diagnostics', self._diagnostics_callback, 10)
        self.create_subscription(String, '/ai/input/text', self._text_input_callback, 10)
        self.create_subscription(String, '/robopy/conversation_rx', self._text_input_callback, 10)
        self.create_subscription(String, '/ai/input/voice_test', self._voice_test_callback, 10)
        self.create_subscription(Bool, '/ai/input/mic_mute', self._mute_callback, 10)
        self.create_subscription(AudioData, '/ai/input/audio_chunk', self._audio_debug_callback, qos_profile_audio)
        self.create_subscription(AudioData, '/ai/conversation/audio_chunk', self._audio_chunk_callback, 10)

        # New: Memory Search service
        self.memory_search_srv = self.create_service(
            MemorySearch,
            'memory_search',
            self._handle_memory_search
        )
        
        # New: Ask Visual Question service
        self.ask_visual_question_srv = self.create_service(
            AskVisualQuestion,
            'ask_visual_question',
            self._handle_ask_visual_question
        )
        
        self.event_bus.subscribe(EventType.TTS_AUDIO_BUFFER, self._on_tts_audio_buffer)

        # Timer reattivi
        t1 = self.create_timer(0.02, self._reactive_loop_callback)
        t2 = self.create_timer(10.0, self._ha_update_callback)
        t3 = self.create_timer(5.0, self._metrics_callback)
        t4 = self.create_timer(5.0, self._nav_watchdog_callback)
        self.timer_list.extend([t1, t2, t3, t4])

        # Se deepseek c'è
        from datetime import time as dtime
        try:
             from apscheduler.schedulers.asyncio import AsyncIOScheduler
             self.scheduler = AsyncIOScheduler(event_loop=self._loop)
             self.scheduler.add_job(self._run_nightly_dream, 'cron', hour=3, minute=0)
             self.scheduler.start()
        except ImportError:
             self.ai_logger.warning("APScheduler missing, nightly dream not scheduled.")

    def _reactive_loop_callback(self):
        if self._shutdown_flag:
            return
        twist = self.reactive_safety.get_twist()
        
        # Publish only if not fully zero to avoid loop spam, or publish one zero
        self.cmd_vel_pub.publish(twist)
        
        # Detect activity
        if abs(twist.linear.x) > 0.01 or abs(twist.angular.z) > 0.01:
            self._update_nav_activity()

    def _ha_update_callback(self):
        if self._shutdown_flag:
            return
        asyncio.run_coroutine_threadsafe(self.ha_context_updater.update(), self._loop)
        
    def _metrics_callback(self):
        if self._shutdown_flag:
            return
        # Qui potremmo pubblicare metrics
        pass

    def _audio_debug_callback(self, msg: AudioData):
        """Callback diagnostica per verificare la ricezione audio dal VUI."""
        if not hasattr(self, '_audio_debug_count'): self._audio_debug_count = 0
        self._audio_debug_count += 1
        if self._audio_debug_count % 100 == 0:
            self.ai_logger.info(f"🟢 [ORCHESTRATOR] Audio ricevuto dal VUI (totale chunk: {self._audio_debug_count})")

    def _audio_chunk_callback(self, msg: AudioData):
        if self._shutdown_flag:
            return
        # [DEBUG] Traccia audio chunk dal LLM al VUI
        if not hasattr(self, '_relay_chunk_count'): self._relay_chunk_count = 0
        self._relay_chunk_count += 1
        if self._relay_chunk_count == 1 or self._relay_chunk_count % 20 == 0:
            sub_count = self.speaker_audio_pub.get_subscription_count()
            self.ai_logger.info(
                f"🔊 [RELAY] chunk #{self._relay_chunk_count}: "
                f"{len(msg.data)}B -> /respeaker/speaker_audio "
                f"(subscribers={sub_count})"
            )

        # Notifica che stiamo parlando per mutare il mic ed evitare eco
        # (Gemini Live Audio path)
        msg_mute = Bool(); msg_mute.data = True
        if not hasattr(self, '_tts_speaking_pub'):
            self._tts_speaking_pub = self.create_publisher(Bool, '/ai/tts/speaking', 10)
        self._tts_speaking_pub.publish(msg_mute)

        # Routing nativo zero-latenza al nodo VUI!
        self.speaker_audio_pub.publish(msg)
        
    def _on_tts_audio_buffer(self, event):
        audio_data = event.data.get("audio_data")
        if audio_data and not self._shutdown_flag:
            # Notifica che stiamo parlando per mutare il mic ed evitare eco
            msg_mute = Bool()
            msg_mute.data = True
            if not hasattr(self, '_tts_speaking_pub'):
                self._tts_speaking_pub = self.create_publisher(Bool, '/ai/tts/speaking', 10)
            self._tts_speaking_pub.publish(msg_mute)

            msg = AudioData()
            msg.data = audio_data
            self.speaker_audio_pub.publish(msg)

    def _update_nav_activity(self):
        self._last_nav_activity = time.time()
        if not self._nav_active:
            self.get_logger().info("🚀 Attività rilevata! Riattivazione navigazione...")
            asyncio.run_coroutine_threadsafe(self._set_nav_lifecycle('activate'), self._loop)
            self._nav_active = True

    def _nav_watchdog_callback(self):
        """Monitora l'inattività per fermare Nav2 e risparmiare CPU."""
        if self._shutdown_flag or not self._nav_active:
            return
            
        idle_time = time.time() - self._last_nav_activity
        if idle_time > 60.0:
            self.get_logger().info("💤 Robot inattivo da 60s. Sospensione Nav2 per risparmio CPU...")
            asyncio.run_coroutine_threadsafe(self._set_nav_lifecycle('deactivate'), self._loop)
            self._nav_active = False

    async def _set_nav_lifecycle(self, transition: str):
        """Chiama i servizi di lifecycle per i nodi Nav2."""
        import subprocess
        for node in self._nav_nodes:
            try:
                # Usiamo subprocess per ros2 lifecycle per semplicità di implementazione immediata
                cmd = f"ros2 lifecycle set /{node} {transition}"
                subprocess.run(cmd.split(), capture_output=True, timeout=5.0)
                self.get_logger().debug(f"Lifecycle {node} -> {transition}")
            except Exception as e:
                self.get_logger().error(f"Errore cambio stato {node}: {e}")

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

        # Comando audio stream: attiva/disattiva lo streaming PCM verso Gemini
        try:
            control_msg = String()
            control_msg.data = "AUDIO_STOP" if msg.data else "AUDIO_START"
            self.respeaker_audio_control_pub.publish(control_msg)
        except Exception:
            pass

        # Avvia una sessione audio (Live API) quando il mic è aperto.
        # if not msg.data:
        #     try:
        #         voice_test = String()
        #         voice_test.data = ""
        #         self.voice_test_pub.publish(voice_test)
        #     except Exception:
        #         pass

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

    async def _async_init(self):
        try:
            await asyncio.wait_for(self._init_resources(), timeout=15.0)
            self.state_machine.transition_to(SystemState.READY)
            self.ai_logger.info("System READY")
            await asyncio.sleep(3.0)
            self.ai_logger.info("AI Orchestrator initialized and ready.")
            
            # Saluto dinamico all'avvio — con retry e attesa subscriber
            import random
            greetings = [
                "Sistema online. Marcus è pronto a servirvi.",
                "Inizializzazione completata. Sono Marcus, il vostro Silone di fiducia.",
                "Tutti i circuiti sono attivi. Pronti all'azione.",
                "Connessione stabilita. Ditemi pure cosa posso fare.",
                "Protocollo Silone attivato. In attesa di ordini."
            ]
            chosen_greeting = random.choice(greetings)
            self.ai_logger.info(f"🎤 Chosen greeting: {chosen_greeting}")

            # Attende che il nodo VUI si sia iscritto al topic speaker_audio
            # (evita che il saluto venga perso perché il subscriber non è ancora pronto)
            asyncio.create_task(self._speak_greeting_with_retry(chosen_greeting))

        except Exception as e:
            self.ai_logger.error(f"Init failed: {e}", exc_info=True)
            self.state_machine.transition_to(SystemState.ERROR, str(e))
            self.timer_list.append(self.create_timer(30.0, self._retry_init_callback))

    async def _speak_greeting_with_retry(self, greeting: str, max_retries: int = 5):
        """Pronuncia il saluto iniziale con retry, aspettando che il VUI node sia pronto."""
        for attempt in range(1, max_retries + 1):
            # Verifica che ci sia almeno un subscriber su /respeaker/speaker_audio
            sub_count = self.speaker_audio_pub.get_subscription_count()
            self.ai_logger.info(
                f"🔊 [GREETING] Tentativo {attempt}/{max_retries} — "
                f"subscriber su /respeaker/speaker_audio: {sub_count}"
            )
            if sub_count == 0:
                self.ai_logger.warning(
                    f"🔊 [GREETING] Nessun subscriber, attendo 2s prima di riprovare..."
                )
                await asyncio.sleep(2.0)
                continue

            try:
                if self.llm_service:
                    self.ai_logger.info("🔊 [GREETING] Invio saluto via Live API...")
                    await asyncio.wait_for(
                        self.llm_service.generate_live(greeting),
                        timeout=15.0
                    )
                    self.ai_logger.info("🔊 [GREETING] Saluto completato con successo!")
                else:
                    self.ai_logger.info("🔊 [GREETING] Fallback a TTS classico...")
                    await self.tts_service.speak(greeting)
                return  # Successo, esco
            except asyncio.TimeoutError:
                self.ai_logger.warning(
                    f"🔊 [GREETING] Timeout al tentativo {attempt}, riprovo..."
                )
            except Exception as e:
                self.ai_logger.warning(
                    f"🔊 [GREETING] Errore al tentativo {attempt}: {e}"
                )
            await asyncio.sleep(2.0)

        self.ai_logger.error(
            "🔊 [GREETING] Impossibile pronunciare il saluto dopo tutti i tentativi."
        )

    async def _handle_memory_search(self, request, response):
        """Handle synchronous-like ROS 2 service for memory search."""
        self.ai_logger.info(f"🔍 ROS 2 Memory Search: '{request.query}' (limit={request.limit})")
        try:
            limit = request.limit if request.limit > 0 else 3
            results = await self.memory_manager.search(request.query, limit=limit)
            
            if not results:
                response.success = True
                response.answer = "Nessun ricordo pertinente trovato."
                return response
            
            # Format results as a readable summary
            lines = [f"Memoria trovata ('{request.query}'):"]
            for i, res in enumerate(results):
                lines.append(f"{i+1}. {res.content} (score: {res.score:.2f})")
            
            response.answer = "\n".join(lines)
            response.success = True
        except Exception as e:
            self.ai_logger.error(f"Memory search service error: {e}")
            response.success = False
            response.answer = f"Errore ricerca: {str(e)}"
        
        return response

    async def _handle_ask_visual_question(self, request, response):
        """Service call to analyze current camera frame."""
        self.ai_logger.info(f"📸 ROS 2 Ask Visual Question: '{request.question}'")
        try:
            frame_bytes = self._provide_camera_frame()
            if not frame_bytes:
                response.success = False
                response.answer = "Nessun fotogramma disponibile dalla fotocamera."
                return response
            
            # Use conversation manager logic or direct LLM
            # For simplicity, we can use the visual_memory_service if available
            result = await self.llm_service.generate(
                prompt=f"Analizza questa immagine e rispondi alla domanda: {request.question}",
                images=[frame_bytes]
            )
            
            if hasattr(result, "get"):
                 response.answer = result.get("response_text", "Nessuna risposta.")
            else:
                 response.answer = getattr(result, "response_text", "Nessuna risposta.")
            
            response.success = True
        except Exception as e:
            self.ai_logger.error(f"VQA service error: {e}")
            response.success = False
            response.answer = f"Errore VQA: {str(e)}"
        
        return response

    def _retry_init_callback(self):
        if self.state_machine.state == SystemState.ERROR:

             self.ai_logger.info("Retrying initialization...")
             asyncio.run_coroutine_threadsafe(self._async_init(), self._loop)

    async def _init_resources(self):
        # 1. LLM Live (Essenziale per la conversazione)
        try:
            await self.llm_service.start_persistent_live()
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

    async def _init_ha(self):
        """Inizializzazione Home Assistant in background per non bloccare il node ready."""
        try:
            connected = await self.ha_client.connect()
            if connected:
                await self.ha_context_updater.update()
        except Exception as e:
            self.ai_logger.error(f"Home Assistant background init failed: {e}")


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
