#!/usr/bin/env python3
"""
Robot AI Orchestrator Node
===========================
Main ROS 2 node for the AI system.
Coordinates perception, reasoning, and action.
"""

import sys
import os
import time
import asyncio
import threading
from typing import Any, Dict, List, Optional

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from std_msgs.msg import String, Bool
from geometry_msgs.msg import PoseStamped

# Add proper path if running as script
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from robot_ai.core import (
    ConfigManager, EventBus, StateMachine, CircuitBreakerRegistry,
    AIError, EventType, SystemState,
)
from robot_ai.utils import InputSanitizer, AILogger, get_logger
from robot_ai.services import LLMService, FunctionDeclaration, TTSService, ASRService, EmbeddingService
from robot_ai.rag import MemoryStore, Memory, MemoryType, MetadataManager
from robot_ai.integrations import HomeAssistantClient, NavigationClient
from robot_ai.skills import SkillRegistry, SkillResult
from robot_ai.skills.builtin import HomeAssistantSkill, NavigationSkill


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
        self.embedding_service = EmbeddingService(self.config_manager)
        self.tts_service = TTSService(self.config_manager)
        self.asr_service = ASRService(self.config_manager)
        
        # 4. Integrations
        self.ha_client = HomeAssistantClient(self.config_manager)
        self.nav_client = NavigationClient(self, self.config_manager)
        
        # 5. Skills
        self.skill_registry = SkillRegistry()
        self._register_builtin_skills()
        
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
        
        # Subscribers
        self.create_subscription(String, 'ai/input/text', self._text_input_callback, 10)
        self.create_subscription(Bool, 'ai/input/mic_mute', self._mute_callback, 10)
        
        # Timer for status
        self.create_timer(1.0, self._publish_status)
    
    def _register_builtin_skills(self):
        """Register built-in skills."""
        self.skill_registry.register(HomeAssistantSkill(self.ha_client))
        self.skill_registry.register(NavigationSkill(self.nav_client))
    
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
            
            # 1. Sanitize
            clean_text = self.sanitizer.sanitize(text)
            
            # 2. Check Skills (Fast Path)
            # Check for direct skill match with high confidence
            skill = self.skill_registry.find_best_match(clean_text, min_confidence=0.8)
            if skill:
                self.ai_logger.info(f"Fast-path skill match: {skill.name}")
                result = await skill.safe_execute(clean_text)
                await self._handle_execution_result(result)
                self.state_machine.transition_to(SystemState.READY)
                return
            
            # 3. RAG Retrieval
            context_memories = []
            if self.config.rag.enabled:
                embedding = await self.embedding_service.embed(clean_text)
                results = self.memory_store.search(
                    embedding, 
                    top_k=self.config.rag.top_k,
                    min_score=self.config.rag.min_score
                )
                context_memories = [r.memory for r in results]
                self.ai_logger.debug(f"Retrieved {len(context_memories)} memories")
            
            # 4. LLM Reasoning
            # Prepare context
            llm_context = self._build_llm_context(context_memories)
            
            # Get available functions
            functions = [s.to_function_declaration() for s in self.skill_registry.get_all()]
            
            # Generate response
            response = await self.llm_service.generate(
                prompt=clean_text,
                context=llm_context,
                functions=[self._convert_to_gemini_function(f) for f in functions]
            )
            
            # 5. Execute Actions
            if response.actions:
                self.ai_logger.info(f"LLM proposed actions: {len(response.actions)}")
                for action_data in response.actions:
                    await self._execute_llm_action(action_data)
            
            # 6. Speak Response
            if response.text:
                await self.tts_service.speak(response.text)
            
            # 7. Store Interaction
            if self.config.rag.enabled:
                await self._store_memory(clean_text, response.text, "conversation")
            
        except Exception as e:
            self.ai_logger.error(f"Error processing input: {e}", exc_info=True)
            await self.tts_service.speak("Scusa, si è verificato un errore.")
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
        # For now, skills take text, but we should upgrade BaseSkill to take **kwargs
        
        # HACK: For now, we mainly use HA and Nav skills which parse text. 
        # But proper function calling gives us args.
        # Let's try to pass structured data if possible, or reconstruct a command
        
        # Better: Skills should have execute_structured method
        # For this phase, we'll just log
        self.ai_logger.info(f"Executing structured action {skill_name} with {args}")
        
        # TODO: Refactor BaseSkill to support structured execution alongside text parsing
        # For home assistant skill:
        if skill_name == "home_assistant":
            # Reconstruct intent dict that execute expects
            # This is a bit backward but fits the text-first design. 
            pass
            
    def _convert_to_gemini_function(self, func_decl: Dict) -> Any:
        # Helper to convert internal dict to Gemini object if needed
        # The LLMService handles dicts fine usually
        return FunctionDeclaration(**func_decl)

    async def _handle_execution_result(self, result: SkillResult):
        """Handle execution result."""
        if result.speak:
            await self.tts_service.speak(result.speak)
        
        if not result.success:
            self.ai_logger.warning(f"Skill execution failed: {result.message}")
            if not result.speak:
                await self.tts_service.speak(f"Non sono riuscito a farlo. {result.message}")

    def _build_llm_context(self, memories: List[Memory]) -> List[Dict[str, str]]:
        """Build conversation history with memories."""
        context = []
        
        # Add retrieved memories as system info
        if memories:
            memory_text = "\n".join([f"- {m.content}" for m in memories])
            context.append({
                "role": "model",  # Injected as info
                "content": f"Informazioni rilevanti dalla memoria:\n{memory_text}"
            })
            
        return context

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
        """Publish node status."""
        msg = String()
        msg.data = self.state_machine.state.name
        self.state_pub.publish(msg)

def main(args=None):
    rclpy.init(args=args)
    
    node = AIOrchestrator()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()
