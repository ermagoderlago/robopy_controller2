#!/usr/bin/env python3
"""
Luminance Safety Gate Node for Marcus AI
========================================
Zero-allocation ITU-R BT.601 perceptual luminance calculation:
    Y = 0.299 * R + 0.587 * G + 0.114 * B
Dual-threshold Schmitt-trigger hysteresis state machine:
    - Y < 25.0: INHIBITED_DARK (mapping rejected, voice warning emitted)
    - Y > 30.0: AUTHORIZED (mapping authorized)
    - [25.0, 30.0]: retains previous state (fail-safe boot default: INHIBITED_DARK)
Interfaces:
    - Topic: /camera/luminance_status (std_msgs/msg/String JSON)
    - Service: /mapping/check_luminance (std_srvs/srv/Trigger)
    - Voice Warning: /ai/conversation/response (std_msgs/msg/String)

Conforms to:
    - TC1 / R2: Pre-mapping luminance verification & vocal warnings
    - SPEC-02 / SPEC-03: Zero-allocation callback & camera safety
    - marcus_core_rules.md: Zero BOM UTF-8, Core 0-1 I/O
"""

import json
import time
from typing import Dict, Any, Tuple, Optional, Union
import numpy as np

# Safe ROS 2 import guard for dual-environment execution
try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
    from sensor_msgs.msg import Image
    from std_msgs.msg import String
    from std_srvs.srv import Trigger
    HAS_ROS2 = True
except ImportError:
    HAS_ROS2 = False
    Node = object  # Fallback base class for non-ROS testing


class LuminanceSafetyGate:
    """
    Pure Python/NumPy engine for perceptual luminance calculation and hysteresis.
    Decoupled from ROS 2 for fast, deterministic unit and contract testing.
    """
    THRESHOLD_LOW: float = 25.0
    THRESHOLD_HIGH: float = 30.0
    STATE_AUTHORIZED: str = "AUTHORIZED"
    STATE_INHIBITED_DARK: str = "INHIBITED_DARK"

    def __init__(self, threshold_low: float = 25.0, threshold_high: float = 30.0):
        self.threshold_low = float(threshold_low)
        self.threshold_high = float(threshold_high)
        self.current_state = self.STATE_INHIBITED_DARK
        self.last_luminance: Optional[float] = None
        self.last_warning: Optional[str] = None

    @staticmethod
    def compute_mean_luminance(
        image_data: Union[np.ndarray, bytes],
        encoding: str = "rgb8",
        height: Optional[int] = None,
        width: Optional[int] = None
    ) -> float:
        """
        Computes ITU-R BT.601 perceptual luminance: Y = 0.299*R + 0.587*G + 0.114*B.
        Optimized with linear channel expectation E[0.299R + 0.587G + 0.114B] =
        0.299*E[R] + 0.587*E[G] + 0.114*E[B] avoiding intermediate full-frame float32 buffers.
        Supports 2D grayscale, 3D RGB/BGR, and raw byte buffers.
        """
        if isinstance(image_data, bytes):
            if height is None or width is None:
                raise ValueError("Height and width required for raw byte buffers")
            enc_lower = encoding.lower()
            if enc_lower in ("rgb8", "bgr8"):
                arr = np.frombuffer(image_data, dtype=np.uint8).reshape((height, width, 3))
            elif enc_lower in ("mono8", "8uc1"):
                arr = np.frombuffer(image_data, dtype=np.uint8).reshape((height, width))
            else:
                arr = np.frombuffer(image_data, dtype=np.uint8).reshape((height, width, -1))
        elif isinstance(image_data, np.ndarray):
            arr = image_data
        else:
            raise TypeError(f"Unsupported image data type: {type(image_data)}")

        if arr.size == 0:
            return float("nan")

        if arr.ndim == 2 or (arr.ndim == 3 and arr.shape[2] == 1):
            return float(np.mean(arr))
        elif arr.ndim == 3 and arr.shape[2] >= 3:
            enc_lower = encoding.lower()
            if "bgr" in enc_lower:
                b = arr[:, :, 0]
                g = arr[:, :, 1]
                r = arr[:, :, 2]
            else:
                # Default RGB
                r = arr[:, :, 0]
                g = arr[:, :, 1]
                b = arr[:, :, 2]

            # Zero-allocation channel mean calculation:
            mean_y = 0.299 * float(r.mean()) + 0.587 * float(g.mean()) + 0.114 * float(b.mean())
            return float(mean_y)
        else:
            raise ValueError(f"Invalid image array shape: {arr.shape}")

    def evaluate(self, mean_luminance: float) -> Dict[str, Any]:
        """
        Pure static evaluation matching LuminanceSafetyContract.evaluate contract:
        - < 25.0, NaN, or Inf: INHIBITED_DARK
        - > 30.0: AUTHORIZED
        - [25.0, 30.0]: INHIBITED_DARK (marginal / critical limit)
        """
        lum = float(mean_luminance)
        if np.isnan(lum) or np.isinf(lum) or lum < self.threshold_low:
            status = self.STATE_INHIBITED_DARK
            authorized = False
            warning = (
                f"Attenzione: illuminazione ambientale insufficiente per la mappatura visiva "
                f"({lum:.1f}/255). Mappatura inibita per sicurezza."
            )
        elif lum > self.threshold_high:
            status = self.STATE_AUTHORIZED
            authorized = True
            warning = None
        else:
            status = self.STATE_INHIBITED_DARK
            authorized = False
            warning = (
                f"Attenzione: illuminazione al limite critico "
                f"({lum:.1f}/255). Richiesti almeno {self.threshold_high:.1f} per l'avvio."
            )

        return {
            "luminance": round(lum, 2) if (not np.isnan(lum) and not np.isinf(lum)) else lum,
            "status": status,
            "authorized": authorized,
            "voice_warning": warning,
            "threshold_low": self.threshold_low,
            "threshold_high": self.threshold_high,
        }

    def update(self, mean_luminance: float) -> Dict[str, Any]:
        """
        Stateful Schmitt-trigger hysteresis update:
        - < 25.0, NaN, or Inf: transitions to INHIBITED_DARK
        - > 30.0: transitions to AUTHORIZED
        - [25.0, 30.0]: retains previous state
        """
        lum = float(mean_luminance)
        self.last_luminance = round(lum, 2) if (not np.isnan(lum) and not np.isinf(lum)) else lum

        if np.isnan(lum) or np.isinf(lum) or lum < self.threshold_low:
            self.current_state = self.STATE_INHIBITED_DARK
            self.last_warning = (
                f"Attenzione: illuminazione ambientale insufficiente per la mappatura visiva "
                f"({lum:.1f}/255). Mappatura inibita per sicurezza."
            )
        elif lum > self.threshold_high:
            self.current_state = self.STATE_AUTHORIZED
            self.last_warning = None
        else:
            # Retention of prior state within hysteresis deadband [25.0, 30.0]
            if self.current_state == self.STATE_AUTHORIZED:
                self.last_warning = None
            else:
                self.current_state = self.STATE_INHIBITED_DARK
                self.last_warning = (
                    f"Attenzione: illuminazione al limite critico "
                    f"({lum:.1f}/255). Richiesti almeno {self.threshold_high:.1f} per l'avvio."
                )

        return {
            "luminance": self.last_luminance,
            "status": self.current_state,
            "authorized": (self.current_state == self.STATE_AUTHORIZED),
            "voice_warning": self.last_warning,
            "threshold_low": self.threshold_low,
            "threshold_high": self.threshold_high,
        }

    def to_ros_topic_payload(self, mean_luminance: Optional[float] = None) -> str:
        """Serializes payload dictionary to JSON string matching /camera/luminance_status contract."""
        lum = mean_luminance if mean_luminance is not None else (self.last_luminance if self.last_luminance is not None else 0.0)
        eval_result = self.evaluate(lum)
        lum_val = eval_result["luminance"]
        if np.isnan(lum_val) or np.isinf(lum_val):
            lum_val = 0.0
        payload = {
            "luminance": lum_val,
            "status": eval_result["status"],
            "threshold_low": eval_result["threshold_low"],
            "threshold_high": eval_result["threshold_high"],
        }
        return json.dumps(payload)

    def trigger_service_check(self, mean_luminance: Optional[float] = None) -> Tuple[bool, str]:
        """Matches /mapping/check_luminance (std_srvs/srv/Trigger) contract."""
        lum = mean_luminance if mean_luminance is not None else (self.last_luminance if self.last_luminance is not None else 0.0)
        res = self.evaluate(lum)
        if res["authorized"]:
            return True, f"Luminance OK ({res['luminance']:.1f}/255). Mapping authorized."
        return False, res["voice_warning"]


class LuminanceSafetyGateNode(Node):
    """
    ROS 2 Node wrapping LuminanceSafetyGate with non-blocking camera ingestion,
    periodic status publishing, Trigger service, and VUI voice alerts.
    """
    def __init__(self):
        if not HAS_ROS2:
            raise RuntimeError("ROS 2 (rclpy) is not available in the current environment.")
        super().__init__('luminance_safety_gate')

        # Parameters
        self.declare_parameter('image_topic', '/camera/color/image_raw')
        self.declare_parameter('status_topic', '/camera/luminance_status')
        self.declare_parameter('service_name', '/mapping/check_luminance')
        self.declare_parameter('voice_topic', '/ai/conversation/response')
        self.declare_parameter('threshold_low', 25.0)
        self.declare_parameter('threshold_high', 30.0)
        self.declare_parameter('evaluation_rate_hz', 2.0)
        self.declare_parameter('staleness_timeout_sec', 3.0)

        self.image_topic = self.get_parameter('image_topic').value
        self.status_topic = self.get_parameter('status_topic').value
        self.service_name = self.get_parameter('service_name').value
        self.voice_topic = self.get_parameter('voice_topic').value
        thresh_low = float(self.get_parameter('threshold_low').value)
        thresh_high = float(self.get_parameter('threshold_high').value)
        rate_hz = float(self.get_parameter('evaluation_rate_hz').value)
        self.staleness_timeout = float(self.get_parameter('staleness_timeout_sec').value)

        # Engine initialization
        self.gate = LuminanceSafetyGate(threshold_low=thresh_low, threshold_high=thresh_high)

        # State & Timing
        self.latest_frame: Optional[np.ndarray] = None
        self.latest_encoding = "rgb8"
        self.last_frame_time: float = 0.0
        self.last_voice_alert_time: float = 0.0

        # QoS profiles
        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        # Subscribers & Publishers
        self.sub_image = self.create_subscription(
            Image, self.image_topic, self._image_callback, qos_sensor
        )
        self.pub_status = self.create_publisher(String, self.status_topic, qos_reliable)
        self.pub_voice = self.create_publisher(String, self.voice_topic, qos_reliable)

        # Service
        self.srv_check = self.create_service(
            Trigger, self.service_name, self._handle_check_luminance
        )

        # Timer for periodic evaluation and publishing
        timer_period = 1.0 / max(rate_hz, 0.5)
        self.timer = self.create_timer(timer_period, self._evaluation_cycle)

        self.get_logger().info(
            f"LuminanceSafetyGateNode initialized [In: {self.image_topic}, "
            f"Status: {self.status_topic}, Srv: {self.service_name}, "
            f"Thresholds: {thresh_low} / {thresh_high}]"
        )

    def _image_callback(self, msg: Image) -> None:
        """Non-blocking ingestion of camera frames using fast buffer reshape."""
        try:
            self.latest_frame = np.frombuffer(msg.data, dtype=np.uint8).reshape((msg.height, msg.width, -1))
            self.latest_encoding = msg.encoding
            self.last_frame_time = time.monotonic()
        except Exception as e:
            self.get_logger().error(f"Failed to decode image frame: {e}")

    def _evaluation_cycle(self) -> None:
        """Periodic luminance evaluation and status topic broadcasting."""
        now = time.monotonic()
        if self.latest_frame is None or (now - self.last_frame_time) > self.staleness_timeout:
            mean_lum = 0.0
        else:
            mean_lum = self.gate.compute_mean_luminance(
                self.latest_frame, encoding=self.latest_encoding
            )

        self.gate.update(mean_lum)

        # Publish JSON topic
        status_msg = String()
        status_msg.data = self.gate.to_ros_topic_payload(mean_lum)
        self.pub_status.publish(status_msg)

    def _handle_check_luminance(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        """Handles /mapping/check_luminance service request."""
        now = time.monotonic()
        if self.latest_frame is None or (now - self.last_frame_time) > self.staleness_timeout:
            mean_lum = 0.0
        else:
            mean_lum = self.gate.compute_mean_luminance(
                self.latest_frame, encoding=self.latest_encoding
            )

        success, msg_text = self.gate.trigger_service_check(mean_lum)
        response.success = success
        response.message = msg_text

        if not success:
            self._dispatch_voice_alert(msg_text)

        self.get_logger().info(f"Luminance check query: {msg_text} (Success: {success})")
        return response

    def _dispatch_voice_alert(self, text: str) -> None:
        """Emits voice alert string to VUI pipeline with rate limiting."""
        now = time.monotonic()
        if (now - self.last_voice_alert_time) >= 5.0:
            self.last_voice_alert_time = now
            v_msg = String()
            v_msg.data = text
            self.pub_voice.publish(v_msg)
            self.get_logger().warn(f"Voice Alert dispatched: {text}")


def main(args=None):
    if not HAS_ROS2:
        print("ROS 2 (rclpy) is not installed in current environment.")
        return
    rclpy.init(args=args)
    node = LuminanceSafetyGateNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
