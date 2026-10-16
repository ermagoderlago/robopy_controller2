#!/usr/bin/env python3
"""
scripts/run_daily_focus_test.py
===============================
Script di verifica per il ciclo Daily Focus & Auto-evoluzione di Marcus.
Esegue:
1. Inizializzazione CuriosityEvolutionEngine su /mnt/ssd/robopy_controller_host
2. Verifica Failure Mode esclusi negli ultimi 7 giorni
3. Selezione ed elaborazione Daily Focus Theme (anti-duplicazione, pianificazione, classificazione rischio)
4. Stampa dettagliata dei risultati e verifica dei file persistiti
"""

import sys
import os
import json
from pathlib import Path

# Configura path per importare robopy_controller
if os.path.exists("/mnt/ssd/robopy_controller_host"):
    WORKSPACE_ROOT = Path("/mnt/ssd/robopy_controller_host")
else:
    WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(WORKSPACE_ROOT))

# Carica .env se presente
env_file = WORKSPACE_ROOT / ".env"
if env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(str(env_file), override=True)
        print(f"✅ .env caricato da {env_file}")
    except Exception as e:
        print(f"⚠️ dotenv error: {e}")

from robopy_controller.robot_ai.services.curiosity_evolution_engine import CuriosityEvolutionEngine

def main():
    print("=" * 70)
    print(" 🤖 MARCUS - VERIFICA DAILY FOCUS & MOTORE EVOLUTIVO")
    print("=" * 70)
    print(f"📁 Workspace Root: {WORKSPACE_ROOT}")

    engine = CuriosityEvolutionEngine(workspace_root=WORKSPACE_ROOT)

    # 1. Failure Modes analizzati di recente
    print("\n🔍 [1/4] Verifica Failure Modes analizzati di recente (anti-duplicazione)...")
    recent = engine.get_recently_analyzed_failure_modes(days=7)
    print(f"   Esclusi (ultimi 7 giorni): {recent}")

    # 2. Top Failure Modes aperti disponibili
    print("\n📋 [2/4] Failure Modes aperti prioritari (escludendo i recenti)...")
    top_fms = engine.get_top_priority_failure_modes(limit=3, exclude_ids=recent)
    for i, fm in enumerate(top_fms, 1):
        print(f"   {i}. ID: {fm.get('id')} | Subsystem: {fm.get('subsystem')} | Guasto: {fm.get('failure_mode')[:60]}... | RPN: {fm.get('residual_scoring', {}).get('rpn', fm.get('initial_scoring', {}).get('rpn'))}")

    # 3. Selezione Daily Focus
    print("\n🎯 [3/4] Esecuzione select_daily_focus_theme()...")
    theme = engine.select_daily_focus_theme()
    print("\n✨ Risultato Daily Focus generato:")
    print(f"   Titolo:             {theme.get('title')}")
    print(f"   Sottosistema:       {theme.get('subsystem')}")
    print(f"   Failure Mode ID:    {theme.get('failure_mode_id')}")
    print(f"   Risk Zone:          {theme.get('risk_zone')}")
    print(f"   Stato Esecuzione:   {theme.get('execution_status')}")
    print(f"   Piano Generato:     {theme.get('plan_file')}")
    print(f"   Descrizione Piano:  {theme.get('plan_summary', '')[:100]}...")

    # 4. Verifica persistenza
    print("\n💾 [4/4] Verifica persistenza su disco...")
    history_file = WORKSPACE_ROOT / "docs" / "evolution" / "analysis_history.json"
    if history_file.exists():
        with open(history_file, "r", encoding="utf-8") as f:
            history = json.load(f)
        print(f"   ✅ analysis_history.json presente ({len(history)} record)")
        if history:
            last = history[-1]
            print(f"      Ultimo record salvato: FM={last.get('failure_mode_id')}, Risk={last.get('risk_zone')}, Status={last.get('status')}")
    else:
        print("   ❌ analysis_history.json NON trovato!")

    if theme.get("plan_file"):
        plan_path = WORKSPACE_ROOT / theme["plan_file"]
        if plan_path.exists():
            print(f"   ✅ File piano creato con successo: {plan_path.name} ({plan_path.stat().st_size} byte)")
        else:
            print(f"   ⚠️ File piano non trovato al percorso: {plan_path}")

    print("\n" + "=" * 70)
    print(" ✅ VERIFICA COMPLETATA CON SUCCESSO!")
    print("=" * 70)

if __name__ == "__main__":
    main()
