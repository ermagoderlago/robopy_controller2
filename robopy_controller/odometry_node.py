import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Point, Pose, Quaternion, Twist, Vector3, TransformStamped
from std_msgs.msg import Float64MultiArray
from tf2_ros import TransformBroadcaster
import math

import os
os.environ['RMW_IMPLEMENTATION'] = 'rmw_fastrtps_cpp'
os.environ['FASTRTPS_TRANSPORT_USE_SHM'] = '0'



class OdometryNode(Node):
    def __init__(self):
        super().__init__('odometry_node')

        # Parametri odometria
        self.wheel_radius = 0.03  # metri
        self.wheel_separation = 0.1  # metri
        self.odom_frame = "odom"
        self.base_frame = "base_link"

        # Stato del robot
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0

        # Publisher odometria
        self.odom_pub = self.create_publisher(Odometry, '/odom', 10)
        self.timer = self.create_timer(0.1, self.publish_odom)  # 10 Hz

        # Broadcaster TF
        self.tf_broadcaster = TransformBroadcaster(self)

        # Subscriber velocit� motori
        self.subscription = self.create_subscription(
            Float64MultiArray,
            'motor_speed',
            self.listener_callback,
            10)

        self.left_speed = 0.0
        self.right_speed = 0.0

        self.get_logger().info("Nodo odometria avviato")

    def listener_callback(self, msg):
        self.left_speed = msg.data[0]
        self.right_speed = msg.data[1]

    def publish_odom(self):
        dt = 0.1

        left_distance = self.left_speed * self.wheel_radius * dt
        right_distance = self.right_speed * self.wheel_radius * dt

        delta_distance = (right_distance + left_distance) / 2
        delta_theta = (right_distance - left_distance) / self.wheel_separation

        delta_x = delta_distance * math.cos(self.theta)
        delta_y = delta_distance * math.sin(self.theta)

        self.x += delta_x
        self.y += delta_y
        self.theta += delta_theta

        q = self.quaternion_from_euler(0, 0, self.theta)

        odom = Odometry()
        odom.header.stamp = self.get_clock().now().to_msg()
        odom.header.frame_id = self.odom_frame
        odom.child_frame_id = self.base_frame

        odom.pose.pose.position = Point(x=self.x, y=self.y, z=0.0)
        odom.pose.pose.orientation = Quaternion(x=q[0], y=q[1], z=q[2], w=q[3])

        odom.twist.twist = Twist(
            linear=Vector3(x=delta_x/dt, y=delta_y/dt, z=0.0),
            angular=Vector3(x=0.0, y=0.0, z=delta_theta/dt)
        )

        self.odom_pub.publish(odom)

        # Pubblica transform odom -> base_link
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = self.odom_frame
        t.child_frame_id = self.base_frame
        t.transform.translation.x = self.x
        t.transform.translation.y = self.y
        t.transform.translation.z = 0.0
        t.transform.rotation = odom.pose.pose.orientation

        self.tf_broadcaster.sendTransform(t)

    def quaternion_from_euler(self, roll, pitch, yaw):
        cy = math.cos(yaw * 0.5)
        sy = math.sin(yaw * 0.5)
        cp = math.cos(pitch * 0.5)
        sp = math.sin(pitch * 0.5)
        cr = math.cos(roll * 0.5)
        sr = math.sin(roll * 0.5)

        q = [0] * 4
        q[0] = sr * cp * cy - cr * sp * sy
        q[1] = cr * sp * cy + sr * cp * sy
        q[2] = cr * cp * sy - sr * sp * cy
        q[3] = cr * cp * cy + sr * sp * sy

        return q

def main(args=None):
    rclpy.init(args=args)
    odometry_node = OdometryNode()
    rclpy.spin(odometry_node)
    rclpy.shutdown()

if __name__ == '__main__':
    main()
