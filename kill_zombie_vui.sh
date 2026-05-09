#!/bin/bash
# kill_zombie_vui.sh
# Uccide tutti i processi respeaker_vui_node e robot_ai_node orfani
# (quelli NON avviati dall'attuale sessione ros2 launch)
echo "=== Nodi prima del kill ==="
ps aux | grep -E 'robot_ai_node|respeaker_vui' | grep -v grep

echo ""
echo "=== Killing zombie VUI (PID non appartenente al launch corrente) ==="
# Prende tutti i PID di respeaker_vui_node
PIDS=$(pgrep -f respeaker_vui_node)
COUNT=$(echo "$PIDS" | wc -w)

if [ "$COUNT" -gt 1 ]; then
    # Ordina per tempo avvio (il più vecchio è il zombie)
    OLDEST=$(ps -o pid= --sort=start_time -p $PIDS | head -1 | tr -d ' ')
    echo "Processo più vecchio (zombie): PID $OLDEST — sto killando..."
    kill -9 $OLDEST
    sleep 1
    echo "Fatto."
else
    echo "Solo un processo respeaker_vui_node trovato (PID: $PIDS), nessun zombie."
fi

echo ""
echo "=== Nodi dopo il kill ==="
ps aux | grep -E 'robot_ai_node|respeaker_vui' | grep -v grep
