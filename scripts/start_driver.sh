#!/bin/bash
source /home/robopy/ros2_jazzy/install/setup.bash
source /home/robopy/ros2_venv/bin/activate
source /mnt/ssd/robopy_controller_host/install/setup.bash
export ROS_DOMAIN_ID=42
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
pkill -9 -f waveshare_motor_driver || true
sleep 1
nohup ros2 run robopy_controller waveshare_motor_driver --ros-args \
    -p serial_port:=/dev/motor_driver \
    -p baud_rate:=115200 \
    -p wheel_radius:=0.0335 \
    -p wheel_separation:=0.285 \
    -p rotational_wheel_separation:=0.285 \
    -p ticks_per_rev:=657 \
    -p invert_left_motor:=False \
    -p invert_right_motor:=False \
    -p invert_left_encoder:=True \
    -p invert_right_encoder:=True \
    -p encoder_dead_zone:=2 \
    -p publish_tf:=False \
    -p odom_topic:=/odom_wheel \
    </dev/null > /home/robopy/robopy/logs/waveshare_motor_driver.log 2>&1 &
sleep 2
echo "DRIVER_STARTED"
