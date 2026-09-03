#!/usr/bin/env python3
"""
Skill Action Server
===================
Asynchronous Action Server engine for Marcus skills with native support
for periodic progress feedback, cancellation, and immediate preemption.

Author: Marcus AI Engineering Team
Version: 01.00.00
"""

import asyncio
import inspect
import time
from typing import Dict, Any, List, Optional, Callable, AsyncGenerator

from robot_ai.skills.skill_registry import SkillRegistry
from robot_ai.integrations import NavigationClient
from robot_ai.orchestration.reactive_safety import ReactiveSafety
from robot_ai.utils import get_logger


class GoalStatus:
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    PREEMPTED = "PREEMPTED"
    FAILED = "FAILED"


class SkillActionGoal:
    def __init__(self, goal_id: str, skill_name: str, args: Dict[str, Any]):
        self.goal_id = goal_id
        self.skill_name = skill_name
        self.args = args
        self.status = GoalStatus.PENDING
        self.start_time = time.time()
        self.completion_time = 0.0
        self.feedback_history: List[Dict[str, Any]] = []
        self.speak_results: List[str] = []
        self.task: Optional[asyncio.Task] = None
        self.cancel_requested = False


class SkillActionServer:
    """
    Executes robot skills with full lifecycle tracking:
    accepts goals, emits feedback streams, and handles immediate preemption.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        nav_client: Optional[NavigationClient] = None,
        reactive_safety: Optional[ReactiveSafety] = None,
        on_feedback_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ):
        self.registry = registry
        self.nav_client = nav_client
        self.reactive_safety = reactive_safety
        self.on_feedback_callback = on_feedback_callback
        self.logger = get_logger("skill_action_server")

        self._active_goals: Dict[str, SkillActionGoal] = {}
        self._goal_counter = 0
        self._lock = asyncio.Lock()

    async def execute_goal(
        self,
        skill_name: str,
        args: Dict[str, Any],
        goal_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a skill goal asynchronously, supporting preemption and streaming feedback.
        """
        async with self._lock:
            self._goal_counter += 1
            if not goal_id:
                goal_id = f"goal_{self._goal_counter}_{int(time.time()*1000)}"

            goal = SkillActionGoal(goal_id, skill_name, args)
            self._active_goals[goal_id] = goal

        self.logger.info(f"[ACTION_SERVER] Nuova richiesta goal: ID={goal_id}, Skill={skill_name}, Args={args}")

        skill = self.registry.get(skill_name)
        if not skill:
            self.logger.error(f"[ACTION_SERVER] Skill '{skill_name}' non registrata.")
            goal.status = GoalStatus.FAILED
            return {"goal_id": goal_id, "status": GoalStatus.FAILED, "speak": [], "error": "Skill not found"}

        # Prepare execution text
        execution_text = args.get("text", "")
        if skill_name == "navigation" and "action" in args:
            action = args.get("action")
            if action == "move_to_room":
                execution_text = f"vai in {args.get('target', '')}"
            elif action == "explore":
                execution_text = "esplora la casa"
            elif action == "stop":
                execution_text = "fermati"
            elif action == "return_base":
                execution_text = "torna alla base"

        goal.status = GoalStatus.EXECUTING
        speak_texts = []

        try:
            # Wrap in cancellable task
            execution_coro = self._run_skill_with_feedback(goal, skill, execution_text, args)
            goal.task = asyncio.create_task(execution_coro)
            speak_texts = await goal.task
            goal.status = GoalStatus.SUCCEEDED
            goal.speak_results = speak_texts
            self.logger.info(f"[ACTION_SERVER] Goal {goal_id} completato con successo.")

        except asyncio.CancelledError:
            goal.status = GoalStatus.PREEMPTED
            self.logger.warning(f"[ACTION_SERVER] Goal {goal_id} PREEMPTED/CANCELLATO.")
            self._apply_emergency_brake()
            speak_texts = ["Azione interrotta."]

        except Exception as e:
            goal.status = GoalStatus.FAILED
            self.logger.error(f"[ACTION_SERVER] Fallimento esecuzione Goal {goal_id}: {e}", exc_info=True)
            speak_texts = [f"Errore durante l'esecuzione: {e}"]

        finally:
            goal.completion_time = time.time()
            async with self._lock:
                if goal_id in self._active_goals:
                    del self._active_goals[goal_id]

        return {
            "goal_id": goal_id,
            "status": goal.status,
            "speak": speak_texts,
            "duration": goal.completion_time - goal.start_time
        }

    async def _run_skill_with_feedback(
        self,
        goal: SkillActionGoal,
        skill: Any,
        execution_text: str,
        args: Dict[str, Any]
    ) -> List[str]:
        """Internal execution with streaming generator detection and periodic feedback."""
        speak_collected = []

        if inspect.isasyncgenfunction(skill.safe_execute):
            result = skill.safe_execute(execution_text, args)
        else:
            result = skill.safe_execute(execution_text, args)
            if inspect.iscoroutine(result):
                result = await result

        if inspect.isasyncgen(result):
            async for chunk in result:
                if goal.cancel_requested:
                    raise asyncio.CancelledError("Preemption requested during stream")

                # Process chunk and emit feedback
                fb = {"timestamp": time.time(), "chunk": str(chunk)}
                goal.feedback_history.append(fb)
                if self.on_feedback_callback:
                    try:
                        self.on_feedback_callback(goal.goal_id, fb)
                    except Exception:
                        pass

                if hasattr(chunk, 'speak') and chunk.speak:
                    speak_collected.append(chunk.speak)
                elif isinstance(chunk, dict) and 'speak' in chunk:
                    speak_collected.append(chunk['speak'])
                elif isinstance(chunk, str):
                    speak_collected.append(chunk)
        else:
            if hasattr(result, 'speak') and result.speak:
                speak_collected.append(result.speak)
            elif isinstance(result, dict) and 'speak' in result:
                speak_collected.append(result['speak'])
            elif isinstance(result, str):
                speak_collected.append(result)

        return speak_collected

    async def cancel_goal(self, goal_id: str) -> bool:
        """Cancels a specific running goal immediately."""
        async with self._lock:
            goal = self._active_goals.get(goal_id)
            if goal and goal.task and not goal.task.done():
                goal.cancel_requested = True
                goal.task.cancel()
                self.logger.info(f"[ACTION_SERVER] Preemption richiesta per Goal {goal_id}.")
                return True
        return False

    async def cancel_all_goals(self) -> int:
        """Preempts and cancels all actively running skill goals immediately."""
        cancelled = 0
        async with self._lock:
            for goal_id, goal in list(self._active_goals.items()):
                if goal.task and not goal.task.done():
                    goal.cancel_requested = True
                    goal.task.cancel()
                    cancelled += 1
            self._apply_emergency_brake()
        if cancelled > 0:
            self.logger.warning(f"[ACTION_SERVER] Preempted {cancelled} active goals on interrupt.")
        return cancelled

    def _apply_emergency_brake(self):
        """Halts motion immediately on preemption."""
        if self.nav_client and hasattr(self.nav_client, 'stop'):
            try:
                self.nav_client.stop()
            except Exception:
                pass
