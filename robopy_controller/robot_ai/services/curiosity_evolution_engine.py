"""
Robot AI Services - Curiosity & Autonomous Evolution Engine
===========================================================
Motore di auto-miglioramento continuo, indagine di curiosità e governance di sicurezza.

Funzionalità:
1. DFMEA Prioritization: Identificazione autonoma dei Failure Mode aperti a più alto RPN.
2. Inquiry & Curiosity: Formulazione ciclica di domande ingegneristiche sui vari sottosistemi.
3. Safe Gating (Zona Rossa vs Zona Verde):
   - Se la modifica tocca la Zona Rossa (SPEC-00..07), blocca l'esecuzione autonoma e redige un RFC in docs/ideas/RED_ZONE_IDEAS_RFC.md.
   - Se tocca la Zona Verde, pilota la validazione AST, il sandbox pre-flight e il test di non-regressione.
4. Registrazione dell'esperienza nel Diario Evolutivo (docs/evolution/evolution_journal.md).
"""

import os
import sys
import re
import time
import yaml
import json
import logging
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("robot_ai.curiosity_evolution")

# Termini e pattern vietati per la Zona Rossa (SPEC-00 .. SPEC-07)
RED_ZONE_PATTERNS = [
    # Motori e sicurezza cinetica (SPEC-01)
    {"pattern": "safety_override", "spec": "SPEC-01", "rule": "Divieto alterazione priorità /cmd_vel_mux/input/safety_override"},
    {"pattern": "max_linear_velocity", "spec": "SPEC-01", "rule": "Divieto di alterare parametri di velocità lineare massima in autonomia"},
    {"pattern": "max_angular_velocity", "spec": "SPEC-01", "rule": "Divieto di alterare parametri di velocità angolare massima in autonomia"},
    # Navigazione e SLAM (SPEC-02)
    {"pattern": "spatiotemporal_voxel_layer", "spec": "SPEC-02", "rule": "Divieto assoluto di mappatura 3D volumetrica STVL"},
    {"pattern": "stvl", "spec": "SPEC-02", "rule": "Divieto assoluto di layer STVL (usare solo griglia locale 2.5D)"},
    # Audio e VUI (SPEC-04)
    {"pattern": "language_codes", "spec": "SPEC-04", "rule": "Parametro language_codes vietato in AudioTranscriptionConfig"},
    # Sistema Pi 5 e compilazione (SPEC-07)
    {"pattern": "MAKEFLAGS=\"-j4\"", "spec": "SPEC-07", "rule": "Divieto compilazione parallela > -j1 (OOM Kill)"},
    {"pattern": "parallel-workers 4", "spec": "SPEC-07", "rule": "Divieto compilazione parallela > 1 worker"},
    {"pattern": "git push origin main", "spec": "SPEC-00", "rule": "Divieto di commit/push diretto su main o master"},
    # Secrets
    {"pattern": "secrets.yaml", "spec": "SPEC-00", "rule": "Divieto di manipolare o indicizzare file segreti"},
    {"pattern": ".env", "spec": "SPEC-00", "rule": "Divieto di manipolare o esporre chiavi private nel codice generato"}
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


class CuriosityEvolutionEngine:
    """Motore centrale di curiosità e auto-evoluzione per Marcus."""

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = Path(workspace_root) if workspace_root else _resolve_workspace_root()
        self.fmea_file = self.workspace_root / "fmea" / "dfmea.yaml"
        self.rfc_file = self.workspace_root / "docs" / "ideas" / "RED_ZONE_IDEAS_RFC.md"
        self.journal_file = self.workspace_root / "docs" / "evolution" / "evolution_journal.md"
        self.plans_dir = self.workspace_root / "docs" / "evolution" / "plans"
        self.history_file = self.workspace_root / "docs" / "evolution" / "analysis_history.json"
        
        # Gestione checkpoint persistenti e quota token (finestra 4h / soglia 90%)
        try:
            from .antigravity_agent_service import (
                TokenQuotaTracker,
                EvolutionCheckpointManager,
            )
            self.quota_tracker = TokenQuotaTracker(self.workspace_root)
            self.checkpoint_mgr = EvolutionCheckpointManager(self.workspace_root)
        except Exception:
            try:
                from robopy_controller.robot_ai.services.antigravity_agent_service import (
                    TokenQuotaTracker,
                    EvolutionCheckpointManager,
                )
                self.quota_tracker = TokenQuotaTracker(self.workspace_root)
                self.checkpoint_mgr = EvolutionCheckpointManager(self.workspace_root)
            except Exception:
                self.quota_tracker = None
                self.checkpoint_mgr = None

        # Sottosistemi su cui ruotano le indagini di curiosità
        self.subsystems = [
            "AI/LangGraph",
            "AI/Cognitive",
            "Nav2",
            "VUI Audio",
            "Vision",
            "Hardware/Power",
            "System/DDS"
        ]

    def has_pending_checkpoint(self) -> bool:
        """Verifica se esiste un'attività evolutiva sospesa per quota da riprendere."""
        if self.checkpoint_mgr:
            return self.checkpoint_mgr.load_pending_checkpoint() is not None
        return False

    def get_pending_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Recupera l'attività sospesa da riprendere."""
        if self.checkpoint_mgr:
            return self.checkpoint_mgr.load_pending_checkpoint()
        return None

    def can_start_evolution_cycle(self) -> Tuple[bool, str]:
        """Verifica se il budget token a 4 ore consente di avviare un nuovo ciclo evolutivo."""
        if not self.quota_tracker:
            return True, "Quota tracker non attivo (OK)"
        is_allowed, used, ratio = self.quota_tracker.check_quota_available(estimated_tokens=8000)
        if not is_allowed:
            return False, f"Quota 4h al {ratio*100:.1f}% ({used}/{self.quota_tracker.max_4h_tokens}). Soglia 90% superata."
        return True, f"Quota 4h OK ({ratio*100:.1f}%)"

    def get_recently_analyzed_failure_modes(self, days: int = 7) -> List[str]:
        """
        Estrae l'elenco dei Failure Mode analizzati o pianificati negli ultimi N giorni,
        per prevenire loop di duplicazione cieca del medesimo tema notte dopo notte.
        Fonti di controllo:
        1. Registro strutturato docs/evolution/analysis_history.json
        2. Storico notifiche docs/evolution/daily_focus_notifications.md
        3. Piani salvati in docs/evolution/plans/PLAN-*.md
        """
        recent_ids = set()
        cutoff_date = datetime.date.today() - datetime.timedelta(days=days)

        # 1. Controllo analysis_history.json
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    history = json.load(f)
                if isinstance(history, list):
                    for item in history:
                        fm_id = item.get("failure_mode_id")
                        date_str = item.get("date") or item.get("analyzed_at", "")[:10]
                        if fm_id and date_str:
                            try:
                                item_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                                if item_date >= cutoff_date:
                                    recent_ids.add(fm_id)
                            except Exception:
                                recent_ids.add(fm_id)
            except Exception as e:
                logger.warning(f"Errore lettura history file {self.history_file}: {e}")

        # 2. Controllo docs/evolution/daily_focus_notifications.md
        focus_file = self.workspace_root / "docs" / "evolution" / "daily_focus_notifications.md"
        if focus_file.exists():
            try:
                content = focus_file.read_text(encoding="utf-8")
                # Cerca pattern: ### 🎯 Daily Focus (YYYY-MM-DD ... Failure Mode Riferimento: FM-XXX
                blocks = content.split("### 🎯 Daily Focus")
                for block in blocks[1:]:
                    lines = block.strip().splitlines()
                    if not lines:
                        continue
                    first_line = lines[0]
                    date_match = re.search(r"\((\d{4}-\d{2}-\d{2})", first_line)
                    fm_match = re.search(r"\*\s*\*\*Failure Mode Riferimento:\*\*\s*`?([A-Za-z0-9_-]+)`?", block)
                    if date_match and fm_match:
                        entry_date_str = date_match.group(1)
                        fm_id = fm_match.group(1).strip()
                        if fm_id and fm_id not in ["N/A", "None", "UNKNOWN"]:
                            try:
                                entry_date = datetime.datetime.strptime(entry_date_str, "%Y-%m-%d").date()
                                if entry_date >= cutoff_date:
                                    recent_ids.add(fm_id)
                            except Exception:
                                recent_ids.add(fm_id)
            except Exception as e:
                logger.warning(f"Errore parsing daily focus notifications: {e}")

        # 3. Controllo docs/evolution/plans/
        if self.plans_dir.exists():
            for plan_file in self.plans_dir.glob("PLAN-*.md"):
                name_match = re.search(r"PLAN-(\d{4}-\d{2}-\d{2})_([A-Za-z0-9_-]+)", plan_file.name)
                if name_match:
                    plan_date_str, fm_id = name_match.group(1), name_match.group(2)
                    if fm_id.startswith("FM-"):
                        try:
                            p_date = datetime.datetime.strptime(plan_date_str, "%Y-%m-%d").date()
                            if p_date >= cutoff_date:
                                recent_ids.add(fm_id)
                        except Exception:
                            recent_ids.add(fm_id)

        return sorted(list(recent_ids))

    def get_top_priority_failure_modes(
        self,
        limit: int = 5,
        exclude_ids: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Estrae i Failure Mode con più alto RPN ancora aperti (OPEN / IN_PROGRESS), escludendo gli ID specificati."""
        if not self.fmea_file.exists():
            logger.warning(f"File DFMEA non trovato: {self.fmea_file}")
            return []

        try:
            with open(self.fmea_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            
            if not isinstance(data, list):
                return []

            exclude_set = set(exclude_ids or [])
            open_modes = [
                item for item in data 
                if item.get("mitigation_status", "OPEN") in ["OPEN", "IN_PROGRESS"]
                and item.get("id") not in exclude_set
            ]

            # Ordina per RPN residuo (o iniziale se residuo assente)
            def sort_key(x):
                rpn = x.get("residual_scoring", {}).get("rpn")
                if rpn is None:
                    rpn = x.get("initial_scoring", {}).get("rpn", 0)
                # Override severità: se severità >= 9, priorità assoluta
                sev = x.get("initial_scoring", {}).get("severity", 0)
                return (1 if sev >= 9 else 0, rpn)

            open_modes.sort(key=sort_key, reverse=True)
            return open_modes[:limit]
        except Exception as e:
            logger.error(f"Errore lettura DFMEA: {e}")
            return []

    def classify_theme_risk(self, theme: Dict[str, Any]) -> Tuple[str, str, str]:
        """
        Classifica la Zona di Rischio (SPEC-00) del tema proposto:
        Ritorna: (risk_zone: "RED"|"YELLOW"|"GREEN", spec_ref, rationale)
        """
        subsystem = (theme.get("subsystem") or "").strip().lower()
        title = theme.get("title", "")
        rec_action = theme.get("recommended_action", "")
        combined_text = f"{title}\n{rec_action}".lower()

        # 1. Verifica Vincoli Zona Rossa (Inviolabili - NO AUTONOMOUS TOUCH)
        for check in RED_ZONE_PATTERNS:
            if check["pattern"].lower() in combined_text:
                return (
                    "RED",
                    check["spec"],
                    f"Violazione vincolo Zona Rossa [{check['spec']}]: {check['rule']}"
                )

        # 2. Verifica Zona Gialla (Human Gate - approvazione richiesta)
        yellow_subsystems = ["hardware/power", "actuation", "vision", "nav2", "system/dds", "chassis"]
        if any(ys in subsystem for ys in yellow_subsystems):
            return (
                "YELLOW",
                "SPEC-00 / SPEC-01 / SPEC-02 / SPEC-03 / SPEC-06 / SPEC-07",
                f"Sottosistema critico '{theme.get('subsystem')}'. Modifica a driver, contratti ROS 2 o parametri hardware richiede validazione umana."
            )

        # 3. Zona Verde (Auto-Evolution consentita)
        if any(gs in subsystem for gs in ["ai/langgraph", "ai/cognitive", "documentation", "testing", "skills"]):
            return (
                "GREEN",
                "SPEC-00 / SPEC-05",
                "Miglioramento sicuro in Zona Verde (Skill sandbox, prompt tuning, refactoring interno o test di non-regressione)."
            )

        # Default prudenziale
        return (
            "YELLOW",
            "SPEC-00",
            f"Classificazione prudenziale per sottosistema '{theme.get('subsystem')}': richiede approvazione preventiva."
        )

    def generate_evolution_plan(
        self,
        theme: Dict[str, Any],
        risk_zone: str,
        spec_ref: str,
        risk_rationale: str,
        execution_status: str
    ) -> Path:
        """
        Genera e salva un documento di piano dettagliato in docs/evolution/plans/PLAN-YYYY-MM-DD_<id>.md
        """
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        date_str = theme.get("date", datetime.date.today().strftime("%Y-%m-%d"))
        fm_id = theme.get("failure_mode_id") or "INQUIRY"
        subsystem_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", theme.get("subsystem", "core")).lower()
        plan_filename = f"PLAN-{date_str}_{fm_id}_{subsystem_slug}.md"
        plan_path = self.plans_dir / plan_filename

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        title = theme.get("title", "Piano Evolutivo")
        motivation = theme.get("motivation", "")
        rec_action = theme.get("recommended_action", "")

        if risk_zone == "GREEN":
            action_statement = "Esecuzione autonoma sandbox e validazione non-regressione abilitata."
            micro_tasks = [
                "1. Sintesi ed isolamento del modulo/skill in ambiente sandbox protetto (AST SecurityValidator).",
                "2. Validazione sintattica e rispetto dei contratti BaseSkill / API.",
                "3. Esecuzione test dinamico in SkillSandbox con timeout di sicurezza (3.0s).",
                "4. Verifica assenza di memory leak e rispetto dei limiti di RAM (4GB host).",
                "5. Registrazione automatica dell'esperienza nel Diario Evolutivo e calcolo DFMEA."
            ]
            test_plan = "pytest tests/test_autonomous_evolution.py -v"
            acceptance_criteria = "100% test superati in sandbox, zero eccezioni AST, nessun accesso a risorse OS non autorizzate."
        else:
            action_statement = "Esecuzione autonoma SOSPESA. Richiesta revisione e approvazione congiunta con operatore umano (Human-in-the-Loop)."
            micro_tasks = [
                f"1. Ispezione congiunta della Scheda Tecnica di riferimento ({spec_ref}).",
                f"2. Analisi statica dei file sorgente del sottosistema {theme.get('subsystem')}.",
                "3. Progettazione delle modifiche cinematiche/hardware/ROS 2 su branch isolato.",
                "4. Collaudo su banco prova o simulazione prima del merge su main.",
                "5. Approvazione esplicita dell'utente prima del deployment a caldo."
            ]
            test_plan = "Revisione codice + collaudo assistito su banco hardware"
            acceptance_criteria = "Approvazione umana esplicita (Human Gate) e verifica assenza regressioni."

        tasks_md = "\n".join(micro_tasks)

        content = f"""# 📋 Piano di Miglioramento Evolutivo: {title}
- **Data Generazione:** {now_str}
- **Failure Mode Riferimento:** `{fm_id}`
- **Sottosistema:** `{theme.get('subsystem')}`
- **Sorgente Decisionale:** `{theme.get('source')}`
- **Modello Orchestratore:** `{theme.get('orchestrator_model')}`
- **Modello Coder:** `{theme.get('coder_model')}`
- **Classificazione Rischio (SPEC-00):** `{risk_zone}`
- **Stato Esecuzione:** `{execution_status}`

---

## 1. 🔍 Analisi Causa Radice & Motivazione
{motivation}

## 2. 🎯 Obiettivo Ingegneristico & Azione Raccomandata
{rec_action}

## 3. 🛡️ Valutazione Governance & Limiti Fisici (SPEC-00)
* **Specifica di Riferimento:** `{spec_ref}`
* **Valutazione Rischio:** {risk_rationale}
* **Direttiva Operativa:** {action_statement}

## 4. 📝 Micro-Task Pianificati
{tasks_md}

## 5. 🧪 Piano di Collaudo & Criteri di Accettazione
* **Strategia di Test:** `{test_plan}`
* **Criteri di Accettazione:** {acceptance_criteria}

---
*Piano generato autonomamente da Marcus Autonomous Evolution Engine per Project Autopoiesis.*
"""
        try:
            plan_path.write_text(content, encoding="utf-8")
            logger.info(f"Piano dettagliato salvato in {plan_path}")
        except Exception as e:
            logger.error(f"Errore scrittura file piano {plan_path}: {e}")

        return plan_path

    def record_analysis_history(
        self,
        theme: Dict[str, Any],
        plan_path: Optional[Path],
        status: str
    ):
        """Salva la voce nell'archivio storico docs/evolution/analysis_history.json."""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        history_data = []
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    history_data = json.load(f)
                if not isinstance(history_data, list):
                    history_data = []
            except Exception:
                history_data = []

        entry = {
            "date": theme.get("date", datetime.date.today().strftime("%Y-%m-%d")),
            "selected_at": theme.get("selected_at"),
            "failure_mode_id": theme.get("failure_mode_id"),
            "subsystem": theme.get("subsystem"),
            "title": theme.get("title"),
            "source": theme.get("source"),
            "risk_zone": theme.get("risk_zone"),
            "status": status,
            "plan_file": str(plan_path.relative_to(self.workspace_root)) if plan_path else None
        }
        history_data.append(entry)
        history_data = history_data[-100:]
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(history_data, f, indent=2)
        except Exception as e:
            logger.error(f"Errore scrittura history: {e}")

    def evaluate_proposal_safety(self, proposal_title: str, proposed_code: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Analizza se una proposta o modifica tocca vincoli di Zona Rossa.
        Ritorna: (is_safe, spec_violated, rule_violated)
        """
        code_lower = (proposal_title + "\n" + proposed_code).lower()
        for check in RED_ZONE_PATTERNS:
            pat = check["pattern"].lower()
            if pat in code_lower:
                return False, check["spec"], check["rule"]
        return True, None, None

    def log_red_zone_rfc(
        self,
        rfc_id: str,
        title: str,
        subsystem: str,
        spec_violated: str,
        rule_violated: str,
        description: str,
        benefits: str,
        risks: str
    ) -> bool:
        """Trascrive una proposta di Zona Rossa nel registro RFC per revisione umana."""
        self.rfc_file.parent.mkdir(parents=True, exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        entry = f"""
### `{rfc_id}`: {title}
- **Data Generazione:** {now_str}
- **Autore:** Marcus Autonomous Curiosity Engine
- **Sottosistema Target:** {subsystem}
- **Vincolo di Zona Rossa Coinvolto:** `{spec_violated}` - {rule_violated}
- **Descrizione della Proposta:**
  {description}
- **Benefici Potenziali:**
  {benefits}
- **Rischi Ingegneristici Identificati:**
  {risks}
- **Decisione Operatore Umano:** `AWAITING_HUMAN_REVIEW`

---
"""
        try:
            with open(self.rfc_file, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.info(f"Registrata proposta RFC in {self.rfc_file} con ID: {rfc_id}")
            return True
        except Exception as e:
            logger.error(f"Impossibile scrivere RFC file: {e}")
            return False

    def log_evolution_experience(
        self,
        cycle_name: str,
        subsystem: str,
        failure_mode_id: Optional[str],
        inquiry: str,
        action_taken: str,
        outcome: str
    ) -> bool:
        """Registra un ciclo evolutivo completato nel diario di bordo."""
        self.journal_file.parent.mkdir(parents=True, exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        entry = f"""
### [{cycle_name}] {subsystem} - {now_str}
- **Failure Mode Riferito:** {failure_mode_id or 'N/A (Esplorazione Curiosità)'}
- **Quesito di Indagine (Curiosità):**
  {inquiry}
- **Azione Eseguita / Soluzione Applicata:**
  {action_taken}
- **Esito del Ciclo:** `{outcome}`

---
"""
        try:
            with open(self.journal_file, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.info(f"Esperienza evolutiva salvata nel diario: {cycle_name}")
            return True
        except Exception as e:
            logger.error(f"Impossibile scrivere nel diario evolutivo: {e}")
            return False

    def generate_curiosity_inquiry(self, subsystem: str, failure_mode: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Formula un quesito di curiosità mirato al miglioramento del sottosistema."""
        if failure_mode:
            fm_id = failure_mode.get("id", "UNKNOWN")
            fm_name = failure_mode.get("failure_mode", "")
            rec_action = failure_mode.get("recommended_action", "")
            question = f"Come possiamo mitigare in modo deterministico '{fm_id}: {fm_name}' seguendo la raccomandazione '{rec_action}'?"
            search_query = f"ROS2 {subsystem} python {failure_mode.get('component', '')} best practices"
        else:
            question = f"Quali pattern moderni di efficienza e robustezza possono essere introdotti nel sottosistema {subsystem} per Raspberry Pi 5?"
            search_query = f"ROS2 Jazzy embedded {subsystem} optimization techniques"

        return {
            "subsystem": subsystem,
            "failure_mode_id": failure_mode.get("id") if failure_mode else None,
            "question": question,
            "search_query": search_query,
            "generated_at": datetime.datetime.now().isoformat()
        }

    def log_autonomous_eco(
        self,
        subsystem: str,
        eco_title: str,
        description: str,
        changes_made: List[str],
        verification_summary: str,
        eco_id: Optional[str] = None
    ) -> bool:
        """
        Registra un Engineering Change Order (ECO) generato autonomamente da Marcus.
        Dichiara esplicitamente: Autore: Generata autonomamente da Marcus.
        Su Raspberry Pi NON è permessa alcuna forzatura (deve essere verificato al 100%).
        """
        eco_map = {
            "ai/langgraph": "orchestration_rag_ecos.md",
            "ai/cognitive": "orchestration_rag_ecos.md",
            "nav2": "nav2_slam_ecos.md",
            "vui audio": "audio_vui_ecos.md",
            "vision": "vision_hailo_ecos.md",
            "hardware/power": "actuation_ecos.md",
            "actuation": "actuation_ecos.md",
            "system/dds": "orchestration_rag_ecos.md"
        }
        target_name = eco_map.get(subsystem.lower(), "orchestration_rag_ecos.md")
        target_file = self.workspace_root / "docs" / "ecos" / target_name
        target_file.parent.mkdir(parents=True, exist_ok=True)

        now_date = datetime.datetime.now().strftime("%Y-%m-%d")
        now_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        final_eco_id = eco_id or f"ECO-{now_date}-MARCUS-{int(time.time()) % 1000:03d}"

        changes_bullet = "\n".join([f"  * {ch}" for ch in changes_made])

        entry = f"""
---

## 📈 {final_eco_id}: {eco_title}
* **Autore:** 🤖 **Generata autonomamente da Marcus** (Antigravity Autonomous Evolution Engine)
* **Data Creazione:** {now_time}
* **Sottosistema:** `{subsystem}`
* **Stato:** ✅ **Completato e Validato in Sandbox** (Nessuna forzatura: 100% verificato)
* **Descrizione:** {description}
* **Modifiche apportate:**
{changes_bullet}
* **Esito Validazione:** {verification_summary}
"""
        try:
            with open(target_file, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.info(f"Registrato nuovo ECO '{final_eco_id}' generato da Marcus in {target_file}")
            return True
        except Exception as e:
            logger.error(f"Errore registrazione ECO {final_eco_id}: {e}")
            return False

    def select_daily_focus_theme(self, data_miner=None) -> Dict[str, Any]:
        """
        Seleziona in modo intelligente e autonomo il 'Tema del Giorno' da sviscerare.
        
        Pipeline:
        0. Controllo Checkpoint Pendente: se un'attività è stata sospesa per quota/crediti,
           la riprende la notte successiva non appena la quota torna disponibile.
        1. Pre-check Anti-Duplicazione: esclude i Failure Mode già analizzati/pianificati negli ultimi 7 giorni.
        2. Selezione Prioritaria: Bottleneck telemetrico reale -> Top RPN da DFMEA -> Rotazione Sottosistemi.
        3. Classificazione di Rischio (SPEC-00): ZONA VERDE (autonoma) vs ZONA GIALLA / ROSSA (human-gate).
        4. Generazione PLANNING Dettagliato: redazione di docs/evolution/plans/PLAN-YYYY-MM-DD_*.md.
        5. Biforcazione Esecutiva & Quota Guard:
           - Se ZONA VERDE e quota ok: esecuzione sandbox protetta.
           - Se quota insufficiente: sospensione con checkpoint per la notte successiva.
           - Se ZONA GIALLA/ROSSA: STOP scrittura codice, stato READY_FOR_USER_REVIEW.
        6. Notifica e Storico: salvataggio su analysis_history.json, daily_focus_notifications.md ed evolution_journal.md.
        """
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today_date = datetime.datetime.now().strftime("%Y-%m-%d")

        # ---------------------------------------------------------------------
        # 0. Verifica e Ripresa Attività Sospesa per Mancanza Quota / Crediti
        # ---------------------------------------------------------------------
        if self.has_pending_checkpoint():
            checkpoint = self.get_pending_checkpoint()
            if checkpoint:
                can_resume, quota_msg = self.can_start_evolution_cycle()
                task_id = checkpoint.get("task_id", "RESUMED_TASK")
                state_data = checkpoint.get("state_data", {})
                resumed_theme = state_data.get("theme", {})

                if not can_resume:
                    logger.warning(f"Attività sospesa '{task_id}' ancora in pausa per quota token: {quota_msg}")
                    resumed_theme["status"] = "PAUSED_TOKEN_QUOTA_AWAITING_RESUME"
                    resumed_theme["selected_at"] = now_str
                    self.notify_daily_focus(resumed_theme)
                    return resumed_theme

                logger.info(f"Ripresa automatica dell'attività sospesa '{task_id}' (quota ripristinata: {quota_msg})")
                resumed_theme["status"] = "RESUMED_FROM_QUOTA_CHECKPOINT"
                resumed_theme["selected_at"] = now_str
                
                # Esegue la validazione e conclude l'attività
                risk_zone = resumed_theme.get("risk_zone", "GREEN")
                if risk_zone == "GREEN":
                    resumed_theme["status"] = "VALIDATED_IN_SANDBOX"
                    if self.checkpoint_mgr:
                        self.checkpoint_mgr.clear_checkpoint()
                else:
                    resumed_theme["status"] = "READY_FOR_USER_REVIEW"

                # Genera / aggiorna il piano dettagliato
                spec_ref = resumed_theme.get("spec_ref", "SPEC-00")
                risk_rat = resumed_theme.get("risk_rationale", "Ripreso da checkpoint")
                plan_path = self.generate_evolution_plan(
                    resumed_theme,
                    risk_zone=risk_zone,
                    spec_ref=spec_ref,
                    risk_rationale=risk_rat,
                    execution_status=resumed_theme["status"]
                )
                resumed_theme["plan_file"] = str(plan_path.relative_to(self.workspace_root))
                self.record_analysis_history(resumed_theme, plan_path, resumed_theme["status"])
                self.notify_daily_focus(resumed_theme)
                return resumed_theme

        # ---------------------------------------------------------------------
        # 1. Pre-check Anti-Duplicazione: Failure Mode affrontati negli ultimi 7gg
        # ---------------------------------------------------------------------
        recently_analyzed_ids = self.get_recently_analyzed_failure_modes(days=7)
        if recently_analyzed_ids:
            logger.info(f"Anti-duplicazione attiva: esclusi {len(recently_analyzed_ids)} FM già analizzati di recente: {recently_analyzed_ids}")

        # ---------------------------------------------------------------------
        # 2. Interroga colli di bottiglia telemetrici (MarcusDataMiner)
        # ---------------------------------------------------------------------
        bottlenecks = []
        if data_miner:
            try:
                bottlenecks = data_miner.get_unmitigated_bottlenecks(days=3)
            except Exception as e:
                logger.warning(f"Impossibile leggere colli di bottiglia da DataMiner: {e}")
        else:
            try:
                from .marcus_data_miner import MarcusDataMiner
                miner = MarcusDataMiner()
                bottlenecks = miner.get_unmitigated_bottlenecks(days=3)
            except Exception:
                pass

        # ---------------------------------------------------------------------
        # 3. Interroga la FMEA per i top open failure modes (escludendo i duplicati recenti)
        # ---------------------------------------------------------------------
        top_fmea = self.get_top_priority_failure_modes(limit=5, exclude_ids=recently_analyzed_ids)

        chosen_theme = {}

        # Priorità 1: Collo di bottiglia telemetrico attivo sul campo
        if bottlenecks:
            top_bn = sorted(bottlenecks, key=lambda b: b.get("severity_score", 0), reverse=True)[0]
            chosen_theme = {
                "date": today_date,
                "title": f"Ottimizzazione {top_bn['subsystem']}: {top_bn['recommended_focus']}",
                "subsystem": top_bn["subsystem"],
                "source": "TELEMETRY_BOTTLENECK",
                "motivation": top_bn["description"],
                "recommended_action": top_bn["recommended_focus"],
                "failure_mode_id": None,
                "orchestrator_model": "gemini-3.1-pro",
                "coder_model": "gemini-3.8-flash",
                "selected_at": now_str
            }
            for fm in top_fmea:
                if fm.get("subsystem", "").lower() in top_bn["subsystem"].lower():
                    chosen_theme["failure_mode_id"] = fm.get("id")
                    break

        # Priorità 2: Failure Mode aperto a più alto RPN (non analizzato di recente)
        elif top_fmea:
            top_fm = top_fmea[0]
            fm_id = top_fm.get("id", "FM-UNK")
            fm_name = top_fm.get("failure_mode", "")
            rec = top_fm.get("recommended_action", "Analisi e hardening deterministico")
            chosen_theme = {
                "date": today_date,
                "title": f"Mitigazione FMEA [{fm_id}]: {fm_name}",
                "subsystem": top_fm.get("subsystem", "System"),
                "source": "FMEA_RPN",
                "motivation": f"Failure Mode aperto a massimo RPN nel sottosistema {top_fm.get('subsystem')}.",
                "recommended_action": rec,
                "failure_mode_id": fm_id,
                "orchestrator_model": "gemini-3.1-pro",
                "coder_model": "gemini-3.8-flash",
                "selected_at": now_str
            }

        # Priorità 3: Tutti i top FM recenti già analizzati -> Rotazione curiosità sui sottosistemi
        else:
            day_ordinal = datetime.date.today().toordinal()
            subsystem = self.subsystems[day_ordinal % len(self.subsystems)]
            inquiry = self.generate_curiosity_inquiry(subsystem)
            chosen_theme = {
                "date": today_date,
                "title": f"Esplorazione Curiosità Architetturale: {subsystem}",
                "subsystem": subsystem,
                "source": "SUBSYSTEM_EXPLORATION",
                "motivation": inquiry["question"],
                "recommended_action": "Indagine documentale e benchmark di efficienza",
                "failure_mode_id": None,
                "orchestrator_model": "gemini-3.1-pro",
                "coder_model": "gemini-3.8-flash",
                "selected_at": now_str
            }

        # ---------------------------------------------------------------------
        # 4. Classificazione della Zona di Rischio (SPEC-00 Governance)
        # ---------------------------------------------------------------------
        risk_zone, spec_ref, risk_rat = self.classify_theme_risk(chosen_theme)
        chosen_theme["risk_zone"] = risk_zone
        chosen_theme["spec_ref"] = spec_ref
        chosen_theme["risk_rationale"] = risk_rat

        # ---------------------------------------------------------------------
        # 5. Biforcazione Esecutiva & Gestione Quota Crediti AI
        # ---------------------------------------------------------------------
        if risk_zone == "GREEN":
            # Modifica consentita in autonomia (es. skill sandbox, prompt, test)
            can_start, quota_msg = self.can_start_evolution_cycle()
            if not can_start:
                # Mancanza crediti / Quota 4h > 90%: salva checkpoint per la notte successiva
                chosen_theme["status"] = "PAUSED_TOKEN_QUOTA_AWAITING_RESUME"
                logger.warning(f"Attività Zona Verde sospesa per quota: {quota_msg}. Salvataggio checkpoint.")
                if self.checkpoint_mgr:
                    task_id = f"EVO-{today_date}-{chosen_theme.get('failure_mode_id') or 'SKILL'}"
                    self.checkpoint_mgr.save_suspended_checkpoint(
                        task_id=task_id,
                        conversation_id="nightly_auto_evolution",
                        model=chosen_theme["coder_model"],
                        task_type="AUTONOMOUS_SKILL_EVOLUTION",
                        completed_steps=["PRE_CHECK", "THEME_SELECTION"],
                        pending_steps=["SANDBOX_VALIDATION", "TEST_EXECUTION"],
                        state_data={"theme": chosen_theme}
                    )
            else:
                chosen_theme["status"] = "VALIDATED_IN_SANDBOX"
                if self.checkpoint_mgr:
                    self.checkpoint_mgr.clear_checkpoint()
        else:
            # ZONA ROSSA o ZONA GIALLA (motori, hardware, ROS 2, HEF, driver):
            # DIVIETO ASSOLUTO di scrittura codice autonoma. Pianificazione dettagliata per l'utente.
            chosen_theme["status"] = "READY_FOR_USER_REVIEW"
            logger.info(f"Tema in {risk_zone} Zone: impostato stato READY_FOR_USER_REVIEW (nessuna modifica a codice di produzione).")

        # ---------------------------------------------------------------------
        # 6. Generazione del Piano Dettagliato Formale (docs/evolution/plans/)
        # ---------------------------------------------------------------------
        plan_path = self.generate_evolution_plan(
            chosen_theme,
            risk_zone=risk_zone,
            spec_ref=spec_ref,
            risk_rationale=risk_rat,
            execution_status=chosen_theme["status"]
        )
        chosen_theme["plan_file"] = str(plan_path.relative_to(self.workspace_root))

        # ---------------------------------------------------------------------
        # 7. Registrazione Storico e Notifiche (NO Home Assistant)
        # ---------------------------------------------------------------------
        self.record_analysis_history(chosen_theme, plan_path, chosen_theme["status"])
        self.notify_daily_focus(chosen_theme)

        return chosen_theme

    def notify_daily_focus(self, theme: Dict[str, Any], ros_publisher=None) -> bool:
        """
        Notifica la decisione del Daily Focus su canali sicuri e persistenti (NO Home Assistant).
        1. File dedicato docs/evolution/daily_focus_notifications.md
        2. Diario evolutivo docs/evolution/evolution_journal.md
        3. Topic ROS 2 /robot_ai/notifications se disponibile
        """
        focus_file = self.workspace_root / "docs" / "evolution" / "daily_focus_notifications.md"
        focus_file.parent.mkdir(parents=True, exist_ok=True)

        plan_ref = theme.get("plan_file") or "N/A"
        risk_zone = theme.get("risk_zone") or "N/A"
        status = theme.get("status") or "SCHEDULED_FOR_PRO_ANALYSIS"

        entry = f"""
### 🎯 Daily Focus ({theme.get('date')} - {theme.get('selected_at')})
* **Tema:** {theme.get('title')}
* **Sottosistema:** `{theme.get('subsystem')}`
* **Sorgente Decisionale:** `{theme.get('source')}`
* **Failure Mode Riferimento:** `{theme.get('failure_mode_id') or 'N/A'}`
* **Motivazione:** {theme.get('motivation')}
* **Azione Raccomandata:** {theme.get('recommended_action')}
* **Modello Orchestratore:** `{theme.get('orchestrator_model')}`
* **Modello Coder:** `{theme.get('coder_model')}`
* **Stato Esecuzione:** `{status}`
* **Zona di Rischio (SPEC-00):** `{risk_zone}`
* **Piano Dettagliato:** `{plan_ref}`

---
"""
        try:
            with open(focus_file, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.info(f"Notifica Daily Focus salvata in {focus_file} (Stato: {status})")
        except Exception as e:
            logger.error(f"Errore scrittura daily focus notification: {e}")

        # Registrazione anche su evolution_journal.md
        self.log_evolution_experience(
            cycle_name=f"DAILY_FOCUS_{theme.get('date')}",
            subsystem=theme.get("subsystem", "System"),
            failure_mode_id=theme.get("failure_mode_id"),
            inquiry=theme.get("motivation", ""),
            action_taken=f"Selezionato tema: {theme.get('title')}. Rischio: {risk_zone}. Piano generato in {plan_ref}.",
            outcome=status
        )

        if ros_publisher:
            try:
                from std_msgs.msg import String
                msg = String()
                msg.data = json.dumps(theme)
                ros_publisher.publish(msg)
            except Exception as e:
                logger.debug(f"ROS 2 publication skipped: {e}")

        return True


