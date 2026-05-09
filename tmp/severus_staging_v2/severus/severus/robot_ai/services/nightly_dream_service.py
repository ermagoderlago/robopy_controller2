"""
Robot AI Services - Nightly Dream Service
=========================================
Analyzes daily interactions to generate insights and improve performance.
Supports collaborative analysis with DeepSeek as second AI model.
"""

import time
import os
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from ..rag.memory_store import Memory, MemoryType
from ..rag.base_memory_store import BaseMemoryStore, SearchResult
from ..services.embedding_service import EmbeddingService
from ..services.llm_service import LLMService
from ..core.config_manager import ConfigManager
from ..utils.logging_utils import get_logger


class NightlyDreamService:
    """
    Service for nightly memory analysis and self-improvement.
    
    When DeepSeek is available, runs a 4-turn collaborative analysis:
    1. Gemini → initial analysis
    2. DeepSeek → critical review
    3. Gemini → refined analysis
    4. DeepSeek → master prompt generation
    
    Falls back to single-pass Gemini analysis when DeepSeek is unavailable.
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        memory_store,
        llm_service: LLMService,
        embedding_service: EmbeddingService,
        deepseek_service=None,
    ):
        self.logger = get_logger("nightly_dream")
        self.config = config_manager
        self.memory_store = memory_store
        self.llm_service = llm_service
        self.embedding_service = embedding_service
        self.deepseek_service = deepseek_service
        self.skills_summary = ""
        
        # Paths - Moved to SSD source directory for persistence and easy sync
        base_path = "/mnt/ssd/severus_host/severus"
        if not os.path.exists(base_path):
            # Fallback if SSD is not mounted or path is different
            base_path = os.path.join(os.path.expanduser("~"), "robopy")
            
        self.log_path = os.path.join(base_path, "logs", "continuous_improvements.md")
        self.master_prompt_path = os.path.join(base_path, "logs", "master_prompt.txt")
        
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
        
        Uses collaborative flow (Gemini + DeepSeek) if DeepSeek is available,
        otherwise falls back to single-pass Gemini analysis.
        
        Returns:
            Dict containing analysis results.
        """
        self.logger.info("Starting Nightly Dream Analysis...")
        
        # 1. Retrieve Memories (last 24h)
        raw_results = await self.memory_store.get_recent(limit=300)
        
        cutoff_time = time.time() - (24 * 3600)
        day_results = [r for r in raw_results if r.timestamp > cutoff_time]
        
        if not day_results:
            self.logger.info("No memories found for the last 24h. Skipping analysis.")
            return {"status": "skipped", "reason": "no_memories"}
            
        # Sort chronologically
        day_results.sort(key=lambda r: r.timestamp)
        
        # Format for LLM
        context_text = ""
        for r in day_results:
            dt = datetime.fromtimestamp(r.timestamp).strftime("%H:%M:%S")
            context_text += f"[{dt}] {r.content}\n"
            
        self.logger.info(f"Analyzing {len(day_results)} memories...")
        
        system_manifest = self._get_system_manifest()
        
        # 2. Choose analysis mode
        use_collaboration = (
            self.deepseek_service is not None
            and hasattr(self.deepseek_service, 'is_available')
            and self.deepseek_service.is_available
        )
        
        if use_collaboration:
            self.logger.info("Running COLLABORATIVE analysis (Gemini + DeepSeek)...")
            report_content = await self._run_collaborative_analysis(context_text, system_manifest)
        else:
            self.logger.info("Running SINGLE-PASS analysis (Gemini only)...")
            report_content = await self._run_single_analysis(context_text, system_manifest)

        if not report_content:
            self.logger.error("Empty response during analysis.")
            return {"status": "failed", "reason": "empty_llm_response"}

        # 3. Save to Log File
        self._append_to_log(report_content)
        
        # 4. Save Summary to Semantic Memory
        summary_text = f"Analisi Notturna {datetime.now().strftime('%Y-%m-%d')}:\n{report_content}"
        try:
            metadata = {
                "memory_type": MemoryType.SUMMARY.value,
                "source": "nightly_dream",
                "date": datetime.now().strftime('%Y-%m-%d'),
                "created_at": time.time(),
            }
            await self.memory_store.add(summary_text, metadata)
            self.logger.info("Saved analysis summary to Semantic Memory.")
            
        except Exception as e:
            self.logger.warning(f"Could not save summary to memory: {e}")

        self.logger.info("Nightly Dream Analysis completed successfully.")
        return {
            "status": "success",
            "memories_analyzed": len(day_results),
            "report_length": len(report_content),
            "collaborative": use_collaboration,
        }

    async def _run_single_analysis(self, context_text: str, system_manifest: str) -> str:
        """Single-pass Gemini analysis (fallback mode)."""
        prompt = self._build_gemini_analysis_prompt(context_text, system_manifest)
        response = await self.llm_service.generate(prompt, max_tokens=8192)
        return response.text or ""

    async def _run_collaborative_analysis(self, context_text: str, system_manifest: str) -> str:
        """
        4-turn collaborative analysis between Gemini and DeepSeek.
        
        Turn 1: Gemini → Initial analysis of daily interactions
        Turn 2: DeepSeek → Critical review of Gemini's analysis
        Turn 3: Gemini → Refined analysis integrating both perspectives
        Turn 4: DeepSeek → Generate definitive master prompt
        
        Returns:
            Full report text combining all turns.
        """
        full_report_parts = []
        
        # --- Turn 1: Gemini Initial Analysis ---
        self.logger.info("Turn 1/4: Gemini initial analysis...")
        gemini_prompt = self._build_gemini_analysis_prompt(context_text, system_manifest)
        
        try:
            gemini_response = await self.llm_service.generate(gemini_prompt, max_tokens=8192)
            gemini_analysis = gemini_response.text or ""
        except Exception as e:
            self.logger.error(f"Turn 1 (Gemini) failed: {e}")
            return ""
        
        if not gemini_analysis:
            self.logger.error("Turn 1: Empty Gemini response")
            return ""
        
        full_report_parts.append(f"## 🧠 Turno 1 — Analisi Gemini\n\n{gemini_analysis}")
        self.logger.info(f"Turn 1 complete ({len(gemini_analysis)} chars)")
        
        # --- Turn 2: DeepSeek Critical Review ---
        self.logger.info("Turn 2/4: DeepSeek critical review...")
        deepseek_review_prompt = (
            f"{system_manifest}\n\n"
            "## CONTESTO\n"
            "Sei stato chiamato come secondo analista AI per un robot domestico.\n"
            "Un altro modello (Gemini) ha già analizzato le interazioni della giornata.\n"
            "Il tuo compito è fornire una REVISIONE CRITICA dell'analisi.\n\n"
            "### Analisi di Gemini:\n"
            f"{gemini_analysis}\n\n"
            "### Log conversazioni originali:\n"
            f"{context_text[:4000]}\n\n"  # Truncate to avoid token limits
            "## ISTRUZIONI\n"
            "1. Identifica punti ciechi o bias nell'analisi di Gemini\n"
            "2. Aggiungi osservazioni che Gemini ha mancato\n"
            "3. Proponi miglioramenti tecnici concreti al codice del robot\n"
            "4. Valuta la qualità delle interazioni da una prospettiva diversa\n"
            "5. Sii diretto, tecnico e costruttivo\n\n"
            "Rispondi in italiano."
        )
        
        try:
            deepseek_review = await self.deepseek_service.generate(
                deepseek_review_prompt,
                system_prompt="Sei un analista AI esperto di robotica e interazione uomo-macchina. Fornisci revisioni critiche e costruttive.",
            )
        except Exception as e:
            self.logger.warning(f"Turn 2 (DeepSeek) failed: {e}. Falling back to Gemini-only.")
            # Graceful fallback: return just Gemini analysis
            return gemini_analysis
        
        if not deepseek_review:
            self.logger.warning("Turn 2: Empty DeepSeek response. Using Gemini-only result.")
            return gemini_analysis
        
        full_report_parts.append(f"## 🔍 Turno 2 — Revisione Critica DeepSeek\n\n{deepseek_review}")
        self.logger.info(f"Turn 2 complete ({len(deepseek_review)} chars)")
        
        # --- Turn 3: Gemini Refined Analysis ---
        self.logger.info("Turn 3/4: Gemini refined analysis...")
        gemini_refined_prompt = (
            f"{system_manifest}\n\n"
            "## CONTESTO\n"
            "Hai già prodotto un'analisi delle interazioni della giornata.\n"
            "Un secondo analista (DeepSeek) ha fornito una revisione critica.\n"
            "Ora devi produrre un'ANALISI RAFFINATA che integra entrambe le prospettive.\n\n"
            "### La tua analisi iniziale:\n"
            f"{gemini_analysis[:3000]}\n\n"
            "### Revisione critica di DeepSeek:\n"
            f"{deepseek_review[:3000]}\n\n"
            "## ISTRUZIONI\n"
            "1. Integra le osservazioni valide di DeepSeek\n"
            "2. Difendi i punti della tua analisi che ritieni corretti\n"
            "3. Produci una lista finale di MIGLIORAMENTI PRIORITARI (max 5)\n"
            "4. Per ogni miglioramento, indica:\n"
            "   - Il problema specifico\n"
            "   - La soluzione proposta (con riferimento al codice se possibile)\n"
            "   - La priorità (alta/media/bassa)\n\n"
            "Rispondi in italiano."
        )
        
        try:
            gemini_refined_response = await self.llm_service.generate(gemini_refined_prompt, max_tokens=8192)
            gemini_refined = gemini_refined_response.text or ""
        except Exception as e:
            self.logger.warning(f"Turn 3 (Gemini refined) failed: {e}")
            gemini_refined = ""
        
        if gemini_refined:
            full_report_parts.append(f"## ✨ Turno 3 — Analisi Raffinata Gemini\n\n{gemini_refined}")
            self.logger.info(f"Turn 3 complete ({len(gemini_refined)} chars)")
        
        # --- Turn 4: DeepSeek Master Prompt Generation ---
        self.logger.info("Turn 4/4: DeepSeek master prompt generation...")
        master_prompt_request = (
            f"{system_manifest}\n\n"
            "## CONTESTO\n"
            "Due AI (Gemini e DeepSeek) hanno collaborato per analizzare le interazioni "
            "di un robot domestico nella giornata.\n\n"
            "### Analisi Gemini (iniziale):\n"
            f"{gemini_analysis[:2000]}\n\n"
            "### Revisione DeepSeek:\n"
            f"{deepseek_review[:2000]}\n\n"
        )
        if gemini_refined:
            master_prompt_request += (
                "### Analisi raffinata Gemini:\n"
                f"{gemini_refined[:2000]}\n\n"
            )
        master_prompt_request += (
            "## ISTRUZIONI\n"
            "Basandoti su TUTTE le analisi sopra, genera un MASTER PROMPT definitivo.\n"
            "Questo master prompt verrà prepeso al prompt di sistema del robot per migliorare "
            "il suo comportamento nelle prossime interazioni.\n\n"
            "FORMATO RICHIESTO:\n"
            "Genera un elenco puntato di istruzioni concise (max 20) in italiano.\n"
            "Ogni istruzione deve essere operativa e specifica.\n\n"
            "Esempio di formato:\n"
            "- Quando l'utente dice \"spegni tutto\", spegni tutte le luci e le tapparelle.\n"
            "- Se riconosci Luca, usa un tono informale e sii proattivo.\n"
            "- In caso di errore di navigazione, chiedi se fermarsi o riprovare.\n\n"
            "NON includere istruzioni generiche. Basati SOLO sui pattern reali osservati oggi.\n"
            "Rispondi SOLO con l'elenco puntato, senza introduzioni o conclusioni."
        )
        
        try:
            master_prompt = await self.deepseek_service.generate(
                master_prompt_request,
                system_prompt="Sei un esperto di prompt engineering. Genera istruzioni operative concise per un robot domestico.",
            )
        except Exception as e:
            self.logger.warning(f"Turn 4 (DeepSeek master prompt) failed: {e}")
            master_prompt = ""
        
        if master_prompt:
            full_report_parts.append(f"## 📋 Turno 4 — Master Prompt (DeepSeek)\n\n{master_prompt}")
            self.logger.info(f"Turn 4 complete ({len(master_prompt)} chars)")
            
            # Save master prompt to file
            self._save_master_prompt(master_prompt)
        else:
            self.logger.warning("Turn 4: No master prompt generated")
        
        # Combine all turns into final report
        full_report = "\n\n---\n\n".join(full_report_parts)
        return full_report

    def _build_gemini_analysis_prompt(self, context_text: str, system_manifest: str) -> str:
        """Build the initial Gemini analysis prompt."""
        return (
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

    def _save_master_prompt(self, master_prompt: str):
        """Save master prompt to file for system prompt integration."""
        try:
            os.makedirs(os.path.dirname(self.master_prompt_path), exist_ok=True)
            with open(self.master_prompt_path, "w", encoding="utf-8") as f:
                f.write(master_prompt)
            self.logger.info(f"Master prompt saved to {self.master_prompt_path}")
        except Exception as e:
            self.logger.error(f"Failed to save master prompt: {e}")

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
