#!/usr/bin/env python3
"""
WaveshareMotorDriverNode — ROS 2 Python node for Waveshare General Driver (ESP32)
Controls two motors via serial USB JSON protocol, reads encoder feedback (odl/odr),
calculates differential drive odometry, and broadcasts tf (odom -> base_link).
Includes a 500ms command timeout watchdog for safety.
"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, Point, Pose, Quaternion, Vector3, TransformStamped
from nav_msgs.msg import Odometry
from tf2_ros import TransformBroadcaster
from sensor_msgs.msg import Imu, BatteryState
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
import serial
import json
import threading
import time
import math

class WaveshareMotorDriver(Node):
    def __init__(self):
        super().__init__('waveshare_motor_driver')
        
        # --- Parameter Declaration ---
        self.declare_parameter('serial_port', '/dev/ttyUSB0')
        self.declare_parameter('baud_rate', 115200)
        self.declare_parameter('wheel_radius', 0.0325)      # in meters
        self.declare_parameter('wheel_separation', 0.16)   # track width in meters
        self.declare_parameter('ticks_per_rev', 1440)      # encoder ticks per wheel revolution
        
        self.declare_parameter('invert_left_motor', False)
        self.declare_parameter('invert_right_motor', False)
        self.declare_parameter('invert_left_encoder', False)
        self.declare_parameter('invert_right_encoder', False)
        
        # --- Retrieve Parameters ---
        self.serial_port = self.get_parameter('serial_port').value
        self.baud_rate = self.get_parameter('baud_rate').value
        self.wheel_radius = self.get_parameter('wheel_radius').value
        self.wheel_separation = self.get_parameter('wheel_separation').value
        self.ticks_per_rev = self.get_parameter('ticks_per_rev').value
        
        self.invert_left_motor = self.get_parameter('invert_left_motor').value
        self.invert_right_motor = self.get_parameter('invert_right_motor').value
        self.invert_left_encoder = self.get_parameter('invert_left_encoder').value
        self.invert_right_encoder = self.get_parameter('invert_right_encoder').value
        
        self.get_logger().info(
            f"Configured parameters: Port={self.serial_port}, Baud={self.baud_rate}, "
            f"Radius={self.wheel_radius}m, Separation={self.wheel_separation}m, Ticks/Rev={self.ticks_per_rev}"
        )
        
        # --- Odometry State ---
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self.prev_left_ticks = None
        self.prev_right_ticks = None
        self.last_odom_time = self.get_clock().now().nanoseconds / 1e9
        
        # --- Watchdog & Control State ---
        self.last_cmd_vel_time = time.time()
        self.motors_stopped = True
        
        # --- Publishers & Broadcasters ---
        self.odom_pub = self.create_publisher(Odometry, '/odom', 10)
        self.imu_pub = self.create_publisher(Imu, '/imu/data', 10)
        self.battery_pub = self.create_publisher(BatteryState, '/battery_state', 10)
        self.diag_pub = self.create_publisher(DiagnosticArray, '/diagnostics', 10)
        self.tf_broadcaster = TransformBroadcaster(self)
        
        # --- Subscribers ---
        self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_callback, 10)
        
        # --- Serial Connection & Threads ---
        self.serial_lock = threading.Lock()
        self.serial_conn = None
        self.running = True
        
        # Start connection manager/reader thread
        self.reader_thread = threading.Thread(target=self.serial_loop, daemon=True)
        self.reader_thread.start()
        
        # --- Watchdog Timer (10Hz) ---
        self.watchdog_timer = self.create_timer(0.1, self.watchdog_callback)
        
        self.get_logger().info("Waveshare Motor Driver initialized and running.")
        
    def connect_serial(self):
        """Attempts to open the serial port and initialize the chassis feedback."""
        with self.serial_lock:
            if self.serial_conn and self.serial_conn.is_open:
                try:
                    self.serial_conn.close()
                except Exception:
                    pass
                self.serial_conn = None
            
            try:
                self.serial_conn = serial.Serial(
                    port=self.serial_port,
                    baudrate=self.baud_rate,
                    timeout=0.5,
                    write_timeout=0.5
                )
                # Perform physical reset sequence to release ESP32
                self.get_logger().info("Performing ESP32 hardware reset sequence...")
                self.serial_conn.dtr = True
                self.serial_conn.rts = True
                time.sleep(0.1)
                self.serial_conn.dtr = False # Release EN (high)
                self.serial_conn.rts = False # Release IO0 (high)
                
                self.get_logger().info("Waiting for ESP32 boot (3s)...")
                time.sleep(3.0)
                
                self.serial_conn.reset_input_buffer()
                self.serial_conn.reset_output_buffer()
                
                # Handshake: send enable command and wait for JSON response
                telemetry_ok = False
                for attempt in range(5):
                    self.get_logger().info(f"Sending telemetry enable (attempt {attempt+1}/5)...")
                    try:
                        self.serial_conn.write(b'{"T":131,"cmd":1}\n')
                        self.serial_conn.write(b'{"T":1001}\n')
                    except Exception as we:
                        self.get_logger().warn(f"Failed to write handshake: {we}")
                        time.sleep(1.0)
                        continue
                        
                    start_time = time.time()
                    while time.time() - start_time < 2.0:
                        line = self.serial_conn.readline()
                        if line:
                            line_str = line.decode('utf-8', errors='ignore').strip()
                            self.get_logger().info(f"Handshake RX: {line_str}")
                            if line_str.startswith('{'):
                                try:
                                    data = json.loads(line_str)
                                    if 'T' in data:
                                        telemetry_ok = True
                                        break
                                except json.JSONDecodeError:
                                    pass
                        else:
                            time.sleep(0.05)
                    if telemetry_ok:
                        break
                
                if telemetry_ok:
                    self.get_logger().info(f"✅ Serial handshake successful on {self.serial_port}")
                    self.serial_conn.timeout = 0.1
                    return True
                else:
                    self.get_logger().error(f"❌ Failed to receive telemetry JSON from {self.serial_port}")
                    self.serial_conn.close()
                    self.serial_conn = None
                    return False
            except Exception as e:
                self.get_logger().error(f"❌ Failed to connect to serial port {self.serial_port}: {e}")
                self.serial_conn = None
                return False

    def cmd_vel_callback(self, msg):
        """Processes geometry_msgs/Twist, computes differential drive wheel velocities (m/s), and sends JSON."""
        v = msg.linear.x
        w = msg.angular.z
        
        # Differential kinematics
        v_L = v - (w * self.wheel_separation / 2.0)
        v_R = v + (w * self.wheel_separation / 2.0)
        
        # Invert direction if parameterized
        if self.invert_left_motor:
            v_L = -v_L
        if self.invert_right_motor:
            v_R = -v_R
            
        # Send velocity command
        self.send_speeds(v_L, v_R)
        
        # Feed watchdog
        self.last_cmd_vel_time = time.time()
        self.motors_stopped = False

    def send_speeds(self, left, right):
        """Formats speeds as JSON and writes to serial port."""
        cmd = {
            "T": 1,
            "L": round(left, 4),
            "R": round(right, 4)
        }
        cmd_str = json.dumps(cmd) + "\n"
        
        with self.serial_lock:
            if self.serial_conn and self.serial_conn.is_open:
                try:
                    self.serial_conn.write(cmd_str.encode('utf-8'))
                except Exception as e:
                    self.get_logger().error(f"Failed to write to serial port: {e}")
                    
    def watchdog_callback(self):
        """Stops the motors if no /cmd_vel messages have been received for more than 500ms."""
        if time.time() - self.last_cmd_vel_time > 0.5:
            if not self.motors_stopped:
                self.get_logger().warn("Watchdog timeout (500ms): stopping motors.")
                self.send_speeds(0.0, 0.0)
                self.motors_stopped = True

    def serial_loop(self):
        """Continuously manages the serial connection and reads incoming status lines."""
        while rclpy.ok() and self.running:
            # Check connection
            is_connected = False
            with self.serial_lock:
                if self.serial_conn and self.serial_conn.is_open:
                    is_connected = True
            
            if not is_connected:
                self.connect_serial()
                time.sleep(1.0)
                continue
                
            try:
                # Read line (timeout is set to 0.1s in Serial constructor)
                line = self.serial_conn.readline()
                if not line:
                    continue
                    
                # Decode line
                try:
                    line_str = line.decode('utf-8', errors='ignore').strip()
                    if line_str:
                        self.get_logger().info(f"🔌 Serial RX: {line_str}")
                except Exception as de:
                    self.get_logger().error(f"Decode error: {de}")
                    continue
                    
                if not line_str.startswith('{'):
                    continue
                
                # Parse JSON
                try:
                    data = json.loads(line_str)
                except json.JSONDecodeError:
                    continue
                
                # Look for chassis feedback
                if data.get('T') == 1001:
                    left_ticks = data.get('odl')
                    right_ticks = data.get('odr')
                    
                    if left_ticks is not None and right_ticks is not None:
                        self.process_encoder_feedback(left_ticks, right_ticks)
                    
                    # Parse and process battery voltage
                    voltage_raw = data.get('v')
                    if voltage_raw is not None:
                        self.process_battery_feedback(voltage_raw)
                        
                    # Parse and process IMU data (roll, pitch, yaw)
                    roll = data.get('roll') if data.get('roll') is not None else data.get('r')
                    pitch = data.get('pitch') if data.get('pitch') is not None else data.get('p')
                    yaw = data.get('yaw') if data.get('yaw') is not None else data.get('y')
                    
                    ax = data.get('ax')
                    ay = data.get('ay')
                    az = data.get('az')
                    gx = data.get('gx')
                    gy = data.get('gy')
                    gz = data.get('gz')
                    
                    if roll is not None and pitch is not None and yaw is not None:
                        self.process_imu_feedback(roll, pitch, yaw, ax, ay, az, gx, gy, gz)
                        
            except Exception as e:
                self.get_logger().error(f"Error in serial communication thread: {e}")
                # Reset connection on hard serial failure
                with self.serial_lock:
                    if self.serial_conn:
                        try:
                            self.serial_conn.close()
                        except Exception:
                            pass
                        self.serial_conn = None
                time.sleep(1.0)

    def process_encoder_feedback(self, left_ticks, right_ticks):
        """Calculates and publishes robot odometry and tf from encoder ticks."""
        current_time = self.get_clock().now().nanoseconds / 1e9
        dt = current_time - self.last_odom_time
        
        if self.prev_left_ticks is None or self.prev_right_ticks is None:
            self.prev_left_ticks = left_ticks
            self.prev_right_ticks = right_ticks
            self.last_odom_time = current_time
            return
            
        # Delta ticks
        delta_ticks_left = left_ticks - self.prev_left_ticks
        delta_ticks_right = right_ticks - self.prev_right_ticks
        
        # Gracefully handle wrap-around/reset: if single step change is absurdly large, ignore it.
        # Max reasonable ticks in 30Hz: ticks_per_rev * 10 max RPM / 60s * (1/30) = ticks_per_rev * 0.005.
        # If the delta is larger than 5 * ticks_per_rev, it's likely a wrap-around or a reset.
        if abs(delta_ticks_left) > self.ticks_per_rev * 5:
            delta_ticks_left = 0
        if abs(delta_ticks_right) > self.ticks_per_rev * 5:
            delta_ticks_right = 0
            
        # Invert readings if specified
        if self.invert_left_encoder:
            delta_ticks_left = -delta_ticks_left
        if self.invert_right_encoder:
            delta_ticks_right = -delta_ticks_right
            
        self.prev_left_ticks = left_ticks
        self.prev_right_ticks = right_ticks
        self.last_odom_time = current_time
        
        # Convert ticks to distance
        meters_per_tick = (2.0 * math.pi * self.wheel_radius) / self.ticks_per_rev
        delta_s_left = delta_ticks_left * meters_per_tick
        delta_s_right = delta_ticks_right * meters_per_tick
        
        delta_s = (delta_s_right + delta_s_left) / 2.0
        delta_theta = (delta_s_right - delta_s_left) / self.wheel_separation
        
        # Integrate pose
        self.x += delta_s * math.cos(self.theta + delta_theta / 2.0)
        self.y += delta_s * math.sin(self.theta + delta_theta / 2.0)
        self.theta += delta_theta
        
        # Calculate velocity
        v_robot = 0.0
        w_robot = 0.0
        if dt > 0.001:
            v_robot = delta_s / dt
            w_robot = delta_theta / dt
            
        # Publish Odometry
        odom = Odometry()
        odom.header.stamp = self.get_clock().now().to_msg()
        odom.header.frame_id = 'odom'
        odom.child_frame_id = 'base_link'
        
        odom.pose.pose.position = Point(x=self.x, y=self.y, z=0.0)
        q = self.quaternion_from_euler(0.0, 0.0, self.theta)
        odom.pose.pose.orientation = Quaternion(x=q[0], y=q[1], z=q[2], w=q[3])
        
        odom.twist.twist = Twist(
            linear=Vector3(x=v_robot, y=0.0, z=0.0),
            angular=Vector3(x=0.0, y=0.0, z=w_robot)
        )
        
        self.odom_pub.publish(odom)
        
        # Broadcast TF odom -> base_link
        t = TransformStamped()
        t.header.stamp = odom.header.stamp
        t.header.frame_id = 'odom'
        t.child_frame_id = 'base_link'
        
        t.transform.translation.x = self.x
        t.transform.translation.y = self.y
        t.transform.translation.z = 0.0
        t.transform.rotation = odom.pose.pose.orientation
        
        self.tf_broadcaster.sendTransform(t)

    def quaternion_from_euler(self, roll, pitch, yaw):
        """Converts euler angles (roll, pitch, yaw) to quaternion [x, y, z, w]."""
        cy = math.cos(yaw * 0.5)
        sy = math.sin(yaw * 0.5)
        cp = math.cos(pitch * 0.5)
        sp = math.sin(pitch * 0.5)
        cr = math.cos(roll * 0.5)
        sr = math.sin(roll * 0.5)

        q = [0.0] * 4
        q[0] = sr * cp * cy - cr * sp * sy
        q[1] = cr * sp * cy + sr * cp * sy
        q[2] = cr * cp * sy - sr * sp * cy
        q[3] = cr * cp * cy + sr * sp * sy
        return q

    def process_battery_feedback(self, voltage_raw):
        """Processes raw battery voltage and publishes BatteryState and Diagnostics."""
        try:
            v_val = float(voltage_raw)
        except ValueError:
            return
            
        # Normalize to Volts
        if v_val > 1000:
            voltage = v_val / 1000.0
        elif v_val > 100:
            voltage = v_val / 100.0
        else:
            voltage = v_val
            
        # Calculate battery percentage (for 3S LiPo: max 12.6V, min 9.9V)
        min_v = 9.9
        max_v = 12.6
        if voltage >= max_v:
            percentage = 100.0
        elif voltage <= min_v:
            percentage = 0.0
        else:
            percentage = ((voltage - min_v) / (max_v - min_v)) * 100.0
            
        # Publish BatteryState
        bat_msg = BatteryState()
        bat_msg.header.stamp = self.get_clock().now().to_msg()
        bat_msg.header.frame_id = 'base_link'
        bat_msg.voltage = voltage
        bat_msg.percentage = percentage / 100.0
        bat_msg.present = True
        bat_msg.power_supply_technology = BatteryState.POWER_SUPPLY_TECHNOLOGY_LIPO
        bat_msg.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_DISCHARGING
        self.battery_pub.publish(bat_msg)
        
        # Publish Diagnostics
        diag_msg = DiagnosticArray()
        diag_msg.header.stamp = bat_msg.header.stamp
        
        status = DiagnosticStatus()
        status.name = "battery"
        status.level = DiagnosticStatus.OK
        if voltage < 10.0:
            status.level = DiagnosticStatus.WARN
            self.get_logger().warn(f"🔋 Low Battery Warning: {voltage:.2f}V ({percentage:.1f}%)")
            
        status.message = f"{percentage:.1f}"
        status.hardware_id = "waveshare_esp32_driver"
        status.values = [
            KeyValue(key="voltage", value=f"{voltage:.2f}V"),
            KeyValue(key="percentage", value=f"{percentage:.1f}%")
        ]
        
        diag_msg.status.append(status)
        self.diag_pub.publish(diag_msg)

    def process_imu_feedback(self, roll, pitch, yaw, ax=None, ay=None, az=None, gx=None, gy=None, gz=None):
        """Processes IMU orientation (Euler angles in degrees) and publishes sensor_msgs/Imu."""
        r_rad = math.radians(roll)
        p_rad = math.radians(pitch)
        y_rad = math.radians(yaw)
        
        imu_msg = Imu()
        imu_msg.header.stamp = self.get_clock().now().to_msg()
        imu_msg.header.frame_id = 'imu_link'
        
        q = self.quaternion_from_euler(r_rad, p_rad, y_rad)
        imu_msg.orientation = Quaternion(x=q[0], y=q[1], z=q[2], w=q[3])
        
        if ax is not None and ay is not None and az is not None:
            # Check if accel is in g or m/s^2. ROS standard is m/s^2.
            # Usually, ESP32 raw is scaled to g. If mean gravity is ~1.0, convert to m/s^2.
            ax_f, ay_f, az_f = float(ax), float(ay), float(az)
            mag = math.sqrt(ax_f**2 + ay_f**2 + az_f**2)
            if 0.5 < mag < 2.0:
                ax_f *= 9.81
                ay_f *= 9.81
                az_f *= 9.81
            imu_msg.linear_acceleration.x = ax_f
            imu_msg.linear_acceleration.y = ay_f
            imu_msg.linear_acceleration.z = az_f
            
        if gx is not None and gy is not None and gz is not None:
            # Check if gyro is in deg/s or rad/s. ROS standard is rad/s.
            # ESP32 usually outputs deg/s.
            imu_msg.angular_velocity.x = math.radians(float(gx))
            imu_msg.angular_velocity.y = math.radians(float(gy))
            imu_msg.angular_velocity.z = math.radians(float(gz))
            
        # Add small default covariances to avoid warnings in EKF
        imu_msg.orientation_covariance = [0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01]
        imu_msg.angular_velocity_covariance = [0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01]
        imu_msg.linear_acceleration_covariance = [0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01]
        
        self.imu_pub.publish(imu_msg)

    def destroy_node(self):
        """Clean shutdown operations."""
        self.running = False
        self.get_logger().info("Stopping serial communication...")
        self.send_speeds(0.0, 0.0)
        with self.serial_lock:
            if self.serial_conn:
                try:
                    self.serial_conn.close()
                except Exception:
                    pass
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = WaveshareMotorDriver()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
