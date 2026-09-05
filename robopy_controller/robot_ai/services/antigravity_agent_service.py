"""
Robot AI Services - Antigravity Agent Autonomous Service
=========================================================
Wrapper ad alte prestazioni per interagire con l'Agente Antigravity nativo in piena autonomia,
con governance proattiva delle quote e del ciclo di vita delle sessioni.

Caratteristiche:
1. Modello Gemini: Utilizzo prioritario della versione più nuova e avanzata (es. gemini-2.5-pro / gemini-3.8-flash).
2. Piena Autonomia: policies=[policy.allow_all()] per operare senza interazione umana da terminale.
3. Protezione RAM Pi 5: enable_subagents=False per non saturare i 4GB di memoria dell'host.
4. Rolling Token Quota Guard (Finestra 4 ore, Soglia 90%):
   - Traccia i token consumati nelle ultime 4 ore tramite ledger persistente (token_quota_state.json).
   - Se l'utilizzo supera il 90% del budget disponibile sulle 4 ore, l'attività viene congelata.
5. Checkpointing & Resumption:
   - Attività non completate per esaurimento quota vengono serializzate preservando il conversation_id.
   - Nelle sedute successive, la sessione viene ripresa via SessionContinuationMode.RESUME.
"""

import os
import re
import json
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger("robot_ai.antigravity_agent_service")

_ANTIGRAVITY_AVAILABLE = False
try:
    from google.antigravity import (
        Agent,
        LocalAgentConfig,
        CapabilitiesConfig,
        AgentBehavior,
    )
    import google.antigravity.policy as policy
    from google.antigravity.types import SessionContinuationMode
    _ANTIGRAVITY_AVAILABLE = True
except ImportError as err:
    logger.warning(f"Modulo 'google.antigravity' non importabile: {err}")


# ---------------------------------------------------------------------------
# Costanti e Configurazioni di Default
# ---------------------------------------------------------------------------
QUOTA_WINDOW_SECONDS = 4 * 3600
DEFAULT_MAX_4H_TOKENS = int(os.environ.get("ANTIGRAVITY_4H_TOKEN_BUDGET", "1000000"))
QUOTA_THROTTLE_THRESHOLD = 0.90

# Modelli Gemini candidati in ordine di preferenza (Generazione 3.8 primaria)
PREFERRED_GEMINI_MODELS = [
    "gemini-3.8-flash",  # 1° Scelta Primaria: Generazione 3.8 (Default nativo Antigravity, reasoning avanzato con thinking)
    "gemini-3.8-pro",    # 2° Scelta: Gemini 3.8 Pro per compiti ad altissima complessità
    "gemini-2.5-pro",    # 3° Scelta: Fallback serie 2.5
    "gemini-2.5-flash",  # 4° Scelta: Fallback rapido
]


def _resolve_workspace_root() -> Path:
    # 1. Se siamo sull'host Marcus Raspberry Pi 5
    fixed_host_root = Path("/mnt/ssd/robopy_controller_host")
    if (fixed_host_root / "marcus_core_rules.md").exists():
        return fixed_host_root

    # 2. Risalita ricorsiva per trovare marcus_core_rules.md
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "marcus_core_rules.md").exists():
            return parent

    return fixed_host_root


def load_gemini_api_key(workspace_root: Optional[Path] = None) -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key

    root = workspace_root or _resolve_workspace_root()
    env_paths = [
        root / ".env",
        Path("/mnt/ssd/robopy_controller_host/.env"),
        Path(".env")
    ]
    for p in env_paths:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GEMINI_API_KEY="):
                            found = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if found:
                                os.environ["GEMINI_API_KEY"] = found
                                return found
            except Exception as e:
                logger.warning(f"[Antigravity] Errore lettura {p}: {e}")
    return ""


class TokenQuotaTracker:
    """
    Monitora e limita il consumo di token su una finestra scorrevole di 4 ore.
    Garantisce che non si superi mai il 90% del budget disponibile.
    """

    def __init__(self, workspace_root: Path, max_4h_tokens: int = DEFAULT_MAX_4H_TOKENS):
        self.workspace_root = workspace_root
        self.max_4h_tokens = max_4h_tokens
        self.throttle_threshold = QUOTA_THROTTLE_THRESHOLD
        self.ledger_file = self.workspace_root / "docs" / "evolution" / "token_quota_ledger.json"
        self._ensure_ledger_file()

    def _ensure_ledger_file(self):
        self.ledger_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.ledger_file.exists():
            try:
                with open(self.ledger_file, "w", encoding="utf-8") as f:
                    json.dump({"entries": []}, f, indent=2)
            except Exception as e:
                logger.error(f"[QuotaTracker] Impossibile inizializzare ledger: {e}")

    def _clean_and_load_entries(self) -> List[Dict[str, Any]]:
        now = time.time()
        cutoff = now - QUOTA_WINDOW_SECONDS
        entries = []
        if self.ledger_file.exists():
            try:
                with open(self.ledger_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    raw_entries = data.get("entries", [])
                    entries = [e for e in raw_entries if e.get("timestamp", 0) >= cutoff]
            except Exception as e:
                logger.error(f"[QuotaTracker] Errore lettura ledger: {e}")
                entries = []
        return entries

    def get_rolling_usage(self) -> Tuple[int, float]:
        entries = self._clean_and_load_entries()
        used_tokens = sum(e.get("total_tokens", 0) for e in entries)
        ratio = (used_tokens / self.max_4h_tokens) if self.max_4h_tokens > 0 else 0.0
        return used_tokens, ratio

    def check_quota_available(self, estimated_tokens: int = 8000) -> Tuple[bool, int, float]:
        used_tokens, ratio = self.get_rolling_usage()
        projected_used = used_tokens + estimated_tokens
        projected_ratio = (projected_used / self.max_4h_tokens) if self.max_4h_tokens > 0 else 0.0
        is_allowed = projected_ratio <= self.throttle_threshold
        return is_allowed, used_tokens, ratio

    def record_usage(
        self,
        total_tokens: int,
        prompt_tokens: int = 0,
        candidates_tokens: int = 0,
        model: str = "unknown"
    ) -> None:
        entries = self._clean_and_load_entries()
        now = time.time()
        new_entry = {
            "timestamp": now,
            "iso_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "candidates_tokens": candidates_tokens,
            "model": model
        }
        entries.append(new_entry)
        try:
            with open(self.ledger_file, "w", encoding="utf-8") as f:
                json.dump({"entries": entries}, f, indent=2)
        except Exception as e:
            logger.error(f"[QuotaTracker] Errore salvataggio ledger: {e}")

    def get_time_until_cooldown(self) -> float:
        entries = self._clean_and_load_entries()
        if not entries:
            return 0.0
        oldest_ts = min(e.get("timestamp", time.time()) for e in entries)
        elapsed = time.time() - oldest_ts
        remaining = QUOTA_WINDOW_SECONDS - elapsed
        return max(0.0, remaining)


class EvolutionCheckpointManager:
    """
    Gestisce lo stato persistente delle attività evolutive interrotte per ragioni
    di quota, consentendo di riprenderle nelle sedute successive con session continuation.
    """

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root
        self.checkpoint_file = self.workspace_root / "docs" / "evolution" / "evolution_checkpoint.json"

    def save_suspended_checkpoint(
        self,
        task_id: str,
        conversation_id: str,
        model: str,
        task_type: str,
        completed_steps: List[str],
        pending_steps: List[str],
        state_data: Dict[str, Any]
    ) -> bool:
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        payload = {
            "task_id": task_id,
            "conversation_id": conversation_id,
            "model": model,
            "task_type": task_type,
            "status": "SUSPENDED_QUOTA_90_PERCENT",
            "suspended_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "timestamp": now,
            "completed_steps": completed_steps,
            "pending_steps": pending_steps,
            "state_data": state_data
        }
        try:
            with open(self.checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.info(f"[Checkpoint] Attività '{task_id}' salvata con successo per ripresa differita.")
            return True
        except Exception as e:
            logger.error(f"[Checkpoint] Errore scrittura checkpoint: {e}")
            return False

    def load_pending_checkpoint(self) -> Optional[Dict[str, Any]]:
        if not self.checkpoint_file.exists():
            return None
        try:
            with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("status") == "SUSPENDED_QUOTA_90_PERCENT":
                return data
            return None
        except Exception as e:
            logger.error(f"[Checkpoint] Errore lettura checkpoint: {e}")
            return None

    def clear_checkpoint(self) -> None:
        if self.checkpoint_file.exists():
            try:
                self.checkpoint_file.unlink()
                logger.info("[Checkpoint] Attività completata: checkpoint rimosso.")
            except Exception as e:
                logger.warning(f"[Checkpoint] Impossibile eliminare checkpoint: {e}")


class AntigravityAgentService:
    """
    Servizio unificato per l'esecuzione di task agentici in piena autonomia con Antigravity.
    Include selezione del modello Gemini più recente, tracker del budget token a 4 ore
    e ripresa automatica delle sessioni interrotte.
    """

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or _resolve_workspace_root()
        self._is_available = _ANTIGRAVITY_AVAILABLE
        self.api_key = load_gemini_api_key(self.workspace_root)
        self.quota_tracker = TokenQuotaTracker(self.workspace_root)
        self.checkpoint_mgr = EvolutionCheckpointManager(self.workspace_root)

        env_model = os.environ.get("ANTIGRAVITY_MODEL", "").strip()
        self.target_model = env_model if env_model else PREFERRED_GEMINI_MODELS[0]

    @property
    def is_available(self) -> bool:
        """Verifica se il runtime Antigravity è installato e l'API key è presente."""
        return self._is_available and bool(self.api_key)

    def get_quota_status(self) -> Dict[str, Any]:
        used, ratio = self.quota_tracker.get_rolling_usage()
        return {
            "used_tokens_4h": used,
            "max_tokens_4h": self.quota_tracker.max_4h_tokens,
            "usage_percentage": round(ratio * 100, 2),
            "is_throttled": ratio >= self.quota_tracker.throttle_threshold,
            "cooldown_seconds": round(self.quota_tracker.get_time_until_cooldown(), 1),
            "target_model": self.target_model
        }

    async def generate_code_autonomous(
        self,
        prompt: str,
        system_instructions: Optional[str] = None,
        timeout_seconds: float = 60.0,
        conversation_id: Optional[str] = None,
        resume_existing: bool = False,
        task_metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        if not self.is_available:
            raise RuntimeError(
                f"Antigravity SDK non disponibile o API Key mancante (SDK: {self._is_available}, Key: {bool(self.api_key)})"
            )

        # 1. Verifica quota a 4 ore (soglia 90%)
        can_proceed, used_tokens, ratio = self.quota_tracker.check_quota_available(estimated_tokens=8000)
        if not can_proceed:
            logger.warning(
                f"[Antigravity Quota Guard] Soglia 90% raggiunta ({used_tokens}/{self.quota_tracker.max_4h_tokens}, "
                f"{ratio*100:.1f}%). Congelamento attività per le sedute successive."
            )
            if task_metadata:
                self.checkpoint_mgr.save_suspended_checkpoint(
                    task_id=task_metadata.get("task_id", f"task_{int(time.time())}"),
                    conversation_id=conversation_id or "default_session",
                    model=self.target_model,
                    task_type=task_metadata.get("task_type", "skill_generation"),
                    completed_steps=task_metadata.get("completed_steps", []),
                    pending_steps=task_metadata.get("pending_steps", ["COMPLETE_CODE_GENERATION"]),
                    state_data={"prompt": prompt, "metadata": task_metadata}
                )
            raise RuntimeError(
                f"QUOTA_90_PERCENT_REACHED: Utilizzo token a 4 ore al {ratio*100:.1f}%. Attività sospesa per le sedute successive."
            )

        sys_inst = system_instructions or (
            "Sei l'agente Antigravity evolutivo di Marcus AI (Raspberry Pi 5). "
            "Il tuo obiettivo è generare o refattorizzare codice Python di abilità (Skills) per ROS 2 Jazzy. "
            "Devi rispettare tassativamente i vincoli di marcus_core_rules.md e l'interfaccia BaseSkill. "
            "Restituisci ESCLUSIVAMENTE il codice Python compreso tra i tag <SKILL_CODE> e </SKILL_CODE>."
        )

        continuation_mode = None
        if resume_existing and conversation_id:
            continuation_mode = SessionContinuationMode.RESUME

        config_kwargs: Dict[str, Any] = {
            "api_key": self.api_key,
            "model": self.target_model,
            "workspaces": [str(self.workspace_root), "/home/robopy"],
            "system_instructions": sys_inst,
            "capabilities": CapabilitiesConfig(
                agent_behavior=AgentBehavior.AUTONOMOUS,
                enable_subagents=False,
            ),
            "policies": [policy.allow_all()],
        }

        if conversation_id:
            config_kwargs["conversation_id"] = conversation_id
        if continuation_mode:
            config_kwargs["session_continuation_mode"] = continuation_mode

        config = LocalAgentConfig(**config_kwargs)

        logger.info(
            f"[Antigravity] Avvio sessione autonoma (Modello: {self.target_model}, "
            f"Quota 4h: {used_tokens}/{self.quota_tracker.max_4h_tokens} - {ratio*100:.1f}%)..."
        )
        full_response = ""
        total_tokens_consumed = 0
        prompt_tokens_consumed = 0
        cand_tokens_consumed = 0

        try:
            async with Agent(config) as agent:
                response = await agent.chat(prompt)
                async for token in response:
                    full_response += token

                meta = response.usage_metadata
                if meta:
                    total_tokens_consumed = getattr(meta, "total_token_count", 0) or 0
                    prompt_tokens_consumed = getattr(meta, "prompt_token_count", 0) or 0
                    cand_tokens_consumed = getattr(meta, "candidates_token_count", 0) or 0
        except Exception as e:
            # Fallback su gemini-3.8-flash se il modello specificato fallisce
            if self.target_model != "gemini-3.8-flash" and ("not found" in str(e).lower() or "404" in str(e)):
                logger.warning(f"[Antigravity] Fallback automatico su gemini-3.8-flash dopo errore: {e}")
                self.target_model = "gemini-3.8-flash"
                config_kwargs["model"] = "gemini-3.8-flash"
                fallback_config = LocalAgentConfig(**config_kwargs)
                async with Agent(fallback_config) as agent:
                    response = await agent.chat(prompt)
                    async for token in response:
                        full_response += token
                    meta = response.usage_metadata
                    if meta:
                        total_tokens_consumed = getattr(meta, "total_token_count", 0) or 0
            else:
                logger.error(f"[Antigravity] Errore esecuzione autonoma: {e}")
                raise e

        # Registrazione token nel ledger 4h
        if total_tokens_consumed > 0:
            self.quota_tracker.record_usage(
                total_tokens=total_tokens_consumed,
                prompt_tokens=prompt_tokens_consumed,
                candidates_tokens=cand_tokens_consumed,
                model=self.target_model
            )

        logger.info(f"[Antigravity] Generazione completata ({len(full_response)} car, {total_tokens_consumed} tokens).")

        if resume_existing:
            self.checkpoint_mgr.clear_checkpoint()

        match = re.search(r"<SKILL_CODE>(.*?)</SKILL_CODE>", full_response, re.DOTALL)
        if match:
            return match.group(1).strip()

        if "```python" in full_response:
            parts = full_response.split("```python")
            if len(parts) > 1:
                return parts[1].split("```")[0].strip()

        return full_response.strip()

    async def consult_antigravity_dialogue(
        self,
        question: str,
        context: Optional[str] = None,
        conversation_id: Optional[str] = None
    ) -> str:
        """
        Consente a Marcus di dialogare on-demand con l'Agente Antigravity per ottenere
        consulenze ingegneristiche, pareri architetturali su codice, o spiegazioni su problemi complessi.
        Rispetta il limite della quota token a 4 ore (soglia 90%).
        """
        if not self.is_available:
            return (
                "L'Agente Antigravity non è attualmente disponibile (SDK non installato o API key mancante). "
                "Posso comunque risponderti sfruttando i miei modelli interni e la documentazione locale."
            )

        # Controllo quota
        can_proceed, used_tokens, ratio = self.quota_tracker.check_quota_available(estimated_tokens=4000)
        if not can_proceed:
            return (
                f"Antigravity ha quasi raggiunto la quota di token disponibile per questa finestra di 4 ore "
                f"({ratio*100:.1f}% consumato). Per non sovraccaricare il budget, preferisco non avviare una "
                f"sessione approfondita adesso. Posso comunque analizzare il problema con la mia conoscenza locale."
            )

        dialogue_prompt = f"""Domanda da Marcus AI:
{question}
"""
        if context:
            dialogue_prompt += f"\nContesto / Codice di riferimento:\n{context}\n"

        sys_inst = (
            "Sei l'Agente Antigravity, consulente senior per Marcus AI (robot su Raspberry Pi 5). "
            "Marcus ti sta consultando su un problema architetturale, di codice o di ingegneria ROS 2. "
            "Rispondi in italiano in modo chiaro, tecnico, conciso e propositivo, rispettando i vincoli di marcus_core_rules.md "
            "e le regole di sicurezza fisica del robot. Fornisci indicazioni pratiche ed empatiche."
        )

        config_kwargs: Dict[str, Any] = {
            "api_key": self.api_key,
            "model": self.target_model,
            "workspaces": [str(self.workspace_root), "/home/robopy"],
            "system_instructions": sys_inst,
            "capabilities": CapabilitiesConfig(
                agent_behavior=AgentBehavior.INTERACTIVE,
                enable_subagents=False,
            ),
            "policies": [policy.allow_all()],
        }
        if conversation_id:
            config_kwargs["conversation_id"] = conversation_id

        config = LocalAgentConfig(**config_kwargs)
        full_response = ""
        total_tokens_consumed = 0
        prompt_tokens_consumed = 0
        cand_tokens_consumed = 0

        try:
            async with Agent(config) as agent:
                response = await agent.chat(dialogue_prompt)
                async for token in response:
                    full_response += token
                meta = response.usage_metadata
                if meta:
                    total_tokens_consumed = getattr(meta, "total_token_count", 0) or 0
                    prompt_tokens_consumed = getattr(meta, "prompt_token_count", 0) or 0
                    cand_tokens_consumed = getattr(meta, "candidates_token_count", 0) or 0
        except Exception as e:
            logger.error(f"[Antigravity Dialogue] Errore: {e}")
            return f"Ho provato a contattare Antigravity, ma si è verificato un errore di connessione: {e}"

        if total_tokens_consumed > 0:
            self.quota_tracker.record_usage(
                total_tokens=total_tokens_consumed,
                prompt_tokens=prompt_tokens_consumed,
                candidates_tokens=cand_tokens_consumed,
                model=self.target_model
            )

        return full_response.strip()

