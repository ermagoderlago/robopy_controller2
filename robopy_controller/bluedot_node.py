#!/usr/bin/env python3
# bluedot_node.py - Con filtro per comandi da tastiera

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray, Float32
from bluedot import BlueDot
from threading import Timer, Event
import time
from collections import deque
import numpy as np

DEAD_ZONE = 0.1
SERVO_MIN = 30.0
SERVO_MAX = 150.0
SERVO_RANGE = 90.0
KEYBOARD_TIMEOUT = 0.3  # 300ms timeout per fermare i motori

class BlueDotNode(Node):
    def __init__(self):
        super().__init__('bluedot_node')
        # Publisher per i motori
        self.motor_pub = self.create_publisher(Float64MultiArray, 'bluedot_input', 10)
        # Publisher per il servo
        self.servo_pub = self.create_publisher(Float32, 'servo_angle', 10)
        
        # Timer per gestire il timeout della tastiera
        self.keyboard_timer = None
        self.last_keyboard_command = None
        self.keyboard_active = False
        
        # Buffer per filtrare gli zeri intermittenti
        self.command_buffer = deque(maxlen=5)  # Buffer ultimi 5 comandi
        self.last_published = [0.0, 0.0]
        
        # BlueDot
        self.setup_bluedot()
        
        # Posiziona subito il servo a 90°
        self.publish_servo(90.0)
        
        self.get_logger().info("Bluedot node avviato con filtro timeout tastiera (0.3s)")
    
    def setup_bluedot(self):
        """Configura BlueDot"""
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
    
    # --- Metodi per BlueDot (invariati) ---
    def handle_motor_input(self, pos):
        """Gestisce input motori da BlueDot"""
        if pos is None:
            return
        
        x = round(pos.x, 4)
        y = round(pos.y, 4)
        
        if abs(x) < DEAD_ZONE and abs(y) < DEAD_ZONE:
            return
        
        # BlueDot ha priorità, quindi resetta il timer tastiera
        self.cancel_keyboard_timer()
        self.keyboard_active = False
        
        self.publish_motor_direct(x, y)
    
    def handle_motor_stop(self):
        """Ferma i motori da BlueDot"""
        self.publish_motor_direct(0.0, 0.0)
    
    def handle_servo_input(self, pos):
        """Gestisce input servo da BlueDot"""
        if pos is None:
            return

        x = round(pos.x, 4)
        if abs(x) < DEAD_ZONE:
            return

        # Mappatura da BlueDot a angolo servo
        angle = SERVO_MIN + ((x + 1) / 2) * (SERVO_MAX - SERVO_MIN)
        self.last_servo_angle = angle
        self.servo_active = True
        self.publish_servo(angle)
    
    def handle_servo_stop(self):
        """Resetta servo da BlueDot"""
        self.servo_active = False
        self.last_servo_angle = 90.0
        self.publish_servo(90.0)
    
    # --- Metodi per gestione tastiera con timeout ---
    def process_keyboard_command(self, x, y):
        """
        Processa comando da tastiera con filtro timeout
        x, y: comandi normalizzati (-1.0 a 1.0)
        """
        # Aggiungi comando al buffer
        self.command_buffer.append((x, y))
        
        # Calcola media mobile per filtrare zeri intermittenti
        if len(self.command_buffer) >= 3:
            # Prendi gli ultimi 3 comandi
            recent = list(self.command_buffer)[-3:]
            avg_x = np.mean([cmd[0] for cmd in recent])
            avg_y = np.mean([cmd[1] for cmd in recent])
            
            # Se la media è vicina a zero, potrebbe essere una pausa
            if abs(avg_x) < 0.05 and abs(avg_y) < 0.05:
                # Attiva timer di timeout
                self.start_keyboard_timer()
                return
            else:
                # Comando valido, cancella timer
                self.cancel_keyboard_timer()
                self.publish_motor_direct(x, y)
                self.last_keyboard_command = (x, y)
                self.keyboard_active = True
        else:
            # Buffer non ancora pieno, pubblica direttamente
            self.cancel_keyboard_timer()
            self.publish_motor_direct(x, y)
            self.last_keyboard_command = (x, y)
            self.keyboard_active = True
    
    def start_keyboard_timer(self):
        """Avvia timer per timeout tastiera"""
        if self.keyboard_timer is not None:
            self.keyboard_timer.cancel()
        
        self.keyboard_timer = Timer(KEYBOARD_TIMEOUT, self.keyboard_timeout)
        self.keyboard_timer.start()
        self.get_logger().debug(f"Timer timeout avviato ({KEYBOARD_TIMEOUT}s)")
    
    def cancel_keyboard_timer(self):
        """Cancella timer di timeout"""
        if self.keyboard_timer is not None:
            self.keyboard_timer.cancel()
            self.keyboard_timer = None
    
    def keyboard_timeout(self):
        """
        Callback chiamato quando scade il timeout tastiera
        Ferma i motori solo se non c'è stato nuovo input
        """
        if self.keyboard_active:
            self.get_logger().info("[KEYBOARD] Timeout scaduto - fermando motori")
            self.publish_motor_direct(0.0, 0.0)
            self.keyboard_active = False
            self.command_buffer.clear()
    
    # --- Metodi di pubblicazione ---
    def publish_motor_direct(self, x, y):
        """
        Pubblica direttamente comando motori senza filtri
        Usa deadzone per piccoli valori
        """
        # Applica deadzone
        if abs(x) < DEAD_ZONE:
            x = 0.0
        if abs(y) < DEAD_ZONE:
            y = 0.0
        
        # Pubblica solo se diverso dall'ultimo pubblicato
        if abs(x - self.last_published[0]) > 0.01 or abs(y - self.last_published[1]) > 0.01:
            msg = Float64MultiArray()
            msg.data = [float(x), float(y)]
            self.motor_pub.publish(msg)
            self.last_published = [x, y]
            
            source = "BLUEDOT" if not self.keyboard_active else "KEYBOARD"
            self.get_logger().info(f'[{source}] Motori: x={x:.2f}, y={y:.2f}')
    
    def publish_servo(self, angle):
        """Pubblica angolo servo"""
        # Limita angolo ai limiti fisici
        clamped_angle = max(SERVO_MIN, min(SERVO_MAX, angle))
        
        msg = Float32()
        msg.data = float(clamped_angle)
        self.servo_pub.publish(msg)
        self.get_logger().info(f'[SERVO] Angolo: {clamped_angle:.1f}°')
    
    # --- Metodo da chiamare dalla tastiera ---
    def keyboard_command(self, x, y):
        """
        Metodo pubblico per inviare comandi da tastiera
        Da chiamare dal nodo che gestisce la tastiera
        """
        self.process_keyboard_command(x, y)


def main(args=None):
    rclpy.init(args=args)
    node = BlueDotNode()
    
    # Esempio di come un altro nodo potrebbe inviare comandi
    # (da sostituire con il tuo nodo tastiera effettivo)
    def simulate_keyboard():
        """Funzione di esempio per simulare input tastiera"""
        import random
        while rclpy.ok():
            # Simula comandi intermittenti con zeri
            if random.random() > 0.7:
                x, y = 0.0, 0.0  # Zero intermittente
            else:
                x, y = random.uniform(-1, 1), random.uniform(-1, 1)
            
            node.keyboard_command(x, y)
            time.sleep(0.05)  # ~20Hz
    
    # Avvia simulazione in thread separato (solo per test)
    # import threading
    # sim_thread = threading.Thread(target=simulate_keyboard, daemon=True)
    # sim_thread.start()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("Interruzione da tastiera")
    finally:
        node.cancel_keyboard_timer()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()