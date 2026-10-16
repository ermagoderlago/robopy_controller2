#!/bin/bash
# export_map_with_live_scan.sh — Esportazione della mappa 2D (YAML + PGM) con coordinate e origin ufficiali Nav2
set -e

source /home/robopy/ros2_jazzy/install/setup.bash
source /mnt/ssd/robopy_controller_host/install/setup.bash 2>/dev/null || true
export ROS_DOMAIN_ID=42
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
export QT_QPA_PLATFORM=offscreen

MAP_NAME="${1:-pianoterra2_opt}"
DB_PATH="${2:-/mnt/ssd/maps/pianoterra2_final.db}"
MAP_DIR="/mnt/ssd/maps"

mkdir -p "$MAP_DIR"

echo "🛑 Arresto eventuali nodi pendenti..."
pkill -9 -f rtabmap 2>/dev/null || true
pkill -9 -f sllidar_node 2>/dev/null || true
sleep 1

echo "📡 Avvio temporaneo Slamtec RPLIDAR C1 su /dev/rplidar..."
source /home/robopy/lidar_ws/install/setup.bash 2>/dev/null || true
nohup ros2 run sllidar_ros2 sllidar_node --ros-args \
    -p channel_type:=serial \
    -p serial_port:=/dev/rplidar \
    -p serial_baudrate:=460800 \
    -p frame_id:=laser \
    -p inverted:=false \
    -p angle_compensate:=true \
    -p scan_mode:=Standard \
    > /tmp/sllidar_temp.log 2>&1 &
sleep 4

echo "🗺️ Generazione params override temporaneo in /tmp/export_rtabmap.yaml..."
cat << EOF > /tmp/export_rtabmap.yaml
rtabmap:
  ros__parameters:
    database_path: "$DB_PATH"
    publish_tf: false
    map_always_update: true
    Mem/IncrementalMemory: "false"
    Grid/Sensor: "0"
    Grid/FromDepth: "false"
    Grid/3D: "false"
EOF

echo "🗺️ Avvio RTAB-Map per assemblaggio e pubblicazione /map da grafo ottimizzato..."
nohup ros2 run rtabmap_slam rtabmap --ros-args \
    --params-file /mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/rtabmap.yaml \
    --params-file /tmp/export_rtabmap.yaml \
    -r scan:=/scan \
    > /tmp/rtabmap_export_live.log 2>&1 &

echo "⏳ Attesa pubblicazione /map da grafo ottimizzato (15 secondi)..."
sleep 15

echo "📸 Cattura mappa e origin YAML con map_saver_cli..."
ros2 run nav2_map_server map_saver_cli \
    -f "${MAP_DIR}/${MAP_NAME}" \
    --ros-args -p map_subscribe_transient_local:=true -p save_map_timeout:=30.0 -p free_thresh:=0.25 -p occupied_thresh:=0.65

pkill -9 -f rtabmap 2>/dev/null || true
pkill -9 -f sllidar_node 2>/dev/null || true

echo "✅ Mappa e YAML esportati con successo:"
ls -lh "${MAP_DIR}/${MAP_NAME}"*
