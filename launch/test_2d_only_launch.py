#!/usr/bin/env python3
# simple_2d_vo_launch.py
# Odometria Visiva 2D PURA - NIENTE EKF, NIENTE IMU, SOLO VO

import os
from launch import LaunchDescription
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import AnyLaunchDescriptionSource


def generate_launch_description():

    pkg_share = get_package_share_directory('robopy_controller')

    # =========================================================================
    # FILE DI CONFIGURAZIONE
    # =========================================================================

    urdf_file = os.path.join(pkg_share, 'urdf', 'robopy.urdf')
    with open(urdf_file, 'r') as f:
        robot_description_content = f.read()

    robot_description = ParameterValue(robot_description_content, value_type=str)

    superpoint_blob = os.path.join(pkg_share, 'models', 'superpoint_raw.blob')

    # =========================================================================
    # ROBOT STATE PUBLISHER
    # =========================================================================

    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        output='screen',
        parameters=[{'robot_description': robot_description}]
    )

    # =========================================================================
    # SUPERPOINT NODE - CONFIGURAZIONE ULTRA-CONSERVATIVA
    # =========================================================================

    superpoint_node = Node(
        package='robopy_controller',
        executable='superpoint_node',
        name='superpoint_node',
        output='screen',
        parameters=[{
            # ---------------- CAMERA ----------------
            'publish_camera_info': True,
            'camera_info_file': '',

            # ---------------- PERFORMANCE ----------------
            'fps': 15,  # ⬇️ Ridotto per stabilità
            'imu_rate': 0,  # ❌ IMU disabilitata

            # ---------------- FEATURE EXTRACTION (MOLTO CONSERVATIVA) ----------------
            'use_enhanced_extraction': True,
            'feature_threshold': 0.020,  # ⬆️ SOGLIA ALTA (solo punti forti)
            'max_features': 200,         # ⬇️ POCHI punti di qualità

            # 🎯 FILTRI ANTI-BORDO AGGRESSIVI
            'grid_size': 24,             # ⬆️ Griglia fitta
            'max_per_cell': 1,           # ⬇️ SOLO 1 punto per cella
            'border': 20,                # ⬆️ Margine grande

            # ---------------- MATCHING ROBUSTO ----------------
            'use_hybrid_matching': True,
            'matcher_type': 'hybrid',
            'min_matches_for_tracking': 15,  # ⬆️ Almeno 15 match
            'flann_match_ratio': 0.65,       # ⬇️ Ratio test molto stretto

            'bf_cross_check': True,          # ✅ Abilita cross-check
            'bf_norm_type': 'L2',

            # ---------------- ODOMETRIA 2D PURA ----------------
            'publish_visual_odom': True,
            'min_keypoints_for_odom': 20,    # ⬆️ Minimo 20 punti
            'adaptive_matching': False,      # ❌ Disabilita adattività

            # 🎯 LIMITI MOVIMENTO 2D (NO ROTAZIONE X/Y)
            'max_translation_per_frame': 0.10,  # ⬇️ Max 10cm/frame
            'max_rotation_per_frame': 0.15,     # ⬇️ Max ~8° solo YAW

            # ---------------- DEBUG ----------------
            'debug_level': 'info',
            'publish_superpoint_debug': True,
            'publish_keypoints_cloud': False,    # ❌ Disabilita per performance
            'publish_matched_cloud': False,
            'publish_matches_visualization': True,

            # ---------------- DEPTH ----------------
            'publish_depth': True,
            'publish_depth_normalized': False,
            'depth_out_size': '320x200',
            'mono_out_size': '320x200',

            # ---------------- SUPERPOINT ----------------
            'superpoint_side': 'left',
            'superpoint_blob': superpoint_blob,
            'descriptor_dim': 256,

            # ---------------- PUBBLICAZIONE ----------------
            'publish_mono': True,
            'publish_features': False,       # ❌ Disabilita feature raw
            'use_imu': False,                # ❌ NIENTE IMU
            'use_rtabmap_format': False,     # ❌ Niente RTAB-Map

            # ---------------- YOLO ----------------
            'use_yolo_segmentation': False,  # ❌ Disabilita per semplicità

            # ---------------- BA ----------------
            'use_bundle_adjustment': False,  # ❌ Disabilita BA per ora
        }]
    )

    # =========================================================================
    # TF STATICI SEMPLIFICATI (SOLO 2D)
    # =========================================================================

    tf_static_nodes = [
        # base_footprint → base_link (identità)
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_footprint_to_base_link',
            arguments=['0', '0', '0', '0', '0', '0',
                       'base_footprint', 'base_link']
        ),

        # base_link → camera_link (camera ORIZZONTALE, punta avanti)
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_to_camera',
            arguments=['0.1', '0', '0.05',  # 10cm avanti, 5cm in alto
                       '0', '0', '0',       # NESSUNA inclinazione
                       'base_link', 'camera_link']
        ),

        # camera_link → camera_optical_frame (solo rotazione assi ottici)
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='camera_to_optical',
            arguments=['0', '0', '0',
                       '-1.5708', '0', '-1.5708',  # Optical frame standard
                       'camera_link', 'camera_optical_frame']
        ),
    ]

    # =========================================================================
    # FOXGLOVE BRIDGE (per visualizzazione)
    # =========================================================================

    foxglove_launch_path = os.path.join(
        get_package_share_directory('foxglove_bridge'),
        'launch',
        'foxglove_bridge_launch.xml'
    )

    foxglove_bridge = IncludeLaunchDescription(
        AnyLaunchDescriptionSource(foxglove_launch_path),
        launch_arguments={'port': '8765', 'host': '0.0.0.0'}.items()
    )

    # =========================================================================
    # HARDWARE CONTROL (opzionale)
    # =========================================================================

    motor_control_node = Node(
        package='robopy_controller',
        executable='waveshare_motor_driver',
        name='waveshare_motor_driver',
        output='screen',
        parameters=[{
            'serial_port': '/dev/ttyUSB0',
            'baud_rate': 115200,
            'wheel_radius': 0.0325,
            'wheel_separation': 0.16,
            'ticks_per_rev': 1440,
            'invert_right_encoder': True,
        }]
    )
    
    bluedot_node = Node(
        package='robopy_controller',
        executable='bluedot_node',
        output='screen'
    )

    # =========================================================================
    # IMAGE COMPRESSOR (per Foxglove)
    # =========================================================================

    image_compressor_node = Node(
        package='robopy_controller',
        executable='image_compressor_node',
        name='image_compressor',
        output='screen',
        parameters=[{
            'ui_fps': 8.0,           # ⬇️ Ridotto per performance
            'jpeg_quality': 60,      # ⬆️ Qualità sufficiente
            'resize_factor': 1.0,
            'use_png_for_depth': True,
        }]
    )

    # =========================================================================
    # LAUNCH DESCRIPTION (SOLO ESSENZIALE)
    # =========================================================================

    return LaunchDescription([
        robot_state_publisher,
        *tf_static_nodes,
        
        superpoint_node,          # ✅ Visual Odometry
        
        # Hardware (opzionale - commentare se non serve)
        # bluedot_node,
        # motor_control_node,
        
        foxglove_bridge,          # ✅ Visualizzazione
        image_compressor_node,    # ✅ Stream video
    ])