from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package='robopy_controller',
            executable='imu_bridge_node.py',
            name='imu_bridge',
            parameters=[{
                'source_imu_topic': '/oak/imu/data',
                'source_mag_topic': '/oak/imu/mag',
                'publish_imu_topic': '/imu/data',
                'publish_mag_topic': '/imu/mag',
                'frame_id': 'imu_link',
                'base_frame': 'base_link',
                'publish_tf': True,
                'use_orientation': True,
                'linear_accel_covariance': [0.1,0,0,0,0.1,0,0,0,0.1],
                'angular_vel_covariance': [0.05,0,0,0,0.05,0,0,0,0.05],
                'orientation_covariance_unknown': True
            }]
        )
    ])
