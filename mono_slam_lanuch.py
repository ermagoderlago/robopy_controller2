from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package='rtabmap_slam',
            executable='rtabmap',
            name='rtabmap',
            output='screen',
            parameters=[{
                'frame_id': 'camera_frame',
                'subscribe_depth': False,
                'subscribe_rgb': True,
                'subscribe_stereo': False,
                'subscribe_odom_info': False,
                'subscribe_odom': False,  # <--- DISABILITA /odom
                'approx_sync': True,
                'queue_size': 10,
                'RGBD/ProximityBySpace': False,
                'Vis/MinInliers': 15,
                'RGBD/OptimizeMaxError': 3.0,
                'Vis/CorGuessMotion': True,
                'Rtabmap/UseOdomFeatures': True,
                'Rtabmap/PublishRAMUsage': True,
                'Mem/InitWMWithAllNodes': True,
                'Odom/Strategy': 0,  # visual odometry
            }],
            remappings=[
                ('rgb/image', '/raw_image'),
                ('rgb/camera_info', '/camera_info'),
            ],
        )
    ])


