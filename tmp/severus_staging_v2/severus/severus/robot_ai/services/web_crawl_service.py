"""
Robot AI Services - Web Crawl Service
======================================
Service for interacting with local OpenCrawl/Firecrawl instance (localhost:3000).
Provides markdown-optimized web content for LLM consumption.
"""

import aiohttp
import json
import logging
from typing import Optional, Dict, Any, List

from ..core.config_manager import ConfigManager

logger = logging.getLogger("web_crawl")

class WebCrawlService:
    """
    Async service for web crawling and scraping.
    Connects to a local instance at http://localhost:3000/v1.
    """

    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager.get_config()
        # Default to localhost if not specified in config
        self.base_url = "http://localhost:3000/v1"
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = 30.0  # Seconds

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy-create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout),
                headers={"Content-Type": "application/json"}
            )
        return self._session

    async def scrape(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Scrape a single URL and return markdown content.
        
        Args:
            url: The URL to scrape.
            
        Returns:
            Dict containing 'markdown', 'metadata', etc., or None on failure.
        """
        endpoint = f"{self.base_url}/scrape"
        payload = {"url": url, "formats": ["markdown"]}
        
        logger.info(f"Scraping URL: {url}")
        try:
            session = await self._get_session()
            async with session.post(endpoint, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"OpenCrawl Scrape error {resp.status}: {error_text[:200]}")
                    return None
                    
                data = await resp.json()
                if data.get("success"):
                    return data.get("data")
                return None
        except Exception as e:
            logger.error(f"OpenCrawl Scrape connection error: {e}")
            return None

    async def crawl(self, url: str, limit: int = 5) -> Optional[str]:
        """
        Crawl a website starting from url and return a summary of findings.
        This is a simplified version that might use a 'search' style endpoint if available,
        or just scrape the main page for now if the crawl endpoint is complex.
        
        Note: Jan's OpenCrawl / Firecrawl often has a /crawl endpoint that returns a job_id.
        For Marcus, we might prefer /search if supported, or a fast scrape.
        """
        # If the tool supports /search (like Firecrawl), use it for better results
        # Otherwise, fallback to scraping the main page.
        
        # Testing if /search exists (common in Firecrawl-compatible APIs)
        search_endpoint = f"{self.base_url}/search"
        payload = {"query": url, "limit": limit, "lang": "it"}
        
        logger.info(f"Searching/Crawling for: {url}")
        try:
            session = await self._get_session()
            async with session.post(search_endpoint, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success"):
                        # Format results into a single string for the LLM
                        results = data.get("data", [])
                        formatted = []
                        for res in results[:limit]:
                            formatted.append(f"Source: {res.get('url')}\nContent: {res.get('markdown', res.get('content', ''))[:1000]}...")
                        return "\n\n---\n\n".join(formatted)
                
                # Fallback to scrape if search fails or is not supported
                logger.warning(f"Search endpoint not available or failed, falling back to scrape of {url}")
                scrape_data = await self.scrape(url)
                if scrape_data:
                    return scrape_data.get("markdown", "Contenuto non disponibile in formato markdown.")
                    
        except Exception as e:
            logger.error(f"OpenCrawl Search/Crawl error: {e}")
            
        return None

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("OpenCrawl session closed")
