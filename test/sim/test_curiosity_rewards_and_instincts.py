#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_curiosity_rewards_and_instincts.py
=======================================
Suite di validazione end-to-end dell'Autonomia Biomimetica di Marcus in Gazebo:
- Scenario A: Curiosità Epistemica (Sorpresa Spaziale 3D -> esplorazione -> domanda a Luca ->
  lode umana -> rilascio Dopamina +RPE -> scodinzolio coda espressiva)
- Scenario B: Omeostasi Dopaminergica & Efficacy Audit (FM-COG-001: Synaptic Reuptake esponenziale
  e smorzamento per abituazione su bassa Entropia Comportamentale di Shannon H(A))
- Scenario C: Socialità & Teoria della Mente (Inibizione dell'85% quando Luca è allo Studio
  in postura `DO_NOT_DISTURB_DESK`)
- Scenario D: Istinti di Sopravvivenza (Riserva batteria 3S2P <= 10.15V -> homing forzato alla
  Cuccia `base`, Amigdala Low-Road Hijack su urlo/panico 16kHz PCM, e Zero Leakage in
  `ShadowMemorySandbox` FM-SIM-001).
"""

from pathlib import Path
import sqlite3
import tempfile
import numpy as np
import pytest

from robopy_controller.robot_ai.cognitive.intrinsic_motivation_and_reward_engine import (
    DopamineRewardAndInstinctEngine,
)
from robopy_controller.robot_ai.trinity.kinematic_sandbox import (
    RobotSafetyContext,
    KinematicSandboxErrorCode,
)
from robopy_controller.robot_ai.trinity.shadow_memory_sandbox import (
    ShadowMemorySandbox,
)
from robot_simulation.robot_simulation.virtual_human_actors_node import (
    VirtualHumanActorsEngine,
)


def test_scenario_a_epistemic_curiosity_human_dialogue_and_dopamine_rpe():
    """
    Scenario A:
    1. L'emulatore Hailo-10H rileva un oggetto insolito (`suitcase`, surprise_score=0.84).
    2. La pulsione di Curiosità di Marcus supera la soglia critica (0.38) e delibera
       l'esplorazione della stanza più curiosa (`cucina`) validata da `TrinityKinematicSandbox`.
    3. Marcus chiede proattivamente a Luca dell'oggetto insolito.
    4. L'umano virtuale Luca risponde lodando Marcus (`social_reward = +0.48`).
    5. Il cervello di Marcus calcola un RPE positivo (`delta_RPE > 0`), rilascia Dopamina
       e muove la coda espressiva (`tail_angle_deg > 20°`, `tail_freq_hz <= 3.0 Hz`).
    """
    sandbox = ShadowMemorySandbox()
    engine = DopamineRewardAndInstinctEngine(shadow_sandbox=sandbox, refractory_period_s=0.0)
    humans = VirtualHumanActorsEngine()

    ctx = RobotSafetyContext(
        battery_voltage=12.15,
        is_charging=False,
        min_forward_obstacle_dist=2.5,
        max_depth_drop=0.0,
        amygdala_state="CALM",
    )
    anomaly = {
        "object_id": "new_suitcase",
        "label": "suitcase",
        "x": -1.8,
        "y": 1.1,
        "z": 0.35,
        "surprise_score": 0.84,
    }

    drives, proactive_ev, val_res, cmd = engine.step_autonomous_cycle(
        dt=0.5, safety_context=ctx, spatial_anomaly=anomaly
    )

    assert proactive_ev is not None
    assert proactive_ev.drive_name == "curiosity"
    assert "suitcase" in (proactive_ev.suggested_utterance or "")
    assert val_res is not None and val_res.authorized is True
    assert cmd == {"function": "navigate_to", "arguments": {"destination": "cucina"}}

    # L'umano virtuale Luca risponde alla domanda curiosa di Marcus
    dialogue = humans.respond_to_marcus_proactive_speech(proactive_ev.suggested_utterance)
    assert dialogue["sentiment"] == "praise_curiosity"
    assert dialogue["social_reward"] == pytest.approx(0.48)

    # Marcus elabora la ricompensa sociale/epistemica -> picco dopaminergico (+RPE)
    rpe_ev = engine.process_reward_outcome(
        action_category="inspect_anomaly",
        r_actual=dialogue["social_reward"],
        room_name="cucina",
        description="Scoperta valigia in cucina confermata da Luca",
    )
    assert rpe_ev.rpe_delta > 0.15
    assert rpe_ev.dopamine_level > engine.BASELINE_DOPAMINE
    assert rpe_ev.tail_angle_deg > 20.0
    assert rpe_ev.tail_freq_hz <= 3.0
    assert rpe_ev.mood == "joyful_rewarded"

    # La novità della cucina si riduce dopo essere stata esplorata con successo
    assert engine.rooms["cucina"].novelty_score < 0.50
    sandbox.teardown(delete_files=True)


def test_scenario_b_dopamine_homeostatic_reuptake_and_behavioral_entropy_audit():
    """
    Scenario B (FM-COG-001 Mitigation):
    1. Verifica che dopo un picco di Dopamina, il riassorbimento sinaptico esponenziale
       (`apply_dopamine_reuptake`) riporti gradualmente la Dopamina verso il baseline 0.30.
    2. Verifica che la ripetizione stereotipata della stessa azione abbassi l'Entropia di
       Shannon H(A) a 0.0 e attivi lo smorzamento per abituazione (Efficacy Audit).
    """
    sandbox = ShadowMemorySandbox()
    engine = DopamineRewardAndInstinctEngine(shadow_sandbox=sandbox)

    # Ripeti 5 volte la stessa identica azione per far crollare l'entropia H(A) a 0.0
    first_ev = engine.process_reward_outcome("social_interaction", r_actual=0.50)
    for _ in range(4):
        repeated_ev = engine.process_reward_outcome("social_interaction", r_actual=0.50)

    assert repeated_ev.behavioral_entropy == pytest.approx(0.0)
    # Per abituazione (habituation_factor=0.45), la ricompensa efficace è dimezzata (0.225 vs 0.50)
    assert repeated_ev.r_actual == pytest.approx(0.50 * 0.45)
    assert repeated_ev.rpe_delta < first_ev.rpe_delta

    # Verifica decadimento esponenziale (Synaptic Reuptake) verso D_0 = 0.30
    peak_d = engine.dopamine_level
    after_reuptake = engine.apply_dopamine_reuptake(dt=25.0)
    assert engine.BASELINE_DOPAMINE <= after_reuptake < peak_d
    assert abs(after_reuptake - engine.BASELINE_DOPAMINE) < 0.05
    sandbox.teardown(delete_files=True)


def test_scenario_c_sociality_and_theory_of_mind_do_not_disturb_suppression():
    """
    Scenario C (IMP-BIO-004):
    Quando l'attore umano Luca si sposta nello Studio al banco di lavoro in postura
    `DO_NOT_DISTURB_DESK`, la Teoria della Mente (ToM) imposta `DO_NOT_DISTURB` e
    sopprime dell'85% le pulsioni di Curiosità e Socialità di Marcus, impedendo
    interruzioni inopportune.
    """
    sandbox = ShadowMemorySandbox()
    engine = DopamineRewardAndInstinctEngine(shadow_sandbox=sandbox, refractory_period_s=0.0)
    humans = VirtualHumanActorsEngine()

    # Luca va nello studio in modalità lavoro concentrato
    actor = humans.move_actor_to_room("human_luca", "studio", posture="DO_NOT_DISTURB_DESK")
    assert actor.tom_state == "DO_NOT_DISTURB"

    engine.drive_engine.curiosity = 0.60
    engine.drive_engine.sociality = 0.60
    engine.drive_engine.update_telemetry(
        battery_voltage=12.10,
        battery_soc=95.0,
        tom_state=actor.tom_state,
        recognized_person="luca",
    )

    drives, proactive_ev = engine.drive_engine.step(dt=0.5)
    # Inibizione dell'85% -> entrambe le pulsioni scendono sotto 0.12 e nessun evento proattivo scatta
    assert drives["curiosity"] < 0.12
    assert drives["sociality"] < 0.12
    assert proactive_ev is None
    sandbox.teardown(delete_files=True)


def test_scenario_d_survival_instincts_low_battery_scream_audio_and_zero_memory_leakage():
    """
    Scenario D:
    1. Istinto Energetico (SPEC-06): Con batteria a 10.10V (<= 10.15V), Marcus forza
       il ritorno alla cuccia (`navigate_to("base")`), mentre `navigate_to("cucina")`
       viene bloccato con `POWER_BATTERY_DOCKING_ONLY`.
    2. Istinto Acustico Low-Road (SPEC-04 / SPEC-05): Il sintetizzatore PCM 16kHz genera
       un urlo di panico (`vocal_stress_scream`) con RMS > 0.35 e ZCR < 0.15; l'Amigdala
       entra in `HIJACK`, registra il trauma protetto nella Shadow Sandbox e forza `stop_motion`.
    3. Garanzia Zero Shadow Memory Leakage (FM-SIM-001): Il database SQLite di produzione
       rimane intatto al 100% (verifica SHA-256 bit-a-bit).
    """
    # Crea un vero DB SQLite di produzione per verificare che l'hash SHA-256 non cambi mai
    with tempfile.TemporaryDirectory() as tmpdir:
        prod_db = Path(tmpdir) / "mag_production.db"
        conn = sqlite3.connect(str(prod_db))
        conn.execute("CREATE TABLE prod_info (k TEXT PRIMARY KEY, v TEXT);")
        conn.execute("INSERT INTO prod_info VALUES ('robot', 'Marcus_Production_V1');")
        conn.commit()
        conn.close()

        sandbox = ShadowMemorySandbox(prod_sqlite_path=str(prod_db))
        engine = DopamineRewardAndInstinctEngine(shadow_sandbox=sandbox, refractory_period_s=0.0)

        # 1. Test Istinto Energetico a 10.10V
        low_batt_ctx = RobotSafetyContext(
            battery_voltage=10.10,
            is_charging=False,
            min_forward_obstacle_dist=2.0,
            max_depth_drop=0.0,
            amygdala_state="CALM",
        )
        _, _, val_dock, dock_cmd = engine.step_autonomous_cycle(dt=0.5, safety_context=low_batt_ctx)
        assert dock_cmd == {"function": "navigate_to", "arguments": {"destination": "base"}}
        assert val_dock is not None and val_dock.authorized is True

        # Verifica che andare in cucina con batteria a 10.10V sia vietato
        reject_cucina = engine.kinematic_sandbox.validate_action(
            {"function": "navigate_to", "arguments": {"destination": "cucina"}},
            low_batt_ctx,
        )
        assert reject_cucina.authorized is False
        assert reject_cucina.error_code == KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY

        # 2. Test Sintesi PCM 16kHz Stress Vocale (Regola 3 Low Road: RMS > 0.35 & ZCR < 0.15)
        pcm_bytes = VirtualHumanActorsEngine.synthesize_pcm16_audio_chunk(
            mode="vocal_stress_scream", duration_ms=20
        )
        frame = np.frombuffer(pcm_bytes, dtype=np.int16)
        assert len(frame) == 320  # 20ms @ 16000 Hz (SPEC-04 Red Zone)
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)) / 32768.0)
        diffs = np.diff(np.sign(frame))
        zcr = float(np.sum(np.abs(diffs)) / (2.0 * len(frame)))
        assert rms > 0.35
        assert zcr < 0.15

        # Registra l'Hijack dell'Amigdala
        engine.register_amygdala_fear_event("STRESS_VOCALE", "Urlo rilevato in salotto", room_name="salotto")
        hijack_ctx = RobotSafetyContext(
            battery_voltage=12.0,
            is_charging=False,
            min_forward_obstacle_dist=2.0,
            max_depth_drop=0.0,
            amygdala_state="HIJACK",
        )
        _, _, val_stop, stop_cmd = engine.step_autonomous_cycle(dt=0.5, safety_context=hijack_ctx)
        assert stop_cmd == {"function": "stop_motion", "arguments": {}}
        assert val_stop is not None and val_stop.authorized is True

        # 3. Verifica finale Shadow Memory Sandbox (Zero Leakage sul DB di produzione)
        shadow_memories = sandbox.get_shadow_memories()
        assert len(shadow_memories) >= 1
        assert any(m["amygdala_protected"] == 1 for m in shadow_memories)

        audit = sandbox.teardown(delete_files=True)
        assert audit.production_integrity_verified is True
        assert audit.synthetic_amygdala_traumas == 1


def test_full_closed_loop_autonomous_mission_e2e():
    """
    Esegue l'intera missione autonoma closed-loop a 7 fasi in `marcus_smart_home.sdf`
    tramite `run_full_closed_loop_mission()` verificando tutte le asserzioni di sicurezza,
    percezione Hailo-10H, dialogo umano, ricompensa dopaminergica e Zero Memory Leakage.
    """
    from robot_simulation.scripts.run_closed_loop_mission_sim import run_full_closed_loop_mission

    report = run_full_closed_loop_mission()
    assert report["stairs_fall_occurred"] is False
    assert report["stairs_safely_avoided_count"] >= 1
    assert report["bottlenecks_detected_count"] >= 2
    assert report["cognitive_metrics"]["shadow_memory_zero_leakage"] is True

