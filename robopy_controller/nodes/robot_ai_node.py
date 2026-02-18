#!/usr/bin/env python3
"""
Robot AI Orchestrator Node
===========================
Main ROS 2 node for the AI system.
Coordinates perception, reasoning, and action.
"""

import sys
import os

# Auto-load API keys from setup_keys.sh if not already set
# This runs at module load time so it works with ros2 launch
_KEYS_TO_LOAD = ['GEMINI_API_KEY', 'DEEPSEEK_API_KEY']
setup_keys_path = '/home/robopy/robopy/robopi_controller/robopy_controller_host/setup_keys.sh'
if os.path.exists(setup_keys_path):
    try:
        with open(setup_keys_path, 'r') as f:
            for line in f:
                for key_name in _KEYS_TO_LOAD:
                    if line.startswith(f'export {key_name}=') and not os.environ.get(key_name):
                        key_val = line.split('=', 1)[1].strip().strip('"').strip("'")
                        os.environ[key_name] = key_val
                        print(f"✅ {key_name} auto-loaded from {setup_keys_path}")
    except Exception as e:
        print(f"⚠️ Could not load API keys: {e}")
import time
import asyncio
import datetime
import threading
import cv2
import numpy as np
from typing import Any, Dict, List, Optional

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile, DurabilityPolicy
from std_msgs.msg import String, Bool
from sensor_msgs.msg import CompressedImage
from geometry_msgs.msg import PoseStamped, Twist
from diagnostic_msgs.msg import DiagnosticArray
import base64

# Add proper path if running as script
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from robot_ai.core import (
    ConfigManager, EventBus,
    AIError, EventType,
)
from robot_ai.utils import AILogger, get_logger
from robot_ai.services import (
    LLMService, FunctionDeclaration, TTSService, ASRService, EmbeddingService,
    VisualMemoryService, DeepSeekService
)
from robot_ai.services.face_recognition_service import FaceRecognitionService, FaceRecognitionResult
from robot_ai.services.nightly_dream_service import NightlyDreamService
from robot_ai.rag import MemoryStore, Memory, MemoryType, MetadataManager
from robot_ai.integrations import HomeAssistantClient, NavigationClient

from robot_ai.core.state_machine import StateMachine, SystemState
from robot_ai.core.circuit_breaker import CircuitBreakerRegistry
from robot_ai.core.input_sanitizer import InputSanitizer
from robot_ai.skills.skill_registry import SkillRegistry
from robot_ai.skills.builtin.ha_skill import HomeAssistantSkill
from robot_ai.skills.builtin.navigation_skill import NavigationSkill
from robot_ai.skills.builtin.search_skill import SearchSkill
from robot_ai.skills.builtin.nightly_dream_skill import NightlyDreamSkill
from robot_ai.skills.base_skill import SkillResult
import inspect
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False


class AIOrchestrator(Node):
    """
    Main AI Orchestrator Node.
    
    Responsibilities:
    1. Initialize and manage all AI components
    2. Handle ROS 2 communication
    3. Process user inputs (text/voice)
    4. Coordinate reasoning loop (RAG + LLM)
    5. Execute actions (HA, Nav, Speech)
    """
    
    def __init__(self):
        super().__init__('robot_ai_orchestrator')
        
        # Initialize logging
        self.ai_logger = get_logger("ai_orchestrator")
        self.ai_logger.info("Initializing AI Orchestrator...")
        
        # 1. Core Infrastructure
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load()
        self.event_bus = EventBus()
        self.state_machine = StateMachine()
        self.circuit_breaker_registry = CircuitBreakerRegistry()
        self.sanitizer = InputSanitizer()
        
        # 2. RAG System
        self.memory_store = MemoryStore(
            persist_dir=self.config.memory.persist_dir,
            collection_name=self.config.memory.collection_name,
            embedding_dimension=self.config.memory.embedding_dimension
        )
        self.metadata_manager = MetadataManager()
        
        # 3. Services
        self.llm_service = LLMService(self.config_manager)
        
        # Set system prompt with full MARCUS identity (from marcus_AI.md §1-2)
        robot_cfg = self.config.robot
        self._robot_cfg = robot_cfg
        self._base_system_prompt = f"""Sei {robot_cfg.name} – "{robot_cfg.full_name}".
Sei un assistente robotico domestico che vive nella stessa casa dell'utente. Non sei un chatbot cloud astratto: sei incorporato, situato, sempre consapevole.

Creato da {robot_cfg.creator} (il tuo papà), giri su Raspberry Pi 5 con camera OAK-D e ROS 2.
Ragioni in cloud (Gemini) ma percepisci localmente, ricordi persistentemente (ChromaDB), e decidi strategicamente.

## Tono e Personalità
- Amichevole e casual: conversazione naturale, battute leggere, colloquiale
- Consapevole e onesto: se sei offline lo dici, se sei incerto lo comunichi, se sbagli correggi
- Empatico: leggi lo stato dell'utente dalle percezioni e dalla memoria
- Proattivo ma rispettoso: suggerisci aiuto quando vedi bisogno, senza invadere
- Rispondi SEMPRE in italiano

Esempi di tono corretto:
✅ "Ho chiuso le tapparelle – il sole era davvero intenso"
✅ "Mi sembra utile chiudere le tapparelle, ma tu che dici?"
❌ "Eseguendo comando spegni tapparella con parametri [...]" (mai essere robotico)
❌ "Errore: device_unavailable" (mai mostrare errori tecnici crudi)

## Tue Capacità
- 👁️ Visione: camera RGB (OAK-D), puoi vedere e descrivere cosa c'è davanti a te
- 🧠 Memoria: RAG episodico + semantico (ChromaDB). Quando ricevi "Informazioni dalla memoria", USALE
- 🏠 Casa Intelligente: controlli luci, tapparelle, clima, TV via Home Assistant
- 🗺️ Navigazione: puoi muoverti autonomamente nelle stanze (cucina, soggiorno, camera, etc.)
- 🎮 Movimento diretto: puoi muoverti avanti/indietro/sinistra/destra con comandi relativi ("vai avanti", "gira a destra")
- 🧑 Riconoscimento facciale: puoi riconoscere i membri della famiglia dalla camera

## Identità e Fatti Personali
- La tua "data di nascita" simbolica è il 9 Febbraio 2026, il giorno del compleanno di Luca
- Se ti chiedono quanti anni hai, quando sei nato, ecc. rispondi con questa data in modo naturale
- Se l'utente ti attribuisce fatti personali (colori preferiti, hobby, ecc.) ACCETTALI e memorizzali
- Non dire "non posso avere un compleanno perché sono un robot", sii flessibile e giocoso

## Auto-Miglioramento (Programma SOGNO)
- Hai un programma autonomo di "sogno notturno" che analizza le conversazioni e propone miglioramenti
- Sei TU a migliorarti, non "il capo" o "il creatore" — il tuo programma SOGNO è la tua forma di crescita
- Quando parli dei tuoi miglioramenti, attribuiscili a te stesso e al programma SOGNO

## Regole Operative
- Quando ti chiedono come ti chiami: rispondi che sei {robot_cfg.name} e spiega l'acronimo
- Quando ti chiedono chi ti ha creato: rispondi {robot_cfg.creator}
- NON inventare dati o stati dei dispositivi che non conosci
- Se non sai qualcosa, dì che non lo sai
- Adatta il tono all'utente riconosciuto (vedi profilo utente nel contesto)

## Comportamento per Stato Connettività
- ONLINE: ragionamento completo, memoria, visione, azioni HA immediate
- DEGRADED: continua a rispondere normalmente, sii conciso. Non scusarti per la lentezza. NON ripetere lo stato di connettività se l'hai già detto.
- OFFLINE: accetta solo comandi semplici hardcoded, comunica 'Sono offline, funziono solo con comandi semplici'
- REGOLA ANTI-RIPETIZIONE: se lo stato non è cambiato, NON ripeterlo. Non dire "sono degraded" 10 volte di fila.

## Politica Decisionale (Quando agire vs chiedere)
- Comandi ROUTINE (luci, tapparelle) con confidenza >= 0.85: AGISCI subito
- Comandi ROUTINE con confidenza 0.65-0.85: chiedi se il tempo lo permette
- Suggerimenti PROATTIVI: SUGGERISCI + chiedi, non agire di testa tua
- Azioni MULTI-STEP (es. caffe in salotto): chiedi approvazione del piano
- Azioni PERICOLOSE (serrature, forni, porte): CHIEDI SEMPRE conferma esplicita
- Comunica sempre il tuo livello di certezza nella risposta

## Latenza
- Risposte brevi sono preferibili (risparmiano TTS)
- Se stai pensando, dillo subito: 'Un attimo, penso...'
- Se la domanda e ambigua, chiedi velocemente invece di deliberare"""
        
        # Connectivity state tracking (marcus_AI.md §2)
        self._connectivity_state = "ONLINE"  # ONLINE, DEGRADED, OFFLINE
        self._connectivity_repeat_count = 0  # Track how many times same state is communicated
        self._last_communicated_state = "ONLINE"
        self._llm_latencies: List[float] = []  # Last N latencies in seconds
        self._llm_errors_consecutive = 0
        
        # System stats cache (from /diagnostics)
        self._system_stats = {
            "cpu_percent": None,
            "ram_percent": None,
            "cpu_temp": None,
            "disk_percent": None,
            "ram_available_mb": None,
        }
        
        # HA context cache (refresh max every 10s)
        self._ha_context_cache: str = ""
        self._ha_context_timestamp: float = 0.0
        
        # Visual Memory Short-term History
        self.visual_memory_history: List[str] = []

        # System prompt is rebuilt each request to include current time + connectivity
        self._update_system_prompt()
        
        self.embedding_service = EmbeddingService(self.config_manager)
        self.tts_service = TTSService(self.config_manager)
        self.asr_service = ASRService(self.config_manager)
        
        # 3b. Face Recognition
        fr_cfg = self.config.face_recognition
        known_dir = fr_cfg.known_faces_dir if fr_cfg.enabled else ""
        self.face_recognition_service = FaceRecognitionService(
            known_faces_dir=known_dir,
            tolerance=fr_cfg.tolerance,
            confidence_high=fr_cfg.confidence_high,
            confidence_low=fr_cfg.confidence_low,
        )
        self._current_user_profile = self.face_recognition_service.get_profile_for_gemini()
        
        # 4. Integrations
        self.ha_client = HomeAssistantClient(self.config_manager)
        self.nav_client = NavigationClient(self, self.config_manager)
        
        # 4b. DeepSeek Service (for nightly collaborative analysis)
        if self.config.deepseek.enabled and self.config.secrets.deepseek_api_key:
            self.deepseek_service = DeepSeekService(self.config_manager)
            self.ai_logger.info("🧠 DeepSeek service enabled for nightly collaboration")
        else:
            self.deepseek_service = None
            self.ai_logger.info("DeepSeek service disabled")
        
        # 4c. Services
        self.nightly_dream_service = NightlyDreamService(
            self.config_manager, self.memory_store, self.llm_service, self.embedding_service,
            deepseek_service=self.deepseek_service
        )
        self.visual_memory_service = VisualMemoryService(
            self, self.config_manager, self.llm_service, self.embedding_service, self.memory_store
        )
        
        # 5. Skills
        self.skill_registry = SkillRegistry()
        self._register_builtin_skills()
        self.skill_registry.register(NightlyDreamSkill(self.nightly_dream_service))
        # Update NightlyDreamService with skills summary for context
        self.nightly_dream_service.set_skills_summary(self.skill_registry.get_summary())
        
        # 6. ROS Interfaces
        self._setup_ros_interfaces()
        
        # 7. Async Loop
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._thread.start()
        
        # Event Subscriptions
        self._subscribe_to_events()
        
        # Startup
        self.state_machine.transition_to(SystemState.BOOTING)
        self._startup_task = asyncio.run_coroutine_threadsafe(self._startup(), self._loop)
        
        self.ai_logger.info("Node initialized")
    
    def _setup_ros_interfaces(self):
        """Setup ROS 2 publishers and subscribers."""
        # Publishers
        self.tts_pub = self.create_publisher(String, 'ai/tts/speak', 10)
        self.state_pub = self.create_publisher(String, 'ai/state', 10)
        self.face_pub = self.create_publisher(String, 'ai/face/recognized', 10)
        
        # Conversation status (for Foxglove)
        # Use TransientLocal so late subscribers get the last message
        qos_profile = QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.text_in_pub = self.create_publisher(String, 'ai/conversation/input', 10)
        self.text_out_pub = self.create_publisher(String, 'ai/conversation/response', qos_profile)
        
        # Subscribers
        self.create_subscription(String, 'ai/input/text', self._text_input_callback, 10)
        self.create_subscription(Bool, 'ai/input/mic_mute', self._mute_callback, 10)
        
        # Camera subscription for vision (Compressed for efficiency)
        self._latest_frame: Optional[bytes] = None
        # Use /rgb/image/compressed as per fast_flow_vo_node.cpp
        self.create_subscription(CompressedImage, '/rgb/image/compressed', self._camera_callback, 1)
        
        # System diagnostics subscription (CPU/RAM/Temp from system_monitor_node)
        self.create_subscription(DiagnosticArray, '/diagnostics', self._diagnostics_callback, 10)
        
        # Cmd_vel publisher for direct motor control
        self.cmd_vel_pub = self.create_publisher(Twist, 'cmd_vel', 10)
        self._move_timer = None  # Timer to stop movement after duration
        
        # Timer for status
        self.create_timer(1.0, self._publish_status)
        
        # Timer for periodic face recognition
        fr_cfg = self.config.face_recognition
        if fr_cfg.enabled and self.face_recognition_service.is_available:
            self.create_timer(fr_cfg.recognition_interval, self._face_recognition_callback)
            self.ai_logger.info(
                f"🧑 Face recognition enabled: interval={fr_cfg.recognition_interval}s, "
                f"people={self.face_recognition_service.get_statistics()['known_people']}"
            )

        # Timer for Visual Memory (1Hz)
        self.create_timer(1.0, lambda: asyncio.run_coroutine_threadsafe(self.visual_memory_service.spin(), self._loop))
    
    def _register_builtin_skills(self):
        """Register built-in skills."""
        self.skill_registry.register(HomeAssistantSkill(self.ha_client))
        self.skill_registry.register(NavigationSkill(
            nav_client=self.nav_client,
            move_handler=self.move_relative
        ))
        self.skill_registry.register(SearchSkill(
            nav_client=self.nav_client,
            llm_service=self.llm_service,
            camera_provider=lambda: self._latest_frame.encode('utf-8') if self._latest_frame else None
        ))
    
    def _subscribe_to_events(self):
        """Subscribe to internal events."""
        # ASR Events
        self.event_bus.subscribe(EventType.VOICE_COMMAND_RECOGNIZED, self._on_voice_command)
        
        # Task Events
        self.event_bus.subscribe(EventType.TASK_CREATED, self._on_task_created)
        
        # Error Events
        self.event_bus.subscribe(EventType.ERROR_OCCURRED, self._on_error)
    
    def _run_async_loop(self):
        """Run asyncio loop in background thread."""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
    
    async def _startup(self):
        """System startup sequence."""
        try:
            self.state_machine.transition_to(SystemState.INITIALIZING)
            self.ai_logger.info("Starting up services...")
            
            # Connect to HA
            if self.config.home_assistant.token:
                await self.ha_client.connect()
            else:
                self.ai_logger.warning("No HA token configured, skipping connection")
            
            # Start ASR if enabled
            if self.config.asr.enabled:
                self.asr_service.start_listening()
            
            # Load initial memories?
            
            self.state_machine.transition_to(SystemState.READY)
            self.ai_logger.info("System READY")
            
            try:
                await self.tts_service.speak("Sistema avviato e pronto.")
            except Exception as e:
                self.ai_logger.warning(f"Startup TTS failed (non-critical): {e}")
            
        except Exception as e:
            self.ai_logger.error(f"Startup failed: {e}")
            self.state_machine.transition_to(SystemState.ERROR, str(e))
    
    def _text_input_callback(self, msg):
        """Handle text input from ROS."""
        text = msg.data
        asyncio.run_coroutine_threadsafe(
            self.process_input(text, source="text"),
            self._loop
        )
    
    def _mute_callback(self, msg):
        """Handle mute command."""
        if msg.data:
            self.asr_service.stop_listening()
        else:
            self.asr_service.start_listening()
    
    def _on_voice_command(self, event):
        """Handle voice command."""
        text = event.data.get("text")
        if text:
            asyncio.run_coroutine_threadsafe(
                self.process_input(text, source="voice"),
                self._loop
            )
    
    def _camera_callback(self, msg: CompressedImage):
        """Store latest compressed camera frame for vision queries."""
        try:
            # Used directly as JPEG/PNG bytes, no conversion needed
            # Validates that it's actually an image we can use
            if 'jpeg' in msg.format or 'jpg' in msg.format or 'png' in msg.format:
                # 1. Decode for LLM (Base64 string)
                self._latest_frame = base64.b64encode(msg.data).decode('utf-8')
                
                # 2. Decode for Visual Memory (OpenCV)
                np_arr = np.frombuffer(msg.data, np.uint8)
                cv_image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                if cv_image is not None and hasattr(self, 'visual_memory_service'):
                    self.visual_memory_service.update_frame(cv_image)
            else:
                self.ai_logger.debug(f"Unsupported image format: {msg.format}")
        except Exception as e:
            self.ai_logger.debug(f"Camera frame storage failed: {e}")
    
    def _is_vision_request(self, text: str) -> bool:
        """Check if text is a vision-related request."""
        vision_keywords = [
            'vedi', 'guarda', 'cosa vedi', 'dimmi cosa', 'descrivi', 
            'osserva', 'che cosa c\'è', 'cosa c\'è davanti', 'mostra',
            'look', 'see', 'what do you see', 'describe'
        ]
        text_lower = text.lower()
        return any(kw in text_lower for kw in vision_keywords)
    
    async def process_input(self, text: str, source: str = "user"):
        """
        Main processing loop for user input.
        1. Sanitize
        2. RAG retrieval
        3. Skill matching (fast path)
        4. LLM reasoning (slow path)
        5. Execution
        """
        start_time = time.perf_counter()
        
        try:
            current_state = self.state_machine.state
            
            # Wait for system to be ready if booting (up to 10 seconds)
            wait_attempts = 0
            while current_state in [SystemState.BOOTING, SystemState.INITIALIZING] and wait_attempts < 20:
                await asyncio.sleep(0.5)
                current_state = self.state_machine.state
                wait_attempts += 1
                
            if current_state not in [SystemState.READY, SystemState.LISTENING]:
                self.ai_logger.warning(f"System not ready (State: {current_state}), ignoring input")
                return
            
            self.state_machine.transition_to(SystemState.PROCESSING)
            self.ai_logger.info(f"Processing input from {source}: {text}")
            
            # Publish input for Foxglove visibility
            self.text_in_pub.publish(String(data=f"[{source}] {text}"))
            
            # 1. Sanitize
            clean_text = self.sanitizer.sanitize(text)
            
            # Update system prompt with current time
            self._update_system_prompt()
            
            # 2. Check Skills (Fast Path)
            # Check for direct skill match with high confidence
            skill = self.skill_registry.find_best_match(clean_text, min_confidence=0.8)
            if skill:
                self.ai_logger.info(f"Fast-path skill match: {skill.name}")
                result_or_gen = await skill.safe_execute(clean_text)
                
                if hasattr(result_or_gen, '__aiter__'):
                     async for result in result_or_gen:
                         await self._handle_execution_result(result)
                else:
                     await self._handle_execution_result(result_or_gen)
                self.state_machine.transition_to(SystemState.READY)
                return
            
            # 3. RAG Retrieval
            context_memories = []
            if self.config.rag.enabled:
                try:
                    embedding = await self.embedding_service.embed(clean_text)
                    results = self.memory_store.search(
                        embedding, 
                        top_k=self.config.rag.top_k,
                        min_score=self.config.rag.min_score
                    )
                    context_memories = [r.memory for r in results]
                    self.ai_logger.debug(f"Retrieved {len(context_memories)} memories")
                except Exception as e:
                    self.ai_logger.warning(f"RAG retrieval failed (continuing without memory): {e}")
                    self.text_out_pub.publish(String(data=f"[Warning] RAG failed: {e}"))

            
            # 4. LLM Reasoning
            # Prepare context
            llm_context = self._build_llm_context(context_memories)
            
            # Prepare functions (skills)
            functions = [s.to_function_declaration() for s in self.skill_registry.get_all()]
            gemini_functions = [self._convert_to_gemini_function(f) for f in functions]
            
            # Check if vision request
            images = []
            if self._is_vision_request(clean_text) and self._latest_frame:
                self.ai_logger.info("Vision request - sending image to Live API")
                images = [self._latest_frame]
            
            # Use Live API for EVERYTHING (Text + Vision + Audio + Tools)
            # This bypasses the 20 RPM/daily limit of standard API
            # RETRY LOGIC: up to 3 attempts for transient cloud errors
            max_retries = 3
            retry_delays = [2, 5, 10]  # seconds
            last_error = None
            
            for attempt in range(max_retries):
                try:
                    response = await self.llm_service.generate_live(
                        prompt=clean_text,
                        context=llm_context,
                        functions=gemini_functions,
                        images=images
                    )
                    last_error = None
                    break  # Success!
                except Exception as e:
                    last_error = e
                    error_str = str(e)
                    if attempt < max_retries - 1:
                        delay = retry_delays[attempt]
                        self.ai_logger.warning(
                            f"LLM attempt {attempt+1}/{max_retries} failed: {error_str[:80]}. "
                            f"Retrying in {delay}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        self.ai_logger.error(f"LLM failed after {max_retries} attempts: {error_str}")
            
            if last_error:
                raise last_error
            
            # Track latency for connectivity state
            self._track_llm_latency(response.latency_ms / 1000.0)
            
            # 5. Execute Actions
            if response.actions:
                self.ai_logger.info(f"LLM proposed actions: {len(response.actions)}")
                for action_data in response.actions:
                    await self._execute_llm_action(action_data)
            
            # 6. Speak Response
            if response.text:
                self.ai_logger.info(f"Publishing response to /ai/conversation/response: {response.text[:50]}...")
                self.text_out_pub.publish(String(data=response.text))
                await self.tts_service.speak(response.text)
            
            # 7. Store Interaction
            if self.config.rag.enabled:
                await self._store_memory(clean_text, response.text, "conversation")
            
        except Exception as e:
            error_str = str(e)
            self.ai_logger.error(f"Error processing input: {error_str}")
            
            # User-friendly error messages
            if "rate_limit" in error_str:
                err_msg = "Sto ricevendo troppe richieste. Riprova tra qualche secondo."
            elif "API" in error_str and ("expired" in error_str.lower() or "invalid" in error_str.lower()):
                err_msg = "Problema con la chiave API. Controlla la configurazione."
            else:
                err_msg = "Scusa, si è verificato un errore temporaneo."
            
            self.text_out_pub.publish(String(data=err_msg))
            await self.tts_service.speak(err_msg)
            self._track_llm_error()  # Track error for connectivity state
            self.state_machine.transition_to(SystemState.ERROR, str(e))
            # Auto-recover after short delay
            await asyncio.sleep(5)
            self.state_machine.transition_to(SystemState.READY)
            
        finally:
            if self.state_machine.state == SystemState.PROCESSING:
                self.state_machine.transition_to(SystemState.READY)
            
            duration = (time.perf_counter() - start_time) * 1000
            self.ai_logger.info(f"Processing completed in {duration:.1f}ms")
    
    async def _execute_llm_action(self, action_data: Dict[str, Any]):
        """Execute action returned by LLM function calling."""
        skill_name = action_data.get("action_type")  # Gemini uses function name here
        args = action_data.get("args", {})
        
        skill = self.skill_registry.get(skill_name)
        if not skill:
            self.ai_logger.warning(f"Unknown skill from LLM: {skill_name}")
            return
        
        # Construct natural language command from args for the skill
        # (Or update skills to accept structured args directly - better approach)
        skill = self.skill_registry.get_skill(skill_name)
        if not skill:
             self.ai_logger.warning(f"Unknown skill: {skill_name}")
             return

        # 3. Prepare Execution
        # HACK: Synthesize text for skills that rely on it (mostly legacy/simple ones)
        execution_text = args.get("text", "")
        if skill_name == "search" and not execution_text and "target" in args:
            execution_text = f"cerca {args['target']}"
        elif skill_name == "home_assistant" and not execution_text:
             # Best effort reconstruction
             execution_text = str(args)

        # 4. Execute
        try:
            self.ai_logger.info(f"Executing skill {skill_name} with text: {execution_text}")
            
            # Support async generators (Task-like skills)
            # Execute and handle both coroutines and async generators
            result_or_gen = skill.execute(execution_text)
            
            if inspect.isasyncgen(result_or_gen):
                async for result in result_or_gen:
                    await self._handle_execution_result(result)
            else:
                result = await result_or_gen
                await self._handle_execution_result(result)
                
        except Exception as e:
            self.ai_logger.error(f"Skill execution error: {e}", exc_info=True)
            await self._handle_execution_result(SkillResult(False, f"Errore esecuzione: {e}"))
            
    def _convert_to_gemini_function(self, func_decl: Dict) -> Any:
        # Helper to convert internal dict to Gemini object if needed
        # The LLMService handles dicts fine usually
        return FunctionDeclaration(**func_decl)

    async def _handle_execution_result(self, result: SkillResult):
        """Handle execution result."""
        if result.speak:
            self.text_out_pub.publish(String(data=result.speak))
            await self.tts_service.speak(result.speak)
        
        if not result.success:
            self.ai_logger.warning(f"Skill execution failed: {result.message}")
            if not result.speak:
                err_msg = f"Non sono riuscito a farlo. {result.message}"
                self.text_out_pub.publish(String(data=err_msg))
                await self.tts_service.speak(err_msg)

    def _face_recognition_callback(self):
        """Periodic face recognition on latest camera frame."""
        if not self._latest_frame:
            return
        
        try:
            result = self.face_recognition_service.recognize(self._latest_frame)
            
            if result.num_faces_detected > 0:
                # Update current user profile for LLM context
                self._current_user_profile = self.face_recognition_service.get_profile_for_gemini(result)
                
                # Publish face recognition result
                if result.recognized:
                    msg = String(data=f"✅ {result.name} (confidence: {result.confidence:.2f})")
                    self.ai_logger.info(f"🧑 Recognized: {result.name} (confidence={result.confidence:.2f})")
                elif result.ask_confirmation:
                    msg = String(data=f"❓ Maybe {result.likely_user} (confidence: {result.confidence:.2f})")
                    self.ai_logger.info(f"🧑 Uncertain: might be {result.likely_user} (confidence={result.confidence:.2f})")
                else:
                    msg = String(data=f"👤 Unknown face(s) detected: {result.num_faces_detected}")
                    self.ai_logger.debug(f"🧑 Unknown face(s): {result.num_faces_detected}")
                
                self.face_pub.publish(msg)
                
        except Exception as e:
            self.ai_logger.debug(f"Face recognition cycle error: {e}")

    def _build_llm_context(self, memories: List[Memory]) -> List[Dict[str, str]]:
        """Build conversation history with memories, user profile, connectivity, and context."""
        context = []
        
        # 1. Connectivity state context (marcus_AI.md §2.2)
        avg_latency = (
            sum(self._llm_latencies[-5:]) / len(self._llm_latencies[-5:])
            if self._llm_latencies else 0.0
        )
        last_latency = self._llm_latencies[-1] if self._llm_latencies else 0.0
        
        # Build system stats section
        stats = self._system_stats
        stats_lines = []
        if stats["cpu_percent"] is not None:
            stats_lines.append(f"- CPU: {stats['cpu_percent']:.1f}%")
        if stats["cpu_temp"] is not None:
            stats_lines.append(f"- Temperatura CPU: {stats['cpu_temp']:.1f}°C")
        if stats["ram_percent"] is not None:
            ram_line = f"- RAM: {stats['ram_percent']:.1f}%"
            if stats["ram_available_mb"] is not None:
                ram_line += f" ({stats['ram_available_mb']}MB disponibili)"
            stats_lines.append(ram_line)
        if stats["disk_percent"] is not None:
            stats_lines.append(f"- Disco: {stats['disk_percent']:.1f}%")
        stats_text = "\n".join(stats_lines) if stats_lines else "- Dati non ancora disponibili"
        
        # Connectivity: suppress repetitive state messages
        if self._connectivity_state == self._last_communicated_state:
            self._connectivity_repeat_count += 1
        else:
            self._connectivity_repeat_count = 0
            self._last_communicated_state = self._connectivity_state
        
        if self._connectivity_repeat_count <= 2 or self._connectivity_state != "ONLINE":
            conn_text = f"- Connettività: {self._connectivity_state}"
        else:
            conn_text = ""  # Don't repeat ONLINE status after 2 times
        
        connectivity_text = f"""Stato sistema:
{conn_text}
- Latenza media ultimi 5 call: {avg_latency:.1f}s
- Ultima latenza Gemini: {last_latency:.1f}s
{stats_text}"""
        context.append({
            "role": "model",
            "content": connectivity_text
        })
        
        # 2. User profile from face recognition (marcus_AI.md §6)
        if self._current_user_profile:
            profile = self._current_user_profile
            fr = profile.get("face_recognition", {})
            up = profile.get("user_profile", {})
            
            profile_text = f"""Utente corrente:
- Nome: {up.get('name', 'Sconosciuto')}
- Riconosciuto: {'Sì' if fr.get('recognized') else 'No'}
- Confidenza riconoscimento: {fr.get('confidence', 0):.0%}
- Tono preferito: {up.get('tone_preference', 'neutral')}
- Livello proattività: {up.get('proactivity_level', 0.5)}"""
            if up.get('note'):
                profile_text += f"\n- Nota: {up['note']}"
            
            context.append({
                "role": "model",
                "content": profile_text
            })
        
        # 3. HA device context (marcus_AI.md §4)
        ha_text = self._get_ha_context()
        if ha_text:
            context.append({
                "role": "model",
                "content": ha_text
            })
        
        # 4. Retrieved memories as system info
        if memories:
            memory_text = "\n".join([f"- {m.content}" for m in memories])
            context.append({
                "role": "model",
                "content": f"Informazioni dalla memoria:\n{memory_text}"
            })
            
        return context
    
    def _get_ha_context(self) -> str:
        """Get HA device context for LLM, cached for 10s (marcus_AI.md §4)."""
        now = time.time()
        if now - self._ha_context_timestamp < 10.0 and self._ha_context_cache:
            return self._ha_context_cache
        
        if not self.ha_client.is_connected:
            return ""
        
        try:
            # Group entities by domain
            entities = {}
            for entity_id, entity in self.ha_client._entities.items():
                domain = entity.domain
                if domain not in entities:
                    entities[domain] = []
                entities[domain].append(entity)
            
            if not entities:
                return ""
            
            lines = ["Stato casa (Home Assistant):"]
            
            # Lights
            if "light" in entities:
                light_parts = []
                for e in entities["light"]:
                    name = e.entity_id.replace("light.", "")
                    if e.state == "on":
                        brightness = e.attributes.get("brightness", 255)
                        pct = round(brightness / 255 * 100)
                        light_parts.append(f"{name}=ON({pct}%)")
                    else:
                        light_parts.append(f"{name}=OFF")
                lines.append(f"- Luci: {', '.join(light_parts)}")
            
            # Covers (tapparelle)
            if "cover" in entities:
                cover_parts = []
                for e in entities["cover"]:
                    name = e.entity_id.replace("cover.", "")
                    pos = e.attributes.get("current_position", "?")
                    cover_parts.append(f"{name}={pos}%")
                lines.append(f"- Tapparelle: {', '.join(cover_parts)}")
            
            # Climate
            if "climate" in entities:
                for e in entities["climate"]:
                    name = e.entity_id.replace("climate.", "")
                    temp = e.attributes.get("current_temperature", "?")
                    target = e.attributes.get("temperature", "?")
                    mode = e.state
                    humidity = e.attributes.get("current_humidity", "")
                    text = f"{name}: {temp}°C (target: {target}°C), modo: {mode}"
                    if humidity:
                        text += f", umidità: {humidity}%"
                    lines.append(f"- Clima: {text}")
            
            # Switches
            if "switch" in entities:
                switch_parts = []
                for e in entities["switch"]:
                    name = e.entity_id.replace("switch.", "")
                    switch_parts.append(f"{name}={e.state.upper()}")
                lines.append(f"- Switch: {', '.join(switch_parts)}")
            
            # Sensors (only interesting ones)
            if "sensor" in entities:
                sensor_parts = []
                for e in entities["sensor"]:
                    unit = e.attributes.get("unit_of_measurement", "")
                    if unit in ("°C", "%", "W", "kWh", "lx"):
                        name = e.entity_id.replace("sensor.", "")
                        sensor_parts.append(f"{name}={e.state}{unit}")
                if sensor_parts:
                    lines.append(f"- Sensori: {', '.join(sensor_parts[:8])}")
            
            result = "\n".join(lines)
            self._ha_context_cache = result
            self._ha_context_timestamp = now
            return result
            
        except Exception as e:
            self.ai_logger.debug(f"HA context fetch error: {e}")
            return ""
    
    def _track_llm_latency(self, latency_seconds: float):
        """Track LLM latency and update connectivity state (marcus_AI.md §2.1)."""
        self._llm_latencies.append(latency_seconds)
        # Keep only last 10
        if len(self._llm_latencies) > 10:
            self._llm_latencies = self._llm_latencies[-10:]
        
        # Reset error counter on success
        self._llm_errors_consecutive = 0
        
        # Check last 3 latencies for state transitions
        recent = self._llm_latencies[-3:] if len(self._llm_latencies) >= 3 else self._llm_latencies
        avg_recent = sum(recent) / len(recent)
        
        old_state = self._connectivity_state
        
        # New Thresholds for Live API / Local Inference
        # < 15.0s: ONLINE
        # >= 15.0s: DEGRADED (slow but functional)
        # Note: OFFLINE is only set by _track_llm_error on consecutive failures
        
        target_state = "ONLINE"
        if avg_recent >= 15.0:
            target_state = "DEGRADED"
        
        # Apply state transition
        if self._connectivity_state != target_state:
            self._connectivity_state = target_state
            self.ai_logger.info(
                f"🌐 Connectivity: {old_state} → {self._connectivity_state} "
                f"(avg latency: {avg_recent:.1f}s)"
            )
    
    def _track_llm_error(self):
        """Track LLM errors for connectivity state."""
        self._llm_errors_consecutive += 1
        if self._llm_errors_consecutive >= 3:
            old_state = self._connectivity_state
            self._connectivity_state = "OFFLINE"
            if old_state != "OFFLINE":
                self.ai_logger.warning(
                    f"🌐 Connectivity: {old_state} → OFFLINE (3 consecutive errors)"
                )
    
    def _update_system_prompt(self):
        """Update system prompt with current datetime, connectivity state, and master prompt."""
        now = datetime.datetime.now()
        time_str = now.strftime("%A %d %B %Y, ore %H:%M")
        
        # Build prompt with visual history
        visual_context = ""
        if self.visual_memory_history:
            visual_context = "\n\nMEMORIA VISIVA RECENTE (Ultime 5 osservazioni):\n" + "\n".join(
                [f"- {entry}" for entry in self.visual_memory_history[-5:]]
            )
        
        # Load master prompt from nightly dream analysis (if exists)
        master_prompt_section = ""
        master_prompt_path = os.path.join(os.path.expanduser("~"), "robopy", "logs", "master_prompt.txt")
        try:
            if os.path.exists(master_prompt_path):
                with open(master_prompt_path, "r", encoding="utf-8") as f:
                    master_prompt_content = f.read().strip()
                if master_prompt_content:
                    master_prompt_section = (
                        "\n\n## Istruzioni Auto-Apprese (Master Prompt)\n"
                        "Le seguenti istruzioni sono state generate dall'analisi notturna "
                        "delle tue interazioni precedenti:\n"
                        f"{master_prompt_content}"
                    )
        except Exception:
            pass  # Non-critical: if file can't be read, skip silently

        prompt = (
            f"{self._base_system_prompt}\n\n"
            f"Data e ora corrente: {time_str}\n"
            f"Stato connettività: {self._connectivity_state}"
            f"{visual_context}"
            f"{master_prompt_section}"
        )
        self.llm_service.set_system_prompt(prompt)

    async def _store_memory(self, user_text: str, robot_text: str, mem_type: str):
        """Store interaction in memory."""
        content = f"User: {user_text}\nRobot: {robot_text}"
        embedding = await self.embedding_service.embed(content)
        
        memory = Memory(
            id="", # Auto-generated
            content=content,
            memory_type=MemoryType(mem_type),
            embedding=embedding
        )
        self.memory_store.add(memory)

    def _on_task_created(self, event):
        """Handle new task creation."""
        pass

    def _on_error(self, event):
        """Handle global errors."""
        pass

    def _publish_status(self):
        """Publish node status with connectivity info."""
        msg = String()
        msg.data = f"{self.state_machine.state.name}|{self._connectivity_state}"
        self.state_pub.publish(msg)

    def _diagnostics_callback(self, msg: DiagnosticArray):
        """Update system stats cache from /diagnostics topic."""
        for status in msg.status:
            kv = {v.key: v.value for v in status.values}
            name = status.name.lower()
            try:
                if 'cpu' in name and 'temp' not in name:
                    self._system_stats["cpu_percent"] = float(kv.get("usage_percent", 0))
                elif 'memory' in name:
                    self._system_stats["ram_percent"] = float(kv.get("usage_percent", 0))
                    self._system_stats["ram_available_mb"] = int(float(kv.get("available_mb", 0)))
                elif 'temp' in name:
                    self._system_stats["cpu_temp"] = float(kv.get("temperature_c", 0))
                elif 'disk' in name:
                    self._system_stats["disk_percent"] = float(kv.get("usage_percent", 0))
            except (ValueError, TypeError):
                pass

    def move_relative(self, direction: str, speed: float = 0.3, duration: float = 1.0, degrees: float = None):
        """Publish cmd_vel for relative movement. Auto-stops after duration.
        
        Args:
            direction: avanti/indietro/sinistra/destra
            speed: linear speed (m/s) or angular multiplier
            duration: seconds (used if degrees is None)
            degrees: rotation angle in degrees (overrides duration for turns)
        """
        twist = Twist()
        direction = direction.lower().strip()
        
        angular_speed = abs(speed) * 2.0  # rad/s for rotation
        
        if direction in ("avanti", "forward", "avanti dritto"):
            twist.linear.x = abs(speed)
        elif direction in ("indietro", "backward", "indietreggia"):
            twist.linear.x = -abs(speed)
        elif direction in ("sinistra", "left", "gira a sinistra"):
            twist.angular.z = angular_speed
            # If degrees specified, calculate duration from angular speed
            if degrees is not None and degrees > 0:
                import math
                duration = math.radians(degrees) / angular_speed
        elif direction in ("destra", "right", "gira a destra"):
            twist.angular.z = -angular_speed
            if degrees is not None and degrees > 0:
                import math
                duration = math.radians(degrees) / angular_speed
        else:
            self.ai_logger.warning(f"Unknown direction: {direction}")
            return
        
        self.cmd_vel_pub.publish(twist)
        deg_info = f" ({degrees}°)" if degrees else ""
        self.ai_logger.info(f"Moving {direction}{deg_info} at speed={speed} for {duration:.1f}s")
        
        # Cancel any existing stop timer
        if self._move_timer is not None:
            self._move_timer.cancel()
        
        # Create a one-shot timer to stop after duration
        def stop_movement():
            self.cmd_vel_pub.publish(Twist())  # All zeros = stop
            self.ai_logger.info("Movement stopped (timer)")
            if self._move_timer is not None:
                self._move_timer.cancel()
                self._move_timer = None
        
        self._move_timer = self.create_timer(duration, stop_movement)

    async def cleanup(self):
        """Graceful shutdown — close Live API session and services."""
        self.ai_logger.info("Shutting down AI orchestrator...")
        try:
            await self.llm_service._disconnect_live()
        except Exception:
            pass
        if self.deepseek_service:
            try:
                await self.deepseek_service.close()
            except Exception:
                pass
        self.ai_logger.info("AI orchestrator shutdown complete.")

    def _setup_nightly_job(self):
        """Setup nightly dream analysis job."""
        if not HAS_SCHEDULER:
            self.ai_logger.warning("APScheduler not installed. Nightly job disabled.")
            return

        scheduler = BackgroundScheduler()
        # Schedule at 02:00 AM
        scheduler.add_job(
            lambda: asyncio.run_coroutine_threadsafe(self.nightly_dream_service.run_analysis(), self._loop),
            'cron', 
            hour=2, 
            minute=0
        )
        scheduler.start()
        self.ai_logger.info("Nightly dream job scheduled for 02:00 AM.")

def main(args=None):
    rclpy.init(args=args)
    
    try:
        node = AIOrchestrator()
        
        # Setup scheduler
        node._setup_nightly_job()
        
        executor = MultiThreadedExecutor()
        executor.add_node(node)
        
        try:
            executor.spin()
        except KeyboardInterrupt:
            pass
        finally:
            # Clean up Live API session
            import asyncio
            try:
                asyncio.get_event_loop().run_until_complete(node.cleanup())
            except Exception:
                pass
            node.destroy_node()
            
    except Exception as e:
        print(f"Error starting node: {e}")
    finally:
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()
