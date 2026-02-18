#!/usr/bin/env python3
"""
Motor Control Node — BuildHAT PassiveMotor driver
==================================================
Dual input: BlueDot (joystick) + cmd_vel (Nav2/AI).

IMPORTANT: Passive motors have NO speed control — they are either
ON (100% PWM) or OFF (0%). The cmd_vel callback uses bang-bang
control to handle this limitation.
"""

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

MAX_PWM = 100.0          # Valore massimo per BuildHAT (0-100)
MIN_MOVING_PWM = 100.0   # Motori passivi: serve SEMPRE 100% per muoversi

# MAPPING FISICO (STIMATO)
BASE_WIDTH = 0.15        # Distanza tra le ruote (metri)

MAX_ACCEL = 300.0        # % / secondo (PWM change rate)
CONTROL_RATE = 0.02      # 50 Hz
CMD_TIMEOUT = 1.0        # Stop se non ricevo comandi per x sec (Nav2 può essere più lento)
INPUT_DEADZONE = 0.05

# Soglie per bang-bang control (cmd_vel)
CMD_VEL_LINEAR_DEADZONE = 0.01   # m/s — sotto questo, considero "fermo"
CMD_VEL_ANGULAR_DEADZONE = 0.01  # rad/s

# Debug logging
DEBUG_LOG_INTERVAL = 50  # Log ogni N ticks del control loop (50 = 1s)


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
        
        # 2. Nav2 / Cmd Vel (Autonomous) + AI move_relative
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
        self.last_cmd_source = "none"  # "bluedot" or "cmd_vel"
        self._debug_tick = 0

        self.get_logger().info("✅ Motor control node avviato (Dual Input: BlueDot + CmdVel)")
        self.get_logger().info(f"   MIN_MOVING_PWM={MIN_MOVING_PWM}%, CMD_TIMEOUT={CMD_TIMEOUT}s")

    # =============================
    # INPUT 1: BLUEDOT (Joystick)
    # =============================
    def bluedot_callback(self, msg):
        if len(msg.data) != 2:
            return

        x, y = msg.data  # -1.0 a 1.0
        self.last_cmd_time = time.time()
        self.last_cmd_source = "bluedot"

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
    # INPUT 2: CMD_VEL (Nav2 / AI)
    # BANG-BANG CONTROL per motori passivi
    # =============================
    def cmd_vel_callback(self, msg):
        self.last_cmd_time = time.time()
        self.last_cmd_source = "cmd_vel"
        
        linear = msg.linear.x
        angular = msg.angular.z
        
        # Differential Drive Kinematics → wheel velocities (m/s)
        v_left_mps = linear - (angular * BASE_WIDTH / 2.0)
        v_right_mps = linear + (angular * BASE_WIDTH / 2.0)
        
        # BANG-BANG: motori passivi → 100% o 0%, nessuna via di mezzo
        if abs(v_left_mps) > CMD_VEL_LINEAR_DEADZONE:
            target_l = math.copysign(MAX_PWM, v_left_mps)
        else:
            target_l = 0.0
            
        if abs(v_right_mps) > CMD_VEL_LINEAR_DEADZONE:
            target_r = math.copysign(MAX_PWM, v_right_mps)
        else:
            target_r = 0.0
        
        self.target_left = target_l
        self.target_right = target_r
        
        # Debug log
        self.get_logger().info(
            f"📥 CmdVel: lin={linear:.3f} ang={angular:.3f} → "
            f"L={target_l:+.0f}% R={target_r:+.0f}% "
            f"(wheel: L={v_left_mps:.3f} R={v_right_mps:.3f} m/s)"
        )

    # =============================
    # CONTROL LOOP (50 Hz)
    # =============================
    def control_loop(self):
        now = time.time()

        # Watchdog: stop se nessun comando ricevuto
        if now - self.last_cmd_time > CMD_TIMEOUT:
            if self.target_left != 0.0 or self.target_right != 0.0:
                self.get_logger().info(f"⏱️  Watchdog timeout ({CMD_TIMEOUT}s) → STOP")
            self.target_left = 0.0
            self.target_right = 0.0

        # Rampa (slew rate limiting)
        self.current_left = self._slew(self.current_left, self.target_left)
        self.current_right = self._slew(self.current_right, self.target_right)

        self._apply_motors()
        
        # Periodic debug log
        self._debug_tick += 1
        if self._debug_tick >= DEBUG_LOG_INTERVAL:
            self._debug_tick = 0
            if self.current_left != 0.0 or self.current_right != 0.0:
                self.get_logger().info(
                    f"🔧 Loop: src={self.last_cmd_source} "
                    f"target=[{self.target_left:+.0f},{self.target_right:+.0f}] "
                    f"current=[{self.current_left:+.0f},{self.current_right:+.0f}]"
                )

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
        if self.motoreL is None:
            return

        l = self._apply_min_speed(self.current_left)
        r = self._apply_min_speed(self.current_right)

        # Inversione polarità (tuning empirico)
        self.motoreL.start(-l) 
        self.motoreD.start(-r)

    # =============================
    # UTILITIES
    # =============================
    def _apply_min_speed(self, v):
        if abs(v) < 0.1:  # Cutoff molto basso
            return 0.0
        # Per motori passivi, qualsiasi valore != 0 → MIN_MOVING_PWM (100%)
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
