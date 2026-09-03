#!/usr/bin/env python3
"""
Live Connection Bridge Node
===========================
Dedicated, lightweight ROS 2 node managing the asynchronous Gemini Live WebSocket session.
Handles bi-directional streaming, tool execution delegation, and audio buffering.

Author: Marcus AI Engineering Team
Version: 01.00.00
"""

import os
import sys
import time
import asyncio
import threading
from typing import Optional, Dict, Any, List, Callable

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from std_msgs.msg import String, Bool
from robopy_controller.msg import AudioData

from robopy_controller.robot_ai.services.llm_models import (
    HAS_GENAI, genai, types, LLMResponse
)
from robopy_controller.robot_ai.services.audio_buffer_manager import AudioBufferManager
from robopy_controller.robot_ai.services.live_connection_manager import LiveConnectionManager


class LiveConnectionBridgeNode(Node):
    """
    Independent ROS 2 node for low-latency Gemini Live API streaming.
    """

    def __init__(self, api_key: str = "", tool_executor: Optional[Callable] = None):
        super().__init__('live_connection_bridge_node')
        self.get_logger().info("Inizializzazione LiveConnectionBridgeNode...")

        # Parameters
        self.declare_parameter('gemini_api_key', '')
        self.declare_parameter('live_model_name', 'gemini-2.5-flash-native-audio-latest')
        self.declare_parameter('voice_name', 'Charon')
        self.declare_parameter('timeout_live', 30.0)
        self.declare_parameter('system_prompt',
            'Sei MARCUS — Modular Autonomous Robotic Control Unit System, un assistente robotico avanzato. Sei amichevole, conciso, intelligente e preciso. Parla SEMPRE e SOLO in lingua italiana.')

        self._api_key = api_key or self.get_parameter('gemini_api_key').value or os.environ.get('GEMINI_API_KEY', '')
        self._live_model = self.get_parameter('live_model_name').value
        self._voice_name = self.get_parameter('voice_name').value
        self._timeout_live = self.get_parameter('timeout_live').value
        self._system_prompt = self.get_parameter('system_prompt').value
        self._tool_executor = tool_executor

        # Audio Buffer Manager
        self.audio_buffer = AudioBufferManager()
        self.audio_buffer.set_barge_in_callback(self._on_barge_in_triggered)

        # QoS
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        # Publishers
        self.pub_audio_out = self.create_publisher(AudioData, '/ai/conversation/audio_out', qos_reliable)
        self.pub_response = self.create_publisher(String, '/ai/conversation/response', qos_reliable)
        self.pub_state = self.create_publisher(String, '/ai/conversation/state', qos_reliable)
        self.pub_live_status = self.create_publisher(String, '/ai/live/status', qos_reliable)

        # Subscriptions
        self.create_subscription(AudioData, '/ai/conversation/audio_chunk', self._audio_chunk_cb, qos_sensor)
        self.create_subscription(String, '/ai/input/text', self._text_input_cb, qos_reliable)
        self.create_subscription(Bool, '/voice/wake_word_detected', self._wake_word_cb, qos_reliable)
        self.create_subscription(Bool, '/ai/conversation/interrupt', self._interrupt_cb, qos_reliable)

        # Client Gemini
        self._client = None
        if self._api_key and HAS_GENAI:
            self._client = genai.Client(api_key=self._api_key)
        else:
            self.get_logger().warn("Gemini API Key non presente o genai non installato. Live WebSocket disabilitato.")

        # Dedicated Async Loop Thread
        self._loop = asyncio.new_event_loop()
        self._async_thread = threading.Thread(target=self._run_async_loop, daemon=True, name="live_bridge_async")
        self._async_thread.start()

        # Init LiveConnectionManager
        self.live_mgr = None
        if self._client:
            self.live_mgr = LiveConnectionManager(
                client=self._client,
                loop=self._loop,
                logger=self.get_logger(),
                model_getter=lambda: self._live_model,
                system_prompt_getter=lambda: self._system_prompt,
                voice_name_getter=lambda: self._voice_name,
                timeout_live_getter=lambda: self._timeout_live,
                on_audio_received=self._on_live_audio_received,
                on_tool_call=self._on_live_tool_call,
                on_turn_complete=self._on_live_turn_complete,
                on_mic_mute=self._on_live_mic_mute,
                on_interrupt=self._on_live_interrupt,
                history_getter=lambda: []
            )
            asyncio.run_coroutine_threadsafe(self.live_mgr.start_loop(), self._loop)
            self.get_logger().info("LiveConnectionBridgeNode avviato con successo.")

    def _run_async_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def set_tool_executor(self, executor: Callable):
        self._tool_executor = executor

    def _audio_chunk_cb(self, msg: AudioData):
        if not msg.data:
            return
        raw_bytes = bytes(msg.data)
        accepted = self.audio_buffer.push_mic_chunk(raw_bytes)
        if accepted and self.live_mgr:
            # Forward accepted non-echo mic chunk to Gemini Live stream
            self.live_mgr.send_audio_chunk(raw_bytes)

    def _text_input_cb(self, msg: String):
        text = msg.data.strip()
        if text and self.live_mgr:
            asyncio.run_coroutine_threadsafe(self.live_mgr.send_text(text), self._loop)

    def _wake_word_cb(self, msg: Bool):
        if msg.data and self.live_mgr:
            self.live_mgr.on_wakeword_detected()

    def _interrupt_cb(self, msg: Bool):
        if msg.data:
            self._on_barge_in_triggered()

    def _on_barge_in_triggered(self):
        self.get_logger().info("[LIVE_BRIDGE] Interruzione/Barge-In: Svuotamento buffer speaker.")
        self.audio_buffer.clear_speaker_buffer()
        if self.live_mgr:
            asyncio.run_coroutine_threadsafe(self.live_mgr.interrupt(), self._loop)

    def _on_live_audio_received(self, raw_audio: bytes):
        """Called when Gemini Live emits synthesized PCM 24kHz/16kHz audio."""
        self.audio_buffer.set_speaker_playing(True)
        self.audio_buffer.push_speaker_chunk(raw_audio)
        msg = AudioData()
        msg.data = list(raw_audio)
        self.pub_audio_out.publish(msg)

    async def _on_live_tool_call(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Delegates tool execution to registered tool executor / action server."""
        self.get_logger().info(f"[LIVE_BRIDGE] Tool Call: {tool_name}({args})")
        if self._tool_executor:
            try:
                res = await self._tool_executor(tool_name, args)
                return res if isinstance(res, dict) else {"result": str(res)}
            except Exception as e:
                self.get_logger().error(f"[LIVE_BRIDGE] Errore tool executor: {e}")
                return {"error": str(e)}
        return {"result": f"Skill {tool_name} eseguita."}

    def _on_live_turn_complete(self, user_text: str, model_text: str):
        self.audio_buffer.set_speaker_playing(False)
        resp_msg = String()
        resp_msg.data = model_text
        self.pub_response.publish(resp_msg)

    def _on_live_mic_mute(self, muted: bool):
        pass

    def _on_live_interrupt(self, interrupted: bool):
        if interrupted:
            self.audio_buffer.clear_speaker_buffer()


def main(args=None):
    rclpy.init(args=args)
    node = LiveConnectionBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
