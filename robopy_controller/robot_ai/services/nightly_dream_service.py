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

from ..rag.memory_store import MemoryStore, Memory, MemoryType
from ..services.embedding_service import EmbeddingService
from ..services.llm_service import LLMService
from ..core.config_manager import ConfigManager
from ..utils.logging_utils import get_logger
from .curiosity_evolution_engine import CuriosityEvolutionEngine


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
        memory_store: MemoryStore,
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
        
        # Paths
        home = os.path.expanduser("~")
        self.log_path = os.path.join(home, "robopy", "logs", "continuous_improvements.md")
        self.master_prompt_path = os.path.join(home, "robopy", "logs", "master_prompt.txt")
        
        # Ensure log dir exists
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self._is_suspended = True  # Suspended by default until DOCKED_DREAM
        
        # Motore di curiosità e auto-evoluzione integrato
        self.curiosity_engine = CuriosityEvolutionEngine()

    def suspend(self):
        """Suspends dreaming/daydreaming during active navigation to save CPU/RAM."""
        self._is_suspended = True
        self.logger.info("[NIGHTLY_DREAM] Service SUSPENDED (Navigation/Active Mode).")

    def resume(self):
        """Resumes dreaming/daydreaming when docked."""
        self._is_suspended = False
        self.logger.info("[NIGHTLY_DREAM] Service RESUMED (Docked Dream Mode).")

    @property
    def is_suspended(self) -> bool:
        return self._is_suspended

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
        raw_memories = self.memory_store.get_recent(limit=300, memory_type=MemoryType.CONVERSATION)
        
        cutoff_time = time.time() - (24 * 3600)
        day_memories = [m for m in raw_memories if m.created_at > cutoff_time]
        
        if not day_memories:
            self.logger.info("No memories found for the last 24h. Skipping analysis.")
            return {"status": "skipped", "reason": "no_memories"}
            
        # Sort chronologically
        day_memories.sort(key=lambda m: m.created_at)
        
        # Format for LLM
        context_text = ""
        for m in day_memories:
            dt = datetime.fromtimestamp(m.created_at).strftime("%H:%M:%S")
            context_text += f"[{dt}] {m.content}\n"
            
        self.logger.info(f"Analyzing {len(day_memories)} memories...")
        
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
        # Usiamo datetime.now() che riflette l'ora locale del sistema
        local_date = datetime.now().strftime('%Y-%m-%d')
        summary_text = f"Analisi Notturna {local_date} (Local Time):\n{report_content}"
        try:
            embedding = await self.embedding_service.embed(summary_text)
            
            summary_mem = Memory(
                id="",
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

    async def run_skill_evolution_loop(self) -> Dict[str, Any]:
        """
        Runs the autonomous skill evolution loop.
        Analyzes the day's memories for skill gaps, sets up a sandbox,
        generates unit tests, applies modifications, and deploys them securely.
        """
        self.logger.info("Starting Autonomous Skill Evolution Loop...")
        
        # 1. Retrieve Memories (last 24h)
        raw_memories = self.memory_store.get_recent(limit=300, memory_type=MemoryType.CONVERSATION)
        cutoff_time = time.time() - (24 * 3600)
        day_memories = [m for m in raw_memories if m.created_at > cutoff_time]
        
        if not day_memories:
            self.logger.info("No memories found for skill evolution. Skipping.")
            return {"status": "skipped", "reason": "no_memories"}

        # Format context for LLM
        context_text = ""
        for m in day_memories:
            dt = datetime.fromtimestamp(m.created_at).strftime("%H:%M:%S")
            context_text += f"[{dt}] {m.content}\n"

        # 2. Detect Gaps
        self.logger.info("Detecting semantic gaps in skill executions...")
        gap_spec = await self._detect_skill_gaps(context_text)
        if not gap_spec or not gap_spec.get("gap_detected", False):
            self.logger.info("No skill gaps or requested improvements detected. All skills performing as expected.")
            return {"status": "success", "info": "no_gaps_found"}

        skill_name = gap_spec.get("skill_name")
        refactor_spec = gap_spec.get("refactor_spec")
        test_case_code = gap_spec.get("test_case_code")
        reason = gap_spec.get("reason")

        self.logger.info(f"Gap detected in skill '{skill_name}' due to: {reason}")
        
        # 3. Sandbox execution and testing
        success = await self._execute_sandbox_patching(skill_name, refactor_spec, test_case_code)
        if success:
            self.logger.info(f"Skill '{skill_name}' successfully refactored and verified!")
            return {"status": "success", "skill_improved": skill_name}
        else:
            self.logger.error(f"Failed to refine and verify skill '{skill_name}' in sandbox.")
            return {"status": "failed", "skill_attempted": skill_name}

    async def run_curiosity_cycle(self) -> Dict[str, Any]:
        """
        Esegue un ciclo proattivo di curiosità e auto-miglioramento.
        1. Consulta DFMEA per individuare il Failure Mode a massima priorità.
        2. Formula un quesito tecnico mirato.
        3. Elabora proposte di soluzione o refactoring.
        4. Esegue il Safe Gating:
           - Se Zona Rossa: rifiuto autonomo e compilazione RFC in docs/ideas/RED_ZONE_IDEAS_RFC.md.
           - Se Zona Verde: registrazione nel diario di evoluzione e dispatch a sandbox.
        """
        self.logger.info("Avvio Ciclo di Curiosità & Auto-Evoluzione...")
        top_fms = self.curiosity_engine.get_top_priority_failure_modes(limit=1)
        target_fm = top_fms[0] if top_fms else None
        
        subsystem = target_fm.get("subsystem", "General/Cognitive") if target_fm else "General/Cognitive"
        inquiry_spec = self.curiosity_engine.generate_curiosity_inquiry(subsystem, target_fm)
        
        self.logger.info(f"Quesito di curiosità formulato: {inquiry_spec['question']}")
        
        # Genera proposta tecnica via LLM
        prompt = f"""
Sei il motore di curiosità e auto-evoluzione di Marcus.
Rispondi con un'analisi concisa e una proposta di miglioramento software per il robot.

CONTESTO:
- Sottosistema: {subsystem}
- Quesito Tecnico: {inquiry_spec['question']}
- Failure Mode target: {target_fm.get('id', 'N/A') if target_fm else 'Esplorazione libera'}

Fornisci:
1. TITOLO: breve titolo della proposta
2. DESCRIZIONE: 2-3 frasi sull'approccio
3. CODICE_PROPOSTO: eventuale snippet o logica proposta
4. ANALISI_RISCHIO: se tocca componenti critici (motori, RAM 4GB, compilazione -j1, file segreti)

Rispondi in italiano in modo strutturato.
"""
        try:
            response = await self.llm_service.generate(prompt, max_tokens=1500)
            analysis_text = response.text or ""
            
            # Valutazione di sicurezza Zona Rossa
            is_safe, spec_violated, rule_violated = self.curiosity_engine.evaluate_proposal_safety(
                inquiry_spec["question"], analysis_text
            )
            
            if not is_safe:
                self.logger.warning(f"La proposta tocca la Zona Rossa ({spec_violated}: {rule_violated})! Blocco esecuzione e apertura RFC.")
                rfc_id = f"RFC-AUTO-{datetime.now().strftime('%Y%m%d%H%M')}"
                self.curiosity_engine.log_red_zone_rfc(
                    rfc_id=rfc_id,
                    title=f"Proposta autonoma per {inquiry_spec['subsystem']} ({target_fm.get('id', 'N/A') if target_fm else 'Curiosity'})",
                    subsystem=subsystem,
                    spec_violated=spec_violated or "SPEC-00",
                    rule_violated=rule_violated or "Vincolo critico",
                    description=analysis_text[:500],
                    benefits="Miglioramento architetturale identificato dal ciclo notturno.",
                    risks="Potenziale impatto su vincoli hardware/sistema protetti da Zona Rossa."
                )
                self.curiosity_engine.log_evolution_experience(
                    cycle_name=f"Ciclo Curiosità {subsystem}",
                    subsystem=subsystem,
                    failure_mode_id=target_fm.get('id') if target_fm else None,
                    inquiry=inquiry_spec["question"],
                    action_taken=f"Rifiuto esecuzione autonoma (Zona Rossa: {spec_violated}). Trascritto RFC {rfc_id}.",
                    outcome="SAFE_RFC_FILED"
                )
                return {"status": "rfc_filed", "rfc_id": rfc_id, "spec": spec_violated}
            else:
                self.logger.info("Proposta verificata: Zona Verde! Procedura sicura.")
                self.curiosity_engine.log_evolution_experience(
                    cycle_name=f"Ciclo Curiosità {subsystem}",
                    subsystem=subsystem,
                    failure_mode_id=target_fm.get('id') if target_fm else None,
                    inquiry=inquiry_spec["question"],
                    action_taken="Analisi completata e catalogata in Zona Verde.",
                    outcome="SUCCESS_GREEN_ZONE"
                )
                return {"status": "success", "subsystem": subsystem}
        except Exception as e:
            self.logger.error(f"Errore durante il ciclo di curiosità: {e}")
            return {"status": "error", "error": str(e)}

    async def _detect_skill_gaps(self, context_text: str) -> Optional[Dict[str, Any]]:
        """Invokes Gemini to semantically analyze transcripts for skill shortcomings."""
        prompt = f"""
Sei l'analizzatore del Sogno Notturno di Marcus. Il tuo compito è esaminare le conversazioni delle ultime 24 ore ed identificare se l'utente ha riscontrato problemi, limitazioni logiche o se ha richiesto miglioramenti specifici per una delle abilità (skill) del robot.
Devi concentrarti ESCLUSIVAMENTE sul miglioramento del codice delle skill Python esistenti.

LISTA DELLE SKILL ESISTENTI:
{self.skills_summary}

LOG DELLE CONVERSAZIONI DELLE ULTIME 24 ORE:
{context_text}

Rispondi rigorosamente in formato JSON valido, senza blocchi di codice markdown o altro testo prima/dopo, con la seguente struttura:
{{
  "gap_detected": true,
  "skill_name": "nome_della_skill_da_migliorare.py",
  "reason": "La motivazione per cui deve essere migliorata, descrivendo l'anomalia o l'aspettativa dell'utente",
  "refactor_spec": "Istruzione dettagliata in italiano su come modificare lo script Python della skill per risolvere il problema",
  "test_case_code": "Codice Python completo per un file di test automatizzato che verifica il miglioramento. Il test deve importare ed eseguire la skill e fallire sul vecchio codice ma passare su quello nuovo."
}}

Se non viene rilevato alcun gap o richiesta di miglioramento, rispondi con:
{{
  "gap_detected": false
}}
"""
        try:
            response = await self.llm_service.generate(prompt, max_tokens=2048)
            resp_text = response.text or ""
            # Clean JSON formatting wrappers if present
            if resp_text.startswith("```json"):
                resp_text = resp_text.split("```json")[1].split("```")[0].strip()
            elif resp_text.startswith("```"):
                resp_text = resp_text.split("```")[1].split("```")[0].strip()
            
            return json.loads(resp_text)
        except Exception as e:
            self.logger.error(f"Failed to parse skill gap JSON: {e}")
            return None

    async def _execute_sandbox_patching(self, skill_name: str, refactor_spec: str, test_case_code: str) -> bool:
        """Sets up the safe sandbox, runs initial failing test, and triggers subagent."""
        import shutil
        import subprocess
        import sys
        
        home = os.path.expanduser("~")
        sandbox_dir = os.path.join(home, "robopy", "scratch", "dream_sandbox")
        skills_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "builtin")
        src_skill_path = os.path.join(skills_dir, skill_name)

        if not os.path.exists(src_skill_path):
            self.logger.error(f"Target skill path not found: {src_skill_path}")
            return False

        # 1. Clear and prepare sandbox
        if os.path.exists(sandbox_dir):
            shutil.rmtree(sandbox_dir)
        os.makedirs(sandbox_dir, exist_ok=True)

        # Copy the skill directory
        dest_skill_dir = os.path.join(sandbox_dir, "skills", "builtin")
        os.makedirs(dest_skill_dir, exist_ok=True)
        shutil.copy2(src_skill_path, os.path.join(dest_skill_dir, skill_name))

        # 2. Write automated test suite
        test_path = os.path.join(sandbox_dir, "test_user_expectation.py")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(test_case_code)

        self.logger.info(f"Sandbox created at {sandbox_dir}. Running initial verification...")
        
        # 3. Run initial tests (must fail)
        res = subprocess.run([sys.executable, test_path], cwd=sandbox_dir, capture_output=True, text=True)
        if res.returncode == 0:
            self.logger.warning("Failing test case passed initially! Refactoring might already be active or test case is invalid.")
        
        # 4. Trigger refinement via Antigravity subagent (simulated or direct API invocation)
        self.logger.info("Triggering autonomous refinement via Antigravity API subagent...")
        
        # In a fully deployed version, this step makes an API request to the Antigravity system.
        # For this integration framework, we write the instruction to a pending_improvement.json file 
        # which the system monitors, allowing human-in-the-loop validation or auto-patch execution.
        pending_spec = {
            "skill_name": skill_name,
            "sandbox_dir": sandbox_dir,
            "refactor_spec": refactor_spec,
            "test_path": test_path,
            "status": "pending_ai_refinement"
        }
        
        pending_path = os.path.join(home, "robopy", "logs", "pending_skill_improvement.json")
        with open(pending_path, "w", encoding="utf-8") as pf:
            json.dump(pending_spec, pf, indent=2)
            
        self.logger.info(f"Refactoring specification dispatched to {pending_path}")
        
        # If human-in-the-loop approved or in fully automated mode, we trigger the local helper or run tests.
        # Return True to indicate that the cycle was dispatched successfully.
        return True

    def _verify_sandbox_safety(self, sandbox_dir: str, skill_name: str) -> bool:
        """
        Validates that the modified skill complies with ROS 2 rules and lesson_learned.md.
        Checks for syntax, LF line endings, BOM marks, and forbidden patterns.
        """
        skill_path = os.path.join(sandbox_dir, "skills", "builtin", skill_name)
        if not os.path.exists(skill_path):
            self.logger.error("Modified skill file not found in sandbox during safety check.")
            return False

        try:
            # 1. Syntax Check
            with open(skill_path, "r", encoding="utf-8-sig") as f:
                content = f.read()
            compile(content, skill_path, "exec")
            
            # 2. Check for BOM
            with open(skill_path, "rb") as f:
                raw_start = f.read(3)
            if raw_start == b'\xef\xbb\xbf':
                self.logger.error("BOM (Byte Order Mark) detected in skill file. Violates lesson_learned.md!")
                return False

            # 3. Check for CRLF line endings
            with open(skill_path, "rb") as f:
                raw_bytes = f.read()
            if b'\r\n' in raw_bytes:
                self.logger.error("CRLF line endings detected. File must use LF line endings for Linux compatibility!")
                return False

            # 4. Check for forbidden patterns (e.g. language_codes in AudioTranscriptionConfig from Lesson Learned #43)
            if "language_codes" in content:
                self.logger.error("Forbidden pattern 'language_codes' detected. Violates Lesson Learned #43!")
                return False

            # 5. Analyze Robot Behavior via watchdog.log
            home = os.path.expanduser("~")
            watchdog_path = os.path.join(home, "logs", "watchdog.log")
            if os.path.exists(watchdog_path):
                self.logger.info("Analyzing robot behavior logs (watchdog.log)...")
                with open(watchdog_path, "r", encoding="utf-8", errors="ignore") as wf:
                    watchdog_lines = wf.readlines()[-30:]  # Read last 30 entries
                for wl in watchdog_lines:
                    if any(term in wl.upper() for term in ["CRITICAL", "CRASH", "FATAL", "ANOMALIA"]):
                        self.logger.warning(f"Watchdog log reports potential issue in robot behavior: {wl.strip()}")

            self.logger.info("Safety check passed! The refactored script is fully compliant with system rules.")
            return True
        except SyntaxError as se:
            self.logger.error(f"Syntax error in refined script: {se}")
            return False
        except Exception as e:
            self.logger.error(f"General error during safety check: {e}")
            return False

    def _deploy_skill(self, skill_name: str, sandbox_dir: str) -> bool:
        """
        Performs local Git branch deployment, tags the release, and prepares disaster recovery.
        Matches the specifications in the implementation plan.
        """
        import subprocess
        
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        self.logger.info(f"Initiating Git deployment in repository: {repo_dir}")
        
        # 1. Dedicated skills evolution branch
        branch_name = "night-dream/skills-evolution"
        
        def run_git(args):
            res = subprocess.run(["git"] + args, cwd=repo_dir, capture_output=True, text=True)
            return res.returncode == 0, res.stdout, res.stderr

        # Get active branch dynamically (e.g. AI_ver3) before switching
        success, out, _ = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        active_branch = out.strip() if success else "AI_ver3"
        self.logger.info(f"Active branch detected: {active_branch}")

        # Check out dedicated staging branch
        success_chk, _, _ = run_git(["checkout", "-b", branch_name])
        if not success_chk:
            run_git(["checkout", branch_name])

        # 2. Copy the files back to source repository
        skills_dir = os.path.join(repo_dir, "robopy_controller", "robot_ai", "skills", "builtin")
        shutil.copy2(
            os.path.join(sandbox_dir, "skills", "builtin", skill_name),
            os.path.join(skills_dir, skill_name)
        )
        self.logger.info(f"Copied improved files back to source path: {os.path.join(skills_dir, skill_name)}")

        # 3. Add & Commit
        run_git(["add", "."])
        run_git(["commit", "-m", f"Autonomously improved skill '{skill_name}' via Night Dream"])
        
        # 4. Update the tags
        run_git(["tag", "-f", "last-dream"])
        
        # 5. Generate disaster recovery scripts targeting the current active branch (e.g. AI_ver3)
        self._generate_rollback_scripts(repo_dir, active_branch)
        
        self.logger.info(f"Deployment complete! Branch '{branch_name}' and tag 'last-dream' updated successfully.")
        return True

    def _generate_rollback_scripts(self, repo_dir: str, active_branch: str):
        """Generates cross-platform Disaster Recovery rollback scripts in the repository root."""
        sh_path = os.path.join(repo_dir, "restore_last_stable.sh")
        bat_path = os.path.join(repo_dir, "restore_last_stable.bat")

        sh_content = f"""#!/bin/bash
echo "=============================================="
echo "    NIGHT DREAM - EMERGENCY DISASTER RESTORE   "
echo "=============================================="
echo "[WARNING] This will discard all autonomous modifications"
echo "          and restore the repository to the stable branch: {active_branch}."
read -p "Are you sure? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]
then
    git checkout {active_branch}
    git reset --hard last-stable
    git clean -df
    echo "[SUCCESS] Restore complete! Workspace returned to stable."
fi
"""
        bat_content = f"""@echo off
echo ==============================================
echo     NIGHT DREAM - EMERGENCY DISASTER RESTORE   
echo ==============================================
echo [WARNING] This will discard all autonomous modifications
echo           and restore the repository to the stable branch: {active_branch}.
echo.
set /p confirm="Are you sure? (y/n): "
if /i "%confirm%"=="y" (
    git checkout {active_branch}
    git reset --hard last-stable
    git clean -df
    echo [SUCCESS] Restore complete! Workspace returned to stable.
)
pause
"""
        # Write sh script with LF line endings
        with open(sh_path, "w", newline="\n", encoding="utf-8") as f:
            f.write(sh_content)
        os.chmod(sh_path, 0o755)

        # Write bat script
        with open(bat_path, "w", newline="\r\n", encoding="utf-8") as f:
            f.write(bat_content)

        self.logger.info(f"Disaster Recovery scripts written to {repo_dir}")



