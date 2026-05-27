#!/bin/bash
# restart.sh — Riavvio rapido AI + VUI node
# [v12.0] Aggiunto restart VUI con parametri auto-calibrazione microfono

# --- Setup ambiente ---
source /home/robopy/ros2_jazzy/install/setup.bash
source /home/robopy/ros2_venv/bin/activate
source /mnt/ssd/robopy_controller_host/install/setup.bash
source /mnt/ssd/robopy_controller_host/setup_keys.sh
export ROS_DOMAIN_ID=42
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
export PYTHONUNBUFFERED=1

# --- Kill nodi precedenti ---
echo "🔴 Stopping robot_ai_node..."
pkill -f robot_ai_node || true
sleep 1
pkill -9 -f robot_ai_node || true

echo "🔴 Stopping respeaker_vui_node..."
pkill -f respeaker_vui_node || true
sleep 1
pkill -9 -f respeaker_vui_node || true

echo "🔴 Stopping respeaker_interface_node..."
pkill -f respeaker_interface_node || true
sleep 1
pkill -9 -f respeaker_interface_node || true

sleep 2

# --- Riavvio ReSpeaker Interface Node (Hardware Serial Bridge) ---
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

# --- Riavvio VUI Node [v12.0] con auto-calibrazione microfono ---
echo "🎤 Starting respeaker_vui_node (v12.0 adaptive)..."
> /home/robopy/robopy/logs/respeaker_vui_node.log
nohup ros2 run robopy_controller respeaker_vui_node --ros-args \
    -r __node:=respeaker_vui_node \
    -p use_sim_time:=False \
    -p stt_gain:=4.0 \
    -p noise_gate_threshold:=100.0 \
    -p wakeword_sensitivity:=0.92 \
    -p enable_barge_in:=true \
    -p barge_in_min_tts_ms:=2500.0 \
    -p barge_in_min_frames:=12 \
    -p enable_adaptive_threshold:=true \
    -p enable_adaptive_silence:=true \
    -p playback_volume:=0.10 \
    -p enable_auto_volume:=true \
    -p diag_mode:=true \
    > /home/robopy/robopy/logs/respeaker_vui_node.log 2>&1 &

# --- Riavvio AI Orchestrator ---
echo "🤖 Starting robot_ai_node..."
> /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log
nohup ros2 run robopy_controller robot_ai_node \
    --ros-args -r /ai/input/text:=/robopy/conversation_rx \
    > /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log 2>&1 &

echo "✅ Nodi riavviati!"
echo "   Interface log: tail -f /home/robopy/robopy/logs/respeaker_interface_node.log"
echo "   VUI log:       tail -f /home/robopy/robopy/logs/respeaker_vui_node.log"
echo "   AI  log:       tail -f /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log"

