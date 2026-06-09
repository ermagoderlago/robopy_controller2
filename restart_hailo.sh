#!/bin/bash
# restart_hailo.sh — Riavvio rapido dell'infrastruttura AI locale con Hailo-10H NPU
# [v1.0] Gestione nodi NPU, VUI adattiva, e C++ semantic mapper

# --- Gestione Watchdog di Sopravvivenza ---
if [ -z "$FROM_WATCHDOG" ]; then
    echo "🛑 Disattivazione temporanea del Watchdog per riavvio manuale..."
    sudo systemctl stop marcus-watchdog.service || true
fi

# --- Setup ambiente ---
source /home/robopy/ros2_jazzy/install/setup.bash
source /home/robopy/ros2_venv/bin/activate
source /mnt/ssd/robopy_controller_host/install/setup.bash
source /mnt/ssd/robopy_controller_host/setup_keys.sh
export ROS_DOMAIN_ID=42

if [ ! -f /tmp/cyclonedds_robopy.xml ]; then
    echo "📄 Generating /tmp/cyclonedds_robopy.xml..."
    cat << 'EOF' > /tmp/cyclonedds_robopy.xml
<?xml version="1.0" encoding="UTF-8" ?>
<CycloneDDS xmlns="https://cdds.io/config" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="https://cdds.io/config https://raw.githubusercontent.com/eclipse-cyclonedds/cyclonedds/master/etc/cyclonedds.xsd">
    <Domain id="any">
        <Discovery>
            <MaxAutoParticipantIndex>200</MaxAutoParticipantIndex>
        </Discovery>
    </Domain>
</CycloneDDS>
EOF
fi
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
export PYTHONUNBUFFERED=1

# --- Kill nodi precedenti (AI & Hailo NPU) ---
echo "🔴 Stopping robot_ai_node..."
pkill -f robot_ai_node || true
pkill -9 -f robot_ai_node || true

echo "🔴 Stopping respeaker_vui_node..."
pkill -f respeaker_vui_node || true
pkill -9 -f respeaker_vui_node || true

echo "🔴 Stopping respeaker_interface_node..."
pkill -f respeaker_interface_node || true
pkill -9 -f respeaker_interface_node || true

echo "🔴 Stopping hailo_bridge_node..."
pkill -f hailo_bridge_node || true
pkill -9 -f hailo_bridge_node || true

echo "🔴 Stopping marcus_semantic_mapper_cpp..."
pkill -f marcus_semantic_mapper_cpp || true
pkill -9 -f marcus_semantic_mapper_cpp || true

echo "🔴 Stopping semantic_costmap_injector..."
pkill -f semantic_costmap_injector || true
pkill -9 -f semantic_costmap_injector || true

echo "🔴 Stopping engagement_monitor..."
pkill -f engagement_monitor || true
pkill -9 -f engagement_monitor || true

echo "🔴 Stopping cloud_watchdog_node..."
pkill -f cloud_watchdog_node || true
pkill -9 -f cloud_watchdog_node || true

echo "🔴 Stopping speaker_id_node..."
pkill -f speaker_id_node || true
pkill -9 -f speaker_id_node || true

echo "🔴 Stopping foxglove_bridge..."
pkill -f foxglove_bridge || true
pkill -9 -f foxglove_bridge || true

echo "🔴 Stopping foxglove_nav2_bridge..."
pkill -f foxglove_nav2_bridge || true
pkill -9 -f foxglove_nav2_bridge || true

sleep 2

# --- Avvio Nodi Audio & VUI Adattiva ---
echo "🔌 Starting respeaker_interface_node..."
> /home/robopy/robopy/logs/respeaker_interface_node.log
nohup ros2 run robopy_controller respeaker_interface_node --ros-args \
    -p uart_port:=/dev/ttyACM0 \
    -p uart_baud:=921600 \
    -p enabled:=True \
    -p default_volume:=5 \
    -p enable_aec:=True \
    -p enable_agc:=False \
    -p enable_ns:=True \
    > /home/robopy/robopy/logs/respeaker_interface_node.log 2>&1 &

echo "🎤 Starting respeaker_vui_node (v12.0 adaptive)..."
> /home/robopy/robopy/logs/respeaker_vui_node.log
nohup ros2 run robopy_controller respeaker_vui_node --ros-args \
    -r __node:=respeaker_vui_node \
    -p use_sim_time:=False \
    -p stt_gain:=5.0 \
    -p noise_gate_threshold:=100.0 \
    -p wakeword_sensitivity:=0.95 \
    -p enable_barge_in:=true \
    -p barge_in_min_tts_ms:=2500.0 \
    -p barge_in_min_frames:=8 \
    -p enable_adaptive_threshold:=true \
    -p enable_adaptive_silence:=true \
    -p playback_volume:=0.10 \
    -p enable_auto_volume:=true \
    -p diag_mode:=true \
    > /home/robopy/robopy/logs/respeaker_vui_node.log 2>&1 &

# --- Avvio Infrastruttura Hailo NPU ---
echo "🧠 Starting hailo_bridge_node (NPU Driver)..."
> /home/robopy/robopy/logs/hailo_bridge_node.log
nohup ros2 run robopy_controller hailo_bridge_node --ros-args \
    -p hef_path:=/mnt/ssd/models/marcus_unified.hef \
    -p sim_mode:=False \
    > /home/robopy/robopy/logs/hailo_bridge_node.log 2>&1 &

echo "🗣️ Starting speaker_id_node (Biometric Verifier)..."
> /home/robopy/robopy/logs/speaker_id_node.log
nohup ros2 run robopy_controller speaker_id_node --ros-args \
    -p speaker_hef_path:=/mnt/ssd/models/ecapa_tdnn.hef \
    > /home/robopy/robopy/logs/speaker_id_node.log 2>&1 &

echo "🗺️ Starting marcus_semantic_mapper_cpp (Visual Fusion C++)..."
> /home/robopy/robopy/logs/marcus_semantic_mapper.log
nohup ros2 run robopy_controller marcus_semantic_mapper_cpp --ros-args \
    -p publish_debug:=True \
    > /home/robopy/robopy/logs/marcus_semantic_mapper.log 2>&1 &

echo "🧱 Starting semantic_costmap_injector..."
> /home/robopy/robopy/logs/semantic_costmap_injector.log
nohup ros2 run robopy_controller semantic_costmap_injector \
    > /home/robopy/robopy/logs/semantic_costmap_injector.log 2>&1 &

echo "👥 Starting engagement_monitor (HRI Gaze/Prossemic)..."
> /home/robopy/robopy/logs/engagement_monitor.log
nohup ros2 run robopy_controller engagement_monitor \
    > /home/robopy/robopy/logs/engagement_monitor.log 2>&1 &

echo "🌐 Starting cloud_watchdog_node (Local NPU vs Cloud Fallback)..."
> /home/robopy/robopy/logs/cloud_watchdog_node.log
nohup ros2 run robopy_controller cloud_watchdog_node \
    > /home/robopy/robopy/logs/cloud_watchdog_node.log 2>&1 &

# --- Avvio AI Orchestrator ---
echo "🤖 Starting robot_ai_node (Cognitive Orchestrator)..."
> /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log
nohup ros2 run robopy_controller robot_ai_node \
    --ros-args -r /ai/input/text:=/robopy/conversation_rx \
    > /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log 2>&1 &

# --- Avvio Bridges Diagnostici (Foxglove) ---
echo "🔌 Starting foxglove_bridge..."
> /home/robopy/robopy/logs/foxglove_bridge.log
nohup ros2 run foxglove_bridge foxglove_bridge --ros-args \
    -p port:=8765 \
    > /home/robopy/robopy/logs/foxglove_bridge.log 2>&1 &

echo "🌉 Starting foxglove_nav2_bridge..."
> /home/robopy/robopy/logs/foxglove_nav2_bridge.log
nohup ros2 run robopy_controller foxglove_nav2_bridge \
    > /home/robopy/robopy/logs/foxglove_nav2_bridge.log 2>&1 &

echo "✅ Nodi AI locali & Hailo NPU riavviati!"
echo "   Hailo log:     tail -f /home/robopy/robopy/logs/hailo_bridge_node.log"
echo "   Mapper C++ log:tail -f /home/robopy/robopy/logs/marcus_semantic_mapper.log"
echo "   VUI log:       tail -f /home/robopy/robopy/logs/respeaker_vui_node.log"
echo "   AI  log:       tail -f /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log"

if [ -z "$FROM_WATCHDOG" ]; then
    echo "🟢 Riattivazione del Watchdog..."
    sudo systemctl start marcus-watchdog.service || true
fi
