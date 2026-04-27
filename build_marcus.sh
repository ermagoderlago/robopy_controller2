#!/bin/bash
# Script di build completo per robopy_controller (Marcus)
set -e

# Carica ambiente ROS 2 Jazzy (base)
source /opt/ros/jazzy/setup.bash

# Spostati nella root del workspace su SSD
cd /mnt/ssd/robopy_controller_host

echo "🧹 Pulizia build/install pre-esistenti per robopy_controller..."
rm -rf build/robopy_controller install/robopy_controller

echo "🏗️ Inizio compilazione con Clang e Ninja..."
colcon build --packages-select robopy_controller \
  --symlink-install \
  --cmake-clean-first \
  --cmake-args -GNinja \
    -DBUILD_TESTING=OFF \
    -DCMAKE_C_COMPILER=/usr/bin/clang \
    -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
    -C /mnt/ssd/ros2_jazzy/pi5_clang_optimization.cmake

echo "✅ Build completata!"
