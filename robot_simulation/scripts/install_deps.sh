#!/bin/bash
# ==============================================================================
# Marcus AI - Installazione Dipendenze ROS 2 Jazzy & Gazebo Harmonic su WSL2
# Conformita': SPEC-07 (Zero BOM UTF-8, MAKEFLAGS="-j1"), SPEC-02, SPEC-03
# ==============================================================================
set -e

WORKSPACE_DIR="${1:-/mnt/d/LUCA/robopy}"

echo "================================================================"
echo "🚀 Installazione ROS 2 Jazzy & Gazebo Harmonic (Ubuntu 24.04)"
echo "================================================================"

export DEBIAN_FRONTEND=noninteractive

# 1. Locale e strumenti base
apt-get update
apt-get install -y locales software-properties-common curl gnupg2 lsb-release \
    build-essential cmake git python3-pip python3-venv python3-pytest \
    python3-numpy python3-opencv python3-yaml python3-scipy dos2unix

locale-gen en_US en_US.UTF-8
update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
export LANG=en_US.UTF-8

# 2. Repository ufficiale ROS 2 Jazzy (se non presente)
if [ ! -f /etc/apt/sources.list.d/ros2.list ]; then
    echo "📦 Configurazione repository APT ROS 2 Jazzy..."
    curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | tee /etc/apt/sources.list.d/ros2.list > /dev/null
    apt-get update
fi

# 3. Installazione pacchetti ROS 2 Jazzy, Gazebo Harmonic, Nav2, RTAB-Map e Foxglove
echo "📦 Installazione pacchetti ROS 2 Jazzy, Gazebo Sim (Harmonic), Nav2 e Foxglove Bridge..."
apt-get install -y \
    ros-jazzy-ros-base \
    ros-jazzy-rmw-cyclonedds-cpp \
    ros-jazzy-ros-gz \
    ros-jazzy-ros-gz-sim \
    ros-jazzy-ros-gz-bridge \
    ros-jazzy-ros-gz-interfaces \
    ros-jazzy-foxglove-bridge \
    ros-jazzy-robot-state-publisher \
    ros-jazzy-joint-state-publisher \
    ros-jazzy-xacro \
    ros-jazzy-tf2-ros \
    ros-jazzy-tf2-tools \
    ros-jazzy-tf2-eigen \
    ros-jazzy-vision-msgs \
    ros-jazzy-cv-bridge \
    ros-jazzy-image-transport \
    ros-jazzy-navigation2 \
    ros-jazzy-nav2-bringup \
    ros-jazzy-nav2-amcl \
    ros-jazzy-nav2-map-server \
    ros-jazzy-rtabmap-ros \
    ros-jazzy-teleop-twist-keyboard \
    python3-colcon-common-extensions

# 4. Generazione profilo CycloneDDS (200 partecipanti, Unicast data routing - SPEC-07)
cat << 'EOF' > /tmp/cyclonedds_robopy.xml
<?xml version="1.0" encoding="UTF-8" ?>
<CycloneDDS xmlns="https://cdds.io/config" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <Domain id="any">
        <General>
            <NetworkInterfaceAddress>lo</NetworkInterfaceAddress>
            <AllowMulticast>spdp</AllowMulticast>
        </General>
        <Discovery>
            <MaxAutoParticipantIndex>200</MaxAutoParticipantIndex>
        </Discovery>
        <Internal>
            <SocketReceiveBufferSize min="10MB"/>
        </Internal>
    </Domain>
</CycloneDDS>
EOF

# 5. Compilazione sequenziale (SPEC-07: MAKEFLAGS="-j1" --parallel-workers 1)
if [ -d "$WORKSPACE_DIR" ]; then
    echo "🔨 Compilazione sequenziale di robot_simulation in $WORKSPACE_DIR..."
    cd "$WORKSPACE_DIR"
    source /opt/ros/jazzy/setup.bash
    MAKEFLAGS="-j1" colcon build --parallel-workers 1 --packages-select robot_simulation --symlink-install
fi

echo "✅ Setup WSL2 ROS 2 Jazzy + Gazebo Harmonic completato con successo!"
