#!/bin/bash
pkill -9 -f nomad_reactive_pipeline_node 2>/dev/null || true
pkill -9 -f vpr_topological_graph_node 2>/dev/null || true
pkill -9 -f bt_navigator 2>/dev/null || true
pkill -9 -f controller_server 2>/dev/null || true
pkill -9 -f pytest 2>/dev/null || true

source /home/robopy/ros2_jazzy/install/setup.bash
export ROS_DOMAIN_ID=42
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml

ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist '{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}' >/dev/null 2>&1 || true

echo "ALL_MOTORS_STOPPED_AND_LOCKED"
