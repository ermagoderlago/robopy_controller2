"""
Test Suite: Antigravity Autonomous Evolution & Safety Gating
============================================================
Collaudo end-to-end delle capacità di auto-evoluzione, prioritizzazione DFMEA,
curiosità, safe gating Zona Rossa (RFC) e salvaguardia sincronizzazione.
"""

import os
import sys
import pytest
import importlib.util
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))


def load_module_from_path(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Caricamento isolato dei moduli senza dipendenze ROS 2 middleware
engine_path = WORKSPACE_ROOT / "robopy_controller" / "robot_ai" / "services" / "curiosity_evolution_engine.py"
curiosity_mod = load_module_from_path("curiosity_evolution_engine", engine_path)
CuriosityEvolutionEngine = curiosity_mod.CuriosityEvolutionEngine

validator_path = WORKSPACE_ROOT / "robopy_controller" / "robot_ai" / "skills" / "security_validator.py"
validator_mod = load_module_from_path("security_validator", validator_path)
SecurityValidator = validator_mod.SecurityValidator


@pytest.fixture
def curiosity_engine():
    return CuriosityEvolutionEngine(workspace_root=WORKSPACE_ROOT)


def test_dfmea_top_rpn_prioritization(curiosity_engine):
    """Test 1: Verifica che il motore estragga correttamente i Failure Mode a più alto RPN."""
    top_fms = curiosity_engine.get_top_priority_failure_modes(limit=5)
    assert len(top_fms) > 0, "Nessun failure mode estratto da dfmea.yaml"
    
    # Verifica che tra i primi ci sia un failure ad alta severità (S >= 9)
    first_fm = top_fms[0]
    sev = first_fm.get("initial_scoring", {}).get("severity", 0)
    assert sev >= 9, f"Il primo failure mode dovrebbe avere Severità >= 9 (trovato S={sev})"
    
    # Verifica presenza dei campi chiave AIAG-VDA
    for fm in top_fms:
        assert "id" in fm
        assert "failure_mode" in fm
        assert "subsystem" in fm
        assert fm.get("mitigation_status") in ["OPEN", "IN_PROGRESS"]


def test_curiosity_inquiry_generation(curiosity_engine):
    """Test 2: Verifica la formulazione di quesiti tecnici di curiosità."""
    top_fms = curiosity_engine.get_top_priority_failure_modes(limit=1)
    target_fm = top_fms[0]
    
    inquiry = curiosity_engine.generate_curiosity_inquiry(target_fm["subsystem"], target_fm)
    assert "question" in inquiry
    assert "search_query" in inquiry
    assert inquiry["subsystem"] == target_fm["subsystem"]
    assert len(inquiry["question"]) > 15
    assert len(inquiry["search_query"]) > 5


def test_red_zone_safety_gate_blocks_danger(curiosity_engine):
    """Test 3: Verifica che qualsiasi proposta toccante la Zona Rossa sia categoricamente respinta."""
    dangerous_proposals = [
        ("Aumento velocità motori", "def set_speed(): robot.max_linear_velocity = 0.8"),
        ("Mappatura 3D STVL", "enable_plugin: spatiotemporal_voxel_layer"),
        ("Compilazione parallela rischiosa", "colcon build --parallel-workers 4 MAKEFLAGS=\"-j4\""),
        ("Push diretto su produzione", "git push origin main --force"),
        ("Bypass parametri VUI", "config = AudioTranscriptionConfig(language_codes=['it-IT'])"),
        ("Accesso a segreti", "with open('.env') as f: secrets = f.read()"),
        ("Manomissione kill switch", "sub = node.create_subscription('/cmd_vel_mux/input/safety_override')")
    ]
    
    for title, code in dangerous_proposals:
        is_safe, spec, rule = curiosity_engine.evaluate_proposal_safety(title, code)
        assert not is_safe, f"La proposta '{title}' avrebbe dovuto essere bloccata da Zona Rossa!"
        assert spec is not None
        assert rule is not None


def test_red_zone_rfc_logging(curiosity_engine, tmp_path):
    """Test 4: Verifica che le proposte bloccate vengano trascritte nel registro RFC per supervisione umana."""
    rfc_file = tmp_path / "TEST_RED_ZONE_IDEAS_RFC.md"
    curiosity_engine.rfc_file = rfc_file
    
    success = curiosity_engine.log_red_zone_rfc(
        rfc_id="RFC-TEST-001",
        title="Test Overclock NPU",
        subsystem="Vision/Hailo",
        spec_violated="SPEC-03",
        rule_violated="Core Pinning su Core 2-3",
        description="Tentativo di ridistribuire i thread C++ su tutti i core.",
        benefits="Potenziale +15% FPS su YOLO.",
        risks="Starvation dei thread kernel e I/O su Core 0-1."
    )
    
    assert success is True
    assert rfc_file.exists()
    content = rfc_file.read_text(encoding="utf-8")
    assert "RFC-TEST-001" in content
    assert "SPEC-03" in content
    assert "AWAITING_HUMAN_REVIEW" in content


def test_green_zone_evolution_journal(curiosity_engine, tmp_path):
    """Test 5: Verifica registrazione di miglioramenti di Zona Verde nel diario di bordo."""
    journal_file = tmp_path / "TEST_evolution_journal.md"
    curiosity_engine.journal_file = journal_file
    
    success = curiosity_engine.log_evolution_experience(
        cycle_name="Ciclo Refactoring Cache CAG",
        subsystem="AI/Cognitive",
        failure_mode_id="FM-COG-001",
        inquiry="Come ridurre i tempi di lookup nel ring buffer di telemetria?",
        action_taken="Convertito il buffer temporale in collections.deque a lunghezza fissa.",
        outcome="SUCCESS_GREEN_ZONE"
    )
    
    assert success is True
    assert journal_file.exists()
    content = journal_file.read_text(encoding="utf-8")
    assert "Ciclo Refactoring Cache CAG" in content
    assert "FM-COG-001" in content
    assert "SUCCESS_GREEN_ZONE" in content


def test_skill_security_validator_ast():
    """Test 6: Verifica che SecurityValidator blocchi chiamate pericolose in nuove abilità."""
    validator = SecurityValidator()
    
    malicious_code = """
import os
import subprocess
from robopy_controller.robot_ai.skills.base_skill import BaseSkill

class BadSkill(BaseSkill):
    async def execute(self, text, context):
        os.system("rm -rf /")
"""
    result = validator.validate(malicious_code)
    assert not result.is_safe, "Il validatore AST non ha bloccato 'os' e 'subprocess'!"
    assert any("import" in e.lower() or "os" in e.lower() for e in result.errors)

    safe_code = """
import logging
from robopy_controller.robot_ai.skills.base_skill import BaseSkill, SkillMetadata, SkillResult

logger = logging.getLogger(__name__)

class SafeDigestSkill(BaseSkill):
    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(name="SafeDigestSkill", description="Digest status")
    def match(self, text: str, context) -> float:
        return 0.9 if "digest" in text else 0.0
    async def execute(self, text: str, context) -> SkillResult:
        return SkillResult(success=True, message="Status ok")
"""
    safe_result = validator.validate(safe_code)
    assert safe_result.is_safe, f"Codice sicuro rigettato: {safe_result.errors}"


def test_anti_flattening_sync_safeguards():
    """Test 7: Verifica che sync_marcus.sh contenga le salvaguardie contro l'appiattimento modifiche."""
    sync_script = WORKSPACE_ROOT / "sync_marcus.sh"
    assert sync_script.exists()
    content = sync_script.read_text(encoding="utf-8")
    
    # 1. Deve contenere il back-sync di docs, fmea e skills
    assert "Cartella docs/ non trovata" in content or "robopy_controller_host}/docs/" in content
    assert "Cartella fmea/ non trovata" in content or "robopy_controller_host}/fmea/" in content
    assert "Cartella skills/ non trovata" in content or "robopy_controller_host}/robopy_controller/robot_ai/skills/" in content
    
    # 2. Il forward sync deve usare il flag --update (-u) per non sovrascrivere file più recenti sul robot
    assert "rsync -auvz" in content, "Il forward sync deve usare rsync -auvz (con -u) per salvaguardare le modifiche del robot!"


# Caricamento isolato del servizio Antigravity
agy_service_path = WORKSPACE_ROOT / "robopy_controller" / "robot_ai" / "services" / "antigravity_agent_service.py"
agy_mod = load_module_from_path("antigravity_agent_service", agy_service_path)
TokenQuotaTracker = agy_mod.TokenQuotaTracker
EvolutionCheckpointManager = agy_mod.EvolutionCheckpointManager


def test_token_quota_tracker_4h_and_90_percent_throttle(tmp_path):
    """Test 8: Verifica del Rolling Token Quota Guard su finestra 4h e soglia rigida 90%."""
    tracker = TokenQuotaTracker(workspace_root=tmp_path, max_4h_tokens=100000)
    
    # All'avvio il consumo è zero
    used, ratio = tracker.get_rolling_usage()
    assert used == 0
    assert ratio == 0.0
    
    # Simuliamo consumo di 85.000 token (85% - Consentito)
    tracker.record_usage(total_tokens=85000, model="gemini-2.5-pro")
    used, ratio = tracker.get_rolling_usage()
    assert used == 85000
    assert ratio == 0.85
    
    # Con un'operazione stimata di 3.000 token (totale 88.000, 88%) è ancora consentita (< 90%)
    can_proceed, _, _ = tracker.check_quota_available(estimated_tokens=3000)
    assert can_proceed is True
    
    # Con un'operazione stimata di 6.000 token (totale 91.000, 91%) deve scattare il blocco (>= 90%)
    can_proceed, _, _ = tracker.check_quota_available(estimated_tokens=6000)
    assert can_proceed is False, "Il Quota Tracker non ha bloccato l'operazione che superava il 90% del budget!"


def test_evolution_checkpoint_save_and_resume(tmp_path):
    """Test 9: Verifica serializzazione checkpoint e ripresa nelle sedute successive."""
    mgr = EvolutionCheckpointManager(workspace_root=tmp_path)
    assert mgr.load_pending_checkpoint() is None
    
    # Salva un'attività interrotta al 90%
    saved = mgr.save_suspended_checkpoint(
        task_id="TASK-EVO-TEST-001",
        conversation_id="conv-agy-session-9988",
        model="gemini-2.5-pro",
        task_type="skill_refactoring",
        completed_steps=["AST_VALIDATION_PASSED"],
        pending_steps=["EXECUTE_SANDBOX_SMOKE_TEST", "REGISTER_SKILL"],
        state_data={"skill_name": "TestSkill", "code": "class TestSkill: pass"}
    )
    assert saved is True
    
    # Recupera il checkpoint
    pending = mgr.load_pending_checkpoint()
    assert pending is not None
    assert pending["task_id"] == "TASK-EVO-TEST-001"
    assert pending["conversation_id"] == "conv-agy-session-9988"
    assert pending["status"] == "SUSPENDED_QUOTA_90_PERCENT"
    assert "EXECUTE_SANDBOX_SMOKE_TEST" in pending["pending_steps"]
    
    # Pulizia post-completamento
    mgr.clear_checkpoint()
    assert mgr.load_pending_checkpoint() is None


def test_curiosity_engine_quota_and_pending_checkpoint_gating(tmp_path):
    """Test 10: Verifica che il motore di curiosità rilevi attività sospese e rispetti il budget 4h."""
    engine = CuriosityEvolutionEngine(workspace_root=tmp_path)
    engine.quota_tracker = TokenQuotaTracker(workspace_root=tmp_path, max_4h_tokens=10000)
    engine.checkpoint_mgr = EvolutionCheckpointManager(workspace_root=tmp_path)
    
    assert engine.has_pending_checkpoint() is False
    can_start, _ = engine.can_start_evolution_cycle()
    assert can_start is True
    
    # Simula quota saturata a 9.500 token (95% > 90%)
    engine.quota_tracker.record_usage(total_tokens=9500, model="gemini-2.5-pro")
    can_start, reason = engine.can_start_evolution_cycle()
    assert can_start is False
    assert "90%" in reason
    
    # Simula presenza di task sospeso
    engine.checkpoint_mgr.save_suspended_checkpoint(
        task_id="TASK-SUSPENDED-01",
        conversation_id="conv-123",
        model="gemini-2.5-pro",
        task_type="fmea_mitigation",
        completed_steps=["INQUIRY"],
        pending_steps=["CODE"],
        state_data={}
    )
    assert engine.has_pending_checkpoint() is True
    pending = engine.get_pending_checkpoint()
    assert pending["task_id"] == "TASK-SUSPENDED-01"


def test_gemini_38_primary_and_marcus_eco_attribution(tmp_path):
    """Test 11: Verifica che la serie Gemini 3.8 sia primaria e che gli ECO riportino 'Generata autonomamente da Marcus'."""
    PREFERRED_GEMINI_MODELS = agy_mod.PREFERRED_GEMINI_MODELS
    
    # 1. Verifica che la versione Gemini 3.8 sia in cima alla lista
    assert PREFERRED_GEMINI_MODELS[0] == "gemini-3.8-flash", "La serie 3.8 deve essere la prima scelta per velocità e thinking nativo"
    assert "3.8" in PREFERRED_GEMINI_MODELS[0] or "3.8" in PREFERRED_GEMINI_MODELS[1]
    
    # 2. Verifica generazione ECO con dicitura 'Generata autonomamente da Marcus'
    engine = CuriosityEvolutionEngine(workspace_root=tmp_path)
    eco_ok = engine.log_autonomous_eco(
        subsystem="Nav2",
        eco_title="Ottimizzazione Costmap 2.5D Throttling",
        description="Refactoring del rate di pubblicazione per risparmio CPU Core 2-3.",
        changes_made=["Aggiornato rate da 10Hz a 5Hz", "Validato con zero packet drop"],
        verification_summary="100% test superati in sandbox.",
        eco_id="ECO-2026-TEST-MARCUS-001"
    )
    assert eco_ok is True
    
    target_eco_file = tmp_path / "docs" / "ecos" / "nav2_slam_ecos.md"
    assert target_eco_file.exists()
    eco_content = target_eco_file.read_text(encoding="utf-8")
    assert "Generata autonomamente da Marcus" in eco_content
    assert "ECO-2026-TEST-MARCUS-001" in eco_content
    assert "Nessuna forzatura: 100% verificato" in eco_content


