#!/usr/bin/env python3
"""
Unit Test - ReSpeaker Port Resolution & Udev Fallback (FM-VUI-024)
==================================================================
Validates:
1. Direct port existence returns requested port.
2. /dev/respeaker symlink existence is selected when configured port is absent.
3. /dev/serial/by-id/*Espressif* or *Seeed* is resolved as fallback.
4. /dev/ttyACM* is resolved as dynamic fallback (e.g. if lidar caused port shift).
5. Safe fallback to default configured port when no device nodes exist.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('robopy_controller'))


class DummyNode:
    def __init__(self, node_name, *args, **kwargs):
        self.node_name = node_name
    def declare_parameter(self, name, default):
        pass
    def get_parameter(self, name):
        m = MagicMock()
        defaults = {
            'uart_port': '/dev/respeaker',
            'uart_baud': 115200,
            'enabled': True,
        }
        val = defaults.get(name, None)
        m.get_parameter_value.return_value.string_value = val if isinstance(val, str) else ''
        m.get_parameter_value.return_value.integer_value = val if isinstance(val, int) else 0
        m.get_parameter_value.return_value.bool_value = val if isinstance(val, bool) else False
        return m
    def get_logger(self):
        logger = MagicMock()
        return logger
    def create_publisher(self, *args, **kwargs):
        return MagicMock()
    def create_subscription(self, *args, **kwargs):
        return MagicMock()
    def create_timer(self, *args, **kwargs):
        return MagicMock()
    def get_clock(self):
        c = MagicMock()
        c.now.return_value = 0.0
        return c


class TestReSpeakerPortResolver(unittest.TestCase):

    def setUp(self):
        # Mock rclpy and serial modules
        self.patchers = []
        if 'rclpy' not in sys.modules:
            m_rclpy = MagicMock()
            sys.modules['rclpy'] = m_rclpy
            self.patchers.append(('rclpy', m_rclpy))
        if 'rclpy.node' not in sys.modules:
            m_node = MagicMock()
            m_node.Node = DummyNode
            sys.modules['rclpy.node'] = m_node
            self.patchers.append(('rclpy.node', m_node))
        if 'std_msgs.msg' not in sys.modules:
            m_std = MagicMock()
            sys.modules['std_msgs.msg'] = m_std
            self.patchers.append(('std_msgs.msg', m_std))

    def _create_node(self):
        with patch('robopy_controller.nodes.respeaker_interface_node.Node', DummyNode):
            with patch('serial.Serial'):
                from robopy_controller.nodes.respeaker_interface_node import ReSpeakerInterfaceNode
                node = ReSpeakerInterfaceNode()
                return node

    def test_configured_port_exists(self):
        node = self._create_node()
        node._port = '/dev/respeaker'
        with patch('os.path.exists', side_effect=lambda p: p == '/dev/respeaker'):
            resolved = node._resolve_port()
            self.assertEqual(resolved, '/dev/respeaker')

    def test_respeaker_symlink_fallback(self):
        node = self._create_node()
        node._port = '/dev/ttyACM0'  # Old default configured
        # Suppose /dev/ttyACM0 does NOT exist, but /dev/respeaker DOES exist
        with patch('os.path.exists', side_effect=lambda p: p == '/dev/respeaker'):
            resolved = node._resolve_port()
            self.assertEqual(resolved, '/dev/respeaker')

    def test_by_id_espressif_fallback(self):
        node = self._create_node()
        node._port = '/dev/respeaker'
        espressif_dev = '/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_12345-if00'

        def mock_exists(p):
            return p == espressif_dev

        def mock_glob(pattern):
            if 'Espressif' in pattern:
                return [espressif_dev]
            return []

        with patch('os.path.exists', side_effect=mock_exists):
            with patch('glob.glob', side_effect=mock_glob):
                resolved = node._resolve_port()
                self.assertEqual(resolved, espressif_dev)

    def test_dynamic_tty_acm_fallback_when_shifted(self):
        """Simulate LiDAR insertion causing shift from ACM0 to ACM1."""
        node = self._create_node()
        node._port = '/dev/respeaker'

        def mock_exists(p):
            return p == '/dev/ttyACM1'

        def mock_glob(pattern):
            if 'ttyACM' in pattern:
                return ['/dev/ttyACM1']
            return []

        with patch('os.path.exists', side_effect=mock_exists):
            with patch('glob.glob', side_effect=mock_glob):
                resolved = node._resolve_port()
                self.assertEqual(resolved, '/dev/ttyACM1')

    def test_safe_fallback_to_port_when_none_exist(self):
        node = self._create_node()
        node._port = '/dev/respeaker'
        with patch('os.path.exists', return_value=False):
            with patch('glob.glob', return_value=[]):
                resolved = node._resolve_port()
                self.assertEqual(resolved, '/dev/respeaker')


if __name__ == '__main__':
    unittest.main()
