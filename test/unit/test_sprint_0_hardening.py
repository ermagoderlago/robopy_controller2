import unittest
import time
import asyncio
from typing import Dict, Any

from robopy_controller.robot_ai.skills.base_skill import SkillResult, SkillErrorCode, BaseSkill, SkillMetadata
from robopy_controller.robot_ai.core.action_controller import ActionRequest, ActionController
from robopy_controller.robot_ai.core.image_handler import Image, ImageValidator
from robopy_controller.robot_ai.core.tool_declarations import ToolRegistry


class MockSkill(BaseSkill):
    """Skill di test."""
    def __init__(self, name="mock_skill"):
        super().__init__()
        self._name = name

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name=self._name,
            description="Mock skill for testing",
            priority=10
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        return 1.0 if text == self._name else 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        return SkillResult.success_result(f"Executed {self._name}")


class TestSkillResult(unittest.TestCase):
    """Test per il contratto SkillResult."""
    
    def test_skill_result_success_creation(self):
        """Test creazione risultato di successo."""
        result = SkillResult.success_result(
            "Luce accesa",
            speak="Ho acceso la luce"
        )
        self.assertTrue(result.success)
        self.assertEqual(result.message, "Luce accesa")
        self.assertIsNone(result.error_code)
    
    def test_skill_result_failure_creation(self):
        """Test creazione risultato di fallimento."""
        result = SkillResult.failure_result(
            "Dispositivo non trovato",
            SkillErrorCode.SKILL_NOT_FOUND
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, SkillErrorCode.SKILL_NOT_FOUND)
    
    def test_skill_result_mutable(self):
        """Test mutabilità (per compatibilità)."""
        result = SkillResult.success_result("test")
        result.success = False
        self.assertFalse(result.success)
    
    def test_skill_result_to_dict(self):
        """Test serializzazione a dict."""
        result = SkillResult.success_result("Messaggio test", speak="test")
        d = result.to_dict()
        self.assertTrue(d["success"])
        self.assertEqual(d["message"], "Messaggio test")
        self.assertEqual(d["speak"], "test")


class TestActionController(unittest.IsolatedAsyncioTestCase):
    """Test per l'esecuzione unificata delle azioni (Async)."""
    
    def setUp(self):
        self.controller = ActionController()
        # Registra skill di test
        self.mock_skill = MockSkill("test_skill")
        self.controller.registry.register(self.mock_skill)
        
        # Registra altre skill fittizie per il test di lista
        self.controller.registry.register(MockSkill("skill_2"))
        self.controller.registry.register(MockSkill("skill_3"))
    
    async def test_execute_action_success(self):
        """Test esecuzione skill esistente."""
        request = ActionRequest(
            skill_name="test_skill",
            parameters={},
        )
        result = await self.controller.execute_action(request)
        self.assertTrue(result.success)
        self.assertEqual(result.message, "Executed test_skill")

    async def test_execute_action_unknown_skill(self):
        """Test esecuzione skill sconosciuta."""
        request = ActionRequest(
            skill_name="skill_inesistente",
            parameters={},
        )
        result = await self.controller.execute_action(request)
        
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, SkillErrorCode.SKILL_NOT_FOUND)
    
    async def test_execute_action_invalid_parameters(self):
        """Test skill con parametri invalidi."""
        request = ActionRequest(
            skill_name="", # Nome vuoto
            parameters={},
        )
        result = await self.controller.execute_action(request)
        
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, SkillErrorCode.INVALID_PARAMETERS)
    
    def test_get_available_skills(self): # Non-async helper
        """Test lista skill disponibili."""
        skills = self.controller.get_available_skills()
        self.assertIn("test_skill", skills)
        self.assertIn("skill_2", skills)
        self.assertIn("skill_3", skills)
        self.assertGreaterEqual(len(skills), 3)
    
    async def test_execution_history_tracking(self):
        """Test tracking storico esecuzioni."""
        # Skill sconosciuta
        await self.controller.execute_action(ActionRequest(
            skill_name="sconosciuta",
            parameters={},
        ))
        
        history = self.controller.get_execution_history()
        self.assertGreater(len(history), 0)
        self.assertEqual(history[-1]["skill"], "sconosciuta")
        self.assertFalse(history[-1]["success"])
        
        # Skill valida
        await self.controller.execute_action(ActionRequest(
            skill_name="test_skill",
            parameters={},
        ))
        history = self.controller.get_execution_history()
        self.assertGreater(len(history), 1)
        self.assertEqual(history[-1]["skill"], "test_skill")
        self.assertTrue(history[-1]["success"])


class TestImageHandler(unittest.TestCase):
    """Test per standardizzazione formato immagini."""
    
    def test_image_creation_raw_bytes(self):
        raw = b"\x89PNG\r\n\x1a\n..."
        img = Image(data=raw, format="png", width=640, height=480)
        
        self.assertIsInstance(img.data, bytes)
        self.assertEqual(img.format, "png")
    
    def test_image_to_base64(self):
        raw = b"dati immagine test"
        img = Image(data=raw, format="jpeg", width=100, height=100)
        
        b64 = img.to_base64()
        self.assertIsInstance(b64, str)
        self.assertGreater(len(b64), 0)
    
    def test_image_from_base64(self):
        original = b"dati immagine test"
        import base64
        b64_str = base64.b64encode(original).decode('utf-8')
        
        restored = Image.from_base64(b64_str, "jpeg", 100, 100)
        self.assertEqual(restored.data, original)
    
    def test_image_validator_size(self):
        large_data = b"x" * (11 * 1024 * 1024)
        img = Image(data=large_data, format="jpeg", width=640, height=480)
        error = ImageValidator.validate(img)
        self.assertIsNotNone(error)
    
    def test_image_validator_format(self):
        img = Image(data=b"test", format="bmp", width=640, height=480)
        error = ImageValidator.validate(img)
        self.assertIsNotNone(error)


class TestToolDeclarations(unittest.TestCase):
    """Test per schema tool Gemini."""
    
    def test_tool_registry_default_tools(self):
        registry = ToolRegistry()
        tools = registry.get_tools_for_gemini()
        
        names = [t["name"] for t in tools]
        self.assertIn("execute_skill", names)
        self.assertIn("query_memory", names)
        self.assertIn("get_ha_state", names)
    
    def test_tool_schema_format(self):
        registry = ToolRegistry()
        tools = registry.get_tools_for_gemini()
        
        for tool in tools:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("parameters", tool)
            self.assertEqual(tool["parameters"]["type"], "object")
    
    def test_tool_call_validation(self):
        registry = ToolRegistry()
        
        error = registry.validate_tool_call(
            "execute_skill",
            {"skill_name": "turn_on_light", "parameters": {}}
        )
        self.assertIsNone(error)
        
        error = registry.validate_tool_call(
            "execute_skill",
            {"parameters": {}}
        )
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
