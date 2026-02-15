"""
Robot AI Services - Nightly Dream Service
=========================================
Analyzes daily interactions to generate insights and improve performance.
"""

import time
import os
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from ..rag.memory_store import MemoryStore, Memory, MemoryType
from ..services.embedding_service import EmbeddingService
from ..services.llm_service import LLMService
from ..core.config_manager import ConfigManager
from ..utils.logging_utils import get_logger

class NightlyDreamService:
    """
    Service for nightly memory analysis and self-improvement.
    """

    def __init__(self, config_manager: ConfigManager, memory_store: MemoryStore, llm_service: LLMService, embedding_service: EmbeddingService):
        self.logger = get_logger("nightly_dream")
        self.config = config_manager
        self.memory_store = memory_store
        self.llm_service = llm_service
        self.embedding_service = embedding_service
        self.skills_summary = ""
        
        # Path for the improvement log: ~/robopy/logs/continuous_improvements.md
        home = os.path.expanduser("~")
        self.log_path = os.path.join(home, "robopy", "logs", "continuous_improvements.md")
        
        # Ensure log dir exists
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

    def set_skills_summary(self, summary: str):
        """Set the summary of available skills."""
        self.skills_summary = summary

    def _get_system_manifest(self) -> str:
        """Get a manifest of the robot's current configuration and capabilities."""
        cfg = self.config.get_config()
        robot = cfg.robot
        
        manifest = f"""
## Chi Sei (Manifesto)
Sei {robot.name} ({robot.full_name}), un robot autonomo basato su {robot.model}.
Creato da: {robot.creator}.
Versione: {robot.version}.

### Hardware & Sensori
- **Visione**: OAK-D Lite (RGB 4K, Depth, AI on-chip).
- **Audio**: Microfono con VAD, Speaker TTS (Google).
- **Movimento**: Base mobile differenziale (2 ruote).
- **Computer**: Raspberry Pi 5 (8GB RAM).

### Software & Integrazioni
- **Cervello**: LLM Gemini 2.5 Flash + RAG (ChromaDB).
- **Domotica**: Home Assistant (Luci, Tapparelle, Clima, Media).
- **Navigazione**: ROS 2 Nav2 + SLAM (RTAB-Map).
- **Skills**: {self.skills_summary}
"""
        return manifest

    async def run_analysis(self) -> Dict[str, Any]:
        """
        Run the nightly analysis.
        
        Returns:
            Dict containing analysis results.
        """
        self.logger.info("Starting Nightly Dream Analysis...")
        
        # 1. Retrieve Memories (last 24h)
        # Get enough recent memories to cover the day
        raw_memories = self.memory_store.get_recent(limit=300, memory_type=MemoryType.CONVERSATION)
        
        cutoff_time = time.time() - (24 * 3600)
        day_memories = [m for m in raw_memories if m.created_at > cutoff_time]
        
        if not day_memories:
            self.logger.info("No memories found for the last 24h. Skipping analysis.")
            return {"status": "skipped", "reason": "no_memories"}
            
        # Sort chronologically for the LLM
        day_memories.sort(key=lambda m: m.created_at)
        
        # Format for LLM
        context_text = ""
        for m in day_memories:
            dt = datetime.fromtimestamp(m.created_at).strftime("%H:%M:%S")
            context_text += f"[{dt}] {m.content}\n"
            
        self.logger.info(f"Analyzing {len(day_memories)} memories...")
        
        # 2. Generate Insights
        system_manifest = self._get_system_manifest()
        
        prompt = (
            f"{system_manifest}\n\n"
            "## OBIETTIVO: ANALISI E AUTO-MIGLIORAMENTO\n"
            "Analizza le tue interazioni delle ultime 24 ore con occhio critico e costruttivo.\n"
            "Non limitarti a riassumere: cerca pattern, emozioni e opportunità tecniche.\n\n"
            "### 1. Analisi Emotiva & Frustrazioni\n"
            "- Identifica momenti in cui l'utente è sembrato frustrato, confuso o impaziente.\n"
            "- Le tue risposte sono state \"robotiche\" o poco empatiche in quei casi?\n"
            "- Hai fallito nel capire l'intento reale?\n\n"
            "### 2. Gap Analysis (Aspettativa vs Realtà)\n"
            "- Dove l'utente si aspettava che tu facessi qualcosa che non sai fare o hai fatto male?\n"
            "- Ci sono comandi che hai rifiutato perché \"non programmati\" ma che dovresti saper gestire?\n\n"
            "### 3. Idee per il Codice (Code Improvements)\n"
            "- Basandoti sui tuoi fallimenti o limitazioni odierne, proponi 2-3 idee CONCRETE per migliorare il tuo codice Python o le tue Skill.\n"
            "- Esempio: \"Aggiungere una regex per gestire 'spegni tutto' nella Skill HA\".\n"
            "- Esempio: \"Migliorare la gestione del timeout nella navigazione\".\n\n"
            "--- LOG CONVERSAZIONI (ULTIME 24H) ---\n"
            f"{context_text}\n"
            "--- FINE LOG ---\n\n"
            "Genera il Report in Markdown (titolo H2 con Data). Sii onesto, tecnico dove serve, e propositivo."
        )
        
        # Use simple generation (text only)
        response = await self.llm_service.generate(prompt, max_tokens=8192)
        report_content = response.text
        
        if not report_content:
            self.logger.error("Empty response from LLM during analysis.")
            return {"status": "failed", "reason": "empty_llm_response"}

        # 3. Save to Log File
        self._append_to_log(report_content)
        
        # 4. Save Summary to Semantic Memory
        summary_text = f"Analisi Notturna {datetime.now().strftime('%Y-%m-%d')}:\n{report_content}"
        try:
            embedding = await self.embedding_service.embed(summary_text)
            
            summary_mem = Memory(
                id="", # Auto-generate
                content=summary_text,
                memory_type=MemoryType.SUMMARY,
                embedding=embedding,
                metadata={"source": "nightly_dream", "date": datetime.now().strftime('%Y-%m-%d')}
            )
            self.memory_store.add(summary_mem)
            self.logger.info("Saved analysis summary to Semantic Memory.")
            
        except Exception as e:
            self.logger.warning(f"Could not save summary to memory: {e}")

        self.logger.info("Nightly Dream Analysis completed successfully.")
        return {
            "status": "success",
            "memories_analyzed": len(day_memories),
            "report_length": len(report_content)
        }

    def _append_to_log(self, content: str):
        """Append content to the improvement log."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"\n\n---\n# Analysis Run: {timestamp}\n"
        
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(header + content)
            self.logger.info(f"Appended report to {self.log_path}")
        except Exception as e:
            self.logger.error(f"Failed to write log file: {e}")
