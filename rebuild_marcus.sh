#!/bin/bash
# rebuild_marcus.sh
cd /mnt/ssd/robopy_controller_host
source /mnt/ssd/ros2_jazzy/install/setup.bash
source /home/robopy/ros2_venv/bin/activate

echo "🛠️ Inizio ricompilazione con path ottimizzati SSD..."
colcon build --packages-select robopy_controller \
  --symlink-install \
  --cmake-args -GNinja \
    -DBUILD_TESTING=OFF \
    -DCMAKE_C_COMPILER=/usr/bin/clang \
    -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
    -C /mnt/ssd/ros2_jazzy/pi5_clang_optimization.cmake

if [ $? -eq 0 ]; then
    echo "✅ Build completata con successo!"
    echo "🔄 Riavvio sistema..."
    bash restart.sh
else
    echo "❌ Errore durante la build!"
    exit 1
fi
