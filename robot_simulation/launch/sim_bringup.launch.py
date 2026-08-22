import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, Command
from launch_ros.actions import Node

def generate_launch_description():
    pkg_robot_simulation = get_package_share_directory('robot_simulation')
    pkg_ros_gz_sim = get_package_share_directory('ros_gz_sim')

    world_path = os.path.join(pkg_robot_simulation, 'worlds', 'test_arena.sdf')
    urdf_path = os.path.join(pkg_robot_simulation, 'urdf', 'marcus_sim.xacro')
    bridge_config = os.path.join(pkg_robot_simulation, 'config', 'bridge.yaml')

    return LaunchDescription([
        DeclareLaunchArgument('world', default_value=world_path, description='SDF world file'),
        
        # Start Gazebo Sim Harmonic
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(pkg_ros_gz_sim, 'launch', 'gz_sim.launch.py')
            ),
            launch_arguments={'gz_args': ['-r -s -v4 ', LaunchConfiguration('world')]}.items()
        ),

        # Robot State Publisher
        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            name='robot_state_publisher',
            output='screen',
            parameters=[{'robot_description': Command(['xacro "', urdf_path, '"'])}]
        ),

        # Spawn Robot
        Node(
            package='ros_gz_sim',
            executable='create',
            arguments=[
                '-name', 'marcus',
                '-topic', 'robot_description',
                '-z', '0.1'
            ],
            output='screen'
        ),

        # ROS-Gazebo Bridge
        Node(
            package='ros_gz_bridge',
            executable='parameter_bridge',
            parameters=[{'config_file': bridge_config}],
            output='screen'
        ),

        # Hailo Mock Node
        Node(
            package='robot_simulation',
            executable='hailo_mock_node',
            name='hailo_mock_node',
            parameters=[{'inference_latency_ms': 50}],
            output='screen'
        )
    ])
