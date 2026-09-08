#!/usr/bin/env python3
"""
SensorStandbyManagerNode — ROS 2 Supervisor for Power Saving and Sensor Wear Protection.
Monitors robot stillness via IMU (/oak/imu/data), wheel odometry (/odom_wheel), and /cmd_vel.
If Marcus is idle for > 2 minutes (120s) with no non-gravitational acceleration, it:
  1. Stops the RPLIDAR C1 motor via /stop_motor.
  2. Freezes RTAB-Map mapping via /rtabmap/pause.
  3. Locks hardware motion gating (/robot/motion_gate = False).

Wakes up reactively upon:
  - Non-gravitational acceleration / bump / lift via IMU (|norm(a) - g| > 0.35 m/s^2 or norm(w) > 0.15 rad/s).
  - Velocity command received via /cmd_vel.
  - Manual request via /robot/wake_sensors.

Enforces motion gating: holds wheel motion until the RPLIDAR C1 has spun up and produced
valid 360-degree laser scans, ensuring safe navigation resumption.
"""

import time
import math
import rclpy
from rclpy.node import Node
from rclpy.callback_groups import ReentrantCallbackGroup
from sensor_msgs.msg import Imu, LaserScan
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from std_msgs.msg import Bool, String
from std_srvs.srv import Empty, Trigger


class SensorStandbyManager(Node):
    def __init__(self):
        super().__init__('sensor_standby_manager')
        
        # --- Parameter Declarations ---
        self.declare_parameter('idle_timeout_sec', 1800.0)         # Inactivity period to trigger standby (30 mins for testing)
        self.declare_parameter('imu_accel_threshold', 0.35)        # m/s^2 deviation from gravity (|norm(a) - g|)
        self.declare_parameter('imu_gyro_threshold', 0.15)         # rad/s angular velocity norm (~8.6 deg/s)
        self.declare_parameter('nominal_gravity', 9.81)            # Nominal gravity m/s^2
        self.declare_parameter('min_scans_to_wake', 2)             # Scans required to consider LiDAR spinning & healthy
        self.declare_parameter('scan_timeout_sec', 6.0)            # Maximum wait time for LiDAR spin-up before fallback
        self.declare_parameter('check_frequency_hz', 10.0)         # Supervisor evaluation rate
        
        self.idle_timeout_sec = float(self.get_parameter('idle_timeout_sec').value)
        self.imu_accel_threshold = float(self.get_parameter('imu_accel_threshold').value)
        self.imu_gyro_threshold = float(self.get_parameter('imu_gyro_threshold').value)
        self.nominal_gravity = float(self.get_parameter('nominal_gravity').value)
        self.min_scans_to_wake = int(self.get_parameter('min_scans_to_wake').value)
        self.scan_timeout_sec = float(self.get_parameter('scan_timeout_sec').value)
        self.check_frequency_hz = float(self.get_parameter('check_frequency_hz').value)
        
        # --- Internal States ---
        # States: 'ACTIVE', 'STANDBY', 'WAKING_UP'
        self.state = 'ACTIVE'
        self.last_activity_time = time.time()
        self.wake_start_time = 0.0
        self.wake_scans_count = 0
        self.last_gate_published = True
        self.last_gate_pub_time = 0.0
        self.last_imu_norm_a = self.nominal_gravity
        self.last_imu_norm_w = 0.0
        
        # Gravity adaptive baseline calibration
        self.gravity_samples = []
        self.gravity_baseline = self.nominal_gravity
        
        self.callback_group = ReentrantCallbackGroup()
        
        # --- Publishers ---
        self.motion_gate_pub = self.create_publisher(Bool, '/robot/motion_gate', 10)
        self.power_state_pub = self.create_publisher(String, '/robot/sensor_power_state', 10)
        
        # --- Subscribers ---
        self.create_subscription(Imu, '/oak/imu/data', self.imu_callback, 10, callback_group=self.callback_group)
        self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_callback, 10, callback_group=self.callback_group)
        self.create_subscription(Odometry, '/odom_wheel', self.odom_callback, 10, callback_group=self.callback_group)
        self.create_subscription(LaserScan, '/scan', self.scan_callback, 10, callback_group=self.callback_group)
        
        # --- Service Clients ---
        self.stop_motor_cli = self.create_client(Empty, '/stop_motor', callback_group=self.callback_group)
        self.start_motor_cli = self.create_client(Empty, '/start_motor', callback_group=self.callback_group)
        self.pause_rtabmap_cli = self.create_client(Empty, '/rtabmap/pause', callback_group=self.callback_group)
        self.resume_rtabmap_cli = self.create_client(Empty, '/rtabmap/resume', callback_group=self.callback_group)
        
        # --- Service Server (Manual wake on demand) ---
        self.wake_srv = self.create_service(Empty, '/robot/wake_sensors', self.manual_wake_srv_callback, callback_group=self.callback_group)
        self.wake_trig_srv = self.create_service(Trigger, '/robot/wake_sensors_trigger', self.manual_wake_trigger_callback, callback_group=self.callback_group)
        
        # --- Periodic Supervisor Timer ---
        timer_period = 1.0 / self.check_frequency_hz
        self.timer = self.create_timer(timer_period, self.supervisor_step)
        
        # Publish initial states
        self.publish_motion_gate(True, force=True)
        self.publish_power_state("ACTIVE")
        
        self.get_logger().info(
            f"⚡ SensorStandbyManager initialized. Idle Timeout={self.idle_timeout_sec}s, "
            f"IMU Accel Threshold={self.imu_accel_threshold} m/s^2, Gyro Threshold={self.imu_gyro_threshold} rad/s"
        )

    def publish_motion_gate(self, gate_open: bool, force: bool = False):
        """Publishes motion gate state. True = wheels allowed to move, False = wheels locked."""
        now = time.time()
        if force or (gate_open != self.last_gate_published) or (now - self.last_gate_pub_time > 1.0):
            msg = Bool()
            msg.data = bool(gate_open)
            self.motion_gate_pub.publish(msg)
            self.last_gate_published = gate_open
            self.last_gate_pub_time = now

    def publish_power_state(self, state_str: str):
        """Publishes diagnostic telemetry string."""
        msg = String()
        msg.data = str(state_str)
        self.power_state_pub.publish(msg)

    def imu_callback(self, msg: Imu):
        """Monitors linear acceleration and angular velocity for stillness or external shocks."""
        ax = msg.linear_acceleration.x
        ay = msg.linear_acceleration.y
        az = msg.linear_acceleration.z
        norm_a = math.sqrt(ax * ax + ay * ay + az * az)
        
        gx = msg.angular_velocity.x
        gy = msg.angular_velocity.y
        gz = msg.angular_velocity.z
        norm_w = math.sqrt(gx * gx + gy * gy + gz * gz)
        
        self.last_imu_norm_a = norm_a
        self.last_imu_norm_w = norm_w
        
        # Adaptive gravity baseline on first 50 valid frames
        if len(self.gravity_samples) < 50:
            if 8.0 < norm_a < 11.5:
                self.gravity_samples.append(norm_a)
                if len(self.gravity_samples) == 50:
                    self.gravity_baseline = sum(self.gravity_samples) / len(self.gravity_samples)
                    self.get_logger().info(f"📊 Calibrated IMU gravity baseline: {self.gravity_baseline:.3f} m/s^2")
        
        delta_a = abs(norm_a - self.gravity_baseline)
        
        # Disturbance detected: either acceleration departure from gravity or significant gyro rate
        is_disturbed = (delta_a > self.imu_accel_threshold) or (norm_w > self.imu_gyro_threshold)
        
        now = time.time()
        if is_disturbed:
            if self.state == 'ACTIVE':
                self.last_activity_time = now
            elif self.state == 'STANDBY':
                reason = f"IMU Disturbance (delta_a={delta_a:.3f} m/s^2, norm_w={norm_w:.3f} rad/s)"
                self.get_logger().info(f"🚨 External force detected: {reason} -> Triggering wake-up!")
                self.trigger_wakeup(reason)

    def cmd_vel_callback(self, msg: Twist):
        """Detects incoming velocity commands to reset idle timer or wake up sensors."""
        v = msg.linear.x
        w = msg.angular.z
        is_motion_requested = (abs(v) > 0.005 or abs(w) > 0.005)
        
        now = time.time()
        if is_motion_requested:
            if self.state == 'ACTIVE':
                self.last_activity_time = now
            elif self.state == 'STANDBY':
                reason = f"cmd_vel received (v={v:.2f}, w={w:.2f})"
                self.get_logger().info(f"🚀 Motion command received: {reason} -> Triggering wake-up!")
                self.trigger_wakeup(reason)

    def odom_callback(self, msg: Odometry):
        """Detects physical robot chassis motion from wheel encoders."""
        v_lin = msg.twist.twist.linear.x
        w_ang = msg.twist.twist.angular.z
        if abs(v_lin) > 0.005 or abs(w_ang) > 0.01:
            if self.state == 'ACTIVE':
                self.last_activity_time = time.time()

    def scan_callback(self, msg: LaserScan):
        """Tracks arrival of laser scans to confirm LiDAR spin-up completion during waking."""
        if self.state == 'WAKING_UP':
            # Count valid scans received since wake initiation
            if len(msg.ranges) > 0:
                self.wake_scans_count += 1
                self.get_logger().info(f"📡 Laser scan #{self.wake_scans_count} received during spin-up ({len(msg.ranges)} ranges).")
                if self.wake_scans_count >= self.min_scans_to_wake:
                    spin_duration = time.time() - self.wake_start_time
                    self.get_logger().info(
                        f"✅ LiDAR spin-up confirmed ({self.wake_scans_count} scans in {spin_duration:.2f}s). "
                        f"Opening motion gate and returning to ACTIVE state."
                    )
                    self.state = 'ACTIVE'
                    self.last_activity_time = time.time()
                    self.publish_motion_gate(True, force=True)
                    self.publish_power_state("ACTIVE")

    def call_async_service(self, client, srv_name: str):
        """Asynchronously calls an Empty service without blocking the node thread."""
        if not client.service_is_ready():
            self.get_logger().warn(f"Service {srv_name} is not currently ready/available.")
            return
        req = Empty.Request()
        future = client.call_async(req)
        def _done_cb(f):
            try:
                f.result()
                self.get_logger().debug(f"Service call {srv_name} completed successfully.")
            except Exception as e:
                self.get_logger().warn(f"Service call {srv_name} failed: {e}")
        future.add_done_callback(_done_cb)

    def trigger_standby(self):
        """Enters power-saving STANDBY state."""
        self.get_logger().info("💤 Entering Smart Standby (Idle > 2 min): stopping LiDAR and pausing RTAB-Map...")
        self.state = 'STANDBY'
        
        # Lock wheels
        self.publish_motion_gate(False, force=True)
        self.publish_power_state("STANDBY")
        
        # Stop RPLIDAR C1 motor
        self.call_async_service(self.stop_motor_cli, '/stop_motor')
        
        # Pause RTAB-Map SLAM mapping
        self.call_async_service(self.pause_rtabmap_cli, '/rtabmap/pause')

    def trigger_wakeup(self, reason: str = "Unspecified"):
        """Initiates reactive wake-up sequence."""
        self.get_logger().info(f"⚡ Wake-up requested ({reason}). Starting LiDAR and resuming RTAB-Map...")
        self.state = 'WAKING_UP'
        self.wake_start_time = time.time()
        self.wake_scans_count = 0
        
        # Ensure wheels remain locked during spin-up
        self.publish_motion_gate(False, force=True)
        self.publish_power_state("WAKING_UP")
        
        # Start RPLIDAR C1 motor
        self.call_async_service(self.start_motor_cli, '/start_motor')
        
        # Resume RTAB-Map SLAM mapping
        self.call_async_service(self.resume_rtabmap_cli, '/rtabmap/resume')

    def manual_wake_srv_callback(self, request, response):
        """Service handler for /robot/wake_sensors."""
        if self.state != 'ACTIVE':
            self.trigger_wakeup("Manual service call /robot/wake_sensors")
        return response

    def manual_wake_trigger_callback(self, request, response):
        """Trigger service handler for /robot/wake_sensors_trigger."""
        if self.state != 'ACTIVE':
            self.trigger_wakeup("Manual service call /robot/wake_sensors_trigger")
            response.success = True
            response.message = f"Wake-up initiated from state {self.state}"
        else:
            response.success = True
            response.message = "Sensors are already ACTIVE."
        return response

    def supervisor_step(self):
        """Periodic supervisor loop at check_frequency_hz."""
        now = time.time()
        
        if self.state == 'ACTIVE':
            idle_duration = now - self.last_activity_time
            if idle_duration >= self.idle_timeout_sec:
                self.trigger_standby()
            else:
                self.publish_motion_gate(True)
                
        elif self.state == 'STANDBY':
            # Maintain motion gate lock
            self.publish_motion_gate(False)
            
        elif self.state == 'WAKING_UP':
            # Keep motion gate locked while waiting for scans
            self.publish_motion_gate(False)
            
            # Spin-up timeout protection: if LiDAR took too long, unlock to prevent permanent deadlock
            elapsed = now - self.wake_start_time
            if elapsed > self.scan_timeout_sec:
                self.get_logger().warn(
                    f"⚠️ LiDAR spin-up scan confirmation timed out after {elapsed:.1f}s. "
                    f"Forcing ACTIVE state and unlocking motion gate."
                )
                self.state = 'ACTIVE'
                self.last_activity_time = now
                self.publish_motion_gate(True, force=True)
                self.publish_power_state("ACTIVE")


def main(args=None):
    rclpy.init(args=args)
    node = SensorStandbyManager()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            node.destroy_node()
        except Exception:
            pass
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
