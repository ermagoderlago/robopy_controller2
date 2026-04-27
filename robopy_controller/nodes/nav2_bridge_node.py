
#!/usr/bin/env python3
# teleop_bridge_node.py
# Nodo "pulito" per teleoperazione: riceve /teleop/cmd_vel (Twist o TwistStamped)
# e converte in Float64MultiArray[x,y] su 'bluedot_input' per il tuo controller motori.

import rclpy
from rclpy.node import Node
from rclpy.time import Time
from geometry_msgs.msg import Twist, TwistStamped
from std_msgs.msg import Float64MultiArray

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

class TeleopBridge(Node):
    def __init__(self):
        super().__init__('teleop_bridge')

        # Parametri configurabili
        self.declare_parameters(
            namespace='',
            parameters=[
                ('teleop_topic',       '/teleop/cmd_vel'),   # Topic usato dal pannello Teleop
                ('output_topic',       'bluedot_input'),     # Uscita verso il tuo nodo motori
                ('cmd_timeout_sec',    0.5),                 # Deadman timeout
                ('scale_linear',       1.0),                 # scala linear.x
                ('scale_angular',      1.0),                 # scala angular.z
                ('invert_angular',     False),               # inverti angolare se meccanica specchiata
            ]
        )

        self.teleop_topic     = self.get_parameter('teleop_topic').get_parameter_value().string_value
        self.output_topic     = self.get_parameter('output_topic').get_parameter_value().string_value
        self.cmd_timeout_sec  = float(self.get_parameter('cmd_timeout_sec').value)
        self.scale_linear     = float(self.get_parameter('scale_linear').value)
        self.scale_angular    = float(self.get_parameter('scale_angular').value)
        self.invert_angular   = bool(self.get_parameter('invert_angular').value)

        # Publisher uscita
        self.pub = self.create_publisher(Float64MultiArray, self.output_topic, 10)

        # Subscribers (accetta Twist e TwistStamped sullo stesso topic)
        self.create_subscription(Twist,        self.teleop_topic, self.cb_twist,   10)
        self.create_subscription(TwistStamped, self.teleop_topic, self.cb_stamped, 10)

        # Stato + watchdog
        self.last_cmd_time = self.get_clock().now()
        self.timer         = self.create_timer(0.02, self.watchdog_loop)  # 50 Hz

        self.get_logger().info(
            f"✅ teleop_bridge avviato | teleop='{self.teleop_topic}' → '{self.output_topic}'"
        )

    # --- Callbacks ---
    def cb_twist(self, msg: Twist):
        self._process(msg.linear.x, msg.angular.z)

    def cb_stamped(self, msg: TwistStamped):
        # Il Teleop di Foxglove usa Twist; se ricevessimo TwistStamped lo trattiamo allo stesso modo.
        self._process(msg.twist.linear.x, msg.twist.angular.z)

    def _process(self, lin_x: float, ang_z: float):
        self.last_cmd_time = self.get_clock().now()

        # Scaling e inversione opzionale dell'angolare
        lin = lin_x * self.scale_linear
        ang = ang_z * ( -self.scale_angular if self.invert_angular else self.scale_angular )

        # Clamp come da tua pipeline: x = angular.z, y = linear.x, entrambi in [-1, +1]
        x = clamp(ang, -1.0, 1.0)
        y = clamp(lin, -1.0, 1.0)

        out = Float64MultiArray()
        out.data = [x, y]
        self.pub.publish(out)

    # --- Deadman ---
    def watchdog_loop(self):
        dt = (self.get_clock().now() - self.last_cmd_time).nanoseconds / 1e9
        if dt > self.cmd_timeout_sec:
            stop = Float64MultiArray()
            stop.data = [0.0, 0.0]
            self.pub.publish(stop)

def main():
    rclpy.init()
    node = TeleopBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
