"""
Questo nodo ROS 2 permette il controllo proporzionale dei motori in base ai comandi ricevuti da BlueDot.
Riceve valori X e Y da un messaggio Float64MultiArray e li converte in velocità per i motori sinistro e destro.
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Point, Pose, Quaternion, Twist, Vector3
from buildhat import PassiveMotor, Hat
import math
import time
import os

MAX_VELOCITA = 100
inc = 1


class MotorControlNode(Node):
    def __init__(self):
        super().__init__('motor_control_node')
        self.subscription = self.create_subscription(
            Float64MultiArray,
            'bluedot_input',
            self.listener_callback,
            100)

        try:
            self.hat = Hat(device='/dev/ttyAMA0')
            self.get_logger().info("BuildHAT inizializzato con /dev/ttyAMA0")
        except Exception as e:
            self.get_logger().warn(f"Errore durante l'inizializzazione di Hat con /dev/ttyAMA0: {e}, provo senza specificare device")
            try:
                self.hat = Hat()
                self.get_logger().info("BuildHAT inizializzato senza device specificato")
            except Exception as e2:
                self.get_logger().fatal(f"Errore durante l'inizializzazione di Hat: {e2}")
                raise RuntimeError(f"BuildHAT non trovata: {e2}")

        try:
            print(self.hat.get())
        except Exception as e:
            self.get_logger().warn(f"Errore durante la lettura delle informazioni dell'HAT: {e}")

        try:
            self.motoreD = PassiveMotor('A')
            self.motoreL = PassiveMotor('B')
        except Exception as e:
            self.get_logger().fatal(f"Errore durante l'inizializzazione dei motori: {e}")
            rclpy.shutdown()
            return

        self.motor_speed_pub = self.create_publisher(Float64MultiArray, 'motor_speed', 10)

        self.left_speed = 0.0
        self.right_speed = 0.0
    

        self.get_logger().info("Nodo controllo motori avviato")

        # Queste righe sono per il PRIMO bottone (disattivale se non ti serve)
        #self.bd[0,0].when_pressed = self.handle_input
        #self.bd[0,0].when_moved = self.handle_input
        #self.bd[0,0].when_released = self.handle_stop

        # Queste righe sono per il TERZO bottone (attivale!)
        #self.bd[2,0].when_pressed = self.handle_input
        #self.bd[2,0].when_moved = self.handle_input
        #self.bd[2,0].when_released = self.handle_stop

    def listener_callback(self, msg):
        if len(msg.data) != 2:
            self.get_logger().warn("Messaggio bluedot_input non valido: attesi due valori (x, y)")
            return

        x, y = msg.data
        self.left_speed_old = self.left_speed
        self.right_speed_old = self.right_speed

        left_speed, right_speed = self.calcola_velocita(x, y,MAX_VELOCITA)

        self.left_speed = left_speed
        self.right_speed = right_speed

        if left_speed == 0.0 and right_speed == 0.0:
            self.ferma_motori()
        else:
            self.muovi_motori(left_speed, right_speed)
            #self.accellera(left_speed, right_speed)

        self.pubblica_velocita_motori()

        self.get_logger().info(f"Comando ricevuto: x={x:.3f}, y={y:.3f} -> sinistra={left_speed:.2f}, destra={right_speed:.2f}")

    def calcola_velocita(self, x, y, velocita_massima=MAX_VELOCITA):
        deadzone = 0.30
        modulo = math.sqrt(x ** 2 + y ** 2)
        

        if modulo < deadzone:
            return 0.0, 0.0

        #calcolo velocita da usare
        direzione = y / abs(y) if y != 0 else 0.0
        velocita = y  * 100
        
        vel_ass = abs(velocita)
        velocita=max(vel_ass,modulo*100)*direzione
        
        if velocita > velocita_massima:
            velocita = velocita_massima
        

        if (x > 0.0 and y > 0) or (x < 0 and y < 0):
             
            velocita_sinistra = velocita
            velocita_destra = velocita * (1 - abs(x) *1.5)

        elif (x < 0 and y > 0) or (x > 0 and y < 0):
            velocita_destra = velocita
            velocita_sinistra = velocita * (1- abs(x)*1.5)

        else:
            velocita_sinistra = velocita
            velocita_destra = velocita

        #self.get_logger().info(f"calcola velocita -> sinistra={velocita_sinistra:.2f}, destra={velocita_destra:.2f}")

        return velocita_sinistra, velocita_destra

    def muovi_motori(self, velocita_sinistra, velocita_destra):
        try:
            
            #self.motoreL.on()
            self.motoreL.start(velocita_sinistra)
            
            
            #self.motoreD.on()
            self.motoreD.start(-velocita_destra)
            

        except Exception as e:
            self.get_logger().error(f"Errore durante il movimento dei motori: {e}")

    def ferma_motori(self):
        try:
            self.motoreL.stop()
            self.motoreD.stop()
            
            self.get_logger().info("Motori fermati")
        except Exception as e:
            self.get_logger().error(f"Errore durante l'arresto dei motori: {e}")

    def pubblica_velocita_motori(self):
        msg = Float64MultiArray()
        msg.data = [float(self.left_speed), float(self.right_speed)]
        self.motor_speed_pub.publish(msg)
        self.get_logger().debug(f"Velocità motori pubblicate: {msg.data}")

    def accellera(self, velocita_sinistra, velocita_destra):
        dvelL = (velocita_sinistra - self.left_speed_old)/10
        dvelD = (velocita_destra - self.right_speed_old)/10

        self.motoreL.on()
        self.motoreD.on()
        for i in range(0,10,1):
            velL=self.left_speed + i*dvelL
            self.motoreL.start(velL)
            velD=self.right_speed + i*dvelD
            self.motoreD.start(velD)
            self.get_logger().info(f"vel vecchia LEFT= {self.left_speed_old:.2f} vel vecchia destra{self.right_speed_old:.2f} nuova -> sinistra={velL:.2f}, destra={velD:.2f}")
            time.sleep(0.05)

        self.left_speed = velocita_sinistra
        self.right_speed = velocita_sinistra

            

       
    

def main(args=None):
    rclpy.init(args=args)
    try:
        nodo_controllo_motori = MotorControlNode()
        rclpy.spin(nodo_controllo_motori)
    except Exception as e:
        print(f"[ERRORE] {e}")
    finally:
        rclpy.shutdown()

if __name__ == '__main__':
    main()
