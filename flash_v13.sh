#!/bin/bash
# flash_v13.sh - Automates stopping nodes, compiling, flashing LED firmware, and restarting.
set -e

echo "=================================================="
echo " 🎤 ReSpeaker Lite LED v13 — Automated Flash Tool"
echo "=================================================="

echo "🛑 [1/4] Stopping robot_ai_node and respeaker_vui_node..."
pkill -f robot_ai_node || true
pkill -f respeaker_vui_node || true
sleep 2

# Force kill any stubborn remnants
pkill -9 -f robot_ai_node || true
pkill -9 -f respeaker_vui_node || true
sleep 1
echo "✅ Nodes stopped."

echo "🔌 [2/4] Activating ESPHome virtual environment..."
source /home/robopy/esphome_venv/bin/activate

echo "🛠️ [3/4] Compiling and flashing ReSpeaker Lite LED firmware..."
cd /mnt/ssd/robopy_controller_host/robopy_controller/files_utili
esphome run respeaker_lite_firmware_led_v13.yaml --device /dev/ttyACM0
echo "✅ Firmware compiled and flashed successfully!"

echo "🔄 [4/4] Restarting ROS 2 nodes..."
cd /mnt/ssd/robopy_controller_host
bash restart.sh

echo "=================================================="
echo " 🎉 SUCCESS! ReSpeaker Lite LED v13 is now active!"
echo "=================================================="
