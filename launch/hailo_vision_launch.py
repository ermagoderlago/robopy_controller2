#!/usr/bin/env python3
# hailo_vision_launch.py
"""
Launch file for the Marcus Hailo AI & Semantic Mapping Pipeline.
Launches:
  1. hailo_bridge_node (NPU inference for YOLO, Face, NetVLAD)
  2. marcus_semantic_mapper_cpp (C++ geometric fusion & 3D projection)
  3. semantic_costmap_injector (Temporal decay & costmap PointCloud2 publishing)
"""

import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    # Arguments
    hef_path_arg = DeclareLaunchArgument(
        'hef_path',
        default_value='/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/models/joined_yolo_superpoint_netvlad.hef',
        description='Path to the compiled joined HEF model'
    )

    sim_mode_arg = DeclareLaunchArgument(
        'sim_mode',
        default_value='False',
        description='Whether to run in simulation mode (swallows hardware dependencies)'
    )

    camera_frame_arg = DeclareLaunchArgument(
        'camera_frame',
        default_value='camera_optical_frame',
        description='Optical frame of the camera for coordinate projection'
    )

    # 1. Hailo Bridge Node (Python)
    hailo_bridge = Node(
        package='robopy_controller',
        executable='hailo_bridge_node',
        name='hailo_bridge_node',
        output='screen',
        parameters=[{
            'hef_path': LaunchConfiguration('hef_path'),
            'sim_mode': LaunchConfiguration('sim_mode'),
            'vlm_rate_hz': 1.5,
            'face_rate_hz': 2.0,          # [CPU-OPT] Ridotto da 5 Hz: -5% CPU, risposta ok a 2 Hz
            'enable_speaker_id': True,
            'known_faces_dir': '/home/robopy/robopy/robopy_controller/known_faces',
            'publish_sim_sedia': False,
        }]
    )

    # 2. Marcus Semantic Mapper (C++)
    semantic_mapper = Node(
        package='robopy_controller',
        executable='marcus_semantic_mapper_cpp',
        name='marcus_semantic_mapper',
        output='screen',
        parameters=[{
            'camera_frame': LaunchConfiguration('camera_frame'),
            'base_frame': 'base_link',
            'map_frame': 'map',
            'odom_frame': 'odom',
            'min_depth_m': 0.3,
            'max_depth_m': 6.0,
            'publish_debug': True,
            'max_queue_depth': 30, # Increased queue size to prevent sync starvation
        }]
    )

    # 3. Semantic Costmap Injector (Python)
    costmap_injector = Node(
        package='robopy_controller',
        executable='semantic_costmap_injector',
        name='semantic_costmap_injector',
        output='screen',
        parameters=[{
            'decay_time_sec': 5.0,
            'min_obstacle_confidence': 0.6,
            'inflation_radius_m': 0.15,
            'costmap_frame': 'map',
            'grid_resolution': 0.05,
        }]
    )

    return LaunchDescription([
        hef_path_arg,
        sim_mode_arg,
        camera_frame_arg,
        hailo_bridge,
        semantic_mapper,
        costmap_injector,
    ])
