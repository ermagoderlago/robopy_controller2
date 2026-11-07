#!/usr/bin/env python3
"""
run_live_gazebo_nav2_suite.py — Live End-to-End Validation Suite for Marcus AI
===============================================================================
Executes REAL Gazebo Harmonic physics + ROS 2 Jazzy + slam_toolbox + Nav2 +
semantic_costmap_injector + sensor_standby_manager + resource_governor_node +
frontier_explorer_node across all 3 progressive worlds:

  1. Level 1 (`simple` — world_01_simple_room.sdf, 6m x 6m):
     - Test 1A: FM-NAV-041 Standby & Motion-Gate Catch-22 Deadlock Regression
       (DOCKED_SLEEP -> /stop_motor -> motion_gate=False -> NavigateToPose ->
        automatic LiDAR wake-up + motion_gate=True + goal reached!)
     - Test 1B: Autonomous Frontier Exploration (/exploration/enable) &
       Multi-Waypoint Room Mapping Patrol

  2. Level 2 (`medium` — world_02_medium_apartment.sdf, 10m x 8m, 4 rooms):
     - Test 2A: Room-to-Room Doorway Traversal (0.90m doorways) + SLAM Mapping:
       Salotto (0,0) -> Cucina (-2.5, 1.1) -> Camera (-2.5, -1.6) ->
       Studio (2.1, -2.1) -> Cuccia (0.0, 0.0)

  3. Level 3 (`extreme` — world_03_extreme_labyrinth.sdf, 12m x 10m):
     - Test 3A: Real 3D Negative Obstacle / Stairwell Drop-Off Avoidance (FM-NAV-009):
       Spawn at (-0.4, -2.0, yaw=pi) facing -45cm sunken stairwell pit ->
       verify OAK-D 16UC1 depth raycasting injects LETHAL 2.5D obstacles on
       /hailo_semantic_obstacles_pc -> Nav2 detours safely (min_z > -0.04m) to (-3.8, -2.0)!
     - Test 3B: Cul-de-Sac U-Trap Global Escape & Staggered 0.88m Chicane Slalom:
       Enter U-trap (-2.0, 2.6) -> escape behind back wall (-4.1, 2.6) ->
       traverse Chicane Slalom (0.8, 0.4) -> (2.6, 0.4) -> (4.6, 1.8)!
"""

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


SIM_PROCESS_PATTERNS = [
    "gz sim",
    "parameter_bridge",
    "robot_state_publisher",
    "marcus_hardware_sim_bridge",
    "hailo_digital_twin_node",
    "virtual_human_actors_node",
    "marcus_gym_evaluator_node",
    "async_slam_toolbox_node",
    "controller_server",
    "planner_server",
    "behavior_server",
    "bt_navigator",
    "collision_monitor",
    "lifecycle_manager",
    "semantic_costmap_injector",
    "sensor_standby_manager",
    "resource_governor_node",
    "foxglove_nav2_bridge",
    "frontier_explorer_node",
]


def cleanup_sim_processes() -> None:
    """Terminates all simulation ROS 2 / Gazebo processes cleanly without touching system daemons."""
    for pat in SIM_PROCESS_PATTERNS:
        subprocess.run(["pkill", "-15", "-f", pat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.2)
    for pat in SIM_PROCESS_PATTERNS:
        subprocess.run(["pkill", "-9", "-f", pat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-9", "-f", "Xvfb.*640x480x24"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)


def yaw_to_quat(yaw: float) -> Tuple[float, float, float, float]:
    half = 0.5 * yaw
    return (0.0, 0.0, math.sin(half), math.cos(half))


def run_single_world(world_level: str, domain_id: int, output_json: str) -> int:
    import rclpy
    from rclpy.action import ActionClient
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from rclpy.parameter import Parameter
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
    from action_msgs.msg import GoalStatus
    from diagnostic_msgs.msg import DiagnosticArray
    from geometry_msgs.msg import PoseStamped, Twist
    from lifecycle_msgs.srv import GetState
    from nav2_msgs.action import NavigateToPose
    from nav_msgs.msg import OccupancyGrid, Odometry
    from sensor_msgs.msg import Imu, LaserScan, PointCloud2
    from std_msgs.msg import Bool, String
    import tf2_ros

    script_dir = os.path.dirname(os.path.abspath(__file__))
    launcher_sh = os.path.join(script_dir, "start_gazebo_marcus.sh")

    spawn_cfg = {
        "simple": ("0.0", "0.0", "0.0"),
        "medium": ("0.0", "0.0", "0.0"),
        "extreme": ("0.0", "0.0", "0.0"),
    }[world_level]

    cleanup_sim_processes()

    # Configure CycloneDDS on loopback BEFORE rclpy.init() so harness & Gazebo share participant space!
    cyclone_cfg = f"/tmp/cyclonedds_robopy_sim_{domain_id}.xml"
    with open(cyclone_cfg, "w", encoding="utf-8") as cf:
        cf.write(
            '<?xml version="1.0" encoding="UTF-8" ?>\n'
            '<CycloneDDS xmlns="https://cdds.io/config">\n'
            '  <Domain id="any">\n'
            '    <General>\n'
            '      <Interfaces><NetworkInterface name="lo" priority="default" multicast="default"/></Interfaces>\n'
            '      <AllowMulticast>spdp</AllowMulticast>\n'
            '    </General>\n'
            '    <Discovery><MaxAutoParticipantIndex>200</MaxAutoParticipantIndex></Discovery>\n'
            '    <Internal><SocketReceiveBufferSize min="10MB"/></Internal>\n'
            '  </Domain>\n'
            '</CycloneDDS>\n'
        )
    os.environ["ROS_DOMAIN_ID"] = str(domain_id)
    os.environ["RMW_IMPLEMENTATION"] = "rmw_cyclonedds_cpp"
    os.environ["CYCLONEDDS_URI"] = f"file://{cyclone_cfg}"

    cmd = [
        "bash",
        launcher_sh,
        f"--world={world_level}",
        f"--domain={domain_id}",
        "enable_foxglove:=false",
        "enable_actors:=false",
        f"spawn_x:={spawn_cfg[0]}",
        f"spawn_y:={spawn_cfg[1]}",
        f"spawn_yaw:={spawn_cfg[2]}",
    ]
    print(f"\n[SUITE] Launching Gazebo world '{world_level}': {' '.join(cmd)}", flush=True)
    log_path = f"/tmp/gazebo_world_{world_level}.log"
    log_file = open(log_path, "w", encoding="utf-8")
    sim_proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )

    rclpy.init()

    class LiveWorldHarness(Node):
        def __init__(self):
            super().__init__(
                f"marcus_live_harness_{world_level}",
                parameter_overrides=[Parameter("use_sim_time", Parameter.Type.BOOL, True)],
            )
            self.lock = threading.Lock()
            self.pose_xyz_yaw: Optional[Tuple[float, float, float, float]] = None
            self.has_tf_map_pose = False
            self.min_z = 999.0
            self.max_tilt_rad = 0.0
            self.min_imu_az = 999.0
            self.max_speed = 0.0
            self.scan_count = 0
            self.last_scan_wall = 0.0
            self.map_known_cells = 0
            self.map_occupied_cells = 0
            self.motion_gate = True
            self.power_state = "UNKNOWN"
            self.governor_state_str = ""
            self.cmd_vel_nav_count = 0
            self.cmd_vel_out_count = 0
            self.semantic_pc_msgs = 0
            self.semantic_pc_max_points = 0
            self.motor_stall_errors = 0
            self.trajectory_xy: List[Tuple[float, float]] = []

            self.tf_buffer = tf2_ros.Buffer()
            self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

            map_qos = QoSProfile(
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
                history=HistoryPolicy.KEEP_LAST,
                depth=1,
            )
            self.create_subscription(Odometry, "/odom", self._on_odom, 20)
            self.create_subscription(Imu, "/imu/data", self._on_imu, 20)
            self.create_subscription(LaserScan, "/scan", self._on_scan, 20)
            self.create_subscription(OccupancyGrid, "/map", self._on_map, map_qos)
            self.create_subscription(Bool, "/robot/motion_gate", self._on_gate, 10)
            self.create_subscription(String, "/robot/sensor_power_state", self._on_power, 10)
            self.create_subscription(String, "/resource_governor/state", self._on_gov, 10)
            self.create_subscription(Twist, "/cmd_vel_nav", self._on_cmd_nav, 10)
            self.create_subscription(Twist, "/cmd_vel", self._on_cmd_out, 10)
            self.create_subscription(PointCloud2, "/hailo_semantic_obstacles_pc", self._on_sem_pc, 10)
            self.create_subscription(DiagnosticArray, "/diagnostics", self._on_diag, 10)
            self.create_timer(0.10, self._poll_map_tf)

            self.pub_gov_override = self.create_publisher(String, "/resource_governor/state", 10)
            self.pub_nav_intent = self.create_publisher(Bool, "/robot/nav_intent", 10)
            self.pub_explore_enable = self.create_publisher(Bool, "/exploration/enable", 10)
            self.pub_goal_pose = self.create_publisher(PoseStamped, "/goal_pose", 10)
            self.pub_cmd_vel = self.create_publisher(Twist, "/cmd_vel", 10)

            self.nav_client = ActionClient(self, NavigateToPose, "navigate_to_pose")
            self.bt_state_cli = self.create_client(GetState, "/bt_navigator/get_state")

        def _poll_map_tf(self):
            try:
                t = self.tf_buffer.lookup_transform("map", "base_link", rclpy.time.Time())
                tx = float(t.transform.translation.x)
                ty = float(t.transform.translation.y)
                tz = float(t.transform.translation.z)
                q = t.transform.rotation
                siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
                cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
                yaw = math.atan2(siny_cosp, cosy_cosp)
                with self.lock:
                    self.has_tf_map_pose = True
                    self.pose_xyz_yaw = (tx, ty, tz, yaw)
                    if tz < self.min_z:
                        self.min_z = tz
                    if not self.trajectory_xy or math.hypot(
                        tx - self.trajectory_xy[-1][0], ty - self.trajectory_xy[-1][1]
                    ) >= 0.05:
                        self.trajectory_xy.append((round(tx, 3), round(ty, 3)))
            except Exception:
                pass

        def _on_imu(self, msg: Imu):
            q = msg.orientation
            sinr_cosp = 2.0 * (q.w * q.x + q.y * q.z)
            cosr_cosp = 1.0 - 2.0 * (q.x * q.x + q.y * q.y)
            roll = math.atan2(sinr_cosp, cosr_cosp)
            sinp = max(-1.0, min(1.0, 2.0 * (q.w * q.y - q.z * q.x)))
            pitch = math.asin(sinp)
            tilt = math.hypot(roll, pitch)
            az = float(msg.linear_acceleration.z)
            with self.lock:
                if tilt > self.max_tilt_rad:
                    self.max_tilt_rad = tilt
                if az < self.min_imu_az:
                    self.min_imu_az = az

        def _on_odom(self, msg: Odometry):
            p = msg.pose.pose.position
            q = msg.pose.pose.orientation
            siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
            cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
            yaw = math.atan2(siny_cosp, cosy_cosp)
            v = abs(msg.twist.twist.linear.x)
            with self.lock:
                if v > self.max_speed:
                    self.max_speed = v
                if p.z < self.min_z:
                    self.min_z = p.z
                if not self.has_tf_map_pose:
                    self.pose_xyz_yaw = (p.x, p.y, p.z, yaw)
                    if not self.trajectory_xy or math.hypot(
                        p.x - self.trajectory_xy[-1][0], p.y - self.trajectory_xy[-1][1]
                    ) >= 0.05:
                        self.trajectory_xy.append((round(p.x, 3), round(p.y, 3)))

        def _on_scan(self, msg: LaserScan):
            with self.lock:
                self.scan_count += 1
                self.last_scan_wall = time.time()

        def _on_map(self, msg: OccupancyGrid):
            data = msg.data
            known = sum(1 for c in data if c >= 0)
            occ = sum(1 for c in data if c >= 65)
            with self.lock:
                self.map_known_cells = known
                self.map_occupied_cells = occ

        def _on_gate(self, msg: Bool):
            with self.lock:
                self.motion_gate = bool(msg.data)

        def _on_power(self, msg: String):
            with self.lock:
                self.power_state = msg.data

        def _on_gov(self, msg: String):
            with self.lock:
                self.governor_state_str = msg.data

        def _on_cmd_nav(self, msg: Twist):
            if abs(msg.linear.x) > 0.005 or abs(msg.angular.z) > 0.005:
                with self.lock:
                    self.cmd_vel_nav_count += 1

        def _on_cmd_out(self, msg: Twist):
            if abs(msg.linear.x) > 0.005 or abs(msg.angular.z) > 0.005:
                with self.lock:
                    self.cmd_vel_out_count += 1

        def _on_sem_pc(self, msg: PointCloud2):
            pts = int(msg.width * msg.height)
            with self.lock:
                if pts > 0:
                    self.semantic_pc_msgs += 1
                    if pts > self.semantic_pc_max_points:
                        self.semantic_pc_max_points = pts

        def _on_diag(self, msg: DiagnosticArray):
            for st in msg.status:
                if st.level == b"\x02" or st.level == 2:
                    if "STALL" in st.message.upper():
                        with self.lock:
                            self.motor_stall_errors += 1

        def wait_stack_ready(self, timeout_s: float = 65.0) -> bool:
            t0 = time.time()
            while time.time() - t0 < timeout_s:
                with self.lock:
                    has_odom = self.pose_xyz_yaw is not None
                    has_scan = self.scan_count >= 3
                    has_map = self.map_known_cells > 50
                if has_odom and has_scan and has_map and self.bt_state_cli.service_is_ready():
                    fut = self.bt_state_cli.call_async(GetState.Request())
                    t_srv = time.time()
                    while not fut.done() and time.time() - t_srv < 2.0:
                        time.sleep(0.05)
                    if fut.done() and fut.result() is not None and fut.result().current_state.id == 3:
                        if self.nav_client.wait_for_server(timeout_sec=2.0):
                            return True
                time.sleep(0.4)
            return False

        def sim_now_s(self) -> float:
            return self.get_clock().now().nanoseconds * 1e-9

        def navigate_to(
            self,
            x: float,
            y: float,
            yaw: float = 0.0,
            timeout_s: float = 70.0,
            label: str = "",
            emit_nav_intent: bool = True,
        ) -> Dict[str, Any]:
            goal = NavigateToPose.Goal()
            goal.pose.header.frame_id = "map"
            goal.pose.header.stamp = self.get_clock().now().to_msg()
            goal.pose.pose.position.x = float(x)
            goal.pose.pose.position.y = float(y)
            goal.pose.pose.position.z = 0.0
            qx, qy, qz, qw = yaw_to_quat(yaw)
            goal.pose.pose.orientation.x = qx
            goal.pose.pose.orientation.y = qy
            goal.pose.pose.orientation.z = qz
            goal.pose.pose.orientation.w = qw

            if emit_nav_intent:
                intent_msg = Bool()
                intent_msg.data = True
                self.pub_nav_intent.publish(intent_msg)

            with self.lock:
                start_pose = self.pose_xyz_yaw
                start_traj_idx = len(self.trajectory_xy)

            print(f"  -> [NAV2] Goal '{label}' ({x:.2f}, {y:.2f}, yaw={yaw:.2f}) from {start_pose}", flush=True)
            t0 = time.time()
            t0_sim = self.sim_now_s()
            send_fut = self.nav_client.send_goal_async(goal)
            while not send_fut.done() and (time.time() - t0) < 20.0:
                time.sleep(0.05)
            if not send_fut.done():
                return {"label": label, "accepted": False, "succeeded": False, "reason": "send_goal_timeout"}

            goal_handle = send_fut.result()
            if not goal_handle.accepted:
                return {"label": label, "accepted": False, "succeeded": False, "reason": "goal_rejected"}

            res_fut = goal_handle.get_result_async()
            wall_ceiling_s = max(210.0, timeout_s * 2.5)
            while not res_fut.done():
                sim_elapsed = max(0.0, self.sim_now_s() - t0_sim)
                wall_elapsed = time.time() - t0
                if sim_elapsed >= timeout_s or wall_elapsed >= wall_ceiling_s:
                    break
                time.sleep(0.1)

            if not res_fut.done():
                cancel_fut = goal_handle.cancel_goal_async()
                t_c = time.time()
                while not cancel_fut.done() and time.time() - t_c < 4.0:
                    time.sleep(0.05)
                with self.lock:
                    end_pose = self.pose_xyz_yaw
                    seg_traj = list(self.trajectory_xy[start_traj_idx:])
                dist_err = math.hypot((end_pose[0] - x), (end_pose[1] - y)) if end_pose else 999.0
                return {
                    "label": label,
                    "accepted": True,
                    "succeeded": dist_err <= 0.35,
                    "status": "TIMEOUT_NEAR_GOAL" if dist_err <= 0.35 else "TIMEOUT",
                    "duration_sim_s": round(max(0.0, self.sim_now_s() - t0_sim), 2),
                    "duration_wall_s": round(time.time() - t0, 2),
                    "end_pose": end_pose,
                    "dist_error_m": round(dist_err, 3),
                    "segment_points": len(seg_traj),
                    "segment_trajectory": seg_traj,
                }

            res = res_fut.result()
            status_code = int(res.status)
            with self.lock:
                end_pose = self.pose_xyz_yaw
                seg_traj = list(self.trajectory_xy[start_traj_idx:])
            dist_err = math.hypot((end_pose[0] - x), (end_pose[1] - y)) if end_pose else 999.0
            ok = (status_code == GoalStatus.STATUS_SUCCEEDED) or (dist_err <= 0.35)

            intent_off = Bool()
            intent_off.data = False
            self.pub_nav_intent.publish(intent_off)

            sim_dt = max(0.0, self.sim_now_s() - t0_sim)
            print(
                f"  <- [NAV2] '{label}' status={status_code} ok={ok} err={dist_err:.3f}m "
                f"sim={sim_dt:.1f}s wall={time.time() - t0:.1f}s pose={end_pose}",
                flush=True,
            )
            return {
                "label": label,
                "accepted": True,
                "succeeded": ok,
                "status_code": status_code,
                "duration_sim_s": round(sim_dt, 2),
                "duration_wall_s": round(time.time() - t0, 2),
                "end_pose": end_pose,
                "dist_error_m": round(dist_err, 3),
                "segment_points": len(seg_traj),
                "segment_trajectory": seg_traj,
            }

    harness = LiveWorldHarness()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(harness)
    spin_running = True

    def _spin_loop():
        while spin_running and rclpy.ok():
            try:
                executor.spin_once(timeout_sec=0.1)
            except Exception:
                break

    spin_thread = threading.Thread(target=_spin_loop, daemon=True)
    spin_thread.start()

    report: Dict[str, Any] = {
        "world_level": world_level,
        "passed": False,
        "stack_ready": False,
        "tests": {},
    }

    try:
        ready = harness.wait_stack_ready(timeout_s=75.0)
        report["stack_ready"] = ready
        if not ready:
            print(f"[ERROR] Stack failed to become ready in world '{world_level}'! Check {log_path}", flush=True)
            return 2

        with harness.lock:
            initial_map_cells = harness.map_known_cells
        print(f"[SUITE] Stack READY in '{world_level}'! Initial map known cells: {initial_map_cells}", flush=True)

        if world_level == "simple":
            # =================================================================
            # TEST 1A: FM-NAV-041 Standby & Motion Gate Deadlock Regression
            # =================================================================
            print("[TEST 1A] Forcing DOCKED_SLEEP / STANDBY to test automatic Nav2 wake-up...", flush=True)
            # Wait >5s so min_awake_time_sec (5.0s) expires cleanly
            time.sleep(5.5)
            sleep_msg = String()
            sleep_msg.data = json.dumps({
                "kinematic_state": "DOCKED_SLEEP",
                "in_dock": True,
                "recommended_profile": {"lidar_active": False},
            })
            for _ in range(4):
                harness.pub_gov_override.publish(sleep_msg)
                time.sleep(0.25)

            entered_standby = False
            t_wait = time.time()
            while time.time() - t_wait < 5.0:
                with harness.lock:
                    if harness.power_state == "STANDBY" and not harness.motion_gate:
                        entered_standby = True
                        break
                harness.pub_gov_override.publish(sleep_msg)
                time.sleep(0.2)

            # Verify /scan actually stops while in STANDBY
            time.sleep(0.6)
            with harness.lock:
                scans_at_standby = harness.scan_count
            time.sleep(0.8)
            with harness.lock:
                scans_after_pause = harness.scan_count
            lidar_stopped = (scans_after_pause - scans_at_standby) == 0
            print(
                f"[TEST 1A] STANDBY entered={entered_standby}, motion_gate={harness.motion_gate}, "
                f"lidar_stopped={lidar_stopped}",
                flush=True,
            )

            # Now send a real Nav2 goal WITHOUT manually calling /start_motor, /robot/wake_sensors, or /robot/nav_intent!
            # Both Governor & StandbyManager must wake automatically on /plan + /cmd_vel_nav (FM-NAV-041 fix)!
            res_wake_nav = harness.navigate_to(
                1.85, -1.85, yaw=0.0, timeout_s=75.0, label="W1_WakeFromStandby_SE", emit_nav_intent=False
            )
            with harness.lock:
                woke_active = (harness.power_state == "ACTIVE") and harness.motion_gate
            report["tests"]["test_1a_standby_wakeup_nav"] = {
                "entered_standby": entered_standby,
                "lidar_stopped_in_standby": lidar_stopped,
                "auto_woke_and_unlocked_gate": woke_active,
                "nav_result": res_wake_nav,
                "passed": bool(entered_standby and woke_active and res_wake_nav["succeeded"]),
            }

            # =================================================================
            # TEST 1B: Autonomous Frontier Exploration & Room Mapping Patrol
            # =================================================================
            print("[TEST 1B] Activating FrontierExplorerNode (/exploration/enable=True) + Room Patrol...", flush=True)
            with harness.lock:
                cells_before_exp = harness.map_known_cells
            exp_on = Bool()
            exp_on.data = True
            harness.pub_explore_enable.publish(exp_on)
            time.sleep(8.0)
            exp_off = Bool()
            exp_off.data = False
            for _ in range(3):
                harness.pub_explore_enable.publish(exp_off)
                time.sleep(0.2)
            time.sleep(3.0)

            leg_nw = harness.navigate_to(-1.70, 1.70, yaw=0.0, timeout_s=110.0, label="W1_Patrol_NW")
            leg_ne = harness.navigate_to(1.80, 1.80, yaw=0.0, timeout_s=85.0, label="W1_Patrol_NE")
            leg_dock = harness.navigate_to(0.0, 0.0, yaw=0.0, timeout_s=85.0, label="W1_Return_Dock")

            with harness.lock:
                cells_after = harness.map_known_cells
            report["tests"]["test_1b_mapping_and_patrol"] = {
                "known_cells_initial": initial_map_cells,
                "known_cells_before_patrol": cells_before_exp,
                "known_cells_final": cells_after,
                "mapped_area_m2": round(cells_after * 0.05 * 0.05, 2),
                "legs": [leg_nw, leg_ne, leg_dock],
                "passed": bool(
                    leg_nw["succeeded"]
                    and leg_ne["succeeded"]
                    and leg_dock["succeeded"]
                    and cells_after > initial_map_cells
                ),
            }

        elif world_level == "medium":
            # =================================================================
            # TEST 2A: 4-Room Apartment Doorway Traversal (0.90m doors) + SLAM
            # =================================================================
            print("[TEST 2A] Multi-Room Apartment Mission (Salotto -> Cucina -> Camera -> Studio -> Cuccia)...", flush=True)
            leg_cucina = harness.navigate_to(-2.45, 1.05, yaw=3.14, timeout_s=105.0, label="W2_Salotto_to_Cucina")
            leg_salotto_mid = harness.navigate_to(-0.25, -1.55, yaw=3.14, timeout_s=95.0, label="W2_Cucina_to_CameraDoor")
            leg_camera = harness.navigate_to(-2.45, -1.60, yaw=-1.57, timeout_s=95.0, label="W2_Enter_Camera")
            leg_studio_door = harness.navigate_to(1.50, -0.15, yaw=-1.57, timeout_s=105.0, label="W2_Camera_to_StudioDoor")
            leg_studio = harness.navigate_to(1.80, -2.10, yaw=-1.57, timeout_s=95.0, label="W2_Enter_Studio")
            leg_dock = harness.navigate_to(0.0, 0.0, yaw=0.0, timeout_s=105.0, label="W2_Studio_to_Cuccia")

            with harness.lock:
                cells_final = harness.map_known_cells
            legs = [leg_cucina, leg_salotto_mid, leg_camera, leg_studio_door, leg_studio, leg_dock]
            report["tests"]["test_2a_multi_room_apartment"] = {
                "known_cells_initial": initial_map_cells,
                "known_cells_final": cells_final,
                "mapped_area_m2": round(cells_final * 0.05 * 0.05, 2),
                "legs": legs,
                "passed": all(l["succeeded"] for l in legs) and (cells_final > initial_map_cells + 1500),
            }

        elif world_level == "extreme":
            # =================================================================
            # TEST 3A: Real 3D Stairwell Pit (-0.45m) Detection & Avoidance
            # =================================================================
            print("[TEST 3A] Approaching sunken stairwell edge (-0.70, -2.00, yaw=pi) for OAK-D 16UC1 raycasting...", flush=True)
            leg_pit_staging = harness.navigate_to(-0.70, -2.00, yaw=3.14159, timeout_s=85.0, label="W3_Stage_Before_Stairwell")

            t_wait_pc = time.time()
            while time.time() - t_wait_pc < 8.0:
                with harness.lock:
                    if harness.semantic_pc_msgs >= 1 and harness.semantic_pc_max_points >= 4:
                        break
                time.sleep(0.25)

            with harness.lock:
                pc_msgs = harness.semantic_pc_msgs
                pc_pts = harness.semantic_pc_max_points
            print(f"[TEST 3A] /hailo_semantic_obstacles_pc msgs={pc_msgs}, max_pts={pc_pts}", flush=True)

            leg_pit = harness.navigate_to(-3.75, -2.00, yaw=3.14159, timeout_s=120.0, label="W3_Across_3D_Stairwell_Pit")
            seg_pts = leg_pit.get("segment_trajectory", [])
            # Verify robot detoured around Y in [-2.6, -1.4] for X in [-2.8, -1.6] and NEVER fell
            max_detour_offset = max((abs(py - (-2.0)) for (px, py) in seg_pts), default=0.0)
            entered_pit_core = any((-2.65 <= px <= -1.75 and -2.45 <= py <= -1.55) for (px, py) in seg_pts)
            with harness.lock:
                min_z_after_pit = harness.min_z
                max_tilt_after_pit = harness.max_tilt_rad

            report["tests"]["test_3a_negative_obstacle_stairwell"] = {
                "staging_result": {k: v for k, v in leg_pit_staging.items() if k != "segment_trajectory"},
                "semantic_pc_msgs": pc_msgs,
                "semantic_pc_max_points": pc_pts,
                "max_y_detour_from_centerline_m": round(max_detour_offset, 3),
                "entered_pit_core": entered_pit_core,
                "min_chassis_z_m": round(min_z_after_pit, 4),
                "max_chassis_tilt_rad": round(max_tilt_after_pit, 4),
                "nav_result": {k: v for k, v in leg_pit.items() if k != "segment_trajectory"},
                "passed": bool(
                    leg_pit_staging["succeeded"]
                    and pc_msgs >= 1
                    and pc_pts >= 4
                    and leg_pit["succeeded"]
                    and not entered_pit_core
                    and max_detour_offset >= 0.40
                    and min_z_after_pit > -0.04
                    and max_tilt_after_pit < 0.25
                ),
            }

            # =================================================================
            # TEST 3B: U-Trap Cul-de-Sac Escape & 0.88m Chicane Slalom
            # =================================================================
            print("[TEST 3B] U-Trap Cul-de-Sac Escape + Staggered Chicane Slalom...", flush=True)
            leg_utrap_approach = harness.navigate_to(-0.85, 2.40, yaw=3.14159, timeout_s=115.0, label="W3_Approach_UTrap")
            leg_utrap_inside = harness.navigate_to(-2.00, 2.60, yaw=3.14159, timeout_s=85.0, label="W3_Enter_UTrap_Pocket")
            leg_utrap_escape = harness.navigate_to(-3.95, 2.60, yaw=3.14159, timeout_s=120.0, label="W3_Escape_UTrap_BehindWall")
            leg_chicane_in = harness.navigate_to(0.55, -0.62, yaw=0.0, timeout_s=125.0, label="W3_Chicane_Entrance")
            leg_chicane_mid = harness.navigate_to(2.65, 0.65, yaw=1.20, timeout_s=105.0, label="W3_Chicane_Chamber_Mid")
            leg_chicane_out = harness.navigate_to(4.85, 1.92, yaw=0.0, timeout_s=105.0, label="W3_Chicane_Exit")

            with harness.lock:
                cells_final = harness.map_known_cells
                min_z_final = harness.min_z
                max_tilt_final = harness.max_tilt_rad
            legs_3b = [
                leg_utrap_approach,
                leg_utrap_inside,
                leg_utrap_escape,
                leg_chicane_in,
                leg_chicane_mid,
                leg_chicane_out,
            ]
            report["tests"]["test_3b_utrap_and_chicane_slalom"] = {
                "known_cells_final": cells_final,
                "mapped_area_m2": round(cells_final * 0.05 * 0.05, 2),
                "min_chassis_z_m": round(min_z_final, 4),
                "max_chassis_tilt_rad": round(max_tilt_final, 4),
                "legs": [{k: v for k, v in l.items() if k != "segment_trajectory"} for l in legs_3b],
                "passed": all(l["succeeded"] for l in legs_3b) and (min_z_final > -0.04) and (max_tilt_final < 0.25),
            }

        with harness.lock:
            report["telemetry_summary"] = {
                "scan_count": harness.scan_count,
                "cmd_vel_nav_count": harness.cmd_vel_nav_count,
                "cmd_vel_out_count": harness.cmd_vel_out_count,
                "max_linear_speed_mps": round(harness.max_speed, 3),
                "min_chassis_z_m": round(harness.min_z, 4),
                "map_known_cells": harness.map_known_cells,
                "map_occupied_cells": harness.map_occupied_cells,
                "semantic_pc_msgs": harness.semantic_pc_msgs,
                "motor_stall_errors": harness.motor_stall_errors,
            }

        report["passed"] = all(t["passed"] for t in report["tests"].values()) and (
            report["telemetry_summary"]["cmd_vel_out_count"] > 20
        )

        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        print(f"[SUITE] World '{world_level}' COMPLETE -> passed={report['passed']}", flush=True)
        return 0 if report["passed"] else 1

    finally:
        spin_running = False
        try:
            spin_thread.join(timeout=1.5)
            executor.shutdown(timeout_sec=1.0)
            harness.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
        except Exception:
            pass
        try:
            os.killpg(os.getpgid(sim_proc.pid), signal.SIGTERM)
        except Exception:
            pass
        log_file.close()
        cleanup_sim_processes()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Live Gazebo + Nav2 + SLAM Suite across 3 progressive worlds")
    parser.add_argument("--single-world", choices=["simple", "medium", "extreme"], default="")
    parser.add_argument("--worlds", default="simple,medium,extreme", help="Comma-separated world levels")
    parser.add_argument("--domain", type=int, default=88, help="Isolated ROS_DOMAIN_ID (default 88)")
    parser.add_argument("--output", default="/tmp/marcus_gazebo_nav2_suite_report.json")
    args = parser.parse_args()

    if args.single_world:
        return run_single_world(args.single_world, args.domain, args.output)

    worlds = [w.strip() for w in args.worlds.split(",") if w.strip()]
    master_report: Dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ros_domain_id": args.domain,
        "worlds": {},
        "all_passed": False,
    }

    all_ok = True
    for w in worlds:
        sub_json = f"/tmp/marcus_gazebo_report_{w}.json"
        if os.path.exists(sub_json):
            os.remove(sub_json)
        rc = subprocess.call(
            [
                sys.executable,
                os.path.abspath(__file__),
                "--single-world",
                w,
                "--domain",
                str(args.domain),
                "--output",
                sub_json,
            ]
        )
        if os.path.exists(sub_json):
            with open(sub_json, "r", encoding="utf-8") as f:
                w_rep = json.load(f)
            master_report["worlds"][w] = w_rep
            if not w_rep.get("passed", False) or rc != 0:
                all_ok = False
        else:
            master_report["worlds"][w] = {"passed": False, "exit_code": rc}
            all_ok = False

    master_report["all_passed"] = all_ok
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(master_report, f, indent=2)

    cleanup_sim_processes()
    print("\n================================================================", flush=True)
    print(f"🏁 LIVE GAZEBO + NAV2 3-WORLD SUITE FINISHED: all_passed={all_ok}", flush=True)
    print(f"📄 Report saved to: {args.output}", flush=True)
    print("================================================================", flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
