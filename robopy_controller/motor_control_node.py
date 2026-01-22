#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray
from buildhat import PassiveMotor, Hat
import math
import time

# =========================
# PARAMETRI DI TUNING
# =========================

MAX_SPEED = 100.0
MIN_MOVING_SPEED = 30.0
MAX_ACCEL = 300.0        # % / secondo
CONTROL_RATE = 0.02      # 50 Hz
CMD_TIMEOUT = 0.1
INPUT_DEADZONE = 0.05


class MotorControlNode(Node):
    def __init__(self):
        super().__init__('motor_control_node')

        # --- ROS ---
        self.create_subscription(
            Float64MultiArray,
            'bluedot_input',
            self.cmd_callback,
            10
        )

        self.timer = self.create_timer(CONTROL_RATE, self.control_loop)

        # --- HAT & MOTORI ---
        self.hat = Hat()
        self.motoreL = PassiveMotor('B')
        self.motoreD = PassiveMotor('A')

        # --- STATO ---
        self.target_left = 0.0
        self.target_right = 0.0
        self.current_left = 0.0
        self.current_right = 0.0
        self.last_cmd_time = time.time()

        self.get_logger().info("✅ Motor control node avviato (passive motors)")

    # =============================
    # INPUT ROS
    # =============================
    def cmd_callback(self, msg):
        if len(msg.data) != 2:
            return

        x, y = msg.data
        self.last_cmd_time = time.time()

        if abs(x) < INPUT_DEADZONE and abs(y) < INPUT_DEADZONE:
            self.target_left = 0.0
            self.target_right = 0.0
            return

        linear = y * MAX_SPEED
        angular = x * MAX_SPEED

        self.target_left = self._clamp(linear + angular)
        self.target_right = self._clamp(linear - angular)

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
        l = self._apply_min_speed(self.current_left)
        r = self._apply_min_speed(self.current_right)

        if l == 0.0 and r == 0.0:
            self.motoreL.stop()
            self.motoreD.stop()
            return

        self.motoreL.start(-l)
        self.motoreD.start(-r) 

    # =============================
    # UTILITIES
    # =============================
    def _apply_min_speed(self, v):
        if abs(v) < 0.01:
            return 0.0
        return math.copysign(max(abs(v), MIN_MOVING_SPEED), v)

    def _clamp(self, v):
        return max(-MAX_SPEED, min(MAX_SPEED, v))


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
