import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    IncludeLaunchDescription,
    OpaqueFunction,
    TimerAction,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


WORLD_LEVEL_MAP = {
    "simple": "world_01_simple_room.sdf",
    "1": "world_01_simple_room.sdf",
    "medium": "world_02_medium_apartment.sdf",
    "2": "world_02_medium_apartment.sdf",
    "extreme": "world_03_extreme_labyrinth.sdf",
    "3": "world_03_extreme_labyrinth.sdf",
    "smart_home": "marcus_smart_home.sdf",
}


def _as_bool(val: str) -> bool:
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _launch_setup(context, *args, **kwargs):
    pkg_robot_simulation = get_package_share_directory("robot_simulation")
    pkg_ros_gz_sim = get_package_share_directory("ros_gz_sim")

    world_override = LaunchConfiguration("world").perform(context).strip()
    world_level = LaunchConfiguration("world_level").perform(context).strip().lower()
    headless = _as_bool(LaunchConfiguration("headless").perform(context))
    enable_slam = _as_bool(LaunchConfiguration("enable_slam").perform(context))
    enable_nav2 = _as_bool(LaunchConfiguration("enable_nav2").perform(context))
    enable_supervisors = _as_bool(LaunchConfiguration("enable_supervisors").perform(context))
    enable_actors = _as_bool(LaunchConfiguration("enable_actors").perform(context))
    enable_foxglove = _as_bool(LaunchConfiguration("enable_foxglove").perform(context))

    spawn_x = LaunchConfiguration("spawn_x").perform(context)
    spawn_y = LaunchConfiguration("spawn_y").perform(context)
    spawn_z = LaunchConfiguration("spawn_z").perform(context)
    spawn_yaw = LaunchConfiguration("spawn_yaw").perform(context)

    if world_override:
        world_path = world_override
    else:
        sdf_name = WORLD_LEVEL_MAP.get(world_level, "marcus_smart_home.sdf")
        world_path = os.path.join(pkg_robot_simulation, "worlds", sdf_name)

    urdf_path = os.path.join(pkg_robot_simulation, "urdf", "marcus_sim.xacro")
    bridge_config = os.path.join(pkg_robot_simulation, "config", "bridge.yaml")
    slam_params_path = os.path.join(pkg_robot_simulation, "config", "slam_toolbox_sim.yaml")
    nav2_params_path = os.path.join(pkg_robot_simulation, "config", "nav2_sim_params.yaml")
    bt_xml_path = os.path.join(pkg_robot_simulation, "config", "nav2_survival_bt.xml")

    gz_flags = "-r -s -v2 " if headless else "-r -v2 "

    actions = [
        # 1. Gazebo Harmonic Physics + Sensor Server
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(pkg_ros_gz_sim, "launch", "gz_sim.launch.py")
            ),
            launch_arguments={"gz_args": f"{gz_flags}{world_path}"}.items(),
        ),
        # 2. Robot State Publisher (REP-105 TF Tree: base_link -> laser, camera_link -> camera_optical_frame, imu_link)
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            name="robot_state_publisher",
            output="screen",
            parameters=[
                {
                    "robot_description": ParameterValue(
                        Command(["xacro ", urdf_path]),
                        value_type=str,
                    )
                },
                {"use_sim_time": True},
            ],
        ),
        # 3. Spawn Marcus 3D URDF in Gazebo
        Node(
            package="ros_gz_sim",
            executable="create",
            arguments=[
                "-name", "marcus",
                "-topic", "robot_description",
                "-x", spawn_x,
                "-y", spawn_y,
                "-z", spawn_z,
                "-Y", spawn_yaw,
            ],
            output="screen",
        ),
        # 4. ROS 2 <-> Gazebo Harmonic Topic Bridge
        Node(
            package="ros_gz_bridge",
            executable="parameter_bridge",
            parameters=[{"config_file": bridge_config}, {"use_sim_time": True}],
            output="screen",
        ),
        # 5. Marcus Hardware, RPLIDAR C1 (/stop_motor, /start_motor), BMS 3S2P & Depth 16UC1 Bridge
        Node(
            package="robot_simulation",
            executable="marcus_hardware_sim_bridge",
            name="marcus_hardware_sim_bridge",
            parameters=[{"use_sim_time": True}],
            output="screen",
        ),
    ]

    # 6. Hailo-10H Digital Twin + Virtual Human Actors + Gym Evaluator
    if enable_actors:
        actions.extend([
            Node(
                package="robot_simulation",
                executable="hailo_digital_twin_node",
                name="hailo_bridge_node_cpp",
                parameters=[
                    {"use_sim_time": True},
                    {"conf_threshold": 0.55},
                    {"face_similarity_threshold": 0.72},
                    {"vlm_rate_hz": 5.0},
                ],
                output="screen",
            ),
            Node(
                package="robot_simulation",
                executable="virtual_human_actors_node",
                name="virtual_human_actors_node",
                parameters=[{"use_sim_time": True}],
                output="screen",
            ),
            Node(
                package="robot_simulation",
                executable="marcus_gym_evaluator_node",
                name="marcus_gym_evaluator_node",
                parameters=[{"use_sim_time": True}],
                output="screen",
            ),
        ])

    # 7. Online Async 2D SLAM (slam_toolbox -> /map + map->odom TF)
    if enable_slam:
        pkg_slam_toolbox = get_package_share_directory("slam_toolbox")
        actions.append(
            TimerAction(
                period=1.5,
                actions=[
                    IncludeLaunchDescription(
                        PythonLaunchDescriptionSource(
                            os.path.join(pkg_slam_toolbox, "launch", "online_async_launch.py")
                        ),
                        launch_arguments={
                            "slam_params_file": slam_params_path,
                            "use_sim_time": "true",
                            "autostart": "true",
                        }.items(),
                    )
                ],
            )
        )

    # 8. Full Nav2 Autonomous Navigation Stack (MPPI + NavFn A* + Survival BT + Collision Monitor)
    if enable_nav2:
        nav2_nodes = [
            Node(
                package="nav2_controller",
                executable="controller_server",
                name="controller_server",
                output="screen",
                parameters=[nav2_params_path, {"use_sim_time": True}],
                remappings=[("/cmd_vel", "/cmd_vel_nav"), ("cmd_vel", "cmd_vel_nav")],
            ),
            Node(
                package="nav2_planner",
                executable="planner_server",
                name="planner_server",
                output="screen",
                parameters=[nav2_params_path, {"use_sim_time": True}],
            ),
            Node(
                package="nav2_behaviors",
                executable="behavior_server",
                name="behavior_server",
                output="screen",
                parameters=[nav2_params_path, {"use_sim_time": True}],
            ),
            Node(
                package="nav2_bt_navigator",
                executable="bt_navigator",
                name="bt_navigator",
                output="screen",
                parameters=[
                    nav2_params_path,
                    {
                        "use_sim_time": True,
                        "default_nav_to_pose_bt_xml": bt_xml_path,
                    },
                ],
            ),
            Node(
                package="nav2_collision_monitor",
                executable="collision_monitor",
                name="collision_monitor",
                output="screen",
                parameters=[nav2_params_path, {"use_sim_time": True}],
            ),
            Node(
                package="nav2_lifecycle_manager",
                executable="lifecycle_manager",
                name="lifecycle_manager_navigation",
                output="screen",
                parameters=[
                    {
                        "use_sim_time": True,
                        "autostart": True,
                        "bond_timeout": 10.0,
                        "node_names": [
                            "controller_server",
                            "planner_server",
                            "behavior_server",
                            "bt_navigator",
                            "collision_monitor",
                        ],
                    }
                ],
            ),
        ]
        actions.append(TimerAction(period=3.5, actions=nav2_nodes))

    # 9. Real Marcus Perception 2.5D & Supervisors (FM-NAV-009, FM-NAV-041, Frontier Explorer)
    if enable_supervisors:
        supervisor_procs = [
            ExecuteProcess(
                cmd=[
                    "python3", "-m", "robopy_controller.nodes.semantic_costmap_injector",
                    "--ros-args",
                    "-p", "use_sim_time:=True",
                    "-p", "decay_time_sec:=15.0",
                    "-p", "enable_negative_obstacles:=True",
                ],
                output="screen",
            ),
            ExecuteProcess(
                cmd=[
                    "python3", "-m", "robopy_controller.nodes.sensor_standby_manager",
                    "--ros-args",
                    "-p", "use_sim_time:=True",
                    "-p", "min_awake_time_sec:=5.0",
                ],
                output="screen",
            ),
            ExecuteProcess(
                cmd=[
                    "python3", "-m", "robopy_controller.nodes.resource_governor_node",
                    "--ros-args",
                    "-p", "use_sim_time:=True",
                    "-p", "shadow_mode:=False",
                ],
                output="screen",
            ),
            ExecuteProcess(
                cmd=[
                    "python3", "-m", "robopy_controller.nodes.foxglove_nav2_bridge",
                    "--ros-args",
                    "-p", "use_sim_time:=True",
                ],
                output="screen",
            ),
            ExecuteProcess(
                cmd=[
                    "python3", "-m", "robopy_controller.nodes.frontier_explorer_node",
                    "--ros-args",
                    "-p", "use_sim_time:=True",
                ],
                output="screen",
            ),
        ]
        actions.append(TimerAction(period=2.5, actions=supervisor_procs))

    # 10. Foxglove Studio WebSocket Bridge (ws://localhost:8765)
    if enable_foxglove:
        actions.append(
            Node(
                package="foxglove_bridge",
                executable="foxglove_bridge",
                name="foxglove_bridge",
                parameters=[
                    {"port": 8765},
                    {"use_sim_time": True},
                    {"use_compression": False},
                ],
                output="screen",
            )
        )

    return actions


def generate_launch_description():
    """
    Launcher Unificato del Digital Twin Completo di Marcus in Gazebo Harmonic + SLAM + Nav2:
      - world_level:=simple    -> world_01_simple_room.sdf (6m x 6m)
      - world_level:=medium    -> world_02_medium_apartment.sdf (10m x 8m, 4 stanze, porte 0.90m)
      - world_level:=extreme   -> world_03_extreme_labyrinth.sdf (12m x 10m, chicane, U-trap, voragine 3D -45cm)
      - world_level:=smart_home -> marcus_smart_home.sdf
    """
    return LaunchDescription([
        DeclareLaunchArgument("world", default_value="", description="Explicit SDF path override"),
        DeclareLaunchArgument(
            "world_level",
            default_value="smart_home",
            description="World preset: simple | medium | extreme | smart_home",
        ),
        DeclareLaunchArgument("headless", default_value="true", description="Run Gazebo server headless (-s)"),
        DeclareLaunchArgument("spawn_x", default_value="0.0", description="Initial robot X in world"),
        DeclareLaunchArgument("spawn_y", default_value="0.0", description="Initial robot Y in world"),
        DeclareLaunchArgument("spawn_z", default_value="0.05", description="Initial robot Z in world"),
        DeclareLaunchArgument("spawn_yaw", default_value="0.0", description="Initial robot Yaw (rad) in world"),
        DeclareLaunchArgument("enable_slam", default_value="true", description="Launch slam_toolbox online async"),
        DeclareLaunchArgument("enable_nav2", default_value="true", description="Launch full Nav2 stack"),
        DeclareLaunchArgument(
            "enable_supervisors",
            default_value="true",
            description="Launch semantic_costmap_injector, sensor_standby_manager, resource_governor, frontier_explorer",
        ),
        DeclareLaunchArgument("enable_actors", default_value="true", description="Launch Hailo twin & human actors"),
        DeclareLaunchArgument("enable_foxglove", default_value="true", description="Launch foxglove_bridge on 8765"),
        OpaqueFunction(function=_launch_setup),
    ])
