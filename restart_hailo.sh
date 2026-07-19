#!/bin/bash
# restart_hailo.sh — Riavvio ordinato dell'infrastruttura AI locale e navigazione
# [v3.0] Avvio sequenziale temporizzato (sleep) per la stabilità di camera, SLAM e NAV2

# --- Gestione Watchdog di Sopravvivenza ---
if [ -z "$FROM_WATCHDOG" ]; then
    echo "🛑 Disattivazione temporanea del Watchdog per riavvio manuale..."
    sudo systemctl stop marcus-watchdog.service || true
fi

# --- Setup ambiente ---
source /home/robopy/ros2_jazzy/install/setup.bash
source /home/robopy/ros2_venv/bin/activate
source /mnt/ssd/robopy_controller_host/install/setup.bash
source /mnt/ssd/robopy_controller_host/setup_keys.sh
export ROS_DOMAIN_ID=42

if [ ! -f /tmp/cyclonedds_robopy.xml ]; then
    echo "📄 Generating /tmp/cyclonedds_robopy.xml..."
    cat << 'EOF' > /tmp/cyclonedds_robopy.xml
<?xml version="1.0" encoding="UTF-8" ?>
<CycloneDDS xmlns="https://cdds.io/config" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="https://cdds.io/config https://raw.githubusercontent.com/eclipse-cyclonedds/cyclonedds/master/etc/cyclonedds.xsd">
    <Domain id="any">
        <Discovery>
            <MaxAutoParticipantIndex>200</MaxAutoParticipantIndex>
        </Discovery>
    </Domain>
</CycloneDDS>
EOF
fi
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
pkill -9 -f cloud_watchdog_node || true
pkill -9 -f speaker_id_node || true
pkill -9 -f foxglove_bridge || true
pkill -9 -f foxglove_nav2_bridge || true

# Kill camera & navigation nodes
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
pkill -9 -f ultrasonic_sensor || true
pkill -9 -f bluedot_node || true

# Reset ROS 2 Daemon
ros2 daemon stop || true
sleep 2
ros2 daemon start || true
sleep 1

# =============================================================================
# STEP 1: AVVIO CAMERA E TRASFORMATE STATICHE (Subito)
# =============================================================================
echo "⚙️ Starting waveshare_motor_driver..."
> /home/robopy/robopy/logs/waveshare_motor_driver.log
nohup ros2 run robopy_controller waveshare_motor_driver --ros-args \
    -p serial_port:=/dev/ttyUSB0 \
    -p baud_rate:=115200 \
    -p wheel_radius:=0.0361 \
    -p wheel_separation:=0.091 \
    -p ticks_per_rev:=594 \
    -p invert_left_motor:=False \
    -p invert_right_motor:=False \
    -p invert_left_encoder:=True \
    -p invert_right_encoder:=False \
    -p publish_tf:=True \
    > /home/robopy/robopy/logs/waveshare_motor_driver.log 2>&1 &

echo "📐 Starting static TF publishers..."
nohup ros2 run tf2_ros static_transform_publisher --x 0.05 --y 0.0 --z 0.08 --yaw 0.0 --pitch -0.1535 --roll 0.0 --frame-id base_link --child-frame-id camera_link > /home/robopy/robopy/logs/tf_camera.log 2>&1 &
nohup ros2 run tf2_ros static_transform_publisher --x 0.0 --y 0.0 --z 0.0 --yaw -1.5708 --pitch 0.0 --roll -1.5708 --frame-id camera_link --child-frame-id camera_optical_frame > /home/robopy/robopy/logs/tf_camera_opt.log 2>&1 &
nohup ros2 run tf2_ros static_transform_publisher --x 0.0 --y 0.0 --z 0.0 --yaw 0.0 --pitch 0.0 --roll 0.0 --frame-id base_link --child-frame-id imu_link > /home/robopy/robopy/logs/tf_imu.log 2>&1 &
nohup ros2 run tf2_ros static_transform_publisher --x 0.12 --y 0.0 --z 0.05 --yaw 0.0 --pitch 0.0 --roll 0.0 --frame-id base_link --child-frame-id ultrasonic_sensor > /home/robopy/robopy/logs/tf_ultrasonic.log 2>&1 &

echo "📷 Starting OAK SuperPoint camera (C++ Driver)..."
mkdir -p /home/robopy/robopy/logs
> /home/robopy/robopy/logs/oak_camera.log
nohup ros2 run robopy_controller oak_superpoint_odometry_cpp --ros-args \
    -p superpoint_blob_path:=/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/models/superpoint.blob \
    -p yolo_blob_path:=/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/models/yolov6nr1_coco_640x352.blob \
    -p enable_yolo:=False \
    -p publish_tf:=False \
    -p depth_fps:=12.0 \
    -p depth_resolution:=400p \
    -p depth_pub_width:=320 \
    -p depth_pub_height:=200 \
    > /home/robopy/robopy/logs/oak_camera.log 2>&1 &

echo "📡 Starting depthimage_to_laserscan..."
> /home/robopy/robopy/logs/depthimage_to_laserscan.log
nohup ros2 run depthimage_to_laserscan depthimage_to_laserscan_node --ros-args \
    -r depth:=/camera/depth/image_raw \
    -r depth_camera_info:=/camera/camera_info \
    -r scan:=/scan \
    -p output_frame:=camera_link \
    > /home/robopy/robopy/logs/depthimage_to_laserscan.log 2>&1 &

echo "📡 Starting ultrasonic_sensor..."
> /home/robopy/robopy/logs/ultrasonic_sensor.log
nohup ros2 run robopy_controller ultrasonic_sensor \
    > /home/robopy/robopy/logs/ultrasonic_sensor.log 2>&1 &

# Diamo tempo alla camera di inizializzare l'NPU/SuperPoint e iniziare lo streaming dei frame
echo "⏳ Attesa inizializzazione hardware camera (15 secondi)..."
sleep 15

# =============================================================================
# STEP 2: AVVIO RTAB-MAP SLAM E SUITE AI DI MARCUS
# =============================================================================
echo "🗺️ Starting RTAB-Map SLAM..."
> /home/robopy/robopy/logs/rtabmap.log
nohup ros2 run rtabmap_slam rtabmap --delete_db_on_start --ros-args \
    --params-file /mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/rtabmap.yaml \
    -r rgb/image:=/rgb/image \
    -r rgb/camera_info:=/camera/camera_info \
    -r depth/image:=/camera/depth/image_raw \
    -r scan:=/scan \
    -r odom:=/odom \
    > /home/robopy/robopy/logs/rtabmap.log 2>&1 &

echo "🔌 Starting respeaker_interface_node..."
> /home/robopy/robopy/logs/respeaker_interface_node.log
nohup ros2 run robopy_controller respeaker_interface_node --ros-args \
    -p uart_port:=/dev/ttyACM0 \
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
    -p stt_gain:=5.0 \
    -p noise_gate_threshold:=100.0 \
    -p wakeword_sensitivity:=0.95 \
    -p enable_barge_in:=true \
    -p barge_in_min_tts_ms:=2500.0 \
    -p barge_in_min_frames:=8 \
    -p enable_adaptive_threshold:=true \
    -p enable_adaptive_silence:=true \
    -p playback_volume:=0.10 \
    -p enable_auto_volume:=true \
    -p diag_mode:=true \
    > /home/robopy/robopy/logs/respeaker_vui_node.log 2>&1 &

echo "🧠 Starting hailo_bridge_node (NPU Driver)..."
> /home/robopy/robopy/logs/hailo_bridge_node.log
nohup ros2 run robopy_controller hailo_bridge_node --ros-args \
    -p hef_path:=/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/models/joined_yolo_superpoint_netvlad.hef \
    -p sim_mode:=False \
    > /home/robopy/robopy/logs/hailo_bridge_node.log 2>&1 &

echo "🗣️ Starting speaker_id_node (Biometric Verifier)..."
> /home/robopy/robopy/logs/speaker_id_node.log
nohup ros2 run robopy_controller speaker_id_node --ros-args \
    -p speaker_hef_path:=/mnt/ssd/models/ecapa_tdnn.hef \
    > /home/robopy/robopy/logs/speaker_id_node.log 2>&1 &

echo "🗺️ Starting marcus_semantic_mapper_cpp (Visual Fusion C++)..."
> /home/robopy/robopy/logs/marcus_semantic_mapper.log
nohup ros2 run robopy_controller marcus_semantic_mapper_cpp --ros-args \
    -p publish_debug:=True \
    > /home/robopy/robopy/logs/marcus_semantic_mapper.log 2>&1 &

echo "🧱 Starting semantic_costmap_injector..."
> /home/robopy/robopy/logs/semantic_costmap_injector.log
nohup ros2 run robopy_controller semantic_costmap_injector \
    > /home/robopy/robopy/logs/semantic_costmap_injector.log 2>&1 &

echo "👥 Starting engagement_monitor (HRI Gaze/Prossemic)..."
> /home/robopy/robopy/logs/engagement_monitor.log
nohup ros2 run robopy_controller engagement_monitor \
    > /home/robopy/robopy/logs/engagement_monitor.log 2>&1 &

echo "🌐 Starting cloud_watchdog_node (Local NPU vs Cloud Fallback)..."
> /home/robopy/robopy/logs/cloud_watchdog_node.log
nohup ros2 run robopy_controller cloud_watchdog_node \
    > /home/robopy/robopy/logs/cloud_watchdog_node.log 2>&1 &

echo "🤖 Starting robot_ai_node (Cognitive Orchestrator)..."
> /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log
nohup ros2 run robopy_controller robot_ai_node \
    --ros-args -r /ai/input/text:=/robopy/conversation_rx \
    > /home/robopy/robopy/logs/robot_ai_node_debug_TEST4.log 2>&1 &

echo "🔌 Starting foxglove_bridge..."
> /home/robopy/robopy/logs/foxglove_bridge.log
nohup ros2 run foxglove_bridge foxglove_bridge --ros-args \
    -p port:=8765 \
    > /home/robopy/robopy/logs/foxglove_bridge.log 2>&1 &

echo "🌉 Starting foxglove_nav2_bridge..."
> /home/robopy/robopy/logs/foxglove_nav2_bridge.log
nohup ros2 run robopy_controller foxglove_nav2_bridge \
    > /home/robopy/robopy/logs/foxglove_nav2_bridge.log 2>&1 &

echo "🔵 Starting bluedot_node..."
> /home/robopy/robopy/logs/bluedot_node.log
nohup ros2 run robopy_controller bluedot_node \
    > /home/robopy/robopy/logs/bluedot_node.log 2>&1 &

# Attendiamo che RTAB-Map crei il frame map->odom e lo stack AI si stabilizzi
echo "⏳ Attesa stabilità SLAM e AI (15 secondi)..."
sleep 15

# =============================================================================
# STEP 3: AVVIO NAV2 STACK (Solo dopo che i sensori e TF odom/map sono stabili)
# =============================================================================
echo "🚀 Starting Nav2 Stack..."
> /home/robopy/robopy/logs/nav2.log
nohup ros2 launch robopy_controller custom_nav2_launch.py \
    params_file:=/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/nav2_params_jazzy.yaml \
    use_sim_time:=false \
    autostart:=true \
    use_respawn:=true \
    > /home/robopy/robopy/logs/nav2.log 2>&1 &

# Attesa di inizializzazione per i nodi lifecycle gestiti nativamente da Nav2
echo "⏳ [NAV2-MONITOR] Nav2 lifecycle manager gestisce la transizione automatica dei nodi..."
sleep 15

echo "✅ Stack completo (AI, Percezione, SLAM e Nav2) avviato con successo!"
echo "   Camera log:    tail -f /home/robopy/robopy/logs/oak_camera.log"
echo "   RTAB-Map log:  tail -f /home/robopy/robopy/logs/rtabmap.log"
echo "   Nav2 log:      tail -f /home/robopy/robopy/logs/nav2.log"
echo "   VUI log:       tail -f /home/robopy/robopy/logs/respeaker_vui_node.log"

if [ -z "$FROM_WATCHDOG" ]; then
    echo "🟢 Riattivazione del Watchdog..."
    sudo systemctl start marcus-watchdog.service || true
fi
