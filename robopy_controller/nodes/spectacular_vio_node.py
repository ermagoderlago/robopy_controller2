#!/usr/bin/env python3
"""
spectacular_vio_node.py — VIO Node per Marcus (OAK-D Lite)
============================================================
Implementa Visual-Inertial Odometry (VIO) per Marcus usando:
- IMU BMI270 OAK-D Lite letta via topic ROS2 `/oak/imu/data`
- Odometria encoder ruote `/odom` (waveshare_motor_driver)

ARCHITETTURA:
- Autorità TF: odom → base_link (30 Hz, unica autorità)
- Input A: /oak/imu/data (sensor_msgs/Imu, ~100 Hz da OAK-D)
- Input B: /odom (nav_msgs/Odometry, encoder ruote)
- Output:  TF odom→base_link, /odometry/filtered, /vio/pose

INTEGRAZIONE HEADING (IMU Gyro Primario):
  - L'integrazione del giroscopio Z dell'IMU traccia al 100% la rotazione fisica
    del robot nello spazio (sia ruotato a mano che dai motori).
  - Quando i motori/ruote girano, viene applicata una correzione lenta di drift (1%)
    verso l'odometria encoder. Se il robot viene ruotato a mano (encoder fermi),
    l'IMU guida interamente l'orientamento senza essere trascinata a 0.

Autore: Marcus AI Stack
Data: 2026
"""

import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from geometry_msgs.msg import PoseStamped, TransformStamped
from sensor_msgs.msg import Imu
from tf2_ros import TransformBroadcaster
import math
import threading
import time


class SpectacularVIONode(Node):
    def __init__(self):
        super().__init__('spectacular_vio_node')

        # --- Parametri ---
        self.declare_parameter('publish_rate_hz', 30.0)
        self.declare_parameter('odom_frame', 'odom')
        self.declare_parameter('base_link_frame', 'base_link')
        self.declare_parameter('imu_frame', 'imu_link')
        self.declare_parameter('use_encoder_fallback', True)
        self.declare_parameter('imu_timeout_sec', 2.0)

        self.publish_rate = self.get_parameter('publish_rate_hz').value
        self.odom_frame = self.get_parameter('odom_frame').value
        self.base_frame = self.get_parameter('base_link_frame').value
        self.imu_frame = self.get_parameter('imu_frame').value
        self.use_fallback = self.get_parameter('use_encoder_fallback').value
        self.imu_timeout = self.get_parameter('imu_timeout_sec').value

        # --- Publishers e TF ---
        self.tf_broadcaster = TransformBroadcaster(self)
        self.odom_pub = self.create_publisher(Odometry, '/odometry/filtered', 10)
        self.pose_pub = self.create_publisher(PoseStamped, '/vio/pose', 10)

        # --- Subscribers ---
        self.imu_sub = self.create_subscription(Imu, '/oak/imu/data', self._imu_cb, 10)
        self.encoder_sub = self.create_subscription(Odometry, '/odom', self._encoder_odom_cb, 10)

        # --- Stato interno ---
        self._lock = threading.Lock()

        # Pose & Heading
        self._x = 0.0
        self._y = 0.0
        self._theta = 0.0           # Heading integrato fuso (radianti)
        self._encoder_theta = 0.0   # Heading grezzo encoder

        # Velocità
        self._vx = 0.0
        self._wz = 0.0

        # IMU Tracking
        self._last_imu_time = None
        self._imu_valid = False

        # Gyro bias calibration (primi 150 campioni ~1.5s @ 100Hz)
        self._gyro_bias_z = 0.0
        self._bias_samples = []
        self._bias_calibrated = False
        self._BIAS_CAL_N = 150

        # Encoder tracking
        self._encoder_valid = False
        self._last_encoder_x = 0.0
        self._last_encoder_y = 0.0

        # Diagnostics
        self._last_log_time = time.time()

        # Timer TF @ 30 Hz
        self.create_timer(1.0 / self.publish_rate, self._publish_tf)

        self.get_logger().info(
            f'✅ SpectacularVIO Node avviato (IMU Gyro Primario @ {self.publish_rate:.0f}Hz)\n'
            f'   IMU: /oak/imu/data | Encoder: /odom | Calibrazione bias: {self._BIAS_CAL_N} campioni')

    def _imu_cb(self, msg: Imu):
        gz_raw = msg.angular_velocity.z

        with self._lock:
            now = time.time()

            # Calibrazione bias iniziale
            if not self._bias_calibrated:
                self._bias_samples.append(gz_raw)
                if len(self._bias_samples) >= self._BIAS_CAL_N:
                    self._gyro_bias_z = sum(self._bias_samples) / len(self._bias_samples)
                    self._bias_calibrated = True
                    self._last_imu_time = now
                    self.get_logger().info(
                        f'🎯 Gyro bias Z calibrato: {self._gyro_bias_z:.6f} rad/s '
                        f'(su {self._BIAS_CAL_N} campioni)')
                return

            gz_corrected = gz_raw - self._gyro_bias_z

            # Noise gate per micro-vibrazioni (< 0.003 rad/s ~ 0.17 deg/s)
            if abs(gz_corrected) < 0.003:
                gz_corrected = 0.0

            if self._last_imu_time is not None:
                dt = now - self._last_imu_time
                if 0.0 < dt < 0.1:
                    # Integrazione pura della velocità angolare IMU Z
                    self._theta += gz_corrected * dt

                    # Se le ruote si muovono attivamente (velocità encoder > 0),
                    # applica correzione lenta (1% per sec) verso l'encoder per prevenire drift
                    if self._encoder_valid and abs(self._vx) > 0.01:
                        d_err = math.atan2(
                            math.sin(self._encoder_theta - self._theta),
                            math.cos(self._encoder_theta - self._theta)
                        )
                        self._theta += 0.01 * d_err * dt

                    # Normalizza heading in [-pi, pi]
                    self._theta = math.atan2(math.sin(self._theta), math.cos(self._theta))

            self._wz = gz_corrected
            self._last_imu_time = now
            self._imu_valid = True

    def _encoder_odom_cb(self, msg: Odometry):
        with self._lock:
            self._x = msg.pose.pose.position.x
            self._y = msg.pose.pose.position.y
            self._vx = msg.twist.twist.linear.x

            q = msg.pose.pose.orientation
            self._encoder_theta = math.atan2(
                2.0 * (q.w * q.z + q.x * q.y),
                1.0 - 2.0 * (q.y * q.y + q.z * q.z)
            )

            self._encoder_valid = True

            # Se IMU non ancora valida o persa, usa l'orientamento degli encoder
            if not self._imu_valid or not self._bias_calibrated:
                self._theta = self._encoder_theta
                self._wz = msg.twist.twist.angular.z

    def _publish_tf(self):
        with self._lock:
            imu_active = False
            if self._imu_valid and self._last_imu_time is not None:
                imu_age = time.time() - self._last_imu_time
                imu_active = (imu_age < self.imu_timeout)

            theta = self._theta
            x = self._x
            y = self._y
            vx = self._vx
            wz = self._wz
            mode = "VIO-Gyro" if (imu_active and self._bias_calibrated) else "Encoder-Fallback"

        if not self._encoder_valid and not self._imu_valid:
            self._publish_identity_tf()
            return

        now = self.get_clock().now()

        qz = math.sin(theta / 2.0)
        qw = math.cos(theta / 2.0)

        # --- TF odom → base_link ---
        tf_msg = TransformStamped()
        tf_msg.header.stamp = now.to_msg()
        tf_msg.header.frame_id = self.odom_frame
        tf_msg.child_frame_id = self.base_frame
        tf_msg.transform.translation.x = x
        tf_msg.transform.translation.y = y
        tf_msg.transform.translation.z = 0.0
        tf_msg.transform.rotation.x = 0.0
        tf_msg.transform.rotation.y = 0.0
        tf_msg.transform.rotation.z = qz
        tf_msg.transform.rotation.w = qw
        self.tf_broadcaster.sendTransform(tf_msg)

        # --- /odometry/filtered ---
        odom_msg = Odometry()
        odom_msg.header.stamp = now.to_msg()
        odom_msg.header.frame_id = self.odom_frame
        odom_msg.child_frame_id = self.base_frame
        odom_msg.pose.pose.position.x = x
        odom_msg.pose.pose.position.y = y
        odom_msg.pose.pose.position.z = 0.0
        odom_msg.pose.pose.orientation.x = 0.0
        odom_msg.pose.pose.orientation.y = 0.0
        odom_msg.pose.pose.orientation.z = qz
        odom_msg.pose.pose.orientation.w = qw

        odom_msg.pose.covariance[0] = 0.01
        odom_msg.pose.covariance[7] = 0.01
        odom_msg.pose.covariance[35] = 0.001 if mode == "VIO-Gyro" else 0.01

        odom_msg.twist.twist.linear.x = vx
        odom_msg.twist.twist.angular.z = wz
        odom_msg.twist.covariance[0] = 0.01
        odom_msg.twist.covariance[35] = 0.001 if mode == "VIO-Gyro" else 0.01

        self.odom_pub.publish(odom_msg)

        # --- /vio/pose ---
        pose_msg = PoseStamped()
        pose_msg.header.stamp = now.to_msg()
        pose_msg.header.frame_id = self.odom_frame
        pose_msg.pose = odom_msg.pose.pose
        self.pose_pub.publish(pose_msg)

        if time.time() - self._last_log_time > 10.0:
            self.get_logger().info(
                f'📍 [{mode}] x={x:.3f}m y={y:.3f}m θ={math.degrees(theta):.1f}° '
                f'wz={math.degrees(wz):.1f}°/s | bias_ok={self._bias_calibrated}')
            self._last_log_time = time.time()

    def _publish_identity_tf(self):
        now = self.get_clock().now()
        tf_msg = TransformStamped()
        tf_msg.header.stamp = now.to_msg()
        tf_msg.header.frame_id = self.odom_frame
        tf_msg.child_frame_id = self.base_frame
        tf_msg.transform.rotation.w = 1.0
        self.tf_broadcaster.sendTransform(tf_msg)


def main(args=None):
    rclpy.init(args=args)
    node = SpectacularVIONode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
