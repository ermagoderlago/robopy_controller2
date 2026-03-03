#!/usr/bin/env python3
"""
Robot AI Orchestrator Node
===========================
Main ROS 2 node for the AI system.
Now with:
- Reactive safety layer (priority commands bypass lock)
- Percentile latency tracking (P50, P95, P99)
- Parallel action execution (with sequential TTS)
- STATIC System Prompt & Dynamic Context Injection
- Smart Wake-Word / Mic Mute logic with context pre-loading
- Advanced Chain-of-Thought filtering (Salvage logic)
"""

import sys
import os
import hashlib
import threading
import asyncio
import time
import datetime
import json
import base64
import cv2
import numpy as np
import math
import traceback
import re
import concurrent.futures
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import deque

# Load environment variables from .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Loaded .env file")
except ImportError:
    pass

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile, DurabilityPolicy
from std_msgs.msg import String, Bool
from sensor_msgs.msg import CompressedImage
from audio_common_msgs.msg import AudioData  
from geometry_msgs.msg import Twist
from diagnostic_msgs.msg import DiagnosticArray
from example_interfaces.srv import Trigger

# Add proper path if running as script
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from robot_ai.core import ConfigManager, EventBus, EventType
from robot_ai.utils import get_logger

from robot_ai.services.llm_service import LLMService, FunctionDeclaration
from robot_ai.services.tts_service import TTSService
from robot_ai.services.asr_service import ASRService
from robot_ai.services.embedding_service import EmbeddingService
from robot_ai.services.visual_memory_service import VisualMemoryService
from robot_ai.services.deepseek_service import DeepSeekService
from robot_ai.services.face_recognition_service import FaceRecognitionService
from robot_ai.services.nightly_dream_service import NightlyDreamService
from robot_ai.rag import MemoryStore, Memory, MemoryType
from robot_ai.integrations import HomeAssistantClient, NavigationClient

from robot_ai.core.state_machine import StateMachine, SystemState
from robot_ai.core.input_sanitizer import InputSanitizer
from robot_ai.skills.skill_registry import SkillRegistry
from robot_ai.skills.builtin.ha_skill import HomeAssistantSkill
from robot_ai.skills.builtin.navigation_skill import NavigationSkill
from robot_ai.skills.builtin.search_skill import SearchSkill
from robot_ai.skills.builtin.nightly_dream_skill import NightlyDreamSkill
from robot_ai.skills.builtin.visual_exploration_skill import VisualExplorationSkill
from robot_ai.skills.base_skill import SkillResult
import inspect

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False


# ---------------------------------------------------------------------------
# Helper per caricamento sicuro delle API key
# ---------------------------------------------------------------------------
def _load_api_keys_from_setup():
    setup_keys_path = '/home/robopy/robopy/robopi_controller/robopy_controller_host/setup_keys.sh'
    keys_to_load = ['GEMINI_API_KEY', 'DEEPSEEK_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS']

    if not os.path.exists(setup_keys_path):
        return

    try:
        with open(setup_keys_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                for key_name in keys_to_load:
                    if line.startswith(f'export {key_name}=') and not os.environ.get(key_name):
                        value = line.split('=', 1)[1].strip()
                        if '#' in value:
                            value = value[:value.index('#')].strip()
                        value = value.strip('"').strip("'")
                        os.environ[key_name] = value
                        print(f"✅ {key_name} auto-loaded (valore nascosto)")
    except Exception as e:
        print(f"⚠️ Could not load API keys: {e}")

_load_api_keys_from_setup()

# Regex di sicurezza con boundary check
SAFETY_PATTERN = re.compile(r'\b(fermati|stop|emergenza)\b', re.IGNORECASE)


@dataclass
class CameraFrame:
    """Frame atomico e immutabile con decodifica lazy."""
    raw: bytes

    @property
    def b64(self) -> str:
        cached = self.__dict__.get('_b64')
        if cached is None:
            cached = base64.b64encode(self.raw).decode('utf-8')
            object.__setattr__(self, '_b64', cached)
        return cached

    @property
    def cv_image(self):
        cached = self.__dict__.get('_cv')
        if cached is None:
            np_arr = np.frombuffer(self.raw, np.uint8)
            cached = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            object.__setattr__(self, '_cv', cached)
        return cached


@dataclass
class PendingMemory:
    user_text: str
    robot_text: str
    mem_type: str
    timestamp: float


# ---------------------------------------------------------------------------
# Modello Interno Persistente (World Model)
# ---------------------------------------------------------------------------
@dataclass
class WorldModel:
    rooms: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    current_user: Optional[Dict[str, Any]] = None
    battery_level: Optional[float] = None
    position: Optional[Tuple[float, float]] = None
    current_task: Optional[str] = None
    recent_events: deque = field(default_factory=lambda: deque(maxlen=10))
    recent_interactions: deque = field(default_factory=lambda: deque(maxlen=5))

    def to_prompt_section(self) -> str:
        lines = ["## MODELLO INTERNO ATTUALE"]
        if self.rooms:
            rooms_str = ", ".join([f"{name}: {len(data)} oggetti" for name, data in self.rooms.items()])
            lines.append(f"- Stanze conosciute: {rooms_str}")
        if self.current_user:
            lines.append(f"- Utente attuale: {self.current_user.get('name', 'Sconosciuto')}")
        if self.battery_level is not None:
            lines.append(f"- Batteria: {self.battery_level:.0f}%")
        if self.position:
            lines.append(f"- Posizione stimata: ({self.position[0]:.1f}, {self.position[1]:.1f})")
        if self.current_task:
            lines.append(f"- Compito in corso: {self.current_task}")
        if self.recent_events:
            events = list(self.recent_events)
            lines.append("- Eventi recenti:")
            for e in events:
                lines.append(f"  * {e}")
        if self.recent_interactions:
            lines.append("- Ultime interazioni:")
            for i in self.recent_interactions:
                lines.append(f"  * {i}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Percentile Tracker
# ---------------------------------------------------------------------------
class PercentileTracker:
    def __init__(self, maxlen=100):
        self.values = deque(maxlen=maxlen)

    def add(self, value: float):
        self.values.append(value)

    def percentile(self, p: float) -> float:
        if not self.values:
            return 0.0
        sorted_vals = sorted(self.values)
        k = (len(sorted_vals) - 1) * p / 100
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_vals[int(k)]
        d0 = sorted_vals[int(f)] * (c - k)
        d1 = sorted_vals[int(c)] * (k - f)
        return d0 + d1

    def max(self) -> float:
        return max(self.values) if self.values else 0.0


# ---------------------------------------------------------------------------
# Nodo Principale
# ---------------------------------------------------------------------------
class AIOrchestrator(Node):
    def __init__(self):
        super().__init__('robot_ai_orchestrator')
        self.ai_logger = get_logger("ai_orchestrator")
        self.ai_logger.info("Initializing AI Orchestrator (agente embodied)")

        self.config_manager = ConfigManager()
        self.config = self.config_manager.load()
        self.event_bus = EventBus()
        self.state_machine = StateMachine()
        self.sanitizer = InputSanitizer()

        self._thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        self._skill_timeout = 10.0
        self._llm_timeout = 25.0
        self._recovery_interval = 30.0

        self.memory_store = MemoryStore(
            persist_dir=self.config.memory.persist_dir,
            collection_name=self.config.memory.collection_name,
            embedding_dimension=self.config.memory.embedding_dimension
        )

        self.llm_service = LLMService(self.config_manager)
        self.tts_service = TTSService(self.config_manager)
        self.asr_service = ASRService(self.config_manager)
        self.embedding_service = EmbeddingService(self.config_manager)

        fr_cfg = self.config.face_recognition
        known_dir = fr_cfg.known_faces_dir if fr_cfg.enabled else ""
        self.face_recognition_service = FaceRecognitionService(
            known_faces_dir=known_dir,
            tolerance=fr_cfg.tolerance,
            confidence_high=fr_cfg.confidence_high,
            confidence_low=fr_cfg.confidence_low,
        )
        self._current_user_profile = self.face_recognition_service.get_profile_for_gemini()

        self.ha_client = HomeAssistantClient(self.config_manager)
        self.nav_client = NavigationClient(self, self.config_manager)

        if self.config.deepseek.enabled and self.config.secrets.deepseek_api_key:
            self.deepseek_service = DeepSeekService(self.config_manager)
            self.ai_logger.info("🧠 DeepSeek service enabled")
        else:
            self.deepseek_service = None

        self.nightly_dream_service = NightlyDreamService(
            self.config_manager, self.memory_store, self.llm_service,
            self.embedding_service, deepseek_service=self.deepseek_service
        )
        self.visual_memory_service = VisualMemoryService(
            self, self.config_manager, self.llm_service,
            self.embedding_service, self.memory_store
        )

        self.skill_registry = SkillRegistry()
        self._register_builtin_skills()
        self.skill_registry.register(NightlyDreamSkill(self.nightly_dream_service))
        self.nightly_dream_service.set_skills_summary(self.skill_registry.get_summary())

        # Microfono mute di default (Wake word)
        self._mic_muted = True
        self._mute_timer = None
        self._master_prompt_cache = {"content": "", "timestamp": 0.0}
        self._master_prompt_path = os.path.join(os.path.expanduser("~"), "robopy", "logs", "master_prompt.txt")

        self._connectivity_state = "ONLINE"
        self._llm_latency_tracker = PercentileTracker(maxlen=100)
        self._llm_errors_consecutive = 0
        self._conn_online_threshold = 12.0
        self._conn_degraded_threshold = 18.0

        self._ha_context_cache: str = ""
        self._ha_context_timestamp: float = 0.0
        self._system_stats = {
            "cpu_percent": None, "ram_percent": None,
            "cpu_temp": None, "disk_percent": None, "ram_available_mb": None,
        }
        self.visual_memory_history: List[str] = []
        self._latest_frame: Optional[CameraFrame] = None

        self._move_task: Optional[asyncio.Task] = None
        self._reactive_cmd_vel = Twist()
        self._reactive_cmd_vel_lock = threading.Lock()

        self._processing_lock = None
        self._move_lock = None
        self._ready_event = None
        self._memory_queue = None
        self._memory_worker_task = None
        self._embedding_cache = {}
        self._max_cache_size = 100

        self._metrics = {
            "requests_total": 0, "requests_success": 0, "requests_failed": 0,
            "llm_calls": 0, "llm_errors": 0, "skill_calls": 0, "skill_errors": 0,
            "llm_latency_p50": 0.0, "llm_latency_p95": 0.0,
            "llm_latency_p99": 0.0, "llm_latency_max": 0.0,
        }
        self._metrics_pub = self.create_publisher(String, 'ai/metrics', 10)
        self._metrics_timer = self.create_timer(5.0, self._publish_metrics)

        self.world_model = WorldModel()
        self._shutdown_flag = False

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._thread.start()

        init_future = asyncio.run_coroutine_threadsafe(self._init_async_resources(), self._loop)
        init_future.result(timeout=5.0)

        self._setup_ros_interfaces()
        self._subscribe_to_events()

        self.state_machine.transition_to(SystemState.BOOTING)
        self._startup_task = asyncio.run_coroutine_threadsafe(self._startup(), self._loop)
        self._setup_nightly_job()

        self.ai_logger.info("Node initialized")

    def _reactive_loop(self):
        with self._reactive_cmd_vel_lock:
            msg = self._reactive_cmd_vel
        self.cmd_vel_pub.publish(msg)

    def emergency_stop(self):
        with self._reactive_cmd_vel_lock:
            self._reactive_cmd_vel = Twist()
        self.ai_logger.warning("EMERGENCY STOP ACTIVATED")
        asyncio.run_coroutine_threadsafe(self._cancel_move_task(), self._loop)

    async def _cancel_move_task(self):
        if self._move_task and not self._move_task.done():
            self._move_task.cancel()
            try:
                await self._move_task
            except asyncio.CancelledError:
                pass

    async def _init_async_resources(self):
        self._processing_lock = asyncio.Lock()
        self._move_lock = asyncio.Lock()
        self._ready_event = asyncio.Event()
        self._memory_queue = asyncio.Queue(maxsize=100)
        
        self._memory_worker_task = asyncio.create_task(self._memory_worker())
        
        self.llm_service.set_system_prompt(self._build_base_system_prompt())
        self._ready_event.set()

    async def _memory_worker(self):
        if self._memory_queue is None: return
        while not self._shutdown_flag:
            try:
                pending = await self._memory_queue.get()
                content = f"User: {pending.user_text}\nRobot: {pending.robot_text}"
                cache_key = hashlib.md5(content.encode()).hexdigest()
                
                if cache_key in self._embedding_cache:
                    embedding = self._embedding_cache[cache_key]
                else:
                    embedding = await self.embedding_service.embed(content)
                    if len(self._embedding_cache) < self._max_cache_size:
                        self._embedding_cache[cache_key] = embedding
                
                memory = Memory(
                    id="", content=content, memory_type=MemoryType(pending.mem_type),
                    embedding=embedding, metadata={"timestamp": pending.timestamp}
                )
                self.memory_store.add(memory)
                self.ai_logger.debug(f"Memory stored: {pending.mem_type}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.ai_logger.error(f"Memory worker error: {e}")

    def _schedule_ha_context_update(self):
        if not hasattr(self, '_loop') or self._loop is None or not self._loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._update_ha_context_background(), self._loop)

    async def _update_ha_context_background(self):
        try:
            entities = await self._loop.run_in_executor(
                self._thread_pool, self.ha_client.get_all_entities
            )
            self._ha_context_cache = self._build_ha_context_string(entities)
            self._ha_context_timestamp = time.time()
        except Exception as e:
            self.ai_logger.debug(f"Failed to update HA context: {e}")

    def _build_ha_context_string(self, entities) -> str:
        if not entities: return ""
        lines = ["## STATO CASA (Home Assistant):"]
        by_domain = {}
        for entity in entities:
            by_domain.setdefault(entity.domain, []).append(entity)

        if "light" in by_domain:
            parts = []
            for e in by_domain["light"]:
                name = e.entity_id.replace("light.", "")
                if e.state == "on":
                    brightness = e.attributes.get("brightness", 255)
                    pct = round(brightness / 255 * 100)
                    parts.append(f"{name}=ON({pct}%)")
                else:
                    parts.append(f"{name}=OFF")
            lines.append(f"- Luci: {', '.join(parts)}")

        if "cover" in by_domain:
            parts = []
            for e in by_domain["cover"]:
                name = e.entity_id.replace("cover.", "")
                pos = e.attributes.get("current_position", "?")
                parts.append(f"{name}={pos}%")
            lines.append(f"- Tapparelle: {', '.join(parts)}")

        if "climate" in by_domain:
            for e in by_domain["climate"]:
                name = e.entity_id.replace("climate.", "")
                temp = e.attributes.get("current_temperature", "?")
                target = e.attributes.get("temperature", "?")
                mode = e.state
                humidity = e.attributes.get("current_humidity", "")
                text = f"{name}: {temp}°C (target: {target}°C), modo: {mode}"
                if humidity: text += f", umidità: {humidity}%"
                lines.append(f"- Clima: {text}")

        if "switch" in by_domain:
            parts = []
            for e in by_domain["switch"]:
                name = e.entity_id.replace("switch.", "")
                parts.append(f"{name}={e.state.upper()}")
            lines.append(f"- Switch: {', '.join(parts)}")

        if "sensor" in by_domain:
            parts = []
            for e in by_domain["sensor"]:
                unit = e.attributes.get("unit_of_measurement", "")
                if unit in ("°C", "%", "W", "kWh", "lx"):
                    name = e.entity_id.replace("sensor.", "")
                    parts.append(f"{name}={e.state}{unit}")
            if parts:
                lines.append(f"- Sensori: {', '.join(parts[:8])}")

        return "\n".join(lines)

    def _get_master_prompt(self) -> str:
        now = time.time()
        if now - self._master_prompt_cache["timestamp"] < 60.0:
            return self._master_prompt_cache["content"]
        try:
            if os.path.exists(self._master_prompt_path):
                with open(self._master_prompt_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                self._master_prompt_cache = {"content": content, "timestamp": now}
                return content
        except Exception:
            pass
        return ""

    def _build_base_system_prompt(self) -> str:
        robot_cfg = self.config.robot
        # PROMPT ALLEGGERITO: Niente formattazioni complesse per non innescare il ragionamento AI.
        return f"""Sei {robot_cfg.name} – "{robot_cfg.full_name}".
Sei un assistente robotico domestico empatico, creato da {robot_cfg.creator}. 
Vivi nella casa dell'utente e comunichi con lui solo tramite voce verbale.

REGOLE DI COMUNICAZIONE:
1. Parla SEMPRE e SOLO in italiano naturale, amichevole e molto conciso.
2. DIVIETO ASSOLUTO DI INGLESE: Non generare mai frasi interne come "I'm analyzing", "I've formulated".
3. NIENTE FORMATTAZIONE: Non usare asterischi, parentesi, grassetto o tag markdown. Genera solo il testo da pronunciare ad alta voce.
"""

    def _build_dynamic_context(self) -> str:
        now = datetime.datetime.now()
        time_str = now.strftime("%A %d %B %Y, ore %H:%M")
        
        ctx = f"[DATI DI SISTEMA (NON LEGGERE): Ora={time_str}"
        
        stats = self._system_stats
        if stats["cpu_percent"] is not None:
            ctx += f" | CPU={stats['cpu_percent']:.0f}%, RAM={stats['ram_percent']:.0f}%"
            
        if self._current_user_profile:
            name = self._current_user_profile.get("user_profile", {}).get('name', 'Sconosciuto')
            ctx += f" | Utente_Riconosciuto={name}"
            
        ctx += "]\n"
        
        if self._ha_context_cache:
            ctx += f"[STATO CASA: {self._ha_context_cache.replace('## STATO CASA (Home Assistant):', '').strip()}]\n"
            
        master_content = self._get_master_prompt()
        if master_content:
            ctx += f"[MEMORIA: {master_content}]\n"
            
        return ctx

    def _setup_ros_interfaces(self):
        self.tts_pub = self.create_publisher(String, 'ai/tts/speak', 10)
        self.state_pub = self.create_publisher(String, 'ai/state', 10)
        self.face_pub = self.create_publisher(String, 'ai/face/recognized', 10)

        qos_profile = QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.text_in_pub = self.create_publisher(String, 'ai/conversation/input', 10)
        self.text_out_pub = self.create_publisher(String, 'ai/conversation/response', qos_profile)
        self.raw_text_pub = self.create_publisher(String, 'ai/conversation/raw', 10)
        self.cmd_vel_pub = self.create_publisher(Twist, 'cmd_vel', 10)
        self.nav_client._cmd_vel_pub = self.cmd_vel_pub

        self.create_subscription(String, 'ai/input/text', self._text_input_callback, 10)
        self.create_subscription(Bool, 'ai/input/mic_mute', self._mute_callback, 10)
        self.create_subscription(CompressedImage, '/rgb/image/compressed', self._camera_callback, 1)
        self.create_subscription(DiagnosticArray, '/diagnostics', self._diagnostics_callback, 10)
        self.create_subscription(AudioData, '/audio/audio', self._raw_audio_callback, 10)

        self.create_timer(1.0, self._publish_status)
        self._reactive_timer = self.create_timer(0.02, self._reactive_loop)

        fr_cfg = self.config.face_recognition
        if fr_cfg.enabled and self.face_recognition_service.is_available:
            self.create_timer(fr_cfg.recognition_interval, self._face_recognition_callback)

        def _visual_memory_timer_cb():
            if hasattr(self, '_loop') and self._loop is not None and self._loop.is_running() and not self._shutdown_flag:
                asyncio.run_coroutine_threadsafe(self.visual_memory_service.spin(), self._loop)
        self.create_timer(1.0, _visual_memory_timer_cb)

        self.create_timer(self._recovery_interval, self._recovery_check)
        self._ha_update_timer = self.create_timer(10.0, self._schedule_ha_context_update)
        self.srv_emergency = self.create_service(Trigger, 'ai/emergency_stop', self._emergency_callback)

    def _register_builtin_skills(self):
        self.skill_registry.register(HomeAssistantSkill(self.ha_client))
        self.skill_registry.register(NavigationSkill(nav_client=self.nav_client, move_handler=self.move_relative))
        self.skill_registry.register(SearchSkill(
            nav_client=self.nav_client, llm_service=self.llm_service,
            camera_provider=lambda: self._latest_frame.b64 if self._latest_frame else None
        ))
        self.skill_registry.register(VisualExplorationSkill(
            nav_client=self.nav_client, llm_service=self.llm_service,
            camera_provider=lambda: self._latest_frame.cv_image if self._latest_frame else None,
            move_handler=self.move_relative
        ))

    def _subscribe_to_events(self):
        self.event_bus.subscribe(EventType.VOICE_COMMAND_RECOGNIZED, self._on_voice_command)
        self.event_bus.subscribe(EventType.TASK_CREATED, self._on_task_created)
        self.event_bus.subscribe(EventType.ERROR_OCCURRED, self._on_error)
        self.event_bus.subscribe("asr_audio_chunk", self._on_asr_audio_chunk)
        self.event_bus.subscribe("llm_audio_chunk", self._on_llm_audio_chunk)
        self.event_bus.subscribe(EventType.HA_EVENT_RECEIVED, self._on_ha_event_for_perception)

    def _text_input_callback(self, msg):
        asyncio.run_coroutine_threadsafe(self.process_input(msg.data, source="text"), self._loop)

    def _mute_callback(self, msg):
        self._mic_muted = msg.data
        if self._mic_muted:
            self.ai_logger.info("🎤 Microfono MUTATO (In attesa della Wake Word)")
            if getattr(self, '_mute_timer', None):
                self._mute_timer.cancel()
        else:
            self.ai_logger.info("🎤 Microfono APERTO (In ascolto!)")
            if self._connectivity_state == "ONLINE" and self.llm_service:
                ctx = self._build_dynamic_context()
                asyncio.run_coroutine_threadsafe(
                    self.llm_service.generate_live(prompt=ctx, context=None, functions=[], images=[]),
                    self._loop
                )
            def auto_mute():
                self._mic_muted = True
                self.ai_logger.info("🎤 Microfono AUTO-MUTATO per timeout")
            
            if getattr(self, '_mute_timer', None):
                self._mute_timer.cancel()
            self._mute_timer = threading.Timer(15.0, auto_mute)
            self._mute_timer.start()

    def _on_voice_command(self, event):
        text = event.data.get("text")
        if text:
            self.ai_logger.info(f"Voice command recognized: {text}")
            asyncio.run_coroutine_threadsafe(self.process_input(text, source="voice"), self._loop)

    def _camera_callback(self, msg: CompressedImage):
        try:
            if 'jpeg' in msg.format or 'jpg' in msg.format or 'png' in msg.format:
                self._latest_frame = CameraFrame(raw=msg.data)
        except Exception as e:
            self.ai_logger.debug(f"Camera frame storage failed: {e}")

    def _diagnostics_callback(self, msg: DiagnosticArray):
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
            except (ValueError, TypeError):
                pass

    def _publish_status(self):
        msg = String()
        msg.data = f"{self.state_machine.state.name}|{self._connectivity_state}"
        self.state_pub.publish(msg)

    def _publish_metrics(self):
        self._metrics.update({
            "llm_latency_p50": self._llm_latency_tracker.percentile(50),
            "llm_latency_p95": self._llm_latency_tracker.percentile(95),
            "llm_latency_max": self._llm_latency_tracker.max(),
        })
        msg = String(data=json.dumps(self._metrics))
        self._metrics_pub.publish(msg)

    def _recovery_check(self):
        if self._connectivity_state == "OFFLINE":
            self._llm_errors_consecutive = 0
            asyncio.run_coroutine_threadsafe(self._test_llm_connection(), self._loop)

    async def _test_llm_connection(self):
        try:
            await asyncio.wait_for(
                self.llm_service.generate_live(prompt="ping", context=None, functions=[], images=[]), timeout=10.0
            )
            self._connectivity_state = "ONLINE"
            self.ai_logger.info("Recovery successful, back ONLINE")
        except Exception:
            pass

    def _emergency_callback(self, request, response):
        self.emergency_stop()
        response.success = True
        return response

    def _face_recognition_callback(self):
        frame = self._latest_frame
        if not frame: return
        try:
            cv_image = frame.cv_image
            if cv_image is None: return

            result = self.face_recognition_service.recognize(cv_image)
            if result.num_faces_detected > 0:
                self._current_user_profile = self.face_recognition_service.get_profile_for_gemini(result)
                self.world_model.current_user = {
                    "name": result.name if result.recognized else None,
                    "confidence": result.confidence if result.recognized else 0,
                }
                if result.recognized:
                    msg = String(data=f"✅ {result.name} (confidence: {result.confidence:.2f})")
                elif result.ask_confirmation:
                    msg = String(data=f"❓ Maybe {result.likely_user}")
                else:
                    msg = String(data=f"👤 Unknown face")
                self.face_pub.publish(msg)
        except Exception:
            pass

    def _on_asr_audio_chunk(self, event):
        pass

    def _raw_audio_callback(self, msg: AudioData):
        if getattr(self, '_mic_muted', True):
            return
        if self._connectivity_state == "ONLINE" and self.llm_service:
            asyncio.run_coroutine_threadsafe(self.llm_service.send_audio_chunk(msg.data), self._loop)

    def _on_llm_audio_chunk(self, event):
        audio_data = event.data.get("data")
        if audio_data:
            self.tts_service.play_raw_pcm(audio_data)

    def _on_ha_event_for_perception(self, data):
        if data.get("action") == "active_perception_trigger":
            try:
                self.visual_memory_service.force_capture()
            except Exception:
                pass

    async def process_input(self, text: str, source: str = "user"):
        self._metrics["requests_total"] += 1

        if SAFETY_PATTERN.search(text):
            self.emergency_stop()
            self.text_out_pub.publish(String(data="Fermo tutto!"))
            await self.tts_service.speak("Fermo tutto!")
            self._metrics["requests_success"] += 1
            return

        async with self._processing_lock:
            success = await self._process_input_locked(text, source)
            if success: self._metrics["requests_success"] += 1
            else: self._metrics["requests_failed"] += 1

    async def _process_input_locked(self, text: str, source: str) -> bool:
        start_time = time.perf_counter()

        try:
            try:
                await asyncio.wait_for(self._ready_event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                return False

            if self.state_machine.state not in [SystemState.READY, SystemState.LISTENING]:
                return False

            self.state_machine.transition_to(SystemState.PROCESSING)
            self.text_in_pub.publish(String(data=f"[{source}] {text}"))

            clean_text = self.sanitizer.sanitize(text)
            self.world_model.recent_interactions.append(f"User: {clean_text}")

            dynamic_ctx = self._build_dynamic_context()
            augmented_prompt = f"{dynamic_ctx}L'utente dice: \"{clean_text}\""

            skill = self.skill_registry.find_best_match(clean_text, min_confidence=0.95)
            if skill:
                self.ai_logger.info(f"Fast-path skill match: {skill.name}")
                try:
                    texts_to_speak = []
                    result_or_gen = await asyncio.wait_for(skill.safe_execute(clean_text), timeout=self._skill_timeout)
                    last_result = None
                    if inspect.isasyncgen(result_or_gen):
                        async for res in result_or_gen:
                            last_result = res
                            if res.speak: texts_to_speak.append(res.speak)
                    else:
                        last_result = result_or_gen
                        if last_result.speak: texts_to_speak.append(last_result.speak)

                    self._metrics["skill_calls"] += 1
                    if last_result and last_result.success:
                        self._llm_errors_consecutive = 0 
                    
                    for txt in texts_to_speak:
                        self.text_out_pub.publish(String(data=txt))
                        await self.tts_service.speak(txt)
                    return True

                except Exception as e:
                    self.ai_logger.error(f"Skill error: {e}")
                    self._metrics["skill_errors"] += 1
                    return False

            if self._connectivity_state == "OFFLINE":
                offline_msg = "Sono offline, riprova più tardi."
                self.text_out_pub.publish(String(data=offline_msg))
                await self.tts_service.speak(offline_msg)
                return True

            functions = [s.to_function_declaration() for s in self.skill_registry.get_all()]
            gemini_functions = [self._convert_to_gemini_function(f) for f in functions]

            frame = self._latest_frame
            images = [frame.b64] if (self._is_vision_request(clean_text) and frame) else []

            self._metrics["llm_calls"] += 1
            try:
                response = await asyncio.wait_for(
                    self.llm_service.generate_live(
                        prompt=augmented_prompt, 
                        context=None, functions=gemini_functions, images=images
                    ),
                    timeout=self._llm_timeout
                )
            except Exception as e:
                self.ai_logger.error(f"LLM call failed: {e}")
                self._track_llm_error()
                return False

            latency = response.latency_ms / 1000.0
            self._llm_latency_tracker.add(latency)
            self._track_connectivity_from_latency()

            if response.actions:
                tasks = [self._execute_llm_action(a) for a in response.actions]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                speak_texts = []
                for res in results:
                    if isinstance(res, list): speak_texts.extend(res)
                for txt in speak_texts:
                    if txt:
                        self.text_out_pub.publish(String(data=txt))
                        await self.tts_service.speak(txt)

            if response.text:
                self.raw_text_pub.publish(String(data=response.text))
                spoken_text = self._extract_speech(response.text)
                
                if spoken_text:
                    self.text_out_pub.publish(String(data=spoken_text))
                    await self.tts_service.speak(spoken_text)
                    self.world_model.recent_interactions.append(f"Robot: {spoken_text[:50]}...")
                else:
                    self.ai_logger.warning("Nessun testo parlato estratto.")

            if self.config.rag.enabled and response.text.strip():
                await self._store_memory_background(clean_text, response.text, "conversation")

            return True

        except Exception as e:
            self.ai_logger.error(f"Errore in process_input: {e}", exc_info=True)
            return False
        finally:
            if self.state_machine.state == SystemState.PROCESSING:
                self.state_machine.transition_to(SystemState.READY)
            duration = (time.perf_counter() - start_time) * 1000
            self.ai_logger.info(f"Processing completed in {duration:.1f}ms")

    def _track_connectivity_from_latency(self):
        if len(self._llm_latency_tracker.values) < 3: return
        p95 = self._llm_latency_tracker.percentile(95)
        old_state = self._connectivity_state
        if old_state == "ONLINE" and p95 > self._conn_degraded_threshold:
            self._connectivity_state = "DEGRADED"
        elif old_state == "DEGRADED" and p95 < self._conn_online_threshold:
            self._connectivity_state = "ONLINE"
        if old_state != self._connectivity_state:
            self.ai_logger.info(f"🌐 Connectivity: {old_state} → {self._connectivity_state} (P95: {p95:.1f}s)")

    def _track_llm_error(self):
        self._llm_errors_consecutive += 1
        if self._llm_errors_consecutive >= 3:
            self._connectivity_state = "OFFLINE"
            self.ai_logger.warning("🌐 Connectivity → OFFLINE")

    async def _execute_llm_action(self, action_data: Dict[str, Any]) -> List[str]:
        skill_name = action_data.get("action_type")
        args = action_data.get("args", {})
        texts_to_speak = []

        skill = self.skill_registry.get(skill_name)
        if not skill: return texts_to_speak

        execution_text = args.get("text", "")
        if skill_name == "navigation" and "action" in args:
            nav_action = args["action"]
            if nav_action == "explore": execution_text = "esplora"
            elif nav_action == "stop": execution_text = "fermati"
            elif nav_action == "move_relative": execution_text = f"vai {args.get('direction', '')}"

        try:
            result_or_gen = await skill.safe_execute(execution_text)
            last_result = None
            if inspect.isasyncgen(result_or_gen):
                async for res in result_or_gen:
                    if res.speak: texts_to_speak.append(res.speak)
                    last_result = res
            else:
                last_result = result_or_gen
                if last_result.speak: texts_to_speak.append(last_result.speak)
        except Exception as e:
            self.ai_logger.error(f"Skill error: {e}")
            
        return texts_to_speak

    def _convert_to_gemini_function(self, func_decl: Dict) -> Any:
        return FunctionDeclaration(**func_decl)

    def _extract_speech(self, raw_text: str) -> str:
        """Filtro super intelligente: tollera i ragionamenti di Gemini ed estrae la voce."""
        
        # 1. Pulisce markdown pesanti che l'IA usa per le azioni
        clean = re.sub(r'\*\*.*?\*\*', '', raw_text, flags=re.DOTALL)
        clean = re.sub(r'\*.*?\*', '', clean, flags=re.DOTALL)
        clean = clean.strip()
        
        # 2. Rileva se l'IA sta pensando ad alta voce in inglese
        english_indicators = [
            " i'm ", " i've ", " i''m ", " i''ve ", " crafted ", " refined ", 
            " response ", " formulation ", " tone ", " realized ", " acknowledged ", " user "
        ]
        
        test_str = f" {clean.lower()} "
        is_reasoning = any(ind in test_str for ind in english_indicators)
        
        if is_reasoning:
            self.ai_logger.warning("Gemini ha ragionato in inglese. Recupero la frase in italiano...")
            
            # Rimuove le virgolette attorno a parole singole (senza spazi in mezzo)
            # Questo impedisce alle virgolette interne (es. un termine virgolettato) di interrompere il regex.
            clean_for_quotes = re.sub(r'["“”\']([^\s"“”\']+)["“”\']', r'\1', clean)
            
            # Cerca tra virgolette che contengono frasi intere (conterranno sicuramente spazi o più parole)
            quotes = re.findall(r'["“”]([^"“”]+)["“”]', clean_for_quotes)
            if quotes:
                salvage_text = max(quotes, key=len).strip()
                self.ai_logger.info(f"Salvata risposta dalle virgolette: {salvage_text}")
                return salvage_text
                
            # Piano B: prende l'ultima riga di testo che non contiene parole inglesi
            lines = [l.strip() for l in clean.split('\n') if l.strip()]
            for line in reversed(lines):
                if not any(ind in f" {line.lower()} " for ind in english_indicators):
                    self.ai_logger.info(f"Salvata l'ultima riga valida: {line}")
                    return line
                    
            self.ai_logger.warning("Recupero fallito, stringa vuota per non far uscire inglese dallo speaker.")
            return ""
            
        # Se non è un ragionamento, ritorna la stringa pulita dalle virgolette esterne
        return clean.strip('"\'') 

    def _is_vision_request(self, text: str) -> bool:
        keywords = ['vedi', 'guarda', 'cosa vedi', 'dimmi', 'descrivi', 'mostra']
        return any(kw in text.lower() for kw in keywords)

    async def _store_memory_background(self, user_text: str, robot_text: str, mem_type: str):
        if not robot_text.strip() or self._memory_queue is None:
            return
            
        pending = PendingMemory(
            user_text=user_text,
            robot_text=robot_text,
            mem_type=mem_type,
            timestamp=time.time()
        )
        try:
            self._memory_queue.put_nowait(pending)
        except asyncio.QueueFull:
            self.ai_logger.warning("Memory queue full, dropping memory")

    def move_relative(self, direction: str, speed: float = 0.3, duration: float = 1.0, degrees: float = None):
        twist = Twist()
        dir_low = direction.lower().strip()
        angular_speed = abs(speed) * 2.0

        if dir_low in ("avanti", "forward"): twist.linear.x = abs(speed)
        elif dir_low in ("indietro", "backward"): twist.linear.x = -abs(speed)
        elif dir_low in ("sinistra", "left"): twist.angular.z = angular_speed
        elif dir_low in ("destra", "right"): twist.angular.z = -angular_speed
        else: return

        with self._reactive_cmd_vel_lock:
            self._reactive_cmd_vel = twist
        asyncio.run_coroutine_threadsafe(self._schedule_stop(duration), self._loop)

    async def _schedule_stop(self, duration: float):
        if self._move_lock is None: return
        async with self._move_lock:
            if self._move_task and not self._move_task.done():
                self._move_task.cancel()
            async def stop_after(sec):
                await asyncio.sleep(sec)
                with self._reactive_cmd_vel_lock: self._reactive_cmd_vel = Twist()
            self._move_task = asyncio.create_task(stop_after(duration))

    async def _startup(self):
        try:
            self.state_machine.transition_to(SystemState.INITIALIZING)
            self.ai_logger.info("Starting up services...")

            if self.config.home_assistant.token: await self.ha_client.connect()

            await self.llm_service.start_persistent_live()
            self.ai_logger.info("Sistema pronto per ricevere audio via ROS 2 (/audio/audio)")

            self.state_machine.transition_to(SystemState.READY)
            self._ready_event.set()
            self.ai_logger.info("System READY")
            try:
                await self.tts_service.speak("Sistema avviato e pronto.")
            except Exception:
                pass
        except Exception as e:
            self.ai_logger.error(f"Startup failed: {e}")
            self.state_machine.transition_to(SystemState.ERROR, str(e))

    async def shutdown(self):
        self.ai_logger.info("Shutting down AI orchestrator...")
        self._shutdown_flag = True

        # Cancella il timer di auto-mute per evitare callback su nodo distrutto
        if getattr(self, '_mute_timer', None) is not None:
            self._mute_timer.cancel()
            self._mute_timer = None

        with self._reactive_cmd_vel_lock: self._reactive_cmd_vel = Twist()
        await self._cancel_move_task()

        if self._memory_worker_task: self._memory_worker_task.cancel()
        try: await self.llm_service.shutdown()
        except Exception: pass
        
        if self.deepseek_service:
            try: await self.deepseek_service.close()
            except Exception: pass
            
        if hasattr(self, '_scheduler'): self._scheduler.shutdown(wait=False)
        if hasattr(self, '_thread_pool'): self._thread_pool.shutdown(wait=False)
        self._loop.stop()

    def _setup_nightly_job(self):
        if not HAS_SCHEDULER: return
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            lambda: asyncio.run_coroutine_threadsafe(self.nightly_dream_service.run_analysis(), self._loop),
            'cron', hour=2, minute=0
        )
        self._scheduler.start()
        self.ai_logger.info("Nightly dream job scheduled for 02:00 AM.")

    def _run_async_loop(self):
        asyncio.set_event_loop(self._loop)
        try: self._loop.run_forever()
        except Exception as e: self.ai_logger.error(f"Fatal error in async loop: {e}")

    # -----------------------------------------------------------------------
    # Event Bus Placeholders
    # -----------------------------------------------------------------------
    def _on_task_created(self, event):
        self.ai_logger.debug(f"Task created event: {event.data}")

    def _on_error(self, event):
        self.ai_logger.warning(f"Error event received: {event.data}")

def main(args=None):
    rclpy.init(args=args)
    node = AIOrchestrator()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if node._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(node.shutdown(), node._loop)
            future.result(timeout=10.0)
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()