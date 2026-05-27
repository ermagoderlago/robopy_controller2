"""
Robot AI Utilities - System Inspector
======================================
Inspects active ROS 2 nodes, topics, and system resources.
Contrasts actual active topology with the expected launch configuration.
Generates an ECO-compliant markdown report of the robot's health.

Version: 01.00.00 (ECO00003)
"""

import os
import sys
import re
import json
import logging
import subprocess
from typing import List, Dict, Any, Tuple
from datetime import datetime

# Try loading rclpy
try:
    import rclpy
    from rclpy.node import Node as ROSNode
    RCLPY_AVAILABLE = True
except ImportError:
    RCLPY_AVAILABLE = False

logger = logging.getLogger("system_inspector")

class SystemInspector:
    """Introspective tool for mapping and validating ROS 2 topology and system metrics."""

    def __init__(self, workspace_root: Optional[str] = None):
        self.home_dir = os.path.expanduser("~")
        if workspace_root:
            self.workspace_root = workspace_root
        else:
            # Try to resolve based on common locations
            self.workspace_root = os.path.join(self.home_dir, "robopy", "robopi_controller", "robopy_controller_host")
            if not os.path.exists(self.workspace_root):
                self.workspace_root = os.path.join(self.home_dir, "OneDrive - BRUGOLA OEB INDUSTRIALE SPA", "Documents", "robopy", "antigravity")
                if not os.path.exists(self.workspace_root):
                    self.workspace_root = os.getcwd()

        self.launch_file = os.path.join(self.workspace_root, "launch", "fast_flow_launch.py")
        self.report_path = os.path.join(self.home_dir, "robopy", "logs", "robot_topology.md")
        os.makedirs(os.path.dirname(self.report_path), exist_ok=True)

    def _get_expected_nodes(self) -> List[str]:
        """Parses fast_flow_launch.py using regex to extract expected ROS 2 nodes."""
        expected = [
            "robot_state_publisher",
            "base_to_camera_tf",
            "base_to_imu_tf",
            "base_to_ultrasonic_tf",
            "fast_flow_vo",
            "madgwick_filter",
            "rtabmap",
            "smart_buildhat_driver",
            "foxglove_bridge",
            "audio_capture",
            "robot_ai_orchestrator",
            "homeassistant_node",
            "servo_coda_node",
            "foxglove_nav2_bridge",
            "wake_word_sentinel",
            "ultrasonic_sensor",
            "respeaker_interface_node"
        ]
        
        # Adding Nav2 standard nodes expected if nav2 is enabled
        expected_nav2 = [
            "controller_server",
            "planner_server",
            "behavior_server",
            "bt_navigator",
            "global_costmap/global_costmap",
            "local_costmap/local_costmap"
        ]
        
        if os.path.exists(self.launch_file):
            try:
                with open(self.launch_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                # Dynamically extract 'name=' parameter from Node definitions
                matches = re.findall(r"Node\s*\([^)]*name\s*=\s*['\"]([^'\"]+)['\"]", content, re.DOTALL)
                if matches:
                    # Filter and merge discovered nodes
                    for m in matches:
                        if m not in expected:
                            expected.append(m)
            except Exception as e:
                logger.warning(f"Error parsing launch file {self.launch_file}: {e}")

        # Combine with Nav2 if expected
        expected.extend(expected_nav2)
        return list(set(expected))

    def _query_ros2_cli(self, command: str) -> List[str]:
        """Fallback to executing ROS 2 CLI commands via subprocess."""
        try:
            # We source the venv and install workspace inside a bash subprocess
            setup_cmd = (
                "source ~/ros2_venv/bin/activate 2>/dev/null; "
                "source ~/ros2_jazzy/install/setup.bash 2>/dev/null; "
                "source ~/robopy/robopy/robopi_controller/robopy_controller_host/install/setup.bash 2>/dev/null; "
                f"export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml 2>/dev/null; "
                f"ros2 {command}"
            )
            result = subprocess.run(
                ["bash", "-c", setup_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5.0
            )
            if result.returncode == 0:
                lines = [line.strip() for line in result.stdout.split("\n") if line.strip()]
                return lines
        except Exception as e:
            logger.debug(f"CLI query error for 'ros2 {command}': {e}")
        return []

    def _get_active_nodes_and_topics(self) -> Tuple[List[str], List[str]]:
        """Retrieves currently running nodes and active topics via CLI or rclpy."""
        nodes = []
        topics = []

        # 1. Fallback to CLI since it includes environment sourcing
        cli_nodes = self._query_ros2_cli("node list")
        if cli_nodes:
            nodes = [n.lstrip("/") for n in cli_nodes]
        
        cli_topics = self._query_ros2_cli("topic list")
        if cli_topics:
            topics = cli_topics

        # 2. Try rclpy if CLI failed or as high fidelity validation
        if not nodes and RCLPY_AVAILABLE:
            try:
                if not rclpy.ok():
                    rclpy.init()
                temp_node = ROSNode("_inspector_temp")
                node_names_namespaces = temp_node.get_node_names_and_namespaces()
                nodes = [name for name, ns in node_names_namespaces if name != "_inspector_temp"]
                
                topic_names_types = temp_node.get_topic_names_and_types()
                topics = [name for name, types in topic_names_types]
                temp_node.destroy_node()
            except Exception as e:
                logger.debug(f"rclpy active query failed: {e}")

        return nodes, topics

    def _get_system_resources(self) -> Dict[str, Any]:
        """Fetches standard system metrics from local Linux files (Raspberry Pi compatibility)."""
        metrics = {
            "cpu_temp": "N/A",
            "ram_total": 0,
            "ram_used": 0,
            "ram_percent": 0.0,
            "cpu_usage": "N/A"
        }

        # Temperature (Raspberry Pi thermal zone 0)
        try:
            if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
                with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                    temp_raw = int(f.read().strip())
                    metrics["cpu_temp"] = f"{temp_raw / 1000.0:.1f} °C"
        except Exception:
            pass

        # Memory (from /proc/meminfo)
        try:
            if os.path.exists("/proc/meminfo"):
                meminfo = {}
                with open("/proc/proc/meminfo" if not os.path.exists("/proc/meminfo") else "/proc/meminfo", "r") as f:
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            meminfo[parts[0].strip()] = int(parts[1].replace("kB", "").strip())
                
                total = meminfo.get("MemTotal", 0) // 1024  # Convert to MB
                free = meminfo.get("MemFree", 0) // 1024
                buffers = meminfo.get("Buffers", 0) // 1024
                cached = meminfo.get("Cached", 0) // 1024
                
                used = total - free - buffers - cached
                metrics["ram_total"] = total
                metrics["ram_used"] = used
                metrics["ram_percent"] = (used / total) * 100 if total > 0 else 0.0
        except Exception:
            pass

        # CPU load average (from /proc/loadavg)
        try:
            if os.path.exists("/proc/loadavg"):
                with open("/proc/loadavg", "r") as f:
                    load = f.read().split()
                    metrics["cpu_usage"] = f"Load 1m: {load[0]}, 5m: {load[1]}"
        except Exception:
            pass

        return metrics

    def inspect(self) -> Dict[str, Any]:
        """Runs full system introspection, compares with expectations, and updates log files."""
        expected_nodes = self._get_expected_nodes()
        active_nodes, active_topics = self._get_active_nodes_and_topics()
        resources = self._get_system_resources()

        # Group nodes
        healthy_nodes = []
        down_nodes = []
        zombie_nodes = []

        for node in expected_nodes:
            if node in active_nodes:
                healthy_nodes.append(node)
            else:
                down_nodes.append(node)

        for node in active_nodes:
            if node not in expected_nodes:
                zombie_nodes.append(node)

        # Health status logic
        if len(down_nodes) == 0:
            status = "HEALTHY"
        elif len(down_nodes) < 4:
            status = "DEGRADED"
        else:
            status = "CRITICAL"

        # Topic summary
        critical_topics = [
            "/scan", "/rgb/image", "/camera/depth/image_raw", 
            "/audio/audio", "/ai/conversation/response", "/ai/tts/speaking"
        ]
        active_critical_topics = [t for t in critical_topics if t in active_topics]

        report = {
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "system_resources": resources,
            "nodes": {
                "active_count": len(active_nodes),
                "expected_count": len(expected_nodes),
                "healthy": healthy_nodes,
                "down": down_nodes,
                "zombie": zombie_nodes
            },
            "topics": {
                "total_count": len(active_topics),
                "active_critical": active_critical_topics,
                "missing_critical": [t for t in critical_topics if t not in active_topics]
            }
        }

        # Write markdown report
        self._write_markdown_report(report)
        return report

    def _write_markdown_report(self, report: Dict[str, Any]):
        """Generates the premium robot_topology.md file for RAG memory ingestion."""
        res = report["system_resources"]
        nodes = report["nodes"]
        topics = report["topics"]
        
        status_colors = {
            "HEALTHY": "🟢 HEALTHY",
            "DEGRADED": "🟡 DEGRADED",
            "CRITICAL": "🔴 CRITICAL"
        }

        md = f"""# ROS 2 System Topology & Health Report
**Generated At:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**Overall Status:** {status_colors.get(report['status'], report['status'])}

---

## 1. System Resource Utilization
*   **CPU Temperature:** {res['cpu_temp']}
*   **CPU Load Average:** {res['cpu_usage']}
*   **RAM Used:** {res['ram_used']} MB / {res['ram_total']} MB ({res['ram_percent']:.1f}%)

---

## 2. ROS 2 Node Analysis
*   **Active Nodes:** {nodes['active_count']}
*   **Expected Nodes (Launch):** {nodes['expected_count']}

### Node Status Matrix

| Node Name | Class | Status | Note |
| :--- | :--- | :--- | :--- |
"""
        for node in sorted(nodes["healthy"]):
            md += f"| `{node}` | Expected | 🟢 ACTIVE | Executing correctly |\n"
        for node in sorted(nodes["down"]):
            md += f"| `{node}` | Expected | 🔴 DOWN | Offline or failed to initialize |\n"
        for node in sorted(nodes["zombie"]):
            md += f"| `{node}` | Unexpected | 🟡 ZOMBIE | Running but not declared in launch |\n"

        md += """
---

## 3. Topic & Pipeline Analysis
*   **Total Active Topics:** {topics['total_count']}

### Critical Pipeline Feeds

| Topic Name | Expected Pipeline | Status |
| :--- | :--- | :--- |
"""
        for t in topics["active_critical"]:
            md += f"| `{t}` | Core Feed | 🟢 ACTIVE |\n"
        for t in topics["missing_critical"]:
            md += f"| `{t}` | Core Feed | 🔴 DOWN |\n"

        md += """
---
*N.B. Questo report viene integrato nel cervello semantico di Marcus ad ogni sessione del Sogno.*
"""
        try:
            with open(self.report_path, "w", encoding="utf-8") as f:
                f.write(md)
            logger.info(f"System Inspector report written to {self.report_path}")
        except Exception as e:
            logger.error(f"Failed to write markdown report: {e}")

def inspect_system(workspace_root: Optional[str] = None) -> Dict[str, Any]:
    """Helper function to execute system inspection easily."""
    inspector = SystemInspector(workspace_root)
    return inspector.inspect()

if __name__ == "__main__":
    res = inspect_system()
    print(json.dumps(res, indent=2))
