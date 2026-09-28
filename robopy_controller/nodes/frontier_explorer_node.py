#!/usr/bin/env python3
"""
Frontier Explorer Node for Marcus AI
====================================
Performs autonomous frontier exploration on 2D Occupancy Grid (/map):
  1. Frontier Cell Detection:
     - Detects free cells (0) bordering unknown cells (-1).
     - Rejects cells touching obstacles (100) or within robot inflation radius (0.18m).
  2. Connected-Component Clustering (BFS):
     - Groups contiguous frontier cells into distinct clusters.
     - Computes bounding box, spatial size (meters), and world centroid (x, y).
  3. Stopping Criteria & Threshold:
     - MIN_FRONTIER_SIZE_METERS = 0.40m (TC3).
     - Stops exploration when all active frontiers < 0.40m or no frontiers remain.
     - Emits zero velocity stop command on /cmd_vel.
  4. Nav2 Action Client Integration:
     - Dispatches goal pose to /navigate_to_pose (nav2_msgs/action/NavigateToPose).
     - Manages unreachable goal blacklisting with spatial radius matching (0.20m).

Conforms to:
  - TC3 / R2: Autonomous frontier exploration & stopping criteria (<0.40m)
  - SPEC-02: Nav2 stack integration, robot_radius 0.18m, REP-105 TF compliance
  - marcus_core_rules.md: RAM < 4GB, sequential build, zero BOM UTF-8
"""

import math
import json
import time
from typing import Dict, Any, List, Tuple, Optional, Set
import numpy as np

# Safe ROS 2 import guard
try:
    import rclpy
    from rclpy.node import Node
    from rclpy.action import ActionClient
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
    from nav_msgs.msg import OccupancyGrid, Odometry
    from geometry_msgs.msg import Twist, PoseStamped
    from std_msgs.msg import String, Bool
    from std_srvs.srv import Trigger
    from nav2_msgs.action import NavigateToPose
    from action_msgs.msg import GoalStatus
    try:
        from robopy_controller.msg import SemanticObjectArray, SemanticObject
    except ImportError:
        SemanticObjectArray = None
        SemanticObject = None
    HAS_ROS2 = True
except ImportError:
    HAS_ROS2 = False
    Node = object
    GoalStatus = None
    SemanticObjectArray = None
    SemanticObject = None

    class _MockMsg:
        def __init__(self, *args, **kwargs):
            self.data = None
        def __call__(self, *args, **kwargs):
            return self

    class _MockSrv:
        Request = _MockMsg
        Response = _MockMsg

    Twist = _MockMsg
    PoseStamped = _MockMsg
    OccupancyGrid = _MockMsg
    Odometry = _MockMsg
    String = _MockMsg
    Bool = _MockMsg
    Trigger = _MockSrv
    NavigateToPose = _MockSrv


class FrontierExplorationEngine:
    """
    Pure Python/NumPy engine for frontier detection, clustering, and stopping evaluation.
    Completely decoupled from ROS 2 for fast, deterministic unit testing.
    """
    FREE: int = 0
    OCCUPIED_THRESHOLD: int = 65
    OCCUPIED_LETHAL: int = 100
    UNKNOWN: int = -1
    MIN_FRONTIER_SIZE_METERS: float = 0.40
    DEFAULT_ROBOT_RADIUS: float = 0.18  # meters (from SPEC-02)
    DEFAULT_CLEARANCE_RADIUS: float = 0.20  # meters
    BLACKLIST_DISTANCE_THRESHOLD: float = 0.20  # meters

    def __init__(
        self,
        resolution: float = 0.05,
        origin: Tuple[float, float] = (-10.0, -10.0),
        min_frontier_size: float = 0.40,
        robot_radius: float = 0.18,
        clearance_radius: float = 0.20,
        selection_strategy: str = "largest",
        w_size: float = 1.0,
        w_dist: float = 0.7,
        w_semantic: float = 1.5
    ):
        self.resolution = float(resolution)
        self.origin = origin
        self.min_frontier_size = float(min_frontier_size)
        self.robot_radius = float(robot_radius)
        self.clearance_radius = float(clearance_radius)
        self.selection_strategy = selection_strategy  # "largest", "nearest", "hybrid", "semantic"
        self.w_size = float(w_size)
        self.w_dist = float(w_dist)
        self.w_semantic = float(w_semantic)
        self.blacklisted_centroids: List[Tuple[float, float]] = []
        self.dispatched_goals: List[Tuple[float, float]] = []
        self.failed_goal_counts: Dict[Tuple[float, float], int] = {}

    def detect_frontier_cells(
        self,
        grid: np.ndarray,
        reject_obstacle_proximity: bool = True
    ) -> List[Tuple[int, int]]:
        """
        Detects free cells (0) that have at least one 8-neighbor that is unknown (-1).
        Rejects cells touching obstacles (>=65) or within clearance_radius of obstacles.
        """
        rows, cols = grid.shape
        frontier_cells: List[Tuple[int, int]] = []

        if rows < 3 or cols < 3:
            return frontier_cells

        # 8-connected neighbor offsets
        neighbors = [
            (-1, -1), (-1, 0), (-1, 1),
            (0, -1),           (0, 1),
            (1, -1),  (1, 0),  (1, 1)
        ]

        # Precompute obstacle mask if obstacle rejection is requested and obstacles exist
        has_obstacles = np.any(grid >= self.OCCUPIED_THRESHOLD)
        clearance_cells = int(math.ceil(self.clearance_radius / self.resolution))

        for r in range(1, rows - 1):
            for c in range(1, cols - 1):
                if grid[r, c] != self.FREE:
                    continue

                # Check if cell has any unknown neighbor
                touches_unknown = False
                touches_obstacle = False

                for dr, dc in neighbors:
                    val = grid[r + dr, c + dc]
                    if val == self.UNKNOWN:
                        touches_unknown = True
                    elif val >= self.OCCUPIED_THRESHOLD:
                        touches_obstacle = True

                # Must touch unknown space and NOT directly touch an obstacle
                if not touches_unknown or touches_obstacle:
                    continue

                # Check inflation clearance if obstacles exist in the grid
                if reject_obstacle_proximity and has_obstacles:
                    min_r = max(0, r - clearance_cells)
                    max_r = min(rows, r + clearance_cells + 1)
                    min_c = max(0, c - clearance_cells)
                    max_c = min(cols, c + clearance_cells + 1)
                    subgrid = grid[min_r:max_r, min_c:max_c]
                    if np.any(subgrid >= self.OCCUPIED_THRESHOLD):
                        continue

                frontier_cells.append((r, c))

        return frontier_cells

    def cluster_frontiers(
        self,
        frontier_cells: List[Tuple[int, int]]
    ) -> List[Dict[str, Any]]:
        """
        Clusters contiguous frontier cells using Connected Component Labeling (BFS).
        Computes bounding box, spatial metric size (meters), world centroid, and safe navigation goal.
        """
        if not frontier_cells:
            return []

        cell_set = set(frontier_cells)
        visited: Set[Tuple[int, int]] = set()
        clusters: List[Dict[str, Any]] = []

        neighbors = [
            (-1, -1), (-1, 0), (-1, 1),
            (0, -1),           (0, 1),
            (1, -1),  (1, 0),  (1, 1)
        ]

        cluster_id = 0
        for cell in frontier_cells:
            if cell in visited:
                continue

            cluster_cells: List[Tuple[int, int]] = []
            queue: List[Tuple[int, int]] = [cell]
            visited.add(cell)

            while queue:
                curr = queue.pop(0)
                cluster_cells.append(curr)
                cr, cc = curr

                for dr, dc in neighbors:
                    nbr = (cr + dr, cc + dc)
                    if nbr in cell_set and nbr not in visited:
                        visited.add(nbr)
                        queue.append(nbr)

            rows = [c[0] for c in cluster_cells]
            cols = [c[1] for c in cluster_cells]

            min_r, max_r = min(rows), max(rows)
            min_c, max_c = min(cols), max(cols)

            # Metric spatial bounding box diagonal
            delta_x = (max_c - min_c) * self.resolution
            delta_y = (max_r - min_r) * self.resolution
            spatial_size = math.sqrt(delta_x ** 2 + delta_y ** 2)

            # World coordinates centroid
            mean_r = float(np.mean(rows))
            mean_c = float(np.mean(cols))
            world_x = self.origin[0] + mean_c * self.resolution
            world_y = self.origin[1] + mean_r * self.resolution

            # Safe navigation goal cell (closest cluster cell to centroid for concavity safety)
            closest_cell = min(cluster_cells, key=lambda c: math.hypot(c[0] - mean_r, c[1] - mean_c))
            nav_goal_x = self.origin[0] + closest_cell[1] * self.resolution
            nav_goal_y = self.origin[1] + closest_cell[0] * self.resolution

            clusters.append({
                "cluster_id": cluster_id,
                "cell_count": len(cluster_cells),
                "cells": cluster_cells,
                "size_meters": round(spatial_size, 4),
                "centroid": (round(world_x, 3), round(world_y, 3)),
                "nav_goal": (round(nav_goal_x, 3), round(nav_goal_y, 3)),
                "bbox": (min_r, min_c, max_r, max_c),
            })
            cluster_id += 1

        return clusters

    def evaluate_frontiers(
        self,
        grid: np.ndarray,
        robot_pose: Optional[Tuple[float, float]] = None,
        semantic_landmarks: Optional[List[Dict[str, Any]]] = None,
        search_target: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes complete exploration evaluation cycle:
          - Detects and clusters frontier cells.
          - Filters out blacklisted clusters.
          - Evaluates stopping threshold (MIN_FRONTIER_SIZE_METERS = 0.40m).
          - Selects next goal according to strategy (largest, nearest, hybrid, or semantic biased).
        """
        frontier_cells = self.detect_frontier_cells(grid)
        clusters = self.cluster_frontiers(frontier_cells)

        # Filter out blacklisted centroids and nav_goals
        active_clusters = []
        for c in clusters:
            cx, cy = c["centroid"]
            nx, ny = c["nav_goal"]
            is_blacklisted = any(
                math.hypot(cx - bx, cy - by) < self.BLACKLIST_DISTANCE_THRESHOLD or
                math.hypot(nx - bx, ny - by) < self.BLACKLIST_DISTANCE_THRESHOLD
                for bx, by in self.blacklisted_centroids
            )
            if not is_blacklisted:
                active_clusters.append(c)

        # Condition 1: No active frontiers remain
        if not active_clusters:
            return {
                "status": "COMPLETED",
                "reason": "NO_FRONTIERS_REMAINING",
                "max_size_meters": 0.0,
                "cluster_count": 0,
                "selected_goal": None,
                "selected_cluster": None,
                "motor_stop_required": True,
            }

        max_size = max(c["size_meters"] for c in active_clusters)
        valid_frontiers = [c for c in active_clusters if c["size_meters"] >= self.MIN_FRONTIER_SIZE_METERS]

        # Condition 2: All active frontiers are below the 0.40m stopping threshold
        if not valid_frontiers:
            return {
                "status": "COMPLETED",
                "reason": f"ALL_FRONTIERS_BELOW_THRESHOLD ({max_size:.2f}m < {self.MIN_FRONTIER_SIZE_METERS}m)",
                "max_size_meters": max_size,
                "cluster_count": len(active_clusters),
                "selected_goal": None,
                "selected_cluster": None,
                "motor_stop_required": True,
            }

        # Condition 3: Valid frontiers exist -> Select optimal goal
        if (self.selection_strategy == "semantic" or search_target or semantic_landmarks) and robot_pose is not None:
            rx, ry = robot_pose
            max_s = max(c["size_meters"] for c in valid_frontiers) if valid_frontiers else 1.0
            dists = [math.hypot(c["centroid"][0] - rx, c["centroid"][1] - ry) for c in valid_frontiers]
            max_d = max(dists) if dists and max(dists) > 0 else 1.0

            for i, c in enumerate(valid_frontiers):
                s_norm = c["size_meters"] / max_s
                d_norm = dists[i] / max_d
                sem_bonus = 0.0

                if semantic_landmarks:
                    cx, cy = c["centroid"]
                    for lm in semantic_landmarks:
                        lx = lm.get("x", 0.0)
                        ly = lm.get("y", 0.0)
                        label = lm.get("label", "").lower()
                        d_lm = math.hypot(cx - lx, cy - ly)
                        if search_target and search_target.lower() in label:
                            # Target relevance: strong directional attraction
                            target_bonus = 3.0 / (d_lm + 0.5)
                            sem_bonus = max(sem_bonus, target_bonus)
                        else:
                            # General exploration landmark/portal proximity
                            if d_lm < 1.5:
                                sem_bonus += 0.25

                score = (self.w_size * s_norm) - (self.w_dist * d_norm) + (self.w_semantic * sem_bonus)
                c["score"] = round(score, 3)

            selected = max(valid_frontiers, key=lambda c: c.get("score", 0.0))
        elif self.selection_strategy == "nearest" and robot_pose is not None:
            rx, ry = robot_pose
            selected = min(valid_frontiers, key=lambda c: math.hypot(c["centroid"][0] - rx, c["centroid"][1] - ry))
        elif self.selection_strategy == "hybrid" and robot_pose is not None:
            rx, ry = robot_pose
            selected = max(valid_frontiers, key=lambda c: c["size_meters"] / (math.hypot(c["centroid"][0] - rx, c["centroid"][1] - ry) + 0.5))
        else:
            # Default "largest" strategy
            selected = max(valid_frontiers, key=lambda c: c["size_meters"])

        goal = selected["nav_goal"]
        self.dispatched_goals.append(goal)

        return {
            "status": "EXPLORING",
            "reason": f"FRONTIER_FOUND (size {selected['size_meters']:.2f}m >= {self.MIN_FRONTIER_SIZE_METERS}m)",
            "max_size_meters": max_size,
            "cluster_count": len(active_clusters),
            "selected_goal": goal,
            "selected_cluster": selected,
            "motor_stop_required": False,
        }

    def blacklist_frontier(self, centroid: Tuple[float, float], reason: str = "UNREACHABLE"):
        """Blacklists an unreachable centroid."""
        norm_centroid = (round(centroid[0], 3), round(centroid[1], 3))
        if norm_centroid not in self.blacklisted_centroids:
            self.blacklisted_centroids.append(norm_centroid)

    def clear_blacklist(self):
        """Clears all blacklisted centroids."""
        self.blacklisted_centroids.clear()
        self.failed_goal_counts.clear()


class FrontierExplorerNode(Node):
    """
    ROS 2 Lifecycle & Interaction Node for autonomous frontier exploration.
    Integrates with Nav2 NavigateToPose action server and Mapping State Machine.
    """
    def __init__(self):
        if not HAS_ROS2:
            raise RuntimeError("ROS 2 libraries not available in current environment")

        super().__init__("frontier_explorer_node")

        # Parameters
        self.declare_parameter("min_frontier_size", 0.40)
        self.declare_parameter("selection_strategy", "semantic")
        self.declare_parameter("robot_radius", 0.18)
        self.declare_parameter("clearance_radius", 0.20)
        self.declare_parameter("max_retries", 3)
        self.declare_parameter("map_topic", "/map")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("w_size", 1.0)
        self.declare_parameter("w_dist", 0.7)
        self.declare_parameter("w_semantic", 1.5)
        self.declare_parameter("max_goal_duration_sec", 45.0)
        self.declare_parameter("min_target_confidence", 0.60)
        self.declare_parameter("approach_standoff_m", 0.85)

        min_size = self.get_parameter("min_frontier_size").value
        strategy = self.get_parameter("selection_strategy").value
        radius = self.get_parameter("robot_radius").value
        clearance = self.get_parameter("clearance_radius").value
        w_size = float(self.get_parameter("w_size").value)
        w_dist = float(self.get_parameter("w_dist").value)
        w_semantic = float(self.get_parameter("w_semantic").value)
        self.max_goal_duration = float(self.get_parameter("max_goal_duration_sec").value)
        self.min_target_confidence = float(self.get_parameter("min_target_confidence").value)
        self.approach_standoff_m = float(self.get_parameter("approach_standoff_m").value)

        # Instantiate pure engine
        self.engine = FrontierExplorationEngine(
            min_frontier_size=min_size,
            selection_strategy=strategy,
            robot_radius=radius,
            clearance_radius=clearance,
            w_size=w_size,
            w_dist=w_dist,
            w_semantic=w_semantic
        )

        self.is_active = False
        self.motors_allowed = True
        self.current_goal_handle = None
        self.goal_start_time = None
        self.latest_grid = None
        self.robot_pose = (0.0, 0.0)

        # Dual Mode & Target Search Tracking
        self.mode = "EXPLORE"  # "EXPLORE" or "HUNT"
        self.search_target = ""
        self.target_acquired = False
        self.target_reached = False
        self.target_position = None
        self.semantic_landmarks: List[Dict[str, Any]] = []

        # QoS Profiles
        map_qos = QoSProfile(
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # Subscriptions
        self.map_sub = self.create_subscription(
            OccupancyGrid,
            self.get_parameter("map_topic").value,
            self.map_callback,
            map_qos
        )
        self.odom_sub = self.create_subscription(
            Odometry,
            "/odom",
            self.odom_callback,
            10
        )
        self.motors_allowed_sub = self.create_subscription(
            Bool,
            "/mapping/motors_allowed",
            self.motors_allowed_callback,
            10
        )
        self.target_sub = self.create_subscription(
            String,
            "/exploration/search_target",
            self.search_target_callback,
            10
        )
        self.enable_sub = self.create_subscription(
            Bool,
            "/exploration/enable",
            self.enable_callback,
            10
        )

        if SemanticObjectArray is not None:
            self.hailo_sub = self.create_subscription(
                SemanticObjectArray,
                "/hailo/semantic_objects",
                self.semantic_objects_callback,
                10
            )
            self.vlm_sub = self.create_subscription(
                SemanticObjectArray,
                "/hailo/vlm/semantic_objects",
                self.semantic_objects_callback,
                10
            )

        # Publishers
        self.cmd_vel_pub = self.create_publisher(Twist, self.get_parameter("cmd_vel_topic").value, 10)
        self.status_pub = self.create_publisher(String, "/frontier_exploration/status", 10)
        self.target_event_pub = self.create_publisher(String, "/exploration/target_event", 10)

        # Nav2 Action Client
        self.nav_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")

        # Services
        self.srv_start = self.create_service(Trigger, "/frontier_exploration/start", self.handle_start)
        self.srv_stop = self.create_service(Trigger, "/frontier_exploration/stop", self.handle_stop)
        self.srv_eval = self.create_service(Trigger, "/frontier_exploration/evaluate", self.handle_evaluate)
        self.srv_clear = self.create_service(Trigger, "/frontier_exploration/clear_blacklist", self.handle_clear_blacklist)

        # Throttled evaluation timer (0.5 Hz = every 2.0s)
        self.eval_timer = self.create_timer(2.0, self.timer_evaluation_loop)

        self.get_logger().info("✅ FrontierExplorerNode con Bias Semantico & Anti-Loop inizializzato con successo.")

    def enable_callback(self, msg: Bool):
        """Attiva o disattiva l'esplorazione autonoma tramite topic /exploration/enable."""
        if msg.data:
            if not self.motors_allowed:
                self.get_logger().warn("Impossibile avviare esplorazione: /mapping/motors_allowed è False!")
                return
            self.is_active = True
            self.get_logger().info("▶️ Frontier exploration attivata via /exploration/enable.")
            self._execute_evaluation_step()
        else:
            self.is_active = False
            if self.current_goal_handle is not None:
                self.current_goal_handle.cancel_goal_async()
                self.current_goal_handle = None
            self.stop_robot_motors()
            self.get_logger().info("⏹️ Frontier exploration fermata via /exploration/enable.")

    def search_target_callback(self, msg: String):
        """Riceve target semantico di ricerca (es. 'persona', 'sedia', 'bottiglia')."""
        target = msg.data.strip().lower()
        self.search_target = target
        if target:
            self.mode = "HUNT"
            self.target_acquired = False
            self.target_reached = False
            self.target_position = None
            self.get_logger().info(f"🎯 Modalità HUNT attivata! Bersaglio cercato: '{target}'")
        else:
            self.mode = "EXPLORE"
            self.get_logger().info("🗺️ Modalità EXPLORE ripristinata (esplorazione autonoma pura).")

    def semantic_objects_callback(self, msg):
        """Raccoglie i landmark semantici recenti e innesca l'aggancio bersaglio in modalità HUNT."""
        now = time.time()
        # Pulizia landmark vecchi (> 30s)
        self.semantic_landmarks = [lm for lm in self.semantic_landmarks if now - lm.get("time", 0.0) < 30.0]

        for obj in getattr(msg, "objects", []):
            conf = float(getattr(obj, "confidence", 0.0))
            label = str(getattr(obj, "label", "")).lower()
            sem_class = str(getattr(obj, "semantic_class", "")).lower()

            pt2d = getattr(obj, "centroid_2d", None)
            pt3d = getattr(obj, "centroid_3d", None)

            ox, oy = 0.0, 0.0
            if pt2d and (abs(pt2d.x) > 1e-3 or abs(pt2d.y) > 1e-3):
                ox, oy = float(pt2d.x), float(pt2d.y)
            elif pt3d and (abs(pt3d.x) > 1e-3 or abs(pt3d.y) > 1e-3 or pt3d.z > 0.05):
                rx, ry = self.robot_pose
                ox, oy = rx + float(pt3d.z), ry - float(pt3d.x)
            else:
                continue

            self.semantic_landmarks.append({
                "x": round(ox, 3),
                "y": round(oy, 3),
                "label": label,
                "class": sem_class,
                "confidence": round(conf, 3),
                "time": now
            })

            # Verifica aggancio target in modalità HUNT
            if self.mode == "HUNT" and self.search_target and not self.target_acquired:
                if (self.search_target in label or self.search_target in sem_class) and conf >= self.min_target_confidence:
                    rx, ry = self.robot_pose
                    d = math.hypot(ox - rx, oy - ry)
                    if d <= 4.0:
                        self.acquire_and_approach_target((ox, oy), label)
                        break

    def acquire_and_approach_target(self, target_xy: Tuple[float, float], label: str):
        """Sospende l'esplorazione delle frontiere e guida Marcus verso il bersaglio a distanza di standoff."""
        tx, ty = target_xy
        rx, ry = self.robot_pose
        dx = tx - rx
        dy = ty - ry
        dist = math.hypot(dx, dy)

        if dist > self.approach_standoff_m:
            ratio = (dist - self.approach_standoff_m) / dist
            approach_x = rx + dx * ratio
            approach_y = ry + dy * ratio
        else:
            approach_x, approach_y = rx, ry

        yaw = math.atan2(dy, dx)
        approach_goal = (round(approach_x, 3), round(approach_y, 3))

        self.get_logger().info(f"🎯 TARGET ACQUIRED! Rilevato '{label}' a ({tx:.2f}, {ty:.2f}). Avvicinamento a ({approach_x:.2f}, {approach_y:.2f}) [standoff {self.approach_standoff_m}m]...")

        # Cancella eventuale goal frontiera attivo
        if self.current_goal_handle is not None:
            self.current_goal_handle.cancel_goal_async()
            self.current_goal_handle = None

        self.target_acquired = True
        self.target_position = (tx, ty)

        event_msg = String()
        event_msg.data = json.dumps({
            "event": "TARGET_ACQUIRED",
            "target": label,
            "target_coordinates": [tx, ty],
            "approach_coordinates": [approach_x, approach_y],
            "timestamp": time.time()
        })
        self.target_event_pub.publish(event_msg)

        if not self.nav_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error("Nav2 Action Server non disponibile per l'avvicinamento al target!")
            return

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id = "map"
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = float(approach_x)
        goal_msg.pose.pose.position.y = float(approach_y)
        goal_msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal_msg.pose.pose.orientation.w = math.cos(yaw / 2.0)

        self.goal_start_time = time.time()
        send_future = self.nav_client.send_goal_async(goal_msg)
        send_future.add_done_callback(lambda f: self.approach_goal_response_callback(f, approach_goal, label))

    def approach_goal_response_callback(self, future, goal_xy: Tuple[float, float], label: str):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().warn(f"Goal di avvicinamento al target {goal_xy} rifiutato da Nav2.")
            self.target_acquired = False
            self.current_goal_handle = None
            return
        self.current_goal_handle = goal_handle
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(lambda f: self.approach_goal_result_callback(f, goal_xy, label))

    def approach_goal_result_callback(self, future, goal_xy: Tuple[float, float], label: str):
        self.current_goal_handle = None
        self.goal_start_time = None
        result = future.result()
        status = result.status

        if status == GoalStatus.STATUS_SUCCEEDED:
            self.target_reached = True
            self.get_logger().info(f"🏆 TARGET REACHED! Marcus ha raggiunto con successo '{label}' a ({goal_xy[0]:.2f}, {goal_xy[1]:.2f})!")
            self.stop_robot_motors()
            self.is_active = False

            event_msg = String()
            event_msg.data = json.dumps({
                "event": "TARGET_REACHED",
                "target": label,
                "coordinates": list(goal_xy),
                "timestamp": time.time()
            })
            self.target_event_pub.publish(event_msg)
        else:
            self.get_logger().warn(f"⚠️ Avvicinamento al target {goal_xy} terminato con stato {status}. Ripresa esplorazione...")
            self.target_acquired = False
            if self.is_active:
                self._execute_evaluation_step()

    def motors_allowed_callback(self, msg: Bool):
        """Monitors physical motion safety interlock from MappingStateMachine."""
        self.motors_allowed = bool(msg.data)
        if not self.motors_allowed:
            if self.current_goal_handle is not None:
                self.get_logger().warn("Motor interlock: motors_allowed is False. Cancelling active Nav2 goal.")
                self.current_goal_handle.cancel_goal_async()
                self.current_goal_handle = None
            self.stop_robot_motors()

    def map_callback(self, msg: OccupancyGrid):
        """Processes incoming occupancy grid map."""
        self.engine.resolution = msg.info.resolution
        self.engine.origin = (msg.info.origin.position.x, msg.info.origin.position.y)
        self.latest_grid = np.array(msg.data, dtype=np.int8).reshape((msg.info.height, msg.info.width))

    def odom_callback(self, msg: Odometry):
        """Tracks robot world position."""
        self.robot_pose = (msg.pose.pose.position.x, msg.pose.pose.position.y)

    def timer_evaluation_loop(self):
        """Periodic evaluation loop when autonomous exploration is active and idle."""
        if not self.is_active or self.latest_grid is None or not self.motors_allowed:
            return

        # In modalità HUNT con bersaglio agganciato, non inviare altre frontiere
        if self.mode == "HUNT" and self.target_acquired:
            return

        # Watchdog anti-stallo su goal attivo
        if self.current_goal_handle is not None:
            if self.goal_start_time and (time.time() - self.goal_start_time > self.max_goal_duration):
                self.get_logger().warn(f"⚠️ Watchdog Nav2 Goal Timeout ({self.max_goal_duration}s superati)! Preempting goal e blacklist...")
                self.current_goal_handle.cancel_goal_async()
                if self.engine.dispatched_goals:
                    last_g = self.engine.dispatched_goals[-1]
                    self.engine.blacklist_frontier(last_g, reason="GOAL_TIMEOUT")
                self.current_goal_handle = None
                self.goal_start_time = None
                self._execute_evaluation_step()
            return

        self._execute_evaluation_step()

    def _execute_evaluation_step(self) -> Dict[str, Any]:
        """Runs one evaluation step with semantic bias and acts on the decision."""
        eval_res = self.engine.evaluate_frontiers(
            self.latest_grid,
            self.robot_pose,
            semantic_landmarks=self.semantic_landmarks,
            search_target=self.search_target
        )

        status_payload = {
            "timestamp": time.time(),
            "status": eval_res["status"],
            "is_active": self.is_active,
            "reason": eval_res["reason"],
            "max_size_meters": eval_res["max_size_meters"],
            "cluster_count": eval_res["cluster_count"],
            "selected_goal": eval_res["selected_goal"],
            "motor_stop_required": eval_res["motor_stop_required"],
            "mode": self.mode,
            "search_target": self.search_target,
            "target_acquired": self.target_acquired,
            "target_reached": self.target_reached,
        }
        status_msg = String()
        status_msg.data = json.dumps(status_payload)
        self.status_pub.publish(status_msg)

        if eval_res["status"] == "COMPLETED":
            self.get_logger().info(f"🏁 Exploration complete: {eval_res['reason']}. Stopping motors.")
            self.stop_robot_motors()
            self.is_active = False
        elif eval_res["status"] == "EXPLORING" and eval_res["selected_goal"]:
            if self.motors_allowed:
                goal = eval_res["selected_goal"]
                self.dispatch_nav2_goal(goal)
            else:
                self.get_logger().warn("Exploration dispatch paused: motors_allowed is False.")

        return eval_res

    def dispatch_nav2_goal(self, goal_xy: Tuple[float, float]):
        """Dispatches goal pose to Nav2 action server."""
        if not self.nav_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error("Nav2 Action Server (/navigate_to_pose) unavailable!")
            return

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id = "map"
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = float(goal_xy[0])
        goal_msg.pose.pose.position.y = float(goal_xy[1])

        # Compute heading facing toward the frontier
        yaw = math.atan2(goal_xy[1] - self.robot_pose[1], goal_xy[0] - self.robot_pose[0])
        goal_msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal_msg.pose.pose.orientation.w = math.cos(yaw / 2.0)

        self.get_logger().info(f"🚀 Dispatching frontier goal to ({goal_xy[0]:.2f}, {goal_xy[1]:.2f}) [Mode: {self.mode}]")
        self.goal_start_time = time.time()
        send_future = self.nav_client.send_goal_async(goal_msg)
        send_future.add_done_callback(lambda f: self.goal_response_callback(f, goal_xy))

    def goal_response_callback(self, future, goal_xy: Tuple[float, float]):
        """Handles Nav2 goal acceptance or rejection."""
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().warn(f"Goal to {goal_xy} rejected by Nav2. Blacklisting...")
            self.engine.blacklist_frontier(goal_xy)
            self.current_goal_handle = None
            self.goal_start_time = None
            self._execute_evaluation_step()
            return

        self.current_goal_handle = goal_handle
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(lambda f: self.goal_result_callback(f, goal_xy))

    def goal_result_callback(self, future, goal_xy: Tuple[float, float]):
        """Handles completion of Nav2 goal."""
        self.current_goal_handle = None
        self.goal_start_time = None
        result = future.result()
        status = result.status

        if status == GoalStatus.STATUS_SUCCEEDED:
            self.get_logger().info(f"✅ Reached frontier goal {goal_xy}. Evaluating next frontiers...")
        else:
            self.get_logger().warn(f"⚠️ Failed to reach frontier {goal_xy} (status: {status}). Blacklisting...")
            self.engine.blacklist_frontier(goal_xy)

        # Trigger immediate next evaluation cycle
        if self.is_active:
            self._execute_evaluation_step()

    def stop_robot_motors(self):
        """Sends zero-velocity Twist command to halt motors immediately."""
        stop_cmd = Twist()
        for _ in range(3):
            self.cmd_vel_pub.publish(stop_cmd)

    def handle_start(self, request, response):
        if not self.motors_allowed:
            self.get_logger().warn("Cannot start exploration: /mapping/motors_allowed is False!")
            response.success = False
            response.message = "Exploration inhibited: motors not allowed by safety interlock."
            return response
        self.is_active = True
        self.get_logger().info("Frontier exploration started via service.")
        response.success = True
        response.message = "Exploration activated."
        return response

    def handle_stop(self, request, response):
        self.is_active = False
        if self.current_goal_handle is not None:
            self.current_goal_handle.cancel_goal_async()
            self.current_goal_handle = None
        self.stop_robot_motors()
        self.get_logger().info("Frontier exploration stopped via service.")
        response.success = True
        response.message = "Exploration halted."
        return response

    def handle_evaluate(self, request, response):
        if self.latest_grid is None:
            response.success = False
            response.message = "No occupancy grid received yet."
            return response
        eval_res = self._execute_evaluation_step()
        response.success = True
        response.message = f"Status: {eval_res['status']} - {eval_res['reason']}"
        return response

    def handle_clear_blacklist(self, request, response):
        self.engine.clear_blacklist()
        response.success = True
        response.message = "Blacklist cleared."
        return response


def main(args=None):
    if not HAS_ROS2:
        print("ROS 2 is not available. Exiting.")
        return

    rclpy.init(args=args)
    node = FrontierExplorerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
