#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, TwistStamped
from nav_msgs.msg import Odometry
from std_msgs.msg import Float64MultiArray
import time
import math
from buildhat import PassiveMotor

class SmartBuildHatDriver(Node):
    def __init__(self):
        super().__init__('smart_buildhat_driver')
        
        # --- Parametri Cinematici e Hardware ---
        self.declare_parameter('motor_left_port', 'B') # Il tuo vecchio nodo mappava L su B
        self.declare_parameter('motor_right_port', 'A') # D(R) su A
        self.declare_parameter('wheel_radius', 0.033) # Distanza in metri
        self.declare_parameter('wheel_separation', 0.16) # Distanza tra le ruote
        self.declare_parameter('invert_right_motor', False) # Erano entrambi negativi, nessuna inversione relativa necessaria
        
        # --- Parametri Regolatore PID (Velocità Lineare) ---
        self.declare_parameter('kp_linear', 100.0)
        self.declare_parameter('ki_linear', 20.0)
        self.declare_parameter('kd_linear', 2.0)
        
        # --- Parametri Regolatore PID (Velocità Angolare) ---
        self.declare_parameter('kp_angular', 60.0)
        self.declare_parameter('ki_angular', 10.0)
        self.declare_parameter('kd_angular', 1.0)
        
        # --- Deadband Compensation e Limiti ---
        # Il PWM base necessario per far muovere il motore e vincere l'attrito statico del tappeto.
        self.declare_parameter('deadband_pwm', 75.0) 
        self.declare_parameter('max_integral', 100.0) # Termine integratore max (Anti-Windup)
        self.declare_parameter('max_pwm', 100.0)
        
        # --- Stall Detection ---
        self.declare_parameter('stall_timeout', 3.0) # Secondi per cui dichiariamo stallo
        self.declare_parameter('stall_velocity_threshold', 0.02) # m/s o rad/s minimi
        
        # Cache dei parametri
        self.w_r = self.get_parameter('wheel_radius').value
        self.w_b = self.get_parameter('wheel_separation').value
        self.inv_right = self.get_parameter('invert_right_motor').value
        
        self.kp_v = self.get_parameter('kp_linear').value
        self.ki_v = self.get_parameter('ki_linear').value
        self.kd_v = self.get_parameter('kd_linear').value
        
        self.kp_w = self.get_parameter('kp_angular').value
        self.ki_w = self.get_parameter('ki_angular').value
        self.kd_w = self.get_parameter('kd_angular').value
        
        self.deadband = self.get_parameter('deadband_pwm').value
        self.max_i = self.get_parameter('max_integral').value
        self.max_pwm = self.get_parameter('max_pwm').value
        
        self.stall_timeout = self.get_parameter('stall_timeout').value
        self.stall_v_thresh = self.get_parameter('stall_velocity_threshold').value
        
        # Stato del Robot
        self.target_v = 0.0
        self.target_w = 0.0
        self.actual_v = 0.0
        self.actual_w = 0.0
        
        # Memoria del PID
        self.err_v_int = 0.0
        self.err_w_int = 0.0
        self.err_v_prev = 0.0
        self.err_w_prev = 0.0
        
        self.last_time = self.get_clock().now()
        self.stall_start_time = None
        
        self.last_cmd_time = time.time()
        self.last_bluedot_time = 0.0
        self.bluedot_max_v = 0.5 # Max m/s per joystick
        self.bluedot_max_w = 2.0 # Max rad/s per joystick
        
        # Inizializzazione Hardware
        left_port = self.get_parameter('motor_left_port').value
        right_port = self.get_parameter('motor_right_port').value
        try:
            self.motor_left = PassiveMotor(left_port)
            self.motor_right = PassiveMotor(right_port)
            self.get_logger().info(f"Initialized BuildHAT PassiveMotors on ports L:{left_port} and R:{right_port}")
        except Exception as e:
            self.get_logger().error(f"Failed to initialize BuildHAT: {e}")
            raise
        
        # ROS 2 Subscribers
        self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_callback, 10) # Fallback / Teleop
        self.create_subscription(Twist, '/cmd_vel_nav', self.cmd_vel_callback, 10) # Nav2 Jazzy default (unstamped in this config)
        self.create_subscription(Float64MultiArray, '/bluedot_input', self.bluedot_callback, 10)
        # Riceviamo feedback reale tramite Odometria Visiva (high Hz)
        self.create_subscription(Odometry, '/odom', self.odom_callback, 10)
        
        # Timer Control Loop Chiuso (20Hz)
        self.control_rate = 20.0 
        self.create_timer(1.0 / self.control_rate, self.control_loop)
        
        self.get_logger().info("Smart BuildHAT Driver initialized and running.")

    def bluedot_callback(self, msg):
        if len(msg.data) != 2:
            return
        x, y = msg.data
        
        if abs(x) < 0.1 and abs(y) < 0.1:
            # We ONLY stop the robot manually if the user was just using it.
            # Otherwise, continuous idle Bluedot inputs would lock out Nav2 forever.
            if time.time() - self.last_bluedot_time < 0.2:
                self.target_v = 0.0
                self.target_w = 0.0
                self.last_cmd_time = time.time()
        else:
            self.target_v = y * self.bluedot_max_v
            # x positivo su Bluedot gira a destra. In ROS giro a destra è -Z.
            self.target_w = -x * self.bluedot_max_w
        
            self.last_bluedot_time = time.time()
            self.last_cmd_time = time.time()

    def cmd_vel_stamped_callback(self, msg):
        # Unwrap the TwistStamped and pass into the main logic
        self.cmd_vel_callback(msg.twist)

    def cmd_vel_callback(self, msg):
        # Override teleop: ignora nav2/AI se bluedot è palesemente in uso (ultimo sec)
        if time.time() - self.last_bluedot_time < 0.5:
            return
            
        self.target_v = msg.linear.x
        self.target_w = msg.angular.z
        self.last_cmd_time = time.time()
        self.get_logger().debug(f"Received velocity: v={self.target_v:.2f}, w={self.target_w:.2f}")

    def odom_callback(self, msg):
        # Prendiamo la velocità attuale stimata dall'odometria
        self.actual_v = msg.twist.twist.linear.x
        self.actual_w = msg.twist.twist.angular.z

    def apply_passive_pwm(self, velocity_demand):
        """
        Converts the PID velocity demand into a proportional PWM (-100 to 100)
        applying a minimum deadband to overcome physical friction.
        """
        if abs(velocity_demand) < 0.01:
            return 0.0
        
        sign = math.copysign(1.0, velocity_demand)
        # Apply deadband: output starts from deadband up to 100
        pwm_out = self.deadband + abs(velocity_demand)
        return sign * min(100.0, pwm_out)

    def control_loop(self):
        current_time = self.get_clock().now()
        dt = (current_time - self.last_time).nanoseconds / 1e9
        if dt <= 0:
            return
        self.last_time = current_time
        
        # Watchdog motori (se non riceviamo nulla da 1 secondo fermiamo tutto per sicurezza)
        if time.time() - self.last_cmd_time > 1.0:
            self.target_v = 0.0
            self.target_w = 0.0
        
        # --- 1. Calcolo Errore Feedback ---
        err_v = self.target_v - self.actual_v
        err_w = self.target_w - self.actual_w
        
        # Ferma in sicurezza se target è nullo 
        if abs(self.target_v) < 1e-3 and abs(self.target_w) < 1e-3:
            # Pulisco gli integratori per non far accumulare wind-up da fermo
            self.err_v_int = 0.0
            self.err_w_int = 0.0
            self.err_v_prev = 0.0
            self.err_w_prev = 0.0
            self.motor_left.stop()
            self.motor_right.stop()
            self.stall_start_time = None
            return

        # --- 2. Integrale & Anti-Windup ---
        self.err_v_int += err_v * dt
        self.err_w_int += err_w * dt
        
        # Clamping dell'integrale 
        self.err_v_int = max(min(self.err_v_int, self.max_i), -self.max_i)
        self.err_w_int = max(min(self.err_w_int, self.max_i), -self.max_i)
        
        # --- 3. Derivata ---
        err_v_deriv = (err_v - self.err_v_prev) / dt
        err_w_deriv = (err_w - self.err_w_prev) / dt
        
        self.err_v_prev = err_v
        self.err_w_prev = err_w
        
        # --- 4. Equazione PID ---
        pid_v_out = (self.kp_v * err_v) + (self.ki_v * self.err_v_int) + (self.kd_v * err_v_deriv)
        pid_w_out = (self.kp_w * err_w) + (self.ki_w * self.err_w_int) + (self.kd_w * err_w_deriv)
        
        # --- 5. Miscelazione PID su base differenziale ---
        # i PID output sono già pseudo-Segnali PWM
        cmd_v_L = pid_v_out - pid_w_out
        cmd_v_R = pid_v_out + pid_w_out
        
        # Moltiplichiamolo per convertire (approx) la richiesta di ms in un segnale pwm 0-100.
        # N.B. I Kp/Ki si occuperanno di assestare questa grandezza dimensionale tramite feedback.
        pwm_L = max(min(cmd_v_L, self.max_pwm), -self.max_pwm)
        pwm_R = max(min(cmd_v_R, self.max_pwm), -self.max_pwm)
        
        # --- 6. Deadband/Passive Compulsory 100% ---
        # I motori passivi supportano il PWM con start(), ma hanno bisogno di 100 per muoversi stabilmente (BangBang).
        pwm_L_comp = self.apply_passive_pwm(pwm_L)
        pwm_R_comp = self.apply_passive_pwm(pwm_R)
        
        # --- 7. Stall Detection Guardiano ---
        # L'integrale aumenta se la velocità misurata non combacia col target.
        is_saturated = abs(self.err_v_int) >= self.max_i * 0.9 or abs(self.err_w_int) >= self.max_i * 0.9
        is_stalled = abs(self.actual_v) < self.stall_v_thresh and abs(self.actual_w) < (self.stall_v_thresh * 5.0) 
        
        if is_saturated and is_stalled and (abs(self.target_v) > 0.05 or abs(self.target_w) > 0.1):
            if self.stall_start_time is None:
                self.stall_start_time = current_time
            elif (current_time - self.stall_start_time).nanoseconds / 1e9 > self.stall_timeout:
                self.get_logger().warn(
                    f"STALLO RILEVATO: I motori stanno ricevendo massima forza ma "
                    f"l'odometria indica v={self.actual_v:.3f} m/s (atteso v={self.target_v:.3f}). \n"
                    f"Rilevata barriera nascosta ad alto attrito!"
                )
        else:
            self.stall_start_time = None
            
        # --- 8. Invia ai motori ---
        # BuildHAT PassiveMotor attende valori interi (es. -100, 100)
        out_L = int(pwm_L_comp)
        out_R = int(pwm_R_comp)
        
        if self.inv_right:
            out_R = -out_R
            
        # Inversione HW necessaria per lo skid-steer (come nel tuo vecchio nodo)
        self.motor_left.start(-out_L)
        self.motor_right.start(-out_R)

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
