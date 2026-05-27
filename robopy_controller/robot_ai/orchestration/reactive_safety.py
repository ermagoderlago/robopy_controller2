import threading
import asyncio
from geometry_msgs.msg import Twist
from robot_ai.utils import get_logger

class ReactiveSafety:
    def __init__(self, cmd_vel_pub):
        self._cmd_vel_pub = cmd_vel_pub
        self._lock = threading.Lock()
        self._current_twist = Twist()
        self._move_task = None
        self._logger = get_logger("reactive_safety")

    def set_twist(self, twist: Twist):
        with self._lock:
            self._current_twist = twist

    def get_twist(self) -> Twist:
        with self._lock:
            return self._current_twist

    def emergency_stop(self):
        self.set_twist(Twist())
        self._cmd_vel_pub.publish(Twist())
        self._cancel_move_task()
        self._logger.warning("Emergency stop triggered.")

    def _cancel_move_task(self):
        if self._move_task and not self._move_task.done():
            self._move_task.cancel()

    async def move_relative(self, direction: str, speed: float = 0.3, duration: float = 1.0):
        twist = Twist()
        dir_low = direction.lower().strip()
        angular_speed = abs(speed) * 2.0
        if dir_low in ("avanti", "forward"):
            twist.linear.x = abs(speed)
        elif dir_low in ("indietro", "backward"):
            twist.linear.x = -abs(speed)
        elif dir_low in ("sinistra", "left"):
            twist.angular.z = angular_speed
        elif dir_low in ("destra", "right"):
            twist.angular.z = -angular_speed
        else:
            self._logger.warning(f"Unknown move direction: {direction}")
            return

        self._cancel_move_task()
        self.set_twist(twist)
        # programma lo stop
        self._move_task = asyncio.create_task(self._stop_after(duration))
        self._logger.info(f"Moving {direction} for {duration} seconds")

    async def _stop_after(self, duration):
        try:
            await asyncio.sleep(duration)
            self.emergency_stop()
        except asyncio.CancelledError:
            pass
