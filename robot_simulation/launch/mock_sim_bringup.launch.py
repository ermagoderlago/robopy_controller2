import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.substitutions import Command
from launch_ros.actions import Node

def generate_launch_description():
    pkg_robot_simulation = get_package_share_directory('robot_simulation')
    urdf_path = os.path.join(pkg_robot_simulation, 'urdf', 'marcus_sim.xacro')

    return LaunchDescription([
        # Robot State Publisher
        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            name='robot_state_publisher',
            output='screen',
            parameters=[{'robot_description': Command(['xacro "', urdf_path, '"'])}]
        ),

        # Differential Drive Kinematic SIL Mock Node
        Node(
            package='robot_simulation',
            executable='diff_drive_mock_node',
            name='diff_drive_mock_node',
            output='screen'
        ),

        # Hailo NPU Mock Node
        Node(
            package='robot_simulation',
            executable='hailo_mock_node',
            name='hailo_mock_node',
            parameters=[{'inference_latency_ms': 50}],
            output='screen'
        )
    ])
