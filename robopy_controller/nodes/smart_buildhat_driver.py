#!/usr/bin/env python3
"""
SmartBuildHatDriver — Open-Loop Bang-Bang Controller

ECO00023: Rimosso PID (feedback da /odom_filtered era rotto da ECO00020).
I motori PassiveMotor non hanno encoder e richiedono ~100% PWM.
Strategia: se Nav2/teleop chiede di muoversi → PWM 100% nella direzione giusta.

Version: 01.00.00 (ECO00003)
"""

__version__ = "01.00.00"
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from std_msgs.msg import Float64MultiArray
from sensor_msgs.msg import Imu, Range
import time
import math
from buildhat import PassiveMotor


class SmartBuildHatDriver(Node):
    def __init__(self):
        super().__init__('smart_buildhat_driver')
        
        # --- Parametri Hardware ---
        self.declare_parameter('motor_left_port', 'B')
        self.declare_parameter('motor_right_port', 'A')
        self.declare_parameter('invert_right_motor', False)
        
        # --- Parametri di Controllo ---
        # PWM base per vincere l'attrito statico (minimo per far girare i motori)
        self.declare_parameter('min_pwm', 70.0)
        # PWM massimo (100 = tutto fuori)
        self.declare_parameter('max_pwm', 100.0)
        # Soglie minime per considerare un comando "valido" (anti-rumore)
        self.declare_parameter('linear_threshold', 0.01)   # m/s
        self.declare_parameter('angular_threshold', 0.05)   # rad/s
        # Fattore di mix angolare (quanto peso dare alla rotazione rispetto al lineare)
        self.declare_parameter('angular_mix_factor', 0.5)
        # --- PID Parameters ---
        self.declare_parameter('kp_linear', 50.0)
        self.declare_parameter('ki_linear', 10.0)
        self.declare_parameter('kd_linear', 5.0)
        
        self.declare_parameter('kp_angular', 150.0)
        self.declare_parameter('ki_angular', 5.0)
        self.declare_parameter('kd_angular', 2.0)
        
        self.kp_v = self.get_parameter('kp_linear').value
        self.ki_v = self.get_parameter('ki_linear').value
        self.kd_v = self.get_parameter('kd_linear').value
        
        self.kp_w = self.get_parameter('kp_angular').value
        self.ki_w = self.get_parameter('ki_angular').value
        self.kd_w = self.get_parameter('kd_angular').value
        
        # PID State
        self.err_sum_v = 0.0
        self.err_last_v = 0.0
        self.err_sum_w = 0.0
        self.err_last_w = 0.0
        self.max_integral = 50.0 # Anti-windup
        
        # Feedback Odom
        self.current_v = 0.0
        self.current_w = 0.0
        self.last_odom_time = time.time()
        
        # Cache parametri
        self.inv_right = self.get_parameter('invert_right_motor').value
        self.min_pwm = self.get_parameter('min_pwm').value  # Setup to 75.0 Deadzone in launch
        self.max_pwm = self.get_parameter('max_pwm').value
        self.lin_thresh = self.get_parameter('linear_threshold').value
        self.ang_thresh = self.get_parameter('angular_threshold').value
        
        # Stato
        self.target_v = 0.0
        self.target_w = 0.0
        self.last_cmd_time = time.time()
        self.last_teleop_time = 0.0
        
        # Caching HW applicato
        self.last_applied_L = 0
        self.last_applied_R = 0
        
        self.accel_x = 0.0
        
        # Stato Sensore Ostacoli
        self.ultrasonic_dist = 2.0
        
        # Hardware
        left_port = self.get_parameter('motor_left_port').value
        right_port = self.get_parameter('motor_right_port').value
        try:
            self.motor_left = PassiveMotor(left_port)
            self.motor_right = PassiveMotor(right_port)
            self.get_logger().info(
                f"BuildHAT PassiveMotors initialized: L={left_port}, R={right_port}, "
                f"min_pwm={self.min_pwm}, max_pwm={self.max_pwm}"
            )
        except Exception as e:
            self.get_logger().error(f"Failed to initialize BuildHAT: {e}")
            raise
        
        # Subscribers
        from nav_msgs.msg import Odometry
        self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_callback, 10)
        self.create_subscription(Twist, '/cmd_vel_nav', self.cmd_vel_callback, 10)
        self.create_subscription(Twist, '/teleop/cmd_vel', self.teleop_callback, 10)
        self.create_subscription(Imu, '/imu/data', self.imu_callback, 10)
        self.create_subscription(Odometry, '/vo/odom', self.odom_callback, 10)
        self.create_subscription(Range, '/ultrasonic_range', self.ultrasonic_callback, 10)
        
        # Control loop at 10Hz
        self.dt = 0.1
        self.create_timer(self.dt, self.control_loop)
        
        self.get_logger().info("Smart BuildHAT Driver with VO-PID ready.")

    def odom_callback(self, msg):
        self.current_v = msg.twist.twist.linear.x
        self.current_w = msg.twist.twist.angular.z
        self.last_odom_time = time.time()

    def teleop_callback(self, msg):
        self.target_v = msg.linear.x
        self.target_w = msg.angular.z
        self.last_teleop_time = time.time()
        self.last_cmd_time = time.time()

    def cmd_vel_callback(self, msg):
        # Override: ignora nav2/AI se teleop è in uso
        if time.time() - self.last_teleop_time < 0.5:
            return
        self.target_v = msg.linear.x
        self.target_w = msg.angular.z
        self.last_cmd_time = time.time()
        
    def imu_callback(self, msg):
        self.accel_x = msg.linear_acceleration.x

    def ultrasonic_callback(self, msg):
        self.ultrasonic_dist = msg.range

    def _reset_pid(self):
        self.err_sum_v = 0.0
        self.err_last_v = 0.0
        self.err_sum_w = 0.0
        self.err_last_w = 0.0

    def compute_pid(self, target, current, kp, ki, kd, err_sum, err_last):
        error = target - current
        err_sum += error * self.dt
        
        # Anti-windup
        err_sum = max(-self.max_integral, min(self.max_integral, err_sum))
        
        d_err = (error - err_last) / self.dt
        err_last = error
        
        out = (kp * error) + (ki * err_sum) + (kd * d_err)
        return out, err_sum, err_last

    def control_loop(self):
        # Watchdog: se nessun comando da 1s o odometria vecchia, ferma tutto
        if time.time() - self.last_cmd_time > 1.0 or time.time() - self.last_odom_time > 2.0:
            self.target_v = 0.0
            self.target_w = 0.0
            self._reset_pid()
        
        v = self.target_v
        w = self.target_w
        
        # --- ULTRASONIC COLLISION AVOIDANCE ---
        # Se c'è un ostacolo a <= 10cm, e stiamo provando ad andare dritti, neghiamo la traslazione
        if self.ultrasonic_dist <= 0.10 and v > 0.0:
            v = 0.0
        
        # Stop pulito
        if abs(v) < self.lin_thresh and abs(w) < self.ang_thresh:
            self._reset_pid()
            if self.last_applied_L != 0 or self.last_applied_R != 0:
                self.motor_left.stop()
                self.motor_right.stop()
                self.last_applied_L = 0
                self.last_applied_R = 0
            return
            
        # Calcolo PID (Delta PWM da aggiungere alla base)
        pid_v, self.err_sum_v, self.err_last_v = self.compute_pid(
            v, self.current_v, self.kp_v, self.ki_v, self.kd_v, self.err_sum_v, self.err_last_v
        )
        pid_w, self.err_sum_w, self.err_last_w = self.compute_pid(
            w, self.current_w, self.kp_w, self.ki_w, self.kd_w, self.err_sum_w, self.err_last_w
        )
        
        out_L = 0
        out_R = 0
        
        # Mixer Differential Drive con Deadzone
        # La ruota sinistra e destra ottengono una spinta base (v) +/- una spinta differenziale (w)
        # Il PWM base (min_pwm) viene erogato per vincere l'attrito solo se ci si vuole muovere.
        
        if abs(v) >= self.lin_thresh or abs(w) >= self.ang_thresh:
            
            # 1. Spinta traslazionale base (v)
            if abs(v) >= self.lin_thresh:
                sign_v = math.copysign(1.0, v)
                pwm_lin = self.min_pwm + abs(pid_v)
                pwm_lin *= sign_v
            else:
                pwm_lin = 0.0
                
            # 2. Spinta rotazionale differenziale (w)
            if abs(w) >= self.ang_thresh:
                sign_w = math.copysign(1.0, w)
                
                # Se è in marcia, min_pwm è già vinto dal pwm_lin, quindi w è puro steering aggiuntivo.
                # Se NON è in marcia, la sola rotazione deve vincere da zero la min_pwm.
                if abs(v) < self.lin_thresh:
                    pwm_ang = (self.min_pwm + abs(pid_w)) * sign_w
                else:
                    # In marcia, applica lo steering modulato dal fattore di mix
                    pwm_ang = abs(pid_w) * sign_w * self.get_parameter('angular_mix_factor').value
            else:
                pwm_ang = 0.0
                
            # 3. Miscelazione Diff-Drive
            # Per girare a sinistra (w positivo), ruota SX rallenta, ruota DX accelera
            out_L_raw_float = pwm_lin - pwm_ang
            out_R_raw_float = pwm_lin + pwm_ang
            
            # Applicazione del cap +/- max_pwm, preservando i segni originali
            out_L = int(max(-self.max_pwm, min(self.max_pwm, out_L_raw_float)))
            out_R = int(max(-self.max_pwm, min(self.max_pwm, out_R_raw_float)))
            
        else:
            out_L, out_R = 0, 0
            
        # Inversione HW necessaria per lo skid-steer
        if self.inv_right:
            out_R = -out_R
        
        # IMPORTANTE: Chiamare start() solo se il valore cambia
        if self.last_applied_L != out_L:
            self.motor_left.start(-out_L)
            self.last_applied_L = out_L
            
        if self.last_applied_R != out_R:
            self.motor_right.start(-out_R) # Libreria buildhat
            self.last_applied_R = out_R



def main(args=None):
    rclpy.init(args=args)
    node = SmartBuildHatDriver()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.motor_left.stop()
        node.motor_right.stop()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
