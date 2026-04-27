#!/bin/bash
# Script per la compilazione ottimizzata di Marcus sul Pi 5
# [v5.8] Unificato con il workflow ufficiale in build.md

source /opt/ros/jazzy/setup.bash
cd /mnt/ssd/robopy_controller_host

echo "🛠️ Avvio compilazione robopy_controller..."
colcon build --packages-select robopy_controller \
  --symlink-install \
  --cmake-clean-first \
  --cmake-args -GNinja \
    -DBUILD_TESTING=OFF \
    -DCMAKE_C_COMPILER=/usr/bin/clang \
    -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
    -C /mnt/ssd/ros2_jazzy/pi5_clang_optimization.cmake

echo "✅ Compilazione completata!"
