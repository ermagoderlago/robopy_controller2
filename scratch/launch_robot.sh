#!/bin/bash
echo "Starting launch script..."
source /opt/ros/jazzy/setup.bash || { echo "Failed to source ROS jazzy"; exit 1; }
source /mnt/ssd/robopy_controller_host/setup_keys.sh || { echo "Failed to source keys"; exit 1; }
source /home/robopy/ros2_venv/bin/activate || { echo "Failed to activate venv"; exit 1; }
cd /mnt/ssd/robopy_controller_host || { echo "Failed to cd to workspace"; exit 1; }
if [ -f install/setup.bash ]; then
    source install/setup.bash
else
    echo "Warning: install/setup.bash not found, but proceeding..."
fi

echo "Launching robot_ia_launch.py..."
# Run in background and redirect to a file we can read
ros2 launch robopy_controller robot_ia_launch.py > /tmp/marcus_launch.log 2>&1 &
echo "Launch started. PID: $!"
echo $! > /tmp/marcus_launch.pid
