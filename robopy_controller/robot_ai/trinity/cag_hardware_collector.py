import os
import time
import socket
from typing import Dict, Any

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from robot_ai.utils import get_logger

class HardwareStateCollector:
    """
    Collects hardware state information for the CAG module.
    """
    
    def __init__(self):
        self.logger = get_logger("HardwareStateCollector")
        # Battery mocked state
        self.battery_voltage = 12.0
        self.battery_percentage = 100.0

    def update_battery(self, voltage: float, percentage: float):
        self.battery_voltage = voltage
        self.battery_percentage = percentage
        
    def _get_cpu_temp(self) -> float:
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                return float(f.read().strip()) / 1000.0
        except Exception:
            return 0.0

    def get_hardware_summary(self) -> Dict[str, Any]:
        """Collects the current hardware state."""
        summary = {
            "timestamp": time.time(),
            "cpu_temp": self._get_cpu_temp(),
            "battery": {
                "voltage": self.battery_voltage,
                "percentage": self.battery_percentage
            }
        }
        
        if HAS_PSUTIL:
            summary["cpu_load"] = psutil.cpu_percent(interval=None, percpu=True)
            mem = psutil.virtual_memory()
            summary["ram"] = {
                "total": mem.total,
                "used": mem.used,
                "free": mem.available,
                "percent": mem.percent
            }
            disk = psutil.disk_usage('/')
            summary["disk"] = {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent
            }
            net_io = psutil.net_io_counters()
            summary["network"] = {
                "bytes_sent": net_io.bytes_sent,
                "bytes_recv": net_io.bytes_recv
            }
        else:
            summary["cpu_load"] = []
            summary["ram"] = {}
            summary["disk"] = {}
            summary["network"] = {}
            
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            summary["network"]["ip"] = s.getsockname()[0]
            summary["network"]["reachable"] = True
            s.close()
        except Exception:
            summary["network"]["ip"] = "unknown"
            summary["network"]["reachable"] = False

        return summary

    def to_text(self) -> str:
        """Returns a compact string representation of the hardware state."""
        summary = self.get_hardware_summary()
        cpu_load_str = ",".join(f"{x:.1f}%" for x in summary.get("cpu_load", []))
        ram_percent = summary.get("ram", {}).get("percent", "N/A")
        temp = summary.get("cpu_temp", 0.0)
        batt = summary.get("battery", {}).get("percentage", 100.0)
        reachable = "Up" if summary.get("network", {}).get("reachable") else "Down"
        
        return f"[HW] CPU: [{cpu_load_str}] Temp: {temp:.1f}C, RAM: {ram_percent}%, Batt: {batt:.1f}%, Net: {reachable}"
