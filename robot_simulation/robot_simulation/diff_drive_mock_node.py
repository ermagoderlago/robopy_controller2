#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image, CameraInfo, BatteryState, JointState, LaserScan
from std_msgs.msg import String
from visualization_msgs.msg import Marker, MarkerArray
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from tf2_ros import TransformBroadcaster, StaticTransformBroadcaster
import math
import numpy as np
import cv2
import os
import json
import urllib.request

class DiffDriveMockNode(Node):
    def __init__(self):
        super().__init__('diff_drive_mock_node')

        # Marcus Robot URDF aligned topics & frames
        self.cmd_sub = self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_callback, 10)
        self.odom_pub = self.create_publisher(Odometry, '/odom', 10)
        self.odom_wheel_pub = self.create_publisher(Odometry, '/odom_wheel', 10)
        
        # Camera topics
        self.camera_info_pub = self.create_publisher(CameraInfo, '/camera/camera_info', 10)
        self.image_pub = self.create_publisher(Image, '/camera/color/image_raw', 10)
        self.rgb_pub = self.create_publisher(Image, '/rgb/image', 10)
        self.depth_pub = self.create_publisher(Image, '/camera/depth/image_raw', 10)
        
        # Scan topic for 3D navigation & costmaps
        self.scan_pub = self.create_publisher(LaserScan, '/scan', 10)
        
        # 3D Room Environment Markers (Walls + Objects)
        self.marker_pub = self.create_publisher(MarkerArray, '/visualization_marker_array', 10)

        # Full Marcus Telemetry
        self.battery_pub = self.create_publisher(BatteryState, '/battery_state', 10)
        self.diag_pub = self.create_publisher(DiagnosticArray, '/diagnostics', 10)
        self.joint_pub = self.create_publisher(JointState, '/joint_states', 10)

        # AI Orchestrator & Gemini Chat Topics
        self.chat_sub_2 = self.create_subscription(String, '/ai/input/text', self.chat_callback, 10)
        self.chat_pub = self.create_publisher(String, '/robopy/conversation_tx', 10)

        self.tf_broadcaster = TransformBroadcaster(self)
        self.static_tf_broadcaster = StaticTransformBroadcaster(self)

        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self.vx = 0.0
        self.vth = 0.0
        self.last_time = self.get_clock().now()

        # Load GEMINI_API_KEY from environment or file
        self.gemini_key = os.environ.get('GEMINI_API_KEY', '')

        # Broadcast exact marcus_robot URDF TF Tree
        self.publish_static_transforms()

        # Timers
        self.timer = self.create_timer(0.02, self.update_kinematics)           # 50 Hz
        self.cam_timer = self.create_timer(0.066, self.publish_camera)          # 15 FPS
        self.scan_timer = self.create_timer(0.1, self.publish_laserscan)        # 10 Hz /scan
        self.marker_timer = self.create_timer(0.1, self.publish_environment_3d) # 10 Hz 3D Markers
        self.diag_timer = self.create_timer(1.0, self.publish_telemetry)        # 1 Hz Telemetry

        self.get_logger().info(f'Marcus Robot Room SIL Node running. URDF aligned (camera z=0.18m). Gemini Live Key loaded.')

    def publish_static_transforms(self):
        now = self.get_clock().now().to_msg()
        transforms = []

        # 1. base_footprint -> base_link (z=0.033m)
        t0 = TransformStamped()
        t0.header.stamp = now
        t0.header.frame_id = 'base_footprint'
        t0.child_frame_id = 'base_link'
        t0.transform.translation.z = 0.033
        t0.transform.rotation.w = 1.0
        transforms.append(t0)

        # 2. base_link -> camera_link (x=0.05m, y=0.0m, z=0.18m, rpy=0 0 0)
        t1 = TransformStamped()
        t1.header.stamp = now
        t1.header.frame_id = 'base_link'
        t1.child_frame_id = 'camera_link'
        t1.transform.translation.x = 0.05
        t1.transform.translation.y = 0.0
        t1.transform.translation.z = 0.18
        t1.transform.rotation.w = 1.0
        transforms.append(t1)

        # 3. camera_link -> camera_depth_optical_frame (rpy="-1.5708 0 -1.5708")
        t2 = TransformStamped()
        t2.header.stamp = now
        t2.header.frame_id = 'camera_link'
        t2.child_frame_id = 'camera_depth_optical_frame'
        t2.transform.rotation.x = -0.5
        t2.transform.rotation.y = 0.5
        t2.transform.rotation.z = -0.5
        t2.transform.rotation.w = 0.5
        transforms.append(t2)

        # 4. camera_link -> camera_optical_frame
        t3 = TransformStamped()
        t3.header.stamp = now
        t3.header.frame_id = 'camera_link'
        t3.child_frame_id = 'camera_optical_frame'
        t3.transform.rotation.x = -0.5
        t3.transform.rotation.y = 0.5
        t3.transform.rotation.z = -0.5
        t3.transform.rotation.w = 0.5
        transforms.append(t3)

        self.static_tf_broadcaster.sendTransform(transforms)

    def cmd_vel_callback(self, msg):
        self.vx = msg.linear.x
        self.vth = msg.angular.z

    def chat_callback(self, msg):
        text = msg.data.strip()
        self.get_logger().info(f"Ricevuto messaggio per Gemini AI: '{text}'")
        
        # Query Gemini API in background
        reply = self.query_gemini(text)
        
        response = String()
        response.data = reply
        self.chat_pub.publish(response)
        self.get_logger().info(f"Inviata risposta Gemini: '{reply}'")

    def query_gemini(self, user_text):
        if not self.gemini_key:
            return f"Marcus AI: GEMINI_API_KEY non configurata. Posa: ({self.x:.2f}, {self.y:.2f})"
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.gemini_key}"
        prompt = (
            f"Sei Marcus, il robot mobile differenziale in simulazione ROS 2 su WSL. "
            f"La tua posa corrente e' X={self.x:.2f}m, Y={self.y:.2f}m, Yaw={math.degrees(self.theta):.1f} deg. "
            f"Rispondi in italiano in modo sintetico, amichevole ed esaustivo. "
            f"Messaggio utente: {user_text}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                ans = res_data['candidates'][0]['content']['parts'][0]['text']
                return f"Marcus AI (Gemini 2.5): {ans.strip()}"
        except Exception as e:
            self.get_logger().error(f"Gemini API Exception: {e}")
            return f"Marcus AI: Ho ricevuto '{user_text}'. Posa: ({self.x:.2f}m, {self.y:.2f}m)."

    def update_kinematics(self):
        current_time = self.get_clock().now()
        dt = (current_time - self.last_time).nanoseconds / 1e9
        self.last_time = current_time

        delta_x = self.vx * math.cos(self.theta) * dt
        delta_y = self.vx * math.sin(self.theta) * dt
        delta_th = self.vth * dt

        self.x += delta_x
        self.y += delta_y
        self.theta += delta_th

        qz = math.sin(self.theta / 2.0)
        qw = math.cos(self.theta / 2.0)

        # /odom & /odom_wheel
        odom = Odometry()
        odom.header.stamp = current_time.to_msg()
        odom.header.frame_id = 'odom'
        odom.child_frame_id = 'base_footprint'

        odom.pose.pose.position.x = self.x
        odom.pose.pose.position.y = self.y
        odom.pose.pose.position.z = 0.0
        odom.pose.pose.orientation.z = qz
        odom.pose.pose.orientation.w = qw

        odom.twist.twist.linear.x = self.vx
        odom.twist.twist.angular.z = self.vth

        self.odom_pub.publish(odom)
        self.odom_wheel_pub.publish(odom)

        # TF: odom -> base_footprint
        t = TransformStamped()
        t.header.stamp = current_time.to_msg()
        t.header.frame_id = 'odom'
        t.child_frame_id = 'base_footprint'
        t.transform.translation.x = self.x
        t.transform.translation.y = self.y
        t.transform.translation.z = 0.0
        t.transform.rotation.z = qz
        t.transform.rotation.w = qw

        self.tf_broadcaster.sendTransform(t)

        # Joint States
        js = JointState()
        js.header.stamp = current_time.to_msg()
        js.name = ['left_wheel_joint', 'right_wheel_joint']
        js.position = [self.x / 0.033, self.x / 0.033]
        js.velocity = [self.vx / 0.033, self.vx / 0.033]
        self.joint_pub.publish(js)

    def publish_laserscan(self):
        now = self.get_clock().now().to_msg()
        scan = LaserScan()
        scan.header.stamp = now
        scan.header.frame_id = 'base_link'
        scan.angle_min = -1.5708 # -90 deg
        scan.angle_max = 1.5708  # +90 deg
        scan.angle_increment = 0.0174533 # 1 deg
        scan.time_increment = 0.0
        scan.scan_time = 0.1
        scan.range_min = 0.1
        scan.range_max = 10.0

        num_readings = int((scan.angle_max - scan.angle_min) / scan.angle_increment) + 1
        ranges = []

        # Room Walls at X=+3.0, X=-3.0, Y=+3.0, Y=-3.0 and obstacles
        obstacles = [
            (2.0, 0.5, 0.3),   # Red Box
            (-1.2, 1.2, 0.4),  # Table
            (1.0, -1.5, 0.6)   # Bookshelf
        ]

        for i in range(num_readings):
            angle = scan.angle_min + i * scan.angle_increment
            global_angle = self.theta + angle
            
            # Raycast against 6m x 6m Room Walls
            r_wall = 10.0
            cos_a = math.cos(global_angle)
            sin_a = math.sin(global_angle)

            if abs(cos_a) > 1e-4:
                if cos_a > 0:
                    d = (3.0 - self.x) / cos_a
                else:
                    d = (-3.0 - self.x) / cos_a
                if d > 0 and d < r_wall:
                    r_wall = d

            if abs(sin_a) > 1e-4:
                if sin_a > 0:
                    d = (3.0 - self.y) / sin_a
                else:
                    d = (-3.0 - self.y) / sin_a
                if d > 0 and d < r_wall:
                    r_wall = d

            # Raycast against obstacles
            r_min = r_wall
            for ox, oy, orad in obstacles:
                dx = ox - self.x
                dy = oy - self.y
                dist_obs = math.hypot(dx, dy)
                ang_obs = math.atan2(dy, dx) - self.theta
                ang_obs = math.atan2(math.sin(ang_obs), math.cos(ang_obs))
                if abs(angle - ang_obs) < 0.2:
                    d_est = dist_obs - orad
                    if d_est > 0.1 and d_est < r_min:
                        r_min = d_est

            ranges.append(float(r_min))

        scan.ranges = ranges
        self.scan_pub.publish(scan)

    def publish_environment_3d(self):
        now = self.get_clock().now().to_msg()
        markers = MarkerArray()

        # Walls (North, South, East, West)
        walls = [
            (1, 0.0, 3.0, 1.0, 6.0, 0.1, 2.0, "PARETE NORD", (0.7, 0.7, 0.8)),
            (2, 0.0, -3.0, 1.0, 6.0, 0.1, 2.0, "PARETE SUD", (0.7, 0.7, 0.8)),
            (3, 3.0, 0.0, 1.0, 0.1, 6.0, 2.0, "PARETE EST", (0.7, 0.7, 0.8)),
            (4, -3.0, 0.0, 1.0, 0.1, 6.0, 2.0, "PARETE OVEST", (0.7, 0.7, 0.8)),
        ]
        for mid, px, py, pz, sx, sy, sz, label, (cr, cg, cb) in walls:
            m = Marker()
            m.header.stamp = now
            m.header.frame_id = 'odom'
            m.id = mid
            m.type = Marker.CUBE
            m.action = Marker.ADD
            m.pose.position.x = px
            m.pose.position.y = py
            m.pose.position.z = pz
            m.scale.x = sx
            m.scale.y = sy
            m.scale.z = sz
            m.color.r = cr
            m.color.g = cg
            m.color.b = cb
            m.color.a = 0.8
            markers.markers.append(m)

        # Obstacle 1: Red Box at (2.0, 0.5)
        m_box = Marker()
        m_box.header.stamp = now
        m_box.header.frame_id = 'odom'
        m_box.id = 10
        m_box.type = Marker.CUBE
        m_box.action = Marker.ADD
        m_box.pose.position.x = 2.0
        m_box.pose.position.y = 0.5
        m_box.pose.position.z = 0.3
        m_box.scale.x = 0.6
        m_box.scale.y = 0.6
        m_box.scale.z = 0.6
        m_box.color.r = 1.0
        m_box.color.g = 0.0
        m_box.color.b = 0.0
        m_box.color.a = 0.95
        markers.markers.append(m_box)

        # Label for Red Box
        m_box_txt = Marker()
        m_box_txt.header.stamp = now
        m_box_txt.header.frame_id = 'odom'
        m_box_txt.id = 11
        m_box_txt.type = Marker.TEXT_VIEW_FACING
        m_box_txt.action = Marker.ADD
        m_box_txt.pose.position.x = 2.0
        m_box_txt.pose.position.y = 0.5
        m_box_txt.pose.position.z = 0.8
        m_box_txt.scale.z = 0.25
        m_box_txt.color.r = 1.0
        m_box_txt.color.g = 1.0
        m_box_txt.color.b = 1.0
        m_box_txt.color.a = 1.0
        m_box_txt.text = "CUBO ROSSO"
        markers.markers.append(m_box_txt)

        # Obstacle 2: Table at (-1.2, 1.2)
        m_table = Marker()
        m_table.header.stamp = now
        m_table.header.frame_id = 'odom'
        m_table.id = 20
        m_table.type = Marker.CUBE
        m_table.action = Marker.ADD
        m_table.pose.position.x = -1.2
        m_table.pose.position.y = 1.2
        m_table.pose.position.z = 0.4
        m_table.scale.x = 1.0
        m_table.scale.y = 0.8
        m_table.scale.z = 0.8
        m_table.color.r = 0.5
        m_table.color.g = 0.3
        m_table.color.b = 0.1
        m_table.color.a = 0.9
        markers.markers.append(m_table)

        # Obstacle 3: Bookshelf at (1.0, -1.5)
        m_shelf = Marker()
        m_shelf.header.stamp = now
        m_shelf.header.frame_id = 'odom'
        m_shelf.id = 30
        m_shelf.type = Marker.CUBE
        m_shelf.action = Marker.ADD
        m_shelf.pose.position.x = 1.0
        m_shelf.pose.position.y = -1.5
        m_shelf.pose.position.z = 0.75
        m_shelf.scale.x = 0.4
        m_shelf.scale.y = 1.2
        m_shelf.scale.z = 1.5
        m_shelf.color.r = 0.2
        m_shelf.color.g = 0.4
        m_shelf.color.b = 0.8
        m_shelf.color.a = 0.9
        markers.markers.append(m_shelf)

        self.marker_pub.publish(markers)

    def publish_camera(self):
        now = self.get_clock().now().to_msg()
        
        info = CameraInfo()
        info.header.stamp = now
        info.header.frame_id = 'camera_optical_frame'
        info.width = 640
        info.height = 480
        info.distortion_model = 'plumb_bob'
        info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
        info.k = [500.0, 0.0, 320.0, 0.0, 500.0, 240.0, 0.0, 0.0, 1.0]
        info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        info.p = [500.0, 0.0, 320.0, 0.0, 0.0, 500.0, 240.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        self.camera_info_pub.publish(info)

        h, w = 480, 640
        img = np.zeros((h, w, 3), dtype=np.uint8)

        img[0:240, :] = [230, 206, 135] # Sky
        img[240:480, :] = [60, 60, 60]  # Ground
        cv2.line(img, (0, 240), (640, 240), (200, 200, 200), 2)

        yaw_deg = math.degrees(self.theta)
        for i in range(-5, 6):
            start_x = 320 + i * 80 - int(yaw_deg * 4)
            cv2.line(img, (320 - int(yaw_deg * 2), 240), (start_x, 480), (100, 100, 100), 1)

        offset_x = int((self.x * 100) % 20)
        for y_line in range(250, 480, 20):
            cv2.line(img, (0, y_line + offset_x), (640, y_line + offset_x), (80, 80, 80), 1)

        # Render Red Box in perspective
        dx = 2.0 - self.x
        dy = 0.5 - self.y
        dist_to_obs = math.hypot(dx, dy)
        ang_to_obs = math.atan2(dy, dx) - self.theta
        ang_to_obs = math.atan2(math.sin(ang_to_obs), math.cos(ang_to_obs))

        if dist_to_obs > 0.2 and dist_to_obs < 6.0 and abs(ang_to_obs) < 1.0:
            scale = int(220.0 / dist_to_obs)
            center_x = int(320 + math.tan(ang_to_obs) * 500)
            center_y = int(240 + 60 / dist_to_obs)
            x1, y1 = max(0, center_x - scale // 2), max(0, center_y - scale // 2)
            x2, y2 = min(w, center_x + scale // 2), min(h, center_y + scale // 2)
            if x2 > x1 and y2 > y1:
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 220), -1)
                cv2.rectangle(img, (x1, y1), (x2, y2), (255, 255, 255), 2)
                cv2.putText(img, 'CUBO ROSSO', (x1 + 5, y1 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        hud_text = f"Marcus Room SIL | X:{self.x:.2f}m Y:{self.y:.2f}m Yaw:{yaw_deg:.1f}deg"
        cv2.rectangle(img, (10, 10), (630, 40), (0, 0, 0), -1)
        cv2.putText(img, hud_text, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

        color_msg = Image()
        color_msg.header.stamp = now
        color_msg.header.frame_id = 'camera_optical_frame'
        color_msg.height = h
        color_msg.width = w
        color_msg.encoding = 'bgr8'
        color_msg.step = w * 3
        color_msg.data = img.tobytes()

        self.image_pub.publish(color_msg)
        self.rgb_pub.publish(color_msg)

        depth_img = np.full((h, w), 3000, dtype=np.uint16)
        depth_img[240:480, :] = 1500
        if dist_to_obs > 0.2 and dist_to_obs < 6.0 and 'x2' in locals() and x2 > x1 and y2 > y1:
            depth_img[y1:y2, x1:x2] = int(dist_to_obs * 1000)

        depth_msg = Image()
        depth_msg.header.stamp = now
        depth_msg.header.frame_id = 'camera_optical_frame'
        depth_msg.height = h
        depth_msg.width = w
        depth_msg.encoding = '16UC1'
        depth_msg.step = w * 2
        depth_msg.data = depth_img.tobytes()
        self.depth_pub.publish(depth_msg)

    def publish_telemetry(self):
        now = self.get_clock().now().to_msg()

        bat = BatteryState()
        bat.header.stamp = now
        bat.voltage = 12.4
        bat.percentage = 0.88
        bat.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_DISCHARGING
        self.battery_pub.publish(bat)

        diag_msg = DiagnosticArray()
        diag_msg.header.stamp = now
        status = DiagnosticStatus()
        status.name = 'Marcus Room SIL Health'
        status.level = DiagnosticStatus.OK
        status.message = 'Room Simulation Nominal'
        status.values = [
            KeyValue(key='Mode', value='Marcus Room SIL + Gemini Live AI'),
            KeyValue(key='CPU_Usage', value='15%'),
            KeyValue(key='RAM_Usage', value='1.4GB / 8GB')
        ]
        diag_msg.status.append(status)
        self.diag_pub.publish(diag_msg)

def main(args=None):
    rclpy.init(args=args)
    node = DiffDriveMockNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
