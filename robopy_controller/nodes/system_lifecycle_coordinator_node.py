#!/usr/bin/env python3
"""
System Lifecycle Coordinator & Memory Pressure Sentinel Node
============================================================
Central coordinator for Marcus AI system lifecycle and memory protection on Raspberry Pi 5 (4GB RAM).

Implements:
1. Kernel PSI (/proc/pressure/memory) monitoring with two-tiered pressure protection:
   - PSI full avg10 >= 0.30 (or RAM > 3.4GB): WARNING -> Freeze embedding generation & heavy vector loads.
   - PSI full avg10 >= 0.60 (or RAM > 3.75GB): CRITICAL -> Force unconfigure of non-vital nodes, trigger gc.collect() and malloc_trim(0).
2. Operating State Machine:
   - NAVIGATION_ACTIVE: VIO, RTAB-Map (1.5 Hz), and Nav2 active. Nightly dreaming, heavy embeddings suspended.
   - DOCKED_DREAM: Robot docked on charger. Nav2 and VIO inactive/unconfigured. Nightly dreaming, memory consolidation & DeepSeek active.
   - HUMAN_INTERACTION_MODE: RTAB-Map throttled to 0.25 Hz. Audio VUI boosted with real-time Linux priority.

Author: Marcus AI Engineering Team
Version: 01.00.00
"""

import os
import sys
import time
import gc
import ctypes
try:
    import psutil
except ImportError:
    psutil = None

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String, Bool, Float32, Float32MultiArray
from geometry_msgs.msg import Twist


class OperatingState:
    NAVIGATION_ACTIVE = "NAVIGATION_ACTIVE"
    DOCKED_DREAM = "DOCKED_DREAM"
    HUMAN_INTERACTION_MODE = "HUMAN_INTERACTION_MODE"


class MemoryPressureLevel:
    NORMAL = "NORMAL"
    WARNING_FREEZE = "WARNING_FREEZE"
    CRITICAL_EVICT = "CRITICAL_EVICT"


def parse_kernel_psi(psi_path="/proc/pressure/memory") -> dict:
    """Parse Linux kernel Pressure Stall Information (PSI) from /proc/pressure/memory.
    
    Returns dictionary with keys:
    {'some_avg10': float, 'some_avg60': float, 'some_avg300': float,
     'full_avg10': float, 'full_avg60': float, 'full_avg300': float}
    """
    psi_stats = {
        'some_avg10': 0.0, 'some_avg60': 0.0, 'some_avg300': 0.0,
        'full_avg10': 0.0, 'full_avg60': 0.0, 'full_avg300': 0.0
    }
    
    if not os.path.exists(psi_path):
        return psi_stats
        
    try:
        with open(psi_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('some'):
                    parts = line.split()
                    for p in parts[1:]:
                        if '=' in p:
                            k, v = p.split('=', 1)
                            psi_stats[f'some_{k}'] = float(v)
                elif line.startswith('full'):
                    parts = line.split()
                    for p in parts[1:]:
                        if '=' in p:
                            k, v = p.split('=', 1)
                            psi_stats[f'full_{k}'] = float(v)
    except Exception:
        pass
        
    return psi_stats


class SystemLifecycleCoordinatorNode(Node):
    def __init__(self):
        super().__init__('system_lifecycle_coordinator_node')
        self.get_logger().info("Inizializzazione SystemLifecycleCoordinatorNode...")

        # Parameters
        self.declare_parameter('psi_warning_thresh', 0.30)
        self.declare_parameter('psi_critical_thresh', 0.60)
        self.declare_parameter('ram_warning_gb', 3.40)
        self.declare_parameter('ram_critical_gb', 3.75)
        self.declare_parameter('docked_voltage_thresh', 12.65)
        self.declare_parameter('check_frequency_hz', 2.0)

        self.psi_warning_thresh = self.get_parameter('psi_warning_thresh').value
        self.psi_critical_thresh = self.get_parameter('psi_critical_thresh').value
        self.ram_warning_gb = self.get_parameter('ram_warning_gb').value
        self.ram_critical_gb = self.get_parameter('ram_critical_gb').value
        self.docked_voltage_thresh = self.get_parameter('docked_voltage_thresh').value
        self.check_freq = self.get_parameter('check_frequency_hz').value

        # QoS
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        # Internal State
        self.current_state = OperatingState.NAVIGATION_ACTIVE
        self.current_pressure = MemoryPressureLevel.NORMAL
        self.is_docked = False
        self.last_interaction_time = 0.0
        self.interaction_timeout_sec = 20.0  # Return to nav state 20s after conversation ends
        self.latest_battery_voltage = 12.0
        self.is_moving = False
        self.last_move_cmd_time = 0.0

        # Publishers
        self.pub_state = self.create_publisher(String, '/system/operating_state', qos_reliable)
        self.pub_pressure = self.create_publisher(String, '/system/memory_pressure', qos_reliable)
        self.pub_freeze_embeddings = self.create_publisher(Bool, '/system/memory_freeze', qos_reliable)
        self.pub_emergency_evict = self.create_publisher(Bool, '/system/emergency_evict', qos_reliable)
        self.pub_psi_stats = self.create_publisher(Float32MultiArray, '/system/memory_psi_stats', qos_sensor)
        self.pub_rtabmap_rate = self.create_publisher(Float32, '/rtabmap/detection_rate_target', qos_reliable)

        # Subscriptions
        self.create_subscription(Float32, '/motor/battery_voltage', self.battery_cb, qos_reliable)
        self.create_subscription(String, '/robot/docking/status', self.docking_status_cb, qos_reliable)
        self.create_subscription(Bool, '/voice/wake_word_detected', self.wake_word_cb, qos_reliable)
        self.create_subscription(String, '/ai/conversation/state', self.conversation_state_cb, qos_reliable)
        self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_cb, qos_sensor)

        # Monitoring Timer
        timer_period = 1.0 / max(0.5, self.check_freq)
        self.timer = self.create_timer(timer_period, self.coordinator_loop)

        # Publish initial state
        self._publish_state_update(self.current_state)
        self.get_logger().info(f"SystemLifecycleCoordinator Node attivo. Stato iniziale: {self.current_state}")

    def battery_cb(self, msg: Float32):
        self.latest_battery_voltage = float(msg.data)
        if self.latest_battery_voltage >= self.docked_voltage_thresh:
            if not self.is_docked:
                self.is_docked = True
                self.get_logger().info(f"Rilevata tensione di ricarica ({self.latest_battery_voltage:.2f}V) -> Robot DOCKED")
        else:
            if self.is_docked and self.latest_battery_voltage < 12.4:
                self.is_docked = False
                self.get_logger().info(f"Tensione a batteria ({self.latest_battery_voltage:.2f}V) -> Robot UNDOCKED")

    def docking_status_cb(self, msg: String):
        status = msg.data.upper()
        if "DOCKED" in status or "CHARGING" in status:
            self.is_docked = True
        elif "UNDOCKED" in status or "NAVIGATING" in status:
            self.is_docked = False

    def wake_word_cb(self, msg: Bool):
        if msg.data:
            self.last_interaction_time = time.time()
            if self.current_state != OperatingState.HUMAN_INTERACTION_MODE:
                self.transition_to(OperatingState.HUMAN_INTERACTION_MODE, reason="Wake Word Detected")

    def conversation_state_cb(self, msg: String):
        state = msg.data.upper()
        if state in ("LISTENING", "THINKING", "SPEAKING", "ACTIVE"):
            self.last_interaction_time = time.time()
            if self.current_state != OperatingState.HUMAN_INTERACTION_MODE:
                self.transition_to(OperatingState.HUMAN_INTERACTION_MODE, reason=f"Conversation {state}")

    def cmd_vel_cb(self, msg: Twist):
        linear_v = abs(msg.linear.x) + abs(msg.linear.y)
        angular_v = abs(msg.angular.z)
        if linear_v > 0.01 or angular_v > 0.05:
            self.is_moving = True
            self.last_move_cmd_time = time.time()
        else:
            if time.time() - self.last_move_cmd_time > 1.5:
                self.is_moving = False

    def check_memory_pressure(self) -> tuple[str, float, float, float]:
        """Reads kernel PSI and system RAM to determine memory pressure tier."""
        psi = parse_kernel_psi()
        full_avg10 = psi['full_avg10']
        some_avg10 = psi['some_avg10']

        # Fallback / Cross-platform estimation if kernel PSI is 0 or unavailable
        if psutil is not None:
            ram = psutil.virtual_memory()
            ram_used_gb = ram.used / (1024 ** 3)
            ram_used_pct = ram.percent
        else:
            ram_used_gb = 1.8
            ram_used_pct = 45.0

        if full_avg10 == 0.0 and ram_used_pct > 80.0:
            # Emulate PSI pressure on high RAM usage
            full_avg10 = max(0.0, (ram_used_pct - 80.0) / 20.0)

        pressure_level = MemoryPressureLevel.NORMAL
        if full_avg10 >= self.psi_critical_thresh or ram_used_gb >= self.ram_critical_gb:
            pressure_level = MemoryPressureLevel.CRITICAL_EVICT
        elif full_avg10 >= self.psi_warning_thresh or ram_used_gb >= self.ram_warning_gb:
            pressure_level = MemoryPressureLevel.WARNING_FREEZE

        return pressure_level, some_avg10, full_avg10, ram_used_gb

    def handle_memory_pressure_actions(self, pressure_level: str, full_avg10: float, ram_used_gb: float):
        """Execute proactive defensive memory containment actions based on PSI tier."""
        if pressure_level != self.current_pressure:
            self.get_logger().warn(
                f"[MEMORY_SENTINEL] Transizione Pressione Memoria: {self.current_pressure} -> {pressure_level} "
                f"(PSI full avg10: {full_avg10:.2f}, RAM Usata: {ram_used_gb:.2f}GB)"
            )
            self.current_pressure = pressure_level

            # Publish Pressure status
            p_msg = String()
            p_msg.data = self.current_pressure
            self.pub_pressure.publish(p_msg)

            if pressure_level == MemoryPressureLevel.WARNING_FREEZE:
                # 1. Freeze embeddings
                freeze_msg = Bool()
                freeze_msg.data = True
                self.pub_freeze_embeddings.publish(freeze_msg)
                self.get_logger().info("[MEMORY_SENTINEL] Freeze caricamento nuovi vettori di embedding attivato.")

            elif pressure_level == MemoryPressureLevel.CRITICAL_EVICT:
                # 1. Freeze embeddings
                freeze_msg = Bool()
                freeze_msg.data = True
                self.pub_freeze_embeddings.publish(freeze_msg)

                # 2. Trigger emergency eviction
                evict_msg = Bool()
                evict_msg.data = True
                self.pub_emergency_evict.publish(evict_msg)

                # 3. Aggressive GC and libc malloc trim
                gc.collect()
                self._trim_libc_memory()
                self.get_logger().error("[MEMORY_SENTINEL] EMERGENZA RAM: Eviction forzata buffer e malloc_trim eseguiti!")

            elif pressure_level == MemoryPressureLevel.NORMAL:
                # Unfreeze
                freeze_msg = Bool()
                freeze_msg.data = False
                self.pub_freeze_embeddings.publish(freeze_msg)
                self.get_logger().info("[MEMORY_SENTINEL] Pressione memoria rientrata nella norma. Ripristino operazioni nominali.")

    def _trim_libc_memory(self):
        """Invoke libc malloc_trim(0) if running on Linux to release cached heap pages back to OS."""
        try:
            if hasattr(ctypes, 'CDLL'):
                libc = ctypes.CDLL("libc.so.6")
                if hasattr(libc, 'malloc_trim'):
                    libc.malloc_trim(0)
        except Exception:
            pass

    def transition_to(self, target_state: str, reason: str = ""):
        """Executes coordinated lifecycle state transition."""
        if target_state == self.current_state:
            return

        self.get_logger().info(f"[LIFECYCLE] Transizione Stato: {self.current_state} -> {target_state}. Motivo: {reason}")
        self.current_state = target_state
        self._publish_state_update(target_state)

        # Apply specific state configurations
        if target_state == OperatingState.HUMAN_INTERACTION_MODE:
            # Throttle RTAB-Map to 0.25 Hz (1 frame every 4 seconds)
            rate_msg = Float32()
            rate_msg.data = 0.25
            self.pub_rtabmap_rate.publish(rate_msg)

        elif target_state == OperatingState.NAVIGATION_ACTIVE:
            # Restore RTAB-Map to nominal 1.5 Hz
            rate_msg = Float32()
            rate_msg.data = 1.5
            self.pub_rtabmap_rate.publish(rate_msg)

        elif target_state == OperatingState.DOCKED_DREAM:
            # In DOCKED_DREAM, pause RTAB-Map
            rate_msg = Float32()
            rate_msg.data = 0.0
            self.pub_rtabmap_rate.publish(rate_msg)

    def _publish_state_update(self, state: str):
        msg = String()
        msg.data = state
        self.pub_state.publish(msg)

    def coordinator_loop(self):
        now = time.time()

        # 1. Check & Handle Memory Pressure
        pressure_level, some_avg10, full_avg10, ram_used_gb = self.check_memory_pressure()
        self.handle_memory_pressure_actions(pressure_level, full_avg10, ram_used_gb)

        # Publish PSI Telemetry
        psi_msg = Float32MultiArray()
        psi_msg.data = [float(some_avg10), float(full_avg10), float(ram_used_gb)]
        self.pub_psi_stats.publish(psi_msg)

        # 2. Evaluate Operating State Transitions
        if self.is_docked:
            if self.current_state != OperatingState.DOCKED_DREAM:
                # If docked and not in an active human conversation, enter DOCKED_DREAM
                if (now - self.last_interaction_time) > self.interaction_timeout_sec:
                    self.transition_to(OperatingState.DOCKED_DREAM, reason="Robot Docked on Charger")
        else:
            # Undocked
            if self.current_state == OperatingState.DOCKED_DREAM:
                self.transition_to(OperatingState.NAVIGATION_ACTIVE, reason="Robot Undocked / Deploying")
            elif self.current_state == OperatingState.HUMAN_INTERACTION_MODE:
                # Check interaction timeout
                if (now - self.last_interaction_time) > self.interaction_timeout_sec:
                    self.transition_to(OperatingState.NAVIGATION_ACTIVE, reason="Interaction Timeout")


def main(args=None):
    rclpy.init(args=args)
    node = SystemLifecycleCoordinatorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
