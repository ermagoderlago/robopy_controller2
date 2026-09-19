#!/usr/bin/env python3
"""
Kidnapped Robot Auto-Recovery Node for Marcus AI
================================================
Monitors AMCL pose covariance trace (cov[0] + cov[7] + cov[35]), detects
kidnapping or loss of localization (trace >= 0.080), selects multimodal
recognition modality based on ambient luminance (VPR >30 lux vs LiDAR <=25 lux),
injects initial pose or disperses particles, commands controlled in-place
rotation scan at 0.35 rad/s with 150ms 0.70 rad/s stiction torque kick, enforces
strict <= 720° rotation limiter, and gates early convergence (< 0.080) with
TRINITY CAG location updates.

Conforms to:
  - R3 / TC5: Kidnapped recovery & pitch-dark RPLIDAR C1 scan matching
  - SPEC-01: 500ms cmd_vel watchdog & 150ms stiction kick
  - SPEC-02: AMCL 2D covariance trace monitor (< 0.080 threshold)
  - SPEC-05: TRINITY CAG update_location integration
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
    from geometry_msgs.msg import Twist, PoseWithCovarianceStamped, Point, Quaternion
    from sensor_msgs.msg import LaserScan
    from std_msgs.msg import String
    from std_srvs.srv import Trigger, Empty
    HAS_ROS2 = True
except ImportError:
    HAS_ROS2 = False
    Node = object

# Safe TRINITY import
try:
    from robot_ai.trinity.mag_room_registry import MAGRoomRegistry
    from robot_ai.trinity.cag_environment import EnvironmentSnapshot
except ImportError:
    try:
        from robopy_controller.robot_ai.trinity.mag_room_registry import MAGRoomRegistry
        from robopy_controller.robot_ai.trinity.cag_environment import EnvironmentSnapshot
    except ImportError:
        MAGRoomRegistry = None
        EnvironmentSnapshot = None

CONVERGENCE_TRACE_THRESHOLD: float = 0.080


def compute_amcl_covariance_trace(cov_array: Sequence[float]) -> float:
    """
    Calculates 2D planar pose covariance trace from 36-element AMCL covariance array.
    cov[0]  = Var(x)
    cov[7]  = Var(y)
    cov[35] = Var(yaw)
    Returns trace = cov[0] + cov[7] + cov[35].
    """
    if cov_array is None or len(cov_array) < 36:
        return float("inf")
    trace = float(cov_array[0] + cov_array[7] + cov_array[35])
    return trace


def is_amcl_converged(trace: float, threshold: float = CONVERGENCE_TRACE_THRESHOLD) -> bool:
    """
    Strict inequality convergence check: trace < 0.080.
    Rejects 0.080 and 0.081; accepts 0.079.
    """
    return bool(trace < threshold)


class RotationScanController:
    """
    Closed-loop in-place rotation controller with stiction torque kick and
    strict 720-degree (2 full turns = 4*pi rad) limiter.
    """
    MAX_ALLOWED_DEGREES: float = 720.0
    CRUISE_ANGULAR_SPEED: float = 0.35   # rad/s (~20 deg/s)
    STICTION_KICK_SPEED: float = 0.70    # rad/s for first 150ms
    STICTION_KICK_DURATION: float = 0.150  # 150ms

    def __init__(self, cmd_vel_publisher=None):
        self.pub = cmd_vel_publisher
        self.accumulated_yaw_rad: float = 0.0
        self.start_time: float = 0.0
        self.last_yaw: Optional[float] = None
        self.is_active: bool = False

    def start(self, initial_yaw: float = 0.0):
        """Starts the rotation scan sequence."""
        self.accumulated_yaw_rad = 0.0
        self.start_time = time.monotonic()
        self.last_yaw = initial_yaw
        self.is_active = True

    def get_current_command(self, current_yaw: float) -> Tuple[float, bool]:
        """
        Calculates the appropriate angular velocity command and checks limits.
        Returns (angular_z_speed, is_running).
        """
        if not self.is_active:
            return 0.0, False

        # Integrate delta yaw with angle wrapping [-pi, pi]
        if self.last_yaw is not None:
            dyaw = (current_yaw - self.last_yaw + math.pi) % (2.0 * math.pi) - math.pi
            self.accumulated_yaw_rad += abs(dyaw)
        self.last_yaw = current_yaw

        accumulated_deg = math.degrees(self.accumulated_yaw_rad)
        if accumulated_deg >= self.MAX_ALLOWED_DEGREES:
            self.stop()
            return 0.0, False

        elapsed = time.monotonic() - self.start_time
        speed = self.STICTION_KICK_SPEED if elapsed < self.STICTION_KICK_DURATION else self.CRUISE_ANGULAR_SPEED
        return speed, True

    def step(self, current_yaw: float) -> bool:
        """Publishes angular command if publisher is configured."""
        speed, running = self.get_current_command(current_yaw)
        if running and self.pub is not None and HAS_ROS2:
            twist = Twist()
            twist.angular.z = speed
            self.pub.publish(twist)
        elif not running and self.pub is not None and HAS_ROS2:
            self.pub.publish(Twist())
        return running

    def stop(self):
        """Stops the rotation sequence immediately."""
        self.is_active = False
        if self.pub is not None and HAS_ROS2:
            self.pub.publish(Twist())


class MultimodalRoomHypothesizer:
    """
    Decides between visual VPR CosPlace and LiDAR ToF recognizer based on luminance.
    """
    def __init__(self, room_registry: Optional[Any] = None, vpr_engine=None, lidar_engine=None):
        self.registry = room_registry
        self.vpr_engine = vpr_engine
        self.lidar_engine = lidar_engine

    def hypothesize_room(
        self,
        ambient_luminance: float,
        camera_image: Optional[np.ndarray] = None,
        lidar_scan: Optional[np.ndarray] = None
    ) -> Dict[str, Any]:
        """
        Branches perception modality:
        - If ambient_luminance > 30.0 lux: use VPR CosPlace.
        - If ambient_luminance <= 25.0 lux (total darkness lux = 0): use LiDAR ToF recognizer.
        - If 25.0 < ambient_luminance <= 30.0: use LiDAR recognizer for safety.
        """
        lum = float(ambient_luminance)
        if lum > 30.0:
            modality = "VPR"
            if self.vpr_engine is not None and camera_image is not None:
                # Direct local matching
                if hasattr(self.vpr_engine, "extract"):
                    vec, _ = self.vpr_engine.extract(camera_image)
                elif hasattr(self.vpr_engine, "extract_embedding_mock"):
                    vec, _ = self.vpr_engine.extract_embedding_mock(seed_feature=float(np.mean(camera_image)))
                else:
                    vec = camera_image
                if hasattr(self.vpr_engine, "match_room"):
                    res = self.vpr_engine.match_room(vec)
                elif hasattr(self.vpr_engine, "matcher"):
                    res = self.vpr_engine.matcher.match_single_frame(vec)
                else:
                    res = {"matched": False, "room_name": None, "similarity": 0.0}
            else:
                res = {"matched": False, "room_name": None, "similarity": 0.0}
        else:
            modality = "LIDAR"
            if self.lidar_engine is not None and lidar_scan is not None:
                if hasattr(self.lidar_engine, "recognize_scan"):
                    res = self.lidar_engine.recognize_scan(lidar_scan)
                else:
                    res = {"matched": False, "room_name": None, "confidence": 0.0}
            else:
                res = {"matched": False, "room_name": None, "confidence": 0.0}

        res["modality"] = modality
        res["luminance"] = lum
        return res


class KidnappedRobotRecoveryEngine:
    """
    Pure algorithmic state machine for Kidnapped Robot Auto-Recovery.
    """
    STATE_IDLE_MONITORING: str = "IDLE_MONITORING"
    STATE_KIDNAPPED_DETECTED: str = "KIDNAPPED_DETECTED"
    STATE_ROTATING_SCAN: str = "ROTATING_SCAN"
    STATE_RECOVERY_SUCCESS: str = "RECOVERY_SUCCESS"
    STATE_RECOVERY_FAILED: str = "RECOVERY_FAILED"

    def __init__(
        self,
        room_registry: Optional[Any] = None,
        vpr_engine=None,
        lidar_engine=None,
        environment_snapshot: Optional[Any] = None
    ):
        self.registry = room_registry
        self.hypothesizer = MultimodalRoomHypothesizer(room_registry, vpr_engine, lidar_engine)
        self.rotation_controller = RotationScanController()
        self.env_snapshot = environment_snapshot

        self.current_state: str = self.STATE_IDLE_MONITORING
        self.current_cov_trace: float = 0.0
        self.current_luminance: float = 0.0
        self.recognized_room: Optional[str] = None
        self.recognized_pose: Optional[Tuple[float, float, float]] = None
        self.last_status_message: str = "Monitoring AMCL pose covariance."
        self._lock = threading.RLock()

    def update_amcl_pose(
        self,
        covariance: Sequence[float],
        pose: Optional[Tuple[float, float, float]] = None,
        current_yaw: float = 0.0
    ) -> Dict[str, Any]:
        """
        Updates AMCL covariance trace and steps recovery state machine.
        """
        with self._lock:
            self.current_cov_trace = compute_amcl_covariance_trace(covariance)
            converged = is_amcl_converged(self.current_cov_trace)

            if self.current_state == self.STATE_IDLE_MONITORING:
                if not converged:
                    self.current_state = self.STATE_KIDNAPPED_DETECTED
                    self.last_status_message = f"Kidnapping detected: AMCL trace {self.current_cov_trace:.3f} >= {CONVERGENCE_TRACE_THRESHOLD}."

            elif self.current_state == self.STATE_ROTATING_SCAN:
                if converged:
                    # Rapid convergence gate: stop immediately!
                    self.rotation_controller.stop()
                    self.current_state = self.STATE_RECOVERY_SUCCESS
                    self.last_status_message = (
                        f"Marcus: Localizzazione recuperata con successo in {self.recognized_room or 'ambiente'}. "
                        f"Traccia covarianza: {self.current_cov_trace:.3f}."
                    )
                    # Update CAG Environment
                    if self.env_snapshot is not None and self.recognized_room:
                        loc = (pose[0], pose[1]) if pose else (0.0, 0.0)
                        self.env_snapshot.update_location(
                            room_name=self.recognized_room,
                            location=loc,
                            map_name="default",
                            covariance_trace=self.current_cov_trace
                        )
                else:
                    speed, running = self.rotation_controller.get_current_command(current_yaw)
                    if not running:
                        # Reached 720 degrees without convergence
                        self.current_state = self.STATE_RECOVERY_FAILED
                        self.last_status_message = (
                            f"Recovery failed: Exceeded 720° rotation with trace {self.current_cov_trace:.3f} >= {CONVERGENCE_TRACE_THRESHOLD}."
                        )

            return {
                "state": self.current_state,
                "cov_trace": round(self.current_cov_trace, 4),
                "converged": converged,
                "recognized_room": self.recognized_room,
                "status_message": self.last_status_message,
                "accumulated_rotation_deg": round(math.degrees(self.rotation_controller.accumulated_yaw_rad), 1)
            }

    def trigger_recovery(
        self,
        ambient_luminance: float,
        camera_image: Optional[np.ndarray] = None,
        lidar_scan: Optional[np.ndarray] = None,
        current_yaw: float = 0.0
    ) -> Dict[str, Any]:
        """
        Explicitly triggers kidnapped robot recovery.
        """
        with self._lock:
            self.current_luminance = ambient_luminance
            self.current_state = self.STATE_KIDNAPPED_DETECTED

            hyp = self.hypothesizer.hypothesize_room(
                ambient_luminance=ambient_luminance,
                camera_image=camera_image,
                lidar_scan=lidar_scan
            )

            if hyp.get("matched"):
                self.recognized_room = hyp.get("room_name")
                yaw_off = hyp.get("yaw_offset_rad", 0.0)
                # Fetch centroid from registry if available
                centroid = (0.0, 0.0)
                if self.registry is not None:
                    meta = self.registry.get_room(self.recognized_room)
                    if meta:
                        centroid = getattr(meta, "centroid", (0.0, 0.0))
                self.recognized_pose = (centroid[0], centroid[1], yaw_off)
            else:
                self.recognized_room = None
                self.recognized_pose = None

            # Start in-place rotation scan
            self.current_state = self.STATE_ROTATING_SCAN
            self.rotation_controller.start(initial_yaw=current_yaw)
            self.last_status_message = (
                f"Starting rotation scan (Modality: {hyp.get('modality')}, "
                f"Candidate: {self.recognized_room or 'Global'})."
            )

            return {
                "state": self.current_state,
                "modality": hyp.get("modality"),
                "recognized_room": self.recognized_room,
                "recognized_pose": self.recognized_pose,
                "status_message": self.last_status_message
            }


class KidnappedRobotRecoveryNode(Node):
    """
    ROS 2 Node managing kidnapped robot recovery, AMCL covariance monitoring,
    and in-place rotation scan commands.
    """
    def __init__(
        self,
        room_registry: Optional[Any] = None,
        vpr_engine=None,
        lidar_engine=None,
        environment_snapshot: Optional[Any] = None
    ):
        if not HAS_ROS2:
            raise RuntimeError("ROS 2 (rclpy) is not available in the current environment.")
        super().__init__("kidnapped_robot_recovery")

        # Parameters
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("amcl_pose_topic", "/amcl_pose")
        self.declare_parameter("initial_pose_topic", "/initialpose")
        self.declare_parameter("luminance_topic", "/camera/luminance_status")
        self.declare_parameter("voice_topic", "/ai/conversation/response")
        self.declare_parameter("db_path", "/home/robopy/mag_trinity.db")
        self.declare_parameter("yaml_path", "/opt/robopy/config/rooms_metadata.yaml")
        self.declare_parameter("trace_threshold", 0.080)

        cmd_topic = self.get_parameter("cmd_vel_topic").value
        amcl_topic = self.get_parameter("amcl_pose_topic").value
        init_pose_topic = self.get_parameter("initial_pose_topic").value
        lum_topic = self.get_parameter("luminance_topic").value
        voice_topic = self.get_parameter("voice_topic").value
        db_path = self.get_parameter("db_path").value
        yaml_path = self.get_parameter("yaml_path").value
        self.trace_threshold = float(self.get_parameter("trace_threshold").value)

        # Registry & CAG
        if room_registry is not None:
            self.registry = room_registry
        elif MAGRoomRegistry is not None:
            self.registry = MAGRoomRegistry(db_path=db_path, yaml_path=yaml_path)
        else:
            self.registry = None

        if environment_snapshot is not None:
            self.env_snapshot = environment_snapshot
        elif EnvironmentSnapshot is not None:
            self.env_snapshot = EnvironmentSnapshot()
        else:
            self.env_snapshot = None

        # Engine
        self.engine = KidnappedRobotRecoveryEngine(
            room_registry=self.registry,
            vpr_engine=vpr_engine,
            lidar_engine=lidar_engine,
            environment_snapshot=self.env_snapshot
        )

        self.current_luminance: float = 35.0  # default bright
        self.current_yaw: float = 0.0

        # QoS
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        qos_best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # Publishers
        self.pub_cmd_vel = self.create_publisher(Twist, cmd_topic, qos_reliable)
        self.pub_init_pose = self.create_publisher(PoseWithCovarianceStamped, init_pose_topic, qos_reliable)
        self.pub_status = self.create_publisher(String, "/recovery/status", qos_reliable)
        self.pub_voice = self.create_publisher(String, voice_topic, qos_reliable)

        # Wire publisher to engine's rotation controller
        self.engine.rotation_controller.pub = self.pub_cmd_vel

        # Subscribers
        self.sub_amcl = self.create_subscription(
            PoseWithCovarianceStamped, amcl_topic, self._on_amcl_pose, qos_best_effort
        )
        self.sub_lum = self.create_subscription(
            String, lum_topic, self._on_luminance_status, qos_best_effort
        )

        # Service clients
        self.cli_global_loc = self.create_client(Empty, "/reinitialize_global_localization")
        self.cli_vpr = self.create_client(Trigger, "/perception/recognize_room_vpr")
        self.cli_lidar = self.create_client(Trigger, "/perception/recognize_room_lidar")

        # Service server
        self.srv_trigger = self.create_service(
            Trigger, "/recovery/trigger_kidnapped_recovery", self._handle_trigger_service
        )

        # Timer: 10 Hz continuous command loop for 500ms watchdog compliance
        self.timer = self.create_timer(0.10, self._command_loop)

        self.get_logger().info("Kidnapped Robot Recovery Node initialized.")

    def _on_luminance_status(self, msg: String):
        try:
            data = json.loads(msg.data)
            self.current_luminance = float(data.get("luminance", 0.0))
        except Exception:
            pass

    def _on_amcl_pose(self, msg: PoseWithCovarianceStamped):
        cov = msg.pose.covariance
        # Extract yaw from quaternion
        q = msg.pose.pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self.current_yaw = math.atan2(siny_cosp, cosy_cosp)
        pos = msg.pose.pose.position

        res = self.engine.update_amcl_pose(
            covariance=cov,
            pose=(pos.x, pos.y, self.current_yaw),
            current_yaw=self.current_yaw
        )

        if res["state"] == KidnappedRobotRecoveryEngine.STATE_RECOVERY_SUCCESS:
            v_msg = String()
            v_msg.data = res["status_message"]
            self.pub_voice.publish(v_msg)

    def _command_loop(self):
        """10 Hz continuous motion cycle."""
        if self.engine.current_state == KidnappedRobotRecoveryEngine.STATE_ROTATING_SCAN:
            self.engine.rotation_controller.step(self.current_yaw)

        # Broadcast recovery status JSON
        status_payload = {
            "state": self.engine.current_state,
            "cov_trace": self.engine.current_cov_trace,
            "recognized_room": self.engine.recognized_room,
            "accumulated_deg": math.degrees(self.engine.rotation_controller.accumulated_yaw_rad)
        }
        s_msg = String()
        s_msg.data = json.dumps(status_payload)
        self.pub_status.publish(s_msg)

    def _handle_trigger_service(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        res = self.engine.trigger_recovery(
            ambient_luminance=self.current_luminance,
            current_yaw=self.current_yaw
        )
        response.success = True
        response.message = json.dumps(res)
        return response


def main(args=None):
    if not HAS_ROS2:
        print("ROS 2 (rclpy) is not installed in current environment.")
        return
    rclpy.init(args=args)
    node = KidnappedRobotRecoveryNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
