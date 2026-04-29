#!/bin/bash
source /mnt/ssd/robopy_controller_host/setup_keys.sh
source ~/ros2_venv/bin/activate
source /mnt/ssd/robopy_controller_host/install/setup.bash

# Listen for 15 seconds in background
ros2 topic echo /ai/conversation/response std_msgs/msg/String > /tmp/ai_resp.log &
LISTENER_PID=$!

sleep 2

# Publish message
ros2 topic pub /robopy/conversation_rx std_msgs/msg/String "{data: 'leggi le email'}" --once

# Wait for LLM to process and answer
sleep 15
kill $LISTENER_PID
cat /tmp/ai_resp.log
