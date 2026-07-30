#!/usr/bin/env python3
"""
extrinsic_camera_calibrator.py - Continuous Extrinsic Auto-Calibration & Camera Sag Self-Healing

Mitigates FM-VIS-003:
- Estimates floor plane normal in base_link frame using RANSAC / SVD on camera depth points.
- Computes pitch sag angle (mechanical tilt error).
- Publishes ROS 2 diagnostics (/diagnostics) and system health alerts (/robot/health_status) -> Awareness.
- Publishes pitch correction to dynamic_camera_tf_node and triggers ghost costmap clearing -> Self-Healing.
"""

import math
import time
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import Float32, String, Bool
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue


class ExtrinsicCameraCalibrator(Node):
    def __init__(self):
        super().__init__('extrinsic_camera_calibrator')

        # Parameters
        self.declare_parameter('depth_topic', '/camera/depth/image_raw')
        self.declare_parameter('camera_info_topic', '/camera/camera_info')
        self.declare_parameter('sag_warn_thresh_deg', 1.5)
        self.declare_parameter('sag_error_thresh_deg', 10.0)
        self.declare_parameter('cam_height_nominal', 0.15)
        self.declare_parameter('cam_pitch_nominal', -0.5236)  # -30 deg
        self.declare_parameter('calib_interval_sec', 1.0)

        self.depth_topic = self.get_parameter('depth_topic').value
        self.camera_info_topic = self.get_parameter('camera_info_topic').value
        self.warn_thresh_deg = self.get_parameter('sag_warn_thresh_deg').value
        self.error_thresh_deg = self.get_parameter('sag_error_thresh_deg').value
        self.cam_z = self.get_parameter('cam_height_nominal').value
        self.cam_pitch = self.get_parameter('cam_pitch_nominal').value
        self.calib_interval = self.get_parameter('calib_interval_sec').value

        self.fx = 500.0
        self.fy = 500.0
        self.cx = 320.0
        self.cy = 240.0
        self.has_camera_info = False

        self.latest_depth = None
        self.last_calib_time = time.time()
        self.accumulated_pitch_error = 0.0

        # QoS
        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5
        )
        reliable_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        # Subscriptions
        self.create_subscription(CameraInfo, self.camera_info_topic, self.info_cb, reliable_qos)
        self.create_subscription(Image, self.depth_topic, self.depth_cb, sensor_qos)

        # Publishers
        self.pub_diag = self.create_publisher(DiagnosticArray, '/diagnostics', reliable_qos)
        self.pub_health = self.create_publisher(String, '/robot/health_status', reliable_qos)
        self.pub_pitch_corr = self.create_publisher(Float32, '/camera/extrinsic_pitch_correction', reliable_qos)
        self.pub_clear_costmap = self.create_publisher(Bool, '/semantic_costmap/clear', reliable_qos)

        # Processing loop at 1 Hz
        self.timer = self.create_timer(self.calib_interval, self.calibration_loop)

        self.get_logger().info('ExtrinsicCameraCalibrator node initialized.')

    def info_cb(self, msg: CameraInfo):
        if not self.has_camera_info:
            if msg.k[0] > 0:
                self.fx = msg.k[0]
                self.fy = msg.k[4]
                self.cx = msg.k[2]
                self.cy = msg.k[5]
                self.has_camera_info = True
                self.get_logger().info(f'Camera intrinsics loaded: fx={self.fx:.1f}, fy={self.fy:.1f}, cx={self.cx:.1f}, cy={self.cy:.1f}')

    def depth_cb(self, msg: Image):
        try:
            # Convert ROS Image to numpy array
            if msg.encoding in ['16UC1', 'mono16']:
                depth_data = np.frombuffer(msg.data, dtype=np.uint16).reshape((msg.height, msg.width))
                self.latest_depth = depth_data.astype(np.float32) / 1000.0  # Convert mm to meters
            elif msg.encoding == '32FC1':
                self.latest_depth = np.frombuffer(msg.data, dtype=np.float32).reshape((msg.height, msg.width))
        except Exception as e:
            self.get_logger().warn(f'Failed to process depth image: {e}')

    def fit_ground_plane_ransac(self, points_3d: np.ndarray, max_iterations=50, distance_threshold=0.03):
        """Fits plane Ax + By + Cz + D = 0 to 3D points using RANSAC."""
        if len(points_3d) < 10:
            return None

        best_inliers = 0
        best_plane = None

        n_points = len(points_3d)
        for _ in range(max_iterations):
            sample_idx = np.random.choice(n_points, 3, replace=False)
            p1, p2, p3 = points_3d[sample_idx]

            # Normal vector via cross product
            v1 = p2 - p1
            v2 = p3 - p1
            normal = np.cross(v1, v2)
            norm_val = np.linalg.norm(normal)

            if norm_val < 1e-6:
                continue

            normal = normal / norm_val
            d = -np.dot(normal, p1)

            # Distances to plane
            distances = np.abs(np.dot(points_3d, normal) + d)
            inliers = np.sum(distances < distance_threshold)

            if inliers > best_inliers:
                best_inliers = inliers
                best_plane = (normal, d)

        if best_plane is not None and best_inliers > 10:
            normal, d = best_plane
            # Ensure normal points upwards (positive Z in base_link)
            if normal[2] < 0:
                normal = -normal
                d = -d
            return normal
        return None

    def calibration_loop(self):
        if self.latest_depth is None:
            return

        h, w = self.latest_depth.shape
        # Sample ground ROI (lower half of image)
        roi_depth = self.latest_depth[int(h * 0.5):int(h * 0.9), int(w * 0.2):int(w * 0.8)]
        
        # Subsample for lightweight RANSAC (< 5ms execution on Pi 5)
        step = 8
        u_grid, v_grid = np.meshgrid(
            np.arange(int(w * 0.2), int(w * 0.8), step),
            np.arange(int(h * 0.5), int(h * 0.9), step)
        )
        z_vals = self.latest_depth[v_grid, u_grid]

        # Valid depth mask (0.3m to 2.5m)
        valid = (z_vals > 0.3) & (z_vals < 2.5) & (~np.isnan(z_vals))
        if np.sum(valid) < 20:
            return

        u_valid = u_grid[valid]
        v_valid = v_grid[valid]
        z_valid = z_vals[valid]

        # Project 2D -> 3D camera optical frame (X-Right, Y-Down, Z-Forward)
        x_cam = (u_valid - self.cx) * z_valid / self.fx
        y_cam = (v_valid - self.cy) * z_valid / self.fy
        z_cam = z_valid

        # Transform to base_link (X-Forward, Y-Left, Z-Up) considering nominal pitch
        # Camera optical to camera_link: X_link = Z_cam, Y_link = -X_cam, Z_link = -Y_cam
        x_link = z_cam
        y_link = -x_cam
        z_link = -y_cam

        # Apply nominal camera pitch rotation around Y axis
        cp = math.cos(self.cam_pitch)
        sp = math.sin(self.cam_pitch)
        x_base = x_link * cp + z_link * sp
        y_base = y_link
        z_base = -x_link * sp + z_link * cp + self.cam_z

        points_base = np.column_stack((x_base, y_base, z_base))

        # Fit ground plane
        normal = self.fit_ground_plane_ransac(points_base)
        if normal is None:
            return

        # Normal vector in base_link should be [0, 0, 1].
        # Deviations: nx represents pitch error (sag), ny represents roll error
        pitch_error_rad = math.asin(np.clip(normal[0], -1.0, 1.0))
        pitch_error_deg = math.degrees(pitch_error_rad)
        roll_error_rad = math.atan2(normal[1], normal[2])
        roll_error_deg = math.degrees(roll_error_rad)

        abs_pitch_error = abs(pitch_error_deg)

        # 1. System Awareness (Diagnostics & Health)
        diag_arr = DiagnosticArray()
        diag_arr.header.stamp = self.get_clock().now().to_msg()
        status = DiagnosticStatus()
        status.name = 'OAK-D Extrinsic Alignment'
        status.hardware_id = 'OAK-D-Lite'

        health_msg = String()
        auto_corrected = False

        if abs_pitch_error > self.error_thresh_deg:
            status.level = DiagnosticStatus.ERROR
            status.message = f'CRITICAL Camera Mechanical Sag: {pitch_error_deg:+.1f} deg (Exceeds hardware limit)'
            health_msg.data = f'RED|Camera Hardware Sag Critical ({pitch_error_deg:+.1f} deg)'
            self.get_logger().error(status.message)
        elif abs_pitch_error >= self.warn_thresh_deg:
            status.level = DiagnosticStatus.WARN
            status.message = f'Camera Sag Detected: {pitch_error_deg:+.1f} deg. Applying Auto-Correction.'
            health_msg.data = f'YELLOW|Camera Sag Detected ({pitch_error_deg:+.1f} deg). Auto-compensating.'
            self.get_logger().warn(status.message)
            auto_corrected = True
        else:
            status.level = DiagnosticStatus.OK
            status.message = f'Extrinsic Camera Alignment Nominal (Sag: {pitch_error_deg:+.1f} deg)'
            health_msg.data = f'GREEN|Camera Extrinsic OK ({pitch_error_deg:+.1f} deg)'

        status.values = [
            KeyValue(key='pitch_sag_deg', value=f'{pitch_error_deg:.2f}'),
            KeyValue(key='roll_drift_deg', value=f'{roll_error_deg:.2f}'),
            KeyValue(key='warn_threshold_deg', value=f'{self.warn_thresh_deg:.1f}'),
            KeyValue(key='auto_corrected', value=str(auto_corrected))
        ]
        diag_arr.status.append(status)
        self.pub_diag.publish(diag_arr)
        self.pub_health.publish(health_msg)

        # 2. Proactive Self-Healing (Publish correction & flush costmap)
        if auto_corrected:
            # Correction offset to be added to nominal pitch
            correction_msg = Float32()
            # If camera sags down (pitch error > 0), we adjust pitch compensation
            correction_msg.data = float(-pitch_error_rad)
            self.pub_pitch_corr.publish(correction_msg)

            # Flush ghost costmap obstacles created by ground mistilt
            clear_msg = Bool()
            clear_msg.data = True
            self.pub_clear_costmap.publish(clear_msg)
            self.get_logger().info(f'Self-Healing: Sent TF pitch correction {-pitch_error_deg:.2f} deg and flushed costmap.')


def main(args=None):
    rclpy.init(args=args)
    node = ExtrinsicCameraCalibrator()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
