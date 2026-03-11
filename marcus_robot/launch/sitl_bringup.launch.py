from launch import LaunchDescription
from launch.actions import (ExecuteProcess, TimerAction,
                             RegisterEventHandler, SetEnvironmentVariable)
from launch.event_handlers import OnProcessStart, OnProcessExit
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import PathJoinSubstitution, Command
from ament_index_python.packages import get_package_share_directory
import os

def generate_launch_description():
    pkg_share = get_package_share_directory('marcus_robot')

    # STEP 0 - Env vars Pi 5
    set_env_vars = [
        SetEnvironmentVariable("LIBGL_ALWAYS_SOFTWARE", "1"),
        SetEnvironmentVariable("MESA_GL_VERSION_OVERRIDE", "3.3"),
        SetEnvironmentVariable("GZ_SIM_RESOURCE_PATH",
            PathJoinSubstitution([FindPackageShare("marcus_robot")])),
    ]

    # STEP 1 - Parametro globale condiviso
    sim_time_param = {"use_sim_time": True}

    # STEP 2 - Carica URDF tramite xacro
    robot_description_content = Command([
        "xacro ",
        PathJoinSubstitution([
            FindPackageShare("marcus_robot"), "urdf", "robot.urdf.xacro"
        ])
    ])

    # STEP 3 - Gazebo
    gazebo = ExecuteProcess(
        cmd=["gz", "sim", "--verbose",
             PathJoinSubstitution([pkg_share, "worlds", "semantic_house.sdf"]),
             "-r"],
        output="screen",
        additional_env={
            "LD_LIBRARY_PATH":
                os.environ.get("LD_LIBRARY_PATH", "") + ":/home/robopy/ros2_jazzy/install/lib"
        }
    )

    rsp_node = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        parameters=[sim_time_param, {
            "robot_description": ParameterValue(robot_description_content, value_type=str)
        }],
        output="screen"
    )

    # STEP 5 - Spawn robot dopo 8s da avvio Gazebo (su Pi 5 minimo sicuro)
    spawn_robot = Node(
        package="ros_gz_sim",
        executable="create",
        arguments=["-name", "marcus_bot",
                   "-topic", "robot_description",
                   "-x", "0.0", "-y", "0.0", "-z", "0.01"],
        parameters=[sim_time_param],
        output="screen"
    )

    delayed_spawn = RegisterEventHandler(
        event_handler=OnProcessStart(
            target_action=gazebo,
            on_start=[TimerAction(period=8.0, actions=[spawn_robot])]
        )
    )

    # STEP 6 - Bridge 1s dopo spawn completato
    bridge_node = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        parameters=[sim_time_param, {"config_file": PathJoinSubstitution([
            FindPackageShare("marcus_robot"), "config", "ros_gz_bridge.yaml"
        ])}],
        output="screen"
    )

    delayed_bridge = RegisterEventHandler(
        event_handler=OnProcessExit(
            target_action=spawn_robot,
            on_exit=[TimerAction(period=1.0, actions=[bridge_node])]
        )
    )

    # STEP 7 - visual_memory_service 2s dopo che bridge è online
    visual_memory = Node(
        package="robopy_controller",
        executable="visual_memory_service",
        name="visual_memory_service",
        output="screen",
        parameters=[sim_time_param],
        arguments=["--ros-args", "--log-level", "debug"]
    )

    delayed_visual_memory = RegisterEventHandler(
        event_handler=OnProcessStart(
            target_action=bridge_node,
            on_start=[TimerAction(period=2.0, actions=[visual_memory])]
        )
    )

    # STEP 8 - Integrazione fast_flow_lanch (Opzione disabilitata per ora per sicurezza sandbox totale)
    # fast_flow = IncludeLaunchDescription(
    #     PythonLaunchDescriptionSource([...fast_flow_lanch.py...]),
    #     launch_arguments={"sim_mode": "true", "use_sim_time": "true",
    #                       "enable_hardware": "false"}.items()
    # )

    return LaunchDescription(
        set_env_vars + [
            gazebo, rsp_node,
            delayed_spawn, delayed_bridge, delayed_visual_memory
        ]
    )
