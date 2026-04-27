#!/usr/bin/env python3
# imu_transformer_node.py

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu
import math

class ImuTransformer(Node):
    def __init__(self):
        super().__init__('imu_transformer')
        
        # Parametri
        self.declare_parameter('input_topic', '/oak/imu/data')
        self.declare_parameter('output_topic', '/imu/data_rotated')
        self.declare_parameter('rotation_type', 'oakd_standard')  # 'oakd_standard', 'custom', 'none'
        
        input_topic = self.get_parameter('input_topic').value
        output_topic = self.get_parameter('output_topic').value
        self.rotation_type = self.get_parameter('rotation_type').value
        
        # Sottoscrittore e publisher
        self.subscription = self.create_subscription(
            Imu,
            input_topic,
            self.imu_callback,
            10
        )
        
        self.publisher = self.create_publisher(Imu, output_topic, 10)
        
        self.get_logger().info(f'IMU Transformer started: {input_topic} -> {output_topic} with rotation {self.rotation_type}')
        
    def imu_callback(self, msg):
        new_msg = Imu()
        new_msg.header = msg.header
        new_msg.header.frame_id = 'imu_link'  # Stesso frame
        
        if self.rotation_type == 'oakd_standard':
            # Rotazione standard per OAK-D Lite
            new_msg.linear_acceleration.x = msg.linear_acceleration.x
            new_msg.linear_acceleration.y = -msg.linear_acceleration.z  # Z -> Y invertito
            new_msg.linear_acceleration.z = msg.linear_acceleration.y    # Y -> Z
            
            new_msg.angular_velocity.x = msg.angular_velocity.x
            new_msg.angular_velocity.y = -msg.angular_velocity.z         # Z -> Y invertito
            new_msg.angular_velocity.z = msg.angular_velocity.y          # Y -> Z
            
        elif self.rotation_type == 'custom':
            # Rotazione personalizzata - modifica qui per sperimentare
            new_msg.linear_acceleration.x = msg.linear_acceleration.x
            new_msg.linear_acceleration.y = -msg.linear_acceleration.y
            new_msg.linear_acceleration.z = -msg.linear_acceleration.z
            
            new_msg.angular_velocity.x = msg.angular_velocity.x
            new_msg.angular_velocity.y = -msg.angular_velocity.y
            new_msg.angular_velocity.z = -msg.angular_velocity.z
            
        else:  # 'none'
            # Nessuna rotazione - copia i dati originali
            new_msg.linear_acceleration = msg.linear_acceleration
            new_msg.angular_velocity = msg.angular_velocity
        
        # Copia le covarianze
        new_msg.orientation_covariance = msg.orientation_covariance
        new_msg.angular_velocity_covariance = msg.angular_velocity_covariance
        new_msg.linear_acceleration_covariance = msg.linear_acceleration_covariance
        
        self.publisher.publish(new_msg)

def main(args=None):
    rclpy.init(args=args)
    node = ImuTransformer()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()