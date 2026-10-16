#!/usr/bin/env python3
"""
Marcus Supervisors Daemon (Phase 4 Optimization)
=================================================
Consolidates 6 lightweight background supervisor nodes into a single process
and a single CycloneDDS participant, eliminating multi-process Python interpreter
overhead (~250-300 MB RAM) and redundant DDS participant event handling (~30-40% CPU).

Managed Nodes:
- BatteryManagerNode (BMS, Anti-Sag, Safety Poweroff)
- RobotHealthSupervisor (Hardware Watchdog & Liveness)
- SensorStandbyManager (Smart Standby & Sensor Power-Save)
- ResourceGovernorNode (Dynamic Resource Governor F4)
- CloudWatchdogNode (Cloud Latency & Offline Fallback)
- FoxgloveNav2Bridge (Foxglove Studio 2D Goal Bridge)
"""

import sys
import rclpy
from rclpy.executors import SingleThreadedExecutor

from robopy_controller.nodes.battery_manager_node import BatteryManagerNode
from robopy_controller.nodes.robot_health_supervisor import RobotHealthSupervisor
from robopy_controller.nodes.sensor_standby_manager import SensorStandbyManager
from robopy_controller.nodes.resource_governor_node import ResourceGovernorNode
from robopy_controller.nodes.cloud_watchdog_node import CloudWatchdogNode
from robopy_controller.nodes.foxglove_nav2_bridge import FoxgloveNav2Bridge


def main(args=None):
    rclpy.init(args=args)
    executor = SingleThreadedExecutor()

    nodes = []
    try:
        battery_mgr = BatteryManagerNode()
        nodes.append(battery_mgr)
        executor.add_node(battery_mgr)

        health_sup = RobotHealthSupervisor()
        nodes.append(health_sup)
        executor.add_node(health_sup)

        standby_mgr = SensorStandbyManager()
        nodes.append(standby_mgr)
        executor.add_node(standby_mgr)

        governor = ResourceGovernorNode()
        nodes.append(governor)
        executor.add_node(governor)

        cloud_wd = CloudWatchdogNode()
        nodes.append(cloud_wd)
        executor.add_node(cloud_wd)

        foxglove_bridge = FoxgloveNav2Bridge()
        nodes.append(foxglove_bridge)
        executor.add_node(foxglove_bridge)

        print("🚀 [MarcusSupervisorsDaemon] 6 Supervisor Nodes successfully instantiated in single process!")
        executor.spin()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"❌ [MarcusSupervisorsDaemon] Fatal error in executor: {e}", file=sys.stderr)
    finally:
        for node in nodes:
            try:
                node.destroy_node()
            except Exception:
                pass
        rclpy.shutdown()


if __name__ == '__main__':
    main()
