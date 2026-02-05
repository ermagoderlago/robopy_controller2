#!/usr/bin/env python3
# robopy_stable_launch_fixed.py

import os
from launch import LaunchDescription
from launch.actions import TimerAction
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory

def generate_launch_description():

    pkg_share = get_package_share_directory('robopy_controller')

    # ------------------------------------------------
    # ROBOT DESCRIPTION
    # ------------------------------------------------
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
    # OAK-D CAMERA
    # ------------------------------------------------
    oak_node = Node(
        package='robopy_controller',
        executable='oakd_camera_publisher_node_test',
        name='oakd_camera',
        output='screen',
        parameters=[{
            'optical_frame_id': 'camera_optical_frame',
            'imu_frame_id': 'imu_link',
            'low_w': 320,
            'low_h': 240,
            'low_fps': 15.0,
            'depth_fps': 15.0,
            'enable_nn': False,
            'enhance_images': False,
        }]
    )

    # ------------------------------------------------
    # STATIC TRANSFORM: camera_link to base_link (CORRETTA)
    # ------------------------------------------------
    static_transform = TimerAction(
        period=1.0,
        actions=[Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='camera_to_base_tf',
            arguments=[
                '0.1', '0.0', '0.2',  # x, y, z
                '0.0', '0.0', '0.0',  # roll, pitch, yaw
                'camera_link', 'base_link'  # CORRETTO
            ]
        )]
    )

    # ------------------------------------------------
    # IMU FILTER (MADGWICK)
    # ------------------------------------------------
    madgwick = TimerAction(
        period=1.5,
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
    # DEPTH TO LASERSCAN
    # ------------------------------------------------
    depth_to_scan = TimerAction(
        period=2.5,
        actions=[Node(
            package='depthimage_to_laserscan',
            executable='depthimage_to_laserscan_node',
            name='depth_to_scan',
            output='screen',
            parameters=[{
                'output_frame': 'base_link',  # MODIFICATO: base_link invece di camera_link
                'range_min': 0.3,
                'range_max': 2.5,
                'scan_height': 10,
                'scan_time': 0.033,
                'inf_epsilon': 1.0,
                'queue_size': 5,
            }],
            remappings=[
                ('depth', '/oak/stereo/image_raw'),
                ('depth_camera_info', '/oak/stereo/camera_info'),
                ('scan', '/scan'),
            ]
        )]
    )

    # ------------------------------------------------
    # VISUAL ODOMETRY
    # ------------------------------------------------
    rgbd_odometry = TimerAction(
        period=3.0,
        actions=[Node(
            package='rtabmap_odom',
            executable='rgbd_odometry',
            name='rgbd_odometry',
            output='screen',
            parameters=[{
                'frame_id': 'camera_link',
                'odom_frame_id': 'odom',
                'publish_tf': True,  # ATTIVATO per debug
                'approx_sync': True,
                'approx_sync_max_interval': 0.1,
                'queue_size': 20,
                'wait_for_transform': 0.2,
                'expected_update_rate': 15.0,
            }],
            remappings=[
                ('rgb/image', '/oak/rgb/image_raw'),
                ('rgb/camera_info', '/oak/rgb/camera_info'),
                ('depth/image', '/oak/stereo/image_raw'),
                ('odom', '/odometry/visual'),
            ]
        )]
    )

    # ------------------------------------------------
    # EKF LOCALIZATION
    # ------------------------------------------------
    ekf_node = TimerAction(
        period=3.5,
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

                'imu0': '/imu/data',
                'imu0_config': [
                    False, False, False,
                    False, False, True,
                    False, False, False,
                    False, False, True,
                    False, False, False,
                ],

                'odom0': '/odometry/visual',
                'odom0_config': [
                    True, True, False,
                    False, False, True,
                    False, False, False,
                    False, False, False,
                    False, False, False,
                ],
            }],
            remappings=[
                ('odometry/filtered', '/odom')
            ]
        )]
    )

    # ------------------------------------------------
    # MAP SERVER
    # ------------------------------------------------
    map_server = TimerAction(
        period=4.0,
        actions=[Node(
            package='nav2_map_server',
            executable='map_server',
            name='map_server',
            output='screen',
            parameters=[{
                'use_sim_time': False,
                'yaml_filename': os.path.expanduser('~/.ros/map.yaml')
            }]
        )]
    )

    # ------------------------------------------------
    # AMCL
    # ------------------------------------------------
    amcl = TimerAction(
        period=6.0,
        actions=[Node(
            package='nav2_amcl',
            executable='amcl',
            name='amcl',
            output='screen',
            parameters=[{
                'use_sim_time': False,
                'base_frame_id': 'base_link',
                'odom_frame_id': 'odom',
                'global_frame_id': 'map',
                'scan_topic': '/scan',

                'set_initial_pose': True,  # IMPORTANTE: attiva posa iniziale
                'initial_pose.x': 0.0,
                'initial_pose.y': 0.0,
                'initial_pose.z': 0.0,
                'initial_pose.yaw': 0.0,

                'min_particles': 800,
                'max_particles': 3000,

                'laser_min_range': 0.3,
                'laser_max_range': 2.5,
                'laser_model_type': 'likelihood_field',

                'odom_model_type': 'diff',
            }]
        )]
    )

    # ------------------------------------------------
    # LIFECYCLE MANAGER
    # ------------------------------------------------
    lifecycle_manager = TimerAction(
        period=7.0,
        actions=[Node(
            package='nav2_lifecycle_manager',
            executable='lifecycle_manager',
            name='lifecycle_manager',
            output='screen',
            parameters=[{
                'use_sim_time': False,
                'autostart': True,
                'node_names': ['map_server', 'amcl']
            }]
        )]
    )

    #SuperPoint CPU Node
    superpoint_node = Node(
        package='robopy_controller',
        executable='cpu_superpoint_node',
        name='superpoint_node',
        output='screen',
        parameters=[{
            'weights_path': os.path.join(pkg_share, 'models', 'superpoint_v1.pth'),
            'input_topic': '/oak/rgb/image_raw',
            'max_fps': 5.0, # Mantienilo basso per non saturare la CPU
            'conf_thresh': 0.015
        }]
    )

    # ------------------------------------------------
    # BUILD
    # ------------------------------------------------
    return LaunchDescription([
        robot_state_publisher,
        oak_node,
        static_transform,
        madgwick,
        depth_to_scan,
        rgbd_odometry,
        ekf_node,
        map_server,
        amcl,
        lifecycle_manager,  # AGGIUNTO
        superpoint_node
    ])