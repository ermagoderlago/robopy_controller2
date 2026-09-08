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
