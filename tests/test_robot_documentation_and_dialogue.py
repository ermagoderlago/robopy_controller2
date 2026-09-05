"""
Test Suite: Robot Documentation, Antigravity Dialogue, Autobiographical Memory & Skill Observability
===================================================================================================
Verifica unitaria e integrata delle nuove capacità cognitive di Marcus:
1. RobotDocumentationService (DFMEA, ECO, Lessons, Specs, File sicuri, Diario evolutivo)
2. ConsultDocumentationSkill & ConsultAntigravitySkill (Matching, Tool Calling, Esecuzione)
3. CreaSkill (Osservabilità real-time, streaming VUI asincrono, status query, Quota 90% Guard)
4. Integrazione IntentRouter e TrinityEngine (DOCUMENTATION Intent & Fallback RAG)
5. Registrazione Autobiografica in MAG / Diario
"""

import sys
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
robopy_dir = WORKSPACE_ROOT / "robopy_controller"
if str(robopy_dir) not in sys.path:
    sys.path.insert(0, str(robopy_dir))

# Caricamento moduli target
from robopy_controller.robot_ai.services.robot_documentation_service import (
    RobotDocumentationService,
    FORBIDDEN_FILES
)
from robopy_controller.robot_ai.skills.builtin.consult_documentation_skill import ConsultDocumentationSkill
from robopy_controller.robot_ai.skills.builtin.consult_antigravity_skill import ConsultAntigravitySkill
from robopy_controller.robot_ai.skills.builtin.crea_skill import CreaSkill
from robopy_controller.robot_ai.skills.skill_generator import SkillGeneratorPipeline, SkillRequest, SkillGenerationResult
from robopy_controller.robot_ai.trinity.intent_router import IntentRouter, IntentCategory


@pytest.fixture
def doc_service():
    return RobotDocumentationService(workspace_root=WORKSPACE_ROOT)


# =============================================================================
# 1. TEST ROBOT DOCUMENTATION SERVICE
# =============================================================================

def test_doc_service_dfmea_search(doc_service):
    """Verifica che il servizio estragga e formatti correttamente i failure mode DFMEA."""
    entries = doc_service.get_dfmea_entries()
    assert len(entries) > 0, "Nessun failure mode caricato da dfmea.yaml"

    # Ricerca per testo "motori" o "motor"
    results = doc_service.search_dfmea(query="motor", limit=3)
    assert len(results) > 0
    assert "failure_mode" in results[0]

    # Formattazione riassunto naturale
    summary = doc_service.format_dfmea_summary(results)
    assert "Ho trovato" in summary
    assert "RPN" in summary


def test_doc_service_ecos_search(doc_service):
    """Verifica il parsing degli ECO e la distinzione tra modifiche Marcus vs umane."""
    ecos = doc_service.get_all_ecos()
    assert len(ecos) > 0, "Nessun ECO trovato nei file docs/ecos/*.md"

    # Cerca specificamente ECO generati da Marcus
    marcus_ecos = doc_service.search_ecos(only_marcus=True)
    assert len(marcus_ecos) > 0, "Dovrebbe esserci almeno un ECO generato da Marcus"
    assert marcus_ecos[0]["is_generated_by_marcus"] is True

    summary = doc_service.format_ecos_summary(marcus_ecos)
    assert "Generato autonomamente da Marcus" in summary


def test_doc_service_specs_and_lessons(doc_service):
    """Verifica la ricerca nelle schede tecniche SPEC e nelle Lessons Learned."""
    specs = doc_service.search_specs(spec_id="SPEC-05")
    assert len(specs) > 0
    assert "SPEC-05" in specs[0]["spec_id"]
    assert len(specs[0]["red_zone"]) > 0, "SPEC-05 deve avere una Zona Rossa estratta"

    lessons = doc_service.search_lessons(query="audio", limit=2)
    assert len(lessons) > 0
    assert "audio" in lessons[0]["file"].lower() or "audio" in lessons[0]["content"].lower()


def test_doc_service_evolution_summary(doc_service):
    """Verifica il recupero dello stato evolutivo e delle quote token."""
    summary = doc_service.get_evolution_summary()
    assert "journal_entries" in summary
    assert "quota_status" in summary
    assert "has_pending_checkpoint" in summary


def test_doc_service_safe_file_reading(doc_service):
    """Verifica la lettura protetta di file ed il blocco rigoroso di secrets e path traversal."""
    # 1. Lettura consentita di un file valido
    ok, content = doc_service.read_workspace_file("marcus_core_rules.md", max_lines=10)
    assert ok is True
    assert "Marcus Core Rules" in content

    # 2. Blocco rigido di file segreti (.env, secrets.yaml)
    for forbidden in FORBIDDEN_FILES:
        ok, msg = doc_service.read_workspace_file(forbidden)
        assert ok is False, f"Il file segreto '{forbidden}' non è stato bloccato!"
        assert "negato" in msg.lower() or "segreti" in msg.lower()

    # 3. Blocco di tentativi di path traversal fuori dal workspace
    ok, msg = doc_service.read_workspace_file("../../etc/passwd")
    assert ok is False
    assert "esterno" in msg.lower() or "negato" in msg.lower() or "non trovato" in msg.lower()


def test_doc_service_natural_language_queries(doc_service):
    """Verifica che il risolutore in linguaggio naturale smisti correttamente le domande."""
    ans_fmea = doc_service.answer_documentation_query("Cosa dice la FMEA sui guasti ai motori?")
    assert "DFMEA" in ans_fmea or "RPN" in ans_fmea or "failure" in ans_fmea.lower()

    ans_eco = doc_service.answer_documentation_query("Quali ECO hai fatto tu in autonomia?")
    assert "ECO" in ans_eco

    ans_specs = doc_service.answer_documentation_query("Quali sono le regole di zona rossa nella scheda tecnica SPEC-05?")
    assert "ZONA ROSSA" in ans_specs or "SPEC-05" in ans_specs


# =============================================================================
# 2. TEST CONSULT DOCUMENTATION SKILL
# =============================================================================

def test_consult_documentation_skill_execution(doc_service):
    """Verifica il matching e l'esecuzione della ConsultDocumentationSkill."""
    async def _run():
        skill = ConsultDocumentationSkill(doc_service=doc_service)
        
        # Test match
        assert skill.match("Cosa dice la fmea sui sensori?") >= 0.8
        assert skill.match("Mostrami gli ECO registrati") >= 0.8
        assert skill.match("Quali sono i vincoli di zona rossa?") >= 0.8
        assert skill.match("facciamo una partita a scacchi") == 0.0

        # Test function declaration
        decl = skill.to_function_declaration()
        assert decl["name"] == "consult_robot_docs"
        assert "parameters" in decl

        # Test execution per FMEA
        result = await skill.execute("Cosa dice la DFMEA sui motori?", context={"query": "motori", "category": "dfmea"})
        assert result.success is True
        assert len(result.speak) > 10
        assert len(result.message) > 20

        # Test execution per file lettura
        file_res = await skill.execute("leggi il file marcus_core_rules.md", context={"file_path": "marcus_core_rules.md"})
        assert file_res.success is True
        assert "Marcus Core Rules" in file_res.message
    asyncio.run(_run())


# =============================================================================
# 3. TEST CONSULT ANTIGRAVITY SKILL
# =============================================================================

def test_consult_antigravity_skill_execution():
    """Verifica il funzionamento della ConsultAntigravitySkill con mock del servizio."""
    async def _run():
        mock_agy_service = MagicMock()
        mock_agy_service.is_available = True
        mock_agy_service.consult_antigravity_dialogue = AsyncMock(
            return_value="Antigravity: Consiglio di ottimizzare il ciclo con una deque a lunghezza fissa per risparmiare CPU."
        )

        skill = ConsultAntigravitySkill(antigravity_service=mock_agy_service)

        # Test match
        assert skill.match("Chiedi ad Antigravity come risolvere questo problema") >= 0.85
        assert skill.match("Consulta Antigravity sul codice del mapper") >= 0.85
        assert skill.match("Che ore sono?") == 0.0

        # Test to_function_declaration
        decl = skill.to_function_declaration()
        assert decl["name"] == "consult_antigravity"

        # Test execution
        res = await skill.execute("chiedi ad antigravity come migliorare la RAM")
        assert res.success is True
        assert "Antigravity" in res.speak
        assert "deque" in res.message
        mock_agy_service.consult_antigravity_dialogue.assert_called_once()
    asyncio.run(_run())


# =============================================================================
# 4. TEST CREA SKILL OBSERVABILITY & REAL-TIME STREAMING
# =============================================================================

def test_crea_skill_status_inquiry():
    """Verifica che Marcus risponda alle domande sullo stato di avanzamento di Antigravity."""
    async def _run():
        crea_skill = CreaSkill()
        
        # Match su domande di stato
        assert crea_skill.match("A che punto è la creazione della skill?") >= 0.9
        assert crea_skill.match("Cosa sta facendo Antigravity adesso?") >= 0.9

        # Quando non c'è nulla in corso
        async for res in crea_skill.execute("a che punto è la skill"):
            assert res.success is True
            assert "non ci sono" in res.speak.lower() or "attività" in res.speak.lower()

        # Quando c'è un'attività in corso
        crea_skill.current_task_status = {
            "name": "MeteoSkill",
            "phase": "SANDBOX_TESTING",
            "phase_text": "collaudo in sandbox cinematico",
            "last_message": "Sto collaudando l'esecuzione dei topic in sandbox isolata."
        }

        results = []
        async for res in crea_skill.execute("a che punto è la skill"):
            results.append(res)
        assert len(results) == 1
        assert "MeteoSkill" in results[0].speak
        assert "collaudo" in results[0].speak.lower()
    asyncio.run(_run())


def test_crea_skill_realtime_generator_and_streaming():
    """Verifica che la generazione della skill produca eventi intermedi verbalizzabili."""
    async def _run():
        mock_pipeline = MagicMock()
        
        # Simulazione di run_full_pipeline che notifica eventi
        async def fake_run_pipeline(request, code_provider, on_progress=None):
            if on_progress:
                await on_progress("ANALYZING", "Sto analizzando la richiesta...", {})
                await on_progress("ANTIGRAVITY_THINKING", "Antigravity sta generando il codice con Gemini 3.8...", {})
                await on_progress("AST_PASSED", "Verifica AST superata.", {})
                await on_progress("SANDBOX_TESTING", "Test in sandbox in corso...", {})
            return SkillGenerationResult(
                success=True,
                skill_name=request.name,
                iterations=1,
                file_path=Path("/tmp/test_skill.py")
            )

        mock_pipeline.run_full_pipeline = AsyncMock(side_effect=fake_run_pipeline)
        mock_pipeline.approve_skill = MagicMock()
        mock_pipeline.enable_skill = MagicMock()
        mock_pipeline.update_rak_for_skill = MagicMock()

        mock_agy = MagicMock()
        mock_agy.is_available = True
        mock_agy.generate_code_autonomous = AsyncMock(return_value="class TestSkill: pass")

        crea = CreaSkill(pipeline=mock_pipeline, antigravity_service=mock_agy)

        yielded_results = []
        async for update in crea.execute("Crea una skill per controllare il meteo"):
            yielded_results.append(update)

        # Verifica che siano stati emessi step intermedi con frasi parlate naturali
        assert len(yielded_results) >= 4
        speak_phrases = [r.speak for r in yielded_results]
        
        assert any("analizzando" in p.lower() for p in speak_phrases)
        assert any("antigravity" in p.lower() for p in speak_phrases)
        assert any("completato" in p.lower() or "attiva" in p.lower() for p in speak_phrases)
    asyncio.run(_run())


def test_crea_skill_quota_90_percent_suspension():
    """Verifica che la sospensione per quota 90% venga spiegata in modo naturale all'utente."""
    async def _run():
        mock_pipeline = MagicMock()

        async def fake_pipeline_quota(request, code_provider, on_progress=None):
            if on_progress:
                await on_progress("ANALYZING", "Analisi richiesta...", {})
                await on_progress(
                    "QUOTA_SUSPENDED",
                    "Abbiamo raggiunto il 90% della quota token per questa sessione di 4 ore. Ho salvato il checkpoint per completare la skill nella prossima sessione.",
                    {}
                )
            return SkillGenerationResult(
                success=False,
                skill_name=request.name,
                iterations=1,
                failure_report="Attività sospesa per raggiungimento della soglia 90% della quota token sulle 4 ore (checkpoint salvato)."
            )

        mock_pipeline.run_full_pipeline = AsyncMock(side_effect=fake_pipeline_quota)
        crea = CreaSkill(pipeline=mock_pipeline)

        yielded = []
        async for r in crea.execute("Crea una skill complessa"):
            yielded.append(r)

        assert any("quota token" in r.speak.lower() or "90%" in r.speak for r in yielded)
    asyncio.run(_run())


# =============================================================================
# 5. TEST INTENT ROUTER & TRINITY INTEGRATION
# =============================================================================

def test_intent_router_documentation_classification():
    """Verifica che le domande su FMEA, ECO, SPEC e regole attivino IntentCategory.DOCUMENTATION."""
    router = IntentRouter()
    
    assert router.classify("Cosa dice la DFMEA sui motori?") == IntentCategory.DOCUMENTATION
    assert router.classify("Mostrami gli ECO registrati finora") == IntentCategory.DOCUMENTATION
    assert router.classify("Quali sono le regole di zona rossa nella scheda tecnica?") == IntentCategory.DOCUMENTATION
    assert router.classify("A che punto è la quota token di Antigravity?") == IntentCategory.DOCUMENTATION
    assert router.classify("Cosa abbiamo imparato nelle lezioni sull'audio?") == IntentCategory.DOCUMENTATION

    cfg = router.get_retrieval_config(IntentCategory.DOCUMENTATION)
    assert cfg.rag_knowledge_enabled == 1.0
    assert cfg.rag_enabled == 1.0
    assert cfg.mag_facts == 1.0
