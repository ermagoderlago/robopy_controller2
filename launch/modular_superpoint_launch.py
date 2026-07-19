#!/usr/bin/env python3
# modular_superpoint_launch.py
#
# Launches the OakSuperPointOdometry Node + EKF + RTAB-Map
#

import os
from launch import LaunchDescription
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory
from launch.actions import IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import AnyLaunchDescriptionSource


def generate_launch_description():

    pkg_share = get_package_share_directory('robopy_controller')

    # =========================================================================
    # FILE DI CONFIGURAZIONE
    # =========================================================================

    urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')
    with open(urdf_file, 'r') as f:
        robot_description_content = f.read()

    robot_description = ParameterValue(robot_description_content, value_type=str)

    ekf_config_path = os.path.join(pkg_share, 'config', 'ekf.yaml')
    rtabmap_config = os.path.join(pkg_share, 'config', 'rtabmap_params.yaml')

    database_path = os.path.expanduser('~/.ros/rtabmap_modular.db') 

    # Blob Paths (Check actual filenames)
    superpoint_blob = os.path.join(pkg_share, 'models', 'superpoint_v1_shave4.blob')
    # If file not found, fallback to generic name or existing one
    if not os.path.exists(superpoint_blob):
         # Try alternative name found in file list previously
         # "superpoint_480x360_raw.blob" ?
         # Let's use the one from previous launch file
         superpoint_blob = os.path.join(pkg_share, 'models', 'superpoint_480x360_raw.blob')

    yolo_blob = os.path.join(pkg_share, 'models', 'yolo_seg.blob') #yolov8n_seg_512x288.blob
    # Check if exists
    if not os.path.exists(yolo_blob):
        yolo_blob = os.path.join(pkg_share, 'models', 'yolov8n_seg_512x288.blob')


    # =========================================================================
    # ROBOT STATE PUBLISHER
    # =========================================================================

    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        output='screen',
        parameters=[{'robot_description': robot_description}]
    )

    # =========================================================================
    # OAK SUPERPOINT ODOMETRY NODE
    # =========================================================================
    
    # =========================================================================
    # NODE PYTHON LEGACY (COMMENTATO)
    # =========================================================================
    """
    oak_node = Node(
        package='robopy_controller',
        executable='oak_superpoint_odometry_node',
        name='oak_superpoint_odometry',
        output='screen',
        parameters=[{
            'fps': 20.0,  # Optimized for RPi5 + OAK-D Lite
            'yolo_blob_path': yolo_blob,
            'superpoint_blob_path': superpoint_blob,
            'superpoint_conf_thresh': 0.015,
            'superpoint_nms_dist': 4,
            'min_matches': 15,
            'min_inliers': 8,
            'enable_imu': True,

            # Per massima performance
            'enable_yolo': False,
            'use_bruteforce': True,
            'filter_alpha': 0.25,
            'min_features': 20,
            'enable_clahe': True,
            'depth_fps': 15.0,        # Higher FPS (was bottleneck)
            'depth_resolution': '400p', # 400p is standard for OAK-D Lite
            'depth_pub_width': 320,     # Risoluzione pubblicazione depth
            'depth_pub_height': 200,

            # Per VO + detection
            #'enable_yolo': True,
            #'yolo_frequency': 2.0,
            #'use_bruteforce': True,

            # NN input sizes matching existing blob files
            'sp_w': 480,   # superpoint_480x360_raw.blob
            'sp_h': 360,
            'yolo_w': 320, # yolo_seg.blob expects 320x320
            'yolo_h': 320,
        }],
        remappings=[
             ('/superpoint/odometry', '/vo'),
             ('/camera/rgb/image_raw', '/rgb/image'),
        ]
    )
    """

    # =========================================================================
    # OAK SUPERPOINT ODOMETRY NODE (C++ NATIVE)
    # =========================================================================
    
    oak_node = Node(
        package='robopy_controller',
        executable='oak_superpoint_odometry_cpp',
        name='oak_superpoint_odometry',
        output='screen',
        parameters=[{
            'superpoint_blob_path': superpoint_blob,
            'yolo_blob_path': yolo_blob,
            'enable_yolo': False, # Come richiesto per performance
            'yolo_frequency': 2.0,
            
            # Hybrid Odometry (ORB + SuperPoint)
            'use_orb_primary': True,      # High FPS tracking
            'superpoint_relocalization': True, # Robust recovery
            'max_orb_features': 500,
            'vo_skip_frames': 1,          # No skipping needed for ORB
            'lost_tracking_threshold': 10,
            'relocalization_inliers': 30,

            # Standard Odometry params
            'min_features': 30,
            'min_inliers': 12,
            'min_depth': 0.3,
            'max_depth': 8.0,
            'filter_alpha': 0.25,
            'use_bruteforce': False,
            'enable_clahe': False,
            'publish_tf': False, # EKF publishes odom->base_link (fused VO+IMU)
            
            # Camera Config
            'depth_fps': 30.0,
            'depth_resolution': '400p',
            'depth_pub_width': 320,
            'depth_pub_height': 200,
        }],
        remappings=[
             # ('/vo/odom', '/vo'), # REMOVED: Match EKF config which expects /vo/odom
             ('/camera/rgb/image_raw', '/rgb/image'),
        ]
    )

    # =========================================================================
    # EKF — Fuses VO (/vo) + IMU (if available) -> /odom
    # =========================================================================

    ekf_node = Node(
        package='robot_localization',
        executable='ekf_node',
        name='ekf_filter_node',
        output='screen',
        parameters=[ekf_config_path],
        remappings=[
            ('odometry/filtered', '/odom')
        ]
    )

    # =========================================================================
    # IMU FILTER — Madgwick AHRS
    # =========================================================================
    # Note: Raw IMU data now published by C++ node at /oak/imu/data
    
    # OAK-D IMU Publisher - DISABLED (now integrated in C++ node)
    # oak_imu_node = Node(
    #     package='robopy_controller',
    #     executable='IMU_oakd_node.py',
    #     name='oak_imu_publisher',
    #     output='screen',
    #     parameters=[{
    #         'imu_fps': 100,
    #         'frame_id': 'imu_link',
    #         'topic': '/oak/imu/data',
    #         'calibrate_gyro': True,
    #     }]
    # )
    
    # Madgwick AHRS Filter (orientation estimation)
    madgwick_node = Node(
        package='robopy_controller',
        executable='madgwick_node',  # Fixed: no .py extension
        name='madgwick_filter',
        output='screen',
        parameters=[{
            'input_topic': '/oak/imu/data',
            'output_topic': '/imu/data',
            'frame_id': 'imu_link',
            'beta': 0.04,  # Madgwick gain (tune if needed)
            'rate': 200.0,
            'calibration_samples': 100,
        }]
    )

    # =========================================================================
    # RTAB-MAP — LOOP CLOSURE
    # =========================================================================
    
    # We need to bridge data for RTAB-Map
    # RTAB-Map expects RGB + Depth + CameraInfo + Odom
    # Oak Node publishes:
    # - /camera/depth/image_raw
    # - /camera/camera_info
    # - /yolo/detections (Not used directly by standard RTAB w/o customization)
    # - /superpoint/odometry -> /vo -> EKF -> /odom
    # But where is RGB? 
    # OakNode publishes /yolo/detections but NOT raw RGB unless we enable it.
    # User said: "Non pubblicare streaming video RGB (risparmia USB)."
    # Wait. RTAB-Map needs RGB for loop closure (visual).
    # If RGB is disabled, RTAB-Map works in Odom-only or Depth-only mode?
    # RTAB-Map usually needs RGB.
    # The user request said: "Non pubblicare streaming video RGB (risparmia USB)."
    # "Preview... per Yolo...".
    # "Mono Left... per Odometria".
    # Maybe we can publish Mono Left as "RGB" for RTAB-Map? It supports Mono.
    # oak_superpoint_odometry_node publishes debug view (Compressed). 
    # Maybe we SHOULD publish Mono Frame as ImageRaw for RTABMap?
    # The node has 'mono_frame' available.
    # CHECK: Did I include Mono Publisher in the Node?
    # I did: self.pub_sp_debug (Compressed). RTABMap needs Raw usually? Or Compressed?
    # RTABMap supports compressed.
    
    rtabmap_node = TimerAction(
        period=5.0,
        actions=[
            Node(
                package='rtabmap_slam',
                executable='rtabmap',
                name='rtabmap',
                output='screen',
                parameters=[
                    rtabmap_config,
                    {
                        'database_path': database_path,
                        'frame_id': 'base_link',
                        'odom_frame_id': 'odom',
                        'map_frame_id': 'map',

                        'subscribe_depth': True,
                        'subscribe_rgb': False, # No RGB
                        # Can we use Mono?
                        # If subscribe_rgb is false, how does it do Loop Closure?
                        # Setup for RGB-D but with Mono?
                        # Let's set subscribe_rgb = True and remap rgb/image to something?
                        # User constraint: "Non pubblicare streaming video RGB".
                        
                        'subscribe_odom': True,
                        'publish_tf': True,
                        
                        # Optimization
                        'Mem/IncrementalMemory': 'true',
                    }
                ],
                remappings=[
                    ('depth/image', '/camera/depth/image_raw'),
                    ('odom', '/odom'),
                    ('rgb/camera_info', '/camera/camera_info'), # Left Intrinsics
                ],
                arguments=['-d']
            )
        ]
    )

    # =========================================================================
    # TF STATICI
    # =========================================================================

    tf_static_nodes = [
        # base_footprint -> base_link
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_footprint_to_base_link',
            arguments=['0', '0', '0', '0', '0', '0',
                       'base_footprint', 'base_link']
        ),
        # base_link -> left_optical_frame (Camera Center)
        # Adjust per actual mounting. Assuming Camera is at front.
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_to_camera',
            arguments=['0.1', '0', '0.15', '-1.5708', '0.0', '-1.4173', #modificato
            #arguments=['0.1', '0', '0.15', '-1.5708', '0.0', '-1.5708', #modificato
                       'base_link', 'left_optical_frame']
        ),
        # camera_color_optical_frame for YOLO (if different)
        Node(
             package='tf2_ros',
             executable='static_transform_publisher',
             name='left_to_color',
             arguments=['0', '0', '0', '0', '0', '0',
                        'left_optical_frame', 'camera_color_optical_frame']
        )
    ]

    # =========================================================================
    # FOXGLOVE BRIDGE
    # =========================================================================

    foxglove_launch_path = os.path.join(
        get_package_share_directory('foxglove_bridge'),
        'launch',
        'foxglove_bridge_launch.xml'
    )

    foxglove_bridge = IncludeLaunchDescription(
        AnyLaunchDescriptionSource(foxglove_launch_path),
        launch_arguments={'port': '8765', 'host': '0.0.0.0'}.items()
    )

    motor_control_node = Node(
        package='robopy_controller',
        executable='waveshare_motor_driver',
        name='waveshare_motor_driver',
        output='screen',
        parameters=[{
            'serial_port': '/dev/ttyUSB0',
            'baud_rate': 115200,
            'wheel_radius': 0.0325,
            'wheel_separation': 0.16,
            'ticks_per_rev': 1440,
            'invert_right_encoder': True,
        }]
    )

    # H) Robot AI Orchestrator
    robot_ai_node = Node(
        package='robopy_controller',
        executable='robot_ai_node.py',
        name='robot_ai_orchestrator',
        output='screen',
        emulate_tty=True
    )

    # =========================================================================
    # LAUNCH DESCRIPTION
    # =========================================================================

    return LaunchDescription([
        oak_node, 
        robot_state_publisher,
        *tf_static_nodes,
        madgwick_node,         # IMU filter (receives from C++ node)
        ekf_node,
        rtabmap_node, # Disabled for now to test Odometry first
        motor_control_node,
        #robot_ai_node,
        foxglove_bridge,
    ])
