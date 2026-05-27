#!/usr/bin/env python3
# robopy_mapping_launch.py
import os

from launch import LaunchDescription
from launch.actions import TimerAction
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory
from launch.actions import IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import AnyLaunchDescriptionSource




def generate_launch_description():

    pkg_share = get_package_share_directory('robopy_controller')

    # ----------------------------
    # URDF
    # ----------------------------
    urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')
    with open(urdf_file, 'r') as f:
        robot_description_content = f.read()

    robot_description = ParameterValue(
        robot_description_content,
        value_type=str
    )

    # ----------------------------
    # Config files
    # ----------------------------
    rtabmap_config = os.path.join(pkg_share, 'config', 'rtabmap_params.yaml')

    database_path = os.path.expanduser('~/.ros/rtabmap.db')

    # ============================================================
    # 1) Robot State Publisher (TF statici da URDF)
    # ============================================================
    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        output='screen',
        parameters=[{'robot_description': robot_description}]
    )

    # ============================================================
    # 2) OAK-D Camera Publisher
    # ============================================================
    oak_node = Node(
        package='robopy_controller',
        executable='oakd_camera_publisher_node_test',
        name='oakd_camera',
        output='screen',
        parameters=[{
            'optical_frame_id': 'camera_optical_frame',
            'imu_frame_id': 'imu_link',

            # RGB
            'low_w': 320,
            'low_h': 240,
            'low_fps': 10.0,

            # Depth (allineata RGB)
            'depth_w': 320,
            'depth_h': 240,
            'depth_fps': 10.0,

            # NN OFF (mapping only)
            'enable_nn': True,
            'debug': True,
        }]
    )

    # ============================================================
    # 3) Madgwick IMU Filter
    # ============================================================
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

    # ============================================================
    # 4) RGB-D ODOMETRY  ⭐ FONDAMENTALE ⭐
    #    crea TF: odom → base_link
    # ============================================================
    rgbd_odometry = TimerAction(
        period=5.0,
        actions=[
            Node(
                package='rtabmap_odom',
                executable='rgbd_odometry',
                name='rgbd_odometry',
                output='screen',
                parameters=[{
                    'frame_id': 'base_link',
                    'odom_frame_id': 'odom',
                    'publish_tf': True,

                    'subscribe_rgbd': False,
                    'subscribe_rgb': True,
                    'subscribe_depth': True,

                    'approx_sync': True,
                    'approx_sync_max_interval': 0.15,
                    'queue_size': 8,


                    #aggiunto dopo
                    # --- GRID ---
                    'Grid/3D': False,
                    'Grid/CellSize': 0.05,
                    'Grid/RangeMax': 3.0,

                    # --- DEPTH ---
                    'RGBD/DepthMin': 0.25,
                    'RGBD/DepthMax': 2.5,

                    # --- ODOM ---
                    'RGBD/LinearUpdate': 0.15,
                    'RGBD/AngularUpdate': 0.15,

                    # --- VISIVO ---
                    "Vis/MinInliers": "20",
                    "Vis/InlierDistance": "0.08",
                    # --- SLAM ---
                    "Reg/Force3DoF": "True",    
                    "Reg/Strategy": "1",  # 1=Visodomtry

                }],
                 
                remappings=[
                    ('rgb/image', '/oak/rgb/image_raw'),
                    ('rgb/camera_info', '/oak/rgb/camera_info'),
                    ('depth/image', '/oak/stereo/image_raw'),
                ]
                #arguments=['--ros-args', '--log-level', 'error']
            )
        ]
    )

    # ============================================================
    # 5) RTAB-Map (Mapping)
    #    crea TF: map → odom
    # ============================================================
    rtabmap_node = TimerAction(
        period=8.0,
        actions=[
            Node(
                package='rtabmap_slam',
                executable='rtabmap',
                name='rtabmap',
                 prefix='taskset -c 0-1',
                output='screen',
                parameters=[
                    rtabmap_config,
                    {
                        'database_path': database_path,

                        'frame_id': 'base_link',
                        'odom_frame_id': 'odom',
                        'map_frame_id': 'map',

                        'subscribe_rgb': True,
                        'subscribe_depth': True,
                        'subscribe_rgbd': False,
                        'subscribe_odom': True,

                        #'approx_sync': True,
                        #'approx_sync_max_interval': 0.1, # <--- AUMENTA QUESTO PER SINCRONIZZARE MEGLIO
                        #"Rtabmap/DetectionRate": "5.0",   # 1 Hz va bene per il Pi

                                               # === PARAMETRI PER MAPPATURA INCREMENTALE ===
                    
                        # 1) NON cancellare il database all'avvio
                        #"Rtabmap/StartNewMapOnStartup": "false",
                        
                        # 2) Carica il database esistente
                        "Database/InMemory": "false",
                        "Mem/InitWMWithAllNodes": "true",  # Carica tutti i nodi dal DB
                        
                        # 3) Modalità incrementale
                        "Mem/IncrementalMemory": "true",
                        "Mem/STMSize": "30",  # Dimensione memoria a breve termine
                        
                        # 4) Mantieni i nodi nella memoria
                        "Mem/NotLinkedNodesKept": "true",
                        "Mem/RecentWmRatio": "0.3",  # Percentuale di nodi recenti da mantenere
                        
                        # 5) Loop closure attivo
                        "Rtabmap/LoopThr": "0.11",
                        "RGBD/LoopClosureReextractFeatures": "true",
                        
                        # 6) Aggiornamento della mappa
                        "RGBD/LinearUpdate": "0.1",  # Aggiorna ogni 10cm
                        "RGBD/AngularUpdate": "0.1",  # Aggiorna ogni 0.1 rad
                        
                        # 7) Rilevamento features (come nel tuo YAML)
                        "Kp/DetectorStrategy": "6",
                        "Vis/FeatureType": "2",
                        "Rtabmap/DetectionRate": "3.0",
                        
                        # 8) Ottimizzazione del grafico
                        "RGBD/OptimizeFromGraphEnd": "true",
                        "RGBD/OptimizeMaxError": "1.0",
                        "RGBD/ProximityBySpace": "true",
                        
                        # 9) Salvataggio automatico
                        "Database/AutoSave": "true",
                        "Database/AutoSaveDelay": "300",  # Salva ogni 5 minuti
                    }
                ],
                remappings=[
                    ('rgb/image', '/oak/rgb/image_raw'),
                    ('rgb/camera_info', '/oak/rgb/camera_info'),
                    ('depth/image', '/oak/stereo/image_raw'),
                    ('odom', '/odom'),
                ],
                arguments=['-d --ros-args --log-level warn']  # cancella DB ad ogni avvio
            )
        ]
    )

    # 6) Motor control and bluedot / teleop
    motor_control_node = Node(
        package='robopy_controller',
        executable='motor_control_node',
        name='motor_control_node',
        output='screen'
    )

    bluedot_node = Node(
        package='robopy_controller',
        executable='bluedot_node',
        name='bluedot_node',
        output='screen'
    )

    # SuperPoint CPU Node
    superpoint_node = Node(
        package='robopy_controller',
        executable='cpu_superpoint_node',
        name='superpoint_node',
        prefix='taskset -c 2-3',
        output='screen',
        parameters=[{
            'weights_path': os.path.join(pkg_share, 'models', 'superpoint_v1.pth'),
            'input_topic': '/oak/rgb/image_raw',
            'max_fps': 5.0, # Mantienilo basso per non saturare la CPU
            'conf_thresh': 0.015
        }]
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

    #servo 
    servo_coda_node = Node(
        package='robopy_controller',
        executable='servo_coda_node',
        name='servo_coda_node',
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

    # ============================================================
    # LAUNCH DESCRIPTION
    # ============================================================
    return LaunchDescription([
        robot_state_publisher,
        oak_node,
        madgwick_node,
        rgbd_odometry,
        rtabmap_node,
        object_3d_mapper,
        # control
        bluedot_node,
        motor_control_node,
        #performance_monitor,
        #foxglove_bridge_include,
        #superpoint_node,
        servo_coda_node,
    ])
