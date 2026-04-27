#!/usr/bin/env python3
# test_odometry_launch.py - Test odometria con SuperPoint

import os
from launch import LaunchDescription
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory

from launch.actions import IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import AnyLaunchDescriptionSource

def generate_launch_description():
    pkg_share = get_package_share_directory('robopy_controller')

    # URDF
    urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')
    with open(urdf_file, 'r') as f:
        robot_description_content = f.read()
    
    robot_description = ParameterValue(robot_description_content, value_type=str)

    # Percorso blob SuperPoint
    superpoint_blob = os.path.join(pkg_share, 'models', 'superpoint.blob')
    
    nodes = [
        # Robot State Publisher
        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            name='robot_state_publisher',
            output='screen',
            parameters=[{'robot_description': robot_description}]
        ),

        # Nodo SuperPoint
        Node(
            package='robopy_controller',
            executable='superpoint_node',
            name='superpoint_node',
            output='screen',
            parameters=[{
                'fps': 15,
                'imu_rate': 50,
                'superpoint_side': 'left',
                'superpoint_blob': superpoint_blob,
                'depth_out_size': '320x200',
                'mono_out_size': '320x200',
                'publish_depth': True,
                'publish_mono': True,
                'publish_features': True,
                'publish_camera_info': True,
                'use_imu': True,
                'use_rtabmap_format': False,
                'feature_threshold': 0.015,
                'max_features': 200,
                'features_skip_frames': 2,
            }]
        ),

        # TF statiche secondo standard ROS:
        # 1. base_link -> camera_link (posizione fisica della camera sul robot)
        #    args: x y z roll pitch yaw frame_id child_frame_id
        #    offset: camera 10cm avanti, 15cm in alto
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            arguments=['0.1', '0', '0.15', '0', '0', '0', 'base_link', 'camera_link'],
            output='screen'
        ),
        
        # 2. camera_link -> camera_optical_frame (trasformazione fissa tra frame robotico e ottico)
        #    La rotazione converte da assi robotica a assi visione computer
        #    Rotazione invertita per correggere visualizzazione SuperPoint sopra il robot:
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            arguments=['0', '0', '0', '-1.5708', '0', '-1.5708', 'camera_link', 'camera_optical_frame'],
            output='screen'
        ),
        
        # 3. base_link -> imu_link (posizione IMU sul robot)
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'imu_link'],
            output='screen'
        ),

        # RGB-D Odometry (con timer per avvio ritardato)
        TimerAction(
            period=3.0,
            actions=[
                Node(
                    package='rtabmap_odom',
                    executable='rgbd_odometry',
                    name='rgbd_odometry',
                    output='screen',
                    parameters=[{
                        'frame_id': 'base_link',
                        'odom_frame_id': 'odom',
                        'publish_tf': True,  # Per test, pubblica TF direttamente
                        'wait_for_transform': 0.2,
                        'subscribe_rgb': True,
                        'subscribe_depth': True,
                        'approx_sync': True,
                        'queue_size': 20,

                        # Configurazione per feature esterne
                        'Vis/FeatureType': '6',
                        'Kp/DetectorStrategy': '6',
                        'Vis/MaxFeatures': '200',
                        'Kp/MaxFeatures': '200',
                        
                        # Tuning
                        "RGBD/DepthMin": "0.25",
                        "RGBD/DepthMax": "3.0",
                        "RGBD/LinearUpdate": "0.15",
                        "RGBD/AngularUpdate": "0.15",
                        "Vis/MinInliers": "10",  # Ridotto per più robustezza
                        "Reg/Strategy": "1",
                    }],
                    remappings=[
                        ('rgb/image', '/camera/image_raw'),
                        ('rgb/camera_info', '/camera/camera_info'),
                        ('depth/image', '/depth/image_raw'),
                        ('features', '/superpoint/features'),
                    ]
                )
            ]
        ),

        # Visualizzatore per debugging (opzionale)
        Node(
            package='rqt_image_view',
            executable='rqt_image_view',
            name='image_viewer',
            arguments=['/camera/image_raw']
        ),
    ]

        # K) Foxglove bridge
    foxglove_launch_path = os.path.join(
        get_package_share_directory('foxglove_bridge'),
        'launch',
        'foxglove_bridge_launch.xml'
    )
    foxglove_bridge_include = IncludeLaunchDescription(
        AnyLaunchDescriptionSource(foxglove_launch_path),
        launch_arguments={'port': '8765', 'host': '0.0.0.0'}.items()
    )

    return LaunchDescription(nodes)