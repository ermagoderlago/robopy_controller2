#!/usr/bin/env python3
#fast_flow_launch.py

"""
RTAB-Map System — Scenario A (Mapping)
TF Ownership:
  - EKF  → odom → base_link (50Hz, unica autorità)
  - RTAB-Map SLAM → map → odom
  - rgbd_odometry → NO TF (sensore per EKF)
  - fast_flow_vo → NO TF (velocity sensor per EKF)
"""

import os
from launch import LaunchDescription
from launch.actions import TimerAction, DeclareLaunchArgument, OpaqueFunction, IncludeLaunchDescription, ExecuteProcess
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node, SetParameter
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory

# Import Marcus Config
import sys
# Aggiunge il path per trovare config_utils
sys.path.append(os.path.join(get_package_share_directory('robopy_controller'), 'utils'))
try:
    import config_utils
except ImportError:
    # Fallback if not installed in shared-dir yet
    sys.path.append('/mnt/ssd/robopy_controller_host/robopy_controller/utils')
    import config_utils


def generate_launch_description():
    pkg_share = get_package_share_directory('robopy_controller')
    
    # FIX: CycloneDDS Participant Limit (per evitare il crash di Nav2 controller_server)
    # Crea un file xml temporaneo per superare il limite default di 120 nodi.
    dds_config = "/tmp/cyclonedds_robopy.xml"
    with open(dds_config, "w") as f:
        f.write('''<?xml version="1.0" encoding="UTF-8" ?>
<CycloneDDS xmlns="https://cdds.io/config" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="https://cdds.io/config https://raw.githubusercontent.com/eclipse-cyclonedds/cyclonedds/master/etc/cyclonedds.xsd">
    <Domain id="any">
        <Discovery>
            <MaxAutoParticipantIndex>200</MaxAutoParticipantIndex>
        </Discovery>
    </Domain>
</CycloneDDS>''')
    os.environ["CYCLONEDDS_URI"] = f"file://{dds_config}"
    
    # Launch Arguments
    args = [
        DeclareLaunchArgument('delete_db', default_value='false', description='Delete RTAB-Map database on start'),
        DeclareLaunchArgument('localization', default_value='false', description='Location mode (no mapping)'),
        DeclareLaunchArgument('enable_nav2', default_value='true', description='Enable Navigation2 stack'),
        DeclareLaunchArgument('sim_mode', default_value='false', description='Enable Gazebo simulation mode'),
    ]
    
    # URDF
    urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')
    with open(urdf_file, 'r') as f:
        robot_description = ParameterValue(f.read(), value_type=str)

    # YOLO Blob Path
    # YOLO Blob Path
    # FORCE YOLOv6 (640x352) - Strict detection model to avoid crashes
    yolo_blob = os.path.join(pkg_share, 'models', 'yolov6nr1_coco_640x352.blob')
    if not os.path.exists(yolo_blob):
         # Fallback only if absolutely necessary, but warn user
         print(f"WARNING: YOLOv6 blob not found at {yolo_blob}")
         yolo_blob = os.path.join(pkg_share, 'models', 'yolo_seg.blob')
    
    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        parameters=[{'robot_description': robot_description}],
        output='screen'
    )
    
    # ================================================
    # STATIC TF
    # ================================================
    camera_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_camera_tf',
        arguments=[
            '0.05', '0.0', '0.08',
            '-1.5708', '0.0', '-1.4173',
            'base_link',
            'oak_left_camera_optical_frame'
        ],
        output='log'
    )
    
    imu_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_imu_tf',
        arguments=[
            '0.0', '0.0', '0.0',
            '0.0', '0.0', '0.0', '1.0',
            'base_link',
            'imu_link'
        ],
        output='log'
    )
    
    ultrasonic_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_ultrasonic_tf',
        arguments=[
            '0.12', '0.0', '0.05',
            '0.0', '0.0', '0.0',
            'base_link',
            'ultrasonic_sensor'
        ],
        output='log'
    )
    
    # ================================================
    # FAST+KLT VO (VELOCITY SENSOR ONLY!)
    # Pubblica: /fast_flow/velocity (TWIST ONLY!)
    #           /rgb/image + /camera/depth/image_raw
    #           /oak/imu/data
    # NO TF publishing!
    # ================================================
    oak_camera = Node(
        package='robopy_controller',
        executable='fast_flow_vo_cpp',
        name='fast_flow_vo',
        output='screen',
        respawn=True,
        respawn_delay=2.0,
        parameters=[{
            'camera_frame': 'oak_left_camera_optical_frame',
            'odom_frame': 'odom',
            'base_frame': 'base_link',
            
            # Odom TF is explicitly broadcasted internally by FastFlowVO
            'publish_tf': True,
            
            # Camera settings
            'camera_fps': 15.0, # Alzato a 15Hz (fondamentale per inseguire i punti in ottico KLT senza perderli nei movimenti)
            'skip_frames': 1,  # Process every frame (0 causes div-by-zero!)
            
            # FAST Detection
            'fast_threshold': 10, # Abbassato da 15 a 10 per estrarre più corner su pavimenti piatti
            'max_features': 800,
            
            # KLT Tracking
            'klt_win_size': 31,
            'enable_floor_filter': True,  # ECO00013: filtro pavimento attivo
            'camera_height': 0.08,
            'camera_pitch': 0.0,
            # floor_z_threshold: usa il default del codice (0.03m) — non override
            'klt_max_level': 4,
            'klt_max_error': 15.0,
            'fb_threshold': 1.5,
            
            # Depth
            # ECO00014: rimossi override 'enable_depth_filter: False' e 'enable_floor_filter: False'
            # I default del codice (ECO00013) sono corretti: floor_z_threshold=0.03, floor_filter=True
            
            # LaserScan Region (Tuning ECO00006)
            'scan_height': 50,
            # Spostato in basso rispetto al centro (positivo=in basso)
            'scan_y_offset': 52,
            
            # Motion Gate (ZUPT)
            'enable_motion_gate': True,
            'imu_gyro_threshold': 0.02,
            'imu_accel_threshold': 0.15,
            'cmd_vel_timeout': 0.5,
            
            # YOLO e Debuging
            'enable_yolo': False,
            'yolo_blob_path': yolo_blob,  # PASSED EXPLICITLY
            # FLAG PER DISABILITARE I TOPIC DEBUG: 
            # Imposta a False per disattivare /vo/debug_view/compressed e risparmiare CPU/Banda
            'publish_debug': True,
        }],
        remappings=[
            ('imu', '/oak/imu/data'),
            # Camera topic remapping for Gazebo
            ('/rgb/image', PythonExpression(["'/oak/stereo/image_raw' if '", LaunchConfiguration('sim_mode'), "' == 'true' else '/rgb/image'"])),
            ('/camera/camera_info', PythonExpression(["'/oak/stereo/camera_info' if '", LaunchConfiguration('sim_mode'), "' == 'true' else '/camera/camera_info'"])),
            ('/camera/depth/image_raw', PythonExpression(["'/oak/stereo/image_depth' if '", LaunchConfiguration('sim_mode'), "' == 'true' else '/camera/depth/image_raw'"])),
        ]
    )
    
    # ================================================
    # IMU MADGWICK FILTER
    # ================================================
    madgwick = TimerAction(
        period=1.0,
        actions=[Node(
            package='robopy_controller',
            executable='madgwick_node',
            name='madgwick_filter',
            output='screen',
            parameters=[{
                'input_topic': '/oak/imu/data',
                'output_topic': '/imu/data',
                'frame_id': 'imu_link',
                'beta': 0.1,
                'use_mag': False,
                'publish_tf': False,
            }]
        )]
    )
    
    # ================================================
    # ECO00018: RTAB-Map RGBD ODOMETRY rimosso
    # fast_flow_vo + emu forniscono velocità all'EKF
    # rgbd_odometry andava in conflitto causing "rubber banding"
    # ================================================
    
    # ================================================
    # ECO00020: EKF rimosso. fast_flow_vo pubblica direttamente 
    # TF odom -> base_link. Motivo: l'EKF introduceva ritardi e 
    # falsi movimenti laterali basati su incongruenze dei sensori.
    # ================================================
    
    # ================================================
    # RTAB-MAP SLAM (loop closure)
    # ================================================
    rtabmap = TimerAction(
        period=3.0,
        actions=[OpaqueFunction(function=lambda context: [
            Node(
                package='rtabmap_slam',
                executable='rtabmap',
                name='rtabmap',
                output='screen',
                arguments=['--delete_db_on_start'] if LaunchConfiguration('delete_db').perform(context).lower() == 'true' else [],
                parameters=[
                    os.path.join(pkg_share, 'config', 'rtabmap.yaml'),
                    {
                        'Mem/IncrementalMemory': 'false' if LaunchConfiguration('localization').perform(context).lower() == 'true' else 'true',
                        'Mem/InitWMWithAllNodes': 'true' if LaunchConfiguration('localization').perform(context).lower() == 'true' else 'false',
                        'database_path': config_utils.get_path('RTABMAP_DB_PATH', '/home/robopy/.ros/rtabmap.db'),
                    }
                ],
                remappings=[
                    ('rgb/image', PythonExpression(["'/oak/stereo/image_raw' if '", LaunchConfiguration('sim_mode'), "' == 'true' else '/rgb/image'"])),
                    ('rgb/camera_info', PythonExpression(["'/oak/stereo/camera_info' if '", LaunchConfiguration('sim_mode'), "' == 'true' else '/camera/camera_info'"])),
                    ('depth/image', PythonExpression(["'/oak/stereo/image_depth' if '", LaunchConfiguration('sim_mode'), "' == 'true' else '/camera/depth/image_raw'"])),
                    ('scan', '/scan'),
                ],
                sigterm_timeout='60.0',
                sigkill_timeout='60.0'
            )
        ])]
    )
    
    # ================================================
    # ALTRI NODI
    # ================================================
    motor_control = TimerAction(
        period=4.0,
        actions=[Node(
            package='robopy_controller',
            executable='smart_buildhat_driver',
            name='smart_buildhat_driver',
            output='screen',
            parameters=[{
                # ECO00023: Open-loop bang-bang (no PID, no encoder feedback)
                'min_pwm': 70.0,       # Minimo per vincere l'attrito
                'max_pwm': 100.0,      # Massimo — motori piccoli usano tutto
                'angular_mix_factor': 0.5,  # Mix rotazione/lineare
            }]
        )]
    )
    
    foxglove = TimerAction(
        period=5.0,
        actions=[Node(
            package='foxglove_bridge',
            executable='foxglove_bridge',
            name='foxglove_bridge',
            output='log',
            parameters=[{'port': 8765}]
        )]
    )
    

    # ================================================
    # NAV2 STACK (Navigation) - ENABLE ON DEMAND
    # ================================================
    def launch_nav2(context, *args, **kwargs):
        if LaunchConfiguration('enable_nav2').perform(context).lower() == 'true':
            # File parametri Nav2 ottimizzato per 4GB RAM (RPP, ObstacleLayer 2D)
            nav2_params_file = os.path.join(pkg_share, 'config', 'nav2_params_jazzy.yaml')
            # Use our custom launch file to avoid launching unconfigured nodes
            custom_nav2_launch = os.path.join(pkg_share, 'launch', 'custom_nav2_launch.py')
            
            return [IncludeLaunchDescription(
                PythonLaunchDescriptionSource(custom_nav2_launch),
                launch_arguments={
                    'params_file': nav2_params_file,
                    'use_sim_time': 'false',
                    'autostart': 'true',
                    'use_respawn': 'true',  # Auto-restart crashed nodes
                }.items()
            )]
        return []

    nav2_stack = TimerAction(
        period=45.0,  # Wait for RTAB-Map odometry TF to be ready (increased for stability)
        actions=[OpaqueFunction(function=launch_nav2)]
    )
    
    # Ensure all Nav2 lifecycle nodes reach active state
    # The lifecycle_manager may timeout on Pi5 due to slow costmap initialization
    nav2_ensure_active = TimerAction(
        period=70.0,  # 45s (Nav2 start delay) + 25s (activation buffer)
        actions=[OpaqueFunction(function=lambda context, *a, **kw: [
            ExecuteProcess(
                cmd=['bash', '-c', '''
source /home/robopy/ros2_venv/bin/activate 2>/dev/null
source /home/robopy/robopy/robopi_controller/robopy_controller_host/install/setup.bash 2>/dev/null
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml

for node in controller_server planner_server behavior_server bt_navigator global_costmap/global_costmap local_costmap/local_costmap; do
    state=$(ros2 lifecycle get /$node 2>/dev/null | grep -oP '^\w+')
    if [ "$state" = "unconfigured" ]; then
        echo "[NAV2-ENSURE] Configuring /$node..."
        ros2 lifecycle set /$node configure 2>/dev/null
        sleep 3
        state="inactive"
    fi
    if [ "$state" = "inactive" ]; then
        echo "[NAV2-ENSURE] Activating /$node..."
        ros2 lifecycle set /$node activate 2>/dev/null
        sleep 2
    fi
    final=$(ros2 lifecycle get /$node 2>/dev/null | grep -oP '^\w+')
    echo "[NAV2-ENSURE] /$node → $final"
done
echo "[NAV2-ENSURE] Done."
'''],
                output='screen'
            )
        ] if LaunchConfiguration('enable_nav2').perform(context).lower() == 'true' else [])]
    )
    
    # Percezione: depthimage_to_laserscan (da perception_composition.launch.py)
    perception_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_share, 'launch', 'perception_composition.launch.py')
        )
    )
    
    # Prepara l'ambiente per GStreamer esportando GST_PLUGIN_PATH
    if 'GST_PLUGIN_PATH' not in os.environ:
        os.environ['GST_PLUGIN_PATH'] = '/usr/local/lib/gstreamer-1.0:/usr/local/lib/aarch64-linux-gnu/gstreamer-1.0:'

    # audio_capture_node e wake_word_node sono stati sostituiti dal nuovo VUI
    # che gestisce nativamente acquisizione, WW e pubblicazione.
    respeaker_vui = Node(
        package='robopy_controller',
        executable='respeaker_vui_node',
        name='respeaker_vui_node',
        output='screen'
    )

    robot_ai_node = Node(
        package='robopy_controller',
        executable='robot_ai_node',
        name='robot_ai_orchestrator',
        output='screen',
        emulate_tty=True,
        remappings=[
            ('/camera/image_raw', '/rgb/image'),
            ('/ai/input/text', '/robopy/conversation_rx'),
            ('/ask_visual_question', '/memory/visual_ask'),
            ('/memory_search', '/memory/search')
        ]
    )

    homeassistant_node = Node(
        package='robopy_controller',
        executable='homeassistant_node',
        name='homeassistant_node',
        output='screen',
        parameters=[{'update_interval': 150.0}]
    )

    servo_coda_node = Node(
        package='robopy_controller',
        executable='servo_coda_node',
        name='servo_coda_node',
        output='screen',
        # servo_pin=18 (default), calibration_wag=False
        parameters=[{'servo_pin': 18}]
    )

    foxglove_bridge = Node(
        package='robopy_controller',
        executable='foxglove_nav2_bridge',
        name='foxglove_nav2_bridge',
        output='screen'
    )

    # wake_word_node eliminato (integrato in respeaker_vui_node)

    ultrasonic_node = Node(
        package='robopy_controller',
        executable='ultrasonic_sensor',
        name='ultrasonic_sensor',
        output='screen'
    )

    # ── ReSpeaker Lite — Hardware Wake Word + LED + Speaker ────────────
    # Bridge UART bidirezionale con XIAO ESP32S3 su /dev/ttyACM0.
    # Gestisce: TRIGGER_JARVIS → /ai/input/mic_mute, LED feedback,
    #            heartbeat watchdog. Speaker USB gestito da TTS service.
    respeaker_node = Node(
        package='robopy_controller',
        executable='respeaker_interface_node',
        name='respeaker_interface_node',
        output='screen',
        parameters=[{
            'uart_port': '/dev/ttyACM0',
            'uart_baud': 115200,
            'enabled': False,
        }]
    )

    return LaunchDescription([
        *args,  # Include declared launch arguments
        
        # Force use_sim_time if sim_mode is true
        SetParameter('use_sim_time', LaunchConfiguration('sim_mode')),
        
        robot_state_publisher,
        camera_tf,
        imu_tf,
        
        # ORDINE CRITICO:
        oak_camera,         # 1. Camera + velocity (30Hz)
        perception_launch,  # 1b. depth→LaserScan per costmap Nav2
        madgwick,           # 2. IMU filter (50Hz)
        rtabmap,            # 5. SLAM (TF map→odom)
        nav2_stack,         # 6. Navigation (RPP + NavfnPlanner)
        nav2_ensure_active, # 6b. Ensure Nav2 nodes reach active on Pi5
        
        motor_control,
        foxglove,
        foxglove_bridge,
        
        # Hardware specific nodes - disabled in sim_mode
        TimerAction(period=5.0, actions=[OpaqueFunction(function=lambda context: [
            respeaker_vui,
            respeaker_node,
            robot_ai_node,
            homeassistant_node,
            servo_coda_node,
            ultrasonic_node,
        ] if LaunchConfiguration('sim_mode').perform(context).lower() == 'false' else [])]),
        
        ultrasonic_tf,
    ])
