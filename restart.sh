#!/bin/bash
# restart.sh — Riavvio rapido AI + VUI node
# [v12.0] Aggiunto restart VUI con parametri auto-calibrazione microfono

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

# --- Kill nodi precedenti ---
pkill -9 -f waveshare_motor_driver || true

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

echo "🔴 Stopping foxglove_bridge..."
pkill -f foxglove_bridge || true
sleep 1
pkill -9 -f foxglove_bridge || true

echo "🔴 Stopping foxglove_nav2_bridge..."
pkill -f foxglove_nav2_bridge || true
sleep 1
pkill -9 -f foxglove_nav2_bridge || true

sleep 2

# --- Riavvio Waveshare Motor Driver ---
echo "⚙️ Starting waveshare_motor_driver..."
> /home/robopy/robopy/logs/waveshare_motor_driver.log
nohup ros2 run robopy_controller waveshare_motor_driver --ros-args \
    -p serial_port:=/dev/ttyUSB0 \
    -p baud_rate:=115200 \
    -p wheel_radius:=0.0325 \
    -p wheel_separation:=0.29 \
    -p ticks_per_rev:=70 \
    -p invert_left_motor:=False \
    -p invert_right_motor:=False \
    -p invert_left_encoder:=False \
    -p invert_right_encoder:=False \
    -p publish_tf:=True \
    > /home/robopy/robopy/logs/waveshare_motor_driver.log 2>&1 &

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
    -p stt_gain:=18.0 \
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

# --- Riavvio AI Orchestrator ---
echo "🤖 Starting robot_ai_node..."
> /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log
nohup ros2 run robopy_controller robot_ai_node \
    --ros-args -r /ai/input/text:=/robopy/conversation_rx \
    > /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log 2>&1 &

# --- Riavvio Foxglove Bridge & Nav2 Bridge ---
echo "🔌 Starting foxglove_bridge..."
> /home/robopy/robopy/logs/foxglove_bridge.log
nohup ros2 run foxglove_bridge foxglove_bridge --ros-args \
    -p port:=8765 \
    > /home/robopy/robopy/logs/foxglove_bridge.log 2>&1 &

echo "🌉 Starting foxglove_nav2_bridge..."
> /home/robopy/robopy/logs/foxglove_nav2_bridge.log
nohup ros2 run robopy_controller foxglove_nav2_bridge \
    > /home/robopy/robopy/logs/foxglove_nav2_bridge.log 2>&1 &

echo "✅ Nodi riavviati!"
echo "   Interface log: tail -f /home/robopy/robopy/logs/respeaker_interface_node.log"
echo "   VUI log:       tail -f /home/robopy/robopy/logs/respeaker_vui_node.log"
echo "   AI  log:       tail -f /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log"
echo "   Foxglove log:  tail -f /home/robopy/robopy/logs/foxglove_bridge.log"
echo "   Foxglove Nav:  tail -f /home/robopy/robopy/logs/foxglove_nav2_bridge.log"

if [ -z "$FROM_WATCHDOG" ]; then
    echo "🟢 Riattivazione del Watchdog..."
    sudo systemctl start marcus-watchdog.service || true
fi

