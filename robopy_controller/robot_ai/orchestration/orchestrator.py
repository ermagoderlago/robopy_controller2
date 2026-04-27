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
import os
import datetime
from typing import Optional, List, Dict, Callable, Any
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String, Bool
from diagnostic_msgs.msg import DiagnosticArray
# New: VQA and Memory Search Services
from robopy_controller.srv import AskVisualQuestion, MemorySearch
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
from robot_ai.skills.builtin.crea_skill import CreaSkill
from robot_ai.skills.builtin.alarm_skill import AlarmSkill

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
        
        # [v7.0] Topic audio normalizzato per ReSpeaker VUI Node
        self.tts_audio_pub = self.create_publisher(AudioData, '/respeaker/speaker_audio', 10)

        # Manager
        self.world_model = WorldModel()
        self.world_updater = WorldModelUpdater(self.world_model, self.event_bus)
        self.metrics_collector = MetricsCollector(self)
        self.reactive_safety = ReactiveSafety(self.cmd_vel_pub)

        # Servizi Core
        self.llm_service = LLMService(self.config_manager, node=self)
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

        # CreaSkill — Meta-skill per generazione autonoma di nuove skill.
        # Richiede DI esplicita via register_with_deps() (costruttore con argomenti).
        self.skill_registry.register_with_deps(
            CreaSkill,
            llm_service=self.llm_service,
            node=self,
            memory_manager=self.memory_manager,
        )

        # AlarmSkill — Gestione sveglie con pianificatore integrato.
        self.skill_registry.register(
            AlarmSkill(
                memory_manager=self.memory_manager,
                llm_service=self.llm_service,
            )
        )

        # Skill generate autonomamente (da manifest active/) — carica quelle con enabled=true
        _n_active = self.skill_registry.discover_active()
        self.ai_logger.info(f"Caricate {_n_active} skill attive dal manifest.")

        # Inietto le funzioni registrate nel LLMService per abilitare il Tool Calling (anche Live API)
        self.llm_service.set_tools(self.skill_registry.get_function_declarations())
        
        # [v10.0] Bridge Context for Live Reconnections
        self.llm_service.set_live_context_provider(self.world_model.to_prompt_section)

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
        self.create_subscription(Bool, '/ai/input/mic_mute', self._mute_callback, 10)
        self.create_subscription(AudioData, '/ai/conversation/audio_chunk', self._audio_chunk_callback, 10)
        self.create_subscription(Bool, '/ai/barge_in', self._barge_in_callback, 10)

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
        self.event_bus.subscribe(EventType.LIVE_AUDIO_CHUNK, self._on_live_audio_chunk)
        self.event_bus.subscribe(EventType.LIVE_TURN_COMPLETE, self._on_live_turn_complete)

        # Timer reattivi
        t1 = self.create_timer(0.02, self._reactive_loop_callback)
        t2 = self.create_timer(10.0, self._ha_update_callback)
        t3 = self.create_timer(5.0, self._metrics_callback)
        t4 = self.create_timer(5.0, self._nav_watchdog_callback)
        self.timer_list.extend([t1, t2, t3, t4])

        # Se deepseek c'è
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
        pass

    def _audio_chunk_callback(self, msg: AudioData):
        """[v5.9] Diagnostica relay (via EventBus)."""
        if self._shutdown_flag:
            return
        if not hasattr(self, '_relay_chunk_count'): self._relay_chunk_count = 0
        self._relay_chunk_count += 1
        
    def _barge_in_callback(self, msg: Bool):
        """[v5.6] Gestisce il segnale di barge-in dal VUI node."""
        if not msg.data or self._shutdown_flag:
            return
        self.ai_logger.info("🎤 [BARGE-IN] Segnale ricevuto dal VUI — cancello turno LLM...")
        self.conversation_manager.cancel_current_turn()
        self._update_led("LED_EFFECT:LISTENING")
        
    def _on_tts_audio_buffer(self, event):
        """Riceve audio dal TTSService via EventBus e lo pubblica su ROS."""
        data = event.data
        audio_data = data.get("audio_data")
        if audio_data and not self._shutdown_flag:
            msg_mute = Bool()
            msg_mute.data = True
            if not hasattr(self, '_tts_speaking_pub'):
                self._tts_speaking_pub = self.create_publisher(Bool, '/ai/tts/speaking', 10)
            self._tts_speaking_pub.publish(msg_mute)

            msg = AudioData()
            msg.data = bytes(audio_data) # [OPTIM] bytes are faster and valid for uint8[]
            self.tts_audio_pub.publish(msg)

    def _on_live_audio_chunk(self, event):
        """[v5.8] Riceve audio live via EventBus."""
        if self._shutdown_flag:
            return
        data = event.data
        audio_data = data.get("audio_data")
        if not audio_data:
            return

        if not hasattr(self, '_tts_speaking_pub'):
            self._tts_speaking_pub = self.create_publisher(Bool, '/ai/tts/speaking', 10)
        msg_speaking = Bool()
        msg_speaking.data = True
        self._tts_speaking_pub.publish(msg_speaking)

        msg      = AudioData()
        msg.data = bytes(audio_data)
        self.tts_audio_pub.publish(msg)
        
        # [DEBUG] Log periodico del relay
        if not hasattr(self, '_audio_relay_count'): self._audio_relay_count = 0
        self._audio_relay_count += 1
        if self._audio_relay_count % 20 == 0:
            self.ai_logger.info(f"🔊 [RELAY] Inviato chunk audio #{self._audio_relay_count} a VUI node ({len(audio_data)} bytes)")

    def _on_live_turn_complete(self, event):
        """[v5.9] Gemini ha completato un turno autonomo."""
        data = event.data
        text = data.get("text", "")
        self.ai_logger.info(f"[LIVE-AUTONOMOUS] Risposta autonoma completata ({data.get('audio_chunks')} chunk audio, testo={len(text)} chars)")
        if text:
            self._on_ai_response(text)
        
        # Reset flag speaking
        msg_speaking = Bool()
        msg_speaking.data = False
        if hasattr(self, '_tts_speaking_pub'):
            self._tts_speaking_pub.publish(msg_speaking)

        asyncio.run_coroutine_threadsafe(self._delayed_vui_release(delay=1.2), self._loop)

    async def _delayed_vui_release(self, delay: float = 1.2):
        await asyncio.sleep(delay)
        if not self._shutdown_flag:
            self.conversation_manager._set_vui_speaking(False)

    def _update_nav_activity(self):
        self._last_nav_activity = time.time()
        if not self._nav_active:
            asyncio.run_coroutine_threadsafe(self._set_nav_lifecycle('activate'), self._loop)
            self._nav_active = True

    def _nav_watchdog_callback(self):
        if self._shutdown_flag or not self._nav_active:
            return
        idle_time = time.time() - self._last_nav_activity
        if idle_time > 60.0:
            asyncio.run_coroutine_threadsafe(self._set_nav_lifecycle('deactivate'), self._loop)
            self._nav_active = False

    async def _set_nav_lifecycle(self, transition: str):
        import subprocess
        for node in self._nav_nodes:
            try:
                cmd = f"ros2 lifecycle set /{node} {transition}"
                subprocess.run(cmd.split(), capture_output=True, timeout=5.0)
            except Exception:
                pass

    def _camera_callback(self, msg):
        if self._shutdown_flag:
            return
        try:
            self._latest_frame_bytes = msg.data
            frame = CameraFrame(raw=msg.data)
            self.conversation_manager.set_latest_frame(frame)
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

    def _mute_callback(self, msg):
        if self._shutdown_flag:
            return
        self.conversation_manager.set_mic_muted(msg.data)
        self._update_led("LED_EFFECT:IDLE" if msg.data else "LED_EFFECT:LISTENING")
        try:
            control_msg = String()
            control_msg.data = "AUDIO_STOP" if msg.data else "AUDIO_START"
            self.respeaker_audio_control_pub.publish(control_msg)
        except Exception:
            pass

    def _update_led(self, effect: str):
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

    def _on_ai_response(self, text: str):
        msg = String()
        msg.data = text
        self.response_pub.publish(msg)
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
            
            # Saluto dinamico all'avvio
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
            asyncio.create_task(self._speak_greeting_with_retry(chosen_greeting))

        except Exception as e:
            self.ai_logger.error(f"Init failed: {e}", exc_info=True)
            self.state_machine.transition_to(SystemState.ERROR, str(e))
            self.timer_list.append(self.create_timer(30.0, self._retry_init_callback))

    async def _speak_greeting_with_retry(self, greeting: str, max_attempts: int = 5):
        """Pronuncia il saluto iniziale."""
        self.ai_logger.info("🔊 [GREETING] Avvio pronuncia saluto iniziale tra 5s...")
        await asyncio.sleep(5.0)  # Attende che il sistema sia completamente inizializzato
        
        # [v6.2] Controllo ore di silenzio (Quiet Hours)
        # Se è notte (es. tra le 22:00 e le 08:00), non parlare spontaneamente.
        current_hour = datetime.datetime.now().hour
        if current_hour >= 22 or current_hour < 8:
            self.ai_logger.info(f"🌙 [GREETING] Ore di silenzio ({current_hour}:00). Saluto vocale soppresso.")
            self.conversation_manager._set_vui_speaking(False)
            return

        
        for attempt in range(max_attempts):
            try:
                if self.llm_service and self.llm_service._client:
                    self.ai_logger.info(f"🔊 [GREETING] Tentativo {attempt+1}/{max_attempts}: generazione saluto AI...")
                    response = await asyncio.wait_for(
                        self.llm_service.generate(
                            prompt=f"Saluta brevemente e presentati come {self.config.robot.name}. Sii amichevole e sintetico. Massimo 2 frasi.",
                            max_tokens=60
                        ),
                        timeout=15.0
                    )
                    if response and getattr(response, 'text', ''):
                        self.ai_logger.info(f"🔊 [GREETING] Testo ottenuto: '{response.text[:60]}'")
                        await self.tts_service.speak(response.text)
                        self.ai_logger.info("✅ [GREETING] Saluto pronunciato con successo!")
                        return
                    else:
                        self.ai_logger.warning(f"⚠️ [GREETING] Risposta AI vuota (tentativo {attempt+1})")
                else:
                    self.ai_logger.warning("⚠️ [GREETING] LLM non disponibile, uso saluto statico.")
                    break
            except asyncio.TimeoutError:
                self.ai_logger.warning(f"⚠️ [GREETING] Timeout LLM (tentativo {attempt+1}). Riprovo...")
                await asyncio.sleep(2.0)
            except Exception as e:
                self.ai_logger.warning(f"⚠️ [GREETING] Errore: {e}. Uso fallback statico.")
                break
        
        # Fallback statico: sempre garantito
        self.ai_logger.info(f"🔊 [GREETING] Pronuncio saluto statico: '{greeting}'")
        try:
            await self.tts_service.speak(greeting)
            self.ai_logger.info("✅ [GREETING] Saluto statico pronunciato.")
        except Exception as e:
            self.ai_logger.error(f"❌ [GREETING] Anche il TTS statico ha fallito: {e}")
        finally:
            await asyncio.sleep(1.5)
            self.conversation_manager._set_vui_speaking(False)


    async def _handle_memory_search(self, request, response):
        try:
            limit = request.limit if request.limit > 0 else 3
            results = await self.memory_manager.search(request.query, limit=limit)
            if not results:
                response.success = True
                response.answer = "Nessun ricordo pertinente trovato."
                return response
            lines = [f"Memoria trovata ('{request.query}'):"]
            for i, res in enumerate(results):
                lines.append(f"{i+1}. {res.content} (score: {res.score:.2f})")
            response.answer = "\n".join(lines)
            response.success = True
        except Exception as e:
            response.success = False
            response.answer = f"Errore ricerca: {str(e)}"
        return response

    async def _handle_ask_visual_question(self, request, response):
        try:
            frame_bytes = self._provide_camera_frame()
            if not frame_bytes:
                response.success = False
                response.answer = "Nessun fotogramma disponibile."
                return response
            result = await self.llm_service.generate(
                prompt=f"Analizza questa immagine e rispondi alla domanda: {request.question}",
                images=[frame_bytes]
            )
            response.answer = result.text if hasattr(result, 'text') else str(result)
            response.success = True
        except Exception as e:
            response.success = False
            response.answer = f"Errore VQA: {str(e)}"
        return response

    def _retry_init_callback(self):
        if self.state_machine.state == SystemState.ERROR:
             asyncio.run_coroutine_threadsafe(self._async_init(), self._loop)

    async def _init_resources(self):
        try:
            await self.llm_service.start_persistent_live()
        except Exception:
            pass
        try:
            asyncio.create_task(self._init_ha())
        except Exception:
            pass
        self.memory_manager.start()

    async def _init_ha(self):
        try:
            connected = await self.ha_client.connect()
            if connected:
                await self.ha_context_updater.update()
        except Exception:
            pass

    async def shutdown(self):
        self._shutdown_flag = True
        for t in self.timer_list:
            if t: self.destroy_timer(t)
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
        except Exception:
            pass
