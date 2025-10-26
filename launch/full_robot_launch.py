from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import IncludeLaunchDescription, ExecuteProcess
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command
from ament_index_python.packages import get_package_share_directory
import os
from launch.actions import TimerAction
import subprocess
from launch.launch_description_sources import PythonLaunchDescriptionSource, AnyLaunchDescriptionSource


# Disattiva SHM che causa problemi nei container
#os.environ['RMW_IMPLEMENTATION'] = 'rmw_fastrtps_cpp'

def generate_launch_description():
    urdf_file = os.path.join(
        get_package_share_directory('robopy_controller'),
        'urdf',
        'robopy.urdf'
    )

    ekf_config = os.path.join(
        get_package_share_directory('robopy_controller'),
        'config',
        'ekf.yaml'
    )
    
    # Percorso per i parametri RTAB-Map
    #rtabmap_params_file = os.path.join(
    #    get_package_share_directory('robopy_controller'),
    #    'config',
    #    'rtabmap_params.yaml'
    #)

    robot_description_content = Command(['xacro', ' ', urdf_file])

    rtabmap_params = {
        # --- ROS2 native params ---
        'frame_id': 'base_link',
        'map_frame_id': 'map',
        'odom_frame_id': 'odom',
    
        'subscribe_depth': False,
        'subscribe_rgb': True,
        'subscribe_scan': False,
        'subscribe_odom': True,  # ✅ IMPORTANTE: ora True per usare odometria
        'subscribe_imu': False,  # ✅ Disabilitato per semplificare debug
        'approx_sync': True,
        'queue_size': 60,
        'topic_queue_size': 50,
        
        # === PARAMETRI MIGLIORATI PER VISUAL SLAM MONOCULARE ===
        # Strategia odometria corretta per monocular
        'Odom/Strategy': '1',  # ✅ 1=Frame-to-Map (migliore per monocular)
        
        # Feature detection
        'Vis/FeatureType': '6',     # ORB
        'Vis/MaxFeatures': '300',   # Più features per monocular
        'Kp/MaxFeatures': '300',    # Consistent con Vis/MaxFeatures
        'Kp/DetectorStrategy': '6', # ORB detector
        
        # Mappa e griglia
        'Grid/FromDepth': 'false',
        'Grid/3D': 'false',
        'Grid/CellSize': '0.05',
        'Grid/RangeMax': '3.0',
        
        # Loop closure
        'Rtabmap/DetectionRate': '2',
        'Rtabmap/LoopThr': '0.15',
        'Mem/STMSize': '20',
        
        # Ottimizzazione
        'Reg/Strategy': '1',
        'Optimizer/Strategy': '1',
        'Optimizer/Iterations': '10',
        
        # Aggiornamento mappa
        'RGBD/LinearUpdate': '0.2',
        'RGBD/AngularUpdate': '0.3',
        
        # Parametri esistenti mantenuti ma disabilitati se conflittuali
        'Reg/Force3DoF': 'true',
        'Optimizer/GravitySigma': '0.1',
        'RGBD/OptimizeMaxError': '1.0',
        'Vis/EstimationType': '1',

        # QoS
        'qos_image': 2,
        'qos_camera_info': 2,
        'qos_odom': 2,
        'qos_imu': 2,

        # IMU disabilitata
        'use_imu': False,
        'wait_imu_to_init': False,
        'imu_fusion': 0,
    }



    # --- Include foxglove_bridge launch (inclusione del launch XML) ---
    foxglove_launch_path = os.path.join(
        get_package_share_directory('foxglove_bridge'),
        'launch',
        'foxglove_bridge_launch.xml'
    )

    foxglove_bridge_include = IncludeLaunchDescription(
        AnyLaunchDescriptionSource(foxglove_launch_path),
        # Se vuoi passare argomenti al launch di foxglove_bridge, aggiungili qui:
        # es. porta WebSocket 8765 e bind su tutte le interfacce (LAN)
        launch_arguments={
            'port': '8765',        # imposta la porta WS (se il launch xml accetta 'port')
            'host': '0.0.0.0'      # opzionale: bind address (se il launch xml lo supporta)
        }.items()
    )








    return LaunchDescription([


        Node(
            package='robopy_controller',
            executable='camera_publisher_node',
            name='camera_publisher_node',
            output='screen',
            parameters=[{
                'width': 640,
                'height': 480,
                'target_fps': 10,
                'min_fps': 3,           # FPS minimo under load
                'max_fps': 10,          # FPS massimo
                'frame_id': 'camera_frame',
                'adaptive_mode': True,  # Abilita adattamento
                'cpu_threshold': 80.0   # Soglia CPU per riduzione FPS
            }]
        ),

        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            name='robot_state_publisher',
            output='screen',
            parameters=[{'robot_description': robot_description_content}]
        ),

        Node(
            package='robopy_controller',
            executable='ultrasonic_sensor',
            name='ultrasonic_sensor',
            parameters=[{
                'trig_pin': 23,
                'echo_pin': 24,
                'frame_id': 'ultrasonic_sensor',
                'min_range': 0.02,
                'max_range': 2.0,
            }],
            output='screen'
        ),

        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(
                    get_package_share_directory('robopy_controller'),
                    'launch',
                    'ultrasonic_tf_launch.py'
                )
            )
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

        #Node(
        #    package='robopy_controller',
        #    executable='odometry_node',
        #    name='odometry_node',
        #    output='screen',
        #    parameters=[{
        #        'odom_frame_id': 'odom',
        #        'base_frame_id': 'base_link',
        #        'publish_tf': True
        #    }]
        #),

        #Node(
        #    package='robopy_controller',
        #    executable='servo_node',
        #    name='servo_node',
        #    output='screen'
        #),

        #Node(
        #    package='robopy_controller',
        #    executable='camera_publisher_node',
        #    name='camera_publisher_node',
        #    output='screen',
        #    parameters=[{
        #        'frame_id': 'camera_frame',
        #        'camera_info_url': 'package://robopy_controller/config/camera_info.yaml',
        #        'width': 320,
        #        'height': 240
        #    }]
        #),



        # Nodi commentati mantenuti per riferimento
        #Node(
        #    package='robopy_controller',
        #    executable='lite_mono_depth_node',
        #    name='lite_mono_depth_node',
        #    output='screen'
        #),
        
        #Node
        #(
        #    package='robopy_controller',
        #    executable='lite_mono_node',
        #    name='lite_mono_node',
        #    output='screen'
        #),
        #Node(
        #    package='robopy_controller',
        #    executable='midas_depth_node',
        #    name='midas_depth_node',
        #    output='screen'
        #),
        #Node(
        #    package='robopy_controller',
        #    executable='FastDepth_node',
        #    name='FastDepth_node',
        #    output='screen'
        #),

        #Node(
        #    package='robopy_controller',
        #    executable='sync_publisher_node',
        #    name='sync_publisher_node',
        #    output='screen'
        #),

        #Node(
        #    package='robopy_controller',
        #    executable='depth_to_pointcloud_node',
        #    name='depth_to_pointcloud_node',
        #    output='screen'
        #),

        # RIMOSSO: map_to_odom static transform (l'EKF lo pubblica dinamicamente)
        # Node(
        #    package='tf2_ros',
        #    executable='static_transform_publisher',
        #    name='map_to_odom',
        #    arguments=['0', '0', '0.10', '0', '0', '0', 'map', 'odom'],
        #    output='screen'
        # ),

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




        # Trasformata da base_link a imu_link (NECESSARIA per l'IMU)
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_to_imu_tf',
            arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'imu_link'],
            output='screen'
        ),

        # Trasformata statica da odom a base_link (temporanea)
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='static_odom_to_base',
            arguments=['0', '0', '0', '0', '0', '0', 'odom', 'base_link'],
            output='screen'
        ),

        #provato ad eliminato il 19/09/2025
        # Trasformata da odom a base_link (statica)
        #Node(
        #    package='tf2_ros',
        #    executable='static_transform_publisher',
        #    arguments=['0', '0', '0', '0', '0', '0', 'odom', 'base_link'],
        #    name='static_odom_to_base'
        #),

        # Trasformata da base_link a camera_frame (statica)
        #eliminata 19/09/2025
        #Node(
        #    package='tf2_ros',
        #    executable='static_transform_publisher',
        #    arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'camera_frame'],
        #    name='static_base_to_camera'
        #),
        
        # ⚠️ CORREZIONE: Transform da camera_frame a imu_link con rotazione per Z verso il basso
        # La IMU è dietro la camera con X verso la camera e Z verso il basso
        # Dobbiamo ruotare la IMU di 180° attorno all'asse X per allineare Z verso l'alto
        # Con questa (assicurati che camera_frame esista)
        
        #Node(
        #    package='tf2_ros',
        #    executable='static_transform_publisher',
        #    name='base_to_imu_tf',
        #    arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'imu_link'],
        #    output='screen'
        #),


        # Nodi commentati mantenuti per riferimento
        #Node(
        #    package='rtabmap_viz',
        #    executable='rtabmap_viz',
        #    name='rtabmap_viz',
        #    output='screen',
        #    parameters=[rtabmap_params],
        #    remappings=[
        #        ('rgb/image', '/camera/image_raw'),
        #        ('depth/image', '/camera/depth/image_rect_raw'),
        #        ('rgb/camera_info', '/camera/camera_info'),
        #        ('odom', '/odometry/filtered')
        #    ]
        #),

        #Node(
        #    package='rtabmap_sync',
        #    executable='rgbd_sync',
        #    name='rgbd_sync',
        #    output='screen',
        #    parameters=[{
        #        'approx_sync': True,
        #        'queue_size': 60,
        #        'qos': 2,
        #        'compress_rgb': True,
        #        'compress_depth': True,
        #        'rate_limit': 15.0
        #    }],
        #    remappings=[
        #        ('rgb/image', '/camera/image_raw'),
        #        ('rgb/camera_info', '/camera/camera_info'),
        #        ('depth/image', '/camera/depth/image_rect_raw'),
        #        ('rgbd_image', '/rgbd_image')
        #    ]
        #),


        
   # Node(
   #     package='rtabmap_odom',
   #     executable='rtabmap_odom',   # nodo principale per monoculare + IMU
   #     name='visual_odometry',
   #     output='screen',
   #     parameters=[{
   #         'frame_id': 'camera_frame',
   #         'odom_frame_id': 'odom',
   #         'publish_tf': True,
   #         'approx_sync': True,
   #         'queue_size': 30,
   #         'Vis/EstimationType': 1,        # 1 = feature matching
   #         'Vis/FeatureType': 6,           # 6 = ORB
   #         'Odom/Strategy': 0,             # 0 = monoculare
   #         'OdomF2M/BundleAdjustment': 1,  # BA Gauss-Newton
   #         'Odom/GuessMotion': True,       # usa IMU se disponibile
   #         'RGBD/Enabled': False            # disabilita RGB-D
   #     }],
   #     remappings=[
   #         ('rgb/image', '/camera/image_raw'),
   #         ('rgb/camera_info', '/camera/camera_info'),
   #         ('imu/data', '/imu/data'),      # dal tuo filtro complementare
   #         ('odom', '/odom')
   #     ],
   #     ),

                # ✅ NODO ODOMETRIA VISUALE MIGLIORATO
        Node(
            package='rtabmap_odom',
            executable='rgbd_odometry',
            name='visual_odometry',
            output='screen',
            parameters=[{
                'frame_id': 'camera_frame',
                'odom_frame_id': 'odom',
                'publish_tf': True,  # ✅ IMPORTANTE: pubblica TF dinamica
                'approx_sync': True,
                'queue_size': 30,
                
                # ✅ STRATEGIA CORRETTA per MONOCULARE
                'Odom/Strategy': 1,  # 1=Frame-to-Map (migliore per monocular)
                
                # Parametri visivi
                'Vis/FeatureType': 6,    # ORB
                'Vis/MaxFeatures': 600,
                'Odom/VisKeyFrameThr': 50,
                
                # Ottimizzazioni
                'OdomF2M/MaxSize': 500,
                'Odom/GuessMotion': True,
            }],
            remappings=[
                ('rgb/image', '/camera/image_raw'),
                ('rgb/camera_info', '/camera/camera_info'),
                ('odom', '/odometry/visual')
            ]
        ),

 

        # ✅ IMU Node
        Node(
            package='robopy_controller',
            executable='IMU_node',
            name='imu_node',
            output='screen',
            parameters=[{
                'frame_id': 'imu_link',
                'use_mag': False
            }]
        ),

        # ✅ EKF Localization Node
        Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_localization',
            output='screen',
            parameters=[ekf_config],
            remappings=[
                ('odometry/filtered', 'odometry/filtered'),
                ('/set_pose', '/set_pose')
            ],
            # Aggiungi questi argomenti se necessario
            arguments=['--ros-args', '--log-level', 'info']
        ),


        # ❌ RIMOSSO: Nodo di odometria ibrida (non più necessario con EKF)
        # Node(
        #    package='robopy_controller',
        #    executable='odometria_ibrida_node',
        #    name='odometria_ibrida_node',
        #    output='screen'
        # ),

        #rimosso in data 19/09/2025
        #ExecuteProcess(
        #    cmd=[
        #        'rpicam-vid',
        #        '-t', '0',
        #        '--width', '320',
        #        '--height', '240',
        #        '--framerate', '15',     
        #        '--codec', 'mjpeg',
        #        '--quality', '50',
        #        '--nopreview',
        #        '--inline',
        #        '--listen',
        #        '-o', 'udp://127.0.0.1:5000'
        #    ],
        #    output='screen'
        #),

        # Nodo commentato mantenuto per riferimento
        #ExecuteProcess(
        #    cmd=['rviz2', '-d', '/host_home/robopy/RVIZ2/robopy.rviz'],
        #    output='screen'
        #),

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
            output='screen'
        ),

        #Node(
        #    package='imu_filter_madgwick',
        #    executable='imu_filter_madgwick_node',
        #    name='imu_filter',
        #    output='screen',
        #    parameters=[os.path.join(
        #        get_package_share_directory('robopy_controller'),
        #        'config',
        #        'imu_filter.yaml'
        #    )],
        #    remappings=[
        #        ('imu/data_raw', '/imu/data_raw'),
        #        ('imu/data', '/imu/data')
        #    ]
        #),



                # Nodo complementary filter
        Node(
            package='rtabmap_slam',
            executable='rtabmap',
            name='rtabmap',
            output='screen',
            parameters=[rtabmap_params],
            remappings=[
                ('rgb/image', '/camera/image_raw'),
                ('rgb/camera_info', '/camera/camera_info'),
                ('odom', '/odometry/visual'),  # ✅ Usa odometria visiva direttamente
                ('grid_map', '/map')
            ],
            arguments=['--delete_db_on_start']
        ),

        #avvia foxglove
        foxglove_bridge_include,

        # Nodo di diagnostica per verificare i topic
        Node(
            package='robopy_controller',
            executable='topic_checker_node',
            name='topic_checker',
            output='screen',
            parameters=[{
                'required_topics': ['/camera/image_raw', '/camera/camera_info', '/odometry/filtered', '/imu/data_raw']
            }]


        
),
    ])