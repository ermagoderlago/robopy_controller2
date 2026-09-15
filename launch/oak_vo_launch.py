import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, Node
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node as RosNode
from launch_ros.substitutions import FindPackageShare

def generate_launch_description():
    """
    Complete launch file for visual odometry system with EKF fusion
    
    Components:
    1. robot_state_publisher - publishes TF tree from URDF
    2. oak_vo_node - visual odometry (ORB + SuperPoint)
    3. robot_localization EKF - fuses wheel odom + visual odom + IMU
    
    Package: robopy_controller
    """
    
    # Paths
    pkg_name = 'robopy_controller'
    pkg_share = FindPackageShare(pkg_name)
    urdf_file = PathJoinSubstitution([pkg_share, 'urdf', 'robopy.urdf']) # Using robopy.urdf based on listing
    
    # Arguments
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    
    return LaunchDescription([
        # Argument declarations
        DeclareLaunchArgument('use_sim_time', default_value='false'),
        DeclareLaunchArgument('publish_tf', default_value='false', 
            description='Let VO publish TF (false = use EKF instead)'),
        DeclareLaunchArgument('enable_yolo', default_value='false',
            description='Enable YOLO detection (requires blob file)'),
        
        # =====================================================================
        # 1. ROBOT STATE PUBLISHER (publishes static transforms from URDF)
        # =====================================================================
        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            name='robot_state_publisher',
            output='screen',
            parameters=[{
                'use_sim_time': use_sim_time,
                'robot_description': open('/home/robopy/robopy/robopi_controller/robopy_controller_host/urdf/robopy.urdf').read(),  # Reading directly for safety or correct finding
            }]
        ),
        
        # =====================================================================
        # 2. VISUAL ODOMETRY NODE
        # =====================================================================
        Node(
            package=pkg_name,
            executable='oak_superpoint_odometry_cpp', # C++ executable
            name='oak_vo_node',
            output='screen',
            parameters=[{
                # SuperPoint
                'superpoint_blob_path': '/home/robopy/robopy/robopi_controller/robopy_controller_host/models/superpoint_480x360.blob',
                'superpoint_relocalization': True,
                
                # YOLO (optional)
                'yolo_blob_path': '/home/robopy/robopy/robopi_controller/robopy_controller_host/models/yolov6/yolov6nr1_coco_640x352.blob', # Guessed path, adjust if needed
                'enable_yolo': LaunchConfiguration('enable_yolo'),
                'yolo_frequency': 2.0,
                
                # VO Algorithm
                'use_orb_primary': True,  # ✅ ORB as primary tracker
                'max_orb_features': 500,
                'min_features': 30,
                'min_inliers': 12,
                'lost_tracking_threshold': 10,
                'relocalization_inliers': 30,
                
                # Performance
                'vo_skip_frames': 1,  # Process every frame
                'depth_fps': 30.0,
                'depth_resolution': '400p',
                'depth_pub_width': 320,
                'depth_pub_height': 200,
                
                # Image Processing
                'enable_clahe': False,  # ORB is robust enough
                'use_bruteforce': False,  # FLANN for speed
                
                # Depth Processing
                'min_depth': 0.3,
                'max_depth': 8.0,
                
                # Output
                'publish_tf': LaunchConfiguration('publish_tf'),  # ✅ false for EKF
                'filter_alpha': 0.25,
                
                'use_sim_time': use_sim_time,
            }],
            # Remap output to visual_odom namespace so EKF can fuse it
            remappings=[
                ('/vo/odom', '/visual_odom/odom'),  # ✅ Separate namespace
            ]
        ),
        
        # =====================================================================
        # 3. ROBOT_LOCALIZATION EKF (fuses all odometry sources)
        # =====================================================================
        Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_filter_node',
            output='screen',
            parameters=[{
                'use_sim_time': use_sim_time,
                
                # ✅ CRITICAL: This publishes odom -> base_link transform
                'publish_tf': True,
                
                # Frames
                'map_frame': 'map',
                'odom_frame': 'odom',
                'base_link_frame': 'base_link',
                'world_frame': 'odom',
                
                # Frequency
                'frequency': 30.0,
                
                # Input sources
                'odom0': '/wheel_odom',  # From your wheel encoders (if available)
                'odom1': '/visual_odom/odom',  # From visual odometry ✅
                'imu0': '/oak/imu/data',  # From OAK-D IMU
                
                # ✅ WHEEL ODOMETRY CONFIGURATION (Example)
                'odom0_config': [
                    True,  True,  False,
                    False, False, True,
                    False, False, False,
                    False, False, False,
                    False, False, False
                ],
                
                # ✅ VISUAL ODOMETRY CONFIGURATION
                'odom1_config': [
                    True,  True,  True,   # x, y, zPosition
                    False, False, True,   # yaw
                    False, False, False,
                    False, False, False,
                    False, False, False
                ],
                
                # ✅ IMU CONFIGURATION
                'imu0_config': [
                    False, False, False,
                    False, False, False,
                    False, False, False,
                    True,  True,  True,   # Angular vel
                    True,  True,  True    # Linear accel
                ],
                
                'odom0_differential': False,
                'odom1_differential': False,
                'imu0_remove_gravitational_acceleration': True,
                
                # Initial covariance (optional, EKF handles it usually)
            }],
            remappings=[
                ('/odometry/filtered', '/odom'),  # ✅ Final fused odometry
            ]
        )
    ])
