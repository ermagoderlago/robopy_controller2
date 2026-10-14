#!/usr/bin/env python3
"""
Unit tests for Autonomous Self-Improvement (Project Autopoiesis)
===============================================================
Verifica:
1. Selezione autonoma del Tema del Giorno in CuriosityEvolutionEngine:
   - Prioritizzazione di colli di bottiglia telemetrici da MarcusDataMiner.
   - Fallback su Failure Mode a più alto RPN da dfmea.yaml.
2. Notifiche sicure (docs/evolution/daily_focus_notifications.md, NO Home Assistant).
3. Mappatura ruoli e modelli Gemini (Orchestratore: Gemini 3.1 Pro / Coder: Gemini Flash).
"""

import os
import tempfile
import datetime
import pytest
from pathlib import Path

from robopy_controller.robot_ai.services.curiosity_evolution_engine import CuriosityEvolutionEngine
from robopy_controller.robot_ai.services.marcus_data_miner import MarcusDataMiner
from robopy_controller.robot_ai.services.antigravity_agent_service import AntigravityAgentService


@pytest.fixture
def test_workspace():
    """Crea un workspace fittizio per verificare le scritture di log e FMEA."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "fmea").mkdir()
        (root / "docs" / "evolution").mkdir(parents=True)
        (root / "docs" / "ideas").mkdir(parents=True)
        (root / "docs" / "ecos").mkdir(parents=True)
        (root / "marcus_core_rules.md").write_text("# Core Rules", encoding="utf-8")

        # Mock dfmea.yaml con 2 failure modes
        sample_fmea = """
- id: FM-NAV-008
  subsystem: Nav2
  failure_mode: Jitter MPPI elevato
  recommended_action: Ottimizzare costi lookahead
  mitigation_status: OPEN
  initial_scoring:
    severity: 8
    occurrence: 7
    detection: 3
    rpn: 168
- id: FM-ACT-001
  subsystem: Actuation
  failure_mode: Stiction attrito statico
  recommended_action: Regolare motor_min_duty_cycle
  mitigation_status: OPEN
  initial_scoring:
    severity: 7
    occurrence: 4
    detection: 2
    rpn: 56
"""
        (root / "fmea" / "dfmea.yaml").write_text(sample_fmea, encoding="utf-8")
        yield root


def test_select_daily_focus_from_telemetry_bottleneck(test_workspace):
    """Verifica che un collo di bottiglia reale rilevato da DataMiner abbia precedenza assoluta."""
    engine = CuriosityEvolutionEngine(workspace_root=test_workspace)
    
    # Prepariamo un DataMiner con dati di telemetria anomali
    db_path = test_workspace / "telemetry_test.db"
    miner = MarcusDataMiner(db_path=db_path)
    miner.set_navigation_state(True)
    miner.latest_cte = 0.28  # Errore alto
    miner.sample_tick(event_tag="HIGH_CTE")
    miner.flush_to_sqlite()

    # Selezione tema
    theme = engine.select_daily_focus_theme(data_miner=miner)

    assert theme["source"] == "TELEMETRY_BOTTLENECK"
    assert theme["subsystem"] == "Nav2"
    assert theme["orchestrator_model"] == "gemini-3.1-pro"
    assert theme["coder_model"] == "gemini-3.8-flash"

    # Verifica scrittura notifica su file (NO Home Assistant)
    notif_file = test_workspace / "docs" / "evolution" / "daily_focus_notifications.md"
    assert notif_file.exists()
    content = notif_file.read_text(encoding="utf-8")
    assert "Daily Focus" in content
    assert "Nav2" in content


def test_select_daily_focus_from_fmea_fallback(test_workspace):
    """Verifica che in assenza di colli di bottiglia telemetrici venga scelto il top RPN dalla FMEA."""
    engine = CuriosityEvolutionEngine(workspace_root=test_workspace)
    
    # Nessun bottleneck attivo nel miner
    db_path = test_workspace / "telemetry_empty.db"
    miner = MarcusDataMiner(db_path=db_path)

    theme = engine.select_daily_focus_theme(data_miner=miner)

    assert theme["source"] == "FMEA_RPN"
    assert theme["failure_mode_id"] == "FM-NAV-008"
    assert "FM-NAV-008" in theme["title"]
    assert theme["orchestrator_model"] == "gemini-3.1-pro"
    assert theme["coder_model"] == "gemini-3.8-flash"


def test_antigravity_agent_model_roles(test_workspace):
    """Verifica l'allocazione corretta dei modelli Gemini per ruolo operativo."""
    service = AntigravityAgentService(workspace_root=test_workspace)
    
    # Default roles
    assert service.get_model_for_role("orchestrator") == "gemini-3.1-pro"
    assert service.get_model_for_role("architect") == "gemini-3.1-pro"
    assert service.get_model_for_role("coder") == "gemini-3.8-flash"

    # Quota status include entrambi i ruoli
    status = service.get_quota_status()
    assert status["orchestrator_model"] == "gemini-3.1-pro"
    assert status["coder_model"] == "gemini-3.8-flash"


def test_anti_duplication_excludes_recently_analyzed(test_workspace):
    """Verifica che un Failure Mode già analizzato di recente venga escluso e si passi al successivo."""
    engine = CuriosityEvolutionEngine(workspace_root=test_workspace)
    miner = MarcusDataMiner(db_path=test_workspace / "telemetry_empty.db")

    # Simuliamo che FM-NAV-008 sia stato analizzato ieri nelle notifiche
    focus_file = test_workspace / "docs" / "evolution" / "daily_focus_notifications.md"
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    focus_file.write_text(f"""
### 🎯 Daily Focus ({yesterday} - {yesterday} 03:00:00)
* **Tema:** Mitigazione FMEA [FM-NAV-008]: Jitter MPPI elevato
* **Sottosistema:** `Nav2`
* **Sorgente Decisionale:** `FMEA_RPN`
* **Failure Mode Riferimento:** `FM-NAV-008`
* **Stato Esecuzione:** `READY_FOR_USER_REVIEW`
""", encoding="utf-8")

    recently_analyzed = engine.get_recently_analyzed_failure_modes(days=7)
    assert "FM-NAV-008" in recently_analyzed

    # La selezione deve scartare FM-NAV-008 e scegliere FM-ACT-001 (il secondo aperto)
    theme = engine.select_daily_focus_theme(data_miner=miner)
    assert theme["failure_mode_id"] == "FM-ACT-001"
    assert "FM-ACT-001" in theme["title"]


def test_all_fms_analyzed_triggers_subsystem_rotation(test_workspace):
    """Verifica che se tutti i FM aperti sono stati analizzati recentemente, si attivi la rotazione curiosità."""
    engine = CuriosityEvolutionEngine(workspace_root=test_workspace)
    miner = MarcusDataMiner(db_path=test_workspace / "telemetry_empty.db")

    # Inseriamo nello storico sia FM-NAV-008 che FM-ACT-001
    focus_file = test_workspace / "docs" / "evolution" / "daily_focus_notifications.md"
    two_days_ago = (datetime.date.today() - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    focus_file.write_text(f"""
### 🎯 Daily Focus ({two_days_ago} - {two_days_ago} 03:00:00)
* **Failure Mode Riferimento:** `FM-NAV-008`

### 🎯 Daily Focus ({two_days_ago} - {two_days_ago} 03:05:00)
* **Failure Mode Riferimento:** `FM-ACT-001`
""", encoding="utf-8")

    theme = engine.select_daily_focus_theme(data_miner=miner)
    assert theme["source"] == "SUBSYSTEM_EXPLORATION"
    assert theme["failure_mode_id"] is None
    assert theme["subsystem"] in engine.subsystems


def test_detailed_plan_generation_and_storage(test_workspace):
    """Verifica la generazione fisica del file di piano dettagliato in docs/evolution/plans/."""
    engine = CuriosityEvolutionEngine(workspace_root=test_workspace)
    miner = MarcusDataMiner(db_path=test_workspace / "telemetry_empty.db")

    theme = engine.select_daily_focus_theme(data_miner=miner)
    assert "plan_file" in theme
    assert theme["plan_file"] is not None

    plan_path = test_workspace / theme["plan_file"]
    assert plan_path.exists()
    plan_content = plan_path.read_text(encoding="utf-8")
    assert "Piano di Miglioramento Evolutivo" in plan_content
    assert "Analisi Causa Radice" in plan_content
    assert "Micro-Task Pianificati" in plan_content
    assert "Piano di Collaudo" in plan_content


def test_risk_zone_bifurcation(test_workspace):
    """Verifica la classificazione di rischio (Zona Gialla/Rossa vs Verde) e lo stato."""
    engine = CuriosityEvolutionEngine(workspace_root=test_workspace)

    # 1. Tema su Nav2 / Actuation (hardware/driver) -> YELLOW / READY_FOR_USER_REVIEW
    yellow_theme = {
        "title": "Tuning PID Motori",
        "subsystem": "Actuation",
        "recommended_action": "Modifica parametri seriali ESP32",
        "failure_mode_id": "FM-ACT-001"
    }
    risk, spec, rat = engine.classify_theme_risk(yellow_theme)
    assert risk in ["YELLOW", "RED"]

    # 2. Tema su AI/Cognitive / Skills -> GREEN
    green_theme = {
        "title": "Nuova Skill Domotica HA",
        "subsystem": "AI/LangGraph",
        "recommended_action": "Sintesi skill isolata in sandbox",
        "failure_mode_id": "FM-LLM-007"
    }
    risk_g, spec_g, rat_g = engine.classify_theme_risk(green_theme)
    assert risk_g == "GREEN"


def test_token_quota_suspension_and_resumption(test_workspace, monkeypatch):
    """Verifica la sospensione per esaurimento quota/crediti e la ripresa automatica la notte successiva."""
    engine = CuriosityEvolutionEngine(workspace_root=test_workspace)

    # Creiamo un tema di test Zona Verde
    sample_fmea = """
- id: FM-LLM-010
  subsystem: AI/LangGraph
  failure_mode: Mancanza skill timer
  recommended_action: Generare skill timer
  mitigation_status: OPEN
  initial_scoring:
    severity: 7
    occurrence: 3
    detection: 2
    rpn: 42
"""
    (test_workspace / "fmea" / "dfmea.yaml").write_text(sample_fmea, encoding="utf-8")

    # 1. Simuliamo blocco quota (quota al 95% / can_start = False)
    monkeypatch.setattr(engine, "can_start_evolution_cycle", lambda: (False, "Quota 4h al 95% superata"))

    theme = engine.select_daily_focus_theme()
    assert theme["status"] == "PAUSED_TOKEN_QUOTA_AWAITING_RESUME"
    assert engine.has_pending_checkpoint() is True

    # 2. Simuliamo la notte successiva: quota ripristinata (can_start = True)
    monkeypatch.setattr(engine, "can_start_evolution_cycle", lambda: (True, "Quota 4h OK (12%)"))

    resumed_theme = engine.select_daily_focus_theme()
    assert resumed_theme["status"] in ["RESUMED_FROM_QUOTA_CHECKPOINT", "VALIDATED_IN_SANDBOX"]
    # Il checkpoint deve essere stato rimosso dopo il completamento
    assert engine.has_pending_checkpoint() is False

