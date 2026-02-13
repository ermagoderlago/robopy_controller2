#!/usr/bin/env python3
#fast_flow_launch.py

"""
RTAB-Map System with CORRECTED TF and data flow
- FAST+KLT VO → velocity only
- RTAB-Map rgbd_odom → pose + TF
- IMU → orientation
"""

import os
from launch import LaunchDescription
from launch.actions import TimerAction, DeclareLaunchArgument, OpaqueFunction, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_share = get_package_share_directory('robopy_controller')
    
    # Launch Arguments
    args = [
        DeclareLaunchArgument('delete_db', default_value='false', description='Delete RTAB-Map database on start'),
        DeclareLaunchArgument('localization', default_value='false', description='Location mode (no mapping)'),
        DeclareLaunchArgument('enable_nav2', default_value='false', description='Enable Navigation2 stack'),
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
        parameters=[{
            'camera_frame': 'oak_left_camera_optical_frame',
            'odom_frame': 'odom',
            'base_frame': 'base_link',
            
            # CRITICO: NO TF publishing! RTAB-Map lo fa
            'publish_tf': False,
            
            # Camera settings
            'camera_fps': 10.0, # Reduced to 10Hz to fix X_LINK_ERROR
            'skip_frames': 1,  # Process every frame (0 causes div-by-zero!)
            
            # FAST Detection
            'fast_threshold': 15,
            'max_features': 800,
            
            # KLT Tracking
            'klt_win_size': 31,
            'klt_max_level': 4,
            'klt_max_error': 15.0,
            'fb_threshold': 1.5,
            
            # Depth
            'enable_depth_filter': False,
            'enable_floor_filter': False,
            
            # Motion Gate (ZUPT)
            'enable_motion_gate': True,
            'imu_gyro_threshold': 0.02,
            'imu_accel_threshold': 0.15,
            'cmd_vel_timeout': 0.5,
            
            # YOLO (DISABLED TO ISOLATE X_LINK_ERROR)
            'enable_yolo': False,
            'yolo_blob_path': yolo_blob,  # PASSED EXPLICITLY
            'publish_debug': True,
        }],
        remappings=[
            ('imu', '/oak/imu/data'),
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
    # RTAB-Map RGBD ODOMETRY
    # Pubblica: /rtabmap/odom (POSE con basso drift)
    #           TF: odom → base_link (SOLO LUI!)
    # ================================================
    rgbd_odom = TimerAction(
        period=1.5,
        actions=[Node(
            package='rtabmap_odom',
            executable='rgbd_odometry',
            name='rgbd_odometry',
            output='screen',
            arguments=['--ros-args', '--log-level', 'WARN'],
            parameters=[{
                'frame_id': 'base_link',
                'odom_frame_id': 'odom',
                
                # SOLO LUI PUBBLICA TF odom→base_link!
                'publish_tf': True,
                
                'wait_for_transform': 0.2,
                'approx_sync': True,
                'queue_size': 10,
                
                # Odometry strategy
                'Odom/Strategy': '0',
                'Odom/ResetCountdown': '1',
                'Odom/GuessSmoothingDelay': '0.5',
                
                # Visual features
                'Vis/FeatureType': '6',
                'Vis/MaxFeatures': '1000',
                'Vis/MinInliers': '20',
                'Vis/InlierDistance': '0.05',
                
                # ICP refinement (reduced iterations with guess)
                'Reg/Strategy': '1',
                'Icp/VoxelSize': '0.05',
                'Icp/PointToPlane': 'true',
                'Icp/Iterations': '5',  # Reduced: guess gives good start
                'Icp/Epsilon': '0.001',
            }],
            remappings=[
                ('rgb/image', '/rgb/image'),
                ('rgb/camera_info', '/camera/camera_info'),
                ('depth/image', '/camera/depth/image_raw'),
                ('odom', '/rtabmap/odom'),
            ]
        )]
    )
    
    # ================================================
    # EKF FUSION (TRIADE SENSORI)
    # Input 1: /fast_flow/velocity (30Hz, velocity)
    # Input 2: /rtabmap/odom (15Hz, pose)
    # Input 3: /imu/data (50Hz, orientation)
    # Output: /odom_filtered (50Hz)
    # ================================================
    ekf_node = TimerAction(
        period=2.0,
        actions=[Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_localization',
            output='screen',
            parameters=[{
                'frequency': 50.0,
                'two_d_mode': True,
                
                'map_frame': 'map',
                'odom_frame': 'odom',
                'base_link_frame': 'base_link',
                'world_frame': 'odom',
                
                # EKF NON pubblica TF (rgbd_odom lo fa già)
                'publish_tf': False,
                'publish_acceleration': False,
                
                # ================== INPUT 1: VELOCITY ==================
                'odom0': '/fast_flow/velocity',
                'odom0_config': [
                    False, False, False,  # NO position
                    False, False, False,  # NO orientation
                    True,  True,  False,  # SÌ velocity x,y
                    False, False, True,   # SÌ yaw rate
                    False, False, False,
                ],
                'odom0_differential': False,
                'odom0_relative': False,
                
                # ================== INPUT 2: POSE ==================
                'odom1': '/rtabmap/odom',
                'odom1_config': [
                    True,  True,  False,  # SÌ position x,y
                    False, False, False,  # NO orientation (usa IMU)
                    False, False, False,  # NO velocity (usa VO!)
                    False, False, False,
                    False, False, False,
                ],
                'odom1_differential': False,
                
                # ================== INPUT 3: IMU ==================
                'imu0': '/imu/data',
                'imu0_config': [
                    False, False, False,
                    False, False, True,   # SÌ yaw
                    False, False, False,
                    False, False, True,   # SÌ yaw_rate
                    False, False, False,
                ],
                'imu0_differential': False,
                'imu0_relative': False,
                
                'process_noise_covariance': [
                    0.03, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0.03, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0.04, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0.02, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0.02, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0.02, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0.02, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0.02, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0.03, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.015, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01,
                ],
            }],
            remappings=[
                ('odometry/filtered', '/odom_filtered'),
            ]
        )]
    )
    
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
                parameters=[{
                    'frame_id': 'base_link',
                    'odom_frame_id': 'odom',
                    'map_frame_id': 'map',
                    
                    'subscribe_rgb': True,
                    'subscribe_depth': True,
                    'subscribe_odom_info': True,
                    'approx_sync': True,
                    'queue_size': 10,
                    
                    'publish_tf': True,
                    
                    'Mem/IncrementalMemory': 'false' if LaunchConfiguration('localization').perform(context).lower() == 'true' else 'true',
                    'Mem/InitWMWithAllNodes': 'true' if LaunchConfiguration('localization').perform(context).lower() == 'true' else 'false',
                    
                    'RGBD/ProximityBySpace': 'true',
                    'RGBD/AngularUpdate': '0.1',
                    'RGBD/LinearUpdate': '0.1',
                    'RGBD/OptimizeMaxError': '3.0',
                    'Optimizer/Strategy': '1',
                    'Optimizer/Iterations': '30',
                    
                    # --- TUNING MAPPING (GHOSTS) ---
                    'Grid/RangeMax': '3.5',
                    'Grid/MaxGroundHeight': '0.10',
                    'Grid/MinGroundHeight': '-0.50',
                    'Grid/MinClusterSize': '20',
                    'Grid/NormalsSegmentation': 'false', 
                    
                    # --- TUNING LOOP CLOSURE ---
                    'Kp/MaxFeatures': '1000',
                    'Vis/FeatureType': '6',
                    'Rtabmap/LoopThr': '0.08',
                    'RGBD/LoopClosureReextractFeatures': 'true',
                }],
                remappings=[
                    ('rgb/image', '/rgb/image'),
                    ('rgb/camera_info', '/camera/camera_info'),
                    ('depth/image', '/camera/depth/image_raw'),
                    ('odom', '/rtabmap/odom'),
                ]
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
            executable='motor_control_node',
            name='motor_control',
            output='screen'
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
            nav2_params_file = os.path.join(pkg_share, 'config', 'nav2_params.yaml')
            nav2_bringup_dir = get_package_share_directory('nav2_bringup')
            
            return [IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    os.path.join(nav2_bringup_dir, 'launch', 'navigation_launch.py')
                ),
                launch_arguments={
                    'params_file': nav2_params_file,
                    'use_sim_time': 'false',
                    'autostart': 'true',
                }.items()
            )]
        return []

    nav2_stack = TimerAction(
        period=8.0,  # Wait for RTAB-Map to be ready
        actions=[OpaqueFunction(function=launch_nav2)]
    )
    
    robot_ai_node = Node(
        package='robopy_controller',
        executable='robot_ai_node',
        name='robot_ai_orchestrator',
        output='screen',
        emulate_tty=True
    )

    homeassistant_node = Node(
        package='robopy_controller',
        executable='homeassistant_node',
        name='homeassistant_node',
        output='screen',
        parameters=[{'update_interval': 150.0}]
    )

    return LaunchDescription([
        *args,  # Include declared launch arguments
        robot_state_publisher,
        camera_tf,
        imu_tf,
        
        # ORDINE CRITICO:
        oak_camera,       # 1. Camera + velocity (30Hz)
        madgwick,         # 2. IMU filter (50Hz)
        rgbd_odom,        # 3. RTAB-Map Odometry (15Hz, TF odom→base_link)
        ekf_node,         # 4. EKF fusion (50Hz, NO TF)
        rtabmap,          # 5. SLAM (TF map→odom)
        nav2_stack,       # 6. Navigation
        
        motor_control,
        foxglove,
        robot_ai_node,
        homeassistant_node,

    ])
