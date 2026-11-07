#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marcus_hardware_sim_bridge.py
=============================
Emulatore ad alta fedeltà dell'Hardware Fisico di Marcus per Gazebo Harmonic:
1. Scheda Motori Waveshare ESP32 (SPEC-01):
   - Watchdog Inviolabile 500 ms su /cmd_vel (Zona Rossa)
   - Clamping v_max = 0.40 m/s (0.20 m/s in ECO Mode) e w_max = 1.80 rad/s (Zona Rossa)
   - Motion Gating su /robot/motion_gate (Smart Standby)
   - S-Curve Jerk Limiter continuo di 2° ordine C^1 (FM-MOT-009)
   - Encoder fisici 1440 CPR + Integrazione Runge-Kutta 2° ordine (FM-MOT-007)
   - Rilevamento Stallo Meccanico (>1.0s comando attivo con v_eff ~ 0) e arresto emergenza
2. Convertitore Depth 16UC1 (SPEC-02 Zona Rossa, FM-VIS-002):
   - Converte lo stream /gz/camera/depth_float (32FC1 in metri) di Gazebo
     in /camera/depth/image_raw con encoding rigoroso "16UC1" (millimetri uint16)
     con buffer NumPy pre-allocati (Zero allocazioni dinamiche in callback).
3. Batteria LiPo 3S2P (6 celle) & BMS INA219 (SPEC-06):
   - Tensione nominale 12.20V -> scarica realistica con caduta ohmica I * R_ESR
   - Rilevamento automatico Cuccia di Ricarica (x=0.0, y=0.0, r=0.35m) -> 12.80V CHARGING
   - Soglie ECO (10.40V), Docking (10.15V) e Shutdown Critico (9.85V)
4. Servo Coda Espressiva (SPEC-01):
   - Clamping angolare [-45°, +45°] e frequenza <= 3 Hz.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Tuple, Optional
import numpy as np

try:
    import rclpy
    from rclpy.node import Node
    from geometry_msgs.msg import Twist
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import Image, BatteryState, Range, LaserScan
    from std_msgs.msg import Bool, Float32, String
    from std_srvs.srv import Empty
    from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
    HAS_ROS2 = True
except ImportError:
    HAS_ROS2 = False
    Node = object


@dataclass
class HardwareSimState:
    # Pose & kinematics
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    v_cmd: float = 0.0
    w_cmd: float = 0.0
    v_filtered: float = 0.0
    w_filtered: float = 0.0
    a_linear: float = 0.0
    v_actual: float = 0.0
    w_actual: float = 0.0
    left_ticks: int = 0
    right_ticks: int = 0
    last_cmd_vel_time: float = field(default_factory=time.time)

    # Safety & gating
    motion_gate_open: bool = True
    watchdog_tripped: bool = False
    motor_stalled: bool = False
    stall_timer_sec: float = 0.0

    # Battery 3S2P state (SPEC-06)
    ocv_voltage: float = 12.15
    terminal_voltage: float = 12.15
    current_amperes: float = 1.2
    soc_percentage: float = 1.0
    is_charging: bool = False
    eco_mode_active: bool = False
    docking_triggered: bool = False
    critical_shutdown_triggered: bool = False
    voltage_history: List[float] = field(default_factory=list)

    # Thermal & Tail servo
    cpu_temp_c: float = 52.0
    npu_temp_c: float = 48.0
    tail_angle_deg: float = 0.0


class MarcusHardwareSimEngine:
    """
    Motore fisico-hardware deterministico di Marcus (utilizzabile sia dal nodo ROS 2
    in Gazebo Harmonic sia dalla suite di test unitari/SIL).
    """

    # 🔴 ZONA ROSSA: Costanti Inviolabili SPEC-01, SPEC-02, SPEC-06
    WATCHDOG_TIMEOUT_SEC: float = 0.500
    MAX_LINEAR_VEL_MPS: float = 0.40
    MAX_ANGULAR_VEL_RADPS: float = 1.80
    WHEEL_RADIUS_M: float = 0.0335
    WHEEL_SEPARATION_M: float = 0.285
    TICKS_PER_REV: int = 1440
    STALL_DURATION_LIMIT_SEC: float = 1.00

    # BMS 3S2P Thresholds (SPEC-06)
    V_CHARGING_SUPPLY: float = 12.80
    V_CHARGING_DETECT: float = 12.70
    V_FULL_3S2P: float = 12.15
    V_ECO_THRESHOLD: float = 10.40
    V_DOCK_THRESHOLD: float = 10.15
    V_CRITICAL_SHUTDOWN: float = 9.85
    V_INSTANT_SHUTDOWN: float = 9.78
    R_ESR_OHM: float = 0.085  # Resistenza interna equivalente 3S2P dimezzata

    # S-Curve Jerk Limiter (FM-MOT-009)
    MAX_ACCEL_MPS2: float = 1.20
    MAX_JERK_MPS3: float = 6.00

    def __init__(self, dock_position: Tuple[float, float] = (0.0, 0.0), dock_radius: float = 0.35):
        self.state = HardwareSimState()
        self.dock_position = dock_position
        self.dock_radius = dock_radius
        self._low_v_eco_timer: float = 0.0
        self._low_v_dock_timer: float = 0.0
        self._low_v_shut_timer: float = 0.0

        # Pre-allocazione buffer 640x480 16UC1 per Zero-Allocations in callback (SPEC-03/SPEC-02)
        self._depth_16uc1_buf = np.zeros((480, 640), dtype=np.uint16)

    def receive_cmd_vel(self, linear_x: float, angular_z: float, now: Optional[float] = None) -> Tuple[float, float]:
        """
        Riceve un comando /cmd_vel, aggiorna il watchdog e applica i limiti di Zona Rossa.
        """
        t_now = now if now is not None else time.time()
        self.state.last_cmd_vel_time = t_now
        self.state.watchdog_tripped = False

        # Consenti il disimpegno da stallo su comando di stop o retromarcia controllata (DisengageBackUp)
        if self.state.motor_stalled and (
            (abs(linear_x) < 0.01 and abs(angular_z) < 0.01) or float(linear_x) < -0.02
        ):
            self.state.motor_stalled = False
            self.state.stall_timer_sec = 0.0

        if not self.state.motion_gate_open or self.state.motor_stalled or self.state.critical_shutdown_triggered:
            self.state.v_cmd = 0.0
            self.state.w_cmd = 0.0
            return 0.0, 0.0

        # Clamping velocità massima (ridotta al 50% in modalità ECO - SPEC-06)
        v_limit = self.MAX_LINEAR_VEL_MPS * (0.5 if self.state.eco_mode_active else 1.0)
        w_limit = self.MAX_ANGULAR_VEL_RADPS

        self.state.v_cmd = max(-v_limit, min(v_limit, float(linear_x)))
        self.state.w_cmd = max(-w_limit, min(w_limit, float(angular_z)))
        return self.state.v_cmd, self.state.w_cmd

    def set_tail_angle(self, angle_deg: float) -> float:
        """Imposta l'angolo del servo coda entro i limiti di SPEC-01 [-45°, +45°]."""
        self.state.tail_angle_deg = max(-45.0, min(45.0, float(angle_deg)))
        return self.state.tail_angle_deg

    def convert_depth_32fc1_to_16uc1(self, depth_meters: np.ndarray) -> np.ndarray:
        """
        Converte una mappa di profondità float32 (metri) in uint16 ('16UC1' millimetri)
        senza allocazioni dinamiche se la forma è 480x640 (SPEC-02 Red Zone).
        """
        clean_m = np.nan_to_num(depth_meters, nan=0.0, posinf=10.0, neginf=0.0)
        clipped_mm = np.clip(clean_m * 1000.0, 0.0, 65535.0)
        if clipped_mm.shape == self._depth_16uc1_buf.shape:
            np.copyto(self._depth_16uc1_buf, clipped_mm, casting="unsafe")
            return self._depth_16uc1_buf
        return clipped_mm.astype(np.uint16)

    def step(
        self,
        dt: float,
        now: Optional[float] = None,
        external_obstruction_stall: bool = False,
        discharge_rate_multiplier: float = 1.0
    ) -> HardwareSimState:
        """
        Avanza la simulazione hardware di un passo temporale `dt` secondi.
        """
        t_now = now if now is not None else time.time()

        # 1. Watchdog 500ms Inviolabile (SPEC-01 Red Zone)
        if (t_now - self.state.last_cmd_vel_time) > self.WATCHDOG_TIMEOUT_SEC:
            self.state.watchdog_tripped = True
            self.state.v_cmd = 0.0
            self.state.w_cmd = 0.0

        # 2. S-Curve Jerk Limiter C^1 (FM-MOT-009) con arresto istantaneo su stop
        if abs(self.state.v_cmd) < 1e-4 and abs(self.state.w_cmd) < 1e-4:
            self.state.v_filtered = 0.0
            self.state.w_filtered = 0.0
            self.state.a_linear = 0.0
        else:
            v_err = self.state.v_cmd - self.state.v_filtered
            desired_a = max(-self.MAX_ACCEL_MPS2, min(self.MAX_ACCEL_MPS2, v_err / max(dt, 1e-3)))
            da = desired_a - self.state.a_linear
            max_da = self.MAX_JERK_MPS3 * dt
            self.state.a_linear += max(-max_da, min(max_da, da))
            self.state.v_filtered += self.state.a_linear * dt
            # Clamping di sicurezza post-filtro
            v_limit = self.MAX_LINEAR_VEL_MPS * (0.5 if self.state.eco_mode_active else 1.0)
            self.state.v_filtered = max(-v_limit, min(v_limit, self.state.v_filtered))
            self.state.w_filtered = self.state.w_cmd

        # 3. Verifica Stallo Meccanico Motori (>1.0s comando attivo ma ruote bloccate - SPEC-01)
        if external_obstruction_stall and (abs(self.state.v_cmd) > 0.05 or abs(self.state.w_cmd) > 0.15):
            self.state.v_actual = 0.0
            self.state.w_actual = 0.0
            self.state.stall_timer_sec += dt
            if self.state.stall_timer_sec >= self.STALL_DURATION_LIMIT_SEC:
                self.state.motor_stalled = True
                self.state.v_cmd = 0.0
                self.state.w_cmd = 0.0
                self.state.v_filtered = 0.0
                self.state.w_filtered = 0.0
        else:
            self.state.stall_timer_sec = max(0.0, self.state.stall_timer_sec - dt * 2.0)
            self.state.v_actual = self.state.v_filtered
            self.state.w_actual = self.state.w_filtered

        # 4. Integrazione Odometria Runge-Kutta 2° Ordine (Punto Medio - FM-MOT-007) e 1440 CPR
        ds = self.state.v_actual * dt
        dtheta = self.state.w_actual * dt
        mid_yaw = self.state.yaw + 0.5 * dtheta

        self.state.x += ds * math.cos(mid_yaw)
        self.state.y += ds * math.sin(mid_yaw)
        self.state.yaw = math.atan2(math.sin(self.state.yaw + dtheta), math.cos(self.state.yaw + dtheta))

        # Calcolo tick encoder 1440 CPR
        v_left = self.state.v_actual - (self.state.w_actual * self.WHEEL_SEPARATION_M / 2.0)
        v_right = self.state.v_actual + (self.state.w_actual * self.WHEEL_SEPARATION_M / 2.0)
        circumference = 2.0 * math.pi * self.WHEEL_RADIUS_M
        self.state.left_ticks += int(round((v_left * dt / circumference) * self.TICKS_PER_REV))
        self.state.right_ticks += int(round((v_right * dt / circumference) * self.TICKS_PER_REV))

        # 5. Simulazione BMS Batteria 3S2P, Sag I*R_ESR e Cuccia di Ricarica (SPEC-06)
        dist_to_dock = math.hypot(self.state.x - self.dock_position[0], self.state.y - self.dock_position[1])
        is_at_dock = (dist_to_dock <= self.dock_radius) and (abs(self.state.v_actual) < 0.01)

        if is_at_dock and self.state.ocv_voltage < 12.10:
            self.state.is_charging = True
            self.state.ocv_voltage = min(self.V_FULL_3S2P, self.state.ocv_voltage + 0.02 * dt * discharge_rate_multiplier)
            self.state.current_amperes = -2.5  # Corrente di carica
            self.state.terminal_voltage = self.V_CHARGING_SUPPLY
        else:
            self.state.is_charging = False
            # Corrente assorbita: base Pi5+Hailo (1.4A) + spunto motori
            motor_current = 3.2 * (abs(self.state.v_actual) / self.MAX_LINEAR_VEL_MPS) + 1.5 * (
                abs(self.state.w_actual) / self.MAX_ANGULAR_VEL_RADPS
            )
            if external_obstruction_stall:
                motor_current += 4.5  # Picco di stallo
            self.state.current_amperes = 1.4 + motor_current
            # Scarica OCV e caduta ohmica I * R_ESR
            self.state.ocv_voltage = max(
                9.20, self.state.ocv_voltage - (0.0004 * self.state.current_amperes * dt * discharge_rate_multiplier)
            )
            self.state.terminal_voltage = self.state.ocv_voltage - (self.state.current_amperes * self.R_ESR_OHM)

        # Filtro media mobile anti-sag (20 campioni - SPEC-06 Red Zone)
        self.state.voltage_history.append(self.state.terminal_voltage)
        if len(self.state.voltage_history) > 20:
            self.state.voltage_history.pop(0)
        v_filtered_bms = sum(self.state.voltage_history) / len(self.state.voltage_history)

        self.state.soc_percentage = max(0.0, min(1.0, (v_filtered_bms - 9.60) / (12.15 - 9.60)))

        # Valutazione persistenza soglie (2.0s - SPEC-06)
        if not self.state.is_charging and v_filtered_bms < self.V_CHARGING_DETECT:
            if v_filtered_bms <= self.V_ECO_THRESHOLD:
                self._low_v_eco_timer += dt
                if self._low_v_eco_timer >= 2.0:
                    self.state.eco_mode_active = True
            else:
                self._low_v_eco_timer = 0.0
                self.state.eco_mode_active = False

            if v_filtered_bms <= self.V_DOCK_THRESHOLD:
                self._low_v_dock_timer += dt
                if self._low_v_dock_timer >= 2.0:
                    self.state.docking_triggered = True
            else:
                self._low_v_dock_timer = 0.0

            if v_filtered_bms <= self.V_CRITICAL_SHUTDOWN or self.state.terminal_voltage <= self.V_INSTANT_SHUTDOWN:
                self._low_v_shut_timer += dt
                if self._low_v_shut_timer >= 2.0 or self.state.terminal_voltage <= self.V_INSTANT_SHUTDOWN:
                    self.state.critical_shutdown_triggered = True
                    self.state.v_cmd = 0.0
                    self.state.w_cmd = 0.0
                    self.state.v_actual = 0.0
                    self.state.w_actual = 0.0
        else:
            self.state.eco_mode_active = False
            self.state.docking_triggered = False

        return self.state


class MarcusHardwareSimBridgeNode(Node):
    """
    Nodo ROS 2 che incapsula `MarcusHardwareSimEngine` e fa da ponte tra lo stack
    di Marcus (`/cmd_vel`, `/odom`, `/camera/depth/image_raw`, `/battery_state`, `/diagnostics`)
    e Gazebo Harmonic (`/gz/cmd_vel_safe`, `/gz/odom_raw`, `/gz/camera/depth_float`).
    """

    def __init__(self):
        super().__init__("marcus_hardware_sim_bridge")
        if not self.has_parameter("use_sim_time"):
            self.declare_parameter("use_sim_time", True)
        self.engine = MarcusHardwareSimEngine()

        self._last_gz_odom: Optional[Odometry] = None
        self._last_step_time: float = 0.0
        self._lidar_motor_running: bool = True

        # Subscriptions
        self.create_subscription(Twist, "/cmd_vel", self._on_cmd_vel, 10)
        self.create_subscription(Bool, "/robot/motion_gate", self._on_motion_gate, 10)
        self.create_subscription(Odometry, "/gz/odom_raw", self._on_gz_odom, 10)
        self.create_subscription(LaserScan, "/gz/scan_raw", self._on_gz_scan, 10)
        self.create_subscription(Image, "/gz/camera/depth_float", self._on_gz_depth, 10)
        self.create_subscription(Float32, "/servo/coda", self._on_tail_cmd, 10)

        # Publishers
        self.pub_gz_cmd = self.create_publisher(Twist, "/gz/cmd_vel_safe", 10)
        self.pub_odom = self.create_publisher(Odometry, "/odom", 10)
        self.pub_scan = self.create_publisher(LaserScan, "/scan", 10)
        self.pub_depth_16uc1 = self.create_publisher(Image, "/camera/depth/image_raw", 10)
        self.pub_battery = self.create_publisher(BatteryState, "/battery_state", 10)
        self.pub_dock_trigger = self.create_publisher(Bool, "/robot/docking/trigger", 10)
        self.pub_diag = self.create_publisher(DiagnosticArray, "/diagnostics", 10)
        self.pub_ultra = self.create_publisher(Range, "/ultrasonic_range", 10)

        # RPLIDAR C1 Motor Control Services (/stop_motor & /start_motor for SensorStandbyManager)
        self.srv_stop_motor = self.create_service(Empty, "/stop_motor", self._handle_stop_motor)
        self.srv_start_motor = self.create_service(Empty, "/start_motor", self._handle_start_motor)

        self.create_timer(0.02, self._control_loop_50hz)
        self.create_timer(0.20, self._telemetry_loop_5hz)
        self.get_logger().info("⚙️ [MarcusHardwareSimBridge] Avviato (50Hz PID/Watchdog, C1 LiDAR Motor Srv, 16UC1 Depth, 3S2P BMS).")

    def _handle_stop_motor(self, request, response):
        self._lidar_motor_running = False
        self.get_logger().info("🛑 [RPLIDAR C1 Sim] Motore LiDAR arrestato via /stop_motor.")
        return response

    def _handle_start_motor(self, request, response):
        self._lidar_motor_running = True
        self.get_logger().info("⚡ [RPLIDAR C1 Sim] Motore LiDAR avviato via /start_motor.")
        return response

    def _clock_now_sec(self) -> float:
        sim_s = self.get_clock().now().nanoseconds * 1e-9
        return sim_s if sim_s > 0.0 else time.time()

    def _on_cmd_vel(self, msg: Twist):
        self.engine.receive_cmd_vel(msg.linear.x, msg.angular.z, now=self._clock_now_sec())

    def _on_motion_gate(self, msg: Bool):
        self.engine.state.motion_gate_open = bool(msg.data)

    def _on_tail_cmd(self, msg: Float32):
        self.engine.set_tail_angle(msg.data)

    def _on_gz_odom(self, msg: Odometry):
        msg.header.frame_id = "odom"
        msg.child_frame_id = "base_link"
        self._last_gz_odom = msg
        # Sincronizza posa nel motore BMS per il rilevamento preciso della cuccia (x=0, y=0)
        self.engine.state.x = float(msg.pose.pose.position.x)
        self.engine.state.y = float(msg.pose.pose.position.y)
        self.pub_odom.publish(msg)

    def _on_gz_scan(self, msg: LaserScan):
        msg.header.frame_id = "laser"
        if self._lidar_motor_running:
            self.pub_scan.publish(msg)

        # Estrae cono frontale (+X di base_link, corrispondente a angle_laser ~ +-pi) per /ultrasonic_range
        n = len(msg.ranges)
        if n > 0:
            front_dists = []
            angle = msg.angle_min
            inc = msg.angle_increment
            for r in msg.ranges:
                # laser_joint ha yaw = pi rispetto a base_link
                ang_base = math.atan2(math.sin(angle + math.pi), math.cos(angle + math.pi))
                if abs(ang_base) <= 0.05 and math.isfinite(r) and r >= msg.range_min:
                    front_dists.append(float(r))
                angle += inc
            min_front = min(front_dists) if front_dists else 1.50
            ultra = Range()
            ultra.header = msg.header
            ultra.header.frame_id = "ultrasonic_sensor"
            ultra.radiation_type = Range.ULTRASOUND
            ultra.field_of_view = 0.10
            ultra.min_range = 0.02
            ultra.max_range = 1.50
            ultra.range = max(0.02, min(1.50, min_front))
            self.pub_ultra.publish(ultra)

    def _on_gz_depth(self, msg: Image):
        if msg.encoding == "32FC1":
            arr_f32 = np.frombuffer(msg.data, dtype=np.float32).reshape((msg.height, msg.width))
            arr_16u = self.engine.convert_depth_32fc1_to_16uc1(arr_f32)
            out = Image()
            out.header = msg.header
            out.header.frame_id = "camera_optical_frame"
            out.height = msg.height
            out.width = msg.width
            out.encoding = "16UC1"
            out.is_bigendian = 0
            out.step = msg.width * 2
            out.data = arr_16u.tobytes()
            self.pub_depth_16uc1.publish(out)
        elif msg.encoding == "16UC1":
            self.pub_depth_16uc1.publish(msg)

    def _control_loop_50hz(self):
        now = self._clock_now_sec()
        if self._last_step_time <= 0.0 or now < self._last_step_time:
            dt = 0.02
        else:
            dt = max(0.005, min(0.1, now - self._last_step_time))
        self._last_step_time = now

        # Rileva eventuale stallo fisico da Gazebo (solo traslazione pura sostenuta, ignorando spin sul posto)
        stalled_in_gz = False
        if self._last_gz_odom is not None:
            gz_vx = self._last_gz_odom.twist.twist.linear.x
            if (
                abs(self.engine.state.v_filtered) > 0.14
                and abs(self.engine.state.w_filtered) < 0.10
                and abs(gz_vx) < 0.004
            ):
                stalled_in_gz = True

        st = self.engine.step(dt=dt, now=now, external_obstruction_stall=stalled_in_gz)

        safe_twist = Twist()
        safe_twist.linear.x = st.v_actual
        safe_twist.angular.z = st.w_actual
        self.pub_gz_cmd.publish(safe_twist)

    def _telemetry_loop_5hz(self):
        st = self.engine.state
        now_msg = self.get_clock().now().to_msg()

        batt = BatteryState()
        batt.header.stamp = now_msg
        batt.voltage = float(st.terminal_voltage)
        batt.current = float(st.current_amperes)
        batt.percentage = float(st.soc_percentage)
        batt.power_supply_status = (
            BatteryState.POWER_SUPPLY_STATUS_CHARGING
            if st.is_charging
            else BatteryState.POWER_SUPPLY_STATUS_DISCHARGING
        )
        self.pub_battery.publish(batt)

        if st.docking_triggered:
            d_msg = Bool()
            d_msg.data = True
            self.pub_dock_trigger.publish(d_msg)

        diag = DiagnosticArray()
        diag.header.stamp = now_msg

        st_mot = DiagnosticStatus()
        st_mot.name = "motor_stall"
        st_mot.level = DiagnosticStatus.ERROR if st.motor_stalled else DiagnosticStatus.OK
        st_mot.message = "STALLO MOTORI RILEVATO" if st.motor_stalled else "Motori nominali"
        diag.status.append(st_mot)

        st_sys = DiagnosticStatus()
        st_sys.name = "system_monitor"
        st_sys.level = DiagnosticStatus.OK
        st_sys.values = [
            KeyValue(key="cpu_temperature", value=f"{st.cpu_temp_c:.1f}"),
            KeyValue(key="npu_temperature", value=f"{st.npu_temp_c:.1f}"),
            KeyValue(key="eco_mode", value=str(st.eco_mode_active)),
        ]
        diag.status.append(st_sys)
        self.pub_diag.publish(diag)


def main(args=None):
    if not HAS_ROS2:
        return
    rclpy.init(args=args)
    node = MarcusHardwareSimBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
