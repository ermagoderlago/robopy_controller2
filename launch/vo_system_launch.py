#!/usr/bin/env python3
"""
FAST Flow VO + RTAB-Map - ARCHITETTURA CORRETTA

FLUSSO CORRETTO:
  VO (/vo/odom RAW) → RTAB-Map (loop closure + TF) → EKF (IMU fusion) → /odom_filtered

FEATURE MANTENUTE:
  - Motion Gate (anti-drift)
  - Floor Filter
  - Servo Coda
  - Robot AI
"""

import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, TimerAction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_share = get_package_share_directory('robopy_controller')
    
    # ================================================
    # LAUNCH ARGUMENTS
    # ================================================
    arg_localization = DeclareLaunchArgument(
        'localization', default_value='false',
        description='Localization mode (true) or mapping mode (false)'
    )
    
    arg_database_path = DeclareLaunchArgument(
        'database_path',
        default_value=os.path.expanduser('~/robopy_maps/current.db'),
        description='Path to RTAB-Map database'
    )
    
    arg_use_imu = DeclareLaunchArgument(
        'use_imu', default_value='true',
        description='Enable IMU fusion'
    )
    
    arg_debug = DeclareLaunchArgument(
        'debug', default_value='false',
        description='Enable debug mode'
    )
    
    # ================================================
    # URDF
    # ================================================
    urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')
    with open(urdf_file, 'r') as f:
        robot_description = ParameterValue(f.read(), value_type=str)
    
    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        parameters=[{'robot_description': robot_description}],
        output='screen'
    )
    
    # ================================================
    # STATIC TF: Camera (CORRETTO con quaternion)
    # Z forward, X right, Y down → X forward, Y left, Z up
    # ================================================
    camera_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_camera_tf',
        arguments=[
            '0.05', '0.0', '0.08',  # x, y, z
            '0.5', '-0.5', '0.5', '0.5',  # qx, qy, qz, qw (REP-103 camera optical frame)
            'base_link', 'oak_left_camera_optical_frame'
        ],
        output='log'
    )
    
    # IMU TF
    imu_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_imu_tf',
        arguments=['0', '0', '0', '0', '0', '0', '1', 'base_link', 'imu_link'],
        output='log'
    )
    
    # ================================================
    # FAST FLOW VO NODE (NO TF - RTAB-Map lo gestisce)
    # ================================================
    fast_flow_vo = Node(
        package='robopy_controller',
        executable='fast_flow_vo_cpp',
        name='fast_flow_vo',
        output='screen',
        parameters=[{
            # Frames
            'odom_frame': 'odom',
            'base_frame': 'base_link',
            'camera_frame': 'oak_left_camera_optical_frame',
            'publish_tf': False,  # RTAB-Map gestisce TF
            
            # FAST Detection
            'fast_threshold': 15,
            'max_features': 400,
            'grid_rows': 6,
            'grid_cols': 8,
            
            # KLT Tracking
            'klt_win_size': 21,
            'klt_max_level': 3,
            'klt_max_error': 12.0,
            'fb_threshold': 1.0,
            
            # Depth
            'min_depth': 0.3,
            'max_depth': 8.0,
            'depth_fps': 30.0,
            
            # Floor Filter
            'enable_floor_filter': True,
            'camera_height': 0.08,
            
            # PnP
            'min_points': 20,
            'min_inliers': 15,
            'reproj_error': 3.0,
            
            # Motion Validation
            'max_translation_per_frame': 0.2,
            'max_rotation_per_frame': 0.52,
            
            # EMA Filter
            'ema_alpha': 0.3,
            
            # State
            'lost_threshold': 5,
            'skip_frames': 1,
            
            # Debug
            'publish_debug': True,
            
            # YOLO
            'enable_yolo': False,
            'yolo_blob_path': os.path.join(pkg_share, 'models', 'yolov6nr1_coco_640x352.blob'),
            'yolo_conf_threshold': 0.5,
            
            # Motion Gate
            'enable_motion_gate': True,
            'imu_gyro_threshold': 0.02,
            'imu_accel_threshold': 0.15,
            'cmd_vel_timeout': 0.5,
        }]
    )
    
    # ================================================
    # IMU MADGWICK FILTER
    # ================================================
    madgwick = TimerAction(
        period=1.0,
        actions=[Node(
            package='imu_filter_madgwick',
            executable='imu_filter_madgwick_node',
            name='imu_filter',
            output='screen',
            parameters=[{
                'use_mag': False,
                'publish_tf': False,
                'world_frame': 'enu',
                'fixed_frame': 'odom',
                'orientation_stddev': 0.01,
                'gain': 0.1,
            }],
            remappings=[
                ('imu/data_raw', '/oak/imu/data'),
                ('imu/data', '/imu/data'),
            ],
            condition=IfCondition(LaunchConfiguration('use_imu'))
        )]
    )
    
    # ================================================
    # RTAB-MAP (RICEVE VO RAW, PUBBLICA TF!)
    # ================================================
    rtabmap = TimerAction(
        period=2.0,
        actions=[Node(
            package='rtabmap_slam',
            executable='rtabmap',
            name='rtabmap',
            output='screen',
            arguments=['--ros-args', '--log-level', 'WARN'],
            parameters=[{
                # Frames
                'frame_id': 'base_link',
                'odom_frame_id': 'odom',
                'map_frame_id': 'map',
                
                # Subscribe settings
                'subscribe_rgb': True,
                'subscribe_depth': True,
                'subscribe_odom': True,
                'approx_sync': True,
                'queue_size': 10,
                
                # Database management
                'database_path': LaunchConfiguration('database_path'),
                
                # Memory mode (mapping vs localization)
                'Mem/IncrementalMemory': PythonExpression([
                    "'false' if '", LaunchConfiguration('localization'),
                    "' == 'true' else 'true'"
                ]),
                'Mem/InitWMWithAllNodes': 'false',
                
                # TF publishing (RTAB-Map gestisce tutto!)
                'publish_tf': True,
                'tf_delay': 0.05,
                'odom_tf_linear_variance': 0.001,
                'odom_tf_angular_variance': 0.001,
                
                # Odometry
                'odom_sensor_sync': False,
                'wait_for_transform': 0.2,
                
                # Visual settings
                'Reg/Strategy': '1',
                'Reg/Force3DoF': 'true',
                'Vis/EstimationType': '1',
                'Vis/CorType': '1',
                'Vis/MaxFeatures': '400',
                'Vis/MinInliers': '10',
                'Vis/InlierDistance': '0.1',
                
                # Loop closure
                'RGBD/NeighborLinkRefining': 'true',
                'RGBD/ProximityBySpace': 'true',
                'RGBD/ProximityMaxGraphDepth': '100',
                'RGBD/AngularUpdate': '0.05',
                'RGBD/LinearUpdate': '0.05',
                'RGBD/OptimizeMaxError': '3.0',
                
                # Optimization
                'Optimizer/Strategy': '1',
                'Optimizer/Iterations': '20',
                'Optimizer/Robust': 'true',
                
                # Grid
                'Grid/Sensor': '0',
                'Grid/RangeMax': '5.0',
                'Grid/CellSize': '0.05',
            }],
            remappings=[
                # RTAB-Map riceve VO RAW!
                ('odom', '/vo/odom'),
                ('rgb/image', '/rgb/image'),
                ('rgb/camera_info', '/camera/camera_info'),
                ('depth/image', '/camera/depth/image_raw'),
            ]
        )]
    )
    
    # ================================================
    # EKF (RICEVE RTAB-Map ODOM, NO TF!)
    # ================================================
    ekf_node = TimerAction(
        period=2.5,
        actions=[Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_vo_imu_fusion',
            output='screen',
            parameters=[{
                'frequency': 50.0,
                'two_d_mode': True,
                
                # Frames
                'map_frame': 'map',
                'odom_frame': 'odom',
                'base_link_frame': 'base_link',
                'world_frame': 'odom',
                
                # NO TF! (RTAB-Map lo fa già)
                'publish_tf': False,
                
                # IMU (orientation only)
                'imu0': '/imu/data',
                'imu0_config': [
                    False, False, False,
                    False, False, True,   # yaw
                    False, False, False,
                    False, False, True,   # yaw rate
                    False, False, False,
                ],
                'imu0_differential': False,
                'imu0_relative': False,
                'imu0_remove_gravitational_acceleration': True,
                
                # RTAB-Map corrected odometry
                'odom0': '/rtabmap/odom',
                'odom0_config': [
                    True, True, False,
                    False, False, False,
                    True, True, False,
                    False, False, False,
                    False, False, False,
                ],
                'odom0_differential': False,
                'odom0_relative': False,
                
                # Process noise
                'process_noise_covariance': [
                    0.05, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0.05, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0.06, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0.03, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0.03, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0.04, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0.025, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0.025, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0.04, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.015, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.015,
                ],
            }],
            remappings=[
                ('odometry/filtered', '/odom_filtered'),
            ],
            condition=IfCondition(LaunchConfiguration('use_imu'))
        )]
    )
    
    # ================================================
    # MOTOR CONTROL
    # ================================================
    motor_control = TimerAction(
        period=3.0,
        actions=[Node(
            package='robopy_controller',
            executable='motor_control_node',
            name='motor_control_node',
            output='screen'
        )]
    )
    
    # ================================================
    # FOXGLOVE BRIDGE
    # ================================================
    foxglove = TimerAction(
        period=4.0,
        actions=[Node(
            package='foxglove_bridge',
            executable='foxglove_bridge',
            name='foxglove_bridge',
            output='log',
            arguments=['--ros-args', '--log-level', 'WARN'],
            parameters=[{'port': 8765, 'address': '0.0.0.0'}]
        )]
    )
    
    # ================================================
    # ROBOT AI NODE
    # ================================================
    robot_ai_node = Node(
        package='robopy_controller',
        executable='robot_ai_node',
        name='robot_ai_orchestrator',
        output='screen',
        emulate_tty=True
    )
    
    # ================================================
    # SERVO CODA
    # ================================================
    servo_coda = TimerAction(
        period=2.0,
        actions=[Node(
            package='robopy_controller',
            executable='servo_coda_node',
            name='servo_coda_node',
            output='screen',
            parameters=[{'servo_pin': 18, 'calibration_wag': True}]
        )]
    )
    
    # ================================================
    # LAUNCH
    # ================================================
    return LaunchDescription([
        # Arguments
        arg_localization,
        arg_database_path,
        arg_use_imu,
        arg_debug,
        
        # Static
        robot_state_publisher,
        camera_tf,
        imu_tf,
        
        # Processing
        fast_flow_vo,
        madgwick,
        rtabmap,
        ekf_node,
        motor_control,
        
        # Utilities
        foxglove,
        robot_ai_node,
        #servo_coda,
    ])
