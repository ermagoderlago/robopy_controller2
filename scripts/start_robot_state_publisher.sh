#!/bin/bash
# start_robot_state_publisher.sh — Marcus AI Robot Description & TF publisher
set -e
source /home/robopy/ros2_jazzy/install/setup.bash
source /mnt/ssd/robopy_controller_host/install/setup.bash 2>/dev/null || true
export ROS_DOMAIN_ID=42
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml

pkill -9 -f robot_state_publisher 2>/dev/null || true
sleep 0.5

nohup ros2 run robot_state_publisher robot_state_publisher /mnt/ssd/robopy_controller_host/urdf/robopy.urdf \
    --ros-args -p publish_frequency:=5.0 \
    > /home/robopy/robopy/logs/robot_state_publisher.log 2>&1 &

sleep 1
pgrep -af robot_state_publisher || true
