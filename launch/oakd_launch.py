#!/usr/bin/env python3

from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package='depthai_ros_driver',
            executable='camera_node',
            name='oak',
            output='screen',
            parameters=[{
                'camera_name': 'oak',
                'camera_model': 'OAK-D-LITE',

                'base_frame': 'camera_link',
                'camera_frame': 'camera_optical_frame',
                'imu_frame': 'imu_link',

                # RGB
                'rgb_camera.resolution': '720p',
                'rgb_camera.fps': 10.0,
                'rgb.publish_topic': True,

                # STEREO + DEPTH  ⭐⭐
                'stereo_camera.resolution': '400p',
                'stereo_camera.fps': 10.0,
                'stereo_camera.publish_topic': True,
                'stereo_camera.depth_publish_topic': True,
                'stereo_camera.depth_align': 'RGB',
                'stereo_camera.depth_unit': 'millimeter',

                # FORMATI (importantissimo)
                'depth.output_16bit': True,

                # IMU
                'imu.enable': True,
                'imu.frequency': 400,

                # Sync
                'enable_sync': True,
                'sync_video_meta': True,
            }]
        )

    ])
