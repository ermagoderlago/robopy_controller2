#!/usr/bin/env python3
"""
spectacular_vio_node.py — VIO Node per Marcus (OAK-D Lite)
============================================================
Implementa Visual-Inertial Odometry (VIO) per Marcus usando:
- IMU BMI270 OAK-D Lite letta via topic ROS2 `/oak/imu/data`
  (il driver `oak_superpoint_odometry_cpp` tiene aperta la pipeline
   DepthAI — non possiamo aprirne una seconda in parallelo)
- Odometria encoder ruote `/odom` (waveshare_motor_driver)

ARCHITETTURA:
- Autorità TF: odom → base_link  (30 Hz, unica autorità)
- Input A: /oak/imu/data (sensor_msgs/Imu, ~50 Hz da oak_superpoint_odometry_cpp)
- Input B: /odom (nav_msgs/Odometry, encoder ruote)
- Output:  TF odom→base_link, /odometry/filtered, /vio/pose
- Failsafe: encoder-only se IMU non disponibile

FUSIONE HEADING (ibrida Gyro + Encoder):
  θ_fusa = w_gyro * θ_gyro_integrato + (1-w_gyro) * θ_encoder
  dove w_gyro = 0.85 (default)

POSIZIONE:
  x, y: sempre da encoder (più stabile per traslazione breve)

Autore: Marcus AI Stack
Data: 2026
"""

import rclpy
from rclpy.node import Node
from rclpy.time import Time
from nav_msgs.msg import Odometry
from geometry_msgs.msg import PoseStamped, TransformStamped
from sensor_msgs.msg import Imu
from tf2_ros import TransformBroadcaster
import math
import threading
import time


class SpectacularVIONode(Node):
    """
    Nodo VIO per Marcus.

    Fusione Gyro (OAK-D BMI270 via ROS2 topic) + Encoder Ruote.
    Pubblica TF odom→base_link a 30 Hz come unica autorità.
    """

    def __init__(self):
        super().__init__('spectacular_vio_node')

        # --- Parametri ---
        self.declare_parameter('publish_rate_hz', 30.0)
        self.declare_parameter('odom_frame', 'odom')
        self.declare_parameter('base_link_frame', 'base_link')
        self.declare_parameter('imu_frame', 'imu_link')
        self.declare_parameter('use_encoder_fallback', True)
        self.declare_parameter('imu_gyro_weight', 0.85)
        self.declare_parameter('imu_timeout_sec', 2.0)   # switch a fallback dopo N sec senza IMU

        self.publish_rate = self.get_parameter('publish_rate_hz').value
        self.odom_frame = self.get_parameter('odom_frame').value
        self.base_frame = self.get_parameter('base_link_frame').value
        self.imu_frame = self.get_parameter('imu_frame').value
        self.use_fallback = self.get_parameter('use_encoder_fallback').value
        self.gyro_weight = self.get_parameter('imu_gyro_weight').value
        self.imu_timeout = self.get_parameter('imu_timeout_sec').value

        # --- Publishers e TF ---
        self.tf_broadcaster = TransformBroadcaster(self)
        self.odom_pub = self.create_publisher(Odometry, '/odometry/filtered', 10)
        self.pose_pub = self.create_publisher(PoseStamped, '/vio/pose', 10)

        # --- Subscribers ---
        self.imu_sub = self.create_subscription(
            Imu, '/oak/imu/data', self._imu_cb, 10)
        self.encoder_sub = self.create_subscription(
            Odometry, '/odom', self._encoder_odom_cb, 10)

        # --- Stato interno ---
        self._lock = threading.Lock()

        # Stato pose fusa
        self._x = 0.0
        self._y = 0.0
        self._theta_gyro = 0.0      # heading integrato dal giroscopio
        self._theta_encoder = 0.0   # heading dagli encoder
        self._theta_fused = 0.0     # heading fuso finale

        # Velocità
        self._vx = 0.0
        self._wz = 0.0

        # IMU
        self._last_imu_time = None
        self._imu_valid = False
        self._imu_sample_count = 0

        # Gyro bias calibration (primi 150 campioni = ~3s @ 50Hz)
        self._gyro_bias_z = 0.0
        self._bias_samples = []
        self._bias_calibrated = False
        self._BIAS_CAL_N = 150

        # Encoder
        self._encoder_valid = False

        # Diagnostics
        self._last_log_time = time.time()

        # --- Timer TF 30 Hz ---
        self.create_timer(1.0 / self.publish_rate, self._publish_tf)

        self.get_logger().info(
            f'✅ SpectacularVIO Node avviato (Gyro+Encoder fusion @ {self.publish_rate:.0f}Hz)\n'
            f'   IMU: /oak/imu/data | Encoder: /odom\n'
            f'   Gyro weight: {self.gyro_weight:.0%} | Calibrazione bias: {self._BIAS_CAL_N} campioni')

    # =========================================================
    # IMU CALLBACK (da /oak/imu/data, pubblicato da oak_superpoint)
    # =========================================================
    def _imu_cb(self, msg: Imu):
        """
        Processa dati IMU OAK-D BMI270 (GYROSCOPE_RAW):
        - Integra giroscopio Z per stimare heading
        - Calibra bias automaticamente sui primi campioni
        """
        gz_raw = msg.angular_velocity.z

        with self._lock:
            now = time.time()

            # === FASE 1: Calibrazione bias ===
            if not self._bias_calibrated:
                self._bias_samples.append(gz_raw)
                if len(self._bias_samples) >= self._BIAS_CAL_N:
                    self._gyro_bias_z = sum(self._bias_samples) / len(self._bias_samples)
                    self._bias_calibrated = True
                    self.get_logger().info(
                        f'🎯 Gyro bias calibrato: {self._gyro_bias_z:.6f} rad/s '
                        f'(su {self._BIAS_CAL_N} campioni)')
                return  # non integrare durante calibrazione

            # === FASE 2: Integrazione heading ===
            gz_corrected = gz_raw - self._gyro_bias_z

            # Noise gate: ignora rumore sotto ~0.002 rad/s
            if abs(gz_corrected) < 0.002:
                gz_corrected = 0.0

            if self._last_imu_time is not None:
                dt = now - self._last_imu_time
                if 0.0 < dt < 0.1:  # solo step ragionevoli (< 100ms)
                    self._theta_gyro += gz_corrected * dt
                    # Normalizza in [-pi, pi]
                    self._theta_gyro = math.atan2(
                        math.sin(self._theta_gyro),
                        math.cos(self._theta_gyro))

            self._wz = gz_corrected
            self._last_imu_time = now
            self._imu_valid = True
            self._imu_sample_count += 1

    # =========================================================
    # ENCODER CALLBACK (da /odom)
    # =========================================================
    def _encoder_odom_cb(self, msg: Odometry):
        """
        Riceve odometria encoder (waveshare_motor_driver).
        - Posizione x,y: usata come fonte principale
        - Heading: usato come backup se IMU non disponibile
        - Velocità lineare: usata sempre
        """
        with self._lock:
            self._x = msg.pose.pose.position.x
            self._y = msg.pose.pose.position.y
            self._vx = msg.twist.twist.linear.x

            # Estrai yaw quaternion
            q = msg.pose.pose.orientation
            self._theta_encoder = math.atan2(
                2.0 * (q.w * q.z + q.x * q.y),
                1.0 - 2.0 * (q.y * q.y + q.z * q.z))

            self._encoder_valid = True

            # Se encoder fornisce wz e IMU non disponibile, usalo
            if not self._imu_valid:
                self._wz = msg.twist.twist.angular.z

    # =========================================================
    # PUBLISH TF (30 Hz timer)
    # =========================================================
    def _publish_tf(self):
        """
        Calcola heading fuso e pubblica:
        - TF odom → base_link
        - /odometry/filtered
        - /vio/pose
        """
        with self._lock:
            # Controllo timeout IMU
            imu_active = False
            if self._imu_valid and self._last_imu_time is not None:
                imu_age = time.time() - self._last_imu_time
                imu_active = (imu_age < self.imu_timeout)

            # Calcolo heading fuso
            if imu_active and self._bias_calibrated:
                # Fusione pesata: 85% gyro + 15% encoder (correzione drift lento)
                w = self.gyro_weight
                # Differenza angolare pesata (gestisce wrap-around)
                d_theta = math.atan2(
                    math.sin(self._theta_gyro - self._theta_encoder),
                    math.cos(self._theta_gyro - self._theta_encoder))
                self._theta_fused = self._theta_encoder + w * d_theta
                self._theta_fused = math.atan2(
                    math.sin(self._theta_fused),
                    math.cos(self._theta_fused))
                mode = "VIO"
            else:
                # Fallback encoder puro
                self._theta_fused = self._theta_encoder
                mode = "Encoder-Fallback"

            theta = self._theta_fused
            x = self._x
            y = self._y
            vx = self._vx
            wz = self._wz

        if not self._encoder_valid:
            # Pubblica identità se nessun dato
            self._publish_identity_tf()
            return

        now = self.get_clock().now()

        # Quaternion da yaw 2D
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

        # --- /odometry/filtered (per RTAB-Map) ---
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
        # Covarianze calibrate per Marcus
        odom_msg.pose.covariance[0] = 0.01    # σ²_x
        odom_msg.pose.covariance[7] = 0.01    # σ²_y
        odom_msg.pose.covariance[35] = 0.003 if mode == "VIO" else 0.01  # σ²_yaw
        odom_msg.twist.twist.linear.x = vx
        odom_msg.twist.twist.angular.z = wz
        odom_msg.twist.covariance[0] = 0.01
        odom_msg.twist.covariance[35] = 0.003 if mode == "VIO" else 0.01
        self.odom_pub.publish(odom_msg)

        # --- /vio/pose (Foxglove debug) ---
        pose_msg = PoseStamped()
        pose_msg.header.stamp = now.to_msg()
        pose_msg.header.frame_id = self.odom_frame
        pose_msg.pose = odom_msg.pose.pose
        self.pose_pub.publish(pose_msg)

        # Log periodico (ogni 10s)
        if time.time() - self._last_log_time > 10.0:
            self.get_logger().info(
                f'📍 [{mode}] x={x:.3f}m y={y:.3f}m θ={math.degrees(theta):.1f}° '
                f'vx={vx:.3f}m/s wz={wz:.4f}rad/s | '
                f'IMU_active={imu_active} bias_ok={self._bias_calibrated}')
            self._last_log_time = time.time()

    def _publish_identity_tf(self):
        """Pubblica TF identità in assenza di dati encoder."""
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
