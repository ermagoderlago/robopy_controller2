import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import time

class AIMasterTest(Node):
    def __init__(self):
        super().__init__('ai_master_test')
        self.publisher = self.create_publisher(String, '/ai/input/text', 10)
        self.subscription = self.create_subscription(
            String,
            '/ai/conversation/response',
            self.listener_callback,
            10)
        self.responses = []
        self.questions = [
            "Chi sei e qual è il tuo scopo?",
            "Qual è l'ultima versione del modello di embedding che stai usando?",
            "Puoi dirmi se il tuo sistema RAG è stato resettato?",
            "Quali sono le tue capacità principali?",
            "Come gestisci le memorie a lungo termine?",
            "Cosa sai della famiglia di robot Marcus?",
            "Puoi controllare i dispositivi di Home Assistant?",
            "Cosa sogni durante la notte?",
            "Qual è la tua configurazione hardware attuale?",
            "Salutami con un proverbio italiano."
        ]
        self.current_q = 0
        self.last_sent_time = 0
        self.timeout = 20.0 # 20 seconds per question

    def listener_callback(self, msg):
        print(f"\n--- RISPOSTA AI ({self.current_q+1}/10) ---\n{msg.data}\n")
        self.responses.append(msg.data)
        self.current_q += 1
        if self.current_q < len(self.questions):
            self.send_question()
        else:
            print("--- TEST COMPLETATO ---")
            rclpy.shutdown()

    def send_question(self):
        q = self.questions[self.current_q]
        msg = String()
        msg.data = q
        print(f"\n>>> DOMANDA ({self.current_q+1}/10): {q}")
        self.publisher.publish(msg)
        self.last_sent_time = time.time()

def main():
    rclpy.init()
    node = AIMasterTest()
    print("Avvio test Marcus AI...")
    node.send_question()
    
    while rclpy.ok():
        rclpy.spin_once(node, timeout_sec=0.1)
        if time.time() - node.last_sent_time > node.timeout and node.current_q < len(node.questions):
             print("Timeout risposta, invio prossima domanda...")
             node.current_q += 1
             if node.current_q < len(node.questions):
                 node.send_question()
             else:
                 break
    
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
