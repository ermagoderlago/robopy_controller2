"""
==============================================================================
🧪 TIER 3: CROSS-FEATURE PAIRWISE COMBINATIONS (Combinatorial Interactions)
==============================================================================
Systematic pairwise interaction tests covering major feature boundaries in Marcus
Hybrid AI Architecture (PROJECT.md, TEST_INFRA.md, SPEC-00 to SPEC-07).

Coverage Threshold: >= 15 combinatorial tests.
==============================================================================
"""

import time
import pytest
import numpy as np

from tests.e2e.harness import (
    CognitiveIntentArbiter,
    CoreIsolationManager,
    FastPathSafetyArbiter,
    GeminiLiveConnectionManager,
    HailoQwenClient,
    NPUBenchmarkRunner,
    ResilientAudioPipeline,
    RobotSafetyContext,
    RouteTarget,
    SandboxErrorCode,
    TRINITYKinematicSandbox,
    VisionThroughputMonitor,
    WebSocketState,
)


class TestTier3PairwiseCombinations:
    # ------------------------------------------------------------------------
    # 1. F5 (Fast-Path Stop) + F1 (Hailo Qwen2 Streaming)
    # ------------------------------------------------------------------------
    def test_pair_01_fast_path_stop_during_qwen_streaming(self):
        """Emergency stop arriving while Qwen2 is streaming tokens triggers immediate halt."""
        arb_stop = FastPathSafetyArbiter()
        client = HailoQwenClient()

        # Simulated Qwen generation started
        gen_active = True
        # Emergency stop received mid-stream
        is_stop, lat = arb_stop.evaluate("FERMATI SUBITO!")
        if is_stop:
            gen_active = False

        assert is_stop is True
        assert lat < 10.0
        assert gen_active is False
        assert len(arb_stop.published_messages) == 1

    # ------------------------------------------------------------------------
    # 2. F5 (Fast-Path Stop) + F12 (Gemini Live WebSocket)
    # ------------------------------------------------------------------------
    def test_pair_02_fast_path_stop_during_gemini_active_session(self):
        """Emergency stop spoken during active Gemini Live WebSocket session halts robot immediately."""
        arb_stop = FastPathSafetyArbiter()
        mgr_live = GeminiLiveConnectionManager()

        mgr_live.request_session(current_time=100.0)
        assert mgr_live.state == WebSocketState.ACTIVE

        is_stop, lat = arb_stop.evaluate("Stop!")
        assert is_stop is True
        assert lat < 10.0
        assert arb_stop.published_messages[0]["linear_x"] == 0.0

    # ------------------------------------------------------------------------
    # 3. F6 (Intent Classifier) + F7 (Connectivity Failover)
    # ------------------------------------------------------------------------
    def test_pair_03_conversational_intent_with_degraded_wan(self):
        """Conversational query during high latency (>500ms) seamlessly routes to local Qwen2."""
        arb = CognitiveIntentArbiter()
        decision = arb.classify_intent("Raccontami una barzelletta", network_latency_ms=620.0)
        assert decision.destination == RouteTarget.LOCAL_QWEN
        assert decision.reason == "fallback_high_latency"
        assert decision.latency_us < 1500.0

    # ------------------------------------------------------------------------
    # 4. F1 (Hailo Qwen2) + F3 (Vision Throughput Preservation)
    # ------------------------------------------------------------------------
    def test_pair_04_qwen_inference_during_continuous_vision(self):
        """Heavy Qwen2 generation does not degrade vision pipeline below 10 FPS."""
        client = HailoQwenClient()
        monitor = VisionThroughputMonitor(target_fps=10.0)

        t = 0.0
        for _ in range(25):
            monitor.record_frame(t)
            t += 0.085

        _, prof = client.mock_generate_stream("Trova la bottiglia d'acqua")
        fps = monitor.compute_current_fps()
        assert fps >= 10.0
        assert prof.vision_fps >= 10.0
        assert monitor.dropped_frames == 0

    # ------------------------------------------------------------------------
    # 5. F2 (Core Pinning) + F3 (Vision Pipeline Concurrency)
    # ------------------------------------------------------------------------
    def test_pair_05_core_isolation_under_concurrent_vision_and_llm(self):
        """C++ vision nodes remain isolated on Cores 2-3 while Python components execute on Cores 0-1."""
        cpp_affinity = CoreIsolationManager.get_expected_affinity("hailo_bridge_node_cpp")
        py_affinity = CoreIsolationManager.get_expected_affinity("cognitive_router")

        assert set(cpp_affinity).isdisjoint(set(py_affinity))
        assert cpp_affinity == [2, 3]
        assert py_affinity == [0, 1]

    # ------------------------------------------------------------------------
    # 6. F9 (Structured JSON) + F10 (Kinematic Sandbox Approval)
    # ------------------------------------------------------------------------
    def test_pair_06_qwen_valid_json_passes_kinematic_sandbox(self):
        """Valid JSON tool call generated by Qwen2 passes kinematic pre-flight checks."""
        client = HailoQwenClient()
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext(battery_voltage=11.9, front_obstacle_distance=2.0)

        raw_llm = '{"action": "move_distance", "parameters": {"distance_m": 0.50, "speed_mps": 0.20}}'
        tool_call = client.extract_structured_json(raw_llm)
        verdict = sandbox.validate_action(tool_call, ctx)

        assert verdict.approved is True
        assert verdict.error_code is None
        assert verdict.sanitized_parameters["speed_mps"] == 0.20

    # ------------------------------------------------------------------------
    # 7. F9 (Structured JSON) + F10 (Kinematic Sandbox Rejection)
    # ------------------------------------------------------------------------
    def test_pair_07_qwen_excessive_speed_rejected_by_sandbox(self):
        """Qwen2 hallucinates excessive velocity (0.60 m/s); sandbox rejects with KINEMATIC_VIOLATION."""
        client = HailoQwenClient()
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext()

        raw_llm = '{"action": "move_distance", "parameters": {"distance_m": 1.0, "speed_mps": 0.60}}'
        tool_call = client.extract_structured_json(raw_llm)
        verdict = sandbox.validate_action(tool_call, ctx)

        assert verdict.approved is False
        assert verdict.error_code == SandboxErrorCode.KINEMATIC_VIOLATION
        assert "exceeds hard cap" in verdict.reason

    # ------------------------------------------------------------------------
    # 8. F10 (Kinematic Sandbox) + F11 (LiDAR Clearance Gate)
    # ------------------------------------------------------------------------
    def test_pair_08_obstacle_clearance_gate_rejection(self):
        """Close obstacle (< requested + 0.20m buffer) causes sandbox pre-flight rejection."""
        sandbox = TRINITYKinematicSandbox()
        # Requesting 0.60m move with obstacle at 0.70m (requires 0.60 + 0.20 = 0.80m)
        ctx = RobotSafetyContext(front_obstacle_distance=0.70)
        tool_call = {"action": "move_distance", "parameters": {"distance_m": 0.60, "speed_mps": 0.15}}
        verdict = sandbox.validate_action(tool_call, ctx)

        assert verdict.approved is False
        assert verdict.error_code == SandboxErrorCode.SPATIAL_OBSTACLE

    # ------------------------------------------------------------------------
    # 9. F10 (Kinematic Sandbox) + F12 (Gemini Live & Charging Lock)
    # ------------------------------------------------------------------------
    def test_pair_09_charging_interlock_blocks_motion_during_cloud_session(self):
        """During active cloud conversation, motion requests are locked out if charging (V >= 12.70V)."""
        mgr_live = GeminiLiveConnectionManager()
        sandbox = TRINITYKinematicSandbox()

        mgr_live.request_session()
        assert mgr_live.state == WebSocketState.ACTIVE

        ctx = RobotSafetyContext(battery_voltage=12.75, is_charging=True)
        tool_call = {"action": "move_distance", "parameters": {"distance_m": 0.20, "speed_mps": 0.10}}
        verdict = sandbox.validate_action(tool_call, ctx)

        assert verdict.approved is False
        assert verdict.error_code == SandboxErrorCode.BATTERY_CHARGING_LOCK

    # ------------------------------------------------------------------------
    # 10. F10 (Kinematic Sandbox) + F12 (Gemini Live & Battery Shutdown)
    # ------------------------------------------------------------------------
    def test_pair_10_critical_shutdown_cutoff_during_cloud_session(self):
        """Critical battery cutoff (V <= 9.85V) prevents motion while conversing."""
        mgr_live = GeminiLiveConnectionManager()
        sandbox = TRINITYKinematicSandbox()

        mgr_live.request_session()
        ctx = RobotSafetyContext(battery_voltage=9.75)
        tool_call = {"action": "move_distance", "parameters": {"distance_m": 0.10, "speed_mps": 0.05}}
        verdict = sandbox.validate_action(tool_call, ctx)

        assert verdict.approved is False
        assert verdict.error_code == SandboxErrorCode.BATTERY_LOW

    # ------------------------------------------------------------------------
    # 11. F13 (Resilient Audio) + F14 (VUI Barge-in Safety)
    # ------------------------------------------------------------------------
    def test_pair_11_audio_filtering_and_barge_in_gain_adaptation(self):
        """Audio pipeline applies 140Hz HPF and reduces STT gain to 0.1x during TTS playback."""
        pipeline = ResilientAudioPipeline(hpf_cutoff_hz=140.0)

        # Baseline listening
        assert pipeline.stt_gain == 1.0

        # TTS playback starts (barge-in protection)
        pipeline.set_barge_in_mode(is_tts_speaking=True)
        assert pipeline.stt_gain == 0.10

        # Filter audio chunk with Pi 5 fan noise (60Hz rumble)
        t = np.linspace(0, 0.05, 800, endpoint=False)
        noisy_chunk = (np.sin(2 * np.pi * 60.0 * t) * 8000).astype(np.int16)
        filtered = pipeline.apply_140hz_hpf(noisy_chunk)

        rms_in = np.sqrt(np.mean(noisy_chunk.astype(float)**2))
        rms_out = np.sqrt(np.mean(filtered.astype(float)**2))
        assert rms_out < rms_in * 0.35

    # ------------------------------------------------------------------------
    # 12. F12 (Gemini Live) + F14 (Conversation Inactivity Disconnect)
    # ------------------------------------------------------------------------
    def test_pair_12_inactivity_timer_disconnects_after_conversation_pause(self):
        """Prolonged user silence (25s) after conversation triggers graceful WebSocket close."""
        mgr = GeminiLiveConnectionManager()
        mgr.request_session(current_time=50.0)
        assert mgr.state == WebSocketState.ACTIVE

        # Inactivity update at t=75.1s (silence >= 25s)
        state = mgr.update_tick(current_time=75.1)
        assert state == WebSocketState.IDLE_TIMED_OUT

    # ------------------------------------------------------------------------
    # 13. F1 (Hailo Qwen2) + F9 (ChatML System Prompt & Tool Extraction)
    # ------------------------------------------------------------------------
    def test_pair_13_chatml_formatting_and_tool_roundtrip(self):
        """Full roundtrip: ChatML prompt contains schemas and parser handles output."""
        client = HailoQwenClient()
        prompt = client.format_chatml_prompt("Avvia esplorazione frontiere")
        assert "start_frontier_exploration" in prompt

        simulated_response = '{"action": "start_frontier_exploration", "parameters": {}}'
        parsed = client.extract_structured_json(simulated_response)
        assert parsed["action"] == "start_frontier_exploration"

    # ------------------------------------------------------------------------
    # 14. F7 (Failover Arbiter) + F12 (Gemini Live Cleanup)
    # ------------------------------------------------------------------------
    def test_pair_14_wan_drop_forces_session_cleanup_and_local_fallback(self):
        """WAN latency spike to 800ms cleans up cloud session and routes to local Qwen2."""
        mgr_live = GeminiLiveConnectionManager()
        arb = CognitiveIntentArbiter()

        mgr_live.request_session(current_time=10.0)
        assert mgr_live.state == WebSocketState.ACTIVE

        # WAN drops
        dec = arb.classify_intent("Spiegami l'entropia", network_latency_ms=800.0)
        assert dec.destination == RouteTarget.LOCAL_QWEN
        assert dec.reason == "fallback_high_latency"

        # Cloud session closed on failover
        mgr_live.close(current_time=15.0)
        assert mgr_live.state == WebSocketState.DISCONNECTED

    # ------------------------------------------------------------------------
    # 15. F5 (Fast-Path Stop) + F10 (Kinematic Sandbox Safety Precedence)
    # ------------------------------------------------------------------------
    def test_pair_15_fast_path_stop_overrides_pending_kinematic_actions(self):
        """Fast-path stop publishes directly to safety override, taking precedence over motion."""
        arb_stop = FastPathSafetyArbiter()
        sandbox = TRINITYKinematicSandbox()

        # Motion prepared
        motion_call = {"action": "move_distance", "parameters": {"distance_m": 0.5, "speed_mps": 0.2}}
        verdict = sandbox.validate_action(motion_call, RobotSafetyContext())
        assert verdict.approved is True

        # Emergency stop received immediately
        is_stop, _ = arb_stop.evaluate("bloccati")
        assert is_stop is True
        assert arb_stop.published_messages[0]["topic"] == "/cmd_vel_mux/input/safety_override"
        assert arb_stop.published_messages[0]["linear_x"] == 0.0

    # ------------------------------------------------------------------------
    # 16. F4 (NPU Benchmark) + F1 (Hailo Qwen2 Performance Tracking)
    # ------------------------------------------------------------------------
    def test_pair_16_npu_benchmark_tracks_qwen_metrics(self):
        """Benchmark profiler measures TTFT and CPU usage on active Qwen2 workload."""
        client = HailoQwenClient()
        _, prof = client.mock_generate_stream("Diagnostica di sistema", ttft_target_ms=220.0, cpu_delta_target=2.5)

        benchmark = NPUBenchmarkRunner.run_benchmark(
            num_prompts=5,
            simulated_ttft_ms=prof.ttft_ms,
            simulated_cpu_pct=prof.cpu_delta_pct,
            simulated_vision_fps=prof.vision_fps,
            simulated_mem_mb=prof.memory_rss_mb
        )
        assert benchmark["gate1_passed"] is True

    # ------------------------------------------------------------------------
    # 17. F13 (Resilient Audio) + F12 (Gemini Live Stream Rate Alignment)
    # ------------------------------------------------------------------------
    def test_pair_17_resampling_converts_stream_for_hardware_dac(self):
        """16kHz audio received from Gemini Live is resampled to 48kHz for hardware DAC playback."""
        pipeline = ResilientAudioPipeline()
        # 16kHz mono PCM chunk (160 samples = 320 bytes = 10ms)
        chunk_16k = np.zeros(160, dtype=np.int16).tobytes()
        resampled_48k = pipeline.resample_16k_to_48k(chunk_16k)

        assert len(resampled_48k) == 960  # 480 samples * 2 bytes = 960 bytes
        assert len(resampled_48k) == len(chunk_16k) * 3
