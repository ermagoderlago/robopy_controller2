"""
CAG Room Bridge: Feeds localization pose and semantic room registry into CAG EnvironmentSnapshot.
Supports direct python feed_pose() and optional throttled ROS 2 subscription on /amcl_pose.
"""

import time
import math
from typing import Optional, Tuple, Any

from robot_ai.utils.logging_utils import get_logger
from robot_ai.trinity.cag_environment import EnvironmentSnapshot
from robot_ai.trinity.mag_room_registry import MAGRoomRegistry

logger = get_logger("CAGRoomBridge")

try:
    from geometry_msgs.msg import PoseWithCovarianceStamped
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
    HAS_ROS = True
except ImportError:
    HAS_ROS = False
    PoseWithCovarianceStamped = None


class CAGRoomBridge:
    """
    Bridges robot localization telemetry with MAGRoomRegistry and EnvironmentSnapshot.
    Computes active room, proximity to centroid, and updates CAG environment state.
    """

    def __init__(
        self,
        environment_snapshot: EnvironmentSnapshot,
        room_registry: MAGRoomRegistry,
        node: Optional[Any] = None,
        pose_topic: str = "/amcl_pose",
        map_name: str = "casa_piano1",
        throttle_sec: float = 0.5
    ):
        self.snapshot = environment_snapshot
        self.registry = room_registry
        self.node = node
        self.map_name = map_name
        self.throttle_sec = throttle_sec
        self._last_time = 0.0
        self._sub = None

        if HAS_ROS and self.node is not None and PoseWithCovarianceStamped is not None:
            try:
                qos = QoSProfile(
                    reliability=ReliabilityPolicy.BEST_EFFORT,
                    history=HistoryPolicy.KEEP_LAST,
                    depth=1
                )
                self._sub = self.node.create_subscription(
                    PoseWithCovarianceStamped,
                    pose_topic,
                    self._on_pose_msg,
                    qos
                )
                logger.info(f"Subscribed to {pose_topic} for CAG room updates.")
            except Exception as e:
                logger.warning(f"Failed to create ROS 2 subscription on {pose_topic}: {e}")

    def _on_pose_msg(self, msg: Any) -> None:
        """ROS 2 callback with throttle gate."""
        now = time.time()
        if now - self._last_time < self.throttle_sec:
            return
        self._last_time = now

        try:
            x = float(msg.pose.pose.position.x)
            y = float(msg.pose.pose.position.y)
            cov = msg.pose.covariance
            # 2D positional trace: Var(x) + Var(y)
            cov_trace = float(cov[0] + cov[7]) if len(cov) >= 8 else 0.0
            self.feed_pose(x, y, cov_trace=cov_trace, map_name=self.map_name)
        except Exception as e:
            logger.error(f"Error parsing pose msg in CAGRoomBridge: {e}")

    def feed_pose(
        self,
        x: float,
        y: float,
        cov_trace: float = 0.0,
        map_name: Optional[str] = None
    ) -> str:
        """
        Direct, ROS-agnostic method for updating environment state.
        Computes room matching and distance to centroid.
        """
        active_map = map_name or self.map_name
        room_name = self.registry.match_room_by_pose(x, y)
        dist_to_centroid = None

        if room_name:
            room = self.registry.get_room(room_name)
            if room and room.centroid:
                cx, cy = room.centroid
                dist_to_centroid = math.hypot(x - cx, y - cy)
        else:
            nearest = self.registry.get_nearest_room(x, y)
            if nearest:
                n_name, n_dist = nearest
                room_name = f"Unknown (nearest: {n_name} {n_dist:.1f}m)"
            else:
                room_name = "Unknown"

        self.snapshot.update_location(
            room_name=room_name,
            location=(x, y),
            map_name=active_map,
            covariance_trace=cov_trace,
            dist_to_centroid=dist_to_centroid
        )
        return room_name
