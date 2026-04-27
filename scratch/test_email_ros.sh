#!/bin/bash
source /opt/ros/jazzy/setup.bash
source /mnt/ssd/robopy_controller_host/install/setup.bash
ros2 topic pub /robopy/conversation_rx std_msgs/msg/String "{data: 'leggi le ultime email'}" --once
