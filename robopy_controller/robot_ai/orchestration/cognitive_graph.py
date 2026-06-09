"""
Robot AI Orchestration - Cognitive Graph & Biomimetic Alignment
==============================================================
Implements the LangGraph-style cognitive state and biomimetic alignment mechanics.
Includes Critic/Evaluator Node for dopamine-like RPE (Reward Prediction Error) calculation,
ChromaDB episodic persistence (Severus RAG integration), and Predictive Router Node
for synaptic-like inhibition prompt injection.
"""

import os
import time
import re
import asyncio
from typing import List, Dict, Any, Optional, Union
from robot_ai.utils import get_logger
from robot_ai.rag.memory_store import Memory, MemoryType

logger = get_logger("cognitive_graph")


class MarcusAgentState:
    """
    Represents the operational state of the Marcus Agent.
    Extends the state with dopamine biometric alignment parameters.
    """
    def __init__(self, messages: Optional[List[Dict[str, Any]]] = None, current_task: str = ""):
        self.messages: List[Dict[str, Any]] = messages or []
        self.current_task: str = current_task
        
        # Dopamine biometric alignment fields
        self.reward_score: float = 0.0  # Local dopamine balance
        self.last_rpe: float = 0.0       # Reward Prediction Error
        self.feedback_context: Dict[str, Any] = {}  # Historic trigger context
        
        # Synaptic control fields
        self.inhibited_skills: List[str] = []
        self.system_prompt_override: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "messages": self.messages,
            "current_task": self.current_task,
            "reward_score": self.reward_score,
            "last_rpe": self.last_rpe,
            "feedback_context": self.feedback_context,
            "inhibited_skills": self.inhibited_skills,
            "system_prompt_override": self.system_prompt_override
        }

    def update(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)


class CriticEvaluatorNode:
    """
    Evaluator/Critic Node: Parses user verbal input or ROS 2 error logs,
    computes dopamine Reward Prediction Error (RPE), updates reward balance,
    and persists episodic alignment events to ChromaDB.
    """
    def __init__(self, memory_store: Any, embedding_service: Any):
        self.memory_store = memory_store
        self.embedding_service = embedding_service
        self.learning_rate = 0.2
        
        # Verbal patterns
        self.pos_patterns = re.compile(
            r"\b(ottimo|bravo|fai cos[iì]|va bene|perfetto|eccellente|giusto|mi piace|bene|grazie|ok)\b", 
            re.IGNORECASE
        )
        self.neg_patterns = re.compile(
            r"\b(no|fermati|non muoverti cos[iì]|sbagliato|brutto|ferma|zitto|pessimo|non fare|stop|errore|fallito)\b", 
            re.IGNORECASE
        )

    async def evaluate(self, state: MarcusAgentState, user_text: str, skill_outcome: Optional[Dict[str, Any]] = None) -> MarcusAgentState:
        """
        Parses text or log outcomes to adjust dopamine reward, compute RPE,
        and save RAG episodic alignment events.
        """
        feedback_val = 0.0
        feedback_source = "neutral"
        skill_name = "general"

        # 1. Analyze ROS 2 deterministic outcomes / logs
        if skill_outcome:
            skill_name = skill_outcome.get("skill_name", "unknown")
            success = skill_outcome.get("success", True)
            error_msg = skill_outcome.get("error_message", "")
            
            if not success:
                feedback_val = -1.0
                feedback_source = f"ros2_error: {error_msg}"
            else:
                feedback_val = 0.5  # Soft success reward
                feedback_source = "ros2_success"

        # 2. Analyze user verbal trigger
        if user_text:
            pos_matches = self.pos_patterns.findall(user_text)
            neg_matches = self.neg_patterns.findall(user_text)
            
            if neg_matches:
                feedback_val = -1.0
                feedback_source = f"verbal_negative: {neg_matches[0]}"
            elif pos_matches:
                feedback_val = 1.0
                feedback_source = f"verbal_positive: {pos_matches[0]}"

        # 3. Calculate Reward Prediction Error (RPE)
        # expectation is based on current reward_score scaled to the same bounds
        expected_reward = max(-1.0, min(1.0, state.reward_score * 0.5))
        rpe = feedback_val - expected_reward
        
        # Apply biometric learning update
        new_reward_score = state.reward_score + self.learning_rate * rpe
        
        logger.info(
            f"[Critic Node] Evaluated '{feedback_source}'. "
            f"Feedback: {feedback_val}, Expectation: {expected_reward:.2f}, "
            f"RPE: {rpe:.2f}, New Reward: {new_reward_score:.2f}"
        )
        
        # Update state
        state.last_rpe = rpe
        state.reward_score = new_reward_score
        state.feedback_context = {
            "source": feedback_source,
            "trigger_text": user_text,
            "timestamp": time.time(),
            "skill_target": skill_name
        }

        # 4. Episodic Persistence on ChromaDB if RPE is significant (|RPE| >= 0.3)
        if abs(rpe) >= 0.3:
            metric_type = "reward" if rpe > 0 else "penalty"
            content = (
                f"Al tempo {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}, "
                f"l'utente ha espresso un feedback associato al task '{state.current_task}' "
                f"e alla skill '{skill_name}'. Feedback: {feedback_source}. RPE: {rpe:.2f}. "
                f"Risultato: {metric_type.upper()}."
            )
            
            # Save the event to ChromaDB via the ChromaNativeStore
            try:
                embedding = await self.embedding_service.embed(content)
                memory = Memory(
                    id="",
                    content=content,
                    memory_type=MemoryType.SYSTEM_EVENT,
                    embedding=embedding,
                    metadata={
                        "type": "alignment_event",
                        "metric": metric_type,
                        "skill_target": skill_name,
                        "value": float(rpe),
                        "task_target": state.current_task,
                        "timestamp": time.time()
                    }
                )
                
                # Perform add in background or block since it's an async node
                self.memory_store.add(memory)
                logger.info(f"[RAG Severus] Persistito evento di allineamento su ChromaDB (metric: {metric_type})")
            except Exception as ex:
                logger.error(f"[RAG Severus] Impossibile salvare evento su ChromaDB: {ex}")

        return state


class PredictiveRouterNode:
    """
    Router Node with Synaptic Inhibition: Semantically queries ChromaDB for
    past penalty/reward events, dynamically updating the System Prompt with inhibition instructions.
    """
    def __init__(self, memory_store: Any):
        self.memory_store = memory_store

    async def route(self, state: MarcusAgentState, base_system_prompt: str) -> MarcusAgentState:
        """
        Executes a vector search on past alignment events to detect past negative feedbacks
        and inject synaptic inhibition prompt directives.
        """
        if not state.current_task:
            state.system_prompt_override = base_system_prompt
            return state

        logger.info(f"[Router Node] Esecuzione query semantica preventiva per task: '{state.current_task}'")
        
        # Search the ChromaNativeStore
        inhibitions = []
        inhibited_skills = []
        
        try:
            # We do a semantic search on Chroma
            search_results = await self.memory_store.search(state.current_task, top_k=5)
            
            for res in search_results:
                meta = res.memory.metadata or {}
                # Check for strict alignment metadata and score threshold (similarity > 0.6)
                if meta.get("type") == "alignment_event" and res.score > 0.6:
                    metric = meta.get("metric")
                    skill_target = meta.get("skill_target", "unknown")
                    val = meta.get("value", 0.0)
                    
                    if metric == "penalty":
                        logger.warning(
                            f"[Inibizione Sinaptica] Rilevato episodio di penalità passata. "
                            f"Skill: {skill_target}, Score: {res.score:.2f}, RPE: {val:.2f}"
                        )
                        inhibited_skills.append(skill_target)
                        inhibitions.append(
                            f"ATTENZIONE: In un episodio simile l'utente ha penalizzato il comportamento "
                            f"associato alla skill '{skill_target}' (Task: '{meta.get('task_target', '')}'). "
                            f"Cambia strategia, traiettoria o registro linguistico per evitare questo errore!"
                        )
        except Exception as ex:
            logger.error(f"[Router Node] Errore durante query semantica: {ex}")

        # Inject dynamic prompt rules
        override_prompt = base_system_prompt
        if inhibitions:
            state.inhibited_skills = inhibited_skills
            inhibition_block = "\n\n=== REGOLE DI INIBIZIONE COGNITIVA (SINAPSI ATTIVE) ===\n"
            for i, inh in enumerate(inhibitions, 1):
                inhibition_block += f"{i}. {inh}\n"
            inhibition_block += "========================================================\n"
            override_prompt = base_system_prompt + inhibition_block
            logger.info("[Router Node] System Prompt arricchito con regole di inibizione sinaptica.")
        else:
            state.system_prompt_override = base_system_prompt

        state.system_prompt_override = override_prompt
        return state


class MarcusStateGraph:
    """
    A lightweight, production-grade StateGraph manager mimicking LangGraph's lifecycle.
    Compiles Critic and Router nodes into an executable async pipeline.
    """
    def __init__(self, memory_store: Any, embedding_service: Any):
        self.critic = CriticEvaluatorNode(memory_store, embedding_service)
        self.router = PredictiveRouterNode(memory_store)

    async def run_input_flow(self, state: MarcusAgentState, user_text: str, base_system_prompt: str) -> MarcusAgentState:
        """
        Executes: User Input -> Critic Node (verbal feedback RPE) -> Router Node (synaptic conditioning)
        """
        # 1. Evaluate user input feedback first (dopamine check)
        state = await self.critic.evaluate(state, user_text=user_text)
        
        # 2. Run Router Node to inject prompt conditioning
        state = await self.router.route(state, base_system_prompt)
        
        return state

    async def run_post_execution_flow(self, state: MarcusAgentState, skill_name: str, success: bool, error_message: str = "") -> MarcusAgentState:
        """
        Executes: Post ROS 2 Skill execution -> Critic Node (deterministic RPE check)
        """
        outcome = {
            "skill_name": skill_name,
            "success": success,
            "error_message": error_message
        }
        state = await self.critic.evaluate(state, user_text="", skill_outcome=outcome)
        return state
