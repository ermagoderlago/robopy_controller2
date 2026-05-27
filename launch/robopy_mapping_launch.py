#!/usr/bin/env python3
# robopy_mapping_launch.py - Versione con Sensor Fusion (EKF + RGBD + IMU)

import os
from launch import LaunchDescription
from launch.actions import TimerAction
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory

def generate_launch_description():
    pkg_share = get_package_share_directory('robopy_controller')

    # ----------------------------
    # 1. Caricamento File
    # ----------------------------
    # URDF
    urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')
    with open(urdf_file, 'r') as f:
        robot_description_content = f.read()
    
    robot_description = ParameterValue(robot_description_content, value_type=str)

    # Configs
    rtabmap_config = os.path.join(pkg_share, 'config', 'rtabmap_params.yaml')
    ekf_config_path = os.path.join(pkg_share, 'config', 'ekf.yaml') # [EKF] Path al file yaml
    database_path = os.path.expanduser('~/.ros/rtabmap.db')

    # ----------------------------
    # 2. Nodi Base
    # ----------------------------
    
    # A) Robot State Publisher
    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        output='screen',
        parameters=[{'robot_description': robot_description}]
    )

    # B) OAK-D Camera (Hardware Sync Attivo)
    oak_node = Node(
        package='robopy_controller',
        executable='oakd_camera_publisher_node',
        name='oakd_camera',
        output='screen',
        parameters=[{
            'optical_frame_id': 'camera_optical_frame',
            'imu_frame_id': 'imu_link',
            'low_w': 320, 'low_h': 240, 'low_fps': 10.0,
            'depth_fps': 10.0,
            'enable_nn': False, # Mapping puro
            'debug': False,
        }]
    )

    # C) Madgwick Filter (Tua versione custom)
    madgwick_node = TimerAction(
        period=2.0,
        actions=[
            Node(
                package='robopy_controller',
                executable='madgwick_node', # Assicurati che entry_point in setup.py punti al tuo nuovo script
                name='madgwick_filter',
                output='screen',
                parameters=[{
                    'input_topic': '/oak/imu/data',
                    'output_topic': '/imu/data', # Questo va all'EKF
                    'frame_id': 'imu_link',
                    'beta': 0.1,
                }]
            )
        ]
    )

    # ----------------------------
    # 3. Odometria & Fusion [MODIFICATO]
    # ----------------------------

    # D) RGB-D Odometry (Visuale)
    # NOTA: Disabilitiamo la pubblicazione TF e rimappiamo l'output
    # per darlo in pasto all'EKF invece che usarlo direttamente.
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
                    'publish_tf': False,  # [EKF] IMPORTANTE: Lasciamo che sia l'EKF a pubblicare la TF odom->base_link
                    'wait_for_transform': 0.2,
                    
                    'subscribe_rgbd': False,
                    'subscribe_rgb': True,
                    'subscribe_depth': True,
                    'approx_sync': True, 
                    'queue_size': 20,

                    # Tuning Odometria
                    "Grid/3D": "false",
                    "Grid/CellSize": "0.05",
                    "RGBD/DepthMin": "0.25",
                    "RGBD/DepthMax": "2.5",
                    "RGBD/LinearUpdate": "0.15",
                    "RGBD/AngularUpdate": "0.15",
                    "Vis/MinInliers": "15",
                    #'Vis/InlierDistance': 0.07,
                    "Reg/Strategy": "1", 

                                    # === AGGIUNGI QUESTI ===
                    "Icp/CorrespondenceRatio": "0.1",
                    "Vis/CorFlowMaxLevel": "5",
                    "Vis/CorNNType": "1",
                    "Vis/EstimationType": "1",
                    "Vis/ForwardEstOnly": "False",
                    "Vis/FeatureType": "6",  # GFTT+BRIEF
                    "Kp/DetectorStrategy": "6",
                    "Vis/MaxFeatures": "800",  # Riduci un po'

                    #"approx_sync_max_interval": "0.05", # <--- AUMENTA QUESTO PER SINCRONIZZARE MEGLIO
                    #"wait_imu_to_init": "false"

                    # 1. Fondamentale: imposta la strategia su "User Features"
                    #"Kp/DetectorStrategy": "10", 
                    #"Vis/FeatureType": "10",

                    # 2. Sottoscrizione ai dati extra
                    #'subscribe_user_data': True,
                    
                    # 3. Altri parametri di ottimizzazione per SuperPoint
                    #'RGBD/ProximityPathMaxNeighbors': '10',
                    #'Mem/IncrementalMemory': 'true',

                }],
                remappings=[
                    ('rgb/image', '/oak/rgb/image_raw'),
                    ('rgb/camera_info', '/oak/rgb/camera_info'),
                    ('depth/image', '/oak/stereo/image_raw'),
                    # [EKF] Rimappiamo l'uscita: da 'odom' standard a un topic specifico per EKF
                    ('odom', '/odometry/visual'), 
                    #('user_data', '/cpu/superpoint/keypoints'), # <--- COLLEGA QUI I KEYPOINTS
                ]
            )
        ]
    )

    # E) EKF Robot Localization (Sensor Fusion)
    # Fonde /imu/data + /odometry/visual -> Pubblica /odom e la TF corretta
    ekf_node = Node(
        package='robot_localization',
        executable='ekf_node',
        name='ekf_filter_node',
        output='screen',
        parameters=[ekf_config_path],
        remappings=[
            # L'EKF pubblica su /odometry/filtered, noi lo vogliamo sul topic standard /odom
            ('odometry/filtered', '/odom')
        ]
    )

    # ----------------------------
    # 4. SLAM & Controllo
    # ----------------------------

    # F) RTAB-Map
    rtabmap_node = TimerAction(
        period=8.0,
        actions=[
            Node(
                package='rtabmap_slam',
                executable='rtabmap',
                name='rtabmap',
                prefix='taskset -c 0',
                output='screen',
                parameters=[
                    rtabmap_config,
                    {
                        'database_path': database_path,
                        'frame_id': 'base_link',
                        'odom_frame_id': 'odom', # Usa l'odom fuso dall'EKF
                        'map_frame_id': 'map',
                        'subscribe_rgb': True,
                        'subscribe_depth': True,
                        'subscribe_odom': True,
                        'approx_sync': True,
                        'approx_sync': True,
                        'approx_sync_max_interval': 0.05, # <--- AUMENTA QUESTO
                        'Kp/DetectorStrategy': '6',       # <--- USIAMO GFTT/ORB INVECE DI SIFT
                        "Vis/FeatureType": "6",
                        "Rtabmap/DetectionRate": "1.0",   # 1 Hz va bene per il Pi

                         # === PARAMETRI PER MAPPATURA INCREMENTALE ===
                    
                        # 1) NON cancellare il database all'avvio
                        "Rtabmap/StartNewMapOnStartup": "false",
                        
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
                        "Vis/FeatureType": "6",
                        "Rtabmap/DetectionRate": "1.0",
                        
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
                    ('odom', '/odom'), # Ascolta l'EKF
                ],
                arguments=['-d']
            )
        ]
    )

        # H) SuperPoint CPU Node
    superpoint_node = Node(
        package='robopy_controller',
        executable='cpu_superpoint_node',
        name='superpoint_node',
        prefix='taskset -c 1',
        output='screen',
        parameters=[{
            'weights_path': os.path.join(pkg_share, 'models', 'superpoint_v1.pth'),
            'input_topic': '/oak/rgb/image_raw',
            'max_fps': 5.0, # Mantienilo basso per non saturare la CPU
            'conf_thresh': 0.015
        }]
    )

    # G) Hardware Control
    motor_control_node = Node(package='robopy_controller', executable='motor_control_node', output='screen')
    bluedot_node = Node(package='robopy_controller', executable='bluedot_node', output='screen')



    return LaunchDescription([
        robot_state_publisher,
        oak_node,
        madgwick_node,
        rgbd_odometry,
        ekf_node,          # [EKF] Aggiunto nodo di fusione
        rtabmap_node,
        bluedot_node,
        motor_control_node,
        #superpoint_node,

    ])