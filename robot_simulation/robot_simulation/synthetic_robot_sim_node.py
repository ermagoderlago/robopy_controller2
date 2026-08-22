#!/usr/bin/env python3
import math
import numpy as np
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu, JointState, LaserScan, Image, CameraInfo
from tf2_ros import TransformBroadcaster

class SyntheticRobotSimNode(Node):
    def __init__(self):
        super().__init__('synthetic_robot_sim_node')

        self.declare_parameter('use_sim_time', False)

        # Kinematic state
        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0
        self.v = 0.0
        self.w = 0.0
        self.left_wheel_pos = 0.0
        self.right_wheel_pos = 0.0

        # Subscriptions
        self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_callback, 10)

        # Publishers
        self.odom_pub = self.create_publisher(Odometry, '/odom', 10)
        self.joint_pub = self.create_publisher(JointState, '/joint_states', 10)
        self.imu_pub = self.create_publisher(Imu, '/imu/data', 10)
        self.scan_pub = self.create_publisher(LaserScan, '/scan', 10)
        self.rgb_pub = self.create_publisher(Image, '/rgb/image', 10)
        self.depth_pub = self.create_publisher(Image, '/camera/depth/image_raw', 10)
        self.camera_info_pub = self.create_publisher(CameraInfo, '/camera/camera_info', 10)

        self.tf_broadcaster = TransformBroadcaster(self)

        # Obstacles in world coordinates (x, y, radius/size)
        self.obstacles = [
            {'type': 'box', 'x': 2.0, 'y': 0.5, 'size': 0.5},
            {'type': 'box', 'x': -1.0, 'y': 1.5, 'size': 0.8},
            {'type': 'cylinder', 'x': 1.5, 'y': -1.5, 'radius': 0.3},
            {'type': 'box', 'x': -1.5, 'y': -1.0, 'size': 0.4}
        ]

        # Timer at 20 Hz (50ms)
        self.timer = self.create_timer(0.05, self.update_sim)
        self.get_logger().info('Synthetic Robot Simulation Node started!')

    def cmd_vel_callback(self, msg: Twist):
        self.v = msg.linear.x
        self.w = msg.angular.z

    def update_sim(self):
        dt = 0.05
        now = self.get_clock().now().to_msg()

        # 1. Update Kinematics
        self.x += self.v * math.cos(self.yaw) * dt
        self.y += self.v * math.sin(self.yaw) * dt
        self.yaw += self.w * dt

        # Normalize yaw
        self.yaw = math.atan2(math.sin(self.yaw), math.cos(self.yaw))

        wheel_base = 0.22
        wheel_radius = 0.033
        v_l = self.v - (self.w * wheel_base / 2.0)
        v_r = self.v + (self.w * wheel_base / 2.0)
        self.left_wheel_pos += (v_l / wheel_radius) * dt
        self.right_wheel_pos += (v_r / wheel_radius) * dt

        # 2. Publish TF (odom -> base_link)
        t = TransformStamped()
        t.header.stamp = now
        t.header.frame_id = 'odom'
        t.child_frame_id = 'base_link'
        t.transform.translation.x = self.x
        t.transform.translation.y = self.y
        t.transform.translation.z = 0.0
        t.transform.rotation.z = math.sin(self.yaw / 2.0)
        t.transform.rotation.w = math.cos(self.yaw / 2.0)
        self.tf_broadcaster.sendTransform(t)

        # 3. Publish Odometry
        odom = Odometry()
        odom.header.stamp = now
        odom.header.frame_id = 'odom'
        odom.child_frame_id = 'base_link'
        odom.pose.pose.position.x = self.x
        odom.pose.pose.position.y = self.y
        odom.pose.pose.position.z = 0.0
        odom.pose.pose.orientation = t.transform.rotation
        odom.twist.twist.linear.x = self.v
        odom.twist.twist.angular.z = self.w
        self.odom_pub.publish(odom)

        # 4. Publish Joint States
        js = JointState()
        js.header.stamp = now
        js.name = ['left_wheel_joint', 'right_wheel_joint']
        js.position = [self.left_wheel_pos, self.right_wheel_pos]
        js.velocity = [v_l / wheel_radius, v_r / wheel_radius]
        self.joint_pub.publish(js)

        # 5. Publish IMU Data
        imu = Imu()
        imu.header.stamp = now
        imu.header.frame_id = 'imu_link'
        imu.orientation = t.transform.rotation
        imu.angular_velocity.z = self.w
        imu.linear_acceleration.z = 9.81
        self.imu_pub.publish(imu)

        # 6. Publish Synthetic LaserScan (360 degrees)
        scan = LaserScan()
        scan.header.stamp = now
        scan.header.frame_id = 'base_link'
        scan.angle_min = -math.pi
        scan.angle_max = math.pi
        num_readings = 180
        scan.angle_increment = (scan.angle_max - scan.angle_min) / num_readings
        scan.time_increment = 0.0
        scan.range_min = 0.1
        scan.range_max = 8.0

        ranges = []
        for i in range(num_readings):
            angle = scan.angle_min + i * scan.angle_increment
            global_angle = self.yaw + angle

            # Raycasting against 6x6m walls (-3.0 to 3.0)
            r_wall = 8.0
            cos_a = math.cos(global_angle)
            sin_a = math.sin(global_angle)

            if abs(cos_a) > 1e-4:
                tx1 = (3.0 - self.x) / cos_a
                tx2 = (-3.0 - self.x) / cos_a
                if tx1 > 0: r_wall = min(r_wall, tx1)
                if tx2 > 0: r_wall = min(r_wall, tx2)
            if abs(sin_a) > 1e-4:
                ty1 = (3.0 - self.y) / sin_a
                ty2 = (-3.0 - self.y) / sin_a
                if ty1 > 0: r_wall = min(r_wall, ty1)
                if ty2 > 0: r_wall = min(r_wall, ty2)

            # Raycasting against obstacles
            r_obj = 8.0
            for obs in self.obstacles:
                dx = obs['x'] - self.x
                dy = obs['y'] - self.y
                d_center = math.hypot(dx, dy)
                angle_to_obs = math.atan2(dy, dx)
                diff = math.atan2(math.sin(global_angle - angle_to_obs), math.cos(global_angle - angle_to_obs))
                if abs(diff) < 0.3:
                    size = obs.get('radius', obs.get('size', 0.5) / 2.0)
                    r_candidate = d_center - size
                    if r_candidate > 0:
                        r_obj = min(r_obj, r_candidate)

            ranges.append(min(r_wall, r_obj))

        scan.ranges = ranges
        self.scan_pub.publish(scan)

        # 7. Publish Synthetic RGB Image (320x240)
        rgb_img = Image()
        rgb_img.header.stamp = now
        rgb_img.header.frame_id = 'camera_depth_optical_frame'
        rgb_img.height = 120
        rgb_img.width = 160
        rgb_img.encoding = 'rgb8'
        rgb_img.step = 160 * 3
        # Synthetic blue/green gradient
        arr = np.zeros((120, 160, 3), dtype=np.uint8)
        arr[:, :, 0] = 50
        arr[:, :, 1] = 120
        arr[:, :, 2] = 200
        rgb_img.data = arr.tobytes()
        self.rgb_pub.publish(rgb_img)

        # 8. Publish Synthetic Depth Image (320x240 float32)
        depth_img = Image()
        depth_img.header.stamp = now
        depth_img.header.frame_id = 'camera_depth_optical_frame'
        depth_img.height = 120
        depth_img.width = 160
        depth_img.encoding = '32FC1'
        depth_img.step = 160 * 4
        depth_arr = np.ones((120, 160), dtype=np.float32) * 2.5
        depth_img.data = depth_arr.tobytes()
        self.depth_pub.publish(depth_img)

        # 9. Publish Camera Info
        ci = CameraInfo()
        ci.header.stamp = now
        ci.header.frame_id = 'camera_depth_optical_frame'
        ci.height = 120
        ci.width = 160
        ci.distortion_model = 'plumb_bob'
        ci.d = [0.0, 0.0, 0.0, 0.0, 0.0]
        # Focal length for 90deg FOV: f = width / (2 * tan(FOV/2)) = 160 / 2 = 80
        ci.k = [80.0, 0.0, 80.0,
                0.0, 80.0, 60.0,
                0.0, 0.0, 1.0]
        ci.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        ci.p = [80.0, 0.0, 80.0, 0.0,
                0.0, 80.0, 60.0, 0.0,
                0.0, 0.0, 1.0, 0.0]
        self.camera_info_pub.publish(ci)

def main(args=None):
    rclpy.init(args=args)
    node = SyntheticRobotSimNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()
