#!/bin/bash
# launch_ai.sh - Launcher dedicato per robot_ai_node
source /home/robopy/ros2_jazzy/install/setup.bash
source /home/robopy/ros2_venv/bin/activate
source /mnt/ssd/robopy_controller_host/install/setup.bash
source /mnt/ssd/robopy_controller_host/setup_keys.sh
export ROS_DOMAIN_ID=42
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
export PYTHONUNBUFFERED=1

echo "🤖 Avvio robot_ai_node..."
exec ros2 run robopy_controller robot_ai_node
