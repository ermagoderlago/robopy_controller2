import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import time

def main():
    rclpy.init()
    node = rclpy.create_node('obs_tester')
    pub = node.create_publisher(String, '/robopy/conversation_rx', 10)
    
    print("Inizio invio di 201 messaggi per triggerare dump metriche...")
    print("Obiettivo: il log [INFO] di robot_ai deve apparire dopo 200 operazioni.")
    
    for i in range(201):
        msg = String()
        # Usiamo messaggi diversi per saltare la deduplica Hash!
        msg.data = f"Spam observability {i} {time.time()}" 
        pub.publish(msg)
        time.sleep(0.02)
        if i % 50 == 0:
            print(f"Progresso: {i}/201...")
            
    print("Finito. Controlla i log del robot per [MemoryStore Obs].")
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
