#!/bin/bash
# save_map.sh — Salva la mappa 2D attiva (/map) in formato standard Nav2 YAML+PGM
# Utilizzo: ./scripts/save_map.sh [nome_mappa] (default: salotto)

set -e

MAP_NAME="${1:-piano_terra}"
MAP_DIR="/mnt/ssd/maps"


mkdir -p "$MAP_DIR"

echo "🗺️ Esportazione mappa 2D attiva da /map..."
echo "📁 Destinazione: ${MAP_DIR}/${MAP_NAME}.yaml e ${MAP_DIR}/${MAP_NAME}.pgm"

# Verifica ambiente ROS 2
source /home/robopy/ros2_jazzy/install/setup.bash 2>/dev/null || true
source /home/robopy/ros2_venv/bin/activate 2>/dev/null || true
source /mnt/ssd/robopy_controller_host/install/setup.bash 2>/dev/null || true
export ROS_DOMAIN_ID=42
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml

# Esegue map_saver_cli
ros2 run nav2_map_server map_saver_cli \
    -f "${MAP_DIR}/${MAP_NAME}" \
    --ros-args -p map_subscribe_transient_local:=true

if [ -f "${MAP_DIR}/${MAP_NAME}.yaml" ]; then
    echo "✅ Mappa '${MAP_NAME}' esportata con successo in ${MAP_DIR}!"
    
    # Salva la posa attuale del robot nel frame map per Pose Persistence
    echo "📍 Salvataggio posa attuale del robot per AMCL..."
    python3 /mnt/ssd/robopy_controller_host/scripts/save_current_pose.py "${MAP_DIR}/${MAP_NAME}_pose.yaml" || true

    echo "   Puoi ora avviare Marcus in modalità Opzione A (AMCL 2D) con:"
    echo "   ./restart_hailo.sh --amcl --map=${MAP_DIR}/${MAP_NAME}.yaml"
else
    echo "❌ Errore durante l'esportazione della mappa."
    exit 1
fi
