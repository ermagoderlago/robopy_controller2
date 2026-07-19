# robopy_launch.py
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import IncludeLaunchDescription, TimerAction, ExecuteProcess
from launch.launch_description_sources import PythonLaunchDescriptionSource, AnyLaunchDescriptionSource
from launch.substitutions import Command, PathJoinSubstitution, FindExecutable, LaunchConfiguration
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    pkg_share = get_package_share_directory('robopy_controller')

    urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')
    ekf_config = os.path.join(pkg_share, 'config', 'ekf.yaml')

    # Leggi il file URDF
    with open(urdf_file, 'r') as file:
        robot_description_content = file.read()
    
    robot_description_param = ParameterValue(
        value=robot_description_content,
        value_type=str
    )

    # ---------------------------------------------------------
    # 1. ROBOT STATE PUBLISHER (URDF → TF)
    # ---------------------------------------------------------
    robot_state_pub = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        output='screen',
        parameters=[{'robot_description': robot_description_param}]
    )

    # ---------------------------------------------------------
    # 2. OAK-D Camera Publisher
    # ---------------------------------------------------------
    oak_node = Node(
        package='robopy_controller',
        executable='oakd_camera_publisher_node',
        name='oakd_camera',
        output='screen',
        parameters=[{
            'frame_id': 'camera_link',
            'optical_frame_id': 'camera_optical_frame',
            'rgb_width': 640,
            'rgb_height': 480,
            'rgb_fps': 15.0,
            'depth_width': 640,
            'depth_height': 480,
            'depth_fps': 15.0,
            'imu_frame_id': 'imu_link',
            'imu_topic': '/oak/imu/data',
            'accel_rate': 100,
            'gyro_rate': 100,
        }]
    )

    # ---------------------------------------------------------
    # 3. MADGWICK FILTER
    # ---------------------------------------------------------
    madgwick_node = TimerAction(
        period=3.0,
        actions=[
            Node(
                package='robopy_controller',
                executable='madgwick_node',
                name='madgwick_filter',
                output='screen',
                parameters=[{
                    'input_topic': '/oak/imu/data',
                    'output_topic': '/imu/data',
                    'frame_id': 'imu_link',
                    'beta': 0.1,
                    'sample_rate': 100.0,
                    'debug': False,
                }]
            )
        ]
    )

    # ---------------------------------------------------------
    # 4. DYNAMIC CAMERA TF (stabilizzazione IMU)
    # ---------------------------------------------------------
    dynamic_camera_tf_node = TimerAction(
        period=4.0,
        actions=[
            Node(
                package='robopy_controller',
                executable='dynamic_camera_tf_node',
                name='dynamic_camera_tf',
                output='screen',
                parameters=[{
                    'imu_topic': '/imu/data',
                    'camera_pitch_offset': -0.5236,  # -30° in radianti
                    'compensation_factor': 1.0,
                    'lowpass_alpha': 0.3,
                }]
            )
        ]
    )

    # ---------------------------------------------------------
    # 5. ULTRASONIC SENSOR
    # ---------------------------------------------------------
    ultrasonic_sensor = Node(
        package='robopy_controller',
        executable='ultrasonic_sensor',
        name='ultrasonic_sensor',
        parameters=[{
            'trig_pin': 23,
            'echo_pin': 24,
            'frame_id': 'ultrasonic_link',
            'min_range': 0.02,
            'max_range': 2.0,
        }],
        output='screen'
    )

    # ---------------------------------------------------------
    # 6. MOTOR CONTROL e BLUEDOT
    # ---------------------------------------------------------
    bluedot_node = Node(
        package='robopy_controller',
        executable='bluedot_node',
        name='bluedot_node',
        output='screen'
    )

    motor_control_node = Node(
        package='robopy_controller',
        executable='waveshare_motor_driver',
        name='waveshare_motor_driver',
        output='screen',
        parameters=[{
            'serial_port': '/dev/ttyUSB0',
            'baud_rate': 115200,
            'wheel_radius': 0.0325,
            'wheel_separation': 0.16,
            'ticks_per_rev': 1440,
            'invert_right_encoder': True,
        }]
    )

    # ---------------------------------------------------------
    # 7. VISUAL ODOMETRY (RTAB-Map RGB-D Odometry)
    # ---------------------------------------------------------
    # In ROS2 Humble/Jazzy, rtabmap_ros è stato suddiviso
    visual_odometry_node = TimerAction(
        period=6.0,
        actions=[
            Node(
                package='rtabmap_odom',
                executable='rgbd_odometry',
                name='rgbd_odometry',
                output='screen',
                parameters=[{
                    'frame_id': 'camera_optical_frame_stabilized',  # Usa frame stabilizzato!
                    'odom_frame_id': 'odom',
                    'publish_tf': False, #era true
                    'approx_sync': True,
                    'approx_sync_max_interval': 0.12,
                    'queue_size': 12,
                    'wait_for_transform': 0.5,
                    'Odom/Strategy': '1',  # 1=RGB-D
                    'Reg/Strategy': '0',  # 0=Vis
                    'Vis/FeatureType': '5',  # 0=ORB
                    'Vis/MaxFeatures': '200',
                    'Kp/MaxFeatures': '300',
                                    # Disabilita ICP se non necessario
                    'Icp/PointToPlaneK': '0',
                    'Icp/PointToPlaneRadius': '0',
                    
                    # Riduci qualità per performance
                    'Vis/SubPix': 'false',
                    'Vis/CorNNType': '1',             # 1=KDTree (FLANN)
                }],
                remappings=[
                    ('rgb/image', '/oak/rgb/image_raw'),
                    ('rgb/camera_info', '/oak/rgb/camera_info'),
                    ('depth/image', '/oak/stereo/image_raw'),
                    ('odom', '/odometry/visual'),
                ]
            )
        ]
    )

    # ---------------------------------------------------------
    # 8. EKF (IMU + Visual Odometry)
    # ---------------------------------------------------------
    ekf_localization = Node(
        package='robot_localization',
        executable='ekf_node',
        name='ekf_localization',
        output='screen',
        parameters=[ekf_config] if os.path.exists(ekf_config) else [{
            'frequency': 50.0,
            'sensor_timeout': 0.1,
            'two_d_mode': True,
            'publish_tf': True,
            'map_frame': 'map',
            'odom_frame': 'odom',
            'base_link_frame': 'base_link',
            'world_frame': 'odom',

            'imu0': '/imu/data',
            'imu0_config': [False, False, False,
                           False, False, True,
                           False, False, False,
                           False, False, False,
                           False, False, False],
            'imu0_differential': False,
            'imu0_queue_size': 10,

            'odom0': '/odometry/visual',
            'odom0_config': [True, True, False,
                            False, False, True,
                            False, False, False,
                            False, False, False,
                            False, False, False],
            'odom0_queue_size': 10,
            'odom0_differential': False,
        }],
        remappings=[('odometry/filtered', '/odometry/filtered')]
    )

    # ---------------------------------------------------------
    # 9. RTAB-Map SLAM
    # ---------------------------------------------------------
    database_path = os.path.expanduser('/home/robopy/.ros/rtabmap.db')
    
    rtabmap_node = TimerAction(
        period=10.0,
        actions=[
            Node(
                package='rtabmap_slam',
                executable='rtabmap',
                name='rtabmap',
                output='screen',
                parameters=[{
                    'frame_id': 'camera_optical_frame_stabilized',  # Usa frame stabilizzato!
                    'odom_frame_id': 'odom',
                    'map_frame_id': 'map',
                    'subscribe_depth': True,
                    'subscribe_rgb': True,
                    'subscribe_odom': True,
                    'approx_sync': True,
                    'approx_sync_max_interval': 0.15,
                    'queue_size': 12,
                    'wait_for_transform': 0.5,
                    'Vis/FeatureType': '0',  # ORB
                    'Kp/DetectorStrategy': '0',
                    'Vis/MaxFeatures': '500',
                    'Kp/MaxFeatures': '500',
                    'Odom/Strategy': '1',
                    'Odom/VisKeyFrameThr': '15',
                    'Mem/IncrementalMemory': 'true',
                    'Mem/STMSize': '15',
                    'Rtabmap/DetectionRate': '2.0',
                    'Rtabmap/LoopThr': '0.15',
                    'Grid/3D': 'true',
                    'Grid/CellSize': '0.05',
                    'Grid/RangeMax': '4.0',
                    'RGBD/LinearUpdate': '0.3',
                    'RGBD/AngularUpdate': '0.3',
                    'publish_tf': True,
                    'publish_map': True,
                    'database_path': database_path,
                }],
                remappings=[
                    ('rgb/image', '/oak/rgb/image_raw'),
                    ('rgb/camera_info', '/oak/rgb/camera_info'),
                    ('depth/image', '/oak/stereo/image_raw'),
                    ('odom', '/odometry/visual'),
                ]
            )
        ]
    )

    # ---------------------------------------------------------
    # 10. PERFORMANCE e UTILITY NODES
    # ---------------------------------------------------------
    performance_monitor = Node(
        package='robopy_controller',
        executable='performance_monitor',
        name='performance_monitor',
        output='screen'
    )

    homeassistant_node = Node(
        package='robopy_controller',
        executable='homeassistant_node',
        name='homeassistant_node',
        output='screen',
        parameters=[{'update_interval': 150.0}]
    )

    map_manager_node = Node(
        package='robopy_controller',
        executable='map_manager_node',
        name='map_manager',
        output='screen',
        parameters=[{
            'database_path': database_path,
            'min_similarity_threshold': 0.3,
            'auto_save_interval': 300,
        }]
    )

    teleop_node = Node(
        package='robopy_controller',
        executable='teleop_node',
        name='teleop_node',
        output='screen'
    )

    # ---------------------------------------------------------
    # 11. FOXGLOVE BRIDGE
    # ---------------------------------------------------------
    foxglove_launch_path = os.path.join(
        get_package_share_directory('foxglove_bridge'),
        'launch',
        'foxglove_bridge_launch.xml'
    )

    foxglove_bridge_include = IncludeLaunchDescription(
        AnyLaunchDescriptionSource(foxglove_launch_path),
        launch_arguments={'port': '8765', 'host': '0.0.0.0'}.items()
    )

    # ---------------------------------------------------------
    # 12. TOPIC CHECKER
    # ---------------------------------------------------------
    topic_checker = Node(
        package='robopy_controller',
        executable='topic_checker_node',
        name='topic_checker',
        output='screen',
        parameters=[{
            'required_topics': [
                '/oak/rgb/image_raw',
                '/oak/rgb/camera_info',
                '/oak/stereo/image_raw',
                '/oak/imu/data',
                '/imu/data',
                '/odometry/visual',
                '/odometry/filtered'
            ]
        }]
    )

    # ---------------------------------------------------------
    # LAUNCH DESCRIPTION
    # ---------------------------------------------------------
    ld = LaunchDescription([
        # Ordine importante:
        # 1. Robot state (URDF)
        robot_state_pub,
        
        # 2. Sensori fisici
        oak_node,
        ultrasonic_sensor,
        
        # 3. Processing IMU (con delay)
        madgwick_node,
        dynamic_camera_tf_node,  # Questo deve venire DOPO madgwick
        
        # 4. Controllo motori
        bluedot_node,
        motor_control_node,
        
        # 5. Localizzazione e SLAM (con delay)
        visual_odometry_node,
        ekf_localization,
        rtabmap_node,
        
        # 6. Utility nodes
        performance_monitor,
        homeassistant_node,
        map_manager_node,
        #teleop_node,
        
        # 7. Debug e monitor
        foxglove_bridge_include,
        topic_checker,
    ])

    return ld