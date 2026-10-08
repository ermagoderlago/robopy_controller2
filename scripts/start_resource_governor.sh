#!/bin/bash
# =============================================================================
# start_resource_governor.sh — Avvio daemon Resource Governor in Shadow Mode
# =============================================================================
source /home/robopy/ros2_jazzy/install/setup.bash
source /home/robopy/ros2_venv/bin/activate
source /mnt/ssd/robopy_controller_host/install/setup.bash
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml

pkill -9 -f resource_governor_node || true
sleep 1

mkdir -p /home/robopy/robopy/logs
> /home/robopy/robopy/logs/resource_governor_node.log

nohup ros2 run robopy_controller resource_governor_node --ros-args \
    -p shadow_mode:=true \
    > /home/robopy/robopy/logs/resource_governor_node.log 2>&1 &

sleep 2
echo "✅ Resource Governor Node avviato in background con PID $(pgrep -f resource_governor_node)"
