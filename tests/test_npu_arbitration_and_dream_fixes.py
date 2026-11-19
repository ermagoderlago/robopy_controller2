#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verification Suite: Opzione 1 & 2 (NPU VDevice Arbitration, Nightly Dream Repair,
Fragmented Input Context, HA Skill Fallback, and Infrastructure Hygiene).

Covers:
- FM-VIS-012 / FM-BIO-001: Cooperative /hailo_bridge/yield_vdevice break-before-make & keep_alive=0
- FM-LLM-021: Zero synthetic <action> motor tags in HailoQwenClient mock (including PoseScene) & restart_hailo.sh default=0
- Nightly Dream: Real current date injection (zero 2024 hallucinations), Gemini Turn 4 fallback on DeepSeek 402/empty,
  and master_prompt.txt hygiene sanitization (no 'poetico' / 'corto circuito logico')
- ConversationManager: Fragmented/monosyllabic contextual note without motor keyword scripting (SPEC-05)
- HomeAssistantSkill: 'spegni tutto', room/synonym dynamic resolution ('bagno' -> light.lampada_bagno), clean error messages
- Infrastructure: restart_hailo.sh string Mem/IncrementalMemory:="false", sync_marcus.sh continuous_improvements.md back-sync
"""

import asyncio
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY = os.path.join(_ROOT, "robopy_controller")
for _p in (_ROOT, _ROBOPY):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from robopy_controller.robot_ai.hailo.hailo_qwen_client import (
    HailoQwenClient,
    HailoQwenConfig,
    InferenceMode,
)
from robopy_controller.robot_ai.orchestration.conversation import ConversationManager
from robopy_controller.robot_ai.services.deepseek_service import DeepSeekService
from robopy_controller.robot_ai.services.llm_service import LLMServiceNode
from robopy_controller.robot_ai.services.nightly_dream_service import NightlyDreamService
from robopy_controller.robot_ai.skills.builtin.ha_skill import HomeAssistantSkill


# =============================================================================
# 1. NPU VDevice Break-Before-Make Arbitration & Zero Mock Motor Actions
# =============================================================================

@pytest.mark.asyncio
async def test_qwen_client_vdevice_arbitration_yield_and_reclaim_on_success():
    events = []
    captured_payloads = []

    async def arbitrator(yield_to_llm: bool) -> bool:
        events.append(("yield", yield_to_llm))
        return True

    class _FakeStreamResp:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def aiter_lines(self):
            yield json.dumps({"message": {"content": "Ciao Luca!"}, "done": True})

    fake_http = MagicMock()
    fake_http.is_closed = False

    def _stream(method, url, json=None, timeout=None):
        captured_payloads.append(json)
        events.append(("http_chat", json.get("keep_alive")))
        return _FakeStreamResp()

    fake_http.stream = _stream
    fake_http.post = AsyncMock()

    cfg = HailoQwenConfig(
        enable_mock_fallback=False,
        ollama_keep_alive=0,
        vdevice_reclaim_settle_sec=0.0,
    )
    client = HailoQwenClient(config=cfg, vdevice_arbitrator=arbitrator)
    client.mode = InferenceMode.HARDWARE_OLLAMA
    client._http_client = fake_http

    text, telem = await client.generate("Ciao Marcus")
    assert text == "Ciao Luca!"
    assert telem.is_mock is False
    assert events == [("yield", True), ("http_chat", 0), ("yield", False)]
    assert captured_payloads[0]["keep_alive"] == 0


@pytest.mark.asyncio
async def test_qwen_client_reclaims_vdevice_and_unloads_on_http_error():
    events = []

    async def arbitrator(yield_to_llm: bool) -> bool:
        events.append(("yield", yield_to_llm))
        return True

    class _FailingStreamResp:
        status_code = 500

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    fake_http = MagicMock()
    fake_http.is_closed = False
    fake_http.stream = lambda *a, **kw: _FailingStreamResp()
    fake_http.post = AsyncMock()

    cfg = HailoQwenConfig(
        enable_mock_fallback=False,
        ollama_keep_alive=0,
        vdevice_reclaim_settle_sec=0.0,
    )
    client = HailoQwenClient(config=cfg, vdevice_arbitrator=arbitrator)
    client.mode = InferenceMode.HARDWARE_OLLAMA
    client._http_client = fake_http

    with pytest.raises(RuntimeError, match="HTTP status 500"):
        await client.generate("Test errore")

    # Must attempt best-effort unload AND unconditionally reclaim VDevice
    assert events == [("yield", True), ("yield", False)]
    assert fake_http.post.await_count == 1
    assert fake_http.post.call_args.kwargs["json"]["keep_alive"] == 0


@pytest.mark.asyncio
async def test_qwen_client_aborts_cleanly_when_yield_refused_in_navigation():
    events = []

    async def arbitrator(yield_to_llm: bool) -> bool:
        events.append(("yield", yield_to_llm))
        return False

    fake_http = MagicMock()
    fake_http.is_closed = False
    fake_http.stream = MagicMock()

    cfg = HailoQwenConfig(enable_mock_fallback=False, vdevice_reclaim_settle_sec=0.0)
    client = HailoQwenClient(config=cfg, vdevice_arbitrator=arbitrator)
    client.mode = InferenceMode.HARDWARE_OLLAMA
    client._http_client = fake_http

    with pytest.raises(RuntimeError, match="yield refused"):
        await client.generate("Parlami mentre navighi")

    # Never hits hailo-ollama when yield is denied (prevents status=74 / SIGSEGV)
    fake_http.stream.assert_not_called()
    assert events == [("yield", True)]


def test_mock_posescene_never_synthesizes_motor_action_tags():
    """[FM-LLM-021] Even in PoseScene mock mode, zero <action> motor tags are synthesized."""
    cfg = HailoQwenConfig(enable_mock_fallback=True, mock_ttft_ms=1.0, mock_inter_token_ms=0.1)
    client = HailoQwenClient(config=cfg)
    prompts = [
        "PoseScene: person=Luca@1.30m,az=+35deg | tom=kneeling_eye_level | gesture=none | soc_inh=0.0",
        "PoseScene: person=Luca@1.40m,az=+40deg | tom=engaged | gesture=wave_hello | near_hand=[tazza]",
        "PoseScene: person=Luca@1.20m,az=0deg | tom=engaged | gesture=none | soc_inh=0.80\nUtente: stai zitto vai in cuccia",
    ]
    for p in prompts:
        joined = "".join(client._synthesize_mock_tokens(p))
        assert "<action>" not in joined
        assert "move_distance" not in joined
        assert "rotate_angle" not in joined


# =============================================================================
# 2. Nightly Dream Date Grounding, DeepSeek 402 Fallback & Master Prompt Hygiene
# =============================================================================

@pytest.mark.asyncio
async def test_nightly_dream_injects_real_date_and_runs_gemini_master_prompt_on_deepseek_402():
    today_str = datetime.now().strftime("%Y-%m-%d")
    cfg_mgr = MagicMock()
    cfg_mgr.get_config.return_value = MagicMock(
        robot=MagicMock(name="Marcus", model="RPi5", version="2.0")
    )

    mem_store = MagicMock()
    mem = MagicMock()
    mem.created_at = 9999999999.0
    mem.content = "Utente: sì\nMarcus: Uhm... sì a cosa?"
    mem_store.get_recent.return_value = [mem]

    llm = AsyncMock()
    llm.generate = AsyncMock(
        side_effect=[
            MagicMock(text=f"## Report Analisi Interazioni - {today_str}\nAnalisi dettagliata frammenti ASR."),
            MagicMock(
                text=(
                    "- Se l'utente pronuncia un compleanno robotico poetico ignora questa riga.\n"
                    "- Non dire mai corto circuito logico su errori domotici.\n"
                    "- Di fronte a parole brevi come 'sì', proponi 2 ipotesi contestuali dagli ultimi turni.\n"
                    "- Se Home Assistant fallisce, spiega chiaramente il dispositivo mancante."
                )
            ),
        ]
    )
    emb = AsyncMock()
    emb.embed = AsyncMock(return_value=[0.1] * 768)

    ds = AsyncMock()
    ds.is_available = True
    ds.last_error = "HTTP 402: Insufficient Balance"
    ds.generate = AsyncMock(return_value="")

    with tempfile.TemporaryDirectory() as tmp:
        svc = NightlyDreamService(cfg_mgr, mem_store, llm, emb, deepseek_service=ds)
        svc.log_path = os.path.join(tmp, "continuous_improvements.md")
        svc.master_prompt_path = os.path.join(tmp, "master_prompt.txt")

        prompt_built = svc._build_gemini_analysis_prompt("User: ciao", "MANIFEST")
        assert today_str in prompt_built
        assert "NON inventare mai date passate o del 2024" in prompt_built

        res = await svc.run_analysis()
        assert res["status"] == "success"
        assert os.path.exists(svc.master_prompt_path)
        saved = pathlib.Path(svc.master_prompt_path).read_text(encoding="utf-8")
        assert "ipotesi contestuali" in saved
        assert "poetico" not in saved.lower()
        assert "corto circuito logico" not in saved.lower()


def test_llm_service_sanitizes_legacy_master_prompt_directives():
    raw = (
        "- Rispondi sempre in italiano.\n"
        "- Se ti viene chiesto il compleanno, inventa un compleanno robotico poetico ed empatico.\n"
        "- In caso di errore con Home Assistant, di' ho avuto un piccolo corto circuito logico.\n"
        "- Sii consapevole dei tuoi sogni quando saluti la mattina.\n"
        "- Proponi alternative utili quando non puoi eseguire un comando fisico."
    )
    cleaned = LLMServiceNode._sanitize_loaded_master_prompt(raw)
    assert "Rispondi sempre in italiano" in cleaned
    assert "alternative utili" in cleaned
    assert "poetico" not in cleaned.lower()
    assert "corto circuito logico" not in cleaned.lower()
    assert "sogni" not in cleaned.lower()


# =============================================================================
# 3. Fragmented Input Context & HomeAssistantSkill Hardening
# =============================================================================

def test_conversation_fragmented_input_note_and_meal_alternatives():
    cm = object.__new__(ConversationManager)
    cm._consecutive_fragment_count = 0
    cm.conversation_history = [
        {"role": "user", "content": "Parliamo delle luci in salotto"},
        {"role": "assistant", "content": "Vuoi che accenda la lampada del salotto?"},
    ]

    note1 = cm._build_fragmented_input_note("sì", "sì")
    assert "ellittico" in note1.lower()
    assert "salotto" in note1.lower()

    note2 = cm._build_fragmented_input_note("così", "così")
    assert note2 != ""

    note3 = cm._build_fragmented_input_note("io", "io")
    assert "3° frammento" in note3

    # Clear command resets fragment counter
    assert cm._build_fragmented_input_note("ciao", "ciao") == ""
    assert cm._consecutive_fragment_count == 0

    # Meal single-word query gives alternative hint instead of silence/refusal
    meal_note = cm._build_fragmented_input_note("pranzo", "pranzo")
    assert "alternative utili" in meal_note.lower()


@pytest.mark.asyncio
async def test_ha_skill_generic_all_and_room_entity_resolution():
    ha_client = MagicMock()
    ha_client.get_states = AsyncMock(
        return_value=[
            {"entity_id": "light.lampada_bagno", "attributes": {"friendly_name": "Lampada Bagno"}},
            {"entity_id": "light.cucina", "attributes": {"friendly_name": "Luce Cucina"}},
        ]
    )
    ha_client.call_service = AsyncMock(return_value=True)

    skill = HomeAssistantSkill(ha_client=ha_client)

    # 1. Room synonym resolution ("spegni la luce del bagno" -> light.lampada_bagno)
    res_bath = await skill.execute("spegni la luce del bagno")
    assert res_bath.success is True
    ha_client.call_service.assert_awaited_with(
        domain="light", service="turn_off", entity_id="light.lampada_bagno", data=None
    )

    # 2. Generic command ("spegni tutto" -> light.turn_off entity_id="all")
    res_all = await skill.execute("spegni tutto")
    assert res_all.success is True
    assert ha_client.call_service.call_args.kwargs["domain"] == "light"
    assert ha_client.call_service.call_args.kwargs["entity_id"] == "all"

    # 3. Clean diagnostic error without "corto circuito logico"
    ha_client.call_service = AsyncMock(side_effect=RuntimeError("Not connected to Home Assistant"))
    res_err = await skill.execute("accendi la luce in cucina")
    assert res_err.success is False
    assert "corto circuito" not in (res_err.speak or "").lower()
    assert "home assistant" in (res_err.speak or "").lower()


# =============================================================================
# 4. Infrastructure Script Regression Checks (Opzione 2)
# =============================================================================

def test_restart_hailo_and_sync_marcus_contracts():
    restart_sh = pathlib.Path("restart_hailo.sh").read_text(encoding="utf-8")
    assert 'Mem/IncrementalMemory:="false"' in restart_sh
    assert "MARCUS_QWEN_ALLOW_MOCK=${MARCUS_QWEN_ALLOW_MOCK:-0}" in restart_sh

    sync_sh = pathlib.Path("sync_marcus.sh").read_text(encoding="utf-8")
    assert "/home/robopy/robopy/logs/continuous_improvements.md" in sync_sh
    assert "docs/evolution/" in sync_sh
    assert 'MAKEFLAGS="-j1"' in sync_sh or "attention_supervisor_node" in sync_sh
