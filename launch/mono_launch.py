# File: robopy_controller/launch/full_robot_launch.py

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

    robot_description_content = Command([
        'xacro', ' ', urdf_file
    ])

    rtabmap_params = {
        'frame_id': 'camera_frame',
        'subscribe_depth': False,
        'subscribe_rgb': True,
        'subscribe_scan': False,
        'approx_sync': True,
        'queue_size': 60,
        'qos_image': 2,
        'qos_camera_info': 2,
        'Reg/Force3DoF': 'true',
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
            output='screen'
        ),

        Node(
            package='robopy_controller',
            executable='camera_publisher_node',
            name='camera_publisher_node',
            output='screen'
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

        Node(
            package='robopy_controller',
            executable='sync_publisher_node',
            name='sync_publisher_node',
            output='screen'
        ),
        Node(
            package='robopy_controller',
            executable='motion_detector_node',
            name='motion_detector_node',
            output='screen'
        ),
        #Node(
        #    package='robopy_controller',
        #    executable='object_detection_node',
        #    name='object_detection_node',
        #    output='screen'
        #),
        #Node(
        #    package='robopy_controller',
        #    executable='motion_controller_node',
        #    name='motion_controller_node',
        #    output='screen'
        #),

        Node(
            package='robopy_controller',
            executable='depth_to_pointcloud_node',
            name='depth_to_pointcloud_node',
            output='screen'
        ),

        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='map_to_odom',
            arguments=['0', '0', '0.10', '0', '0', '0', 'map', 'odom'],
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



  #      Node(
  #          package='rtabmap_viz',
  #          executable='rtabmap_viz',
  #          name='rtabmap_viz',
  #          output='screen',
  #          parameters=[rtabmap_params],
        #    remappings=[
        #        ('rgb/image', '/camera/rgb/image_rect_color'),
        #        ('depth/image', '/camera/depth/image_rect_raw'),
        #        ('rgb/camera_info', '/camera/rgb/camera_info'),
        #        ('odom', '/odom')
        #    ]
  #              remappings=[
  #          ('rgb/image', '/rgb/image'),
  #          ('rgb/camera_info', '/rgb/camera_info'),
  #          ('odom', '/odom')
  #      ]
  #      ),

        Node(
            package='rtabmap_sync',
            executable='rgbd_sync',
            name='rgbd_sync',
            output='screen',
            parameters=[{
                'approx_sync': True,
                'queue_size': 60,
                'qos': 2,
                'compress_rgb': True,
                #'compress_depth': True,
                'rate_limit': 15.0
            }],
         #   remappings=[
         #       ('rgb/image', '/camera/rgb/image_rect_color'),
         #       ('rgb/camera_info', '/camera/rgb/camera_info'),
         #       #('depth/image', '/camera/depth/image_rect_raw'),
         #       ('rgbd_image', '/rgbd_image')
         #   ]
         remappings=[
    ('rgb/image', '/rgb/image'),
    ('rgb/camera_info', '/rgb/camera_info'),
    ('odom', '/odom')
]
        ),

        Node(
            package='rtabmap_slam',
            executable='rtabmap',
            name='rtabmap',
            output='screen',
            parameters=[rtabmap_params],
            #remappings=[
            #    ('rgb/image', '/camera/rgb/image_rect_color'),
            #    #('depth/image', '/camera/depth/image_rect_raw'),
            #    ('rgb/camera_info', '/camera/rgb/camera_info'),
            #    ('odom', '/odom')  # <--- questa riga collega il topic odom
            #],
            remappings=[
                ('rgb/image', '/rgb/image'),
                ('rgb/camera_info', '/rgb/camera_info'),
                ('odom', '/odom')
            ],
            arguments=['--delete_db_on_start']
        ),

                ExecuteProcess(
            cmd=['rviz2', '-d', '/host_home/robopy/RVIZ2/robopy.rviz'],
            output='screen'
        ),
    ])