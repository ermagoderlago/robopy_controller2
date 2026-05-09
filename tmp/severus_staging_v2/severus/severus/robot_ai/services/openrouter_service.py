"""
Robot AI Services - OpenRouter Service
======================================
Service for interacting with OpenRouter API to access high-performance models
like Qwen 3.6-Plus.
"""

import aiohttp
import json
import logging
import os
from typing import Optional, Dict, Any, List

from ..core.config_manager import ConfigManager

logger = logging.getLogger("openrouter")

class OpenRouterService:
    """
    Async service for OpenRouter API.
    Used to process search results with Qwen 3.6-Plus.
    """

    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, config_manager: ConfigManager):
        self.logger = logger
        self.config = config_manager.get_config()
        
        # Load key from .env via environment variable or config
        self._api_key = os.environ.get("OPENROUTER_API_KEY")
        if not self._api_key:
            # Fallback to secrets in config if available
            try:
                self._api_key = self.config.secrets.openrouter_api_key
            except AttributeError:
                pass

        # Model identifier for Qwen 3.6-Plus
        self._model = "qwen/qwen-3.6-plus" # Or the exact OpenRouter string
        self._timeout = 45.0
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy-create aiohttp session."""
        if self._session is None or self._session.closed:
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/robopy/antigravity", # Optional for OpenRouter
                "X-Title": "Marcus Robot AI",
            }
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )
        return self._session

    async def analyze_search_results(self, query: str, markdown_content: str) -> str:
        """
        Analyze search results using Qwen 3.6-Plus.
        
        Args:
            query: The original user search query.
            markdown_content: The scraped content from OpenCrawl.
            
        Returns:
            A summarized answer based on the search results.
        """
        if not self._api_key:
            self.logger.error("OpenRouter API key missing!")
            return "Errore: Chiave API OpenRouter non configurata."

        system_prompt = (
            "Sei un esperto analista web integrato in un robot di nome Marcus. "
            "Il tuo compito è analizzare i risultati di ricerca forniti (in Markdown) "
            "e rispondere alla domanda dell'utente in modo conciso ma completo. "
            "Usa SEMPRE la lingua italiana. Basa la tua risposta esclusivamente sui fatti trovati. "
            "Se non trovi la risposta, dillo chiaramente."
        )

        user_prompt = (
            f"Domanda dell'utente: {query}\n\n"
            f"Risultati della ricerca web:\n"
            f"{markdown_content[:15000]}\n\n" # Limit content to avoid token overflow
            f"Fornisci una risposta accurata e colloquiale per Marcus."
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 1024
        }

        try:
            session = await self._get_session()
            async with session.post(self.BASE_URL, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    self.logger.error(f"OpenRouter error {resp.status}: {error_text}")
                    return f"Errore nella comunicazione con Qwen (OpenRouter: {resp.status})."
                
                data = await resp.json()
                choices = data.get("choices", [])
                if not choices:
                    return "Non sono riuscito a generare una risposta dai risultati della ricerca."
                
                return choices[0].get("message", {}).get("content", "").strip()

        except Exception as e:
            self.logger.error(f"OpenRouter connection error: {e}")
            return f"Errore di rete durante l'analisi con Qwen: {str(e)}"

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self.logger.info("OpenRouter session closed")
