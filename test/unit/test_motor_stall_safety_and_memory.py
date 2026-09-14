import sys
import os
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio
import time

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('robopy_controller'))

class DummyNode:
    def __init__(self, node_name="", *args, **kwargs):
        self.node_name = node_name
    def declare_parameter(self, name, default=None):
        pass
    def get_parameter(self, name):
        m = MagicMock()
        defaults = {
            'chroma_persist_dir': '/tmp/chroma',
            'collection_name': 'robot_memories',
            'fear_threshold': 0.30,
            'enable_hijack': True,
            'serial_port': '/dev/null',
            'baud_rate': 115200,
            'wheel_radius': 0.0335,
            'wheel_separation': 0.285,
            'rotational_wheel_separation': 0.285,
            'ticks_per_rev': 657,
            'invert_left_motor': False,
            'invert_right_motor': False,
            'invert_left_encoder': False,
            'invert_right_encoder': False,
            'encoder_dead_zone': 0,
            'publish_tf': False,
            'odom_topic': '/odom_wheel',
            'feedforward_nominal_voltage': 11.10,
            'feedforward_min_scale': 0.70,
            'feedforward_max_scale': 1.40,
            'enable_voltage_feedforward': False,
            'max_linear_speed': 0.40,
            'motor_min_duty_cycle': 0.18,
            'raw_battery_topic': '/battery/raw',
            'esp32_adc_scale_factor': 2880.95,
            'enable_esp32_pid': True,
            'esp32_pid_kp': 3.20,
            'esp32_pid_ki': 0.22,
            'esp32_pid_kd': 0.04,
        }
        val = defaults.get(name, 0.0)
        m.get_parameter_value().string_value = str(val)
        m.get_parameter_value().double_value = float(val) if isinstance(val, (int, float)) else 0.0
        m.get_parameter_value().bool_value = bool(val)
        m.value = val
        return m
    def create_publisher(self, *args, **kwargs):
        return MagicMock()
    def create_subscription(self, *args, **kwargs):
        return MagicMock()
    def create_timer(self, *args, **kwargs):
        return MagicMock()
    def create_client(self, *args, **kwargs):
        return MagicMock()
    def get_logger(self):
        return MagicMock()
    def get_clock(self):
        clock = MagicMock()
        clock.now().nanoseconds = int(time.time() * 1e9)
        return clock
    def add_on_set_parameters_callback(self, *args, **kwargs):
        pass

# Ensure ROS 2 & chromadb modules are mocked if running outside ROS 2 Linux environment
for mod in [
    'rclpy', 'rclpy.node', 'rclpy.callback_groups', 'rclpy.action', 'rclpy.executors',
    'rclpy.time', 'rclpy.duration', 'rclpy.qos',
    'diagnostic_msgs', 'diagnostic_msgs.msg',
    'geometry_msgs', 'geometry_msgs.msg',
    'sensor_msgs', 'sensor_msgs.msg',
    'std_msgs', 'std_msgs.msg',
    'vision_msgs', 'vision_msgs.msg',
    'nav2_msgs', 'nav2_msgs.action',
    'nav_msgs', 'nav_msgs.msg',
    'rcl_interfaces', 'rcl_interfaces.srv', 'rcl_interfaces.msg',
    'action_msgs', 'action_msgs.srv', 'action_msgs.msg',
    'example_interfaces', 'example_interfaces.srv',
    'robopy_controller.msg', 'robopy_controller.srv',
    'chromadb', 'chromadb.config',
    'aiohttp', 'cv_bridge',
    'sensor_msgs_py', 'sensor_msgs_py.point_cloud2',
    'visualization_msgs', 'visualization_msgs.msg',
    'tf2_ros', 'tf_transformations',
    'aioimaplib', 'aiosmtplib', 'spotipy'
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

sys.modules['rclpy.node'].Node = DummyNode

# Define lightweight message classes for testing
class DummyDiagnosticStatus:
    OK = 0
    WARN = 1
    ERROR = 2
    STALE = 3
    def __init__(self, name="", level=0, message=""):
        self.name = name
        self.level = level
        self.message = message
        self.values = []

class DummyDiagnosticArray:
    def __init__(self):
        self.status = []

sys.modules['diagnostic_msgs.msg'].DiagnosticStatus = DummyDiagnosticStatus
sys.modules['diagnostic_msgs.msg'].DiagnosticArray = DummyDiagnosticArray

from robopy_controller.robot_ai.core.event_bus import EventType
from robopy_controller.robot_ai.rag.memory_store import MemoryType


class TestMotorStallSafetyAndMemory(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_orchestrator_motor_stall_handling(self):
        """Test that AIOrchestrator cancels Nav2, speaks TTS, notifies EventBus, and stores memory on motor stall."""
        from robopy_controller.robot_ai.orchestration.orchestrator import AIOrchestrator

        orchestrator = AIOrchestrator.__new__(AIOrchestrator)
        orchestrator._loop = self.loop
        orchestrator._shutdown_flag = False
        orchestrator.get_logger = MagicMock()
        orchestrator.reactive_safety = MagicMock()
        orchestrator.tts_service = MagicMock()
        orchestrator.tts_service.speak = AsyncMock()
        orchestrator.event_bus = MagicMock()
        orchestrator.memory_manager = MagicMock()
        orchestrator.memory_manager.store_background = AsyncMock()
        
        orchestrator.nav_client = MagicMock()
        orchestrator.nav_client.is_navigating = True
        orchestrator.nav_client.cancel_navigation = AsyncMock()

        # Create DiagnosticArray with motor_stall error
        diag_msg = DummyDiagnosticArray()
        status = DummyDiagnosticStatus(name="motor_stall", level=2, message="Stallo meccanico (ruote ferme)")
        diag_msg.status.append(status)

        # Invoke callback
        orchestrator._diagnostics_callback(diag_msg)

        # 1. Emergency stop called
        orchestrator.reactive_safety.emergency_stop.assert_called_once()

        # 2. Nav2 cancellation scheduled and executed
        self.loop.run_until_complete(asyncio.sleep(0.05))
        orchestrator.nav_client.cancel_navigation.assert_called_once()

        # 3. TTS speech scheduled
        orchestrator.tts_service.speak.assert_called_once()
        spoken_text = orchestrator.tts_service.speak.call_args[0][0]
        self.assertIn("Rilevato blocco o ostacolo nei motori", spoken_text)

        # 4. EventBus publication
        orchestrator.event_bus.publish.assert_called_once_with(
            EventType.DIAGNOSTIC_UPDATE,
            {"motor_stall": True, "error_message": "Stallo meccanico (ruote ferme)"}
        )

        # 5. MemoryManager store_background scheduled
        orchestrator.memory_manager.store_background.assert_called_once()
        mem_user, mem_robot, mem_type = orchestrator.memory_manager.store_background.call_args[0]
        self.assertIn("Anomalia stallo o sovraccarico", mem_user)
        self.assertEqual(mem_type, "system_event")

    def test_memory_manager_system_event_protection(self):
        """Test that MemoryManager assigns amygdala_protected and zero decay to SYSTEM_EVENT."""
        from robopy_controller.robot_ai.orchestration.memory_manager import MemoryManager

        mock_store = MagicMock()
        mock_embedding = MagicMock()
        mock_embedding.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])

        mm = MemoryManager(mock_store, mock_embedding)

        async def _run():
            mm.start()
            await mm.store_background("Allarme stallo motori", "Motori arrestati.", "system_event")
            await asyncio.sleep(0.05)
            await mm.shutdown()

        self.loop.run_until_complete(_run())

        mock_store.add.assert_called_once()
        saved_memory = mock_store.add.call_args[0][0]
        self.assertEqual(saved_memory.memory_type, MemoryType.SYSTEM_EVENT)
        self.assertEqual(saved_memory.importance, 1.0)
        self.assertEqual(saved_memory.metadata.get("amygdala_protected"), "true")
        self.assertEqual(saved_memory.metadata.get("synaptic_strength"), 100.0)
        self.assertEqual(saved_memory.metadata.get("lambda_decay"), 0.0)

    def test_cognitive_amygdala_motor_stall_and_rearm(self):
        """Test that CognitiveAmygdalaNode triggers hijack on motor stall and rearms on nominal status."""
        from robopy_controller.robot_ai.cognitive.cognitive_amygdala import CognitiveAmygdalaNode

        amygdala = CognitiveAmygdalaNode.__new__(CognitiveAmygdalaNode)
        import threading
        amygdala._lock = threading.RLock()
        amygdala.trigger_amigdala = False
        amygdala.amygdala_state = "CALM"
        amygdala.get_logger = MagicMock()
        amygdala.cmd_vel_pub = MagicMock()
        amygdala.interrupt_pub = MagicMock()
        amygdala.enable_hijack = True
        amygdala.collection = MagicMock()
        amygdala.nav_action_client = MagicMock()
        amygdala.nav_action_client._cancel_goal_service_client.service_is_ready.return_value = True

        # Trigger motor_stall ERROR
        diag_err = DummyDiagnosticArray()
        status_err = DummyDiagnosticStatus(name="motor_stall", level=2, message="Stallo meccanico (ruote ferme)")
        diag_err.status.append(status_err)

        amygdala._diagnostics_callback(diag_err)

        self.assertTrue(amygdala.trigger_amigdala)
        self.assertEqual(amygdala.amygdala_state, "HIJACK")
        self.assertEqual(amygdala._last_hijack_event, "MOTOR_STALL")
        self.assertTrue(amygdala.cmd_vel_pub.publish.called)
        amygdala.collection.add.assert_called_once()
        added_doc = amygdala.collection.add.call_args[1]["documents"][0]
        self.assertIn("MOTOR_STALL", added_doc)

        # Trigger motor_stall OK -> should re-arm to CALM
        diag_ok = DummyDiagnosticArray()
        status_ok = DummyDiagnosticStatus(name="motor_stall", level=0, message="Motori OK")
        diag_ok.status.append(status_ok)

        amygdala._diagnostics_callback(diag_ok)
        self.assertFalse(amygdala.trigger_amigdala)
        self.assertEqual(amygdala.amygdala_state, "CALM")


if __name__ == '__main__':
    unittest.main()
