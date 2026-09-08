#!/usr/bin/env python3
"""
Unit tests for MarcusDataMiner
==============================
Verifica:
1. Inizializzazione schema SQLite e modalità WAL.
2. IDLE GATING MANDATORIO:
   - A navigazione spenta e robot fermo -> Nessuna raccolta dati.
   - Con navigazione attiva o moto -> Raccolta dati attiva a 0.1 Hz.
3. Calcolo metriche real-time (cross-track error, jitter angolare, stop-and-go).
4. Flush transazionale batch su SQLite e consistenza dati.
5. API di sintesi giornaliera ed estrazione colli di bottiglia per l'Orchestratore.
"""

import os
import time
import tempfile
import sqlite3
import pytest
from pathlib import Path

from robopy_controller.robot_ai.services.marcus_data_miner import MarcusDataMiner


@pytest.fixture
def temp_miner():
    """Crea un'istanza di MarcusDataMiner su database temporaneo isolato."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_telemetry.db"
        miner = MarcusDataMiner(db_path=db_path, buffer_limit=10)
        yield miner


def test_sqlite_wal_init(temp_miner):
    """Verifica che il DB sia creato correttamente con journal WAL."""
    assert temp_miner.db_path.exists()
    conn = sqlite3.connect(str(temp_miner.db_path))
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode;")
    mode = cursor.fetchone()[0]
    assert mode.lower() == "wal"
    
    # Verifica esistenza tabelle
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cursor.fetchall()]
    assert "telemetry_samples" in tables
    assert "daily_aggregates" in tables
    conn.close()


def test_idle_gating_behavior(temp_miner):
    """
    Test vincolante richiesto dall'utente:
    A navigazione spenta e robot fermo, NESSUNA RACCOLTA DATI.
    """
    temp_miner.set_navigation_state(False)
    temp_miner.update_motion_state(linear_v=0.0, angular_v=0.0)

    # Robot fermo e navigazione inattiva -> Deve essere IDLE
    assert temp_miner.is_idle() is True

    # sample_tick deve ritornare False e non accumulare nulla nel buffer
    recorded = temp_miner.sample_tick()
    assert recorded is False
    assert len(temp_miner.sample_buffer) == 0

    # 1. Attivazione navigazione Nav2
    temp_miner.set_navigation_state(True)
    assert temp_miner.is_idle() is False
    recorded_nav = temp_miner.sample_tick()
    assert recorded_nav is True
    assert len(temp_miner.sample_buffer) == 1

    # 2. Spegnimento navigazione ma movimento presente
    temp_miner.set_navigation_state(False)
    temp_miner.update_motion_state(linear_v=0.15, angular_v=0.05)
    assert temp_miner.is_idle() is False
    recorded_motion = temp_miner.sample_tick()
    assert recorded_motion is True
    assert len(temp_miner.sample_buffer) == 2


def test_metrics_calculation(temp_miner):
    """Verifica il calcolo di jitter angolare e CTE."""
    # Test calcolo jitter
    for w in [0.0, 0.2, -0.2, 0.1, -0.1]:
        temp_miner.update_motion_state(linear_v=0.1, angular_v=w)
    jitter = temp_miner.calculate_angular_jitter()
    assert jitter > 0.0

    # Test calcolo Cross Track Error con un percorso rettilineo su Y=0 da X=0 a X=10
    path = [(0.0, 0.0), (10.0, 0.0)]
    # Robot si trova a X=5.0, Y=0.3 -> Distanza attesa: 0.3 m
    temp_miner.update_cross_track_error(robot_x=5.0, robot_y=0.3, path_poses=path)
    assert pytest.approx(temp_miner.latest_cte, rel=1e-2) == 0.3


def test_batch_flush_to_sqlite(temp_miner):
    """Verifica il salvataggio atomico dei campioni nel database."""
    temp_miner.set_navigation_state(True)
    for i in range(5):
        temp_miner.update_motion_state(linear_v=0.1, angular_v=0.01 * i)
        temp_miner.sample_tick(event_tag="TEST")

    assert len(temp_miner.sample_buffer) == 5

    # Esecuzione flush
    written = temp_miner.flush_to_sqlite()
    assert written == 5
    assert len(temp_miner.sample_buffer) == 0

    # Verifica righe in SQLite
    conn = sqlite3.connect(str(temp_miner.db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM telemetry_samples;")
    count = cursor.fetchone()[0]
    assert count == 5
    conn.close()


def test_daily_summary_and_bottlenecks(temp_miner):
    """Verifica il riepilogo giornaliero e la rilevazione automatica di colli di bottiglia."""
    temp_miner.set_navigation_state(True)
    temp_miner.latest_cte = 0.25  # Errore alto (> 0.15m)
    # Popoliamo velocita angolari discordanti per generare jitter > 0.30 rad/s
    temp_miner.recent_angular_velocities.extend([0.0, 0.4, -0.4, 0.5, -0.5])
    temp_miner.recovery_event_count = 2
    
    # Simuliamo un urto (accelerazione > 1.5G => ~14.7 m/s^2)
    temp_miner.update_imu_state(accel_x=16.0, accel_y=0.0, accel_z=9.81)
    
    temp_miner.sample_tick(event_tag="TEST_ANOMALY")
    temp_miner.flush_to_sqlite()

    # Query summary odierno
    summary = temp_miner.get_daily_summary()
    assert summary["total_samples"] >= 1
    assert summary["max_cross_track_error"] >= 0.25
    assert summary["total_collision_events"] == 1
    assert summary["max_linear_accel"] > 1.5

    # Rilevamento colli di bottiglia per l'Orchestratore
    bottlenecks = temp_miner.get_unmitigated_bottlenecks(days=1)
    subsystems = [b["subsystem"] for b in bottlenecks]
    assert "Nav2" in subsystems
    assert "Attuazione" in subsystems
    assert "Sicurezza/Cinematica" in subsystems
