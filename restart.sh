#!/bin/bash
# Script per il riavvio completo del sistema Marcus AI con ambiente virtuale
# [v3.0] Unificato caricamento environment via setup_keys.sh

SESSION_NAME="marcus"
WORKSPACE_PATH="/mnt/ssd/robopy_controller_host"

echo "🛑 Arrestando processi Marcus AI esistenti..."
tmux kill-session -t $SESSION_NAME 2>/dev/null

# [v4.0] Kill forzato di qualsiasi processo zombie rimasto fuori dalla sessione tmux.
# Questo previene il bug del "VUI duplicato" dove un processo respeaker_vui_node
# avviato in una sessione precedente rimane in background e compete sui topic ROS 2.
echo "🧹 Pulizia processi orfani (zombie guard)..."
pkill -9 -f robot_ai_node 2>/dev/null || true
pkill -9 -f respeaker_vui_node 2>/dev/null || true
sleep 1

echo "🚀 Avviando nuova sessione TMUX: $SESSION_NAME"
tmux new-session -d -s $SESSION_NAME

# Preparazione ambiente nella sessione tmux
tmux send-keys -t $SESSION_NAME "cd $WORKSPACE_PATH" C-m
tmux send-keys -t $SESSION_NAME "source /mnt/ssd/ros2_jazzy/install/setup.bash" C-m
tmux send-keys -t $SESSION_NAME "source /home/robopy/ros2_venv/bin/activate" C-m
tmux send-keys -t $SESSION_NAME "source install/setup.bash" C-m

# Caricamento chiavi API (da .env tramite setup_keys.sh)
tmux send-keys -t $SESSION_NAME "source $WORKSPACE_PATH/setup_keys.sh" C-m

tmux send-keys -t $SESSION_NAME "export ROS_DOMAIN_ID=42" C-m
tmux send-keys -t $SESSION_NAME "export PYTHONUNBUFFERED=1" C-m

# LANCIO DEL SISTEMA COMPLETO
tmux send-keys -t $SESSION_NAME "ros2 launch robopy_controller robot_ia_launch.py" C-m

echo "✅ Sistema Marcus AI (Launch) avviato con successo!"
echo "👉 Per monitorare scrivi: tmux attach -t $SESSION_NAME"
