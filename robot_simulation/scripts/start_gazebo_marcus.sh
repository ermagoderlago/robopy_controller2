#!/bin/bash
# ==============================================================================
# start_gazebo_marcus.sh — Avvio Completo del Digital Twin di Marcus in Gazebo
# Supporta 4 livelli di mondo: simple | medium | extreme | smart_home
# Conformita': SPEC-00..07, FM-SIM-001 (Shadow Memory Sandbox), FM-NAV-041
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

WORLD_LEVEL="smart_home"
DOMAIN_ID="${ROS_DOMAIN_ID:-42}"
HEADLESS="true"
EXTRA_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --world=*)
            WORLD_LEVEL="${arg#*=}"
            ;;
        --domain=*)
            DOMAIN_ID="${arg#*=}"
            ;;
        --gui)
            HEADLESS="false"
            ;;
        *)
            EXTRA_ARGS+=("$arg")
            ;;
    esac
done

echo "================================================================"
echo "🤖 AVVIO MARCUS DIGITAL TWIN + SLAM + NAV2 (GAZEBO HARMONIC)"
echo "   Mondo: $WORLD_LEVEL | ROS_DOMAIN_ID: $DOMAIN_ID | Headless: $HEADLESS"
echo "================================================================"

if [ -f "/opt/ros/jazzy/setup.bash" ]; then
    source /opt/ros/jazzy/setup.bash
elif [ -f "/home/robopy/ros2_jazzy/install/setup.bash" ]; then
    source /home/robopy/ros2_jazzy/install/setup.bash
fi

if [ -f "/mnt/ssd/robopy_controller_host/install/setup.bash" ]; then
    source /mnt/ssd/robopy_controller_host/install/setup.bash
fi
if [ -f "$WORKSPACE_DIR/install/setup.bash" ]; then
    source "$WORKSPACE_DIR/install/setup.bash"
fi
if [ -d "/tmp/marcus_gazebo_ws/py_overlay" ]; then
    export PYTHONPATH="/tmp/marcus_gazebo_ws/py_overlay:$PYTHONPATH"
fi

export ROS_DOMAIN_ID="$DOMAIN_ID"
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe
export MARCUS_SIM_MODE=1
export MARCUS_SHADOW_SANDBOX_ROOT="/tmp/marcus_shadow_sandbox"
mkdir -p "$MARCUS_SHADOW_SANDBOX_ROOT"

CYCLONE_CFG="/tmp/cyclonedds_robopy_sim_${DOMAIN_ID}.xml"
cat << 'EOF' > "$CYCLONE_CFG"
<?xml version="1.0" encoding="UTF-8" ?>
<CycloneDDS xmlns="https://cdds.io/config" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <Domain id="any">
        <General>
            <Interfaces>
                <NetworkInterface name="lo" priority="default" multicast="default" />
            </Interfaces>
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
export CYCLONEDDS_URI="file://$CYCLONE_CFG"

echo "🛡️ Shadow Memory Sandbox attiva in: $MARCUS_SHADOW_SANDBOX_ROOT (FM-SIM-001)"

LAUNCH_CMD=(
    ros2 launch robot_simulation full_marcus_gazebo.launch.py
    "world_level:=$WORLD_LEVEL"
    "headless:=$HEADLESS"
    "${EXTRA_ARGS[@]}"
)

if [ -z "${DISPLAY:-}" ] && command -v xvfb-run >/dev/null 2>&1; then
    echo "🖥️ Rendering OGRE2/EGL software via xvfb-run + llvmpipe..."
    exec xvfb-run -a -s "-screen 0 640x480x24" "${LAUNCH_CMD[@]}"
else
    exec "${LAUNCH_CMD[@]}"
fi
