#!/bin/bash
# Script di build remota per robopy_controller (v0.1.2)
cd /mnt/ssd/robopy_controller_host
source /opt/ros/jazzy/setup.bash

echo "🛠️ Avvio compilazione robopy_controller su Marcus..."

colcon build --packages-select robopy_controller \
  --symlink-install \
  --cmake-clean-first \
  --cmake-args -GNinja \
    -DBUILD_TESTING=OFF \
    -DCMAKE_C_COMPILER=/usr/bin/clang \
    -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
    -C /mnt/ssd/ros2_jazzy/pi5_clang_optimization.cmake

if [ $? -eq 0 ]; then
    echo "✅ Compilazione completata con successo!"
else
    echo "❌ Errore durante la compilazione."
    exit 1
fi
