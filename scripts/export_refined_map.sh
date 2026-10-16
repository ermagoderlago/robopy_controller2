#!/bin/bash
# export_refined_map.sh — Rigenerazione della mappa 2D Occupancy Grid dal database RTAB-Map ottimizzato
set -e

source /home/robopy/ros2_jazzy/install/setup.bash
source /mnt/ssd/robopy_controller_host/install/setup.bash 2>/dev/null || true
export ROS_DOMAIN_ID=42
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
export QT_QPA_PLATFORM=offscreen

DB_PATH="${1:-/mnt/ssd/maps/pianoterra2_opt.db}"
MAP_NAME="${2:-pianoterra2_opt}"
MAP_DIR="/mnt/ssd/maps"

mkdir -p "$MAP_DIR"

echo "🗺️ Generazione params override temporaneo in /tmp/export_rtabmap.yaml..."
cat << 'EOF' > /tmp/export_rtabmap.yaml
rtabmap:
  ros__parameters:
    publish_tf: false
    map_always_update: true
    Mem/IncrementalMemory: "false"
    Grid/Sensor: "0"
    Grid/FromDepth: "false"
    Grid/3D: "false"
EOF

# Aggiunge database_path dinamico
echo "    database_path: \"$DB_PATH\"" >> /tmp/export_rtabmap.yaml

echo "🗺️ Avvio RTAB-Map per rigenerazione /map da $DB_PATH..."
pkill -9 -f rtabmap 2>/dev/null || true
sleep 1

nohup ros2 run rtabmap_slam rtabmap --ros-args \
    --params-file /mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/rtabmap.yaml \
    --params-file /tmp/export_rtabmap.yaml \
    > /tmp/rtabmap_export_temp.log 2>&1 &

echo "⏳ Attesa caricamento grafo e pubblicazione /map (10 secondi)..."
sleep 10

echo "📸 Cattura mappa pulita con map_saver_cli..."
ros2 run nav2_map_server map_saver_cli \
    -f "${MAP_DIR}/${MAP_NAME}" \
    --ros-args -p map_subscribe_transient_local:=true

pkill -9 -f rtabmap 2>/dev/null || true

echo "✅ Mappa rigenerata:"
ls -lh "${MAP_DIR}/${MAP_NAME}"*
