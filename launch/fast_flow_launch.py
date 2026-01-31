#!/usr/bin/env python3
"""
FAST + Optical Flow Visual Odometry Launch File
NO SuperPoint, NO neural networks, just FAST + KLT
"""

import os
from launch import LaunchDescription
from launch.actions import TimerAction, DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_share = get_package_share_directory('robopy_controller')
    
    # URDF
    urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')
    with open(urdf_file, 'r') as f:
        robot_description = ParameterValue(f.read(), value_type=str)
    
    # ------------------------------------------------
    # ROBOT STATE PUBLISHER
    # ------------------------------------------------
    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        parameters=[{'robot_description': robot_description}],
        output='screen'
    )
    
    # ------------------------------------------------
    # FAST + OPTICAL FLOW VO (NO SuperPoint!)
    # ------------------------------------------------
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
            
            # PnP
            'min_points': 20,
            'min_inliers': 15,
            'reproj_error': 3.0,
            
            # Motion Validation
            'max_translation_per_frame': 0.5,
            'max_rotation_per_frame': 0.52,  # ~30°
            
            # EMA Filter
            'ema_alpha': 0.3,
            
            # State
            'lost_threshold': 5,
            'skip_frames': 1,
        }]
    )
    
    # ------------------------------------------------
    # IMU FILTER (MADGWICK)
    # ------------------------------------------------
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
            }]
        )]
    )
    
    # ------------------------------------------------
    # EKF LOCALIZATION
    # ------------------------------------------------
    ekf_node = TimerAction(
        period=2.0,
        actions=[Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_localization',
            output='screen',
            parameters=[{
                'frequency': 30.0,
                'two_d_mode': True,
                'publish_tf': True,
                
                'map_frame': 'map',
                'odom_frame': 'odom',
                'base_link_frame': 'base_link',
                'world_frame': 'odom',
                
                # IMU (yaw + angular velocity)
                'imu0': '/imu/data',
                'imu0_config': [
                    False, False, False,  # pos
                    False, False, True,   # rot (yaw only)
                    False, False, False,  # vel
                    False, False, True,   # ang vel (yaw rate)
                    False, False, False,  # accel
                ],
                'imu0_differential': False,
                'imu0_relative': False,
                
                # Visual Odometry
                'odom0': '/vo/odom',
                'odom0_config': [
                    True, True, False,    # pos (x, y)
                    False, False, True,   # rot (yaw)
                    False, False, False,  # vel
                    False, False, False,  # ang vel
                    False, False, False,  # accel
                ],
                'odom0_differential': False,
                
                # Process noise (tuned for VO + IMU)
                'process_noise_covariance': [
                    0.05, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0.05, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0.06, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0.03, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0.03, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0.1, 0, 0, 0, 0, 0, 0, 0, 0, 0,  # yaw
                    0, 0, 0, 0, 0, 0, 0.025, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0.025, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0.04, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.02, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.015,
                ],
            }],
            remappings=[
                ('odometry/filtered', '/odom')
            ]
        )]
    )
    
    # ------------------------------------------------
    # MOTOR CONTROL
    # ------------------------------------------------
    motor_control = TimerAction(
        period=3.0,
        actions=[Node(
            package='robopy_controller',
            executable='motor_control_node',
            name='motor_control_node',
            output='screen'
        )]
    )
    
    # ------------------------------------------------
    # STATIC TF: base_link -> oak_left_camera_optical_frame
    # ------------------------------------------------
    # Transl: 0.05, 0, 0.08 | Rot: -90 roll, 0 pitch, -90 yaw (approx for optical)
    camera_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_camera_tf',
        arguments=[
            '0.05', '0.0', '0.08',           # x, y, z
            '-1.5708', '0.0', '-1.4173',     # yaw, pitch, roll (ROS standard: z, y, x axes)
            'base_link', 'oak_left_camera_optical_frame'
        ],
        output='log'
    )

    # ------------------------------------------------
    # FOXGLOVE BRIDGE (quiet mode)
    # ------------------------------------------------
    foxglove = TimerAction(
        period=4.0,
        actions=[Node(
            package='foxglove_bridge',
            executable='foxglove_bridge',
            name='foxglove_bridge',
            output='log',
            arguments=['--ros-args', '--log-level', 'WARN'],
            parameters=[{
                'port': 8765,
                'address': '0.0.0.0',
            }]
        )]
    )

    # ------------------------------------------------
    # Robot AI Orchestrator
    # ------------------------------------------------
    # 
    robot_ai_node = Node(
        package='robopy_controller',
        executable='robot_ai_node.py',
        name='robot_ai_orchestrator',
        output='screen',
        emulate_tty=True
    )
    
    # ------------------------------------------------
    # RTAB-MAP (external VO)
    # ------------------------------------------------
    rtabmap = TimerAction(
        period=5.0,
        actions=[Node(
            package='rtabmap_slam',
            executable='rtabmap',
            name='rtabmap',
            output='screen',
            arguments=['--ros-args', '--log-level', 'WARN'],
            parameters=[{
                'frame_id': 'base_link',
                'odom_frame_id': 'odom',
                'map_frame_id': 'map',
                'subscribe_depth': True,
                'subscribe_rgb': True,
                'approx_sync': True,
                'queue_size': 20,
                
                # External odometry (from our VO + EKF)
                'odom_sensor_sync': False,
                'wait_for_transform': 0.2,
                
                # RTAB-Map parameters
                'Mem/IncrementalMemory': 'true',
                'RGBD/AngularUpdate': '0.1',
                'RGBD/LinearUpdate': '0.1',
            }],
            remappings=[
                ('rgb/image', '/rgb/image'),
                ('rgb/camera_info', '/camera/camera_info'),
                ('depth/image', '/camera/depth/image_raw'),
            ]
        )]
    )
    
    return LaunchDescription([
        robot_state_publisher,
        fast_flow_vo,
        camera_tf,
        madgwick,
        ekf_node,
        motor_control,
        foxglove,
        rtabmap,
        #robot_ai_node,
    ])
