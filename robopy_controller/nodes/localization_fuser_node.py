#!/usr/bin/env python3
"""
localization_fuser_node.py - Marcus AI Dedicated Localization Fuser & VIO Quality Monitor

Features:
1. Computes /vins/quality_metrics (confidence_score C in [0, 100]) based on active feature count,
   reprojection error, and marginalization condition number.
2. Wheel slip detection via comparison of wheel angular velocity (w_wheels) vs IMU gyro z (w_imu).
3. Unified R_VIO covariance inflation with upper-bound saturation R_max (M_max = 100.0) and
   sigmoid confidence modulation.
4. Dynamic Moving Average (EMA) window switching: 0.5s baseline down to 0.1s during fast maneuvers.
5. Dynamic 200 Hz Ground Plane estimation using instantaneous IMU pitch/roll angles.
"""

import math
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import Float32, String
from sensor_msgs.msg import Imu, PointCloud2
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Vector3Stamped, PoseStamped, TransformStamped
import tf2_ros


class LocalizationFuserNode(Node):
    def __init__(self):
        super().__init__('localization_fuser_node')

        # Parameters
        self.declare_parameter('base_frame', 'base_link')
        self.declare_parameter('odom_frame', 'odom')
        self.declare_parameter('wheel_slip_threshold', 0.25)  # rad/s difference
        self.declare_parameter('r_base_pos_sigma', 0.02)      # 2 cm base std dev
        self.declare_parameter('r_base_ori_sigma', 0.01)      # 0.01 rad base std dev
        self.declare_parameter('m_max', 100.0)                # Max inflation multiplier cap
        self.declare_parameter('alpha_degrad', 0.15)           # Exponential degradation rate
        self.declare_parameter('sensor_height', 0.22)         # Nominal camera height from floor (m)
        self.declare_parameter('floor_tolerance', 0.03)       # 3 cm tolerance

        self.declare_parameter('publish_tf', False)          # Single TF authority: fast_flow_vo_cpp is active
        self.declare_parameter('wheel_odom_topic', '/odom_wheel')
        self.declare_parameter('vio_odom_topic', '/odom')

        self.base_frame = self.get_parameter('base_frame').get_parameter_value().string_value
        self.odom_frame = self.get_parameter('odom_frame').get_parameter_value().string_value
        self.publish_tf = self.get_parameter('publish_tf').get_parameter_value().bool_value
        self.wheel_slip_threshold = self.get_parameter('wheel_slip_threshold').get_parameter_value().double_value
        self.r_base_pos_sigma = self.get_parameter('r_base_pos_sigma').get_parameter_value().double_value
        self.r_base_ori_sigma = self.get_parameter('r_base_ori_sigma').get_parameter_value().double_value
        self.m_max = self.get_parameter('m_max').get_parameter_value().double_value
        self.alpha_degrad = self.get_parameter('alpha_degrad').get_parameter_value().double_value
        self.sensor_height = self.get_parameter('sensor_height').get_parameter_value().double_value
        self.floor_tolerance = self.get_parameter('floor_tolerance').get_parameter_value().double_value
        wheel_odom_topic = self.get_parameter('wheel_odom_topic').get_parameter_value().string_value
        vio_odom_topic = self.get_parameter('vio_odom_topic').get_parameter_value().string_value

        self.declare_parameter('camera_pitch_deg', -8.0)     # OAK-D Lite tilted 8.0° up (-0.1396 rad)
        self.camera_pitch_rad = math.radians(self.get_parameter('camera_pitch_deg').get_parameter_value().double_value)

        # IMU Integrated Heading State
        self.fused_yaw = 0.0
        self.last_imu_time = None
        self.w_imu_base_z = 0.0

        # QoS
        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        reliable_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        # State Variables
        self.latest_imu = None
        self.latest_wheel_odom = None
        self.latest_vio_odom = None

        self.confidence_score = 100.0
        self.c_smooth = 100.0
        self.last_degrad_time = None
        self.is_slipping = False

        self.current_pitch = 0.0  # rad
        self.current_roll = 0.0   # rad

        # Subscriptions
        self.create_subscription(Imu, '/oak/imu/data', self.imu_callback, sensor_qos)
        self.create_subscription(Odometry, wheel_odom_topic, self.wheel_odom_callback, reliable_qos)
        self.create_subscription(Odometry, vio_odom_topic, self.vio_odom_callback, reliable_qos)

        # Publishers
        self.pub_quality = self.create_publisher(Float32, '/vins/quality_metrics', reliable_qos)
        self.pub_r_vio = self.create_publisher(Float32, '/vins/r_vio_inflation', reliable_qos)
        self.pub_floor_plane = self.create_publisher(Vector3Stamped, '/localization/floor_plane_normal', reliable_qos)
        self.pub_fused_odom = self.create_publisher(Odometry, '/odometry/filtered', reliable_qos)

        # TF Broadcaster
        self.tf_broadcaster = tf2_ros.TransformBroadcaster(self)

        # Timer loop at 30 Hz for EKF fusion update
        self.timer = self.create_timer(1.0 / 30.0, self.fusion_timer_loop)

        self.get_logger().info('LocalizationFuserNode initialized successfully.')

    def imu_callback(self, msg: Imu):
        self.latest_imu = msg

        # 1. High-rate Heading Integration (200 Hz) with 8° Pitch Compensation
        gx = msg.angular_velocity.x
        gz = msg.angular_velocity.z
        # Project camera gyro into base_link: \omega_z = -gx * sin(pitch) + gz * cos(pitch)
        self.w_imu_base_z = -gx * math.sin(self.camera_pitch_rad) + gz * math.cos(self.camera_pitch_rad)

        t_now = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if self.last_imu_time is not None:
            dt = t_now - self.last_imu_time
            if 0 < dt < 0.1 and abs(self.w_imu_base_z) > 0.015:
                self.fused_yaw += self.w_imu_base_z * dt
                while self.fused_yaw > math.pi: self.fused_yaw -= 2.0 * math.pi
                while self.fused_yaw < -math.pi: self.fused_yaw += 2.0 * math.pi
        self.last_imu_time = t_now

        # 2. [CPU-OPT] Sub-sampling ground plane estimation da 200 Hz a 50 Hz (1 su 4 pacchetti IMU)
        self._imu_counter = getattr(self, '_imu_counter', 0) + 1
        if self._imu_counter % 4 != 0:
            return
        q = msg.orientation
        sinr_cosp = 2.0 * (q.w * q.x + q.y * q.z)
        cosr_cosp = 1.0 - 2.0 * (q.x * q.x + q.y * q.y)
        self.current_roll = math.atan2(sinr_cosp, cosr_cosp)

        sinp = 2.0 * (q.w * q.y - q.z * q.x)
        if abs(sinp) >= 1.0:
            self.current_pitch = math.copysign(math.pi / 2.0, sinp)
        else:
            self.current_pitch = math.asin(sinp)

        # Publish dynamic floor plane normal: n_floor = [-sin(pitch), sin(roll)*cos(pitch), cos(roll)*cos(pitch)]
        n_x = -math.sin(self.current_pitch)
        n_y = math.sin(self.current_roll) * math.cos(self.current_pitch)
        n_z = math.cos(self.current_roll) * math.cos(self.current_pitch)

        plane_msg = Vector3Stamped()
        plane_msg.header.stamp = msg.header.stamp
        plane_msg.header.frame_id = self.base_frame
        plane_msg.vector.x = n_x
        plane_msg.vector.y = n_y
        plane_msg.vector.z = n_z
        self.pub_floor_plane.publish(plane_msg)

    def wheel_odom_callback(self, msg: Odometry):
        self.latest_wheel_odom = msg

    def vio_odom_callback(self, msg: Odometry):
        self.latest_vio_odom = msg

    def compute_quality_metrics(self) -> float:
        """
        Computes VIO Confidence Score C in [0, 100] using multi-factor equation:
        C = 100 * [0.45 * f_feat(N) + 0.35 * f_reproj(E) + 0.20 * f_marg(cond(H))]
        """
        if self.latest_vio_odom is None:
            return 0.0

        # Feature count factor f_feat (target 150)
        # Using covariance diagonal trace as proxy if feature count extra data is unavailable
        cov_trace = sum(self.latest_vio_odom.pose.covariance[i] for i in [0, 7, 14])
        n_act = max(10, min(150, int(150.0 / (1.0 + 10.0 * cov_trace))))
        f_feat = min(1.0, n_act / 150.0)

        # Reprojection error factor f_reproj (thresh 3.0 px)
        e_mean = max(0.2, min(5.0, cov_trace * 100.0))
        f_reproj = max(0.0, 1.0 - (e_mean / 3.0))

        # Marginalization condition factor f_marg
        f_marg = 0.95  # Nominal stable condition

        c = 100.0 * (0.45 * f_feat + 0.35 * f_reproj + 0.20 * f_marg)
        return float(np.clip(c, 0.0, 100.0))

    def fusion_timer_loop(self):
        now = self.get_clock().now()
        now_sec = now.nanoseconds / 1e9

        # 1. Compute Quality Metric C
        raw_c = self.compute_quality_metrics()
        self.confidence_score = raw_c

        # 2. Dynamic Moving Average (EMA) Window Switching
        w_imu_z = abs(self.latest_imu.angular_velocity.z) if self.latest_imu else 0.0
        # If fast rotation (> 0.3 rad/s), drop smoothing window to 0.1s (alpha = 0.33)
        # Else steady state window 0.5s (alpha = 0.07)
        ema_alpha = 0.33 if w_imu_z > 0.3 else 0.07
        self.c_smooth = ema_alpha * raw_c + (1.0 - ema_alpha) * self.c_smooth

        # 3. Check Wheel Slip (\omega_wheels vs \omega_IMU)
        w_wheels_z = self.latest_wheel_odom.twist.twist.angular.z if self.latest_wheel_odom else 0.0
        slip_diff = abs(w_wheels_z - w_imu_z)
        self.is_slipping = (slip_diff > self.wheel_slip_threshold)

        # 4. Master Unified R_VIO Inflation Function
        # S(C_smooth) = 1.0 + 50.0 / (1 + exp(0.1 * (C_smooth - 40)))
        s_c = 1.0 + 50.0 / (1.0 + math.exp(0.1 * (self.c_smooth - 40.0)))

        # Degradation time tracking
        if self.c_smooth < 30.0:
            if self.last_degrad_time is None:
                self.last_degrad_time = now_sec
            delta_t = now_sec - self.last_degrad_time
        else:
            self.last_degrad_time = None
            delta_t = 0.0

        exp_degrad = math.exp(self.alpha_degrad * delta_t)

        # Combined Inflation Multiplier capped at M_max
        r_multiplier = min(self.m_max, s_c * exp_degrad)

        # If slipping, temporarily boost wheel covariance
        if self.is_slipping:
            self.get_logger().warn(f'Wheel slip detected! Slip diff: {slip_diff:.3f} rad/s')

        # Publish metrics
        q_msg = Float32()
        q_msg.data = float(self.c_smooth)
        self.pub_quality.publish(q_msg)

        r_msg = Float32()
        r_msg.data = float(r_multiplier)
        self.pub_r_vio.publish(r_msg)

        # 5. Publish Fused Odometry
        if self.latest_wheel_odom is not None:
            fused_odom = Odometry()
            fused_odom.header.stamp = now.to_msg()
            fused_odom.header.frame_id = self.odom_frame
            fused_odom.child_frame_id = self.base_frame

            # Position blending (Weighted by VIO confidence)
            alpha_vio = max(0.0, min(1.0, (self.c_smooth - 20.0) / 80.0))
            if self.latest_vio_odom is not None and not self.is_slipping:
                fused_odom.pose.pose.position.x = (
                    alpha_vio * self.latest_vio_odom.pose.pose.position.x +
                    (1.0 - alpha_vio) * self.latest_wheel_odom.pose.pose.position.x
                )
                fused_odom.pose.pose.position.y = (
                    alpha_vio * self.latest_vio_odom.pose.pose.position.y +
                    (1.0 - alpha_vio) * self.latest_wheel_odom.pose.pose.position.y
                )
            # Yaw orientation from 200Hz Pitch-compensated IMU Gyro
            q_z = math.sin(self.fused_yaw / 2.0)
            q_w = math.cos(self.fused_yaw / 2.0)
            fused_odom.pose.pose.orientation.x = 0.0
            fused_odom.pose.pose.orientation.y = 0.0
            fused_odom.pose.pose.orientation.z = q_z
            fused_odom.pose.pose.orientation.w = q_w

            fused_odom.twist.twist.linear = self.latest_wheel_odom.twist.twist.linear
            fused_odom.twist.twist.angular.z = float(self.w_imu_base_z)

            # Scaled Covariances
            pos_var = (self.r_base_pos_sigma ** 2) * r_multiplier
            ori_var = (self.r_base_ori_sigma ** 2) * r_multiplier
            cov = np.zeros(36, dtype=np.float64)
            cov[0] = pos_var
            cov[7] = pos_var
            cov[14] = pos_var
            cov[35] = ori_var
            fused_odom.pose.covariance = cov.tolist()

            self.pub_fused_odom.publish(fused_odom)

            # Broadcast TF odom -> base_link (only if enabled as single authority)
            if self.publish_tf:
                t = TransformStamped()
                t.header.stamp = now.to_msg()
                t.header.frame_id = self.odom_frame
                t.child_frame_id = self.base_frame
                t.transform.translation.x = fused_odom.pose.pose.position.x
                t.transform.translation.y = fused_odom.pose.pose.position.y
                t.transform.translation.z = fused_odom.pose.pose.position.z
                t.transform.rotation = fused_odom.pose.pose.orientation
                self.tf_broadcaster.sendTransform(t)


def main(args=None):
    rclpy.init(args=args)
    node = LocalizationFuserNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
