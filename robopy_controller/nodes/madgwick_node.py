#!/usr/bin/env python3
# madgwick_node.py
# Madgwick AHRS for OAK-D Lite (DepthAI IMU) – ROS2 compliant

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu
from std_srvs.srv import Trigger
import numpy as np
import math

# ------------------ Madgwick Filter ------------------

class MadgwickAHRS:
    def __init__(self, beta=0.04):
        self.beta = beta
        self.dt = 0.01
        self.q = np.array([1.0, 0.0, 0.0, 0.0], dtype=float)  # w x y z

    def update(self, gx, gy, gz, ax, ay, az, dt):
        self.dt = dt

        q1, q2, q3, q4 = self.q

        norm = math.sqrt(ax*ax + ay*ay + az*az)
        if norm < 1e-6:
            return self.q
        ax /= norm; ay /= norm; az /= norm

        f1 = 2*(q2*q4 - q1*q3) - ax
        f2 = 2*(q1*q2 + q3*q4) - ay
        f3 = 2*(0.5 - q2*q2 - q3*q3) - az

        s1 = -2*q3*f1 + 2*q2*f2
        s2 =  2*q4*f1 + 2*q1*f2 - 4*q2*f3
        s3 = -2*q1*f1 + 2*q4*f2 - 4*q3*f3
        s4 =  2*q2*f1 + 2*q3*f2

        norm_s = math.sqrt(s1*s1 + s2*s2 + s3*s3 + s4*s4)
        if norm_s > 1e-6:
            s1/=norm_s; s2/=norm_s; s3/=norm_s; s4/=norm_s

        q1 += (-0.5*(q2*gx + q3*gy + q4*gz) - self.beta*s1) * self.dt
        q2 += ( 0.5*(q1*gx + q3*gz - q4*gy) - self.beta*s2) * self.dt
        q3 += ( 0.5*(q1*gy - q2*gz + q4*gx) - self.beta*s3) * self.dt
        q4 += ( 0.5*(q1*gz + q2*gy - q3*gx) - self.beta*s4) * self.dt

        self.q = self.q / np.linalg.norm(self.q)
        return self.q

# ------------------ Node ------------------

class MadgwickNode(Node):
    def __init__(self):
        super().__init__("madgwick_filter")

        self.declare_parameter("input_topic", "/oak/imu/data")
        self.declare_parameter("output_topic", "/imu/data")
        self.declare_parameter("frame_id", "imu_link")
        self.declare_parameter("beta", 0.04)
        self.declare_parameter("rate", 200.0)
        self.declare_parameter("calibration_samples", 100)

        self.frame_id = self.get_parameter("frame_id").value

        self.filter = MadgwickAHRS(
            beta=self.get_parameter("beta").value
        )

        self.sub = self.create_subscription(
            Imu,
            self.get_parameter("input_topic").value,
            self.cb,
            50
        )

        self.pub = self.create_publisher(
            Imu,
            self.get_parameter("output_topic").value,
            10
        )

        # ---- nuovo publisher IMU lineare (robot_localization) ----
        self.pub_linear = self.create_publisher(
            Imu,
            "/imu/linear",
            10
        )

        self.last_time = None

        # -------- bias calibration --------
        self.calib_samples = self.get_parameter("calibration_samples").value
        self.calib_count = 0
        self.acc_bias = np.zeros(3)
        self.gyr_bias = np.zeros(3)
        self.calibrated = False

        self.get_logger().info("Madgwick IMU filter started")

    def cb(self, msg: Imu):
        # -------- DepthAI -> ROS axis remap --------
        # DepthAI: X right, Y forward, Z up
        # ROS:     X forward, Y left, Z up



        # --- Rimappatura assi OAK-D -> ROS REP-103 ---
        # NOTA: la rimappatura è GIÀ stata fatta nel publisher
        ax = float(msg.linear_acceleration.x)
        ay = float(msg.linear_acceleration.y)
        az = float(msg.linear_acceleration.z)

        gx = float(msg.angular_velocity.x)
        gy = float(msg.angular_velocity.y)
        gz = float(msg.angular_velocity.z)



        # -------- bias calibration automatica --------
        if not self.calibrated:
            self.acc_bias += np.array([ax, ay, az])
            self.gyr_bias += np.array([gx, gy, gz])
            self.calib_count += 1

            if self.calib_count >= self.calib_samples:
                self.acc_bias /= self.calib_samples
                self.gyr_bias /= self.calib_samples
                self.calibrated = True
                self.get_logger().info("IMU bias calibration completed")

            return

        ax -= self.acc_bias[0]
        ay -= self.acc_bias[1]
        az -= self.acc_bias[2]

        gx -= self.gyr_bias[0]
        gy -= self.gyr_bias[1]
        gz -= self.gyr_bias[2]



        # -------- dt dinamico dal timestamp --------
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if self.last_time is None:
            self.last_time = t
            return

        dt = t - self.last_time
        self.last_time = t

        if dt <= 0.0 or dt > 0.1:
            return



        q = self.filter.update(gx, gy, gz, ax, ay, az, dt)

        # -------- rimozione gravità --------
        g = 9.81
        gx_g = 2*(q[1]*q[3] - q[0]*q[2]) * g
        gy_g = 2*(q[0]*q[1] + q[2]*q[3]) * g
        gz_g = (q[0]*q[0] - q[1]*q[1] - q[2]*q[2] + q[3]*q[3]) * g

        ax_lin = ax - gx_g
        ay_lin = ay - gy_g
        az_lin = az - gz_g



        out = Imu()
        out.header.stamp = msg.header.stamp
        out.header.frame_id = self.frame_id

        out.orientation.w = q[0]
        out.orientation.x = q[1]
        out.orientation.y = q[2]
        out.orientation.z = q[3]

        out.angular_velocity.x = gx
        out.angular_velocity.y = gy
        out.angular_velocity.z = gz

        out.linear_acceleration.x = ax
        out.linear_acceleration.y = ay
        out.linear_acceleration.z = az

        # Roll/Pitch accurate from gravity, Yaw unreliable (no magnetometer)
        out.orientation_covariance = [
            0.02, 0, 0,
            0, 0.02, 0,
            0, 0, 5.0  # VERY HIGH variance for Yaw = minimal influence, drift correction only
        ]
        out.angular_velocity_covariance = [1e-3]*9
        out.linear_acceleration_covariance = [1e-2]*9

        self.pub.publish(out)



        # -------- IMU linear (robot_localization ready) --------
        lin = Imu()
        lin.header = out.header

        # Quaternion valido ma ignorato
        lin.orientation.w = 1.0
        lin.orientation.x = 0.0
        lin.orientation.y = 0.0
        lin.orientation.z = 0.0
        lin.orientation_covariance = [-1.0]*9

        # Angular velocity non usata
        lin.angular_velocity.x = 0.0
        lin.angular_velocity.y = 0.0
        lin.angular_velocity.z = 0.0
        lin.angular_velocity_covariance = [-1.0]*9


        lin.linear_acceleration.x = ax_lin
        lin.linear_acceleration.y = ay_lin
        lin.linear_acceleration.z = az_lin
        lin.linear_acceleration_covariance = out.linear_acceleration_covariance

        self.pub_linear.publish(lin)

def main():
    rclpy.init()
    node = MadgwickNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()
