"""
Robot AI Services - DeepSeek Service
=====================================
Async DeepSeek API client for collaborative nightly analysis.
Uses aiohttp for HTTP calls to DeepSeek Chat API.
"""

import aiohttp
import json
from typing import Optional

from ..core.config_manager import ConfigManager
from ..utils.logging_utils import get_logger


class DeepSeekService:
    """
    Async DeepSeek API service.
    
    Used as a second AI model for nightly collaborative analysis
    with Gemini. Provides diverse perspectives for self-improvement.
    
    Usage:
        service = DeepSeekService(config_manager)
        response = await service.generate("Analizza questo report...")
        await service.close()
    """

    BASE_URL = "https://api.deepseek.com/chat/completions"

    def __init__(self, config_manager: ConfigManager):
        self.logger = get_logger("deepseek")
        config = config_manager.get_config()
        
        self._api_key = config.secrets.deepseek_api_key
        self._model = config.deepseek.model
        self._temperature = config.deepseek.temperature
        self._max_tokens = config.deepseek.max_tokens
        self._timeout = config.deepseek.timeout
        self._enabled = config.deepseek.enabled and bool(self._api_key)
        
        self._session: Optional[aiohttp.ClientSession] = None
        
        if self._enabled:
            self.logger.info(f"DeepSeek service initialized (model={self._model})")
        else:
            self.logger.warning("DeepSeek service disabled (no API key or disabled in config)")

    @property
    def is_available(self) -> bool:
        """Check if the service is enabled and has an API key."""
        return self._enabled

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy-create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            )
        return self._session

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = None,
        temperature: float = None,
    ) -> str:
        """
        Generate a response from DeepSeek Chat API.
        
        Args:
            prompt: User prompt text.
            system_prompt: Optional system prompt.
            max_tokens: Override default max_tokens.
            temperature: Override default temperature.
            
        Returns:
            Generated text, or empty string on failure.
        """
        if not self._enabled:
            self.logger.warning("DeepSeek generate called but service is disabled")
            return ""

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature if temperature is not None else self._temperature,
            "stream": False,
        }

        try:
            session = await self._get_session()
            async with session.post(self.BASE_URL, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    self.logger.error(f"DeepSeek API error {resp.status}: {error_text[:200]}")
                    return ""

                data = await resp.json()
                choices = data.get("choices", [])
                if not choices:
                    self.logger.warning("DeepSeek returned empty choices")
                    return ""

                content = choices[0].get("message", {}).get("content", "")
                
                # Log token usage
                usage = data.get("usage", {})
                if usage:
                    self.logger.info(
                        f"DeepSeek tokens: prompt={usage.get('prompt_tokens', 0)}, "
                        f"completion={usage.get('completion_tokens', 0)}"
                    )
                
                return content

        except aiohttp.ClientError as e:
            self.logger.error(f"DeepSeek connection error: {e}")
            return ""
        except TimeoutError:
            self.logger.error(f"DeepSeek timeout after {self._timeout}s")
            return ""
        except Exception as e:
            self.logger.error(f"DeepSeek unexpected error: {e}")
            return ""

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self.logger.info("DeepSeek session closed")
