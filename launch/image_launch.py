from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import IncludeLaunchDescription, ExecuteProcess
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command
from ament_index_python.packages import get_package_share_directory
import os

# Disattiva SHM che causa problemi nei container
os.environ['RMW_IMPLEMENTATION'] = 'rmw_fastrtps_cpp'

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
    rtabmap_params_file = os.path.join(
        get_package_share_directory('robopy_controller'),
        'config',
        'rtabmap_params.yaml'
    )

    robot_description_content = Command(['xacro', ' ', urdf_file])

    rtabmap_params = {
        'frame_id': 'camera_frame',
        'subscribe_depth': False,  # Disabilitato per modalità mono
        'subscribe_rgb': True,
        'subscribe_scan': False,
        'approx_sync': True,
        'queue_size': 60,
        'qos_image': 2,
        'qos_camera_info': 2,
        'Reg/Force3DoF': 'false',  # 6DoF con IMU
        'RGBD/AngularUpdate': '0.05',
        'RGBD/LinearUpdate': '0.05',
        'Optimizer/Strategy': '1',
        'Grid/CellSize': '0.05',
        'Rtabmap/DetectionRate': '1',
        'Kp/MaxFeatures': '200',
        'Vis/MaxDepth': '10.0',
        'RGBD/OptimizeMaxError': '0.1',
        'Grid/MaxGroundHeight': '0.1',
        'Grid/MaxObstacleHeight': '0.5',
        'rgbd_sync/approx_sync': True,
        'rgbd_sync/queue_size': 60,
        
        # Parametri aggiuntivi per IMU
        'use_imu': True,
        'wait_imu_to_init': True,
        'imu_fusion': 1,
        'ImsFusion': 1,
        'imu_topic': '/imu/data_raw',
        'odom_frame_id': 'odom',
        'map_frame_id': 'map',
        'publish_tf_map': False  # Fondamentale per evitare conflitti
    }

    return LaunchDescription([
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
            package='robopy_controller',
            executable='odometry_node',
            name='odometry_node',
            output='screen',
            parameters=[{
                'odom_frame_id': 'odom',
                'base_frame_id': 'base_link',
                'publish_tf': True
            }]
        ),

        Node(
            package='robopy_controller',
            executable='camera_publisher_node',
            name='camera_publisher_node',
            output='screen',
            parameters=[{
                'frame_id': 'camera_frame',
                'camera_info_url': 'package://robopy_controller/config/camera_info.yaml'
            }]
        ),

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
        
        # Aggiunto: static transform da camera_frame a imu_link
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='camera_to_imu_tf',
            arguments=['0', '0', '0', '0', '0', '0', 'camera_frame', 'imu_link'],
            output='screen'
        ),

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
        #        ('odom', '/odometry/filtered')  # Usa l'output EKF
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

        # RTAB-Map Node (sbloccato e modificato)
               
   #     Node(
   #         package='rtabmap_slam',  # <-- corretto
   #         executable='rtabmap',
   #         name='rtabmap',
   #         output='screen',
   #         parameters=[rtabmap_params_file, rtabmap_params],
   #         remappings=[
   #             ('rgb/image', '/camera/image_raw'),
   #             ('rgb/camera_info', '/camera/camera_info'),
   #             ('odom', '/odometry/filtered')  # Usa l'odometria filtrata da EKF
   #         ],
   #         arguments=['--delete_db_on_start']
   #     ),


        # ✅ IMU Node
        Node(
            package='robopy_controller',
            executable='IMU_node',
            name='imu_node',
            output='screen',
            parameters=[{
                'frame_id': 'imu_link',
                'use_mag': False  # Disabilita se non usi il magnetometro
            }]
        ),

        # ✅ EKF Localization Node (il tuo nodo personalizzato)
        Node(
            package='robopy_controller',
            executable='ekf_localization_node',
            name='ekf_localization',
            output='screen',
            parameters=[ekf_config]
        ),

        # ExecuteProcess(
        #     cmd=[
        #         'rpicam-vid',
        #         '-t', '0',
        #         '--width', '640',
        #         '--height', '480',
        #         '--framerate', '25',
        #         '--codec', 'mjpeg',
        #         '--nopreview',
        #         '--inline',
        #         '--listen',
        #         '-o', 'udp://127.0.0.1:5000'
        #     ],
        #     output='screen'
        # ),
        # TODO: Setup standard v4l2_camera or usb_cam here for PC environment

        ExecuteProcess(
            cmd=['rviz2', '-d', '/home/robopy/severus/config/robopy.rviz'],
            output='screen'
        ),

        Node(
            package='robopy_controller',
            executable='performance_monitor',
            name='performance_monitor',
            output='screen'
        ),
    ])
