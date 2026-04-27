from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package='rtabmap_ros',
            executable='rtabmap',
            output='screen',
            parameters=[{
                'frame_id': 'base_link',
                'odom_frame_id': 'odom',
                'subscribe_rgb': True,
                'subscribe_imu': True,
                'approx_sync': True,
                'wait_imu_to_init': True,
                'qos_imu': 2,  # QoS_SENSOR_DATA
                'Reg/Strategy': 1,  # 1=visuale
                'Optimizer/GravitySigma': '0.1'  # Vincolo gravità IMU
            }],
            remappings=[
                ('rgb/image', '/camera/image_raw'),
                ('rgb/camera_info', '/camera/camera_info'),
                ('imu', '/imu/data')
            ],
            arguments=['--delete_db_on_start']
        )
    ])