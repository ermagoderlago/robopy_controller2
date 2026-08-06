#!/bin/bash

echo "Configuring environment for WSL2 Gazebo Sim Harmonic..."

# Prevent WSLg crashes on some hardware setups
export LIBGL_ALWAYS_SOFTWARE=1
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_DOMAIN_ID=42

echo "Building robot_simulation package..."
cd ..
colcon build --packages-select robot_simulation --symlink-install

echo "Setup complete. Run 'source install/setup.bash' and 'ros2 launch robot_simulation sim_bringup.launch.py'"
