#!/usr/bin/env python3

import os
from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():

    pkg_share = get_package_share_directory('robopy_controller')
    urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')

    # ------------------------------------------------
    # Robot State Publisher
    # ------------------------------------------------
    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        parameters=[{
            'robot_description': open(urdf_file).read()
        }],
        output='screen'
    )

    # ------------------------------------------------
    # 🔥 DEPTHAI CAMERA — STANDALONE (NO CONTAINER)
    # ------------------------------------------------
    oak_camera = Node(
        package='depthai_ros_driver',
        executable='camera',
        name='oak',
        output='screen',
        parameters=[{
            # Device
            'camera_model': 'OAK-D-LITE',

            # === DISABILITA TUTTO IL SUPERFLUO ===
            'enable_rgb': False,
            'enable_nn': False,
            'enable_pointcloud': False,
            'enable_rectification': False,
            'enable_sync': False,
            'publish_tf': False,

            # === STEREO ONLY ===
            'mono_resolution': '400p',
            'stereo_decimation': 2,
            'depth_fps': 30,

            # === PERFORMANCE ===
            'confidence_threshold': 200,
            'lrcheck': False,
            'extended': False,
            'subpixel': False,
        }]
    )

    # ------------------------------------------------
    # RGBD ODOMETRY (LEGGERA)
    # ------------------------------------------------
    rgbd_odometry = Node(
        package='rtabmap_odom',
        executable='rgbd_odometry',
        output='screen',
        parameters=[{
            'frame_id': 'base_link',
            'odom_frame_id': 'odom',
            'publish_tf': False,

            # 🔥 FONDAMENTALE
            'approx_sync': True,
            'queue_size': 5,

            # ORB (veloce)
            'Vis/FeatureType': 1,
            'Vis/MaxFeatures': 100,

            'RGBD/DepthMin': 0.3,
            'RGBD/DepthMax': 3.0,
        }],
        remappings=[
            ('rgb/image', '/oak/left/image_raw'),
            ('rgb/camera_info', '/oak/left/camera_info'),
            ('depth/image', '/oak/stereo/image_raw'),
            ('odom', '/odometry/visual'),
        ]
    )

    # ------------------------------------------------
    # EKF
    # ------------------------------------------------
    ekf_node = Node(
        package='robot_localization',
        executable='ekf_node',
        name='ekf_localization',
        output='screen',
        parameters=[os.path.join(pkg_share, 'config', 'ekf.yaml')],
        remappings=[
            ('odometry/filtered', '/odom')
        ]
    )

    return LaunchDescription([
        robot_state_publisher,
        oak_camera,
        rgbd_odometry,
        ekf_node,
    ])
