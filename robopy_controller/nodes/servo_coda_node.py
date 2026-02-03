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
        
        # Start calibration waggle if enabled
        if self.calibration_wag:
            self.calibration_thread = threading.Thread(target=self.calibration_wag_loop, daemon=True)
            self.calibration_thread.start()
        else:
            self.startup_sequence()

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

    def calibration_wag_loop(self):
        """Scodinzolio lento durante la calibrazione"""
        self.get_logger().info('🐕 Avvio scodinzolio calibrazione...')
        self.busy = True
        
        while self.calibration_active and rclpy.ok():
            # Movimento lento e ampio
            self.move_and_relax(60, 0.6)
            if not self.calibration_active:
                break
            self.move_and_relax(120, 0.6)
            if not self.calibration_active:
                break
        
        # Quando il tracking inizia, scodinzola veloce per celebrare!
        self.get_logger().info('✅ Tracking attivo! Scodinzolio felice!')
        self.scodinzola_felice()
        self.move_and_relax(90, 0.5)  # Torna al centro
        self.busy = False

    def scodinzola(self):
        """Scodinzolio normale (reazione a eventi)"""
        if self.busy or self.calibration_active:
            return
        def anima():
            self.busy = True
            for _ in range(3):
                self.move_and_relax(45, 0.25)
                self.move_and_relax(135, 0.25)
            self.move_and_relax(90, 0.3)
            self.busy = False
        threading.Thread(target=anima, daemon=True).start()

    def scodinzola_felice(self):
        """Scodinzolio veloce per celebrare"""
        for _ in range(5):
            self.move_and_relax(40, 0.15)
            self.move_and_relax(140, 0.15)

    def startup_sequence(self):
        """Sequenza di test all'avvio (se calibration_wag è disabilitato)"""
        def run():
            self.busy = True
            self.move_and_relax(10, 0.8)
            self.move_and_relax(170, 0.8)
            self.move_and_relax(90, 0.5)
            self.busy = False
        threading.Thread(target=run, daemon=True).start()

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