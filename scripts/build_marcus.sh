#!/bin/bash
set -e
source /opt/ros/jazzy/setup.bash
cd /mnt/ssd/robopy_controller_host
MAKEFLAGS="-j1" colcon build --parallel-workers 1 --packages-select robopy_controller --cmake-args -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS="-O3 -mcpu=cortex-a76+crypto"
echo "BUILD_COMPLETE_SUCCESS"
