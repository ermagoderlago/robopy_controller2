#!/usr/bin/env python3
"""
Multimodal Identity Node
========================
ROS 2 Node that integrates visual facial recognition (SCRFD + ArcFace)
with speaker identification (ECAPA-TDNN) and acoustic spatial localization (DOA).

Features:
- Maintains visual person tracks and enforces temporal re-verification policy
  to save 70-80% of NPU ArcFace compute cycles.
- Fuses progressive voice biometrics (provisional on KWS <300ms, confirmed on EOS <500ms).
- Performs spatial coherence gating against ReSpeaker Direction of Arrival (DOA).
- Publishes consolidated JSON state to /identity/multimodal_state for TRINITY / CAG.

Version: 02.00.00 (Fase F2)
"""

import json
import time
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String, Float32, Bool, Int32
from vision_msgs.msg import Detection2DArray

from robopy_controller.robot_ai.services.multimodal_identity_tracker import (
    MultimodalIdentityTracker,
    MultimodalPerson,
)


class MultimodalIdentityNode(Node):
    """
    ROS 2 Node orchestrating multimodal biometric identity tracking.
    """

    def __init__(self):
        super().__init__('multimodal_identity_node')
        self.get_logger().info("Inizializzazione multimodal_identity_node (Fase F2)...")

        # Declare parameters
        self.declare_parameter('camera_hfov_deg', 73.0)
        self.declare_parameter('max_track_staleness_sec', 3.0)
        self.declare_parameter('face_confidence_threshold', 0.72)
        self.declare_parameter('voice_confidence_threshold', 0.70)
        self.declare_parameter('publish_rate_hz', 10.0)

        camera_hfov = self.get_parameter('camera_hfov_deg').value
        max_staleness = self.get_parameter('max_track_staleness_sec').value
        face_thresh = self.get_parameter('face_confidence_threshold').value
        voice_thresh = self.get_parameter('voice_confidence_threshold').value
        pub_rate = self.get_parameter('publish_rate_hz').value

        # Tracker instance
        self.tracker = MultimodalIdentityTracker(
            camera_hfov_deg=camera_hfov,
            max_track_staleness_sec=max_staleness,
            face_confidence_threshold=face_thresh,
            voice_confidence_threshold=voice_thresh,
        )

        # QoS profiles
        qos_best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        # Subscriptions - Vision
        self.sub_face_detections = self.create_subscription(
            Detection2DArray,
            '/hailo/face/detections',
            self.face_detections_callback,
            qos_best_effort,
        )
        self.sub_face_identity = self.create_subscription(
            String,
            '/hailo/face/identity',
            self.face_identity_callback,
            qos_reliable,
        )

        # Subscriptions - Audio Voice Biometrics
        self.sub_speaker_identity = self.create_subscription(
            String,
            '/speaker/identity',
            self.speaker_identity_callback,
            qos_reliable,
        )
        self.sub_speaker_confidence = self.create_subscription(
            Float32,
            '/speaker/confidence',
            self.speaker_confidence_callback,
            qos_reliable,
        )
        self.sub_prov_speaker_id = self.create_subscription(
            String,
            '/speaker/provisional_identity',
            self.provisional_speaker_identity_callback,
            qos_reliable,
        )
        self.sub_prov_speaker_conf = self.create_subscription(
            Float32,
            '/speaker/provisional_confidence',
            self.provisional_speaker_confidence_callback,
            qos_reliable,
        )

        # Subscriptions - Audio Spatial Direction of Arrival (DOA)
        self.sub_audio_doa = self.create_subscription(
            Int32,
            '/respeaker/audio_angle',
            self.audio_doa_callback,
            qos_best_effort,
        )

        # Subscriptions - Resource Governor State
        self.sub_governor_state = self.create_subscription(
            String,
            '/resource_governor/state',
            self.governor_state_callback,
            qos_reliable,
        )

        # Publishers
        self.pub_multimodal_state = self.create_publisher(
            String,
            '/identity/multimodal_state',
            qos_reliable,
        )
        self.pub_active_person = self.create_publisher(
            String,
            '/identity/active_person',
            qos_reliable,
        )
        self.pub_confidence = self.create_publisher(
            Float32,
            '/identity/confidence',
            qos_reliable,
        )
        self.pub_recheck_needed = self.create_publisher(
            Bool,
            '/identity/verification_needed',
            qos_best_effort,
        )

        # Internal state for partial voice callbacks
        self._last_voice_name = "unknown"
        self._last_voice_conf = 0.0
        self._last_prov_name = "unknown"
        self._last_prov_conf = 0.0
        self._current_doa: Optional[float] = None

        # Periodic fusion timer
        timer_period = 1.0 / max(1.0, pub_rate)
        self.timer = self.create_timer(timer_period, self.timer_callback)

        self.get_logger().info("multimodal_identity_node avviato con successo.")

    def face_detections_callback(self, msg: Detection2DArray):
        """Processes 2D face detection bounding boxes from OAK / Hailo bridge."""
        detections = []
        for det in msg.detections:
            cx = det.bbox.center.position.x
            cy = det.bbox.center.position.y
            w = det.bbox.size_x
            h = det.bbox.size_y
            x = cx - w / 2.0
            y = cy - h / 2.0
            detections.append({'bbox': [x, y, w, h]})

        needs_recheck = self.tracker.update_visual_tracks(detections)
        if needs_recheck:
            recheck_msg = Bool()
            recheck_msg.data = True
            self.pub_recheck_needed.publish(recheck_msg)

    def face_identity_callback(self, msg: String):
        """
        Ingests recognized face identity from ArcFace.
        Format can be 'Name:Confidence' or plain 'Name' or JSON.
        """
        raw = msg.data.strip()
        name = raw
        conf = 0.85
        if ":" in raw:
            parts = raw.split(":")
            name = parts[0].strip()
            try:
                conf = float(parts[1].strip())
            except ValueError:
                pass
        elif raw.startswith("{"):
            try:
                data = json.loads(raw)
                name = data.get("name", "unknown")
                conf = float(data.get("confidence", 0.85))
                track_id = data.get("track_id", None)
                if track_id is not None:
                    self.tracker.update_face_identity(track_id, name, conf)
                    return
            except Exception:
                pass

        # Update primary or first track needing identity
        with self.tracker._lock:
            for tid, trk in self.tracker._tracks.items():
                if trk.needs_recheck or trk.face_identity == "unknown":
                    self.tracker.update_face_identity(tid, name, conf)
                    break

    def speaker_identity_callback(self, msg: String):
        """Ingests confirmed speaker identity (Stage 2)."""
        self._last_voice_name = msg.data
        if self._last_voice_name and self._last_voice_conf > 0:
            self.tracker.update_voice_identity(
                identity=self._last_voice_name,
                confidence=self._last_voice_conf,
                doa_deg=self._current_doa,
                is_provisional=False,
            )

    def speaker_confidence_callback(self, msg: Float32):
        self._last_voice_conf = float(msg.data)
        if self._last_voice_name and self._last_voice_name != "unknown":
            self.tracker.update_voice_identity(
                identity=self._last_voice_name,
                confidence=self._last_voice_conf,
                doa_deg=self._current_doa,
                is_provisional=False,
            )

    def provisional_speaker_identity_callback(self, msg: String):
        """Ingests provisional speaker identity (Stage 1, <300ms latency)."""
        self._last_prov_name = msg.data
        if self._last_prov_name and self._last_prov_conf > 0:
            self.tracker.update_voice_identity(
                identity=self._last_prov_name,
                confidence=self._last_prov_conf,
                doa_deg=self._current_doa,
                is_provisional=True,
            )

    def provisional_speaker_confidence_callback(self, msg: Float32):
        self._last_prov_conf = float(msg.data)
        if self._last_prov_name and self._last_prov_name != "unknown":
            self.tracker.update_voice_identity(
                identity=self._last_prov_name,
                confidence=self._last_prov_conf,
                doa_deg=self._current_doa,
                is_provisional=True,
            )

    def audio_doa_callback(self, msg: Int32):
        """Ingests sound angle (0 to 359 degrees) from ReSpeaker DOA."""
        self._current_doa = float(msg.data)

    def governor_state_callback(self, msg: String):
        """Updates re-verification cadence based on Resource Governor state."""
        self.tracker.set_governor_state(msg.data)

    def timer_callback(self):
        """Periodic fusion cycle and telemetry broadcast."""
        active_person, all_persons = self.tracker.fuse_and_get_active_identities()

        if active_person:
            # Active person name
            name_msg = String()
            name_msg.data = active_person.person_id
            self.pub_active_person.publish(name_msg)

            # Confidence
            conf_msg = Float32()
            conf_msg.data = float(active_person.fused_confidence)
            self.pub_confidence.publish(conf_msg)
        else:
            name_msg = String()
            name_msg.data = "none"
            self.pub_active_person.publish(name_msg)

            conf_msg = Float32()
            conf_msg.data = 0.0
            self.pub_confidence.publish(conf_msg)

        # Full serialized state for CAG Aggregator & TRINITY
        payload = {
            "timestamp": time.time(),
            "governor_state": self.tracker._governor_state,
            "recheck_interval_sec": self.tracker.get_recheck_interval(),
            "active_person": active_person.to_dict() if active_person else None,
            "tracked_persons": [p.to_dict() for p in all_persons],
            "cag_summary": self.tracker.get_cag_context_string(),
        }

        state_msg = String()
        state_msg.data = json.dumps(payload)
        self.pub_multimodal_state.publish(state_msg)


def main(args=None):
    rclpy.init(args=args)
    node = MultimodalIdentityNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
