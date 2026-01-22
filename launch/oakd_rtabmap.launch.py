# launch/robopy_system_launch.py oakd_rtabmap.launch.py
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import IncludeLaunchDescription, ExecuteProcess, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource, AnyLaunchDescriptionSource
from launch.substitutions import Command
from ament_index_python.packages import get_package_share_directory
import os

def generate_launch_description():
    pkg_share = get_package_share_directory('robopy_controller')
    
    urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')
    ekf_config = os.path.join(pkg_share, 'config', 'ekf.yaml')
    visual_odom_yaml = os.path.join(pkg_share, 'config', 'visual_odometry_params.yaml')
    rtabmap_yaml = os.path.join(pkg_share, 'config', 'rtabmap_params.yaml')

    # ------------------ Nodo OAK-D Camera ------------------
    oak_node = Node(
        package='depthai_ros_driver',
        executable='camera_node',
        name='oak',
        parameters=[{
            'pipeline_type': 'RGBD',
            'enable_imu': True,
            'enable_nn': False,
            'enable_sync': True,
            'i_rgb_fps': 10.0,
            'i_depth_fps': 10.0,
            'i_rgb_width': 640,
            'i_rgb_height': 480,
            'i_depth_width': 640, 
            'i_depth_height': 400,
            'i_align_depth': True,
            'i_depth_align_rgb': True,
        }],
        output='screen'
    )

    # ------------------ Static Transforms ------------------
    static_transforms = [
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='left_track_tf',
            arguments=['0', '0.1', '0', '1.5708', '0', '0', 'base_link', 'left_track'],
            output='screen'
        ),
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='right_track_tf',
            arguments=['0', '-0.1', '0', '-1.5708', '0', '0', 'base_link', 'right_track'],
            output='screen'
        ),
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_to_camera_tf',
            arguments=['0.10', '0', '0.10', '0', '0', '0', 'base_link', 'camera_frame'],
            output='screen'
        ),
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_to_imu_tf',
            arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'imu_link'],
            output='screen'
        )
    ]

    # ------------------ Robot State Publisher ------------------
    robot_state_pub = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        output='screen',
        parameters=[{'robot_description': Command(['xacro', ' ', urdf_file])}]
    )

    # ------------------ Sensori e Controlli ------------------
    sensor_nodes = [
        Node(
            package='robopy_controller',
            executable='ultrasonic_sensor',
            name='ultrasonic_sensor',
            parameters=[{
                'trig_pin': 23, 'echo_pin': 24, 'frame_id': 'ultrasonic_sensor',
                'min_range': 0.02, 'max_range': 2.0,
            }],
            output='screen'
        ),
        Node(
            package='robopy_controller',
            executable='bluedot_node',
            name='bluedot_node',
            output='screen'
        ),
        Node(
            package='robopy_controller',
            executable='motor_control_node',
            name='motor_control_node',
            output='screen'
        ),
        Node(
            package='robopy_controller',
            executable='IMU_node',
            name='imu_node',
            output='screen',
            parameters=[{
                'frame_id': 'imu_link',
                'use_mag': False,
                'source_imu_topic': '/oak/imu/data',
                'publish_imu_topic': '/imu/data',
            }]
        )

        Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_imu',
            output='screen',
            parameters=[os.path.join(pkg_share, 'config', 'ekf_imu.yaml')]
        )

    ]

    # ------------------ Ultrasonic TF Launch ------------------
    ultrasonic_tf_include = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(pkg_share, 'launch', 'ultrasonic_tf_launch.py'))
    )

    # ------------------ Localizzazione ------------------
    localization_nodes = [
        Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_localization',
            output='screen',
            parameters=[ekf_config],
            remappings=[('odometry/filtered', 'odometry/filtered')]
        )
    ]

    # ------------------ Visual Odometry ------------------
    visual_odometry_node = TimerAction(
        period=5.0,
        actions=[
            Node(
                package='rtabmap_odom',
                executable='rgbd_odometry',
                name='visual_odometry',
                output='screen',
                parameters=[visual_odom_yaml],
                remappings=[
                    ('rgb/image', '/oak/rgb/image_raw'),        # ⚠️ CORRETTO: /oak/ non /oak_d/
                    ('depth/image', '/oak/stereo/image_raw'),   # ⚠️ CORRETTO
                    ('rgb/camera_info', '/oak/rgb/camera_info'),
                    ('odom', '/odometry/visual'),
                ]
            )
        ]
    )

    # ------------------ RTAB-Map SLAM ------------------
    rtabmap_node = TimerAction(
        period=8.0,
        actions=[
            Node(
                package='rtabmap_slam',
                executable='rtabmap',
                name='rtabmap',
                output='screen',
                parameters=[rtabmap_yaml],
                remappings=[
                    ('rgb/image', '/oak/rgb/image_raw'),        # ⚠️ CORRETTO
                    ('depth/image', '/oak/stereo/image_raw'),   # ⚠️ CORRETTO  
                    ('rgb/camera_info', '/oak/rgb/camera_info'),
                    ('odom', '/odometry/visual'),
                ],
                arguments=['--delete_db_on_start']
            )
        ]
    )

    # ------------------ Monitoring ------------------
    monitoring_nodes = [
        Node(
            package='robopy_controller',
            executable='performance_monitor',
            name='performance_monitor',
            output='screen'
        ),
        Node(
            package='robopy_controller',
            executable='homeassistant_node',
            name='homeassistant_node',
            output='screen',
            parameters=[{'update_interval': 15.0}]
        )
    ]

    # ------------------ Foxglove Bridge ------------------
    foxglove_launch_path = os.path.join(
        get_package_share_directory('foxglove_bridge'),
        'launch',
        'foxglove_bridge_launch.xml'
    )
    foxglove_bridge_include = IncludeLaunchDescription(
        AnyLaunchDescriptionSource(foxglove_launch_path),
        launch_arguments={'port': '8765', 'host': '0.0.0.0'}.items()
    )

    # ------------------ Launch Description ------------------
    return LaunchDescription([
        oak_node,
        robot_state_pub,
        *static_transforms,
        ultrasonic_tf_include,
        *sensor_nodes,
        *localization_nodes,
        visual_odometry_node,
        rtabmap_node,
        *monitoring_nodes,
        foxglove_bridge_include,
    ])