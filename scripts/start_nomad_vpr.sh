#!/bin/bash

source /home/robopy/ros2_jazzy/install/setup.bash
source /home/robopy/ros2_venv/bin/activate
source /mnt/ssd/robopy_controller_host/install/setup.bash
export ROS_DOMAIN_ID=42
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
export PYTHONUNBUFFERED=1

pkill -9 -f nomad_reactive_pipeline_node >/dev/null 2>&1 || true
pkill -9 -f vpr_topological_graph_node >/dev/null 2>&1 || true
sleep 1

nohup python3 -u /mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/nodes/nomad_reactive_pipeline_node.py \
    --ros-args -p cmd_vel_topic:=/cmd_vel -p image_topic:=/rgb/image \
    </dev/null > /home/robopy/robopy/logs/nomad_reactive_pipeline_node.log 2>&1 &

nohup python3 -u /mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/nodes/vpr_topological_graph_node.py \
    --ros-args -p image_topic:=/rgb/image \
    </dev/null > /home/robopy/robopy/logs/vpr_topological_graph_node.log 2>&1 &

sleep 2
echo "ACTIVE_NODES:"
ps aux | grep -E 'nomad_reactive_pipeline_node|vpr_topological_graph_node' | grep -v grep
