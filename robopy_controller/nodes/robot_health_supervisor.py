#!/usr/bin/env python3
"""
robot_health_supervisor.py - Marcus AI System Health Supervisor & Safety Arbitrator

Monitors system parameters against strict numerical thresholds:
- GREEN: Normal Operation (VIO C >= 70, CPU < 70C, RAM < 3.2GB, Battery > 11.1V)
- YELLOW: Dynamic Speed Limit -50% (VIO C 30-69, CPU 70-80C, RAM 3.2-3.6GB)
- RED: Active SAFE_STOP Override (VIO C < 30 for >2s, Heartbeat >300ms, CPU >80C, RAM >3.7GB, Battery <10.5V)

Priority 0 Hard Arbitration:
Publishes /cmd_vel_mux/input/safety_override (Twist message = 0.0) which preempts Nav2 and teleop commands on twist_mux.
"""

import psutil
import time
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import Float32, String
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu, Image


class RobotHealthSupervisor(Node):
    def __init__(self):
        super().__init__('robot_health_supervisor')

        # Parameters
        self.declare_parameter('vio_yellow_thresh', 70.0)
        self.declare_parameter('vio_red_thresh', 30.0)
        self.declare_parameter('cpu_temp_yellow', 70.0)
        self.declare_parameter('cpu_temp_red', 80.0)
        self.declare_parameter('ram_yellow_gb', 3.2)
        self.declare_parameter('ram_red_gb', 3.7)
        self.declare_parameter('batt_yellow_v', 11.1)
        self.declare_parameter('batt_red_v', 10.5)
        self.declare_parameter('sensor_timeout_sec', 0.3)  # 300 ms timeout

        self.vio_yellow_thresh = self.get_parameter('vio_yellow_thresh').get_parameter_value().double_value
        self.vio_red_thresh = self.get_parameter('vio_red_thresh').get_parameter_value().double_value
        self.cpu_temp_yellow = self.get_parameter('cpu_temp_yellow').get_parameter_value().double_value
        self.cpu_temp_red = self.get_parameter('cpu_temp_red').get_parameter_value().double_value
        self.ram_yellow_gb = self.get_parameter('ram_yellow_gb').get_parameter_value().double_value
        self.ram_red_gb = self.get_parameter('ram_red_gb').get_parameter_value().double_value
        self.batt_yellow_v = self.get_parameter('batt_yellow_v').get_parameter_value().double_value
        self.batt_red_v = self.get_parameter('batt_red_v').get_parameter_value().double_value
        self.sensor_timeout_sec = self.get_parameter('sensor_timeout_sec').get_parameter_value().double_value

        # QoS
        reliable_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        # State Variables
        self.current_state = "GREEN"
        self.latest_vio_confidence = 100.0
        self.latest_battery_voltage = 12.4  # Nominal 3S LiPo
        self.last_imu_time = time.time()
        self.last_camera_time = time.time()
        self.red_vio_start_time = None

        # Subscriptions
        self.create_subscription(Float32, '/vins/quality_metrics', self.vio_quality_cb, reliable_qos)
        self.create_subscription(Float32, '/motor/battery_voltage', self.battery_cb, reliable_qos)
        self.create_subscription(Imu, '/oak/imu/data', self.imu_cb, sensor_qos)
        self.create_subscription(Image, '/rgb/image', self.camera_cb, sensor_qos)

        # Publishers
        self.pub_status = self.create_publisher(String, '/robot/health_status', reliable_qos)
        self.pub_safety_override = self.create_publisher(Twist, '/cmd_vel_mux/input/safety_override', reliable_qos)

        # Monitoring loop at 10 Hz
        self.timer = self.create_timer(0.1, self.health_check_loop)

        self.get_logger().info('RobotHealthSupervisor node started.')

    def vio_quality_cb(self, msg: Float32):
        self.latest_vio_confidence = float(msg.data)

    def battery_cb(self, msg: Float32):
        self.latest_battery_voltage = float(msg.data)

    def imu_cb(self, msg: Imu):
        self.last_imu_time = time.time()

    def camera_cb(self, msg: Image):
        self.last_camera_time = time.time()

    def get_cpu_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            if 'cpu_thermal' in temps:
                return temps['cpu_thermal'][0].current
            elif 'coretemp' in temps:
                return temps['coretemp'][0].current
        except Exception:
            pass
        return 45.0  # Fallback nominal temperature

    def health_check_loop(self):
        now = time.time()

        # Check Telemetry & Hardware
        cpu_temp = self.get_cpu_temp()
        ram_used_gb = psutil.virtual_memory().used / (1024 ** 3)
        imu_delay = now - self.last_imu_time
        camera_delay = now - self.last_camera_time

        is_red = False
        is_yellow = False
        reasons = []

        # 1. Sensor Heartbeat Timeout Check (> 300 ms)
        if imu_delay > self.sensor_timeout_sec:
            is_red = True
            reasons.append(f'IMU Timeout ({imu_delay:.2f}s)')
        if camera_delay > self.sensor_timeout_sec:
            is_red = True
            reasons.append(f'Camera Timeout ({camera_delay:.2f}s)')

        # 2. VIO Confidence Check
        if self.latest_vio_confidence < self.vio_red_thresh:
            if self.red_vio_start_time is None:
                self.red_vio_start_time = now
            elif (now - self.red_vio_start_time) > 2.0:  # Persistent RED for > 2.0s
                is_red = True
                reasons.append(f'VIO Low Quality ({self.latest_vio_confidence:.1f})')
        else:
            self.red_vio_start_time = None
            if self.latest_vio_confidence < self.vio_yellow_thresh:
                is_yellow = True
                reasons.append(f'VIO Degraded ({self.latest_vio_confidence:.1f})')

        # 3. CPU Temperature Check
        if cpu_temp >= self.cpu_temp_red:
            is_red = True
            reasons.append(f'CPU Overheat ({cpu_temp:.1f}C)')
        elif cpu_temp >= self.cpu_temp_yellow:
            is_yellow = True
            reasons.append(f'CPU High Temp ({cpu_temp:.1f}C)')

        # 4. RAM Usage Check
        if ram_used_gb >= self.ram_red_gb:
            is_red = True
            reasons.append(f'RAM Critical ({ram_used_gb:.2f}GB)')
        elif ram_used_gb >= self.ram_yellow_gb:
            is_yellow = True
            reasons.append(f'RAM High ({ram_used_gb:.2f}GB)')

        # 5. Battery Voltage Check
        if self.latest_battery_voltage <= self.batt_red_v:
            is_red = True
            reasons.append(f'Battery Critical ({self.latest_battery_voltage:.1f}V)')
        elif self.latest_battery_voltage <= self.batt_yellow_v:
            is_yellow = True
            reasons.append(f'Battery Low ({self.latest_battery_voltage:.1f}V)')

        # Evaluate Final State
        if is_red:
            new_state = "RED"
        elif is_yellow:
            new_state = "YELLOW"
        else:
            new_state = "GREEN"

        if new_state != self.current_state:
            self.get_logger().info(f'Health State Changed: {self.current_state} -> {new_state}. Reasons: {", ".join(reasons)}')
            self.current_state = new_state

        # Publish Status Message
        status_msg = String()
        status_msg.data = f"{self.current_state}|{', '.join(reasons)}"
        self.pub_status.publish(status_msg)

        # Execute Priority 0 SAFE_STOP Hard Arbitration Override if RED
        if self.current_state == "RED":
            stop_msg = Twist()
            stop_msg.linear.x = 0.0
            stop_msg.linear.y = 0.0
            stop_msg.linear.z = 0.0
            stop_msg.angular.x = 0.0
            stop_msg.angular.y = 0.0
            stop_msg.angular.z = 0.0
            self.pub_safety_override.publish(stop_msg)


def main(args=None):
    rclpy.init(args=args)
    node = RobotHealthSupervisor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
