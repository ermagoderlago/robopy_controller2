#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from vision_msgs.msg import Detection2DArray
import gpiozero as gpio
import time
import threading

class ServoCodaNode(Node):
    def __init__(self):
        super().__init__('servo_coda_node')
        
        self.declare_parameter('servo_pin', 18)
        self.pin_num = self.get_parameter('servo_pin').value
        
        self.get_logger().info(f'🚀 Avvio Servo su GPIO {self.pin_num}')
        
        try:
            # Usiamo parametri più larghi (0.5ms a 2.5ms) per forzare il movimento completo
            # Molti servi cinesi/standard non si muovono con i valori di default di gpiozero
            self.servo = gpio.Servo(
                self.pin_num, 
                min_pulse_width=0.5/1000, 
                max_pulse_width=2.5/1000
            )
            self.mode = "SERVO"
        except Exception:
            self.servo = gpio.PWMOutputDevice(self.pin_num, frequency=50, duty_cycle=0)
            self.mode = "PWM"

        self.busy = False
        self.create_subscription(Detection2DArray, '/oakdetections', self.detection_cb, 10)
        self.create_subscription(Bool, '/movement_detected', self.movement_cb, 10)
        
        self.startup_sequence()

    def move_and_relax(self, angle, duration=0.4):
        """
        Mappa i gradi (0-180) nel valore richiesto da gpiozero (-1 a 1)
        0°   -> -1.0
        90°  ->  0.0
        180° ->  1.0
        """
        try:
            # La formula magica per gpiozero.Servo
            target_value = (angle / 90.0) - 1.0
            
            if self.mode == "SERVO":
                self.servo.value = target_value
                time.sleep(duration)
                self.servo.detach() # Rilassa il motore
            else:
                # Se siamo in PWM software (duty cycle 2.5% a 12.5%)
                duty = 0.025 + (angle / 180.0) * 0.1
                self.servo.value = duty
                time.sleep(duration)
                self.servo.value = 0
                
        except Exception as e:
            self.get_logger().error(f"Errore: {e}")

    def scodinzola(self):
        if self.busy: return
        def anima():
            self.busy = True
            # Angoli ampi per vedere bene il movimento
            for _ in range(3):
                self.move_and_relax(45, 0.25)  # Sinistra ampia
                self.move_and_relax(135, 0.25) # Destra ampia
            self.move_and_relax(90, 0.3)      # Torna al centro
            self.busy = False
        threading.Thread(target=anima, daemon=True).start()

    def startup_sequence(self):
        def run():
            self.busy = True
            # Test estremi per verificare il range
            self.move_and_relax(10, 0.8)  # Quasi tutto a sinistra
            self.move_and_relax(170, 0.8) # Quasi tutto a destra
            self.move_and_relax(90, 0.5)  # Centro
            self.busy = False
        threading.Thread(target=run, daemon=True).start()

    def detection_cb(self, msg):
        if len(msg.detections) > 0: self.scodinzola()

    def movement_cb(self, msg):
        if msg.data: self.scodinzola()

def main(args=None):
    rclpy.init(args=args)
    node = ServoCodaNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        gpio.Device.close_all()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()