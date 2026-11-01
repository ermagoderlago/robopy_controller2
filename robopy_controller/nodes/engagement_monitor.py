#!/usr/bin/env python3
"""
Engagement Monitor
==================
ROS 2 Python node that monitors HRI (Human-Robot Interaction) engagement, gaze,
and proxemic distance, sending cancel and interrupt signals to the robot brain.

Version: 01.00.00
"""

import time
import json
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String, Bool, Float32
from geometry_msgs.msg import Vector3Stamped
from vision_msgs.msg import Detection2DArray
from visualization_msgs.msg import MarkerArray
from action_msgs.msg import GoalInfo

# Custom package messages
from robopy_controller.msg import EngagementStatus
from robopy_controller.robot_ai.services.visual_gesture_pointing_engine import VisualGesturePointingEngine, GestureType
from robopy_controller.robot_ai.cognitive.theory_of_mind_engine import TheoryOfMindEngine, ToMState


class EngagementMonitor(Node):
    def __init__(self):
        super().__init__('engagement_monitor')
        self.get_logger().info("Inizializzazione engagement_monitor...")

        # Parameters
        self.declare_parameter('gaze_timeout_sec', 3.0)
        self.declare_parameter('proxemics_intimate_m', 0.45)
        self.declare_parameter('proxemics_personal_m', 1.2)
        self.declare_parameter('proxemics_social_m', 3.6)
        self.declare_parameter('enable_preemption', True)

        self.gaze_timeout = self.get_parameter('gaze_timeout_sec').value
        self.d_intimate = self.get_parameter('proxemics_intimate_m').value
        self.d_personal = self.get_parameter('proxemics_personal_m').value
        self.d_social = self.get_parameter('proxemics_social_m').value
        self.enable_preemption = self.get_parameter('enable_preemption').value

        # QoS Profiles (BestEffort, depth=1)
        qos_best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        # Subscribers
        self.sub_face = self.create_subscription(
            Detection2DArray, '/hailo/face/detections', self.face_callback, qos_best_effort
        )
        self.sub_gaze = self.create_subscription(
            Vector3Stamped, '/hailo/gaze/direction', self.gaze_callback, qos_best_effort
        )
        # Skeletons 17 keypoints subscription for In-Process Gestures & ToM
        self.sub_pose = self.create_subscription(
            MarkerArray, '/hailo/pose/skeletons', self.pose_callback, qos_best_effort
        )

        # In-Process Engines (Zero extra OS processes / Strict 4GB RAM preservation)
        self.gesture_engine = VisualGesturePointingEngine()
        self.tom_engine = TheoryOfMindEngine()

        # Publishers
        self.pub_status_str = self.create_publisher(
            String, '/engagement/status_str', qos_best_effort
        )
        self.pub_status = self.create_publisher(
            EngagementStatus, '/engagement/status', qos_best_effort
        )
        self.pub_cancel_goal = self.create_publisher(
            GoalInfo, '/engagement/cancel_goal', qos_reliable
        )
        self.pub_interrupt = self.create_publisher(
            String, '/engagement/interrupt', qos_reliable
        )
        self.pub_conv_interrupt = self.create_publisher(
            Bool, '/ai/conversation/interrupt', qos_reliable
        )
        self.pub_proxemics = self.create_publisher(
            Float32, '/engagement/proxemics_distance', qos_best_effort
        )
        self.pub_gesture = self.create_publisher(
            String, '/vision/gesture', qos_reliable
        )
        self.pub_pointing_vector = self.create_publisher(
            String, '/vision/pointing_vector', qos_reliable
        )
        self.pub_tom_state = self.create_publisher(
            String, '/marcus/social_context/tom_state', qos_reliable
        )
        self.pub_fall_emergency = self.create_publisher(
            Bool, '/marcus/social_context/fall_emergency', qos_reliable
        )

        # State Variables
        self.last_face_time = 0.0
        self.last_gaze_time = 0.0
        self.gaze_direction = None
        self.current_engagement_state = "LOST"
        self.current_zone = "PUBLIC"
        self.last_published_distance = 99.0
        
        # Simulated distance for tracking (falls back when skeleton keypoints are missing)
        self.proxemic_distance = 99.0

        # Loop to evaluate engagement at 2Hz
        self.create_timer(0.5, self.evaluate_engagement)

        self.get_logger().info("engagement_monitor avviato.")

    def face_callback(self, msg):
        if len(msg.detections) > 0:
            self.last_face_time = time.time()
            
            # Simple approximation of distance based on bounding box size if we don't have skeleton tracking
            # A face bounding box height/width is inversely proportional to distance.
            # Normal size is around 0.1 to 0.5 of image size.
            bbox = msg.detections[0].bbox
            size_norm = max(bbox.size_x, bbox.size_y)
            if size_norm > 0:
                # Empirical calibration: face size_norm ~0.2 at 1.0 meters
                self.proxemic_distance = 0.2 / size_norm
            else:
                self.proxemic_distance = 2.0
        else:
            # No face detected
            pass

    def pose_callback(self, msg: MarkerArray):
        """Processes 17 keypoints for in-process gestures & Theory of Mind."""
        kps_17 = []
        person_identity = "unknown"
        for marker in msg.markers:
            if marker.ns == "pose_keypoints_17":
                person_identity = marker.text or "unknown"
                for pt in marker.points:
                    kps_17.append((pt.x, pt.y, pt.z))
                break
            elif marker.ns == "skeleton_joints":
                # Fallback from joints marker
                for pt in marker.points:
                    norm_x = (pt.x / 1.5) + 0.5
                    norm_y = (pt.y / 1.5) + 0.5
                    kps_17.append((norm_x, norm_y, 0.8))

        if len(kps_17) < 17:
            return

        # 1. Gesture recognition
        g_res = self.gesture_engine.evaluate_pose(kps_17, estimated_depth_m=self.proxemic_distance)
        if g_res.gesture != GestureType.NONE:
            payload = {
                "gesture": g_res.gesture.value,
                "confidence": round(g_res.confidence, 3),
                "hand": g_res.hand,
                "identity": person_identity,
                "timestamp": g_res.timestamp
            }
            g_msg = String()
            g_msg.data = json.dumps(payload)
            self.pub_gesture.publish(g_msg)

            if g_res.gesture == GestureType.OPEN_PALM:
                self.get_logger().warn(f"🛑 [VISUAL BARGE-IN] Gesto OPEN PALM ({g_res.hand}) rilevato -> E-Stop e interrupt!")
                intr_msg = Bool()
                intr_msg.data = True
                self.pub_conv_interrupt.publish(intr_msg)
                if self.enable_preemption:
                    cancel_msg = GoalInfo()
                    self.pub_cancel_goal.publish(cancel_msg)

            elif g_res.gesture == GestureType.POINTING and g_res.pointing_target:
                tgt = g_res.pointing_target
                p_payload = {
                    "hand": tgt.hand,
                    "origin_3d": [round(c, 3) for c in tgt.origin_3d],
                    "direction_3d": [round(c, 3) for c in tgt.direction_3d],
                    "target_3d": [round(c, 3) for c in tgt.intersection_3d],
                    "confidence": round(tgt.confidence, 3)
                }
                p_msg = String()
                p_msg.data = json.dumps(p_payload)
                self.pub_pointing_vector.publish(p_msg)

        # 2. Theory of Mind Evaluation
        is_face = (time.time() - self.last_face_time < 1.0)
        is_gaze = (time.time() - self.last_gaze_time < self.gaze_timeout)
        tom_eval = self.tom_engine.evaluate_posture(
            kps_17, face_detected=is_face, gaze_at_robot=is_gaze, depth_m=self.proxemic_distance
        )
        tom_msg = String()
        tom_msg.data = tom_eval.state.value
        self.pub_tom_state.publish(tom_msg)

        if tom_eval.state == ToMState.FALL_EMERGENCY:
            self.get_logger().error(f"🚨 [FALL_EMERGENCY] Rilevata caduta utente! {tom_eval.explanation}")
            fall_msg = Bool()
            fall_msg.data = True
            self.pub_fall_emergency.publish(fall_msg)

    def gaze_callback(self, msg):
        self.gaze_direction = msg.vector
        # Check if gaze is directed roughly towards the robot (Z value is high positive, X and Y near zero)
        # Assuming gaze vector is normalized and points along Z axis when looking straight at camera
        if self.gaze_direction.z > 0.8 and abs(self.gaze_direction.x) < 0.3 and abs(self.gaze_direction.y) < 0.3:
            self.last_gaze_time = time.time()

    def evaluate_engagement(self):
        now = time.time()
        time_since_face = now - self.last_face_time
        time_since_gaze = now - self.last_gaze_time

        # Determine Proxemics Zone
        zone = "PUBLIC"
        if self.proxemic_distance < self.d_intimate:
            zone = "INTIMATE"
        elif self.proxemic_distance < self.d_personal:
            zone = "PERSONAL"
        elif self.proxemic_distance < self.d_social:
            zone = "SOCIAL"

        # Determine Engagement State
        new_state = "LOST"
        gaze_score = 0.0

        if time_since_face < 2.0:
            # Face detected recently
            if time_since_gaze < self.gaze_timeout:
                new_state = "ENGAGED"
                gaze_score = 1.0 - (time_since_gaze / self.gaze_timeout)
                gaze_score = max(0.0, min(1.0, gaze_score))
            else:
                new_state = "DISENGAGED"
                gaze_score = 0.0
        else:
            new_state = "LOST"
            gaze_score = 0.0
            self.proxemic_distance = 99.0 # reset to default far distance

        # Check for state transitions and handle preemption
        state_changed = (new_state != self.current_engagement_state)
        zone_changed = (zone != self.current_zone)
        dist_changed = (abs(self.proxemic_distance - self.last_published_distance) > 0.15)

        if state_changed or zone_changed or dist_changed:
            if state_changed:
                self.get_logger().info(f"Stato engagement cambiato: {self.current_engagement_state} -> {new_state} (Zone: {zone}, Dist: {self.proxemic_distance:.2f}m)")
                
                # If we lose the user suddenly while executing a task, trigger preemption/cancel
                if self.enable_preemption and self.current_engagement_state == "ENGAGED" and new_state == "LOST":
                    self.trigger_preemption()

                self.current_engagement_state = new_state
            
            if zone_changed:
                self.current_zone = zone
                
            self.last_published_distance = self.proxemic_distance

            # Publish info
            # 1. Custom message
            status_msg = EngagementStatus()
            status_msg.header.stamp = self.get_clock().now().to_msg()
            status_msg.header.frame_id = "base_link"
            status_msg.status = self.current_engagement_state
            status_msg.gaze_score = gaze_score
            status_msg.distance_m = float(self.proxemic_distance)
            status_msg.zone = self.current_zone
            self.pub_status.publish(status_msg)

            # 2. String status
            str_msg = String()
            str_msg.data = self.current_engagement_state
            self.pub_status_str.publish(str_msg)

            # 3. Proxemics
            prox_msg = Float32()
            prox_msg.data = float(self.proxemic_distance)
            self.pub_proxemics.publish(prox_msg)

    def trigger_preemption(self):
        self.get_logger().warn("🚨 Rilevata perdita improvvisa di engagement! Invio segnali di preemption...")
        
        # 1. Pubblica sul topic interrupt dell'orchestratore
        interrupt_msg = String()
        interrupt_msg.data = "user_lost_engagement"
        self.pub_interrupt.publish(interrupt_msg)

        # 2. Pubblica un GoalInfo vuoto/cancellazione sul topic cancel_goal
        cancel_info = GoalInfo()
        cancel_info.stamp = self.get_clock().now().to_msg()
        # In una vera applicazione, compileremmo l'id del goal corrente, ma lasciandolo a zero/vuoto
        # l'orchestratore intercetta la richiesta di interruzione generica
        self.pub_cancel_goal.publish(cancel_info)


def main(args=None):
    rclpy.init(args=args)
    node = EngagementMonitor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
