#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu
from geometry_msgs.msg import Pose, Point, Quaternion, Twist, Vector3
import numpy as np
import math
import transforms3d


try:
    from tf_transformations import quaternion_from_euler, euler_from_quaternion
except ImportError:
    # Fallback a transforms3d se tf_transformations non è disponibile
    from transforms3d.euler import euler2quat, quat2euler
    
    def quaternion_from_euler(roll, pitch, yaw):
        q = euler2quat(roll, pitch, yaw, 'sxyz')
        return [q[1], q[2], q[3], q[0]]  # Converti da w,x,y,z a x,y,z,w
    
    def euler_from_quaternion(quaternion):
        # La quaternion in ROS è x,y,z,w
        q = [quaternion[3], quaternion[0], quaternion[1], quaternion[2]]  # Converti a w,x,y,z
        euler = quat2euler(q, 'sxyz')
        return euler

class HybridOdometry3DNode(Node):
    def __init__(self):
        super().__init__('hybrid_odometry_3d_node')
        
        # Subscribers
        self.odom_sub = self.create_subscription(Odometry, '/odom', self.odom_callback, 10)
        self.imu_sub = self.create_subscription(Imu, '/imu/data', self.imu_callback, 10)
        
        # Publisher
        self.hybrid_odom_pub = self.create_publisher(Odometry, '/odometry/hybrid', 10)
        
        # Variabili di stato
        self.last_odom = None
        self.last_imu = None
        self.last_imu_time = None
        
        # Stime basate su IMU per movimento manuale 3D
        self.position_estimate = [0.0, 0.0, 0.0]  # x, y, z
        self.velocity_estimate = [0.0, 0.0, 0.0]  # vx, vy, vz
        self.orientation_estimate = [0.0, 0.0, 0.0]  # roll, pitch, yaw
        
        # Soglie per rilevamento movimento manuale
        self.motor_speed_threshold = 0.1
        self.acceleration_threshold = 0.3
        
        # Timer per pubblicazione
        self.timer = self.create_timer(0.05, self.publish_hybrid_odom)  # 20 Hz

    def odom_callback(self, msg):
        self.last_odom = msg

    def imu_callback(self, msg):
        self.last_imu = msg
        current_time = self.get_clock().now().nanoseconds / 1e9
        
        if self.last_imu_time is not None:
            dt = current_time - self.last_imu_time
            
            if dt > 0:
                # Estrai accelerazione lineare (rimuovi gravità approssimativamente)
                ax = msg.linear_acceleration.x
                ay = msg.linear_acceleration.y
                az = msg.linear_acceleration.z - 9.81  # Rimuovi gravità
                
                # Integra per ottenere velocità
                self.velocity_estimate[0] += ax * dt
                self.velocity_estimate[1] += ay * dt
                self.velocity_estimate[2] += az * dt
                
                # Integra per ottenere posizione
                self.position_estimate[0] += self.velocity_estimate[0] * dt
                self.position_estimate[1] += self.velocity_estimate[1] * dt
                self.position_estimate[2] += self.velocity_estimate[2] * dt
                
                # Aggiorna orientamento dalla velocità angolare
                self.orientation_estimate[0] += msg.angular_velocity.x * dt
                self.orientation_estimate[1] += msg.angular_velocity.y * dt
                self.orientation_estimate[2] += msg.angular_velocity.z * dt
        
        self.last_imu_time = current_time

    def publish_hybrid_odom(self):
        if self.last_odom is None or self.last_imu is None:
            return
            
        hybrid_odom = Odometry()
        hybrid_odom.header.stamp = self.get_clock().now().to_msg()
        hybrid_odom.header.frame_id = 'odom'
        hybrid_odom.child_frame_id = 'base_link'
        
        # Determina se il robot è mosso manualmente
        is_manual_movement = (
            abs(self.last_odom.twist.twist.linear.x) < self.motor_speed_threshold and
            abs(self.last_odom.twist.twist.angular.z) < self.motor_speed_threshold and
            (abs(self.last_imu.linear_acceleration.x) > self.acceleration_threshold or
             abs(self.last_imu.linear_acceleration.y) > self.acceleration_threshold or
             abs(self.last_imu.linear_acceleration.z - 9.81) > self.acceleration_threshold)
        )
        
        if is_manual_movement:
            # Usa stima IMU per movimento manuale 3D
            hybrid_odom.pose.pose.position = Point(
                x=self.position_estimate[0],
                y=self.position_estimate[1],
                z=self.position_estimate[2]
            )
            
            # Calcola quaternione dagli angoli di Eulero
            q = quaternion_from_euler(
                self.orientation_estimate[0],
                self.orientation_estimate[1],
                self.orientation_estimate[2]
            )
            hybrid_odom.pose.pose.orientation = Quaternion(
                x=q[0], y=q[1], z=q[2], w=q[3]
            )
            
            # Usa velocità stimata dall'IMU
            hybrid_odom.twist.twist.linear = Vector3(
                x=self.velocity_estimate[0],
                y=self.velocity_estimate[1],
                z=self.velocity_estimate[2]
            )
            hybrid_odom.twist.twist.angular = Vector3(
                x=self.last_imu.angular_velocity.x,
                y=self.last_imu.angular_velocity.y,
                z=self.last_imu.angular_velocity.z
            )
        else:
            # Usa odometria standard per movimento motorizzato
            hybrid_odom.pose.pose = self.last_odom.pose.pose
            hybrid_odom.twist.twist = self.last_odom.twist.twist
            
            # Resetta le stime IMU quando i motori sono attivi
            self.velocity_estimate = [0.0, 0.0, 0.0]
            self.position_estimate = [
                self.last_odom.pose.pose.position.x,
                self.last_odom.pose.pose.position.y,
                self.last_odom.pose.pose.position.z
            ]
            
            # Estrai orientamento dal quaternione dell'odometria
            x = self.last_odom.pose.pose.orientation.x
            y = self.last_odom.pose.pose.orientation.y
            z = self.last_odom.pose.pose.orientation.z
            w = self.last_odom.pose.pose.orientation.w
            self.orientation_estimate = euler_from_quaternion([x, y, z, w])
        
        # Pubblica l'odometria ibrida
        self.hybrid_odom_pub.publish(hybrid_odom)

def main(args=None):
    rclpy.init(args=args)
    node = HybridOdometry3DNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()