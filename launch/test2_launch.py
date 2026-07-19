#!/usr/bin/env python3
# test2_launch.py
# SuperPoint VO + IMU → EKF → RTAB-Map (solo loop closure)

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

    database_path = os.path.expanduser('~/.ros/rtabmap.db')

    superpoint_blob = os.path.join(pkg_share, 'models', 'superpoint_raw.blob')
    yolo_blob = os.path.join(pkg_share, 'models', 'yolov8n-seg.superblob')

    nav2_velocity_smoother_config = os.path.join(
        pkg_share, 'config', 'nav2_velocity_smoother.yaml'
    )

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
    # SUPERPOINT NODE (ODOMETRIA VISIVA PRIMARIA)
    # =========================================================================

    superpoint_node = Node(
        package='robopy_controller',
        executable='superpoint_node',
        name='superpoint_node',
        output='screen',
        parameters=[{
            # ---------------- CAMERA ----------------
            'publish_camera_info': True,
            'camera_info_file': '',

            # ---------------- PERFORMANCE ----------------
            'fps': 18,
            'imu_rate': 100,

            # ---------------- FEATURE EXTRACTION ----------------
            'use_enhanced_extraction': True,
            'feature_threshold': 0.015,
            'max_features': 300,
            'use_harris_fallback': True,

            'grid_size': 20,
            'max_per_cell': 2,
            'border': 15,

            # ---------------- MATCHING ----------------
            'use_hybrid_matching': True,
            'matcher_type': 'hybrid',
            'min_matches_for_tracking': 8,
            'flann_match_ratio': 0.70,

            'bf_cross_check': False,
            'bf_norm_type': 'L2',

            # ---------------- ODOMETRIA ----------------
            'publish_visual_odom': True,
            'min_keypoints_for_odom': 5,
            'adaptive_matching': True,

            'max_translation_per_frame': 1.0,
            'max_rotation_per_frame': 1.0,

            # ---------------- DEBUG ----------------
            'debug_level': 'debug',
            'publish_superpoint_debug': True,
            'publish_keypoints_cloud': True,
            'publish_matched_cloud': True,
            'publish_matches_visualization': True,

            # ---------------- DEPTH ----------------
            'publish_depth': True,
            'publish_depth_normalized': False,
            'depth_out_size': '320x200',
            'mono_out_size': '320x200',

            # ---------------- SUPERPOINT ----------------
            'superpoint_side': 'left',
            'superpoint_blob': superpoint_blob,
            'descriptor_dim': 256,

            # ---------------- PUBBLICAZIONE ----------------
            'publish_mono': True,
            'publish_features': True,
            'use_imu': True,
            'use_rtabmap_format': True,

            # ---------------- YOLO (OPZIONALE) ----------------
            'use_yolo_segmentation': True,
            'yolo_blob': yolo_blob,
            'yolo_confidence_threshold': 0.5,

            # ---------------- BA ----------------
            'use_bundle_adjustment': True,
        }]
    )

    # =========================================================================
    # MADGWICK FILTER (IMU RAW → /imu/data)
    # =========================================================================

    madgwick_node = TimerAction(
        period=2.0,
        actions=[
            Node(
                package='robopy_controller',
                executable='madgwick_node',
                name='madgwick_filter',
                output='screen',
                parameters=[{
                    'input_topic': '/imu/raw',
                    'output_topic': '/imu/data',
                    'frame_id': 'imu_link_corrected',
                    'beta': 0.1,
                    'rate': 200.0,
                    'calibration_samples': 200,
                    'use_magnetometer': False,
                }]
            )
        ]
    )

    # =========================================================================
    # EKF — UNICA AUTORITÀ SU odom → base_link
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
    # RTAB-MAP — SOLO LOOP CLOSURE (map → odom)
    # =========================================================================

    rtabmap_node = TimerAction(
        period=8.0,
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

                        'subscribe_rgb': True,
                        'subscribe_depth': True,
                        'subscribe_odom': True,
                        'subscribe_features': True,

                        'publish_tf': True,
                        'use_odometry_tf': False,

                        # SOLO LOOP CLOSURE
                        'Reg/Strategy': '0',
                        'Reg/Force3DoF': 'true',

                        'Kp/DetectorStrategy': '6',
                        'Vis/FeatureType': '6',

                        'RGBD/DepthMin': '0.3',
                        'RGBD/DepthMax': '4.0',

                        'Optimizer/Strategy': '1',
                        'Optimizer/Iterations': '5',

                        'Mem/IncrementalMemory': 'true',
                        'Rtabmap/DetectionRate': '2.0',
                    }
                ],
                remappings=[
                    ('rgb/image', '/camera/image_raw'),
                    ('rgb/camera_info', '/camera/camera_info'),
                    ('depth/image', '/depth/image_raw'),
                    ('odom', '/odom'),
                    ('features', '/rtabmap/features'),
                ],
                arguments=['-d']
            )
        ]
    )

    # =========================================================================
    # TF STATICI (STANDARD ROS CORRETTO)
    # =========================================================================

    tf_static_nodes = [

        # base_footprint → base_link
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_footprint_to_base_link',
            arguments=['0', '0', '0', '0', '0', '0',
                       'base_footprint', 'base_link']
        ),

        # base_link → camera_link
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_to_camera',
            arguments=['0.1', '0', '0.15', '0', '-0.1745', '0',
                       'base_link', 'camera_link']
        ),

        # camera_link → camera_optical_frame
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='camera_to_optical',
            arguments=['0', '0', '0',
                       '-1.5708', '0', '-1.5708',
                       'camera_link', 'camera_optical_frame']
        ),

        # base_link → imu_link
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_to_imu',
            arguments=['0', '0', '0', '0', '0', '0',
                       'base_link', 'imu_link']
        ),
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
    
    bluedot_node = Node(
        package='robopy_controller',
        executable='bluedot_node',
        output='screen'
    )

    # H) Teleop Bridge (Foxglove → Motor Control)
    teleop_bridge_node = Node(
        package='robopy_controller',
        executable='teleop_bridge_node',
        name='teleop_bridge',
        output='screen',
        parameters=[{
            'teleop_topic': '/cmd_vel',  # Cambiato da /teleop/cmd_vel a /cmd_vel per Foxglove
            'output_topic': 'bluedot_input',
            'cmd_timeout_sec': 0.5,
            'scale_linear': 1.0,
            'scale_angular': 1.0,
            'invert_angular': False,
        }]
    )

    # I) Nav2 Bridge (Nav2 → Motor Control)
    nav2_bridge_node = Node(
        package='robopy_controller',
        executable='nav2_bridge_node',
        name='nav2_bridge',
        output='screen',
        parameters=[
            nav2_velocity_smoother_config,
            {
                'teleop_topic': '/cmd_vel',  # Output di Nav2
                'output_topic': 'bluedot_input',
                'cmd_timeout_sec': 0.5,
                'scale_linear': 1.0,
                'scale_angular': 1.0,
                'invert_angular': False,
            }
        ]
    )

    #image compressor node
    image_compressor_node = Node(
        package='robopy_controller',
        executable='image_compressor_node',
        name='image_compressor',
        output='screen',
        parameters=[{
            'ui_fps': 10.0, # FPS di pubblicazione per l'interfaccia utente
            'jpeg_quality': 50, # Qualità della compressione JPEG
            'resize_factor': 1.0, # Ridimensiona le immagini prima della compressione
            'use_png_for_depth': True,
        }]
    )

    # =========================================================================
    # LAUNCH DESCRIPTION
    # =========================================================================

    return LaunchDescription([
        robot_state_publisher,
        superpoint_node,
        *tf_static_nodes,

        madgwick_node,
        ekf_node,

        # rgbd_odometry,        # opzionale
        rtabmap_node,           # loop closure

        bluedot_node,
        motor_control_node,
        #teleop_bridge_node,

        foxglove_bridge,
        image_compressor_node,
    ])
