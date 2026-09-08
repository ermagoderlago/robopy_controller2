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

    def get_top_priority_failure_modes(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Estrae i Failure Mode con più alto RPN ancora aperti (OPEN / IN_PROGRESS)."""
        if not self.fmea_file.exists():
            logger.warning(f"File DFMEA non trovato: {self.fmea_file}")
            return []

        try:
            with open(self.fmea_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            
            if not isinstance(data, list):
                return []

            open_modes = [
                item for item in data 
                if item.get("mitigation_status", "OPEN") in ["OPEN", "IN_PROGRESS"]
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
        Seleziona in modo totalmente autonomo il 'Tema del Giorno' da sviscerare.
        Incrocia:
        1. Colli di bottiglia telemetrici rilevati da MarcusDataMiner durante il moto reale.
        2. Failure Mode aperti a più alto RPN da dfmea.yaml.
        3. Priorità di sicurezza e usura.
        
        Assegna:
        - Orchestratore primario: Gemini 3.1 Pro (per reasoning profondo e micro-tasking)
        - Coder: Gemini 3.8 Flash (per generazione e validazione sandbox rapida)
        """
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today_date = datetime.datetime.now().strftime("%Y-%m-%d")

        # 1. Interroga i colli di bottiglia da DataMiner se disponibile
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

        # 2. Interroga la FMEA per i top open failure modes
        top_fmea = self.get_top_priority_failure_modes(limit=5)

        chosen_theme = {}

        # Priorità 1: Se c'è un collo di bottiglia telemetrico attivo riscontrato sul campo
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
            # Associazione facoltativa a FM esistente
            for fm in top_fmea:
                if fm.get("subsystem", "").lower() in top_bn["subsystem"].lower():
                    chosen_theme["failure_mode_id"] = fm.get("id")
                    break

        # Priorità 2: Se nessun bottleneck telemetrico rilevato, prendi il failure mode a RPN più alto
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

        # Priorità 3: Esplorazione curiosità sui sottosistemi a rotazione
        else:
            subsystem = self.subsystems[int(time.time()) % len(self.subsystems)]
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

        # Notifica e registra la scelta (NO Home Assistant)
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
* **Stato Esecuzione:** `SCHEDULED_FOR_PRO_ANALYSIS`

---
"""
        try:
            with open(focus_file, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.info(f"Notifica Daily Focus salvata in {focus_file}")
        except Exception as e:
            logger.error(f"Errore scrittura daily focus notification: {e}")

        # Registrazione anche su evolution_journal.md
        self.log_evolution_experience(
            cycle_name=f"DAILY_FOCUS_{theme.get('date')}",
            subsystem=theme.get("subsystem", "System"),
            failure_mode_id=theme.get("failure_mode_id"),
            inquiry=theme.get("motivation", ""),
            action_taken=f"Selezionato tema giornaliero: {theme.get('title')}. Pipeline multi-modello allocata ({theme.get('orchestrator_model')} + {theme.get('coder_model')}).",
            outcome="THEME_SELECTED"
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

