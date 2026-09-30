import inspect
from typing import List, Dict, Any, AsyncGenerator
from robot_ai.skills.skill_registry import SkillRegistry
from robot_ai.integrations import NavigationClient
from robot_ai.utils import get_logger
from robot_ai.orchestration.reactive_safety import ReactiveSafety

class SkillExecutor:
    def __init__(self, registry: SkillRegistry, nav_client: NavigationClient, reactive_safety: ReactiveSafety):
        self.registry = registry
        self.nav_client = nav_client
        self.reactive_safety = reactive_safety
        self._logger = get_logger("skill_executor")

    def find_best_match(self, text: str, min_confidence: float):
        return self.registry.find_best_match(text, min_confidence=min_confidence)

    def get_all(self):
        return self.registry.get_all()

    async def execute_skill(self, skill_name: str, args: dict) -> List[str]:
        # Map direct Gemini function call names to skills
        if skill_name == "start_frontier_exploration":
            skill_name = "frontier_exploration"
            args = {"action": "start_explore", **args}
        elif skill_name == "stop_navigation":
            skill_name = "frontier_exploration"
            args = {"action": "stop", **args}
        elif skill_name == "get_navigation_status":
            skill_name = "frontier_exploration"
            args = {"action": "get_status", **args}
        elif skill_name == "search_target":
            skill_name = "frontier_exploration"
            args = {"action": "search_target", **args}

        skill = self.registry.get(skill_name)
        if not skill:
            self._logger.warning(f"Skill '{skill_name}' non trovata nel registry.")
            return []

        execution_text = args.get("text", "") or args.get("query", "")
        if skill_name == "navigation" and "action" in args:
             # Backward compatibility mappings for older prompts
             action = args.get("action")
             if action == "move_to_room":
                 execution_text = f"vai in {args.get('target', '')}"
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
            texts = await self.execute_skill(action.get("action_type", ""), action.get("args", {}))
            all_speak.extend(texts)
        return all_speak

    async def execute_actions_stream(self, actions: List[Dict[str, Any]]) -> AsyncGenerator[str, None]:
        for action in actions:
            skill_name = action.get("action_type", "")
            args = action.get("args", {})

            # Map direct Gemini function call names
            if skill_name == "start_frontier_exploration":
                skill_name = "frontier_exploration"
                args = {"action": "start_explore", **args}
            elif skill_name == "stop_navigation":
                skill_name = "frontier_exploration"
                args = {"action": "stop", **args}
            elif skill_name == "get_navigation_status":
                skill_name = "frontier_exploration"
                args = {"action": "get_status", **args}
            elif skill_name == "search_target":
                skill_name = "frontier_exploration"
                args = {"action": "search_target", **args}

            skill = self.registry.get(skill_name)
            if not skill:
                self._logger.warning(f"Skill '{skill_name}' non trovata nel registry.")
                continue

            execution_text = args.get("text", "") or args.get("query", "")
            if skill_name == "navigation" and "action" in args:
                 action_val = args.get("action")
                 if action_val == "move_to_room":
                     execution_text = f"vai in {args.get('target', '')}"
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


