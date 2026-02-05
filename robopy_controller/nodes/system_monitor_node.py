#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
import psutil
import os
import time
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue

class SystemMonitor(Node):
    def __init__(self):
        super().__init__('system_monitor')
        
        self.declare_parameter('check_interval', 5.0)
        self.declare_parameter('cpu_threshold', 85.0)
        self.declare_parameter('memory_threshold', 85.0)
        self.declare_parameter('temperature_threshold', 75.0)
        
        self.check_interval = self.get_parameter('check_interval').value
        self.cpu_threshold = self.get_parameter('cpu_threshold').value
        self.memory_threshold = self.get_parameter('memory_threshold').value
        self.temp_threshold = self.get_parameter('temperature_threshold').value
        
        self.diag_pub = self.create_publisher(DiagnosticArray, '/diagnostics', 10)
        self.timer = self.create_timer(self.check_interval, self.check_system)
        
        self.get_logger().info("System Monitor avviato")
    
    def check_system(self):
        diag_array = DiagnosticArray()
        diag_array.header.stamp = self.get_clock().now().to_msg()
        
        # CPU Usage
        cpu_percent = psutil.cpu_percent(interval=0.5)
        cpu_status = self.create_status('CPU', 0 if cpu_percent < self.cpu_threshold else 2)
        cpu_status.message = f"CPU: {cpu_percent:.1f}%"
        cpu_status.values.append(KeyValue(key='usage_percent', value=str(cpu_percent)))
        cpu_status.values.append(KeyValue(key='threshold', value=str(self.cpu_threshold)))
        
        # Memory
        memory = psutil.virtual_memory()
        mem_percent = memory.percent
        mem_status = self.create_status('Memory', 0 if mem_percent < self.memory_threshold else 2)
        mem_status.message = f"Memory: {mem_percent:.1f}%"
        mem_status.values.append(KeyValue(key='usage_percent', value=str(mem_percent)))
        mem_status.values.append(KeyValue(key='available_mb', value=str(memory.available // (1024*1024))))
        
        # Temperature (Raspberry Pi specific)
        temp = self.get_cpu_temperature()
        temp_status = self.create_status('Temperature', 0 if temp < self.temp_threshold else 2)
        temp_status.message = f"Temp: {temp:.1f}°C"
        temp_status.values.append(KeyValue(key='temperature_c', value=str(temp)))
        
        # Disk
        disk = psutil.disk_usage('/')
        disk_status = self.create_status('Disk', 0)
        disk_status.message = f"Disk: {disk.percent:.1f}%"
        disk_status.values.append(KeyValue(key='usage_percent', value=str(disk.percent)))
        
        # Processi ROS
        ros_processes = self.count_ros_processes()
        proc_status = self.create_status('ROS Processes', 0)
        proc_status.message = f"ROS Processes: {ros_processes}"
        proc_status.values.append(KeyValue(key='count', value=str(ros_processes)))
        
        # Aggiungi tutti gli stati
        diag_array.status.extend([cpu_status, mem_status, temp_status, disk_status, proc_status])
        
        # Pubblica warning se necessario
        if cpu_percent > self.cpu_threshold:
            self.get_logger().warn(f"⚠️ CPU alta: {cpu_percent:.1f}%")
        if temp > self.temp_threshold:
            self.get_logger().warn(f"🌡️  Temperatura alta: {temp:.1f}°C")
        
        self.diag_pub.publish(diag_array)
    
    def create_status(self, name: str, level: int) -> DiagnosticStatus:
        status = DiagnosticStatus()
        status.name = f"System: {name}"
        status.level = level
        if level == 0:
            status.message = "OK"
        elif level == 1:
            status.message = "WARN"
        else:
            status.message = "ERROR"
        return status
    
    def get_cpu_temperature(self) -> float:
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = float(f.read().strip()) / 1000.0
            return temp
        except:
            return 0.0
    
    def count_ros_processes(self) -> int:
        count = 0
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                cmdline = proc.info['cmdline']
                if cmdline and any('ros' in part.lower() for part in cmdline):
                    count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return count

def main():
    rclpy.init()
    node = SystemMonitor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()