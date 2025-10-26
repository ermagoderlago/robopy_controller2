from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import IncludeLaunchDescription, ExecuteProcess
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command
from ament_index_python.packages import get_package_share_directory
import os
from launch.actions import TimerAction
import subprocess

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
            'frame_id': 'camera_frame',  # Cambiato da base_link a camera_frame
            'map_frame_id': 'map',
            'odom_frame_id': 'odom',
        
            'subscribe_depth': True,  # ✅ ORA ABILITATO
            'subscribe_rgb': True,
            'subscribe_scan': False,
            'subscribe_odom': True,
            'subscribe_imu': False,
            'approx_sync': True,
            'queue_size': 30,
            'topic_queue_size': 20,
            
            # === PARAMETRI PER RGB-D ===
            'Odom/Strategy': '1',
            'Vis/FeatureType': '6',
            'Vis/MaxFeatures': '300',
            'Kp/MaxFeatures': '300',
            'Kp/DetectorStrategy': '6',
            
            # Mappa 3D abilitata
            'Grid/3D': 'true',
            'Grid/CellSize': '0.05',
            'Grid/RangeMax': '4.0',
            
            # Loop closure
            'Rtabmap/DetectionRate': '1.5',  # Ridotto per performance
            'Rtabmap/LoopThr': '0.15',
            'Mem/STMSize': '15',
            
            # Ottimizzazione
            'Reg/Strategy': '1',
            'Optimizer/Strategy': '1',
            'Optimizer/Iterations': '5',  # Ridotto
            
            # Aggiornamento mappa
            'RGBD/LinearUpdate': '0.3',
            'RGBD/AngularUpdate': '0.4',
            
            # Parametri specifici per depth simulata
            'Reg/Force3DoF': 'false',  # Disabilitato per 3D
            'Icp/PointToPlane': 'false',
            'Icp/Strategy': '0',

            # QoS
            'qos_image': 2,
            'qos_camera_info': 2,
            'qos_odom': 2,
            'qos_imu': 2,
        }






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


        # Odometria Visuale
        Node(
            package='rtabmap_odom',
            executable='rgbd_odometry',
            name='visual_odometry',
            output='screen',
            parameters=[{
                'frame_id': 'camera_frame',
                'odom_frame_id': 'odom',
                'publish_tf': True,
                'approx_sync': True,
                'queue_size': 20,
                'Odom/Strategy': 1,
                'Vis/FeatureType': 6,
                'Vis/MaxFeatures': 400,
                'Odom/VisKeyFrameThr': 30,
            }],
            remappings=[
                ('rgb/image', '/camera/image_raw'),
                ('rgb/camera_info', '/camera/camera_info'),
                ('depth/image', '/camera/depth/image_raw'),  # ✅ AGGIUNTO depth
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



        Node(
            package='robopy_controller',
            executable='performance_monitor',
            name='performance_monitor',
            output='screen'
        ),




 # RTAB-Map SLAM (MODIFICATO per RGB-D)
        Node(
            package='rtabmap_slam',
            executable='rtabmap',
            name='rtabmap',
            output='screen',
            parameters=[rtabmap_params],
            remappings=[
                ('rgb/image', '/camera/image_raw'),
                ('rgb/camera_info', '/camera/camera_info'),
                ('depth/image', '/camera/depth/image_raw'),      # ✅ AGGIUNTO
                ('depth/camera_info', '/camera/depth/camera_info'), # ✅ AGGIUNTO
                ('odom', '/odometry/visual'),
                ('grid_map', '/map')
            ],
            arguments=['--delete_db_on_start']
        ),




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