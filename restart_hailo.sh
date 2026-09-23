#!/bin/bash
# restart_hailo.sh — Riavvio ordinato dell'infrastruttura AI locale e navigazione
# [v3.0] Avvio sequenziale temporizzato (sleep) per la stabilità di camera, SLAM e NAV2

# --- Gestione Watchdog di Sopravvivenza ---
if [ -z "$FROM_WATCHDOG" ]; then
    echo "🛑 Disattivazione temporanea del Watchdog per riavvio manuale..."
    sudo -n systemctl stop marcus-watchdog.service 2>/dev/null || true
    systemctl --user stop marcus-watchdog.service 2>/dev/null || true
    pkill -9 -f watchdog.sh || true
fi

# --- Setup ambiente ---
source /home/robopy/ros2_jazzy/install/setup.bash
source /home/robopy/ros2_venv/bin/activate
source /mnt/ssd/robopy_controller_host/install/setup.bash
source /mnt/ssd/robopy_controller_host/setup_keys.sh
export LD_LIBRARY_PATH=/mnt/ssd/robopy_controller_host/install/robopy_controller/lib:/mnt/ssd/robopy_controller_host/build/robopy_controller:$LD_LIBRARY_PATH
export ROS_DOMAIN_ID=42

# --- Configurazione Hardware & Power-Saving ---
# Se ENABLE_HAILO=false, la NPU Hailo-10H non viene avviata per azzerare il carico di corrente PCIe (previene brownout)
ENABLE_HAILO="${ENABLE_HAILO:-false}"
USE_AMCL="${USE_AMCL:-false}"
if [ -z "$MAP_FILE" ]; then
    if [ -f "/mnt/ssd/maps/piano_terra_opt.yaml" ]; then
        MAP_FILE="/mnt/ssd/maps/piano_terra_opt.yaml"
    elif [ -f "/mnt/ssd/maps/piano_terra.yaml" ]; then
        MAP_FILE="/mnt/ssd/maps/piano_terra.yaml"
    else
        MAP_FILE="/mnt/ssd/maps/salotto.yaml"
    fi
fi
DELETE_DB_FLAG=""


for arg in "$@"; do
    case "$arg" in
        --enable-hailo|--hailo)
            ENABLE_HAILO="true"
            ;;
        --no-hailo|--disable-hailo)
            ENABLE_HAILO="false"
            ;;
        --delete-db)
            DELETE_DB_FLAG="--delete_db_on_start"
            ;;
        --amcl)
            USE_AMCL="true"
            ;;
        --relocalize)
            RELOCALIZE_FLAG="true"
            USE_AMCL="true"
            ;;
        --slam)
            USE_AMCL="false"
            ;;
        --map=*)
            MAP_FILE="${arg#*=}"
            ;;
    esac
done

if [ "$RESET_DB" = "1" ]; then
    DELETE_DB_FLAG="--delete_db_on_start"
fi

if [ "$ENABLE_HAILO" != "true" ]; then
    echo "🛑 [POWER-SAVE] Hailo-10H NPU disattivata (ENABLE_HAILO=false) per azzerare il picco di corrente PCIe."
    sudo systemctl stop hailo-ollama 2>/dev/null || true
else
    echo "⚡ [POWER-NORMAL] Hailo-10H NPU abilitata (ENABLE_HAILO=true)."
fi

sudo sysctl -w net.core.rmem_max=16777216 2>/dev/null || true
sudo sysctl -w net.core.rmem_default=16777216 2>/dev/null || true

echo "📄 Generating /tmp/cyclonedds_robopy.xml..."
cat << 'EOF' > /tmp/cyclonedds_robopy.xml
<?xml version="1.0" encoding="UTF-8" ?>
<CycloneDDS xmlns="https://cdds.io/config" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="https://cdds.io/config https://raw.githubusercontent.com/eclipse-cyclonedds/cyclonedds/master/etc/cyclonedds.xsd">
    <Domain id="any">
        <Discovery>
            <MaxAutoParticipantIndex>200</MaxAutoParticipantIndex>
        </Discovery>
        <Internal>
            <SocketReceiveBufferSize min="10MB"/>
        </Internal>
    </Domain>
</CycloneDDS>
EOF
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
export PYTHONUNBUFFERED=1

# --- Kill nodi precedenti (AI, Hailo NPU, Camera e Navigazione) ---
echo "🔴 Stopping all previous nodes..."
pkill -9 -f custom_nav2_launch.py || true
pkill -9 -f ros2\ launch || true
pkill -9 -f robot_ai_node || true
pkill -9 -f respeaker_vui_node || true
pkill -9 -f respeaker_interface_node || true
pkill -9 -f hailo_bridge_node || true
pkill -9 -f marcus_semantic_mapper_cpp || true
pkill -9 -f semantic_costmap_injector || true
pkill -9 -f engagement_monitor || true
pkill -9 -f robot_health_supervisor || true
pkill -9 -f system_lifecycle_coordinator_node || true
pkill -9 -f attention_supervisor_node || true
pkill -9 -f localization_fuser_node || true
pkill -9 -f battery_manager_node || true
pkill -9 -f cloud_watchdog_node || true
pkill -9 -f speaker_id_node || true
pkill -9 -f foxglove_bridge || true
pkill -9 -f foxglove_nav2_bridge || true
pkill -9 -f nomad_navigator_node || true
pkill -9 -f nomad_reactive_pipeline_node || true
pkill -9 -f vpr_topological_graph_node || true
pkill -9 -f sensor_standby_manager || true

# Kill camera & navigation nodes
pkill -9 -f sllidar_node || true
pkill -9 -f fast_flow_vo_cpp || true
pkill -9 -f oak_driver_node || true
pkill -9 -f oak_superpoint_odometry_cpp || true
pkill -9 -f waveshare_motor_driver || true
pkill -9 -f depthimage_to_laserscan_node || true
pkill -9 -f rtabmap || true
pkill -9 -f static_transform_publisher || true
pkill -9 -f controller_server || true
pkill -9 -f planner_server || true
pkill -9 -f behavior_server || true
pkill -9 -f bt_navigator || true
pkill -9 -f lifecycle_manager || true
pkill -9 -f '/map_server' || pkill -9 -x map_server || true
pkill -9 -f '/amcl' || pkill -9 -x amcl || true
pkill -9 -f ultrasonic_sensor || true
pkill -9 -f bluedot_node || true

# Kill VIO/odometry legacy nodes (NO EKF in questo stack)
pkill -9 -f robot_localization || true
pkill -9 -f ekf_node || true
pkill -9 -f vins_node || true
pkill -9 -f spectacular_vio_node || true
pkill -9 -f fast_flow_vo_cpp || true
pkill -9 -f fast_flow_vo || true
pkill -9 -f superpoint_node || true
pkill -9 -f madgwick_node || true
pkill -9 -f oak_visual_odometry_cpp || true
pkill -9 -f rgbd_odometry || true

# Reset ROS 2 Daemon
ros2 daemon stop || true
sleep 2
ros2 daemon start || true
sleep 1

# =============================================================================
# STEP 1: AVVIO CAMERA E TRASFORMATE STATICHE (Subito)
# =============================================================================
echo "⚙️ Starting waveshare_motor_driver..."
# Auto-sync driver node to install site-packages for immediate deployment without full build
if [ -f "/mnt/ssd/robopy_controller_host/robopy_controller/nodes/waveshare_motor_driver.py" ]; then
    cp -u /mnt/ssd/robopy_controller_host/robopy_controller/nodes/waveshare_motor_driver.py \
          /mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/nodes/waveshare_motor_driver.py 2>/dev/null || true
fi
> /home/robopy/robopy/logs/waveshare_motor_driver.log
nohup ros2 run robopy_controller waveshare_motor_driver --ros-args \
    -p serial_port:=/dev/motor_driver \
    -p baud_rate:=115200 \
    -p wheel_radius:=0.0335 \
    -p wheel_separation:=0.285 \
    -p rotational_wheel_separation:=0.266 \
    -p ticks_per_rev:=1440 \
    -p invert_left_motor:=False \
    -p invert_right_motor:=False \
    -p invert_left_encoder:=False \
    -p invert_right_encoder:=False \
    -p encoder_dead_zone:=2 \
    -p publish_tf:=True \
    -p use_cmd_vel_odometry:=False \
    -p use_imu_for_rotation:=False \
    -p use_encoder_for_linear:=True \
    -p enable_esp32_pid:=False \
    -p odom_topic:=/odom \
    > /home/robopy/robopy/logs/waveshare_motor_driver.log 2>&1 &


echo "📐 Starting CAD static TF publishers (OAK-D Lite 8° pitch UP, Z=0.2616m, RPLIDAR C1 Z=0.18m, yaw 180°)..."
nohup ros2 run tf2_ros static_transform_publisher \
  --x 0.08 --y 0.0 --z 0.18 \
  --roll 0.0 --pitch 0.0 --yaw 3.14159265 \
  --frame-id base_link --child-frame-id laser \
  > /home/robopy/robopy/logs/tf_laser.log 2>&1 &

nohup ros2 run tf2_ros static_transform_publisher \
  --x 0.0332 --y 0.0 --z 0.2616 \
  --roll 0.0 --pitch -0.1396 --yaw 0.0 \
  --frame-id base_link --child-frame-id oak_camera_link \
  > /home/robopy/robopy/logs/tf_camera.log 2>&1 &

nohup ros2 run tf2_ros static_transform_publisher \
  --x 0.0 --y 0.0 --z 0.0 \
  --roll 0.0 --pitch 0.0 --yaw 0.0 \
  --frame-id oak_camera_link --child-frame-id oak_imu_frame \
  > /home/robopy/robopy/logs/tf_imu.log 2>&1 &

nohup ros2 run tf2_ros static_transform_publisher \
  --x 0.0 --y 0.0 --z 0.0 \
  --roll -1.5708 --pitch 0.0 --yaw -1.5708 \
  --frame-id oak_camera_link --child-frame-id camera_optical_frame \
  > /home/robopy/robopy/logs/tf_camera_opt.log 2>&1 &

nohup ros2 run tf2_ros static_transform_publisher \
  --x 0.0 --y 0.0 --z 0.0 \
  --roll -1.5708 --pitch 0.0 --yaw -1.5708 \
  --frame-id oak_camera_link --child-frame-id oak_left_camera_optical_frame \
  > /home/robopy/robopy/logs/tf_camera_oak_left.log 2>&1 &

nohup ros2 run tf2_ros static_transform_publisher \
  --x 0.12 --y 0.0 --z 0.05 \
  --roll 0.0 --pitch 0.0 --yaw 0.0 \
  --frame-id base_link --child-frame-id ultrasonic_sensor \
  > /home/robopy/robopy/logs/tf_ultrasonic.log 2>&1 &

nohup ros2 run tf2_ros static_transform_publisher \
  --x 0.0 --y 0.0 --z 0.05 \
  --roll 0.0 --pitch 0.0 --yaw 0.0 \
  --frame-id base_link --child-frame-id chassis_imu_link \
  > /home/robopy/robopy/logs/tf_chassis_imu.log 2>&1 &

echo "👁️ Starting FastFlow C++ VIO Node..."
> /home/robopy/robopy/logs/fast_flow_vo.log
nohup taskset -c 2,3 ros2 run robopy_controller fast_flow_vo_cpp --ros-args \
    -p camera_fps:=12.0 \
    -p enable_vo:=false \
    -p max_features:=150 \
    -p klt_win_size:=15 \
    -p klt_max_level:=2 \
    -p publish_tf:=false \
    -p odom_frame:=odom \
    -p base_frame:=base_link \
    -p camera_frame:=camera_optical_frame \
    --remap odom:=/odom_vio \
    > /home/robopy/robopy/logs/fast_flow_vo.log 2>&1 &
sleep 3

echo "📡 Starting Slamtec RPLIDAR C1 (360° ToF Laser Scan)..."
> /home/robopy/robopy/logs/sllidar_c1.log
source /home/robopy/lidar_ws/install/setup.bash 2>/dev/null || true
nohup ros2 run sllidar_ros2 sllidar_node --ros-args \
    -p channel_type:=serial \
    -p serial_port:=/dev/rplidar \
    -p serial_baudrate:=460800 \
    -p frame_id:=laser \
    -p inverted:=false \
    -p angle_compensate:=true \
    -p scan_mode:=Standard \
    > /home/robopy/robopy/logs/sllidar_c1.log 2>&1 &

echo "📡 Starting ultrasonic_sensor..."
> /home/robopy/robopy/logs/ultrasonic_sensor.log
nohup ros2 run robopy_controller ultrasonic_sensor \
    > /home/robopy/robopy/logs/ultrasonic_sensor.log 2>&1 &

# Diamo tempo alla camera di inizializzare l'NPU/SuperPoint e iniziare lo streaming dei frame
echo "⏳ Attesa inizializzazione hardware camera (15 secondi)..."
sleep 15

# =============================================================================
# STEP 2: AVVIO RTAB-MAP (SLAM o Supervisore 3D/Semantico)
# =============================================================================
echo "🗺️ Starting RTAB-Map..."
> /home/robopy/robopy/logs/rtabmap.log
# [FM-NAV-014] Di default la mappa persiste. Se richiesto esplicitamente (--delete-db o RESET_DB=1), si avvia con --delete_db_on_start
if [ -n "$DELETE_DB_FLAG" ] || [ "$1" = "--delete-db" ] || [ "$RESET_DB" = "1" ]; then
    echo "🧹 [SLAM-RESET] Reset database RTAB-Map richiesto: avvio con --delete_db_on_start..."
    DELETE_DB_FLAG="--delete_db_on_start"
fi

RTAB_EXTRA_ARGS=""
if [ "$USE_AMCL" = "true" ]; then
    echo "🗺️ [OPZIONE A] Modalità AMCL 2D attiva: disattivata pubblicazione TF map->odom e incremental memory in RTAB-Map (REP-105)."
    RTAB_EXTRA_ARGS="-p publish_tf:=false -p Mem/IncrementalMemory:=false"
else
    echo "🗺️ [SLAM] Modalità standard SLAM attiva: RTAB-Map è autorità map->odom."
fi

# Auto-sync rtabmap.yaml and nav2 configs to install share directory
mkdir -p /mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config
if [ -f "/mnt/ssd/robopy_controller_host/robopy_controller/config/rtabmap.yaml" ]; then
    cp -u /mnt/ssd/robopy_controller_host/robopy_controller/config/rtabmap.yaml \
          /mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/rtabmap.yaml 2>/dev/null || true
fi
if [ -f "/mnt/ssd/robopy_controller_host/robopy_controller/config/nav2_params_jazzy.yaml" ]; then
    cp -u /mnt/ssd/robopy_controller_host/robopy_controller/config/nav2_params_jazzy.yaml \
          /mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/nav2_params_jazzy.yaml 2>/dev/null || true
fi

nohup taskset -c 2,3 ros2 run rtabmap_slam rtabmap $DELETE_DB_FLAG --ros-args \
    --params-file /mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/rtabmap.yaml \
    -p database_path:=/mnt/ssd/rtabmap.db \
    $RTAB_EXTRA_ARGS \
    -r rgb/image:=/rgb/image \
    -r rgb/camera_info:=/camera/camera_info \
    -r depth/image:=/camera/depth/image_raw \
    -r odom:=/odom \
    -r scan:=/scan \
    > /home/robopy/robopy/logs/rtabmap.log 2>&1 &


echo "🔌 Starting respeaker_interface_node..."
> /home/robopy/robopy/logs/respeaker_interface_node.log
nohup ros2 run robopy_controller respeaker_interface_node --ros-args \
    -p uart_port:=/dev/respeaker \
    -p uart_baud:=921600 \
    -p enabled:=True \
    -p default_volume:=5 \
    -p enable_aec:=True \
    -p enable_agc:=False \
    -p enable_ns:=True \
    > /home/robopy/robopy/logs/respeaker_interface_node.log 2>&1 &

echo "🎤 Starting respeaker_vui_node (v12.0 adaptive)..."
> /home/robopy/robopy/logs/respeaker_vui_node.log
nohup ros2 run robopy_controller respeaker_vui_node --ros-args \
    -r __node:=respeaker_vui_node \
    -p use_sim_time:=False \
    -p stt_gain:=1.8 \
    -p noise_gate_threshold:=120.0 \
    -p listen_timeout_sec:=180.0 \
    -p wakeword_sensitivity:=0.95 \
    -p enable_barge_in:=true \
    -p barge_in_min_tts_ms:=2500.0 \
    -p barge_in_min_frames:=10 \
    -p enable_adaptive_threshold:=true \
    -p enable_adaptive_silence:=true \
    -p playback_volume:=0.10 \
    -p enable_auto_volume:=true \
    -p enable_audio_beeps:=true \
    -p diag_mode:=true \
    > /home/robopy/robopy/logs/respeaker_vui_node.log 2>&1 &

if [ "$ENABLE_HAILO" = "true" ]; then
    echo "🧠 Starting hailo_bridge_node_cpp (NPU C++ Driver)..."
    > /home/robopy/robopy/logs/hailo_bridge_node.log
    nohup taskset -c 2,3 /mnt/ssd/robopy_controller_host/install/robopy_controller/lib/robopy_controller/hailo_bridge_node_cpp --ros-args \
        -p hef_path:=/mnt/ssd/models/marcus_unified.hef \
        -p sim_mode:=False \
        -p rgb_topic:=/rgb/image \
        -p vlm_rate_hz:=5.0 \
        > /home/robopy/robopy/logs/hailo_bridge_node.log 2>&1 &
else
    echo "💤 [POWER-SAFE] Salto avvio hailo_bridge_node_cpp (ENABLE_HAILO=false)."
fi

# [CPU-OPT Pi 5] localization_fuser_node disabilitato: l'odometria primaria è già pubblicata con TF odom->base_link
# da waveshare_motor_driver ed è consumata direttamente da RTAB-Map e Nav2. Risparmio: ~20% CPU.
# echo "🛡️ Starting localization_fuser_node (Dedicated EKF/VIO Fuser)..."
# nohup ros2 run robopy_controller localization_fuser_node --ros-args -p publish_tf:=False > /home/robopy/robopy/logs/localization_fuser_node.log 2>&1 &

echo "🚑 Starting robot_health_supervisor (System Health & Safety)..."
> /home/robopy/robopy/logs/robot_health_supervisor.log
nohup ros2 run robopy_controller robot_health_supervisor \
    > /home/robopy/robopy/logs/robot_health_supervisor.log 2>&1 &

echo "🔋 Starting battery_manager_node (BMS & Anti-Sag Supervisor)..."
> /home/robopy/robopy/logs/battery_manager_node.log
nohup ros2 run robopy_controller battery_manager_node --ros-args \
    --params-file /mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/battery_params.yaml \
    > /home/robopy/robopy/logs/battery_manager_node.log 2>&1 &

echo "💤 Starting sensor_standby_manager (Smart Standby & Sensor Power-Save)..."
> /home/robopy/robopy/logs/sensor_standby_manager.log
nohup ros2 run robopy_controller sensor_standby_manager --ros-args \
    -p idle_timeout_sec:=1800.0 \
    -p imu_accel_threshold:=0.35 \
    -p imu_gyro_threshold:=0.15 \
    </dev/null > /home/robopy/robopy/logs/sensor_standby_manager.log 2>&1 &

echo "⚠️ Starting attention_supervisor_node (Context/CPU Switching)..."
> /home/robopy/robopy/logs/attention_supervisor_node.log
nohup ros2 run robopy_controller attention_supervisor_node \
    > /home/robopy/robopy/logs/attention_supervisor_node.log 2>&1 &
if [ "$ENABLE_HAILO" = "true" ]; then
    echo "🗣️ Starting speaker_id_node (Biometric Verifier)..."
    > /home/robopy/robopy/logs/speaker_id_node.log
    nohup ros2 run robopy_controller speaker_id_node --ros-args \
        -p speaker_hef_path:=/mnt/ssd/models/ecapa_tdnn.hef \
        > /home/robopy/robopy/logs/speaker_id_node.log 2>&1 &
else
    echo "💤 [POWER-SAFE] Salto avvio speaker_id_node (ENABLE_HAILO=false)."
fi

echo "🧱 Starting semantic_costmap_injector (Hailo 3D Obstacle & Costmap Fusion)..."
> /home/robopy/robopy/logs/semantic_costmap_injector.log
nohup ros2 run robopy_controller semantic_costmap_injector \
    > /home/robopy/robopy/logs/semantic_costmap_injector.log 2>&1 &

# [CPU-OPT Pi 5] nomad_reactive_pipeline_node disabilitato in modalità pura Nav2:
# risparmia ~20% CPU per timer watchdog e fast loop inattivi.
# echo "🧭 Starting nomad_reactive_pipeline_node..."

echo "👥 Starting engagement_monitor (HRI Gaze/Prossemic)..."
> /home/robopy/robopy/logs/engagement_monitor.log
nohup ros2 run robopy_controller engagement_monitor \
    > /home/robopy/robopy/logs/engagement_monitor.log 2>&1 &

echo "🌐 Starting cloud_watchdog_node (Local NPU vs Cloud Fallback)..."
> /home/robopy/robopy/logs/cloud_watchdog_node.log
nohup ros2 run robopy_controller cloud_watchdog_node \
    > /home/robopy/robopy/logs/cloud_watchdog_node.log 2>&1 &

echo "🛡️ Starting system_lifecycle_coordinator_node (Memory Pressure Sentinel & Lifecycle)..."
> /home/robopy/robopy/logs/system_lifecycle_coordinator_node.log
nohup ros2 run robopy_controller system_lifecycle_coordinator_node \
    > /home/robopy/robopy/logs/system_lifecycle_coordinator_node.log 2>&1 &

echo "🤖 Starting robot_ai_node (Cognitive Orchestrator)..."
> /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log
nohup ros2 run robopy_controller robot_ai_node \
    > /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log 2>&1 &

echo "🔌 Starting foxglove_bridge..."
> /home/robopy/robopy/logs/foxglove_bridge.log
nohup ros2 run foxglove_bridge foxglove_bridge --ros-args \
    -p port:=8765 \
    -p send_buffer_limit:=10000000 \
    -p max_subscription_rate:=10.0 \
    -p use_compression:=true \
    > /home/robopy/robopy/logs/foxglove_bridge.log 2>&1 &

echo "🌉 Starting foxglove_nav2_bridge..."
> /home/robopy/robopy/logs/foxglove_nav2_bridge.log
nohup ros2 run robopy_controller foxglove_nav2_bridge \
    > /home/robopy/robopy/logs/foxglove_nav2_bridge.log 2>&1 &

echo "🔵 Starting bluedot_node..."
> /home/robopy/robopy/logs/bluedot_node.log
nohup ros2 run robopy_controller bluedot_node \
    > /home/robopy/robopy/logs/bluedot_node.log 2>&1 &

# [CPU-OPT Pi 5] Attesa stabilizzazione RTAB-Map SLAM prima del lancio di Nav2
echo "⏳ [NAV2-PREFLIGHT] Attesa stabilizzazione RTAB-Map SLAM (10 secondi)..."
sleep 10

# =============================================================================
# STEP 3: AVVIO NAV2 STACK (Solo dopo che i sensori e TF odom/map sono stabili)
# =============================================================================
echo "🚀 Starting Nav2 Stack (enable_amcl=$USE_AMCL, map=$MAP_FILE)..."
> /home/robopy/robopy/logs/nav2.log
nohup ros2 launch robopy_controller custom_nav2_launch.py \
    params_file:=/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/nav2_params_jazzy.yaml \
    enable_amcl:=$USE_AMCL \
    map:=$MAP_FILE \
    use_sim_time:=false \
    autostart:=true \
    use_respawn:=true \
    > /home/robopy/robopy/logs/nav2.log 2>&1 &

# Attesa di inizializzazione per i nodi lifecycle gestiti nativamente da Nav2
echo "⏳ [NAV2-MONITOR] Nav2 lifecycle manager gestisce la transizione automatica dei nodi..."
sleep 15

# Inizializzazione Automatica Localizzazione AMCL (Pose Persistence o Auto-Relocalize)
if [ "$USE_AMCL" = "true" ]; then
    echo "🎯 [AMCL-INIT] Inizializzazione automatica della localizzazione..."
    POSE_FILE="${MAP_FILE%.*}_pose.yaml"
    if [ ! -f "$POSE_FILE" ]; then
        POSE_FILE="/mnt/ssd/last_known_pose.yaml"
    fi
    
    if [ "$RELOCALIZE_FLAG" = "true" ]; then
        echo "🔄 [AMCL-INIT] Richiesta auto-localizzazione attiva a 360°..."
        nohup python3 /mnt/ssd/robopy_controller_host/scripts/auto_relocalize.py --force-global > /home/robopy/robopy/logs/auto_relocalize.log 2>&1 &
    elif [ -f "$POSE_FILE" ]; then
        echo "📍 [AMCL-INIT] Iniezione posa nota da $POSE_FILE..."
        python3 /mnt/ssd/robopy_controller_host/scripts/auto_relocalize.py --pose-file="$POSE_FILE" --inject-only > /home/robopy/robopy/logs/auto_relocalize.log 2>&1 || true
    else
        echo "🌐 [AMCL-INIT] Nessuna posa salvata trovata: avvio auto-localizzazione globale..."
        nohup python3 /mnt/ssd/robopy_controller_host/scripts/auto_relocalize.py > /home/robopy/robopy/logs/auto_relocalize.log 2>&1 &
    fi
fi

echo "📌 [CPU-OPT] Forzatura Hot-Swap affinità CPU sui nodi C++..."
for node_name in "fast_flow_vo_cpp" "rtabmap" "hailo_bridge_node_cpp"; do
    for pid in $(pgrep -f $node_name); do
        taskset -a -pc 2,3 $pid > /dev/null 2>&1 || true
    done
done

echo "✅ Stack completo (AI, Percezione, SLAM e Nav2) avviato con successo!"
echo "   Camera log:    tail -f /home/robopy/robopy/logs/oak_camera.log"
echo "   RTAB-Map log:  tail -f /home/robopy/robopy/logs/rtabmap.log"
echo "   Nav2 log:      tail -f /home/robopy/robopy/logs/nav2.log"
echo "   VUI log:       tail -f /home/robopy/robopy/logs/respeaker_vui_node.log"

if [ -z "$FROM_WATCHDOG" ]; then
    echo "🟢 Riattivazione del Watchdog..."
    sudo systemctl start marcus-watchdog.service || true
    systemctl --user start marcus-watchdog.service || true
fi
