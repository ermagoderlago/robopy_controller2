#!/bin/bash
# Build script for robopy_controller with clang
# Fixes CMake path conflicts between system ROS and custom ros2_jazzy

set -e

# Change to the script's directory so it works regardless of installation path
cd "$(dirname "$0")"

# Clean old build to avoid generator conflicts
rm -rf build/robopy_controller install/robopy_controller

# Setup clean ROS environment (custom ros2_jazzy only)
unset AMENT_PREFIX_PATH CMAKE_PREFIX_PATH COLCON_PREFIX_PATH

# Source ONLY the custom ros2_jazzy 
source /home/robopy/ros2_jazzy/install/setup.bash 2>/dev/null || true

# Override CMAKE_PREFIX_PATH to exclude system ROS
export CMAKE_PREFIX_PATH="/home/robopy/ros2_jazzy/install:$CMAKE_PREFIX_PATH"

# Limit GNU Make to a single job to prevent OOM
export MAKEFLAGS="-j1"

# Build with clang
colcon build \
  --packages-select robopy_controller \
  --symlink-install \
  --parallel-workers 1 \
  --cmake-args \
    -DCMAKE_C_COMPILER=/usr/bin/clang \
    -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
    -DBUILD_TESTING=OFF \
    -Wno-dev

echo "Build completed!"
