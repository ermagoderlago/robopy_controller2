#!/bin/bash
pkill -9 -f robot_ai_node || true
pkill -9 -f respeaker_vui_node || true
sleep 2

source /home/robopy/ros2_jazzy/install/setup.bash
source /home/robopy/ros2_venv/bin/activate
source /mnt/ssd/robopy_controller_host/install/setup.bash
source /mnt/ssd/robopy_controller_host/setup_keys.sh
export LD_LIBRARY_PATH=/mnt/ssd/robopy_controller_host/install/robopy_controller/lib:/mnt/ssd/robopy_controller_host/build/robopy_controller:$LD_LIBRARY_PATH
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml

echo "🎤 Avvio respeaker_vui_node con watchdog auto-recovery..."
> /home/robopy/robopy/logs/respeaker_vui_node.log
nohup ros2 run robopy_controller respeaker_vui_node --ros-args \
    -r __node:=respeaker_vui_node \
    -p use_sim_time:=False \
    -p stt_gain:=1.8 \
    -p noise_gate_threshold:=120.0 \
    -p listen_timeout_sec:=8.0 \
    -p wakeword_sensitivity:=0.95 \
    -p enable_barge_in:=true \
    -p barge_in_min_tts_ms:=2500.0 \
    -p barge_in_min_frames:=10 \
    -p enable_adaptive_threshold:=true \
    -p enable_adaptive_silence:=true \
    -p playback_volume:=0.08 \
    -p enable_auto_volume:=false \
    -p enable_audio_beeps:=true \
    -p diag_mode:=true \
    > /home/robopy/robopy/logs/respeaker_vui_node.log 2>&1 &

sleep 2

echo "🤖 Avvio robot_ai_node con Live WebSocket timeout watchdog..."
> /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log
nohup nice -n 5 ros2 run robopy_controller robot_ai_node \
    > /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log 2>&1 &

sleep 3
ps -ef | grep -E 'robot_ai_node|respeaker_vui_node' | grep -v grep
