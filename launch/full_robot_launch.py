#!/usr/bin/env python3
# robopy_stable_launch.py
import os

from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import AnyLaunchDescriptionSource
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory



def generate_launch_description():
    pkg_share = get_package_share_directory('robopy_controller')
    urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')

     # Percorsi dei file di configurazione YAML
    ekf_config_path = os.path.join(pkg_share, 'config', 'ekf.yaml')
    rtabmap_config_path = os.path.join(pkg_share, 'config', 'rtabmap_params.yaml')
    vis_odom_config_path = os.path.join(pkg_share, 'config', 'visual_odometry_params.yaml')
 

    # Read URDF content
    with open(urdf_file, 'r') as f:
        robot_description_content = f.read()

    robot_description_param = ParameterValue(
        value=robot_description_content,
        value_type=str
    )

    # database path must be defined before any node that uses it
    database_path = os.path.expanduser('~/.ros/rtabmap.db')

    # 1) Robot State Publisher (publish URDF static TFs)
    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        output='screen',
        parameters=[{'robot_description': robot_description_param}]
    )

    oak_node = Node(
        package='robopy_controller',
        executable='oakd_camera_publisher_node',
        name='oakd_camera',
        output='screen',
        arguments=[
            '--ros-args',
            '--log-level', 'oakd_camera:=warn',
        ],
        parameters=[{
            'optical_frame_id': 'camera_optical_frame',
            'imu_frame_id': 'imu_link',
            
            # RGB per RTAB-Map
            'low_w': 320,
            'low_h': 240,
            'low_fps': 15.0,
            
            # DEPTH ALLINEATA CON RGB
            'depth_w': 320,    # STESSE DIMENSIONI DI RGB
            'depth_h': 240,    # 240/240 = 1 (multiplo intero)
            'depth_fps': 15.0,
            
            # NN (opzionale, puoi disabilitare per più performance)
            'enable_nn': True,  # Disabilitato per debug RTAB-Map
            'model': 'luxonis/yolov6-nano:r2-coco-512x384',
            'nn_width': 512,
            'nn_height': 384,
            'nn_fps': 15.0,
            'draw_detections': True,
            
           
            # DEBUG
            'debug': False,  # NESSUN LOG NON ESSENZIALE
        }]
    )

    # 3) Madgwick AHRS (start slightly delayed to allow device init)
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
                }]
            )
        ]
    )

    # 4) Dynamic camera TF (stabilization) - delayed start to avoid TF races
    dynamic_camera_tf_node = TimerAction(
        period=4.0,
        actions=[
            Node(
                package='robopy_controller',
                executable='dynamic_camera_tf_node_fixed',
                name='dynamic_camera_tf',
                output='screen',
                parameters=[{
                    'imu_topic': '/imu/data',
                    'camera_position_x': 0.10,
                    'camera_position_y': 0.0,
                    'camera_position_z': 0.15,
                    'camera_pitch_offset': -0.5236,
                    'compensation_factor': 1.0,
                    'lowpass_alpha': 0.3,
                }]
            )
        ]
    )

    # 5) Ultrasonic sensor node (optional)
    ultrasonic_sensor = Node(
        package='robopy_controller',
        executable='ultrasonic_sensor',
        name='ultrasonic_sensor',
        output='screen',
        parameters=[{
            'trig_pin': 23,
            'echo_pin': 24,
            'frame_id': 'ultrasonic_link',
            'min_range': 0.02,
            'max_range': 2.0,
        }]
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
            'wheel_separation': 0.285,
            'rotational_wheel_separation': 0.285,
            'ticks_per_rev': 70,
            'invert_left_motor': False,
            'invert_right_motor': False,
            'invert_left_encoder': False,
            'invert_right_encoder': False,
            'publish_tf': True,
        }]
    )

    bluedot_node = Node(
        package='robopy_controller',
        executable='bluedot_node',
        name='bluedot_node',
        output='screen'
    )

    teleop_node = Node(
        package='robopy_controller',
        executable='teleop_node',
        name='teleop_node',
        output='screen'
    )

    # 7) Visual odometry (delayed to allow camera & madgwick)
    visual_odometry_node = TimerAction(
        period=5.0,
        actions=[
            Node(
                package='rtabmap_odom',
                executable='rgbd_odometry',
                name='rgbd_odometry',
                output='screen',
                # CARICA YAML CORRETTAMENTE + FORZA PARAMETRI CRITICI
                parameters=[
                    vis_odom_config_path,  # File YAML
                    {
                        'frame_id': 'base_link',
                        'publish_tf': False,
                        'odom_frame_id': 'odom',
                    }
                ],
                remappings=[
                    ('rgb/image', '/oak/rgb/image_raw'),
                    ('rgb/camera_info', '/oak/rgb/camera_info'),
                    ('depth/image', '/oak/stereo/image_raw'),
                    ('odom', '/odometry/visual'),
                ]
            )
        ]
    )

    # 7b) Map manager (uses database_path)
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

    # 8) EKF localization
    ekf_localization = TimerAction(
        period=8.0,  # Inizia dopo visual_odometry
        actions=[
            Node(
                package='robot_localization',
                executable='ekf_node',
                name='ekf_localization',
                output='screen',
                parameters=[{
                    'frequency': 50.0,
                    'sensor_timeout': 0.1,
                    'two_d_mode': True,
                    'publish_tf': True,
                    
                    'map_frame': 'map',
                    'odom_frame': 'odom',
                    'base_link_frame': 'base_link',
                    'world_frame': 'odom',

                    # IMU config
                    'imu0': '/imu/data',
                    'imu0_frame_id': 'imu_link',  # Nota: nel tuo yaml non c'era, ma è bene specificarlo
                    'imu0_differential': False,
                    'imu0_queue_size': 10,
                    'imu0_remove_gravitational_acceleration': True,
                    'imu0_config': [False, False, False,
                                    False, False, True,
                                    False, False, False,
                                    False, False, True,
                                    False, False, False],

                    # Wheel Odometry config
                    'odom0': '/odom',
                    'odom0_frame_id': 'odom',  # Aggiunto: fondamentale!
                    'odom0_differential': False,
                    'odom0_queue_size': 10,
                    'odom0_config': [True, True, False,
                                     False, False, True,
                                     True,  True,  False,
                                     False, False, True,
                                     False, False, False],

                    # Covarianza e rumore
                    'process_noise_covariance': [
                        0.05, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                        0.0,  0.05, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                        0.0,  0.0,  0.06, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                        0.0,  0.0,  0.0,  0.03, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                        0.0,  0.0,  0.0,  0.0,  0.03, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                        0.0,  0.0,  0.0,  0.0,  0.0,  0.06, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                        0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.02, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                        0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.02, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                        0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.04, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                        0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.01, 0.0,  0.0,  0.0,  0.0,  0.0,
                        0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.01, 0.0,  0.0,  0.0,  0.0,
                        0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.02, 0.0,  0.0,  0.0,
                        0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.01, 0.0,  0.0,
                        0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.01, 0.0,
                        0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.015
                    ]
                }],
                remappings=[('odometry/filtered', '/odometry/filtered')]
            )
        ]
    )

    # 9) RTAB-Map (optional, delayed)
    rtabmap_node = TimerAction(
    period=9.0,
    actions=[
        Node(
            package='rtabmap_slam',
            executable='rtabmap',
            name='rtabmap',
            output='screen',
            parameters=[rtabmap_config_path, {'database_path': database_path}], # <--- CARICA YAML
            remappings=[
                ('rgb/image', '/oak/rgb/image_raw'),
                ('rgb/camera_info', '/oak/rgb/camera_info'),
                ('depth/image', '/oak/stereo/image_raw'),
                ('odom', '/odometry/filtered'), # RTAB si fida dell'output EKF
            ],
            arguments=['-d'] # Cancella database precedente ad ogni avvio (opzionale)
        )
    ]
)

    # 10) Object 3D Mapper - per mappare oggetti YOLO in 3D
    object_3d_mapper = TimerAction(
        period=12.0,  # Inizia dopo RTAB-Map
        actions=[
            Node(
                package='robopy_controller',
                executable='object_3d_mapper',
                name='object_3d_mapper',
                output='screen',
                parameters=[{
                    'min_confidence': 0.5,
                    'max_distance': 3.0,
                    'min_object_height': 0.05,
                    'publish_markers': True,
                    'debug': True,
                }]
            )
        ]
    )

    # 11) Performance monitor
    performance_monitor = Node(
        package='robopy_controller',
        executable='performance_monitor',
        name='performance_monitor',
        output='screen'
    )

    # 12) Foxglove bridge (optional include)
    foxglove_launch_path = os.path.join(
        get_package_share_directory('foxglove_bridge'),
        'launch',
        'foxglove_bridge_launch.xml'
    )
    foxglove_bridge_include = IncludeLaunchDescription(
        AnyLaunchDescriptionSource(foxglove_launch_path),
        launch_arguments={'port': '8765', 'host': '0.0.0.0'}.items()
    )

    # Build LaunchDescription
    ld = LaunchDescription([
        # core
        robot_state_publisher,

        # sensors & base nodes
        oak_node,
        #ultrasonic_sensor,

        # imu processing & stabilization
        madgwick_node,
        
        
        # dynamic_camera_tf_node,   # enable if you want dynamic TF (currently commented out)

        # control
        bluedot_node,
        motor_control_node,

        # localization & odometry
        visual_odometry_node,
        ekf_localization,

        # optional / heavy
        rtabmap_node,
        object_3d_mapper,  # AGGIUNTO QUI!
        map_manager_node,
        

        # monitoring / debug
        performance_monitor,
        foxglove_bridge_include,

        # teleop (commented out — enable if desired)
        # teleop_node,
    ])

    return ld