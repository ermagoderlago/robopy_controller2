#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray
from geometry_msgs.msg import Twist
from buildhat import PassiveMotor, Hat
import math
import time

# =========================
# PARAMETRI DI TUNING
# =========================

MAX_PWM = 100.0 # Valore massimo per BuildHAT (0-100)
MIN_MOVING_PWM = 20.0 # Deadzone motori

# MAPPING FISICO (STIMATO)
MAX_MPS = 0.5  # Velocità massima in m/s (a PWM 100)
BASE_WIDTH = 0.15 # Distanza tra le ruote (metri)

MAX_ACCEL = 300.0        # % / secondo (PWM change rate)
CONTROL_RATE = 0.02      # 50 Hz
CMD_TIMEOUT = 0.5        # Stop se non ricevo comandi per x sec
INPUT_DEADZONE = 0.05


class MotorControlNode(Node):
    def __init__(self):
        super().__init__('motor_control_node')

        # --- ROS SUBSCRIPTIONS ---
        # 1. BlueDot (Joystick)
        self.create_subscription(
            Float64MultiArray,
            'bluedot_input',
            self.bluedot_callback,
            10
        )
        
        # 2. Nav2 / Cmd Vel (Autonomous)
        self.create_subscription(
            Twist,
            'cmd_vel',
            self.cmd_vel_callback,
            10
        )

        self.timer = self.create_timer(CONTROL_RATE, self.control_loop)

        # --- HAT & MOTORI ---
        try:
            self.hat = Hat()
            self.motoreL = PassiveMotor('B')
            self.motoreD = PassiveMotor('A')
        except Exception as e:
            self.get_logger().error(f"Errore inizializzazione motori: {e}")
            self.motoreL = None
            self.motoreD = None

        # --- STATO ---
        self.target_left = 0.0
        self.target_right = 0.0
        self.current_left = 0.0
        self.current_right = 0.0
        self.last_cmd_time = time.time()

        self.get_logger().info("✅ Motor control node avviato (Dual Input: BlueDot + CmdVel)")

    # =============================
    # INPUT 1: BLUEDOT (Joystick)
    # =============================
    def bluedot_callback(self, msg):
        if len(msg.data) != 2:
            return

        x, y = msg.data # -1.0 a 1.0
        self.last_cmd_time = time.time()

        if abs(x) < INPUT_DEADZONE and abs(y) < INPUT_DEADZONE:
            self.target_left = 0.0
            self.target_right = 0.0
            return

        # Mixing Arcade Drive per PWM diretto
        linear = y * MAX_PWM
        angular = x * MAX_PWM

        self.target_left = self._clamp(linear + angular)
        self.target_right = self._clamp(linear - angular)

    # =============================
    # INPUT 2: CMD_VEL (Nav2)
    # =============================
    def cmd_vel_callback(self, msg):
        self.last_cmd_time = time.time()
        
        # Conversione Twist m/s -> PWM
        linear = msg.linear.x
        angular = msg.angular.z
        
        # Differential Drive Kinematics to Wheel Velocity (m/s)
        v_left_mps = linear - (angular * BASE_WIDTH / 2.0)
        v_right_mps = linear + (angular * BASE_WIDTH / 2.0)
        
        # Map m/s to PWM %
        # PWM = (v_mps / MAX_MPS) * 100
        target_l = (v_left_mps / MAX_MPS) * 100.0
        target_r = (v_right_mps / MAX_MPS) * 100.0
        
        self.target_left = self._clamp(target_l)
        self.target_right = self._clamp(target_r)
        
        # Debug temporaneo per verificare ricezione
        # self.get_logger().info(f"CmdVel: Lin={linear:.2f} Ang={angular:.2f} -> L={target_l:.1f} R={target_r:.1f}")

    # =============================
    # CONTROL LOOP (50 Hz)
    # =============================
    def control_loop(self):
        now = time.time()

        # watchdog
        if now - self.last_cmd_time > CMD_TIMEOUT:
            self.target_left = 0.0
            self.target_right = 0.0

        # rampa
        self.current_left = self._slew(self.current_left, self.target_left)
        self.current_right = self._slew(self.current_right, self.target_right)

        self._apply_motors()

    # =============================
    # RAMPATURA
    # =============================
    def _slew(self, current, target):
        step = MAX_ACCEL * CONTROL_RATE
        delta = target - current

        if abs(delta) <= step:
            return target

        return current + math.copysign(step, delta)

    # =============================
    # APPLICAZIONE MOTORI
    # =============================
    def _apply_motors(self):
        if self.motoreL is None: return

        l = self._apply_min_speed(self.current_left)
        r = self._apply_min_speed(self.current_right)

        # Inversione polarità se necessario (tuning empirico)
        # Assumiamo motori montati 'standard', altrimenti invertire qui es: -l
        self.motoreL.start(-l) 
        self.motoreD.start(-r)

    # =============================
    # UTILITIES
    # =============================
    def _apply_min_speed(self, v):
        if abs(v) < 0.1: # Cutoff molto basso
            return 0.0
        return math.copysign(max(abs(v), MIN_MOVING_PWM), v)

    def _clamp(self, v):
        return max(-MAX_PWM, min(MAX_PWM, v))


def main(args=None):
    rclpy.init(args=args)
    node = MotorControlNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
