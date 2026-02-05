#!/usr/bin/env python3
# teleop_keyboard_node.py - versione migliorata con mantenimento comandi

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray
import sys
import tty
import termios
import select
import time
from collections import deque


class TeleopKeyboard(Node):
    def __init__(self):
        super().__init__('teleop_keyboard')

        self.pub = self.create_publisher(
            Float64MultiArray,
            'bluedot_input',
            10
        )

        # Stato comando
        self.target_x = 0.0
        self.target_y = 0.0
        self.current_x = 0.0
        self.current_y = 0.0
        self.speed = 1.0
        
        # Stato tasti premuti
        self.keys_pressed = set()
        
        # Configurazione tempi
        self.deadzone_timeout = 0.3  # Tempo di silenzio prima di fermare
        self.key_repeat_delay = 0.15  # Ritardo per evitare ripetizioni indesiderate
        
        # Buffer per smoothing dei comandi
        self.command_history = deque(maxlen=5)
        self.last_command_time = time.time()
        self.last_key_time = 0.0
        
        # Watchdog più intelligente
        self.last_input_time = time.time()
        self.sent_stop = False
        
        # Terminale: RAW
        self.stdin_fd = sys.stdin.fileno()
        self.old_termios = termios.tcgetattr(self.stdin_fd)
        tty.setraw(self.stdin_fd)
        
        # Timer a 50Hz per pubblicazione fluida
        self.timer = self.create_timer(0.02, self.loop)
        
        self.get_logger().info("=== TELEOP KEYBOARD (VERSIONE MIGLIORATA) ===")
        self.get_logger().info("W/S: avanti/indietro (mantenere premuto)")
        self.get_logger().info("A/D: sinistra/destra (mantenere premuto)")
        self.get_logger().info("SPACE: STOP immediato")
        self.get_logger().info("+/-: velocità")
        self.get_logger().info("Q: esci")
        self.get_logger().info(f"Deadzone timeout: {self.deadzone_timeout}s")
        
        # Pubblica stato iniziale
        self.publish_current()

    # --------------------------------------------------

    def get_key(self):
        """Legge un tasto senza bloccare"""
        rlist, _, _ = select.select([sys.stdin], [], [], 0)
        if rlist:
            key = sys.stdin.read(1)
            # Gestisci Ctrl+C
            if key == '\x03':
                return 'q'
            return key
        return None

    # --------------------------------------------------

    def update_target_from_keys(self):
        """Aggiorna il comando target in base ai tasti premuti"""
        self.target_x = 0.0
        self.target_y = 0.0
        
        # Calcola comando in base a tutti i tasti premuti
        if 'w' in self.keys_pressed:
            self.target_y = 1.0
        if 's' in self.keys_pressed:
            self.target_y = -1.0
        if 'a' in self.keys_pressed:
            self.target_x = -1.0
        if 'd' in self.keys_pressed:
            self.target_x = 1.0
            
        # Normalizza se ci sono comandi diagonali
        if self.target_x != 0 and self.target_y != 0:
            norm = (self.target_x**2 + self.target_y**2)**0.5
            self.target_x /= norm
            self.target_y /= norm

    # --------------------------------------------------

    def publish_current(self):
        """Pubblica il comando corrente"""
        msg = Float64MultiArray()
        msg.data = [self.current_x * self.speed, self.current_y * self.speed]
        self.pub.publish(msg)
        
        # Aggiungi alla history per debug
        self.command_history.append((self.current_x, self.current_y, time.time()))

    # --------------------------------------------------

    def smooth_transition(self):
        """Transizione morbida dal comando corrente al target"""
        smoothing_factor = 0.3  # Più alto = più rapido
        
        # Transizione esponenziale verso il target
        self.current_x = self.current_x + (self.target_x - self.current_x) * smoothing_factor
        self.current_y = self.current_y + (self.target_y - self.current_y) * smoothing_factor
        
        # Soglia minima per evitare valori infinitesimali
        if abs(self.current_x) < 0.01:
            self.current_x = 0.0
        if abs(self.current_y) < 0.01:
            self.current_y = 0.0

    # --------------------------------------------------

    def loop(self):
        """Loop principale a 50Hz"""
        now = time.time()
        key = self.get_key()
        
        # Gestisci input da tastiera
        if key:
            current_time = time.time()
            
            # Evita ripetizioni troppo rapide dello stesso tasto
            if current_time - self.last_key_time > self.key_repeat_delay:
                self.last_input_time = now
                self.last_key_time = current_time
                self.sent_stop = False
                
                # Gestisci pressione tasti (aggiungi al set)
                if key in ('w', 'W'):
                    self.keys_pressed.add('w')
                    self.keys_pressed.discard('s')
                elif key in ('s', 'S'):
                    self.keys_pressed.add('s')
                    self.keys_pressed.discard('w')
                elif key in ('a', 'A'):
                    self.keys_pressed.add('a')
                    self.keys_pressed.discard('d')
                elif key in ('d', 'D'):
                    self.keys_pressed.add('d')
                    self.keys_pressed.discard('a')
                elif key == ' ':
                    # STOP immediato - svuota tutti i tasti
                    self.keys_pressed.clear()
                    self.target_x = 0.0
                    self.target_y = 0.0
                    self.current_x = 0.0
                    self.current_y = 0.0
                    self.publish_current()
                    return
                elif key == '+':
                    self.speed = min(2.0, self.speed + 0.2)
                    self.get_logger().info(f"Velocità: {self.speed:.1f}")
                elif key == '-':
                    self.speed = max(0.1, self.speed - 0.2)
                    self.get_logger().info(f"Velocità: {self.speed:.1f}")
                elif key in ('q', 'Q'):
                    self.shutdown()
                    return
                    
                # Aggiorna comando target
                self.update_target_from_keys()
                
                # Log di debug (solo se cambiamenti significativi)
                if abs(self.target_x) > 0 or abs(self.target_y) > 0:
                    self.get_logger().debug(
                        f"Comando: ({self.target_x:.2f}, {self.target_y:.2f}) "
                        f"Tasti: {self.keys_pressed}"
                    )
        
        # Gestisci rilascio tasti (simulato tramite timeout)
        # Se nessun input per deadzone_timeout, svuota i tasti
        if not self.sent_stop and (now - self.last_input_time) > self.deadzone_timeout:
            if self.keys_pressed:
                self.keys_pressed.clear()
                self.update_target_from_keys()
                self.get_logger().debug("Rilascio tasti (timeout)")
        
        # Smoothing del comando
        self.smooth_transition()
        
        # Pubblica sempre (mantiene il comando attivo)
        self.publish_current()

    # --------------------------------------------------

    def shutdown(self):
        """Pulizia e shutdown"""
        self.get_logger().info("STOP e uscita")
        
        # Assicura che l'ultimo comando sia zero
        self.keys_pressed.clear()
        self.target_x, self.target_y = 0.0, 0.0
        self.current_x, self.current_y = 0.0, 0.0
        self.publish_current()
        
        # Ripristina terminale
        termios.tcsetattr(self.stdin_fd, termios.TCSADRAIN, self.old_termios)
        self.destroy_node()
        rclpy.shutdown()


def main():
    rclpy.init()
    node = TeleopKeyboard()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.shutdown()
    except Exception as e:
        node.get_logger().error(f"Errore: {e}")
        node.shutdown()


if __name__ == '__main__':
    main()