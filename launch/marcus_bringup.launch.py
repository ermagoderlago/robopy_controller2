import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, LogInfo, RegisterEventHandler
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessStart, OnProcessExit
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node, LifecycleNode
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_dir = get_package_share_directory('robopy_controller')

    # Launch Arguments
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    enable_roudi = LaunchConfiguration('enable_roudi', default='true')

    declare_use_sim_time = DeclareLaunchArgument(
        'use_sim_time',
        default_value='false',
        description='Use simulation (Gazebo) clock if true'
    )

    declare_enable_roudi = DeclareLaunchArgument(
        'enable_roudi',
        default_value='true',
        description='Verify and enable RouDi Zero-Copy shared memory daemon'
    )

    # 1. RouDi Daemon Verification / Launch Action
    roudi_process = ExecuteProcess(
        cmd=['iox-roudi', '-c', os.path.join(pkg_dir, 'config', 'roudi_config.toml')],
        output='screen',
        condition=IfCondition(enable_roudi)
    )

    # 2. Waveshare Motor Driver Node
    motor_driver_node = Node(
        package='robopy_controller',
        executable='waveshare_motor_driver.py',
        name='waveshare_motor_driver',
        output='screen',
        parameters=[{
            'use_sim_time': use_sim_time,
            'port': '/dev/ttyUSB0',
            'baudrate': 115200,
            'wheel_separation': 0.285,
            'wheel_radius': 0.0325,
            'encoder_cpr': 1440
        }]
    )

    # 3. OAK-D Lite Vision Node (Pinned to CPU Core 2,3)
    oak_d_node = Node(
        package='robopy_controller',
        executable='oak_driver_node.py',
        name='oak_d_lite_node',
        output='screen',
        prefix=['taskset -c 2,3'],
        parameters=[{
            'use_sim_time': use_sim_time,
            'rgb_resolution': '640x480',
            'depth_resolution': '640x480',
            'fps': 30,
            'tilt_pitch_rad': 0.1396  # +8 deg pitch down
        }]
    )

    # 4. Hailo NPU Bridge Node (Joined HEF, Pinned to CPU Core 2,3)
    hailo_bridge_node = Node(
        package='robopy_controller',
        executable='hailo_bridge_node.py',
        name='hailo_bridge_node',
        output='screen',
        prefix=['taskset -c 2,3'],
        parameters=[{
            'use_sim_time': use_sim_time,
            'hef_path': os.path.join(pkg_dir, 'weights', 'joined_yolo_superpoint_netvlad.hef'),
            'publish_sim_sedia': False,
            'face_identity_threshold': 0.45
        }]
    )

    # 5. Localization Fuser Node (Dedicated EKF / VIO Quality Monitor)
    localization_fuser_node = Node(
        package='robopy_controller',
        executable='localization_fuser_node.py',
        name='localization_fuser_node',
        output='screen',
        parameters=[{
            'use_sim_time': use_sim_time,
            'wheel_slip_threshold': 0.25,
            'r_base_pos_sigma': 0.02,
            'r_base_ori_sigma': 0.01,
            'm_max': 100.0,
            'alpha_degrad': 0.15
        }]
    )

    # 6. Robot Health Supervisor & Safety Arbitrator
    health_supervisor_node = Node(
        package='robopy_controller',
        executable='robot_health_supervisor.py',
        name='robot_health_supervisor',
        output='screen',
        parameters=[{
            'use_sim_time': use_sim_time,
            'vio_yellow_thresh': 70.0,
            'vio_red_thresh': 30.0,
            'cpu_temp_yellow': 70.0,
            'cpu_temp_red': 80.0,
            'ram_yellow_gb': 3.2,
            'ram_red_gb': 3.7
        }]
    )

    # 7. Twist Mux Node (Safety Priority 0 Hard Arbitration)
    twist_mux_node = Node(
        package='twist_mux',
        executable='twist_mux',
        name='twist_mux',
        output='screen',
        parameters=[os.path.join(pkg_dir, 'config', 'twist_mux_params.yaml')]
    )

    # Lifecycle Event Sequencing: RouDi -> Motor Driver & Camera -> Fuser -> Supervisor
    return LaunchDescription([
        declare_use_sim_time,
        declare_enable_roudi,
        roudi_process,
        motor_driver_node,
        oak_d_node,
        hailo_bridge_node,
        localization_fuser_node,
        health_supervisor_node,
        twist_mux_node
    ])
