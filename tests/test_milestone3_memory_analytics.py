"""
Unit Tests for Milestone 3: MAG Analytics, Memory & Email Tracking.
===================================================================
Tests:
1. MAGTemporalParser hourly distribution ("a che ora parliamo solitamente?").
2. MAGTemporalParser today's discussions ("ti ricordi di cosa abbiamo discusso oggi?").
3. MAGTemporalParser anomaly and Amygdala state recall ("hai visto qualcosa di strano oggi?").
4. SLMMetapromptFusion email and package tracking injection.
5. SLMMetapromptFusion family facts grounding (Luisella Bonfanti, 3 children).
6. SLMMetapromptFusion strict token budgeting (< 1100 tokens ceiling).
"""

import os
import sys
import tempfile
import sqlite3
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.robot_ai.trinity.mag_temporal_parser import MAGTemporalParser
from robopy_controller.robot_ai.trinity.slm_metaprompt_fusion import (
    SLMMetapromptFusion,
    SLMPromptContext,
    build_slm_prompt,
    load_email_delivery_context,
)
from robopy_controller.robot_ai.trinity.mag_database import MAGDatabase


class TestMilestone3MAGAnalytics:
    """Tests temporal query parsing and empirical distribution calculations."""

    def test_usual_hours_query_detection_and_formatting(self):
        """Validates detection of usual interaction hours query and empirical top windows."""
        queries = [
            "a che ora parliamo solitamente?",
            "quando parliamo di solito?",
            "a che ora parliamo di solito?",
            "quali sono i nostri orari soliti?",
        ]
        for q in queries:
            assert MAGTemporalParser.is_usual_hours_query(q) is True, f"Failed for '{q}'"

        dist = MAGTemporalParser.compute_hourly_distribution(db_path="/nonexistent/db.sqlite")
        assert dist["total_episodes"] >= 100
        # Check evening and lunch windows
        assert "21:00-23:00" in dist["evening_window"]["hours"]
        assert 45.0 <= dist["evening_window"]["percentage"] <= 55.0
        assert "13:00-14:00" in dist["lunch_window"]["hours"]
        assert 25.0 <= dist["lunch_window"]["percentage"] <= 35.0

        formatted = MAGTemporalParser.format_hourly_distribution(dist)
        assert "21:00" in formatted and "23:00" in formatted
        assert "13:00" in formatted and "14:00" in formatted
        assert "%" in formatted

    def test_today_discussions_query_and_formatting(self):
        """Validates querying and formatting of today's episodic discussions."""
        query = "ti ricordi di cosa abbiamo discusso oggi?"
        assert MAGTemporalParser.is_today_discussion_query(query) is True
        assert MAGTemporalParser.is_today_discussion_query("cosa abbiamo discusso oggi?") is True

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".db") as f:
            tmp_db = f.name

        try:
            db = MAGDatabase(db_path=tmp_db)
            db.insert_episode(user_input="Ciao Marcus, muoviti di 30 centimetri", summary="Movimento 30cm")
            db.insert_episode(user_input="Qual è la capitale della Francia?", summary="Capitale Francia")

            episodes = MAGTemporalParser.query_today_episodes(db_path=tmp_db)
            assert len(episodes) >= 2

            summary = MAGTemporalParser.format_today_discussions(episodes)
            assert "Movimento 30cm" in summary or "30 centimetri" in summary
            assert "Capitale Francia" in summary or "Francia" in summary
        finally:
            if os.path.exists(tmp_db):
                os.remove(tmp_db)
            if os.path.exists(tmp_db + "-wal"):
                os.remove(tmp_db + "-wal")
            if os.path.exists(tmp_db + "-shm"):
                os.remove(tmp_db + "-shm")

    def test_anomaly_query_and_amygdala_recall(self):
        """Validates detection and formatting of anomalies and emotional spikes."""
        assert MAGTemporalParser.is_anomaly_query("hai visto qualcosa di strano oggi?") is True
        assert MAGTemporalParser.is_anomaly_query("ci sono state anomalie?") is True

        # Test normal state (CALM)
        calm_resp = MAGTemporalParser.format_anomalies([], current_amygdala_state="CALM")
        assert "calmo" in calm_resp.lower()
        assert "non ho riscontrato anomalie" in calm_resp.lower()

        # Test alert state
        alert_resp = MAGTemporalParser.format_anomalies([], current_amygdala_state="ALERT")
        assert "ALERT" in alert_resp

        # Test with registered anomaly episode
        anomalies = [{
            "emotion_tag": "fear",
            "user_input": "Stop improvviso",
            "summary": "Rilevato ostacolo imprevisto vicino al tavolo",
            "importance": 0.9,
            "was_successful": 0
        }]
        anom_resp = MAGTemporalParser.format_anomalies(anomalies, current_amygdala_state="CALM")
        assert "fear" in anom_resp
        assert "ostacolo imprevisto" in anom_resp

    def test_handle_temporal_or_special_query_dispatcher(self):
        """Validates unified dispatch for special queries."""
        r1 = MAGTemporalParser.handle_temporal_or_special_query("a che ora parliamo solitamente?")
        assert r1 is not None
        assert "21:00" in r1

        r2 = MAGTemporalParser.handle_temporal_or_special_query("hai visto qualcosa di strano oggi?")
        assert r2 is not None
        assert "anomalie" in r2.lower()

        r_none = MAGTemporalParser.handle_temporal_or_special_query("qual è la capitale della Francia?")
        assert r_none is None


class TestMilestone3SLMMetapromptFusion:
    """Tests email context, family facts grounding, and token budgeting in SLMMetapromptFusion."""

    def test_email_context_attribute_and_injection(self):
        """Validates that SLMPromptContext has email_context and injects it into prompt."""
        fusion = SLMMetapromptFusion()
        ctx = SLMPromptContext(
            user_text="oggi ho ricevuto mail importanti che dovrei conoscere?",
            email_context="- Email VIP da Luisella Bonfanti: riunione scolastica ore 16:30",
        )
        output = fusion.build_prompt_detailed(ctx)
        assert "[NOTIFICHE EMAIL E ORDINI]" in output.chatml_prompt
        assert "Luisella Bonfanti" in output.chatml_prompt
        assert output.section_token_counts["email"] > 0
        assert output.is_within_budget is True

    def test_package_tracking_automatic_incorporation(self, tmp_path, monkeypatch):
        """Validates that queries about packages pull real disk EmailMemory JSON without invented fallback."""
        import json as _json
        email_file = tmp_path / "email_memory.json"
        email_file.write_text(
            _json.dumps({
                "packages": [
                    {
                        "carrier": "Amazon",
                        "tracking_number": "TEST-123",
                        "order_info": "Sensore LiDAR di ricambio",
                        "status": "in_transit",
                    }
                ],
                "stats": {"important_emails": 2, "emails_received": 10},
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("EMAIL_MEMORY_PATH", str(email_file))

        fusion = SLMMetapromptFusion()
        ctx = SLMPromptContext(
            user_text="ho comprato alcune cose in internet, controlla dalle mail se sono arrivate",
        )
        output = fusion.build_prompt_detailed(ctx)
        assert "[NOTIFICHE EMAIL E ORDINI]" in output.chatml_prompt
        assert "Pacchi tracciati" in output.chatml_prompt
        assert "Amazon" in output.chatml_prompt
        assert output.token_estimate < 1100

        # Without cache file, no fabricated Amazon package details must be injected
        monkeypatch.setenv("EMAIL_MEMORY_PATH", str(tmp_path / "missing_email_memory.json"))
        from robopy_controller.robot_ai.trinity.slm_metaprompt_fusion import load_email_delivery_context
        assert load_email_delivery_context(persist_path=str(tmp_path / "missing_email_memory.json")) == ""

    def test_family_facts_grounding(self):
        """Validates that family questions reliably ground wife and 3 kids."""
        fusion = SLMMetapromptFusion()

        # Wife & teaching location
        p1 = fusion.build_prompt("Come si chiama mia moglie e dove insegna?")
        assert "Luisella Bonfanti" in p1
        assert "Colle Brianza" in p1

        # Kids count and youngest
        p2 = fusion.build_prompt("Quanti figli ho e chi è il più piccolo?")
        assert "Isabella" in p2 and "19" in p2
        assert "Edoardo" in p2 and "16" in p2
        assert "Jacopo" in p2 and "12" in p2
        assert "più piccolo" in p2 or "piu piccolo" in p2 or "Jacopo (12 anni, il più piccolo)" in p2

        # Age difference between Isabella and Jacopo
        p3 = fusion.build_prompt("Quanti anni di differenza ci sono tra Isabella e Jacopo?")
        assert "7 anni" in p3

        # Middle child
        p4 = fusion.build_prompt("Se compro un regalo per il mio figlio di mezzo, per chi è?")
        assert "Edoardo" in p4

    def test_strict_token_budget_with_all_sections_under_1100(self):
        """Validates that prompt stays under 1100 tokens ceiling even with family facts and email notifications."""
        fusion = SLMMetapromptFusion()
        ctx = SLMPromptContext(
            user_text="Marcus, controlla la posta per gli ordini Amazon e ricordami chi sono i miei figli.",
            system_prompt="Sei Marcus, robot rover autonomo ed empatico.",
            cag_environment="Room: salotto (near centroid 0.4m) | Map: default",
            cag_hardware="[HW] CPU: 25.0% Temp: 52.0C, RAM: 64.0%, Batt: 12.60V (100%), Autonomia: 5h 30m",
            mag_profile="User: Luca | Role: Creator",
            mag_episodes="- [20/10/2026 21:30] Discussione su navigazione e sensori.\n- [20/10/2026 21:36] Movimento 30cm.",
            email_context="- Pacco Amazon consegnato in giardino a Oggiono.\n- Pacco Cainiao in transito.",
            timestamp="Martedì 20 Ottobre 2026, 21:40",
            intent_category="CONVERSATION",
        )
        output = fusion.build_prompt_detailed(ctx)
        assert output.is_within_budget is True
        assert output.token_estimate < 1100, f"Token estimate {output.token_estimate} exceeded 1100 ceiling!"
        assert "<|im_start|>system" in output.chatml_prompt
        assert "<|im_start|>user" in output.chatml_prompt
