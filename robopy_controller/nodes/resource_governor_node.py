#!/usr/bin/env python3
"""
Nodo ROS 2: resource_governor_node
Esegue il monitoraggio telemetrico, l'orchestrazione delle risorse e la Statechart
del Dynamic Resource Governor in modalità Shadow Mode (IMP-GOV-001).

Pubblica:
- /resource_governor/state (JSON con stato cinematico, cognitivo e profilo raccomandato)
- /resource_governor/metrics (JSON con metriche di stabilità, transizioni e audit invarianti)
"""

import json
import time
import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Bool, Header
from nav_msgs.msg import Odometry
from sensor_msgs.msg import BatteryState

from robopy_controller.robot_ai.services.resource_governor import (
    ResourceGovernorEngine,
    KinematicState,
    CognitiveState,
    SafetyState,
    InvariantViolation
)


class ResourceGovernorNode(Node):
    """
    Nodo ROS 2 per il governo dinamico delle risorse e l'audit degli invarianti.
    """

    def __init__(self):
        super().__init__('resource_governor_node')

        self.declare_parameter('shadow_mode', True)
        self.declare_parameter('cycle_rate_hz', 10.0)
        self.declare_parameter('idle_dock_timeout_s', 180.0)

        self.shadow_mode = self.get_parameter('shadow_mode').get_parameter_value().bool_value
        cycle_rate = self.get_parameter('cycle_rate_hz').get_parameter_value().double_value
        idle_dock = self.get_parameter('idle_dock_timeout_s').get_parameter_value().double_value

        self.engine = ResourceGovernorEngine(
            idle_dock_timeout_s=idle_dock,
            shadow_mode=self.shadow_mode
        )

        # Publisher telemetrici
        self.pub_state = self.create_publisher(String, '/resource_governor/state', 10)
        self.pub_metrics = self.create_publisher(String, '/resource_governor/metrics', 10)

        # Subscriber telemetrici
        self.sub_odom = self.create_subscription(
            Odometry, '/odom', self._on_odom, 10
        )
        self.sub_battery = self.create_subscription(
            BatteryState, '/battery_state', self._on_battery, 10
        )
        self.sub_wake_word = self.create_subscription(
            String, '/wake_word', self._on_wake_word, 10
        )
        self.sub_hailo_kws = self.create_subscription(
            Header, '/hailo/wakeword_trigger', self._on_hailo_kws, 10
        )
        self.sub_multimodal = self.create_subscription(
            String, '/identity/multimodal_state', self._on_multimodal_state, 10
        )
        self.sub_nav_intent = self.create_subscription(
            Bool, '/robot/nav_intent', self._on_nav_intent, 10
        )
        self.sub_stop = self.create_subscription(
            Bool, '/safety/emergency_stop', self._on_emergency_stop, 10
        )

        # Timer principale a 10 Hz
        timer_period = 1.0 / max(1.0, cycle_rate)
        self.timer = self.create_timer(timer_period, self._on_cycle)

        self._start_time = time.monotonic()
        mode_str = "🟢 SHADOW MODE (Audit & Raccomandazioni)" if self.shadow_mode else "⚡ ACTIVE MODE"
        self.get_logger().info(f"🚀 [ResourceGovernor] Avviato con successo in {mode_str} @ {cycle_rate:.1f} Hz!")

    def _on_odom(self, msg: Odometry):
        linear_x = msg.twist.twist.linear.x
        linear_y = msg.twist.twist.linear.y
        speed = (linear_x ** 2 + linear_y ** 2) ** 0.5
        self.engine.update_telemetry(
            linear_speed=speed,
            is_charging_or_docked=self.engine.is_in_dock,
            person_present=self.engine.person_detected,
            cloud_online=self.engine.cloud_connected,
            scan_to_map_match_ok=self.engine.scan_consistency_ok
        )

    def _on_battery(self, msg: BatteryState):
        is_charging = (msg.power_supply_status == BatteryState.POWER_SUPPLY_STATUS_CHARGING) or (msg.voltage >= 12.70)
        self.engine.update_telemetry(
            linear_speed=self.engine.current_speed,
            is_charging_or_docked=is_charging,
            person_present=self.engine.person_detected,
            cloud_online=self.engine.cloud_connected,
            scan_to_map_match_ok=self.engine.scan_consistency_ok
        )

    def _on_wake_word(self, msg: String):
        self.get_logger().info(f"🎤 [ResourceGovernor] Wake word ricevuta ('{msg.data}'): sveglio regione cognitiva!")
        self.engine.notify_user_interaction()

    def _on_hailo_kws(self, msg: Header):
        self.get_logger().info("🔥 [ResourceGovernor] Hailo NPU Wake trigger ricevuto!")
        self.engine.notify_user_interaction()

    def _on_multimodal_state(self, msg: String):
        try:
            data = json.loads(msg.data)
            has_person = len(data.get("visual_tracks", [])) > 0 or data.get("active_person_id") is not None
            self.engine.person_detected = bool(has_person)
            if data.get("voice_activity_detected", False):
                self.engine.notify_user_interaction()
        except Exception:
            pass

    def _on_nav_intent(self, msg: Bool):
        if msg.data:
            self.get_logger().info("🧭 [ResourceGovernor] Nav Intent ricevuto da TRINITY/Nav2.")
            self.engine.request_navigation()
        else:
            self.engine.cancel_navigation()

    def _on_emergency_stop(self, msg: Bool):
        if msg.data:
            self.get_logger().warning("🛑 [ResourceGovernor] Fast-path STOP intercettato!")
            self.engine.trigger_emergency_stop()

    def _on_cycle(self):
        try:
            profile = self.engine.evaluate_cycle()
        except InvariantViolation as iv:
            self.get_logger().error(f"🚨 [ResourceGovernor] VIOLAZIONE INVARIANTE: {iv}")
            profile = self.engine._resolve_resource_profile()

        # Genera payload di stato
        state_payload = {
            "timestamp": time.time(),
            "shadow_mode": self.shadow_mode,
            "kinematic_state": self.engine.kinematic_state.value,
            "cognitive_state": self.engine.cognitive_state.value,
            "safety_state": self.engine.safety_state.value,
            "recommended_profile": profile.to_dict(),
            "in_dock": self.engine.is_in_dock,
            "person_detected": self.engine.person_detected,
            "current_speed_mps": round(self.engine.current_speed, 3)
        }
        msg_state = String()
        msg_state.data = json.dumps(state_payload)
        self.pub_state.publish(msg_state)

        # Genera metriche telemetriche
        metrics_payload = {
            "uptime_sec": round(time.monotonic() - self._start_time, 1),
            "transitions_total": len(self.engine.transition_history),
            "invariant_violations_count": len(self.engine.invariant_violations),
            "invariants_healthy": len(self.engine.invariant_violations) == 0,
            "last_kinematic_transition": self.engine.transition_history[-1] if self.engine.transition_history else None
        }
        msg_metrics = String()
        msg_metrics.data = json.dumps(metrics_payload)
        self.pub_metrics.publish(msg_metrics)


def main(args=None):
    rclpy.init(args=args)
    node = ResourceGovernorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
