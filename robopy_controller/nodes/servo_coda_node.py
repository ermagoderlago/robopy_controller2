#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from vision_msgs.msg import Detection2DArray
import time
import threading

class ServoCodaNode(Node):
    def __init__(self):
        super().__init__('servo_coda_node')
        
        self.declare_parameter('servo_pin', 18)
        self.declare_parameter('calibration_wag', True)  # Slow wag until tracking
        
        self.pin_num = self.get_parameter('servo_pin').value
        self.calibration_wag = self.get_parameter('calibration_wag').value
        
        self.get_logger().info(f'🚀 Avvio Servo su GPIO {self.pin_num}')
        
        try:
            import gpiozero as gpio
            self.gpio = gpio
            # Parametri ampi (0.5ms a 2.5ms) per movimento completo
            self.servo = gpio.Servo(
                self.pin_num, 
                min_pulse_width=0.5/1000, 
                max_pulse_width=2.5/1000
            )
            self.mode = "SERVO"
        except Exception as e:
            self.get_logger().warn(f'GPIO non disponibile: {e}')
            self.servo = None
            self.mode = "DISABLED"
            return

        self.busy = False
        self.is_tracking = False
        self.calibration_active = self.calibration_wag
        
        # Subscriptions
        self.create_subscription(Detection2DArray, '/oakdetections', self.detection_cb, 10)
        self.create_subscription(Bool, '/movement_detected', self.movement_cb, 10)
        self.create_subscription(Bool, '/vo/tracking_status', self.tracking_cb, 10)
        
        # Start startup wag
        threading.Thread(target=self.startup_wag, daemon=True).start()

    def move_and_relax(self, angle, duration=0.4):
        """
        Mappa i gradi (0-180) nel valore richiesto da gpiozero (-1 a 1)
        0°   -> -1.0
        90°  ->  0.0
        180° ->  1.0
        """
        if self.servo is None:
            return
        try:
            target_value = (angle / 90.0) - 1.0
            self.servo.value = target_value
            time.sleep(duration)
            self.servo.detach()
        except Exception as e:
            self.get_logger().error(f"Errore servo: {e}")

    def startup_wag(self):
        """Scodinzolio iniziale di 5 secondi"""
        self.get_logger().info('🐕 Avvio scodinzolio iniziale (5s)...')
        self.busy = True
        
        start_time = time.time()
        while time.time() - start_time < 5.0 and rclpy.ok():
            self.move_and_relax(45, 0.4)
            self.move_and_relax(135, 0.4)
        
        self.move_and_relax(90, 0.5)  # Torna al centro
        self.busy = False
        self.get_logger().info('✅ Scodinzolio iniziale terminato. In attesa di eventi.')

    def scodinzola(self):
        """Scodinzolio normale (reazione a eventi)"""
        if self.busy:
            return
        def anima():
            self.busy = True
            for _ in range(3):
                self.move_and_relax(45, 0.25)
                self.move_and_relax(135, 0.25)
            self.move_and_relax(90, 0.3)
            self.busy = False
        threading.Thread(target=anima, daemon=True).start()

    def tracking_cb(self, msg):
        """Chiamato quando arriva lo stato del tracking VO"""
        if msg.data and not self.is_tracking:
            self.is_tracking = True
            self.calibration_active = False  # Stop calibration wag
            self.get_logger().info('🎯 VO Tracking confermato!')
    
    def detection_cb(self, msg):
        if len(msg.detections) > 0:
            self.scodinzola()

    def movement_cb(self, msg):
        if msg.data:
            self.scodinzola()

def main(args=None):
    rclpy.init(args=args)
    node = ServoCodaNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node.servo is not None:
            node.gpio.Device.close_all()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()