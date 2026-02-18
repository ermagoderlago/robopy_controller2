---
description: Build the robopy_controller ROS 2 package
---

// turbo-all

1. Activate the ROS 2 virtual environment:
```bash
source ~/ros2env/bin/activate
```

2. Build the package:
```bash
cd /home/robopy/robopy/robopi_controller/robopy_controller_host
colcon build --packages-select robopy_controller \
  --symlink-install \
  --cmake-clean-first \
  --cmake-args -GNinja \
    -DBUILD_TESTING=OFF \
    -DCMAKE_C_COMPILER=/usr/bin/clang \
    -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
    -C /home/robopy/ros2_jazzy/pi5_clang_optimization.cmake
```

3. Source the install:
```bash
source install/setup.bash
```

4. I pacchetti ROS2 sono installati in:
'''bash
cd ~/ros2_jazzy
'''

e per compilarli uso:
'''bash

colcon build \
  --symlink-install \
  --packages-select depthai_core \
  --cmake-args \
    -GNinja \
    -DBUILD_TESTING=OFF \
    -C ~/ros2_jazzy/pi5_optimization.cmake \
    -DCMAKE_VERBOSE_MAKEFILE=ON \
    -DCMAKE_PREFIX_PATH="$HOME/ros2_jazzy/install" \
    -DCMAKE_FIND_ROOT_PATH="$HOME/ros2_jazzy/install"
'''
