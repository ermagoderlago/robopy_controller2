#!/bin/bash
set -eo pipefail

# Source workspace compilato su Pi 5
source /home/robopy/ros2_jazzy/install/setup.bash
source /home/robopy/robopy/robopi_controller/install/setup.bash

# Pi 5: rendering software
export LIBGL_ALWAYS_SOFTWARE=1
export MESA_GL_VERSION_OVERRIDE=3.3

export RCUTILS_CONSOLE_OUTPUT_FORMAT=\
"[{severity}] [{time}] [{name}]: {message} ({function_name} @ {file_name}:{line_number})"
export RCUTILS_COLORIZED_OUTPUT=1
export RCUTILS_LOGGING_BUFFERED_STREAM=1

BAG_DIR="$HOME/bags"
mkdir -p "$BAG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

cleanup() {
  echo "[SITL] Arresto in corso..."
  [ -n "${LAUNCH_PID:-}" ] && kill "$LAUNCH_PID" 2>/dev/null || true
  [ -n "${BAG_PID:-}" ]    && kill "$BAG_PID"    2>/dev/null || true
  ros2 daemon stop          2>/dev/null || true
  wait 2>/dev/null          || true
  echo "[SITL] Shutdown completato."
}
trap cleanup EXIT SIGINT SIGTERM

ros2 launch marcus_robot sitl_bringup.launch.py &
LAUNCH_PID=$!
echo "[SITL] Launch PID: $LAUNCH_PID"

echo "[SITL] Attesa camera online (max 45s)..."
timeout 45 bash -c \
  'until ros2 topic list 2>/dev/null | grep -q "/oak/stereo/image_raw"; \
   do sleep 2; done' \
|| { echo "[SITL] ❌ Camera non rilevata. Abort."; exit 1; }

ros2 bag record \
  /oak/stereo/image_raw \
  /oak/stereo/image_depth \
  /oak/stereo/camera_info \
  /tf /tf_static /odom \
  /ai/visual_memory/markers \
  -o "$BAG_DIR/sitl_run_${TIMESTAMP}" \
  --max-cache-size 524288000 \
  --compression-mode file \
  --compression-format zstd &
BAG_PID=$!

sleep 15
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
HEALTH_FAIL=0

check_hz() {
  local T=$1 EXP=$2
  local ACT
  ACT=$(timeout 5 ros2 topic hz "$T" 2>/dev/null \
        | grep "average rate" | awk '{print int($3)}' || echo "0")
  [ "${ACT:-0}" -ge "$EXP" ] \
    && echo "  ✅ $T -> ${ACT}Hz (>=${EXP}Hz)" \
    || { echo "  ❌ $T -> ${ACT:-0}Hz (atteso >=${EXP}Hz)"; HEALTH_FAIL=1; }
}

check_hz /oak/stereo/image_raw   12
check_hz /oak/stereo/image_depth 12
check_hz /tf                      5
check_hz /odom                   10

if timeout 5 ros2 run tf2_ros tf2_echo \
     base_footprint camera_depth_optical_frame > /dev/null 2>&1; then
  echo "  ✅ TF: base_footprint -> camera_depth_optical_frame OK"
else
  echo "  ❌ TF: catena incompleta -> verifica camera_depth_joint rpy"
  HEALTH_FAIL=1
fi

ros2 node list 2>/dev/null | grep -q "parameter_bridge" \
  && echo "  ✅ ros_gz_bridge attivo" \
  || { echo "  ❌ ros_gz_bridge mancante -> topic camera probabilmente vuoti"; HEALTH_FAIL=1; }

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
[ "$HEALTH_FAIL" -eq 1 ] \
  && echo "[SITL] ⚠️  Health check FALLITO" \
  || echo "[SITL] ✅ Sistema operativo. Ctrl+C per terminare."

wait "$LAUNCH_PID"
