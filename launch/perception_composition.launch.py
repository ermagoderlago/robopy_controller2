#!/usr/bin/env python3
# perception_composition.launch.py
"""
Percezione a impatto RAM minimo per Nav2.

Architettura:
  fast_flow_vo_cpp pubblica:
    /camera/depth/image_raw  (320×200 @ 5Hz)
    /camera/camera_info

  Questo launch carica depthimage_to_laserscan che converte
  il depth in LaserScan per i costmap di Nav2.

  NOTA: Non usiamo un container composable con depthai_ros_driver
  perché fast_flow_vo_cpp gestisce già il device OAK-D (conflitto USB).
  Il nodo depthimage_to_laserscan è quindi standalone.

  La latenza aggiuntiva della serializzazione depth→scan è trascurabile
  (<1ms) dato che il depth è già a bassa risoluzione (320×200).
"""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    # ================================================================
    # depthimage_to_laserscan: converte depth image → LaserScan 2D
    #
    # Considera solo 6 righe centrali dell'immagine depth per ridurre
    # il carico computazionale. Portata max 5m, pubblicazione ~10Hz.
    #
    # Output: /scan (sensor_msgs/LaserScan) - usato dai costmap Nav2
    # ================================================================
    depth_to_laserscan = Node(
        package='depthimage_to_laserscan',
        executable='depthimage_to_laserscan_node',
        name='depthimage_to_laserscan',
        output='screen',
        parameters=[{
            # Frame di output: base_link per allinearsi al robot frame
            'output_frame': 'base_link',

            # Range operativo
            'range_min': 0.3,   # min OAK-D depth (30cm)
            'range_max': 5.0,   # portata massima 5 metri

            # scan_height: numero di righe centrali dell'immagine depth
            # da analizzare. 6 righe su 200 = solo 3% dell'immagine,
            # sufficiente per rilevare ostacoli a livello del pavimento.
            'scan_height': 6,

            # scan_time: periodo nominale di pubblicazione (~10Hz).
            # La frequenza effettiva è limitata dal framerate del depth (5Hz).
            'scan_time': 0.1,
        }],
        remappings=[
            ('depth', '/camera/depth/image_raw'),
            ('depth_camera_info', '/camera/camera_info'),
            ('scan', '/scan'),
        ]
    )

    return LaunchDescription([
        depth_to_laserscan,
    ])
