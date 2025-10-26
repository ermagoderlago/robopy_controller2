#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from diagnostic_msgs.msg import DiagnosticStatus, KeyValue
import psutil
import os
import time
import platform

class PerformanceMonitor(Node):
    def __init__(self):
        super().__init__('performance_monitor')
        self.publisher = self.create_publisher(DiagnosticStatus, '/system/performance', 10)
        self.timer = self.create_timer(5.0, self.update_performance)

        # Inizializza metriche
        self.cpu_usage = 0.0
        self.mem_usage = 0.0
        self.cpu_temp = 0.0
        self.last_time = time.time()
        self.last_cpu_times = psutil.cpu_times()

    def get_cpu_temperature(self):
        # Prova più strategie per ottenere la temperatura
        # 1) psutil.sensors_temperatures()
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                # prendi la prima voce numerica utile
                for name, entries in temps.items():
                    for e in entries:
                        if e.current is not None:
                            return float(e.current)
        except Exception:
            pass

        # 2) file standard Linux
        try:
            path = '/sys/class/thermal/thermal_zone0/temp'
            if os.path.exists(path):
                with open(path, 'r') as f:
                    t = float(f.read().strip()) / 1000.0
                    return t
        except Exception:
            pass

        # 3) fallback: 0.0
        self.get_logger().warning("Unable to read CPU temperature (no sensor found)")
        return 0.0

    def calculate_cpu_usage(self):
        current_time = time.time()
        current_cpu_times = psutil.cpu_times()

        time_delta = current_time - self.last_time
        if time_delta <= 0:
            return 0.0

        user_delta = current_cpu_times.user - self.last_cpu_times.user
        system_delta = current_cpu_times.system - self.last_cpu_times.system

        cpu_percent = ((user_delta + system_delta) / time_delta) * 100.0

        self.last_time = current_time
        self.last_cpu_times = current_cpu_times

        return min(100.0, max(0.0, cpu_percent))

    def update_performance(self):
        msg = DiagnosticStatus()
        msg.name = "system_monitor"
        msg.level = DiagnosticStatus.OK
        msg.message = "System Performance Metrics"

        # Calcola utilizzo CPU
        self.cpu_usage = self.calculate_cpu_usage()

        # Ottieni utilizzo memoria
        try:
            mem = psutil.virtual_memory()
            self.mem_usage = float(mem.percent)
        except Exception:
            self.mem_usage = 0.0
            self.get_logger().warning("Unable to read memory usage via psutil")

        # Ottieni temperatura CPU (robusta)
        self.cpu_temp = float(self.get_cpu_temperature())

        # Aggiungi valori (chiavi pulite, senza spazi finali)
        msg.values.append(self.create_kv("CPU Usage", f"{self.cpu_usage:.1f}"))
        msg.values.append(self.create_kv("Memory Usage", f"{self.mem_usage:.1f}"))
        msg.values.append(self.create_kv("CPU Temperature", f"{self.cpu_temp:.1f}"))

        # Device model: prova /proc/device-tree/model oppure platform.platform()
        model = "Unknown"
        try:
            dt_path = '/proc/device-tree/model'
            if os.path.exists(dt_path):
                with open(dt_path, 'r') as f:
                    model = f.read().strip()
            else:
                model = platform.platform()
        except Exception:
            model = platform.platform()

        msg.values.append(self.create_kv("Device Model", model))

        # Pubblica il messaggio
        self.publisher.publish(msg)
        # Log sintetico (throttle manuale se vuoi)
        self.get_logger().info(f"Published perf: CPU {self.cpu_usage:.1f}%, MEM {self.mem_usage:.1f}%, T {self.cpu_temp:.1f}°C, Model: {model}")

    def create_kv(self, key, value):
        kv = KeyValue()
        kv.key = key
        kv.value = value
        return kv

def main(args=None):
    rclpy.init(args=args)
    node = PerformanceMonitor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
