import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray
import tty
import termios
import sys
import select

class TeleopNode(Node):
    def __init__(self):
        super().__init__('teleop_node')
        self.publisher_ = self.create_publisher(Float64MultiArray, 'bluedot_input', 10)
        self.timer = self.create_timer(0.1, self.publish_command)  # Pubblica a 10 Hz
        self.x = 0.0
        self.y = 0.0
        self.speed = 50.0  # Velocità di default

        # Configura la tastiera
        self.settings = termios.tcgetattr(sys.stdin)

        self.get_logger().info("Teleop node started")

    def getKey(self):
        tty.setraw(sys.stdin.fileno())
        rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
        if rlist:
            key = sys.stdin.read(1)
        else:
            key = ''
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.settings)
        return key

    def publish_command(self):
        msg = Float64MultiArray()
        msg.data = [self.x * self.speed, self.y * self.speed]  # Scala con velocità
        self.publisher_.publish(msg)
        self.get_logger().debug(f'Published: {msg.data}')

    def run(self):
        while rclpy.ok():
            key = self.getKey()
            if key == 'w':
                self.x, self.y = 0.0, 1.0
            elif key == 's':
                self.x, self.y = 0.0, -1.0
            elif key == 'a':
                self.x, self.y = -1.0, 0.0
            elif key == 'd':
                self.x, self.y = 1.0, 0.0
            elif key == ' ':
                self.x, self.y = 0.0, 0.0
            elif key == 'q':
                break
            elif key == '+':
                self.speed = min(100.0, self.speed + 10.0)
                self.get_logger().info(f'Velocità aumentata a: {self.speed}')
            elif key == '-':
                self.speed = max(0.0, self.speed - 10.0)
                self.get_logger().info(f'Velocità diminuita a: {self.speed}')
            else:
                self.x, self.y = 0.0, 0.0
                if key == '\x03':
                    break

            self.publish_command()
            rclpy.spin_once(self)

        self.x, self.y = 0.0, 0.0
        self.publish_command()
        self.destroy_node()
        rclpy.shutdown()

def main(args=None):
    rclpy.init(args=args)
    teleop_node = TeleopNode()
    teleop_node.run()

if __name__ == '__main__':
    main()