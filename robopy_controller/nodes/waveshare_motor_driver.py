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
        self.declare_parameter('wheel_radius', 0.0325)      # in meters (65mm diameter)
        self.declare_parameter('wheel_separation', 0.285)   # track width in meters (285mm)
        self.declare_parameter('rotational_wheel_separation', 0.285) # pure kinematic wheel separation (285mm)
        self.declare_parameter('ticks_per_rev', 280)        # exact calibrated ticks per wheel rev (280 CPR)
        
        self.declare_parameter('invert_left_motor', False)
        self.declare_parameter('invert_right_motor', False)
        self.declare_parameter('invert_left_encoder', True)
        self.declare_parameter('invert_right_encoder', False)
        self.declare_parameter('encoder_dead_zone', 0)        # ticks: ignore deltas <= this when both wheels below threshold (0 to disable tick dropping)
        self.declare_parameter('publish_tf', True)             # set False when another node (e.g. fast_flow_vo) owns odom->base_link TF
        
        # --- Retrieve Parameters ---
        self.serial_port = self.get_parameter('serial_port').value
        self.baud_rate = self.get_parameter('baud_rate').value
        self.wheel_radius = self.get_parameter('wheel_radius').value
        self.wheel_separation = self.get_parameter('wheel_separation').value
        self.rotational_wheel_separation = self.get_parameter('rotational_wheel_separation').value
        self.ticks_per_rev = self.get_parameter('ticks_per_rev').value
        
        self.invert_left_motor = self.get_parameter('invert_left_motor').value
        self.invert_right_motor = self.get_parameter('invert_right_motor').value
        self.invert_left_encoder = self.get_parameter('invert_left_encoder').value
        self.invert_right_encoder = self.get_parameter('invert_right_encoder').value
        self.encoder_dead_zone = self.get_parameter('encoder_dead_zone').value
        self.publish_tf = self.get_parameter('publish_tf').value
        
        # Register dynamic parameter callback
        self.add_on_set_parameters_callback(self.parameter_callback)
        
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
        self.is_commanded_stop = True
        self.cmd_linear_x = 0.0
        self.cmd_angular_z = 0.0
        self.v_robot = 0.0
        self.w_robot = 0.0
        self.vo_linear_speed = 0.0
        self.latest_voltage = 12.0
        self.idle_voltage = 12.0
        
        # Obstruction & Stall monitoring
        self.stall_start_time = None
        self.slipping_start_time = None
        self.voltage_drop_start_time = None
        self.is_stalled = False
        self.is_slipping = False
        self.is_voltage_overload = False
        
        # --- Publishers & Broadcasters ---
        self.odom_pub = self.create_publisher(Odometry, '/odom', 10)
        self.imu_pub = self.create_publisher(Imu, '/imu/esp32', 10)  # separate from madgwick /imu/data to avoid topic collision
        self.battery_pub = self.create_publisher(BatteryState, '/battery_state', 10)
        self.diag_pub = self.create_publisher(DiagnosticArray, '/diagnostics', 10)
        self.tf_broadcaster = TransformBroadcaster(self)
        
        # --- Subscribers ---
        self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_callback, 10)
        self.create_subscription(Odometry, '/vo/odom', self.vo_odom_callback, 10)
        
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
        """Processes geometry_msgs/Twist, computes differential drive differential kinematics, and sends JSON."""
        v = msg.linear.x
        w = msg.angular.z
        self.get_logger().info(f"📥 Received cmd_vel: v={v:.4f}, w={w:.4f}")
        
        self.cmd_linear_x = v
        self.cmd_angular_z = w
        
        # Direct differential kinematics (ROS standard)
        # v_left_target = v - (w * self.wheel_separation / 2.0)
        # v_right_target = v + (w * self.wheel_separation / 2.0)
        
        # Dai test fisici:
        # Per muovere la ruota DESTRA in AVANTI: linear=0.1, angular=0.69 (Twist)
        # -> v_left_target = 0.1 - 0.69 * 0.29 / 2.0 = 0.0
        # -> v_right_target = 0.1 + 0.69 * 0.29 / 2.0 = 0.2
        #
        # Per muovere la ruota SINISTRA in AVANTI: linear=-0.1, angular=0.69 (Twist)
        # -> v_left_target = -0.1 - 0.69 * 0.29 / 2.0 = -0.2
        # -> v_right_target = -0.1 + 0.69 * 0.29 / 2.0 = 0.0
        #
        # Questo implica che:
        # Per muovere la ruota DESTRA in avanti, serve un valore di comando POSITIVO.
        # Per muovere la ruota SINISTRA in avanti, serve un valore di comando NEGATIVO.
        #
        # Inoltre, il driver chiama: self.send_speeds(left, right)
        # Dove left va a "L" sulla seriale, e right va a "R" sulla seriale.
        #
        # Dalla nostra mappatura:
        # Quando angular > 0 (gira a SX):
        # - Solo DX in avanti (linear=0.1, angular=0.69) -> muove RUOTA DESTRA in avanti.
        #   Quindi per angular > 0, dobbiamo comandare la DESTRA in avanti.
        # - Solo SX in avanti (linear=-0.1, angular=0.69) -> muove RUOTA SINISTRA in avanti.
        #   Quindi per angular > 0, dobbiamo comandare la SINISTRA in avanti (con segno negativo).
        #
        # Standard ROS differential drive kinematics:
        # v_L = v - (w * B / 2.0)  -> Left wheel goes backward when w > 0 (Turn Left)
        # v_R = v + (w * B / 2.0)  -> Right wheel goes forward when w > 0 (Turn Left)
        v_L = v - (w * self.wheel_separation / 2.0)
        v_R = v + (w * self.wheel_separation / 2.0)

        self.send_speeds(v_L, v_R)
        
        # Feed watchdog
        self.last_cmd_vel_time = time.time()
        if abs(v) < 0.005 and abs(w) < 0.005:
            self.is_commanded_stop = True
            self.motors_stopped = True
        else:
            self.is_commanded_stop = False
            self.motors_stopped = False

    def send_speeds(self, left, right):
        """Formats speeds as JSON and writes to serial port.
        Note: Waveshare ESP32 board channel 'L' drives physical Right motor
        and channel 'R' drives physical Left motor. We swap left->R and right->L.
        """
        cmd = {
            "T": 1,
            "L": round(right, 4),
            "R": round(left, 4)
        }
        cmd_str = json.dumps(cmd, separators=(',', ':')) + "\n"
        
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
                self.cmd_linear_x = 0.0
                self.cmd_angular_z = 0.0
            else:
                # Resend stop command periodically every 1s to ensure ESP32 hardware safety
                if not hasattr(self, '_last_stop_resend') or (time.time() - self._last_stop_resend > 1.0):
                    self.send_speeds(0.0, 0.0)
                    self._last_stop_resend = time.time()
                
        # Rilevamento Stallo / Slittamento / Assorbimento Eccessivo
        cmd_active = (abs(self.cmd_linear_x) > 0.05 or abs(self.cmd_angular_z) > 0.1)
        
        # 1. Stall Meccanico: Comandiamo ma le ruote non girano
        if cmd_active and abs(self.v_robot) < 0.005 and abs(self.w_robot) < 0.02:
            if self.stall_start_time is None:
                self.stall_start_time = time.time()
            elif time.time() - self.stall_start_time > 1.0:
                self.is_stalled = True
        else:
            self.stall_start_time = None
            self.is_stalled = False
            
        # 2. Slittamento (Slipping): Le ruote girano ma il movimento visuale (VO) è zero
        if cmd_active and abs(self.v_robot) > 0.03 and abs(self.vo_linear_speed) < 0.008:
            if self.slipping_start_time is None:
                self.slipping_start_time = time.time()
            elif time.time() - self.slipping_start_time > 1.0:
                self.is_slipping = True
        else:
            self.slipping_start_time = None
            self.is_slipping = False
            
        # 3. Assorbimento Eccessivo: Caduta di tensione della batteria
        v_drop = self.idle_voltage - self.latest_voltage
        if cmd_active and v_drop > 2.0:
            if self.voltage_drop_start_time is None:
                self.voltage_drop_start_time = time.time()
            elif time.time() - self.voltage_drop_start_time > 1.0:
                self.is_voltage_overload = True
        else:
            self.voltage_drop_start_time = None
            self.is_voltage_overload = False

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
                
                t_val = data.get('T')
                if t_val in [1001, 1002, 1003, 1004] or 'ax' in data or 'r' in data or 'roll' in data or 'gx' in data:
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
                    
                    if roll is not None or ax is not None or gx is not None:
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
        """Calculates and publishes robot odometry and tf from encoder ticks.
        Note: On the Waveshare ESP32 board, channel 'L' (odl) drives the physical Right motor
        and channel 'R' (odr) drives the physical Left motor. We swap them.
        """
        current_time = self.get_clock().now().nanoseconds / 1e9
        dt = current_time - self.last_odom_time
        
        if self.prev_left_ticks is None or self.prev_right_ticks is None:
            self.prev_left_ticks = left_ticks
            self.prev_right_ticks = right_ticks
            self.last_odom_time = current_time
            return
            
        # Delta ticks (Note: channel L is physical Right wheel, channel R is physical Left wheel)
        delta_ticks_right = left_ticks - self.prev_left_ticks
        delta_ticks_left = right_ticks - self.prev_right_ticks
        
        if abs(delta_ticks_right) > 0 or abs(delta_ticks_left) > 0:
            self.get_logger().info(f"[ENCODER_RAW] d_right={delta_ticks_right}, d_left={delta_ticks_left}", throttle_duration_sec=0.2)
        
        # Gracefully handle wrap-around/reset: if single step change is absurdly large, ignore it.
        # Max reasonable ticks in 30Hz: ticks_per_rev * 10 max RPM / 60s * (1/30) = ticks_per_rev * 0.005.
        # If the delta is larger than 5 * ticks_per_rev, it's likely a wrap-around or a reset.
        if abs(delta_ticks_left) > self.ticks_per_rev * 5:
            delta_ticks_left = 0
        if abs(delta_ticks_right) > self.ticks_per_rev * 5:
            delta_ticks_right = 0
        
        # --- ZERO-VELOCITY LOCK ---
        # When motors are stopped (no cmd_vel or v=0, w=0 commanded), force delta to zero.
        # This is the primary defense against encoder noise drift at standstill.
        if self.motors_stopped or self.is_commanded_stop:
            delta_ticks_left = 0
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
        
        # Standard ROS 2 Right-Hand Coordinate System (+Z = CCW / Antiorario):
        # Right wheel moves forward (+), Left wheel moves backward (-) during CCW turn -> delta_s_right - delta_s_left > 0
        delta_s = (delta_s_right + delta_s_left) / 2.0
        delta_theta = (delta_s_left - delta_s_right) / self.rotational_wheel_separation
        
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
        self.v_robot = v_robot
        self.w_robot = w_robot
        
        # --- DYNAMIC COVARIANCES ---
        # Low covariance when moving (trustworthy), high when stopped (uncertain).
        if self.motors_stopped:
            pose_cov = 1e-3
            twist_cov = 1e-3
        else:
            pose_cov = 1e-5
            twist_cov = 1e-4
            
        # Publish Odometry
        odom = Odometry()
        odom.header.stamp = self.get_clock().now().to_msg()
        odom.header.frame_id = 'odom'
        odom.child_frame_id = 'base_link'
        
        odom.pose.pose.position = Point(x=self.x, y=self.y, z=0.0)
        q = self.quaternion_from_euler(0.0, 0.0, self.theta)
        odom.pose.pose.orientation = Quaternion(x=q[0], y=q[1], z=q[2], w=q[3])
        
        # Diagonal covariance matrices (6x6 flattened to 36)
        odom.pose.covariance = [0.0] * 36
        odom.pose.covariance[0] = pose_cov    # x
        odom.pose.covariance[7] = pose_cov    # y
        odom.pose.covariance[35] = pose_cov   # yaw
        
        odom.twist.twist = Twist(
            linear=Vector3(x=v_robot, y=0.0, z=0.0),
            angular=Vector3(x=0.0, y=0.0, z=w_robot)
        )
        
        odom.twist.covariance = [0.0] * 36
        odom.twist.covariance[0] = twist_cov   # vx
        odom.twist.covariance[35] = twist_cov  # wz
        
        self.odom_pub.publish(odom)
        
        # Broadcast TF odom -> base_link (only if this node owns TF authority)
        if self.publish_tf:
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
            
        self.latest_voltage = voltage
        if self.motors_stopped:
            self.idle_voltage = 0.95 * self.idle_voltage + 0.05 * voltage
            
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
        
        # Publish Diagnostics Stall / Slip / Overload
        stall_status = DiagnosticStatus()
        stall_status.name = "motor_stall"
        stall_status.hardware_id = "waveshare_esp32_driver"
        v_drop = self.idle_voltage - self.latest_voltage
        
        if self.is_stalled or self.is_slipping or self.is_voltage_overload:
            stall_status.level = DiagnosticStatus.ERROR
            reasons = []
            if self.is_stalled: reasons.append("Stallo meccanico (ruote ferme)")
            if self.is_slipping: reasons.append("Slittamento (ostacolo rilevato da VO)")
            if self.is_voltage_overload: reasons.append("Sovraccarico elettrico (assorbimento elevato)")
            stall_status.message = " | ".join(reasons)
        else:
            stall_status.level = DiagnosticStatus.OK
            stall_status.message = "Motori OK"
            
        stall_status.values = [
            KeyValue(key="stalled", value=str(self.is_stalled)),
            KeyValue(key="slipping", value=str(self.is_slipping)),
            KeyValue(key="voltage_overload", value=str(self.is_voltage_overload)),
            KeyValue(key="voltage_drop", value=f"{v_drop:.2f}V")
        ]
        diag_msg.status.append(stall_status)
        self.diag_pub.publish(diag_msg)

    def process_imu_feedback(self, roll, pitch, yaw, ax=None, ay=None, az=None, gx=None, gy=None, gz=None):
        """Processes IMU orientation (Euler angles in degrees) and publishes sensor_msgs/Imu."""
        r_rad = math.radians(float(roll)) if roll is not None else 0.0
        p_rad = math.radians(float(pitch)) if pitch is not None else 0.0
        y_rad = math.radians(float(yaw)) if yaw is not None else 0.0
        
        imu_msg = Imu()
        imu_msg.header.stamp = self.get_clock().now().to_msg()
        imu_msg.header.frame_id = 'imu_link'
        
        q = self.quaternion_from_euler(r_rad, p_rad, y_rad)
        imu_msg.orientation = Quaternion(x=q[0], y=q[1], z=q[2], w=q[3])
        
        if ax is not None and ay is not None and az is not None:
            # Check if accel is in g or m/s^2. ROS standard is m/s^2.
            ax_f, ay_f, az_f = float(ax), float(ay), float(az)
            mag = math.sqrt(ax_f**2 + ay_f**2 + az_f**2)
            if 0.5 < mag < 2.0:
                ax_f *= 9.81
                ay_f *= 9.81
                az_f *= 9.81
            # Remap axes for 90-degree chassis IMU rotation (REP-103):
            # Forward (+X ROS) -> +az
            # Left (+Y ROS)    -> +ax
            # Up (+Z ROS)      -> +ay (Gravity ~ 9.81 m/s^2)
            imu_msg.linear_acceleration.x = az_f
            imu_msg.linear_acceleration.y = ax_f
            imu_msg.linear_acceleration.z = ay_f
            
        if gx is not None and gy is not None and gz is not None:
            # Remap gyro axes for 90-degree chassis IMU rotation (REP-103):
            # Yaw (+Z ROS)     -> +gy
            # Pitch (+Y ROS)   -> +gz
            # Roll (+X ROS)    -> +gx
            imu_msg.angular_velocity.x = math.radians(float(gx))
            imu_msg.angular_velocity.y = math.radians(float(gz))
            imu_msg.angular_velocity.z = math.radians(float(gy))
            
        # Add small default covariances to avoid warnings in EKF
        imu_msg.orientation_covariance = [0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01]
        imu_msg.angular_velocity_covariance = [0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01]
        imu_msg.linear_acceleration_covariance = [0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01]
        
        self.imu_pub.publish(imu_msg)

    def vo_odom_callback(self, msg):
        self.vo_linear_speed = msg.twist.twist.linear.x

    def parameter_callback(self, params):
        from rcl_interfaces.msg import SetParametersResult
        for param in params:
            if param.name == 'wheel_radius':
                self.wheel_radius = float(param.value)
                self.get_logger().info(f"Dynamic Parameter Updated: wheel_radius = {self.wheel_radius:.5f}m")
            elif param.name == 'wheel_separation':
                self.wheel_separation = float(param.value)
                self.get_logger().info(f"Dynamic Parameter Updated: wheel_separation = {self.wheel_separation:.5f}m")
        return SetParametersResult(successful=True)

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
