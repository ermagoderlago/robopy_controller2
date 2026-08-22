import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node
import xacro

def generate_launch_description():
    # 1. Load URDF
    marcus_robot_path = get_package_share_directory('marcus_robot')
    xacro_file = os.path.join(marcus_robot_path, 'urdf', 'robot.urdf.xacro')
    doc = xacro.process_file(xacro_file)
    robot_description = {'robot_description': doc.toxml()}

    return LaunchDescription([
        # Robot State Publisher
        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            name='robot_state_publisher',
            output='screen',
            parameters=[robot_description, {'use_sim_time': False}]
        ),

        # Synthetic Robot Simulator Node (Kinematics, TF, Odom, JointStates, IMU, LaserScan, RGB/Depth images)
        Node(
            package='robot_simulation',
            executable='synthetic_robot_sim_node',
            name='synthetic_robot_sim_node',
            output='screen',
            parameters=[{'use_sim_time': False}]
        ),

        # YOLO AI / Hailo Mock Node
        Node(
            package='robot_simulation',
            executable='yolo_cpu_mock_node',
            name='yolo_cpu_mock_node',
            output='screen',
            parameters=[{'use_sim_time': False}]
        ),

        # Voice & Gemini AI Mock Node
        Node(
            package='robot_simulation',
            executable='vui_mock_node',
            name='vui_mock_node',
            output='screen',
            parameters=[{'use_sim_time': False}]
        ),

        # Foxglove WebSocket Bridge
        Node(
            package='foxglove_bridge',
            executable='foxglove_bridge',
            name='foxglove_bridge',
            output='screen',
            parameters=[{
                'port': 8765,
                'use_sim_time': False
            }]
        )
    ])
