#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Milestone 4 Comprehensive Unit Test Suite:
Conversational VUI, Situational Awareness & Safety Gates.
=========================================================
Tests:
  1. VUIDialogueEngine:
     - Phrasing variations, case insensitivity, trailing punctuation
     - Location queries, coordinate float precision, nearest room proximity
     - Map queries, covariance trace formatting, exact 0.080 threshold boundary
     - Visual detections: empty, None, single, and multiple items
     - Unrecognized and empty query fallbacks
     - Audio resampling contract: (16000, 48000) True, others False
     - Barge-in gain transitions (1.0 -> 0.1 -> 1.0)
  2. SituationalAwarenessSkill:
     - Priority 95 and BaseSkill metadata compliance
     - Deterministic fast-path matching (1.0 for queries, 0.98 for destructive commands, 0.0 for unrelated)
     - Execution speed (<50ms)
     - Interception when destructive confirmation is pending
  3. DestructiveConfirmationGate:
     - Intent and entity parsing (MAP_NEW_ROOM, MAP_UPDATE, MAP_OVERWRITE, MAP_DELETE)
     - Authoritative warning challenge prompt with 30s timeout mention
     - BUG-HRI-01: Negation priority ('non confermo', 'no', 'annulla') cancels immediately
     - Strict affirmative validation ('sì, confermo', 'si, confermo', 'confermo')
     - Rejection of vague phrases ('procedi pure', 'vai', 'ok') keeping gate pending
     - 30.0s virtual clock countdown and boundary behavior (29.99s pending vs 30.00s timeout)
     - Safe downstream execution on confirmed actions
  4. RespeakerVUINode:
     - Resampling verification contract
     - Barge-in gain set_tts_active method
"""

import pytest
import asyncio
import time
import json
from unittest.mock import MagicMock

from robopy_controller.robot_ai.vui.vui_dialogue_engine import VUIDialogueEngine
from robopy_controller.robot_ai.skills.builtin.situational_awareness_skill import SituationalAwarenessSkill
from robopy_controller.robot_ai.core.destructive_confirmation_gate import DestructiveConfirmationGate


# ============================================================================
# Group 1: VUIDialogueEngine Queries & Natural Phrasing
# ============================================================================
class TestVUIDialogueEngineQueries:
    """Evaluates phrasing variations, casing, and punctuation robustness."""

    @pytest.fixture
    def vui(self):
        engine = VUIDialogueEngine()
        engine.update_cag_location(
            room_name="salotto",
            location=(2.4, -1.8),
            map_name="piano_terra.yaml",
            covariance_trace=0.045,
            visual_detections=["sedia", "tavolo"],
            nearest_room="corridoio",
            nearest_dist=1.2,
        )
        return engine

    @pytest.mark.parametrize(
        "query",
        [
            "Dove ti trovi?",
            "dove ti trovi",
            "DOVE TI TROVI?",
            "Dove ti trovi!",
            "Dove ti trovi...",
            "Dove ti trovi?!?",
            "  dove ti trovi?   ",
            "Dove ti trovi Marcus?",
            "Dove sei?",
            "in che stanza ti trovi",
            "In che stanza sei?",
            "In quale stanza ti trovi?",
            "in quale stanza sei",
        ],
    )
    def test_location_query_variations(self, vui, query):
        reply = vui.handle_query(query)
        assert "salotto" in reply
        assert "2.4" in reply
        assert "-1.8" in reply
        assert "piano_terra.yaml" in reply

    @pytest.mark.parametrize(
        "query",
        [
            "In quale mappa stai navigando?",
            "in quale mappa stai navigando marcus?",
            "In che mappa navighi?",
            "in che mappa navighi",
            "In quale mappa navighi?",
            "Quale mappa stai usando?",
            "Che mappa stai usando?",
            "IN QUALE MAPPA STAI NAVIGANDO!",
            "in quale mappa",
            "in che mappa",
        ],
    )
    def test_map_query_variations(self, vui, query):
        reply = vui.handle_query(query)
        assert "piano_terra.yaml" in reply
        assert "eccellente" in reply
        assert "0.045" in reply

    @pytest.mark.parametrize(
        "query",
        [
            "Cosa vedi?",
            "cosa vedi",
            "COSA VEDI?",
            "Cosa vedi davanti a te?",
            "cosa vedi davanti a te Marcus?",
            "Cosa stai vedendo?",
            "Cosa c'è davanti a te?",
            "cosa c e davanti",
        ],
    )
    def test_vision_query_variations(self, vui, query):
        reply = vui.handle_query(query)
        assert "sedia" in reply
        assert "tavolo" in reply

    def test_unhandled_query_fallback(self, vui):
        reply = vui.handle_query("Chi è il presidente della Repubblica?")
        assert "Non ho compreso la domanda" in reply
        assert "dove mi trovo" in reply

    def test_empty_query_fallback(self, vui):
        reply = vui.handle_query("")
        assert "Non ho compreso la domanda" in reply

    def test_whitespace_query_fallback(self, vui):
        reply = vui.handle_query("    \t \n  ")
        assert "Non ho compreso la domanda" in reply


# ============================================================================
# Group 2: Numerical Precision & Boundary Formatting
# ============================================================================
class TestNumericalPrecisionAndFormatting:
    """Validates precise formatting of coordinates, distances, and covariance thresholds."""

    def test_exact_covariance_trace_boundary_0080(self):
        vui = VUIDialogueEngine()

        # 0.079 < 0.080 -> eccellente
        vui.update_cag_location("cucina", (0.0, 0.0), "map.yaml", 0.079)
        assert "eccellente" in vui.handle_query("In che mappa navighi?")
        assert "0.079" in vui.handle_query("In che mappa navighi?")

        # 0.080 is not < 0.080 -> media
        vui.update_cag_location("cucina", (0.0, 0.0), "map.yaml", 0.080)
        assert "media" in vui.handle_query("In che mappa navighi?")
        assert "0.080" in vui.handle_query("In che mappa navighi?")

        # 0.081 > 0.080 -> media
        vui.update_cag_location("cucina", (0.0, 0.0), "map.yaml", 0.081)
        assert "media" in vui.handle_query("In che mappa navighi?")
        assert "0.081" in vui.handle_query("In che mappa navighi?")

    def test_coordinate_float_formatting(self):
        vui = VUIDialogueEngine()
        vui.update_cag_location("corridoio", (12.345, -98.765), "map.yaml", 0.02)
        reply = vui.handle_query("Dove ti trovi?")
        assert "x=12.3" in reply
        assert "y=-98.8" in reply

    def test_between_rooms_formatting(self):
        vui = VUIDialogueEngine()
        vui.update_cag_location(
            room_name="sconosciuta",
            location=(5.5, 2.0),
            map_name="map.yaml",
            covariance_trace=0.03,
            nearest_room="cucina",
            nearest_dist=0.8,
        )
        reply = vui.handle_query("Dove ti trovi?")
        assert "tra gli ambienti" in reply
        assert "cucina" in reply
        assert "0.8m" in reply


# ============================================================================
# Group 3: Visual Detections Corner Cases
# ============================================================================
class TestVisualDetectionsCornerCases:
    """Validates missing, empty, single, and multiple visual detections."""

    def test_empty_visual_detections(self):
        vui = VUIDialogueEngine()
        vui.update_cag_location("camera", (1.0, 1.0), "map.yaml", 0.04, visual_detections=[])
        reply = vui.handle_query("Cosa vedi?")
        assert reply == "Non rilevo oggetti specifici nel campo visivo attuale."

    def test_none_visual_detections(self):
        vui = VUIDialogueEngine()
        vui.cag_snapshot["last_visual_detections"] = None
        reply = vui.handle_query("Cosa vedi?")
        assert reply == "Non rilevo oggetti specifici nel campo visivo attuale."

    def test_single_visual_detection(self):
        vui = VUIDialogueEngine()
        vui.update_cag_location("salotto", (1.0, 1.0), "map.yaml", 0.04, visual_detections=["persona"])
        reply = vui.handle_query("Cosa vedi?")
        assert reply == "Nel mio campo visivo riconosco: persona."

    def test_multiple_visual_detections(self):
        vui = VUIDialogueEngine()
        vui.update_cag_location(
            "camera",
            (1.0, 1.0),
            "map.yaml",
            0.04,
            visual_detections=["letto", "comodino", "lampada"],
        )
        reply = vui.handle_query("Cosa vedi?")
        assert "letto" in reply
        assert "comodino" in reply
        assert "lampada" in reply


# ============================================================================
# Group 4: Audio Resampling Contract & Barge-in Gain
# ============================================================================
class TestAudioResamplingAndBargeIn:
    """Validates SPEC-04 audio parameters and barge-in gain transitions."""

    def test_audio_streaming_rates(self):
        vui = VUIDialogueEngine()
        assert vui.verify_audio_resampling(16000, 48000) is True
        assert vui.verify_audio_resampling(44100, 48000) is False
        assert vui.verify_audio_resampling(16000, 44100) is False
        assert vui.verify_audio_resampling(8000, 16000) is False

    def test_barge_in_stt_gain(self):
        vui = VUIDialogueEngine()
        assert vui.stt_gain == 1.0
        vui.set_tts_active(True)
        assert vui.stt_gain == 0.1
        vui.set_tts_active(False)
        assert vui.stt_gain == 1.0

    def test_to_json_snapshot(self):
        vui = VUIDialogueEngine()
        snapshot_str = vui.to_json_snapshot()
        data = json.loads(snapshot_str)
        assert "room_name" in data
        assert "stt_gain" in data
        assert "audio_rates" in data
        assert data["audio_rates"]["in"] == 16000
        assert data["audio_rates"]["out_hw"] == 48000


# ============================================================================
# Group 5: Destructive Confirmation Gate Grammar & Intent Extraction
# ============================================================================
class TestDestructiveConfirmationGateGrammar:
    """Validates spoken command intent and entity extraction."""

    @pytest.fixture
    def gate(self):
        return DestructiveConfirmationGate()

    def test_cmd_mappa_nuova_stanza_with_room(self, gate):
        cmd = gate.parse_spoken_command("mappa nuova stanza salotto")
        assert cmd is not None
        assert cmd[0] == "MAP_NEW_ROOM"
        assert cmd[1] == "salotto"

    def test_cmd_aggiorna_mappa_with_room(self, gate):
        cmd = gate.parse_spoken_command("aggiorna la mappa della cucina")
        assert cmd is not None
        assert cmd[0] == "MAP_UPDATE"
        assert cmd[1] == "cucina"

    def test_cmd_sovrascrivi_mappa_with_room(self, gate):
        cmd = gate.parse_spoken_command("sovrascrivi mappa camera")
        assert cmd is not None
        assert cmd[0] == "MAP_OVERWRITE"
        assert cmd[1] == "camera"

    def test_cmd_cancella_mappa_with_room(self, gate):
        cmd = gate.parse_spoken_command("cancella la mappa del corridoio")
        assert cmd is not None
        assert cmd[0] == "MAP_DELETE"
        assert cmd[1] == "corridoio"

    def test_cmd_elimina_synonym(self, gate):
        cmd = gate.parse_spoken_command("elimina mappa ufficio")
        assert cmd is not None
        assert cmd[0] == "MAP_DELETE"
        assert cmd[1] == "ufficio"

    def test_unrelated_command_returns_none(self, gate):
        assert gate.parse_spoken_command("portami un bicchiere d'acqua") is None
        assert gate.parse_spoken_command("dove ti trovi?") is None


# ============================================================================
# Group 6: Destructive Confirmation Phrases & BUG-HRI-01 Negation Priority
# ============================================================================
class TestDestructiveConfirmationPhrasesAndPriority:
    """Validates strict affirmation, vague phrase rejection, and negation priority."""

    @pytest.fixture
    def gate(self):
        g = DestructiveConfirmationGate()
        g.request_action("salotto", "MAP_DELETE")
        return g

    def test_challenge_prompt_content(self, gate):
        assert gate.pending is True
        assert "Attenzione" in gate.last_prompt
        assert "salotto" in gate.last_prompt
        assert "30 secondi" in gate.last_prompt

    def test_affirmation_si_confermo_accent(self, gate):
        msg, ok = gate.process_voice_input("sì, confermo")
        assert ok is True
        assert gate.confirmed is True
        assert gate.pending is False
        assert "Conferma registrata" in msg

    def test_affirmation_si_confermo_no_accent(self, gate):
        msg, ok = gate.process_voice_input("si, confermo")
        assert ok is True
        assert gate.confirmed is True
        assert gate.pending is False

    def test_affirmation_confermo_single(self, gate):
        msg, ok = gate.process_voice_input("confermo")
        assert ok is True
        assert gate.confirmed is True
        assert gate.pending is False

    def test_vague_procedi_pure_rejected(self, gate):
        msg, ok = gate.process_voice_input("procedi pure")
        assert ok is False
        assert gate.confirmed is False
        assert gate.pending is True
        assert "non sufficientemente esplicita" in msg

    def test_vague_vai_avanti_rejected(self, gate):
        msg, ok = gate.process_voice_input("vai avanti")
        assert ok is False
        assert gate.confirmed is False
        assert gate.pending is True

    def test_vague_ok_procedi_rejected(self, gate):
        msg, ok = gate.process_voice_input("ok procedi")
        assert ok is False
        assert gate.confirmed is False
        assert gate.pending is True

    def test_negation_non_confermo_priority_bug_hri_01(self, gate):
        """BUG-HRI-01: 'non confermo' contains 'confermo' but MUST be recognized as negation."""
        msg, ok = gate.process_voice_input("non confermo")
        assert ok is False
        assert gate.confirmed is False
        assert gate.pending is False
        assert "annullata" in msg.lower()

    def test_negation_no_annulla_subito(self, gate):
        msg, ok = gate.process_voice_input("no, annulla subito")
        assert ok is False
        assert gate.confirmed is False
        assert gate.pending is False
        assert "annullata" in msg.lower()

    def test_negation_lascia_stare(self, gate):
        msg, ok = gate.process_voice_input("lascia stare")
        assert ok is False
        assert gate.confirmed is False
        assert gate.pending is False


# ============================================================================
# Group 7: Destructive Gate Timing & Timeout Expiration
# ============================================================================
class TestDestructiveGateTiming:
    """Validates 30.0s virtual timer countdown and boundary conditions."""

    def test_timer_countdown_steps(self):
        gate = DestructiveConfirmationGate()
        gate.request_action("cucina", "MAP_OVERWRITE")
        assert gate.pending is True

        # At 15s -> still pending
        res = gate.advance_time(15.0)
        assert res is None
        assert gate.pending is True

        # At 29.0s -> still pending
        res = gate.advance_time(14.0)
        assert res is None
        assert gate.pending is True

        # At 29.99s -> still pending
        res = gate.advance_time(0.99)
        assert res is None
        assert gate.pending is True

        # At 30.00s -> timeout cancels gate safely
        res = gate.advance_time(0.01)
        assert res is not None
        assert gate.pending is False
        assert gate.confirmed is False
        assert "Tempo scaduto" in res

    def test_post_timeout_confirmation_rejected(self):
        gate = DestructiveConfirmationGate()
        gate.request_action("camera", "MAP_DELETE")
        gate.advance_time(30.0)
        assert gate.pending is False

        # User tries to confirm after timeout
        msg, ok = gate.process_voice_input("sì, confermo")
        assert ok is False
        assert gate.confirmed is False
        assert "Nessuna operazione distruttiva in attesa" in msg


# ============================================================================
# Group 8: SituationalAwarenessSkill Execution & Matching
# ============================================================================
class TestSituationalAwarenessSkillIntegration:
    """Validates BaseSkill integration, fast-path match, and execution latency."""

    def test_skill_metadata(self):
        skill = SituationalAwarenessSkill()
        meta = skill.get_metadata()
        assert meta.name == "situational_awareness"
        assert meta.priority == 95
        assert meta.enabled is True

    def test_skill_matching(self):
        vui = VUIDialogueEngine()
        gate = DestructiveConfirmationGate()
        skill = SituationalAwarenessSkill(dialogue_engine=vui, destructive_gate=gate)

        # Situational queries match with 1.0
        assert skill.match("Dove ti trovi?") == 1.0
        assert skill.match("In che mappa navighi?") == 1.0
        assert skill.match("Cosa vedi?") == 1.0

        # Destructive commands match with 0.98
        assert skill.match("cancella la mappa del salotto") == 0.98
        assert skill.match("mappa nuova stanza taverna") == 0.98

        # Unrelated text
        assert skill.match("accendi le luci della cucina") == 0.0

        # When gate is pending, match MUST return 1.0 to intercept confirmation!
        gate.request_action("salotto", "MAP_DELETE")
        assert skill.match("sì, confermo") == 1.0
        assert skill.match("non confermo") == 1.0

    def test_skill_execution_fast_path(self):
        vui = VUIDialogueEngine()
        vui.update_cag_location("studio", (1.0, 2.0), "piano_1.yaml", 0.03)
        skill = SituationalAwarenessSkill(dialogue_engine=vui)

        t0 = time.time()
        res = asyncio.run(skill.execute("Dove ti trovi?"))
        duration_ms = (time.time() - t0) * 1000.0

        assert res.success is True
        assert "studio" in res.speak
        assert "piano_1.yaml" in res.speak
        # Sub-50ms fast-path execution requirement
        assert duration_ms < 50.0

    def test_skill_execution_destructive_flow(self):
        gate = DestructiveConfirmationGate()
        skill = SituationalAwarenessSkill(destructive_gate=gate)

        # 1. Trigger destructive command
        res = asyncio.run(skill.execute("sovrascrivi mappa garage"))
        assert res.success is True
        assert gate.pending is True
        assert "garage" in res.speak
        assert "Attenzione" in res.speak

        # 2. Confirm command
        res_conf = asyncio.run(skill.execute("sì, confermo"))
        assert res_conf.success is True
        assert gate.confirmed is True
        assert gate.pending is False
        assert "Conferma registrata" in res_conf.speak


# ============================================================================
# Group 9: Downstream Execution on Confirmed Action
# ============================================================================
class TestDownstreamExecutionOnConfirmation:
    """Validates interaction with room registry and SSD map deletion."""

    def test_delete_room_called_on_confirmed_delete(self):
        mock_registry = MagicMock()
        mock_registry.delete_room.return_value = True

        gate = DestructiveConfirmationGate(room_registry=mock_registry)
        gate.request_action("salotto", "MAP_DELETE")
        gate.process_voice_input("sì, confermo")

        assert gate.confirmed is True
        mock_registry.delete_room.assert_called_once_with("salotto")

    def test_delete_room_not_called_on_negation(self):
        mock_registry = MagicMock()
        gate = DestructiveConfirmationGate(room_registry=mock_registry)
        gate.request_action("salotto", "MAP_DELETE")
        gate.process_voice_input("non confermo")

        assert gate.confirmed is False
        mock_registry.delete_room.assert_not_called()

    def test_delete_room_not_called_on_timeout(self):
        mock_registry = MagicMock()
        gate = DestructiveConfirmationGate(room_registry=mock_registry)
        gate.request_action("salotto", "MAP_DELETE")
        gate.advance_time(30.0)

        assert gate.confirmed is False
        mock_registry.delete_room.assert_not_called()
