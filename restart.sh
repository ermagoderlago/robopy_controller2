#!/bin/bash
pkill -f robot_ai_node
sleep 1
pkill -9 -f robot_ai_node
source /home/robopy/ros2_jazzy/install/setup.bash
source install/setup.bash
export PYTHONUNBUFFERED=1
> /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log
nohup ros2 run robopy_controller robot_ai_node > /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log 2>&1 &
echo "Node restarted"
