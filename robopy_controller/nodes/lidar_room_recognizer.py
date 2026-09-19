#!/usr/bin/env python3
"""
LiDAR Room Recognizer Node (RPLIDAR C1 360° ToF Pitch-Dark Localization)
=======================================================================
Processes 360° polar range scans in pitch darkness (lux = 0), computes
rotation-invariant 1D Fourier descriptors, calculates fine orientation via
circular Normalized Cross-Correlation (NCC), and matches against stored
geometric signatures in MAGRoomRegistry.

Conforms to:
  - TC5 / R3: Pitch-dark localization without visual aids
  - SPEC-02: RPLIDAR C1 /scan integration & 360° polar resampling
  - SPEC-05: MAGRoomRegistry LiDAR signature persistence & querying
"""

import os
import sys
import time
import json
import math
import threading
from typing import Dict, Any, List, Optional, Tuple, Sequence, Union

import numpy as np

# Safe ROS 2 import guard
try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
    from sensor_msgs.msg import LaserScan
    from std_msgs.msg import String
    from std_srvs.srv import Trigger
    HAS_ROS2 = True
except ImportError:
    HAS_ROS2 = False
    Node = object

# Safe TRINITY import
try:
    from robot_ai.trinity.mag_room_registry import MAGRoomRegistry
except ImportError:
    try:
        from robopy_controller.robot_ai.trinity.mag_room_registry import MAGRoomRegistry
    except ImportError:
        MAGRoomRegistry = None


def resample_scan_to_360_bins(
    ranges: Sequence[float],
    angle_min: float = 0.0,
    angle_increment: Optional[float] = None,
    range_min: float = 0.10,
    range_max: float = 8.0
) -> np.ndarray:
    """
    Converts arbitrary LaserScan into a clean, 360-bin 1-degree polar array [0..359].
    Replaces inf, nan, and out-of-bounds readings with range_max (8.0m).
    Uses periodic angle wrapping and linear interpolation.
    """
    num_rays = len(ranges)
    if num_rays == 0:
        return np.full(360, range_max, dtype=np.float32)

    clean_ranges = np.array(ranges, dtype=np.float32)
    # Filter nan, inf, and values outside [range_min, range_max]
    invalid_mask = (
        np.isnan(clean_ranges) |
        np.isinf(clean_ranges) |
        (clean_ranges < range_min) |
        (clean_ranges > range_max)
    )
    clean_ranges[invalid_mask] = range_max

    if angle_increment is None:
        angle_increment = (2.0 * math.pi) / max(num_rays, 1)

    # If already exactly 360 bins spanning 2*pi with zero min angle and no sorted interpolation needed
    if num_rays == 360 and abs(angle_increment - (2.0 * math.pi / 360.0)) < 1e-4 and abs(angle_min) < 1e-4:
        return np.clip(clean_ranges, range_min, range_max).astype(np.float32)

    # Calculate raw beam angles in range [0, 2*pi)
    raw_angles = (angle_min + np.arange(num_rays) * angle_increment) % (2.0 * math.pi)

    # Sort rays by angle
    sort_idx = np.argsort(raw_angles)
    sorted_angles = raw_angles[sort_idx]
    sorted_ranges = clean_ranges[sort_idx]

    # Target grid: 360 bins at 0, 1, ..., 359 degrees in radians
    target_angles = np.linspace(0, 2.0 * math.pi, 360, endpoint=False)

    # Periodic extension to avoid boundary wrap artifacts
    extended_angles = np.concatenate([
        sorted_angles - 2.0 * math.pi,
        sorted_angles,
        sorted_angles + 2.0 * math.pi
    ])
    extended_ranges = np.concatenate([
        sorted_ranges,
        sorted_ranges,
        sorted_ranges
    ])

    binned = np.interp(target_angles, extended_angles, extended_ranges)
    return np.clip(binned, range_min, range_max).astype(np.float32)


def compute_fourier_descriptor(polar_ranges: np.ndarray, num_harmonics: int = 32) -> np.ndarray:
    """
    Extracts rotation-invariant Fourier magnitude spectrum from 360-bin polar range scan.
    By circular shift theorem |F(e^{-j*phi*k} * R(k))| = |R(k)|, magnitude spectrum is
    strictly rotation-invariant.
    Returns L2-normalized magnitude vector of shape (num_harmonics,).
    """
    arr = np.asarray(polar_ranges, dtype=np.float32).reshape(-1)
    if len(arr) != 360:
        raise ValueError(f"Input must have exactly 360 bins, got {len(arr)}")

    # 360-point 1D real FFT (length: 181)
    fft_coeffs = np.fft.rfft(arr)
    magnitudes = np.abs(fft_coeffs[:num_harmonics]).astype(np.float32)

    # L2 normalize descriptor
    norm = float(np.linalg.norm(magnitudes))
    if norm > 1e-6:
        magnitudes = magnitudes / norm
    return magnitudes


def compute_circular_ncc(query_scan: np.ndarray, ref_scan: np.ndarray) -> Tuple[float, float]:
    """
    Performs circular normalized cross-correlation between query and reference 360-bin scans.
    Returns (max_correlation_score, best_yaw_offset_rad).
    """
    q_arr = np.asarray(query_scan, dtype=np.float32).reshape(-1)
    r_arr = np.asarray(ref_scan, dtype=np.float32).reshape(-1)

    q = q_arr - np.mean(q_arr)
    r = r_arr - np.mean(r_arr)
    norm_q = float(np.linalg.norm(q))
    norm_r = float(np.linalg.norm(r))

    if norm_q < 1e-5 or norm_r < 1e-5:
        return 0.0, 0.0

    # Circular correlation via FFT
    corr = np.fft.ifft(np.fft.fft(q) * np.conj(np.fft.fft(r))).real
    norm_factor = norm_q * norm_r
    ncc_curve = corr / norm_factor

    best_shift = int(np.argmax(ncc_curve))
    best_score = float(ncc_curve[best_shift])
    yaw_offset_rad = float(best_shift * (2.0 * math.pi / 360.0))
    # Normalize yaw offset to [-pi, pi]
    if yaw_offset_rad > math.pi:
        yaw_offset_rad -= 2.0 * math.pi

    return best_score, yaw_offset_rad


class LidarRoomRecognizerEngine:
    """
    Algorithmic matching engine for LiDAR room recognition.
    Compares 360-bin scans against stored room signatures using
    1D Fourier magnitude distance and circular NCC.
    """
    def __init__(
        self,
        room_registry: Optional[Any] = None,
        fourier_threshold: float = 0.25,
        ncc_threshold: float = 0.70,
        num_harmonics: int = 32
    ):
        self.registry = room_registry
        self.fourier_threshold = fourier_threshold
        self.ncc_threshold = ncc_threshold
        self.num_harmonics = num_harmonics
        self._lock = threading.RLock()

        self.registered_scans: Dict[str, np.ndarray] = {}
        self.registered_descriptors: Dict[str, np.ndarray] = {}

        if self.registry is not None:
            self.refresh_database_signatures()

    def register_room(self, room_name: str, polar_scan: np.ndarray):
        """Registers a 360-bin reference LiDAR scan for a room."""
        arr = np.asarray(polar_scan, dtype=np.float32).reshape(-1)
        if len(arr) != 360:
            raise ValueError(f"Polar scan must have 360 bins, got {len(arr)}")

        with self._lock:
            self.registered_scans[room_name] = arr
            self.registered_descriptors[room_name] = compute_fourier_descriptor(arr, self.num_harmonics)

    def refresh_database_signatures(self):
        """Loads and caches LiDAR signatures from MAGRoomRegistry."""
        if self.registry is None:
            return

        with self._lock:
            self.registered_scans.clear()
            self.registered_descriptors.clear()

            try:
                rooms = self.registry.list_rooms()
            except Exception:
                rooms = []

            for r in rooms:
                sigs = self.registry.get_room_signatures(r.room_name)
                if not sigs:
                    continue

                lidar_sig = sigs.get("lidar_signature")
                if lidar_sig is not None and len(lidar_sig) == 360:
                    arr = np.asarray(lidar_sig, dtype=np.float32)
                    self.registered_scans[r.room_name] = arr
                    self.registered_descriptors[r.room_name] = compute_fourier_descriptor(arr, self.num_harmonics)

    def recognize_scan(self, polar_ranges: np.ndarray) -> Dict[str, Any]:
        """
        Recognizes room from 360-bin query scan.
        Returns match dictionary with candidate room, confidence, yaw offset, and metrics.
        """
        q_desc = compute_fourier_descriptor(polar_ranges, self.num_harmonics)

        best_room = None
        best_ncc = -1.0
        best_fourier_dist = float("inf")
        best_yaw = 0.0

        with self._lock:
            if not self.registered_scans:
                return {
                    "matched": False,
                    "room_name": None,
                    "confidence": 0.0,
                    "yaw_offset_rad": 0.0,
                    "fourier_distance": 1.0,
                    "ncc_score": -1.0
                }

            for room_name, ref_desc in self.registered_descriptors.items():
                f_dist = float(np.linalg.norm(q_desc - ref_desc))
                ref_scan = self.registered_scans[room_name]
                ncc_score, yaw_off = compute_circular_ncc(polar_ranges, ref_scan)

                # Prioritize NCC score with secondary Fourier validation
                if ncc_score > best_ncc:
                    best_ncc = ncc_score
                    best_fourier_dist = f_dist
                    best_room = room_name
                    best_yaw = yaw_off

        # Decision threshold: NCC > 0.70 and Fourier distance < 0.25 (or high NCC > 0.80)
        matched = (best_ncc >= self.ncc_threshold and best_fourier_dist <= self.fourier_threshold) or (best_ncc >= 0.85)

        return {
            "matched": matched,
            "room_name": best_room if matched else None,
            "confidence": round(max(0.0, best_ncc), 4),
            "yaw_offset_rad": round(best_yaw, 4),
            "fourier_distance": round(best_fourier_dist, 4),
            "ncc_score": round(best_ncc, 4)
        }


class LidarRoomRecognizerNode(Node):
    """
    ROS 2 Node wrapping LidarRoomRecognizerEngine for pitch-dark ToF localization.
    """
    def __init__(self, registry: Optional[Any] = None):
        if not HAS_ROS2:
            raise RuntimeError("ROS 2 (rclpy) is not available in the current environment.")
        super().__init__("lidar_room_recognizer")

        # Parameters
        self.declare_parameter("scan_topic", "/scan")
        self.declare_parameter("status_topic", "/perception/lidar_room_id")
        self.declare_parameter("service_name", "/perception/recognize_room_lidar")
        self.declare_parameter("db_path", "/home/robopy/mag_trinity.db")
        self.declare_parameter("yaml_path", "/opt/robopy/config/rooms_metadata.yaml")
        self.declare_parameter("fourier_threshold", 0.25)
        self.declare_parameter("ncc_threshold", 0.70)

        scan_topic = self.get_parameter("scan_topic").value
        status_topic = self.get_parameter("status_topic").value
        service_name = self.get_parameter("service_name").value
        db_path = self.get_parameter("db_path").value
        yaml_path = self.get_parameter("yaml_path").value
        f_thresh = float(self.get_parameter("fourier_threshold").value)
        ncc_thresh = float(self.get_parameter("ncc_threshold").value)

        # Initialize MAG Registry
        if registry is not None:
            self.registry = registry
        elif MAGRoomRegistry is not None:
            self.registry = MAGRoomRegistry(db_path=db_path, yaml_path=yaml_path)
        else:
            self.registry = None

        self.engine = LidarRoomRecognizerEngine(
            room_registry=self.registry,
            fourier_threshold=f_thresh,
            ncc_threshold=ncc_thresh
        )

        self.latest_polar_scan: Optional[np.ndarray] = None
        self.scan_lock = threading.Lock()

        # QoS
        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # Subscribers
        self.sub_scan = self.create_subscription(
            LaserScan, scan_topic, self._on_scan, qos_sensor
        )

        # Publishers
        self.pub_status = self.create_publisher(String, status_topic, 10)

        # Service
        self.srv_recognize = self.create_service(
            Trigger, service_name, self._handle_recognize_service
        )

        # Periodic timer (2 Hz)
        self.timer = self.create_timer(0.5, self._periodic_recognition)

        self.get_logger().info(f"LiDAR Room Recognizer Node initialized [Scan: {scan_topic}]")

    def _on_scan(self, msg: LaserScan):
        """Processes incoming LaserScan into uniform 360-bin polar array."""
        try:
            binned = resample_scan_to_360_bins(
                ranges=msg.ranges,
                angle_min=msg.angle_min,
                angle_increment=msg.angle_increment,
                range_min=msg.range_min,
                range_max=msg.range_max
            )
            with self.scan_lock:
                self.latest_polar_scan = binned
        except Exception as e:
            self.get_logger().error(f"Error processing LaserScan: {e}")

    def _periodic_recognition(self):
        with self.scan_lock:
            if self.latest_polar_scan is None:
                return
            scan = self.latest_polar_scan.copy()

        res = self.engine.recognize_scan(scan)
        if res["matched"]:
            payload = {
                "timestamp": time.time(),
                "room_name": res["room_name"],
                "confidence": res["confidence"],
                "yaw_offset_rad": res["yaw_offset_rad"],
                "fourier_distance": res["fourier_distance"],
                "ncc_score": res["ncc_score"]
            }
            str_msg = String()
            str_msg.data = json.dumps(payload)
            self.pub_status.publish(str_msg)

    def _handle_recognize_service(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        with self.scan_lock:
            scan = self.latest_polar_scan.copy() if self.latest_polar_scan is not None else None

        if scan is None:
            response.success = False
            response.message = json.dumps({"matched": False, "error": "No LiDAR scan available"})
            return response

        res = self.engine.recognize_scan(scan)
        response.success = res["matched"]
        response.message = json.dumps(res)
        return response


def main(args=None):
    if not HAS_ROS2:
        print("ROS 2 (rclpy) is not installed in current environment.")
        return
    rclpy.init(args=args)
    node = LidarRoomRecognizerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
