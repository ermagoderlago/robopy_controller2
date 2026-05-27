import inspect
from typing import List, Dict, Any
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
        skill = self.registry.get(skill_name)
        if not skill:
            self._logger.warning(f"Skill '{skill_name}' non trovata nel registry.")
            return []

        execution_text = args.get("text", "")
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

        try:
            result = await skill.safe_execute(execution_text)
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

    async def _collect_speak_texts(self, result_or_gen) -> List[str]:
        texts = []
        try:
            if inspect.isasyncgen(result_or_gen):
                async for res in result_or_gen:
                    if hasattr(res, 'speak') and res.speak:
                        texts.append(res.speak)
                    elif isinstance(res, dict) and 'speak' in res:
                        texts.append(res['speak'])
            else:
                if hasattr(result_or_gen, 'speak') and result_or_gen.speak:
                    texts.append(result_or_gen.speak)
                elif isinstance(result_or_gen, dict) and 'speak' in result_or_gen:
                    texts.append(result_or_gen['speak'])
        except Exception as e:
            self._logger.error(f"Error collecting skill output: {e}")
        return texts
