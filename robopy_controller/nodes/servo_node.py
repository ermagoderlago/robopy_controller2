import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32
from gpiozero import AngularServo
import time
import sys  # <--- AGGIUNGI QUESTO

class ServoController(Node):
    def __init__(self):
        super().__init__('servo_controller')

        self.servo_pin = 12  # Usa GPIO 12 (pin fisico 32)
        self.timeout_seconds = 3.0
        self.last_command_time = time.time()
        self.servo_active = False  # Per tracciare se il servo è attivo

        try:
            self.servo = AngularServo(
                pin=self.servo_pin,
                min_angle=0,
                max_angle=180,
                min_pulse_width=1/1000,   # Cambia da 0.5/1000 a 1/1000
                max_pulse_width=2/1000,   # Cambia da 2.5/1000 a 2/1000
                frame_width=20/1000
            )

            #self.servo.angle = None  # Inizializza a None
            time.sleep(0.1)          # Piccola attesa
            #self.servo.angle = 90    # Poi posiziona a 90°
            self.servo_active = True
            #self.get_logger().info(" Servo inizializzato a 90°")
        except Exception as e:
            self.get_logger().error(f" Errore nell'inizializzazione del servo: {str(e)}")
            raise RuntimeError(f"Errore servo: {str(e)}")

        self.subscription = self.create_subscription(
            Float32,
            'servo_angle',
            self.listener_callback,
            10
        )
        self.get_logger().info(" ServoController pronto, in ascolto su 'servo_angle'")

        # Timer per disattivare il servo dopo inattività
        self.create_timer(0.5, self.check_inactivity)

    def listener_callback(self, msg):
        try:
            angle = max(0.0, min(180.0, msg.data))  # Limita tra 0° e 180°
            corrected_angle = (angle - 90) * (-1) + 90  # Eventuale inversione
            self.servo.angle = corrected_angle
            self.servo_active = True
            self.last_command_time = time.time()
            self.get_logger().info(f" Angolo impostato a: {angle:.1f}°")
        except Exception as e:
            self.get_logger().error(f"Errore nell'impostazione dell'angolo: {str(e)}")

    def check_inactivity(self):
        """Disattiva il PWM se non ci sono comandi da più di timeout_seconds"""
        if self.servo_active and (time.time() - self.last_command_time) > self.timeout_seconds:
            self.servo.angle = None  # Ferma il PWM
            self.servo_active = False
            self.get_logger().info("⏱️ Timeout: servo disattivato per inattività")

    def cleanup(self):
        if hasattr(self, 'servo'):
            self.servo.angle = None
            self.servo.close()
        self.get_logger().info("🔌 ServoController arrestato correttamente")

def main(args=None):
    rclpy.init(args=args)
    try:
        node = ServoController()
        rclpy.spin(node)
    except Exception as e:
        print(f"[ERRORE] {str(e)}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n Interrotto dall'utente")
    finally:
        if 'node' in locals():
            node.cleanup()
            node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()

