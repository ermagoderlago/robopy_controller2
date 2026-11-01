from dataclasses import dataclass, field
import inspect
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple

try:
    from robot_ai.skills.skill_registry import SkillRegistry
    from robot_ai.integrations import NavigationClient
    from robot_ai.utils import get_logger
    from robot_ai.orchestration.reactive_safety import ReactiveSafety
except ImportError:
    from robopy_controller.robot_ai.skills.skill_registry import SkillRegistry
    from robopy_controller.robot_ai.integrations import NavigationClient
    from robopy_controller.robot_ai.utils import get_logger
    from robopy_controller.robot_ai.orchestration.reactive_safety import ReactiveSafety


@dataclass
class SkillExecutionResult:
    """Standardized execution result for high-level skill actions."""
    success: bool
    message: str = ""
    speak: Optional[str] = None
    actions: List[Dict[str, Any]] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None


class SkillExecutor:
    # High-level action mappings to registered skills and default arguments
    # (Requirement R4 & Milestone 4: Kinematic Sandbox & SkillExecutor Routing)
    skill_mapping: Dict[str, Tuple[str, Dict[str, Any]]] = {
        "start_frontier_exploration": ("frontier_exploration", {"action": "start_explore"}),
        "stop_navigation": ("frontier_exploration", {"action": "stop"}),
        "get_navigation_status": ("frontier_exploration", {"action": "get_status"}),
        "search_target": ("frontier_exploration", {"action": "search_target"}),
        "move_distance": ("navigation", {"action": "move_relative"}),
        "rotate_angle": ("navigation", {"action": "move_relative"}),
        "navigate_to": ("navigation", {"action": "goto"}),
        "stop_motion": ("navigation", {"action": "stop"}),
    }

    def __init__(self, registry: SkillRegistry, nav_client: Optional[NavigationClient] = None, reactive_safety: Optional[ReactiveSafety] = None):
        self.registry = registry
        self.nav_client = nav_client
        self.reactive_safety = reactive_safety
        self._logger = get_logger("skill_executor")

    def find_best_match(self, text: str, min_confidence: float):
        return self.registry.find_best_match(text, min_confidence=min_confidence)

    def get_all(self):
        return self.registry.get_all()

    async def execute_action(self, action_name: str, arguments: Optional[Dict[str, Any]] = None) -> SkillExecutionResult:
        """
        Execute a high-level Kinematic Sandbox action or function call.
        Dispatches to registered skills (e.g. NavigationSkill) via skill_mapping.
        """
        args = dict(arguments or {})
        target_skill = action_name

        if action_name in self.skill_mapping:
            target_skill, default_args = self.skill_mapping[action_name]
            args = {**default_args, **args}

        skill = self.registry.get(target_skill)
        if not skill and target_skill == "frontier_exploration":
            skill = self.registry.get("navigation")

        if not skill:
            msg = f"Skill '{action_name}' non trovata nel registry."
            self._logger.warning(msg)
            return SkillExecutionResult(success=False, message=msg, error_code="SKILL_NOT_FOUND")

        # Prepare execution text for skills that rely on natural language intent
        execution_text = args.get("text", "") or args.get("query", "")
        if "action" in args:
            action = args.get("action")
            if action in ("goto", "navigate_to"):
                dest = args.get("destination") or args.get("target", "")
                execution_text = f"vai in {dest}"
            elif action in ("move_distance", "move_relative"):
                direction = args.get("direction", "avanti")
                if "distance_m" in args:
                    dist_cm = int(round(float(args["distance_m"]) * 100))
                    execution_text = f"muoviti {direction} di {dist_cm} centimetri"
                elif "degrees" in args:
                    deg = int(round(float(args["degrees"])))
                    execution_text = f"ruota a {direction} di {deg} gradi"
                else:
                    execution_text = f"muoviti {direction}"
            elif action == "rotate_angle":
                direction = args.get("direction", "destra")
                deg = int(round(float(args.get("degrees", 90.0))))
                execution_text = f"ruota a {direction} di {deg} gradi"
            elif action == "stop":
                execution_text = "fermati"
            elif action in ("explore", "start_explore"):
                execution_text = "esplora la casa"

        try:
            raw_result = await skill.safe_execute(execution_text, args)
            speak_texts = await self._collect_speak_texts(raw_result)
            speak_str = " ".join(speak_texts) if speak_texts else None

            success = True
            msg = "Action executed successfully."
            actions_list = []
            data_dict = {}

            if hasattr(raw_result, "success"):
                success = raw_result.success
            if hasattr(raw_result, "message"):
                msg = raw_result.message
            if hasattr(raw_result, "actions"):
                actions_list = raw_result.actions
            if hasattr(raw_result, "data"):
                data_dict = raw_result.data
            if hasattr(raw_result, "speak") and raw_result.speak:
                speak_str = raw_result.speak

            return SkillExecutionResult(
                success=success,
                message=msg,
                speak=speak_str,
                actions=actions_list,
                data=data_dict,
            )
        except Exception as e:
            self._logger.error(f"Error in execute_action '{action_name}': {e}", exc_info=True)
            return SkillExecutionResult(
                success=False,
                message=f"Errore esecuzione {action_name}: {e}",
                error_code="EXECUTION_ERROR"
            )

    async def execute_skill(self, skill_name: str, args: dict) -> List[str]:
        # Map direct Gemini / Kinematic Sandbox function call names to registered skills
        if skill_name in self.skill_mapping:
            target_skill, default_args = self.skill_mapping[skill_name]
            args = {**default_args, **args}
            skill_name = target_skill

        skill = self.registry.get(skill_name)
        if not skill and skill_name == "frontier_exploration":
            skill = self.registry.get("navigation")

        if not skill:
            self._logger.warning(f"Skill '{skill_name}' non trovata nel registry.")
            return []

        execution_text = args.get("text", "") or args.get("query", "")
        if skill_name == "navigation" and "action" in args:
             # Backward compatibility mappings for older prompts and semantic commands
             action = args.get("action")
             if action == "move_to_room":
                 execution_text = f"vai in {args.get('target', '')}"
             elif action in ("goto", "navigate_to"):
                 dest = args.get("destination") or args.get("target", "")
                 execution_text = f"vai in {dest}"
             elif action in ("move_distance", "move_relative"):
                 direction = args.get("direction", "avanti")
                 if "distance_m" in args:
                     dist_m = float(args["distance_m"])
                     dist_cm = int(round(dist_m * 100))
                     execution_text = f"muoviti {direction} di {dist_cm} centimetri"
                 elif "degrees" in args:
                     deg = float(args["degrees"])
                     execution_text = f"ruota a {direction} di {int(round(deg))} gradi"
                 else:
                     execution_text = f"muoviti {direction}"
             elif action == "rotate_angle":
                 direction = args.get("direction", "destra")
                 deg = float(args.get("degrees", 90.0))
                 execution_text = f"ruota a {direction} di {int(round(deg))} gradi"
             elif action == "explore":
                 execution_text = "esplora la casa"
             elif action == "stop":
                 execution_text = "fermati"
             elif action == "return_base":
                 execution_text = "torna alla base"
        elif skill_name == "frontier_exploration" and "action" in args:
             action = args.get("action")
             if action in ("start_explore", "explore"):
                 execution_text = "esplora la casa"
             elif action == "search_target":
                 execution_text = f"cerca {args.get('target', '')}"
             elif action == "stop":
                 execution_text = "fermati"
             elif action == "get_status":
                 execution_text = "stato navigazione"

        try:
            # Pass args as the context dictionary to safe_execute
            result = await skill.safe_execute(execution_text, args)
            return await self._collect_speak_texts(result)
        except Exception as e:
            self._logger.error(f"Error executing skill {skill_name}: {e}", exc_info=True)
            return []

    async def execute_actions(self, actions: List[Dict[str, Any]]) -> List[str]:
        all_speak = []
        for action in actions:
            action_type = action.get("action_type") or action.get("function_name") or action.get("name", "")
            args = action.get("args") or action.get("arguments", {})
            texts = await self.execute_skill(action_type, args)
            all_speak.extend(texts)
        return all_speak

    async def execute_actions_stream(self, actions: List[Dict[str, Any]]) -> AsyncGenerator[str, None]:
        for action in actions:
            skill_name = action.get("action_type") or action.get("function_name") or action.get("name", "")
            args = action.get("args") or action.get("arguments", {})

            # Map direct function call names via skill_mapping
            if skill_name in self.skill_mapping:
                target_skill, default_args = self.skill_mapping[skill_name]
                args = {**default_args, **args}
                skill_name = target_skill

            skill = self.registry.get(skill_name)
            if not skill and skill_name == "frontier_exploration":
                skill = self.registry.get("navigation")

            if not skill:
                self._logger.warning(f"Skill '{skill_name}' non trovata nel registry.")
                continue

            execution_text = args.get("text", "") or args.get("query", "")
            if skill_name == "navigation" and "action" in args:
                 action_val = args.get("action")
                 if action_val == "move_to_room":
                     execution_text = f"vai in {args.get('target', '')}"
                 elif action_val in ("goto", "navigate_to"):
                     dest = args.get("destination") or args.get("target", "")
                     execution_text = f"vai in {dest}"
                 elif action_val in ("move_distance", "move_relative"):
                     direction = args.get("direction", "avanti")
                     if "distance_m" in args:
                         dist_m = float(args["distance_m"])
                         dist_cm = int(round(dist_m * 100))
                         execution_text = f"muoviti {direction} di {dist_cm} centimetri"
                     elif "degrees" in args:
                         deg = float(args["degrees"])
                         execution_text = f"ruota a {direction} di {int(round(deg))} gradi"
                     else:
                         execution_text = f"muoviti {direction}"
                 elif action_val == "rotate_angle":
                     direction = args.get("direction", "destra")
                     deg = float(args.get("degrees", 90.0))
                     execution_text = f"ruota a {direction} di {int(round(deg))} gradi"
                 elif action_val == "explore":
                     execution_text = "esplora la casa"
                 elif action_val == "stop":
                     execution_text = "fermati"
                 elif action_val == "return_base":
                     execution_text = "torna alla base"
            elif skill_name == "frontier_exploration" and "action" in args:
                 action_val = args.get("action")
                 if action_val in ("start_explore", "explore"):
                     execution_text = "esplora la casa"
                 elif action_val == "search_target":
                     execution_text = f"cerca {args.get('target', '')}"
                 elif action_val == "stop":
                     execution_text = "fermati"
                 elif action_val == "get_status":
                     execution_text = "stato navigazione"

            try:
                # Pass args as context
                result = await skill.safe_execute(execution_text, args)
                if inspect.isasyncgen(result):
                    async for res in result:
                        if hasattr(res, 'speak') and res.speak:
                            yield res.speak
                        elif isinstance(res, dict) and 'speak' in res and res['speak']:
                            yield res['speak']
                        elif hasattr(res, 'message') and res.message:
                            yield res.message
                        elif isinstance(res, dict) and 'message' in res and res['message']:
                            yield res['message']
                else:
                    if hasattr(result, 'speak') and result.speak:
                        yield result.speak
                    elif isinstance(result, dict) and 'speak' in result and result['speak']:
                        yield result['speak']
                    elif hasattr(result, 'message') and result.message:
                        yield result.message
                    elif isinstance(result, dict) and 'message' in result and result['message']:
                        yield result['message']
            except Exception as e:
                self._logger.error(f"Error executing skill {skill_name} in stream: {e}", exc_info=True)

    async def _collect_speak_texts(self, result_or_gen) -> List[str]:
        texts = []
        try:
            if inspect.isasyncgen(result_or_gen):
                async for res in result_or_gen:
                    if hasattr(res, 'speak') and res.speak:
                        texts.append(res.speak)
                    elif isinstance(res, dict) and 'speak' in res and res['speak']:
                        texts.append(res['speak'])
                    elif hasattr(res, 'message') and res.message:
                        texts.append(res.message)
                    elif isinstance(res, dict) and 'message' in res and res['message']:
                        texts.append(res['message'])
            else:
                if hasattr(result_or_gen, 'speak') and result_or_gen.speak:
                    texts.append(result_or_gen.speak)
                elif isinstance(result_or_gen, dict) and 'speak' in result_or_gen and result_or_gen['speak']:
                    texts.append(result_or_gen['speak'])
                elif hasattr(result_or_gen, 'message') and result_or_gen.message:
                    texts.append(result_or_gen.message)
                elif isinstance(result_or_gen, dict) and 'message' in result_or_gen and result_or_gen['message']:
                    texts.append(result_or_gen['message'])
        except Exception as e:
            self._logger.error(f"Error collecting skill output: {e}")
        return texts



