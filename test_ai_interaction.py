import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import time

class AITester(Node):
    def __init__(self):
        super().__init__('ai_tester')
        self.publisher_ = self.create_publisher(String, '/robopy/conversation_rx', 10)
        self.subscription = self.create_subscription(
            String,
            '/ai/conversation/response',
            self.listener_callback,
            10)
        self.questions = [
            "Chi sei?",
            "Qual è la tua missione?",
            "Cosa puoi fare?",
            "Come ti chiami?",
            "Parlami di Silone.",
            "Qual è la versione del tuo modello di embedding?",
            "Sai dove ti trovi?",
            "Puoi controllare le luci di casa?",
            "Leggi le ultime email.",
            "Cosa ricordi della nostra conversazione precedente?"
        ]
        self.current_q = 0
        self.responses = []

    def listener_callback(self, msg):
        self.get_logger().info(f'✅ Risposta ricevuta: {msg.data[:100]}...')
        self.responses.append((self.questions[self.current_q], msg.data))
        
        self.current_q += 1
        if self.current_q < len(self.questions):
            # Breve pausa tra le domande per non saturare il robot
            time.sleep(2)
            self.ask_next()
        else:
            self.report()
            rclpy.shutdown()

    def ask_next(self):
        question = self.questions[self.current_q]
        self.get_logger().info(f'🚀 Inviando domanda {self.current_q + 1}/10: {question}')
        msg = String()
        msg.data = question
        self.publisher_.publish(msg)

    def report(self):
        self.get_logger().info("\n" + "="*50 + "\nREPORT TEST INTERAZIONE AI\n" + "="*50)
        for q, r in self.responses:
            self.get_logger().info(f"Q: {q}")
            self.get_logger().info(f"A: {r[:200]}...")
            self.get_logger().info("-" * 20)

def main(args=None):
    rclpy.init(args=args)
    tester = AITester()
    
    self_logger = rclpy.logging.get_logger('test_launcher')
    self_logger.info("Avvio test interazione in 3 secondi...")
    time.sleep(3)
    
    tester.ask_next()
    
    try:
        rclpy.spin(tester)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()
