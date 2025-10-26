import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray, Float32  # Aggiunto Float32 per i servo
from bluedot import BlueDot
from threading import Timer, Event
import time

DEAD_ZONE = 0.1
SERVO_MIN = 30.0   # Limite fisico minimo del servo
SERVO_MAX = 150.0  # Limite fisico massimo del servo
SERVO_RANGE = 90.0  # Range di movimento del servo (+/- 90° dal centro)

class BlueDotNode(Node):
    def __init__(self):
        super().__init__('bluedot_node')
        # Publisher per i motori (primo bottone)
        self.motor_pub = self.create_publisher(Float64MultiArray, 'bluedot_input', 10)
        # Publisher per il servo (terzo bottone) - modificato a Float32
        self.servo_pub = self.create_publisher(Float32, 'servo_angle', 10)  # Cambiato il tipo di messaggio

        self.bd = BlueDot(rows=1, cols=3)
        self.bd[0,0].color = "gray"
        self.bd[0,0].square = True
        self.bd[2,0].color = "gray"
        self.bd[2,0].square = True

        # Primo bottone - motori
        self.bd[0,0].when_pressed = self.handle_motor_input
        self.bd[0,0].when_moved = self.handle_motor_input
        self.bd[0,0].when_released = self.handle_motor_stop

        # Terzo bottone - servo
        self.bd[2,0].when_pressed = self.handle_servo_input
        self.bd[2,0].when_moved = self.handle_servo_input
        self.bd[2,0].when_released = self.handle_servo_stop

        # Stato servo
        self.last_servo_angle = 90.0  # Posizione centrale iniziale
        self.servo_active = False

        # All'avvio posiziona subito il servo a 90°
        self.publish_servo(90.0)

    # --- Motori (rimangono invariati) ---
    def handle_motor_input(self, pos):
        if pos is None:
            return
        x = round(pos.x, 4)
        y = round(pos.y, 4)
        if abs(x) < DEAD_ZONE and abs(y) < DEAD_ZONE:
            return
        self.publish_motor(x, y)

    def handle_motor_stop(self):
        self.publish_motor(0.0, 0.0)

    def publish_motor(self, x, y):
        msg = Float64MultiArray()
        msg.data = [x, y]
        self.motor_pub.publish(msg)
        self.get_logger().info(f'[MOTOR] Published: x={x}, y={y}')

    # --- Servo (modificato) ---
    def handle_servo_input(self, pos):
        if pos is None:
            return

        x = round(pos.x, 4)
        if abs(x) < DEAD_ZONE:
            return

        # Mappatura da posizione BlueDot (-1..1) a angolo servo (SERVO_MIN..SERVO_MAX)
        angle = SERVO_MIN + ((x + 1) / 2) * (SERVO_MAX - SERVO_MIN)
        self.last_servo_angle = angle
        self.servo_active = True
        self.publish_servo(angle)

    def handle_servo_stop(self):
        self.servo_active = False
        # Al rilascio torna a 90°
        self.last_servo_angle = 90.0
        self.publish_servo(90.0)

    def publish_servo(self, angle):
        # Assicurati che l'angolo sia nel range fisico
        clamped_angle = max(SERVO_MIN, min(SERVO_MAX, angle))
        msg = Float32()
        msg.data = clamped_angle
        self.servo_pub.publish(msg)
        self.get_logger().info(f'[SERVO] Published angle: {clamped_angle:.1f}°')

def main(args=None):
    rclpy.init(args=args)
    node = BlueDotNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("Node shutdown by user")
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
