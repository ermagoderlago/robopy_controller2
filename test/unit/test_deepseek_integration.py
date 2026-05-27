"""
Unit Tests - DeepSeek Integration
==================================
Tests for DeepSeekService, DeepSeekConfig, and NightlyDream collaboration.
All external API calls are mocked.
"""

import unittest
import asyncio
import os
import tempfile
import json
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


class TestDeepSeekConfig(unittest.TestCase):
    """Test DeepSeekConfig Pydantic model and ConfigManager integration."""

    def test_deepseek_config_defaults(self):
        """Verify DeepSeekConfig has correct defaults."""
        from robopy_controller.robot_ai.core.config_manager import DeepSeekConfig
        
        cfg = DeepSeekConfig()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.model, "deepseek-chat")
        self.assertEqual(cfg.temperature, 0.7)
        self.assertEqual(cfg.max_tokens, 8192)
        self.assertEqual(cfg.timeout, 60)

    def test_deepseek_config_custom(self):
        """Verify DeepSeekConfig accepts custom values."""
        from robopy_controller.robot_ai.core.config_manager import DeepSeekConfig
        
        cfg = DeepSeekConfig(
            enabled=False,
            model="deepseek-reasoner",
            temperature=0.3,
            max_tokens=4096,
            timeout=120,
        )
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.model, "deepseek-reasoner")
        self.assertEqual(cfg.max_tokens, 4096)

    def test_ai_config_has_deepseek(self):
        """Verify AIConfig includes deepseek sub-config."""
        from robopy_controller.robot_ai.core.config_manager import AIConfig
        
        ai_cfg = AIConfig()
        self.assertTrue(hasattr(ai_cfg, "deepseek"))
        self.assertTrue(ai_cfg.deepseek.enabled)

    def test_secrets_has_deepseek_api_key(self):
        """Verify SecretsConfig includes deepseek_api_key."""
        from robopy_controller.robot_ai.core.config_manager import SecretsConfig
        
        secrets = SecretsConfig()
        self.assertTrue(hasattr(secrets, "deepseek_api_key"))
        self.assertEqual(secrets.deepseek_api_key, "")

    def test_config_manager_loads_deepseek_key_from_env(self):
        """Verify ConfigManager reads DEEPSEEK_API_KEY from env."""
        from robopy_controller.robot_ai.core.config_manager import ConfigManager
        
        # Reset singleton for test
        ConfigManager._instance = None
        
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key-123"}):
            cm = ConfigManager()
            config = cm.load()
            self.assertEqual(config.secrets.deepseek_api_key, "test-key-123")
        
        # Reset singleton after test
        ConfigManager._instance = None

    def test_config_validate_warns_missing_deepseek_key(self):
        """Verify validate() warns about missing DeepSeek key."""
        from robopy_controller.robot_ai.core.config_manager import ConfigManager
        
        ConfigManager._instance = None
        
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
            cm = ConfigManager()
            cm.load()
            warnings = cm.validate()
            deepseek_warnings = [w for w in warnings if "DeepSeek" in w]
            self.assertGreater(len(deepseek_warnings), 0)
        
        ConfigManager._instance = None


class TestDeepSeekService(unittest.IsolatedAsyncioTestCase):
    """Test DeepSeekService with mocked HTTP calls."""

    def _make_service(self, api_key="test-key", enabled=True):
        """Create a DeepSeekService with mocked config."""
        from robopy_controller.robot_ai.core.config_manager import ConfigManager
        
        ConfigManager._instance = None
        
        mock_config = MagicMock()
        mock_config.get_config.return_value = MagicMock(
            deepseek=MagicMock(
                enabled=enabled,
                model="deepseek-chat",
                temperature=0.7,
                max_tokens=8192,
                timeout=60,
            ),
            secrets=MagicMock(deepseek_api_key=api_key),
        )
        
        from robopy_controller.robot_ai.services.deepseek_service import DeepSeekService
        return DeepSeekService(mock_config)

    async def test_generate_success(self):
        """Test successful generation returns text."""
        service = self._make_service()
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "Test response from DeepSeek"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        })
        
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=False),
        ))
        mock_session.closed = False
        service._session = mock_session
        
        result = await service.generate("Test prompt")
        self.assertEqual(result, "Test response from DeepSeek")

    async def test_generate_disabled(self):
        """Test generation returns empty when disabled."""
        service = self._make_service(api_key="", enabled=False)
        result = await service.generate("Test prompt")
        self.assertEqual(result, "")

    async def test_generate_no_api_key(self):
        """Test generation returns empty when no API key."""
        service = self._make_service(api_key="")
        result = await service.generate("Test prompt")
        self.assertEqual(result, "")

    async def test_is_available(self):
        """Test is_available property."""
        service_enabled = self._make_service(api_key="key123", enabled=True)
        self.assertTrue(service_enabled.is_available)
        
        service_disabled = self._make_service(api_key="", enabled=True)
        self.assertFalse(service_disabled.is_available)

    async def test_close_session(self):
        """Test session cleanup."""
        service = self._make_service()
        mock_session = AsyncMock()
        mock_session.closed = False
        service._session = mock_session
        
        await service.close()
        mock_session.close.assert_called_once()


class TestNightlyDreamCollaboration(unittest.IsolatedAsyncioTestCase):
    """Test NightlyDreamService collaborative analysis."""

    def _make_service(self, deepseek_available=True):
        """Create NightlyDreamService with mocked dependencies."""
        mock_config_manager = MagicMock()
        mock_config_manager.get_config.return_value = MagicMock(
            robot=MagicMock(
                name="MARCUS",
                full_name="Test Robot",
                creator="Test Creator",
                model="RPi5",
                version="1.0",
            )
        )
        
        # Mock MemoryStore
        mock_memory_store = MagicMock()
        mock_memory = MagicMock()
        mock_memory.created_at = 9999999999.0  # Far future to pass cutoff
        mock_memory.content = "User: ciao\nRobot: Ciao! Come posso aiutarti?"
        mock_memory_store.get_recent.return_value = [mock_memory]
        mock_memory_store.add = MagicMock()
        
        # Mock LLMService
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=MagicMock(text="Gemini analysis result"))
        
        # Mock EmbeddingService
        mock_embedding = AsyncMock()
        mock_embedding.embed = AsyncMock(return_value=[0.1] * 3072)
        
        # Mock DeepSeek
        mock_deepseek = None
        if deepseek_available:
            mock_deepseek = AsyncMock()
            mock_deepseek.is_available = True
            mock_deepseek.generate = AsyncMock(return_value="DeepSeek review result")
        
        from robopy_controller.robot_ai.services.nightly_dream_service import NightlyDreamService
        service = NightlyDreamService(
            mock_config_manager, mock_memory_store, mock_llm, mock_embedding,
            deepseek_service=mock_deepseek
        )
        
        # Use temp file paths for testing
        self._temp_dir = tempfile.mkdtemp()
        service.log_path = os.path.join(self._temp_dir, "improvements.md")
        service.master_prompt_path = os.path.join(self._temp_dir, "master_prompt.txt")
        
        return service, mock_llm, mock_deepseek

    async def test_collaborative_analysis_produces_result(self):
        """Test that collaborative analysis returns success with all 4 turns."""
        service, mock_llm, mock_deepseek = self._make_service(deepseek_available=True)
        
        result = await service.run_analysis()
        
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["collaborative"])
        self.assertEqual(result["memories_analyzed"], 1)
        
        # Verify Gemini called twice (turns 1 and 3)
        self.assertEqual(mock_llm.generate.call_count, 2)
        
        # Verify DeepSeek called twice (turns 2 and 4)
        self.assertEqual(mock_deepseek.generate.call_count, 2)

    async def test_master_prompt_file_written(self):
        """Test that master prompt file is saved after collaborative analysis."""
        service, _, mock_deepseek = self._make_service(deepseek_available=True)
        mock_deepseek.generate = AsyncMock(return_value="- Istruzione test 1\n- Istruzione test 2")
        
        await service.run_analysis()
        
        self.assertTrue(os.path.exists(service.master_prompt_path))
        with open(service.master_prompt_path, "r") as f:
            content = f.read()
        self.assertIn("Istruzione test 1", content)

    async def test_fallback_when_no_deepseek(self):
        """Test single-pass mode when DeepSeek is not available."""
        service, mock_llm, _ = self._make_service(deepseek_available=False)
        
        result = await service.run_analysis()
        
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["collaborative"])
        
        # Gemini called only once (single-pass)
        self.assertEqual(mock_llm.generate.call_count, 1)

    async def test_fallback_when_deepseek_fails(self):
        """Test graceful fallback when DeepSeek returns empty."""
        service, mock_llm, mock_deepseek = self._make_service(deepseek_available=True)
        mock_deepseek.generate = AsyncMock(return_value="")
        
        result = await service.run_analysis()
        
        # Should still succeed with Gemini-only result
        self.assertEqual(result["status"], "success")

    async def test_no_memories_skips_analysis(self):
        """Test that analysis is skipped when no memories in last 24h."""
        service, _, _ = self._make_service(deepseek_available=True)
        # Override with old memory
        old_memory = MagicMock()
        old_memory.created_at = 0.0  # Very old
        old_memory.content = "ancient memory"
        service.memory_store.get_recent.return_value = [old_memory]
        
        result = await service.run_analysis()
        self.assertEqual(result["status"], "skipped")

    async def test_log_file_written(self):
        """Test that improvement log is written."""
        service, _, _ = self._make_service(deepseek_available=True)
        
        await service.run_analysis()
        
        self.assertTrue(os.path.exists(service.log_path))
        with open(service.log_path, "r") as f:
            content = f.read()
        self.assertIn("Analysis Run:", content)


if __name__ == "__main__":
    unittest.main()
