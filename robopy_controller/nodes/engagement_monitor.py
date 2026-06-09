#!/usr/bin/env python3
"""
Engagement Monitor
==================
ROS 2 Python node that monitors HRI (Human-Robot Interaction) engagement, gaze,
and proxemic distance, sending cancel and interrupt signals to the robot brain.

Version: 01.00.00
"""

import time
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String, Bool, Float32
from geometry_msgs.msg import Vector3Stamped
from vision_msgs.msg import Detection2DArray
from action_msgs.msg import GoalInfo

# Custom package messages
from robopy_controller.msg import EngagementStatus


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
        self.pub_proxemics = self.create_publisher(
            Float32, '/engagement/proxemics_distance', qos_best_effort
        )

        # State Variables
        self.last_face_time = 0.0
        self.last_gaze_time = 0.0
        self.gaze_direction = None
        self.current_engagement_state = "LOST"
        
        # Simulated distance for tracking (falls back when skeleton keypoints are missing)
        self.proxemic_distance = 99.0

        # Loop to evaluate engagement at 10Hz
        self.create_timer(0.1, self.evaluate_engagement)

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
        if new_state != self.current_engagement_state:
            self.get_logger().info(f"Stato engagement cambiato: {self.current_engagement_state} -> {new_state} (Zone: {zone}, Dist: {self.proxemic_distance:.2f}m)")
            
            # If we lose the user suddenly while executing a task, trigger preemption/cancel
            if self.enable_preemption and self.current_engagement_state == "ENGAGED" and new_state == "LOST":
                self.trigger_preemption()

            self.current_engagement_state = new_state

        # Publish info
        # 1. Custom message
        status_msg = EngagementStatus()
        status_msg.header.stamp = self.get_clock().now().to_msg()
        status_msg.header.frame_id = "base_link"
        status_msg.status = self.current_engagement_state
        status_msg.gaze_score = gaze_score
        status_msg.distance_m = float(self.proxemic_distance)
        status_msg.zone = zone
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
