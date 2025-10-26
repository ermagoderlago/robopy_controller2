from launch import LaunchDescription
from launch_ros.actions import Node
import os

# 🔧 Disattiva SHM che causa problemi nei container
os.environ['RMW_IMPLEMENTATION'] = 'rmw_fastrtps_cpp'
#os.environ['FASTRTPS_DEFAULT_PROFILES_FILE'] = '/dev/null'

def generate_launch_description():
    parameters = [{
        'frame_id': 'camera_frame',
        'subscribe_depth': True,
        'subscribe_odom_info': True,
        'sync_queue_size': 25,
        'Odom/Strategy': '0',
        'Odom/ResetCountdown': '15',
        'Odom/GuessSmoothingDelay': '0',
        'Rtabmap/StartNewMapOnLoopClosure': 'true',
        'RGBD/CreateOccupancyGrid': 'false',
        'Rtabmap/CreateIntermediateNodes': 'true',
        'RGBD/LinearUpdate': '0',
        'RGBD/AngularUpdate': '0',
        'use_sim_time': False  # Impostalo a True se usi Gazebo o bag ROS
    }]
 
    remappings = [
        ('rgb/image', '/rgb/image'),
        ('rgb/camera_info', '/rgb/camera_info'),
        ('depth/image', '/rgb/depth')
    ]

    return LaunchDescription([
        Node(
            package='rtabmap_odom',
            executable='rgbd_odometry',
            name='rgbd_odometry',
            output='screen',
            parameters=parameters,
            remappings=remappings
        ),
        Node(
            package='rtabmap_slam',
            executable='rtabmap',
            name='rtabmap',
            output='screen',
            parameters=parameters,
            remappings=remappings,
            arguments=['-d']  # usa database in memoria
        ),
        #Node(
        #    package='rtabmap_viz',
        #    executable='rtabmap_viz',
        #    name='rtabmap_viz',
        #    output='screen',
        #    parameters=parameters,
        #    remappings=remappings
        #),
        
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='camera_tf',
            output='screen',
            arguments=[
                '0.0', '0.0', '0.0',    # translation
                '0.0', '0.0', '0.0',    # rotation
                'camera_frame', 'base_link'  # child, parent
            ]
        )
    ])



