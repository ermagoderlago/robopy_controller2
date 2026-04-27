#!/bin/bash
pkill -9 -f "ros2|planner_server|controller_server|behavior_server|bt_navigator|robot_ai_node|fast_flow_vo_cpp|motor_control_node|homeassistant_node|servo_coda_node|madgwick_node|rtabmap|rgbd_odometry|ekf_node|static_transform_publisher|robot_state_publisher|depthimage_to_laserscan" || true
sleep 2
source ~/ros2_venv/bin/activate
source install/setup.bash
source setup_keys.sh
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
ros2 launch robopy_controller fast_flow_launch.py delete_db:=false enable_nav2:=true
