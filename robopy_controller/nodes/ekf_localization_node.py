#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from robot_localization import ekf_node

class EKFLocalizationNode(Node):
    def __init__(self):
        super().__init__('ekf_localization_node')
        
        # Carica i parametri dal file YAML
        self.declare_parameters(
            namespace='',
            parameters=[
                ('frequency', 50.0),
                ('sensor_timeout', 0.1),
                ('two_d_mode', True),
                ('map_frame', 'map'),
                ('odom_frame', 'odom'),
                ('base_link_frame', 'base_link'),
                ('world_frame', 'odom'),
                ('odometry0', '/odom'),
                ('odometry0_config', [True] * 15),
                ('odometry0_differential', False),
                ('odometry0_relative', False),
                ('imu0', '/imu/data_raw'),
                ('imu0_config', [False] * 15),
                ('imu0_differential', False),
                ('imu0_relative', True),
                ('debug', False)
            ]
        )
        
        # Ottieni tutti i parametri
        params = {name: self.get_parameter(name).value for name in self._parameters}
        
        self.get_logger().info("Starting EKF Localization Node with parameters:")
        for name, value in params.items():
            self.get_logger().info(f"  {name}: {value}")
        
        # Crea e avvia il nodo EKF
        self.ekf = ekf_node.EKFNode(
            'ekf_filter_node',
            params=params
        )
    
    def destroy_node(self):
        self.get_logger().info("Shutting down EKF Localization Node")
        self.ekf.destroy_node()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = EKFLocalizationNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()