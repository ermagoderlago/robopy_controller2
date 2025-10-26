import time
from gpiozero import DistanceSensor
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Range
from collections import deque

class UltrasonicSensor(Node):
    def __init__(self):
        super().__init__('ultrasonic_sensor')
        time.sleep(2)  # Attendi 2 secondi all'avvio

        # Dichiara i parametri
        self.declare_parameter('trig_pin', 23)
        self.declare_parameter('echo_pin', 24)
        self.declare_parameter('frame_id', 'ultrasonic_sensor')
        self.declare_parameter('min_range', 0.02)
        self.declare_parameter('max_range', 2.0)

        # Leggi i parametri
        self.trig_pin = self.get_parameter('trig_pin').value
        self.echo_pin = self.get_parameter('echo_pin').value
        self.frame_id = self.get_parameter('frame_id').value
        self.min_range = self.get_parameter('min_range').value
        self.max_range = self.get_parameter('max_range').value

        # Configura DistanceSensor di gpiozero
        try:
            self.sensor = DistanceSensor(echo=self.echo_pin, trigger=self.trig_pin, max_distance=self.max_range)
        except Exception as e:
            self.get_logger().error(f"Errore durante l'inizializzazione DistanceSensor: {e}")
            raise

        # Buffer per media mobile degli ultimi 10 valori
        self.buffer_size = 10
        self.measurements = deque(maxlen=self.buffer_size)

        # Publisher
        self.publisher_ = self.create_publisher(Range, 'ultrasonic_range', 10)

        # Timer: leggi a 20 Hz (0.05 s)
        self.create_timer(0.05, self.read_sensor)

        # Timer: pubblica a 10 Hz (0.1 s)
        self.create_timer(0.1, self.publish_average)

        self.get_logger().info(f"Nodo sensore ultrasonico avviato. TRIG={self.trig_pin}, ECHO={self.echo_pin}")

    def read_sensor(self):
        """Leggi il sensore e aggiorna il buffer dei valori."""
        try:
            distance_m = self.sensor.distance  # Valore in metri
            if distance_m >= 0.0:
                self.measurements.append(distance_m)
            else:
                self.get_logger().warn("Misurazione non valida, ignorata")
        except Exception as e:
            self.get_logger().warn(f"Errore lettura sensore: {e}")

    def publish_average(self):
        """Pubblica la media mobile degli ultimi valori."""
        if not self.measurements:
            self.get_logger().warn("Nessun dato disponibile per la media mobile")
            return

        avg_distance = sum(self.measurements) / len(self.measurements)
        msg = Range()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id
        msg.radiation_type = Range.ULTRASOUND
        msg.field_of_view = 0.1  # angolo in radianti
        msg.min_range = self.min_range
        msg.max_range = self.max_range
        msg.range = avg_distance

        self.publisher_.publish(msg)
        self.get_logger().debug(f"Media mobile pubblicata: {msg.range:.2f} m")

def main(args=None):
    rclpy.init(args=args)
    ultrasonic_sensor_node = None
    try:
        ultrasonic_sensor_node = UltrasonicSensor()
        rclpy.spin(ultrasonic_sensor_node)
    except RuntimeError as e:
        print(f"Errore DistanceSensor: {e}")
    except KeyboardInterrupt:
        pass
    finally:
        if ultrasonic_sensor_node is not None:
            ultrasonic_sensor_node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
