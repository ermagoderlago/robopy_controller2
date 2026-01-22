#!/usr/bin/env python3
"""imu_bridge_node.py — IMU bridge con QoS separati per source/sub/pub.

- source_qos_reliability: QoS usato per le SUBS alla sorgente (es. /oak/imu/data)
- pub_qos_reliability:    QoS usato per i PUB verso il sistema (es. /imu/data)
"""
from __future__ import annotations
import math
from typing import Sequence

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy, QoSDurabilityPolicy

from sensor_msgs.msg import Imu, MagneticField
from std_msgs.msg import Header
from geometry_msgs.msg import TransformStamped
import tf2_ros

# Fallback pure-Python per quaternion/euler (no dipendenze esterne)
class _TF_PURE_PY:
    @staticmethod
    def euler_from_quaternion(q: Sequence[float]):
        if q is None:
            return (0.0, 0.0, 0.0)
        try:
            x, y, z, w = q
        except Exception:
            x, y, z, w = q.x, q.y, q.z, q.w
        sinr_cosp = 2.0 * (w * x + y * z)
        cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
        roll = math.atan2(sinr_cosp, cosr_cosp)
        sinp = 2.0 * (w * y - z * x)
        if abs(sinp) >= 1:
            pitch = math.copysign(math.pi / 2.0, sinp)
        else:
            pitch = math.asin(sinp)
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        yaw = math.atan2(siny_cosp, cosy_cosp)
        return (roll, pitch, yaw)

    @staticmethod
    def quaternion_from_euler(roll: float, pitch: float, yaw: float):
        cy = math.cos(yaw * 0.5)
        sy = math.sin(yaw * 0.5)
        cp = math.cos(pitch * 0.5)
        sp = math.sin(pitch * 0.5)
        cr = math.cos(roll * 0.5)
        sr = math.sin(roll * 0.5)
        w = cr * cp * cy + sr * sp * sy
        x = sr * cp * cy - cr * sp * sy
        y = cr * sp * cy + sr * cp * sy
        z = cr * cp * sy - sr * sp * cy
        return (x, y, z, w)

# prova import tf_transformations vero, altrimenti usa fallback
try:
    import tf_transformations  # type: ignore
except Exception:
    tf_transformations = _TF_PURE_PY()

def _qos_profile(reliability: str = 'best_effort', depth: int = 30) -> QoSProfile:
    rel = QoSReliabilityPolicy.BEST_EFFORT if reliability == 'best_effort' else QoSReliabilityPolicy.RELIABLE
    return QoSProfile(
        reliability=rel,
        history=QoSHistoryPolicy.KEEP_LAST,
        depth=depth,
        durability=QoSDurabilityPolicy.VOLATILE
    )

class ImuBridge(Node):
    def __init__(self):
        super().__init__('imu_bridge_node')

        # Parametri principali
        self.declare_parameter('source_imu_topic', '/oak/imu/data')
        self.declare_parameter('source_mag_topic', '/oak/imu/mag')
        self.declare_parameter('publish_imu_topic', '/imu/data')
        self.declare_parameter('publish_mag_topic', '/imu/mag')
        self.declare_parameter('frame_id', 'imu_link')
        self.declare_parameter('base_frame', 'base_link')
        self.declare_parameter('publish_tf', True)
        self.declare_parameter('use_orientation', True)

        # covarianze (tutti float)
        self.declare_parameter('linear_accel_covariance', [0.1,0.0,0.0,0.0,0.1,0.0,0.0,0.0,0.1])
        self.declare_parameter('angular_vel_covariance', [0.05,0.0,0.0,0.0,0.05,0.0,0.0,0.0,0.05])
        self.declare_parameter('orientation_covariance_unknown', True)

        # QoS params separati
        self.declare_parameter('source_qos_reliability', 'best_effort')   # per SUB alla sorgente
        self.declare_parameter('pub_qos_reliability', 'reliable')        # per PUB verso il mondo
        self.declare_parameter('source_qos_depth', 10)
        self.declare_parameter('pub_qos_depth', 30)

        # Leggi parametri
        self.source_imu_topic = self.get_parameter('source_imu_topic').get_parameter_value().string_value
        self.source_mag_topic = self.get_parameter('source_mag_topic').get_parameter_value().string_value
        self.publish_imu_topic = self.get_parameter('publish_imu_topic').get_parameter_value().string_value
        self.publish_mag_topic = self.get_parameter('publish_mag_topic').get_parameter_value().string_value
        self.frame_id = self.get_parameter('frame_id').get_parameter_value().string_value
        self.base_frame = self.get_parameter('base_frame').get_parameter_value().string_value
        self.publish_tf = self.get_parameter('publish_tf').get_parameter_value().bool_value
        self.use_orientation = self.get_parameter('use_orientation').get_parameter_value().bool_value

        self.linear_cov = list(self.get_parameter('linear_accel_covariance').get_parameter_value().double_array_value)
        self.angular_cov = list(self.get_parameter('angular_vel_covariance').get_parameter_value().double_array_value)
        self.orientation_unknown = self.get_parameter('orientation_covariance_unknown').get_parameter_value().bool_value

        source_q_rel = self.get_parameter('source_qos_reliability').get_parameter_value().string_value
        pub_q_rel = self.get_parameter('pub_qos_reliability').get_parameter_value().string_value
        source_q_depth = int(self.get_parameter('source_qos_depth').get_parameter_value().integer_value)
        pub_q_depth = int(self.get_parameter('pub_qos_depth').get_parameter_value().integer_value)

        # crea QoS separati
        self.qos_sub = _qos_profile(source_q_rel, source_q_depth)
        self.qos_pub = _qos_profile(pub_q_rel, pub_q_depth)

        # Publishers (usano qos_pub)
        self.imu_pub = self.create_publisher(Imu, self.publish_imu_topic, self.qos_pub)
        self.mag_pub = self.create_publisher(MagneticField, self.publish_mag_topic, self.qos_pub)

        # Subscribers (usano qos_sub)
        self.create_subscription(Imu, self.source_imu_topic, self._imu_callback, self.qos_sub)
        self.create_subscription(MagneticField, self.source_mag_topic, self._mag_callback, self.qos_sub)

        # TF broadcaster
        self.tf_broadcaster = tf2_ros.TransformBroadcaster(self) if self.publish_tf else None

        self.get_logger().info(f"IMU bridge started: {self.source_imu_topic} -> {self.publish_imu_topic} (frame: {self.frame_id})")
        self.get_logger().info(f"QoS source: {source_q_rel}/{source_q_depth}, QoS pub: {pub_q_rel}/{pub_q_depth}")

    def _imu_callback(self, msg: Imu):
        out = Imu()
        out.header = Header()
        # copia header ma se è 0 lo sovrascriviamo con now()
        if hasattr(msg, 'header') and getattr(msg.header, 'stamp', None) is not None:
            out.header.stamp = msg.header.stamp
        else:
            out.header.stamp = self.get_clock().now().to_msg()
        out.header.frame_id = self.frame_id

        # orientation handling
        has_orientation = any(v != 0 for v in msg.orientation_covariance)
        if self.use_orientation and has_orientation:
            out.orientation = msg.orientation
            out.orientation_covariance = msg.orientation_covariance
        else:
            out.orientation_covariance = [-1.0] * 9 if self.orientation_unknown else [0.0] * 9

        out.angular_velocity = msg.angular_velocity
        out.angular_velocity_covariance = list(self.angular_cov)
        out.linear_acceleration = msg.linear_acceleration
        out.linear_acceleration_covariance = list(self.linear_cov)

        try:
            self.imu_pub.publish(out)
        except Exception as e:
            self.get_logger().error(f"Errore publish imu: {e}")

        # TF (identità - modifica se hai extrinsics)
        if self.tf_broadcaster:
            t = TransformStamped()
            t.header.stamp = out.header.stamp
            t.header.frame_id = self.base_frame
            t.child_frame_id = self.frame_id
            t.transform.translation.x = 0.0
            t.transform.translation.y = 0.0
            t.transform.translation.z = 0.0
            t.transform.rotation.x = 0.0
            t.transform.rotation.y = 0.0
            t.transform.rotation.z = 0.0
            t.transform.rotation.w = 1.0
            try:
                self.tf_broadcaster.sendTransform(t)
            except Exception as e:
                self.get_logger().warning(f"TF broadcast failed: {e}")

    def _mag_callback(self, msg: MagneticField):
        out = MagneticField()
        out.header = Header()
        out.header.stamp = msg.header.stamp if hasattr(msg, 'header') else self.get_clock().now().to_msg()
        out.header.frame_id = self.frame_id
        out.magnetic_field = msg.magnetic_field
        try:
            self.mag_pub.publish(out)
        except Exception as e:
            self.get_logger().error(f"Errore publish mag: {e}")

def main(args=None):
    rclpy.init(args=args)
    node = ImuBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
