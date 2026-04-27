import rclpy
import signal
import sys
import asyncio
from robot_ai.orchestration.orchestrator import AIOrchestrator

def main(args=None):
    rclpy.init(args=args)
    
    # AIOrchestrator is a Node and manages its own asyncio loop/thread
    node = AIOrchestrator()

    def signal_handler(sig, frame):
        node.get_logger().info("Shutting down robot_ai_node...")
        try:
            # AIOrchestrator has a shutdown() method that cleans up its resources
            if hasattr(node, 'shutdown'):
                # We need to run the shutdown coroutine in the node's loop
                future = asyncio.run_coroutine_threadsafe(node.shutdown(), node._loop)
                future.result(timeout=5.0)
        except Exception as e:
            print(f"Error during node graceful shutdown: {e}")
        finally:
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
            sys.exit(0)
            
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        if node:
            node.get_logger().error(f"Fatal crash in robot_ai_node.py spinning: {e}")
    finally:
        if node:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()