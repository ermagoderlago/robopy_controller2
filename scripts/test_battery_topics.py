import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String, Float32, Bool
import time

try:
    from nav2_msgs.msg import SpeedLimit
    HAS_NAV2 = True
except ImportError:
    HAS_NAV2 = False


class BatteryTopicTester(Node):
    def __init__(self):
        super().__init__('battery_topic_tester')
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        qos_rel = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        self.results = {}

        self.create_subscription(BatteryState, '/battery_state', self.cb_batt, qos_rel)
        self.create_subscription(BatteryState, '/battery_state', self.cb_batt, qos)
        self.create_subscription(String, '/foxglove/power_status', self.cb_status, qos_rel)
        self.create_subscription(Float32, '/foxglove/battery_pct', self.cb_pct, qos_rel)
        self.create_subscription(Bool, '/robot/docking/trigger', self.cb_dock, qos_rel)
        self.create_subscription(Float32, '/motor/battery_voltage', self.cb_volt, qos_rel)
        self.create_subscription(BatteryState, '/battery/raw', self.cb_raw, qos)
        self.create_subscription(Float32, '/battery/raw_voltage', self.cb_raw_v, qos)

        if HAS_NAV2:
            self.create_subscription(SpeedLimit, '/speed_limit', self.cb_speed, qos_rel)
        else:
            self.create_subscription(Float32, '/speed_limit', self.cb_speed, qos_rel)

    def cb_batt(self, msg: BatteryState):
        self.results['/battery_state'] = {
            'voltage': round(msg.voltage, 2),
            'percentage': round(msg.percentage, 2),
            'power_supply_status': 'CHARGING' if msg.power_supply_status == 1 else ('DISCHARGING' if msg.power_supply_status == 2 else str(msg.power_supply_status))
        }

    def cb_status(self, msg: String):
        self.results['/foxglove/power_status'] = msg.data

    def cb_pct(self, msg: Float32):
        self.results['/foxglove/battery_pct'] = round(msg.data, 1)

    def cb_dock(self, msg: Bool):
        self.results['/robot/docking/trigger'] = msg.data

    def cb_volt(self, msg: Float32):
        self.results['/motor/battery_voltage'] = round(msg.data, 2)

    def cb_raw(self, msg: BatteryState):
        self.results['/battery/raw'] = {'voltage': round(msg.voltage, 2)}

    def cb_raw_v(self, msg: Float32):
        self.results['/battery/raw_voltage'] = round(msg.data, 2)

    def cb_speed(self, msg):
        if hasattr(msg, 'speed_limit'):
            self.results['/speed_limit'] = round(msg.speed_limit, 1)
        else:
            self.results['/speed_limit'] = round(msg.data, 1)


def main():
    rclpy.init()
    tester = BatteryTopicTester()
    print("⏳ Ascolto dei topic della batteria per 4 secondi...")
    t0 = time.time()
    while time.time() - t0 < 4.0:
        rclpy.spin_once(tester, timeout_sec=0.1)

    print("\n" + "="*50)
    print(" 📊 RISULTATI TELEMETRIA BATTERIA MARCUS")
    print("="*50)
    for topic, val in sorted(tester.results.items()):
        print(f" • {topic:26s} : {val}")
    print("="*50 + "\n")

    tester.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
