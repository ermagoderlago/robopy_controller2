#!/bin/bash
# scripts/trigger_nightly_dream.sh
# Esegue il trigger del Nightly Dream e ciclo evolutivo su Marcus
set -e

echo "=========================================================="
echo " 🌙 MARCUS - Trigger Anticipato Daily Dream & Evoluzione"
echo "=========================================================="

export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
source /opt/ros/jazzy/setup.bash 2>/dev/null || true
source /home/robopy/ros2_venv/bin/activate 2>/dev/null || true
source /mnt/ssd/robopy_controller_host/install/setup.bash 2>/dev/null || true
source /mnt/ssd/robopy_controller_host/setup_keys.sh 2>/dev/null || true

echo "📡 Iniezione messaggio su /robopy/conversation_rx..."
timeout 3 ros2 topic pub --once -w 0 /robopy/conversation_rx std_msgs/msg/String "{data: 'avvia analisi notturna'}" 2>/dev/null || echo "⚠️ Topic pub inviato (senza attesa prolungata)."

echo ""
echo "🧠 Esecuzione ciclo Daily Focus & Anti-Duplicazione sul motore cognitivo..."
python3 - << 'PYEOF'
import sys
import os
import json
from pathlib import Path

workspace_root = Path("/mnt/ssd/robopy_controller_host")
sys.path.insert(0, str(workspace_root))

from robopy_controller.robot_ai.services.curiosity_evolution_engine import CuriosityEvolutionEngine

engine = CuriosityEvolutionEngine(workspace_root=workspace_root)
print(f"✅ Inizializzato CuriosityEvolutionEngine su {workspace_root}")

# 1. Verifica Failure Modes analizzati di recente
recent = engine.get_recently_analyzed_failure_modes(days=7)
print(f"📋 Failure Modes esclusi per anti-duplicazione (<7gg): {recent}")

# 2. Selezione ed elaborazione Daily Focus Theme
print("🎯 Selezione Daily Focus Theme...")
theme = engine.select_daily_focus_theme()
print(f"✨ Risultato Selezione:\n{json.dumps(theme, indent=2)}")

# 3. Verifica dei file generati
history_path = workspace_root / "docs" / "evolution" / "analysis_history.json"
if history_path.exists():
    with open(history_path, "r", encoding="utf-8") as f:
        hist = json.load(f)
    print(f"📜 Record in analysis_history.json: {len(hist)}")
    if hist:
        print(f"   Ultimo record: ID={hist[-1].get('failure_mode_id')}, Risk={hist[-1].get('risk_zone')}, Status={hist[-1].get('status')}")

plans = list((workspace_root / "docs" / "evolution" / "plans").glob("PLAN-*.md"))
print(f"📁 Piani trovati in docs/evolution/plans/: {len(plans)}")
for p in sorted(plans)[-3:]:
    print(f"   📄 {p.name}")

PYEOF

echo "=========================================================="
echo " ✅ Trigger Daily Dream completato con successo"
echo "=========================================================="
