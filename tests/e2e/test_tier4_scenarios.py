"""
==============================================================================
🧪 TIER 4: REAL-WORLD APPLICATION SCENARIOS (S1 to S8)
==============================================================================
End-to-End Realistic User Workflows for Marcus Hybrid AI Architecture.
Derivation: PROJECT.md § Architecture, TEST_INFRA.md § Real-World Application Scenarios,
marcus_core_rules.md, and Technical Specifications (SPEC-01 to SPEC-07).

Coverage Threshold: 8 Application Workflows (S1 to S8).
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


class TestTier4RealWorldScenarios:
    # ------------------------------------------------------------------------
    # SCENARIO S1: Voice-initiated forward motion with obstacle collision rejection
    # Features: F5, F6, F9, F10
    # ------------------------------------------------------------------------
    def test_s1_voice_forward_motion_with_obstacle_rejection(self):
        """
        User voice command: 'vai avanti di un metro'.
        Cognitive Intent Arbiter classifies as operational -> routed to local Qwen2.
        Qwen2 outputs tool call: move_distance(distance_m=1.0, speed_mps=0.20).
        TRINITY Kinematic Sandbox detects LiDAR obstacle at 0.90m (requires 1.0m + 0.20m buffer = 1.20m).
        Sandbox rejects command with SPATIAL_OBSTACLE. Zero motion published.
        """
        arb = CognitiveIntentArbiter()
        client = HailoQwenClient()
        sandbox = TRINITYKinematicSandbox()

        # 1. Voice input
        transcript = "Marcus vai avanti di un metro per favore"
        decision = arb.classify_intent(transcript, network_latency_ms=30.0)
        assert decision.destination == RouteTarget.LOCAL_QWEN
        assert decision.latency_us < 1500.0

        # 2. Local Qwen2 inference generates structured tool call
        tool_call, profile = client.mock_generate_stream(
            transcript,
            simulated_tool="move_distance",
            simulated_params={"distance_m": 1.0, "speed_mps": 0.20}
        )
        assert tool_call["action"] == "move_distance"
        assert profile.ttft_ms <= 400.0

        # 3. Robot State with front obstacle at 0.90m
        context = RobotSafetyContext(
            battery_voltage=11.80,
            is_charging=False,
            front_obstacle_distance=0.90,  # Insufficient clearance
            linear_velocity=0.0
        )

        # 4. Pre-flight kinematic sandbox validation
        verdict = sandbox.validate_action(tool_call, context)
        assert verdict.approved is False
        assert verdict.error_code == SandboxErrorCode.SPATIAL_OBSTACLE
        assert "Obstacle at 0.90m" in verdict.reason

    # ------------------------------------------------------------------------
    # SCENARIO S2: High-speed navigation interrupted by immediate voice emergency stop
    # Features: F5, F10, F14
    # ------------------------------------------------------------------------
    def test_s2_navigation_interrupted_by_immediate_voice_stop(self):
        """
        Robot is navigating forward. User sees hazard and screams 'fermati subito!'.
        Fast-Path interceptor bypasses LLM, detects stop in < 2ms, publishes
        linear=0.0, angular=0.0 to /cmd_vel_mux/input/safety_override and calls emergency_stop service.
        """
        arb_stop = FastPathSafetyArbiter()
        sandbox = TRINITYKinematicSandbox()

        # 1. Robot is actively navigating
        active_motion = {"action": "move_distance", "parameters": {"distance_m": 2.0, "speed_mps": 0.25}}
        init_verdict = sandbox.validate_action(active_motion, RobotSafetyContext(front_obstacle_distance=5.0))
        assert init_verdict.approved is True

        # 2. Emergency voice command uttered
        t0 = time.perf_counter()
        is_stop, latency_ms = arb_stop.evaluate("fermati subito!")
        dt_ms = (time.perf_counter() - t0) * 1000.0

        assert is_stop is True
        assert latency_ms < 10.0
        assert dt_ms < 10.0

        # 3. Verified safety override published
        assert len(arb_stop.published_messages) == 1
        msg = arb_stop.published_messages[0]
        assert msg["topic"] == "/cmd_vel_mux/input/safety_override"
        assert msg["linear_x"] == 0.0
        assert msg["angular_z"] == 0.0
        assert "/robopy/motion/emergency_stop" in arb_stop.service_calls

    # ------------------------------------------------------------------------
    # SCENARIO S3: Open conversational chat with on-demand cloud session spin-up and auto-idling
    # Features: F6, F12, F13
    # ------------------------------------------------------------------------
    def test_s3_conversational_chat_with_on_demand_cloud_and_auto_idling(self):
        """
        User asks complex open conversational question: 'Spiegami la teoria della relatività'.
        Arbiter classifies as conversational -> routes to Gemini Live.
        GeminiLiveConnectionManager opens dynamic WebSocket: DISCONNECTED -> ACTIVE.
        Audio pipeline filters 140Hz fan noise and resamples audio 16kHz -> 48kHz for DAC.
        After 25.0s of silence, connection automatically transitions to IDLE_TIMED_OUT, saving >70% idle tokens.
        """
        arb = CognitiveIntentArbiter()
        mgr_live = GeminiLiveConnectionManager()
        audio = ResilientAudioPipeline()

        # 1. Initial state
        assert mgr_live.state == WebSocketState.DISCONNECTED

        # 2. Conversational prompt received
        prompt = "Spiegami la teoria della relatività ristretta"
        decision = arb.classify_intent(prompt, network_latency_ms=45.0)
        assert decision.destination == RouteTarget.GEMINI_LIVE

        # 3. Dynamic connection request at t=100.0s
        t_start = 100.0
        mgr_live.request_session(current_time=t_start)
        assert mgr_live.state == WebSocketState.ACTIVE

        # 4. Process incoming audio stream: 16kHz PCM resampled to 48kHz DAC
        pcm_16k = np.zeros(800, dtype=np.int16).tobytes()  # 50ms audio chunk
        resampled_48k = audio.resample_16k_to_48k(pcm_16k)
        assert len(resampled_48k) == len(pcm_16k) * 3

        # 5. User stops talking. Advance time to t=125.1s (silence >= 25s)
        mgr_live.update_tick(current_time=125.1)
        assert mgr_live.state == WebSocketState.IDLE_TIMED_OUT

        # 6. Verify token & session savings over 300s mission
        savings = mgr_live.calculate_savings(total_mission_duration_sec=300.0)
        # Active for 25.1s out of 300s -> ~91.6% savings
        assert savings > 70.0

    # ------------------------------------------------------------------------
    # SCENARIO S4: WAN connectivity drop (ping > 500ms) during active voice conversation
    # Features: F6, F7, F1, F9
    # ------------------------------------------------------------------------
    def test_s4_wan_connectivity_drop_fails_over_to_local_qwen(self):
        """
        User converses with robot. WAN network degrades with latency spike to 700 ms.
        Cognitive Intent Arbiter automatically fails over in < 100 ms to local Qwen2
        on Hailo-10H NPU with reason 'fallback_high_latency'.
        Local Qwen2 generates response locally with zero cloud dependencies.
        """
        arb = CognitiveIntentArbiter()
        client = HailoQwenClient()

        # Prompt during severe network lag
        prompt = "Raccontami una breve storia su un gatto curioso"
        t0 = time.perf_counter()
        decision = arb.classify_intent(prompt, network_latency_ms=700.0)
        failover_duration_ms = (time.perf_counter() - t0) * 1000.0

        assert decision.destination == RouteTarget.LOCAL_QWEN
        assert decision.reason == "fallback_high_latency"
        assert failover_duration_ms < 100.0

        # Local Qwen2 handles query locally
        tool_call, profile = client.mock_generate_stream(prompt, simulated_tool="query_knowledge")
        assert tool_call["action"] == "query_knowledge"
        assert profile.ttft_ms <= 400.0

    # ------------------------------------------------------------------------
    # SCENARIO S5: Heavy vision pipeline workload during multi-turn local Qwen2 tool execution
    # Features: F1, F2, F3, F4, F9
    # ------------------------------------------------------------------------
    def test_s5_vision_pipeline_sustained_during_heavy_qwen_workload(self):
        """
        C++ vision pipeline is running on Cores 2-3 at >= 10 FPS.
        Simultaneously, local Qwen2 on Hailo-10H executes multiple multi-turn operational
        tool calls (motion, status, navigation).
        Vision FPS remains >= 10 FPS with 0 dropped frames, and CPU delta is <= 5%.
        """
        client = HailoQwenClient()
        monitor = VisionThroughputMonitor(target_fps=10.0)

        # 1. Vision streaming active
        t = 0.0
        for _ in range(30):
            monitor.record_frame(t)
            t += 0.080  # 12.5 FPS

        # 2. Multi-turn Qwen2 execution
        queries = ["Avanza di venti centimetri", "Ruota a sinistra", "Stato batteria", "Vai in salotto"]
        for q in queries:
            tool, prof = client.mock_generate_stream(q, cpu_delta_target=2.8, ttft_target_ms=190.0)
            assert tool["action"] in client.SUPPORTED_TOOLS
            assert prof.cpu_delta_pct <= 5.0
            assert prof.ttft_ms <= 400.0
            # Vision frames continue arriving concurrently
            monitor.record_frame(t)
            t += 0.080

        # 3. Vision assertions
        assert monitor.compute_current_fps() >= 10.0
        assert monitor.dropped_frames == 0

    # ------------------------------------------------------------------------
    # SCENARIO S6: Low-battery ECO mode velocity clamping and charging-dock motion lockout
    # Features: F9, F10, F11
    # ------------------------------------------------------------------------
    def test_s6_low_battery_eco_clamping_and_docking_lockout(self):
        """
        Case A: Battery voltage drops to 10.25V (ECO mode threshold <= 10.40V). Commanded
        speed of 0.25 m/s is clamped to 50% (0.125 m/s).
        Case B: Robot is docked on charger (V = 12.75V >= 12.70V). All motion commands
        are completely locked out with BATTERY_CHARGING_LOCK.
        """
        sandbox = TRINITYKinematicSandbox()

        # Case A: ECO mode
        ctx_eco = RobotSafetyContext(battery_voltage=10.25, is_charging=False, front_obstacle_distance=2.0)
        call_eco = {"action": "move_distance", "parameters": {"distance_m": 0.50, "speed_mps": 0.25}}
        verdict_eco = sandbox.validate_action(call_eco, ctx_eco)
        assert verdict_eco.approved is True
        assert verdict_eco.sanitized_parameters["speed_mps"] <= 0.125

        # Case B: Charging Dock Lockout
        ctx_docked = RobotSafetyContext(battery_voltage=12.75, is_charging=True)
        call_dock = {"action": "move_distance", "parameters": {"distance_m": 0.10, "speed_mps": 0.05}}
        verdict_dock = sandbox.validate_action(call_dock, ctx_docked)
        assert verdict_dock.approved is False
        assert verdict_dock.error_code == SandboxErrorCode.BATTERY_CHARGING_LOCK

    # ------------------------------------------------------------------------
    # SCENARIO S7: Rapid alternating operational vs conversational speech turns
    # Features: F6, F12, F13, F14
    # ------------------------------------------------------------------------
    def test_s7_rapid_alternating_operational_and_conversational_turns(self):
        """
        Rapid interleaved voice dialogue:
        Turn 1: 'Avanza di venti centimetri' -> Operational (Qwen2)
        Turn 2: 'Chi ti ha costruito Marcus?' -> Conversational (Gemini Live connects)
        Turn 3: 'Gira a destra di novanta gradi' -> Operational (Qwen2)
        Turn 4: 'Raccontami una barzelletta' -> Conversational (Gemini Live active)
        Turn 5: 'Stop!' -> Fast-Path Stop (<10ms)
        Verifies zero routing confusion and seamless lifecycle adaptation.
        """
        arb = CognitiveIntentArbiter()
        mgr_live = GeminiLiveConnectionManager()
        audio = ResilientAudioPipeline()

        dialogue_turns = [
            ("Avanza di venti centimetri", RouteTarget.LOCAL_QWEN),
            ("Chi ti ha costruito Marcus?", RouteTarget.GEMINI_LIVE),
            ("Gira a destra di novanta gradi", RouteTarget.LOCAL_QWEN),
            ("Raccontami una barzelletta", RouteTarget.GEMINI_LIVE),
            ("Stop!", RouteTarget.FAST_PATH_STOP),
        ]

        t_sim = 10.0
        for phrase, expected_dest in dialogue_turns:
            decision = arb.classify_intent(phrase, network_latency_ms=35.0)
            assert decision.destination == expected_dest

            if decision.destination == RouteTarget.GEMINI_LIVE:
                mgr_live.request_session(current_time=t_sim)
                assert mgr_live.state == WebSocketState.ACTIVE
                # Speech output: barge-in attenuation active during TTS
                audio.set_barge_in_mode(is_tts_speaking=True)
                assert audio.stt_gain == 0.10
                audio.set_barge_in_mode(is_tts_speaking=False)
                assert audio.stt_gain == 1.00

            t_sim += 5.0

    # ------------------------------------------------------------------------
    # SCENARIO S8: Corrupted / hallucinated JSON output from small model with recovery
    # Features: F9, F10, F11
    # ------------------------------------------------------------------------
    def test_s8_corrupted_hallucinated_json_recovery(self):
        """
        Small edge model outputs noisy markdown or partially malformed JSON.
        Defensive JSON parser recovers valid schema; kinematic sandbox blocks
        hallucinated out-of-bound speeds without crashing.
        """
        client = HailoQwenClient()
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext()

        # Output with conversational preamble, code fences, and safe parameters
        noisy_output = """Ecco il comando generato:
```json
{
  "action": "move_distance",
  "parameters": {
    "distance_m": 0.40,
    "speed_mps": 0.20
  }
}
```
Esecuzione in corso."""
        parsed = client.extract_structured_json(noisy_output)
        assert parsed["action"] == "move_distance"
        verdict = sandbox.validate_action(parsed, ctx)
        assert verdict.approved is True

        # Hallucinated dangerous output: speed = 1.50 m/s
        dangerous_output = '{"action": "move_distance", "parameters": {"distance_m": 1.0, "speed_mps": 1.50}}'
        danger_call = client.extract_structured_json(dangerous_output)
        danger_verdict = sandbox.validate_action(danger_call, ctx)
        assert danger_verdict.approved is False
        assert danger_verdict.error_code == SandboxErrorCode.KINEMATIC_VIOLATION
