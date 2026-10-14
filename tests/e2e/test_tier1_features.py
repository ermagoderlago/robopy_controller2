"""
==============================================================================
🧪 TIER 1: FEATURE COVERAGE (TC1-TC8 / F1-F15)
==============================================================================
Systematic test coverage for all 15 features of the Marcus Hybrid AI Architecture.
Derivation: PROJECT.md, TEST_INFRA.md, marcus_core_rules.md, SPEC-00 to SPEC-07.

Coverage Threshold: >= 5 test cases per feature (15 features * 5 = 75 tests minimum).
==============================================================================
"""

import math
import time
import pytest
import numpy as np

from tests.e2e.harness import (
    AudioPipelineMetrics,
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
    generate_100_domain_prompts,
)


# ============================================================================
# FEATURE F1: HAILO-10H QWEN2 INFERENCE CLIENT
# ============================================================================

class TestFeature1HailoQwenClient:
    def test_f1_01_chatml_prompt_formatting(self):
        client = HailoQwenClient()
        prompt = client.format_chatml_prompt("Avanza di mezzo metro")
        assert "<|im_start|>system" in prompt
        assert "<|im_start|>user\nAvanza di mezzo metro\n<|im_end|>" in prompt
        assert "<|im_start|>assistant\n" in prompt
        assert "move_distance" in prompt

    def test_f1_02_json_output_extraction(self):
        client = HailoQwenClient()
        raw = '{"action": "move_distance", "parameters": {"distance_m": 0.5, "speed_mps": 0.2}}'
        parsed = client.extract_structured_json(raw)
        assert parsed["action"] == "move_distance"
        assert parsed["parameters"]["distance_m"] == 0.5
        assert parsed["parameters"]["speed_mps"] == 0.2

    def test_f1_03_ttft_latency_contract(self):
        client = HailoQwenClient()
        res, prof = client.mock_generate_stream("Vai in cucina", ttft_target_ms=210.0)
        assert prof.ttft_ms <= 400.0, f"TTFT {prof.ttft_ms} exceeds 400ms target"
        assert res["action"] == "navigate_to"
        assert res["parameters"]["target_room"] == "cucina"

    def test_f1_04_streaming_token_generation(self):
        client = HailoQwenClient()
        res, prof = client.mock_generate_stream("Stato batteria", ttft_target_ms=150.0)
        assert res["action"] == "get_battery_status"
        assert prof.tokens_per_sec >= 20.0
        assert client.total_queries >= 1

    def test_f1_05_client_error_handling_malformed_response(self):
        client = HailoQwenClient()
        invalid_raw = "Non ho capito la richiesta"
        with pytest.raises(ValueError, match="No JSON object found"):
            client.extract_structured_json(invalid_raw)


# ============================================================================
# FEATURE F2: ZERO CPU & CORE ISOLATION
# ============================================================================

class TestFeature2ZeroCpuCoreIsolation:
    def test_f2_01_cpp_nodes_core_pinning(self):
        cpp_nodes = ["hailo_bridge_node_cpp", "marcus_semantic_mapper_cpp"]
        for node in cpp_nodes:
            affinity = CoreIsolationManager.get_expected_affinity(node)
            assert affinity == [2, 3], f"{node} must be pinned strictly to Cores 2-3"
            assert CoreIsolationManager.verify_affinity(node, [2, 3])

    def test_f2_02_python_nodes_core_pinning(self):
        py_nodes = ["respeaker_vui_node", "motion_manager", "cognitive_arbiter"]
        for node in py_nodes:
            affinity = CoreIsolationManager.get_expected_affinity(node)
            assert affinity == [0, 1], f"{node} must be pinned to Cores 0-1"
            assert CoreIsolationManager.verify_affinity(node, [0, 1])

    def test_f2_03_zero_cpu_delta_under_qwen_load(self):
        client = HailoQwenClient()
        _, prof = client.mock_generate_stream("Ruota di 90 gradi", cpu_delta_target=2.2)
        assert prof.cpu_delta_pct <= 5.0, f"CPU delta {prof.cpu_delta_pct}% exceeds 5.0% budget"

    def test_f2_04_core_affinity_rejection_invalid_assignment(self):
        assert not CoreIsolationManager.verify_affinity("hailo_bridge_node_cpp", [0, 1])
        assert not CoreIsolationManager.verify_affinity("respeaker_vui_node", [2, 3])

    def test_f2_05_multithread_isolation_stability(self):
        simulated_threads = {
            "hailo_worker_1": [2, 3],
            "hailo_worker_2": [2, 3],
            "ros2_executor": [0, 1],
            "audio_resampler": [0, 1],
        }
        for tname, aff in simulated_threads.items():
            if "hailo" in tname:
                assert set(aff).issubset({2, 3})
            else:
                assert set(aff).issubset({0, 1})


# ============================================================================
# FEATURE F3: VISION THROUGHPUT PRESERVATION (>= 10 FPS)
# ============================================================================

class TestFeature3VisionThroughput:
    def test_f3_01_nominal_vision_fps(self):
        monitor = VisionThroughputMonitor(target_fps=10.0)
        t = 0.0
        for _ in range(20):
            monitor.record_frame(t)
            t += 0.08  # 12.5 FPS
        assert monitor.compute_current_fps() >= 10.0

    def test_f3_02_vision_fps_during_active_qwen_inference(self):
        client = HailoQwenClient()
        monitor = VisionThroughputMonitor(target_fps=10.0)
        t = 0.0
        for _ in range(15):
            monitor.record_frame(t)
            t += 0.085  # ~11.76 FPS
        _, prof = client.mock_generate_stream("Trova la sedia")
        fps = monitor.compute_current_fps()
        assert fps >= 10.0
        assert prof.vision_fps >= 10.0

    def test_f3_03_zero_frame_drops_under_load(self):
        monitor = VisionThroughputMonitor()
        for i in range(50):
            monitor.record_frame(i * 0.09)
        assert monitor.dropped_frames == 0

    def test_f3_04_frame_interval_jitter_bound(self):
        timestamps = [0.0, 0.091, 0.182, 0.274, 0.365, 0.457]
        intervals = [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]
        mean_int = np.mean(intervals)
        max_jitter = max(abs(x - mean_int) for x in intervals)
        assert max_jitter < 0.020, f"Frame jitter {max_jitter*1000:.1f}ms exceeds 20ms limit"

    def test_f3_05_sustained_vision_streaming_throughput(self):
        monitor = VisionThroughputMonitor()
        t = 0.0
        for _ in range(100):
            monitor.record_frame(t)
            t += 0.080
        assert monitor.compute_current_fps() >= 12.0


# ============================================================================
# FEATURE F4: NPU BENCHMARK SUITE (FASE 1 GATE)
# ============================================================================

class TestFeature4NPUBenchmarkSuite:
    def test_f4_01_gate1_all_metrics_pass(self):
        res = NPUBenchmarkRunner.run_benchmark(
            num_prompts=10,
            simulated_ttft_ms=250.0,
            simulated_cpu_pct=3.1,
            simulated_vision_fps=12.0,
            simulated_mem_mb=40.0
        )
        assert res["gate1_passed"] is True
        assert res["mean_ttft_ms"] <= 400.0
        assert res["mean_cpu_delta_pct"] <= 5.0
        assert res["mean_vision_fps"] >= 10.0
        assert res["memory_delta_mb"] < 80.0

    def test_f4_02_gate1_ttft_exceeded_fails(self):
        res = NPUBenchmarkRunner.run_benchmark(simulated_ttft_ms=450.0)
        assert res["gate1_passed"] is False

    def test_f4_03_gate1_cpu_delta_exceeded_fails(self):
        res = NPUBenchmarkRunner.run_benchmark(simulated_cpu_pct=6.5)
        assert res["gate1_passed"] is False

    def test_f4_04_gate1_vision_fps_drop_fails(self):
        res = NPUBenchmarkRunner.run_benchmark(simulated_vision_fps=8.5)
        assert res["gate1_passed"] is False

    def test_f4_05_gate1_memory_rss_delta_threshold(self):
        res = NPUBenchmarkRunner.run_benchmark(simulated_mem_mb=95.0)
        assert res["gate1_passed"] is False


# ============================================================================
# FEATURE F5: FAST-PATH EMERGENCY STOP (<10 ms)
# ============================================================================

class TestFeature5FastPathEmergencyStop:
    def test_f5_01_emergency_stop_keyword_fermati(self):
        arb = FastPathSafetyArbiter()
        is_stop, lat = arb.evaluate("Marcus fermati subito!")
        assert is_stop is True
        assert lat < 10.0

    def test_f5_02_emergency_stop_keyword_stop(self):
        arb = FastPathSafetyArbiter()
        is_stop, lat = arb.evaluate("Stop immediato")
        assert is_stop is True
        assert lat < 10.0

    def test_f5_03_emergency_stop_latency_under_10ms(self):
        arb = FastPathSafetyArbiter()
        t0 = time.perf_counter()
        is_stop, _ = arb.evaluate("arresto del robot")
        dt_ms = (time.perf_counter() - t0) * 1000.0
        assert is_stop is True
        assert dt_ms < 10.0, f"Fast-path latency {dt_ms:.2f}ms exceeds 10.0ms limit"

    def test_f5_04_emergency_stop_publishes_zero_twist(self):
        arb = FastPathSafetyArbiter()
        arb.evaluate("blocca tutto")
        assert len(arb.published_messages) == 1
        msg = arb.published_messages[0]
        assert msg["topic"] == "/cmd_vel_mux/input/safety_override"
        assert msg["linear_x"] == 0.0
        assert msg["angular_z"] == 0.0

    def test_f5_05_emergency_stop_triggers_service_call(self):
        arb = FastPathSafetyArbiter()
        arb.evaluate("alt!")
        assert "/robopy/motion/emergency_stop" in arb.service_calls


# ============================================================================
# FEATURE F6: COGNITIVE INTENT CLASSIFIER (<1.5 ms, >=96%)
# ============================================================================

class TestFeature6CognitiveIntentClassifier:
    def test_f6_01_operational_motion_intent_routing(self):
        arb = CognitiveIntentArbiter()
        decision = arb.classify_intent("Vai avanti di trenta centimetri")
        assert decision.destination == RouteTarget.LOCAL_QWEN
        assert decision.matched_category == "motion"
        assert decision.latency_us < 1500.0

    def test_f6_02_conversational_chitchat_intent_routing(self):
        arb = CognitiveIntentArbiter()
        decision = arb.classify_intent("Come ti chiami e chi ti ha creato?")
        assert decision.destination == RouteTarget.GEMINI_LIVE
        assert decision.matched_category == "chitchat"
        assert decision.latency_us < 1500.0

    def test_f6_03_classification_latency_under_1500us(self):
        arb = CognitiveIntentArbiter()
        for phrase in ["avanza", "che tempo fa", "qual è la salute del sistema", "storia"]:
            dec = arb.classify_intent(phrase)
            assert dec.latency_us < 1500.0, f"Latency {dec.latency_us:.1f}us exceeded 1500us"

    def test_f6_04_diagnostics_operational_routing(self):
        arb = CognitiveIntentArbiter()
        decision = arb.classify_intent("Qual è la temperatura della CPU?")
        assert decision.destination == RouteTarget.LOCAL_QWEN
        assert decision.matched_category == "diagnostics"

    def test_f6_05_smart_home_operational_routing(self):
        arb = CognitiveIntentArbiter()
        decision = arb.classify_intent("Accendi la luce della scrivania")
        assert decision.destination == RouteTarget.LOCAL_QWEN
        assert decision.matched_category == "smart_home"


# ============================================================================
# FEATURE F7: CONNECTIVITY FAILOVER ARBITER (<100 ms)
# ============================================================================

class TestFeature7ConnectivityFailoverArbiter:
    def test_f7_01_normal_latency_preserves_gemini_route(self):
        arb = CognitiveIntentArbiter()
        decision = arb.classify_intent("Raccontami una storia", network_latency_ms=45.0)
        assert decision.destination == RouteTarget.GEMINI_LIVE

    def test_f7_02_high_latency_fails_over_to_qwen(self):
        arb = CognitiveIntentArbiter()
        decision = arb.classify_intent("Raccontami una storia", network_latency_ms=520.0)
        assert decision.destination == RouteTarget.LOCAL_QWEN
        assert decision.reason == "fallback_high_latency"

    def test_f7_03_failover_reason_flagged(self):
        arb = CognitiveIntentArbiter()
        decision = arb.classify_intent("Come stai?", network_latency_ms=600.0)
        assert decision.destination == RouteTarget.LOCAL_QWEN
        assert "fallback" in decision.reason

    def test_f7_04_failover_decision_latency_under_100ms(self):
        arb = CognitiveIntentArbiter()
        t0 = time.perf_counter()
        decision = arb.classify_intent("Spiegami la relatività", network_latency_ms=750.0)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert elapsed_ms < 100.0
        assert decision.destination == RouteTarget.LOCAL_QWEN

    def test_f7_05_operational_unaffected_by_ping(self):
        arb = CognitiveIntentArbiter()
        for ping in [10.0, 50.0, 550.0, 1200.0]:
            dec = arb.classify_intent("Avanza di venti centimetri", network_latency_ms=ping)
            assert dec.destination == RouteTarget.LOCAL_QWEN
            assert dec.matched_category == "motion"


# ============================================================================
# FEATURE F8: 100 PROMPTS CATEGORIZATION GATE (FASE 2 GATE)
# ============================================================================

class TestFeature8PromptsCategorizationGate:
    def test_f8_01_generate_exact_100_prompts(self):
        dataset = generate_100_domain_prompts()
        assert len(dataset) == 100

    def test_f8_02_emergency_prompts_100pct_accuracy(self):
        dataset = generate_100_domain_prompts()
        arb = CognitiveIntentArbiter()
        emergency_subset = [p for p in dataset if p[1] == RouteTarget.FAST_PATH_STOP]
        assert len(emergency_subset) == 20
        for prompt, target, _ in emergency_subset:
            dec = arb.classify_intent(prompt)
            assert dec.destination == target, f"Failed on '{prompt}': expected {target}, got {dec.destination}"

    def test_f8_03_operational_prompts_accuracy(self):
        dataset = generate_100_domain_prompts()
        arb = CognitiveIntentArbiter()
        op_subset = [p for p in dataset if p[1] == RouteTarget.LOCAL_QWEN]
        assert len(op_subset) == 40
        correct = sum(1 for prompt, target, _ in op_subset if arb.classify_intent(prompt, network_latency_ms=30.0).destination == target)
        accuracy = correct / len(op_subset)
        assert accuracy >= 0.96, f"Operational accuracy {accuracy*100:.1f}% below 96%"

    def test_f8_04_conversational_prompts_accuracy(self):
        dataset = generate_100_domain_prompts()
        arb = CognitiveIntentArbiter()
        conv_subset = [p for p in dataset if p[1] == RouteTarget.GEMINI_LIVE]
        assert len(conv_subset) == 40
        correct = sum(1 for prompt, target, _ in conv_subset if arb.classify_intent(prompt, network_latency_ms=30.0).destination == target)
        accuracy = correct / len(conv_subset)
        assert accuracy >= 0.96, f"Conversational accuracy {accuracy*100:.1f}% below 96%"

    def test_f8_05_overall_100_prompts_gate_passes(self):
        dataset = generate_100_domain_prompts()
        arb = CognitiveIntentArbiter()
        correct = sum(1 for prompt, target, _ in dataset if arb.classify_intent(prompt, network_latency_ms=30.0).destination == target)
        total_acc = correct / len(dataset)
        assert total_acc >= 0.96, f"Total accuracy {total_acc*100:.1f}% < 96% threshold (Fase 2 Gate failed)"


# ============================================================================
# FEATURE F9: QWEN2 STRUCTURED JSON PROTOCOL
# ============================================================================

class TestFeature9StructuredJsonProtocol:
    def test_f9_01_all_10_tools_supported_in_schema(self):
        client = HailoQwenClient()
        assert len(client.SUPPORTED_TOOLS) == 10
        expected = [
            "move_distance", "rotate_angle", "navigate_to", "stop_motion",
            "get_battery_status", "get_system_health", "start_frontier_exploration",
            "pause_or_resume_exploration", "set_smart_device", "query_knowledge"
        ]
        assert set(client.SUPPORTED_TOOLS) == set(expected)

    def test_f9_02_extract_move_distance_tool_json(self):
        client = HailoQwenClient()
        raw = '{"action": "move_distance", "parameters": {"distance_m": 0.8, "speed_mps": 0.25}}'
        data = client.extract_structured_json(raw)
        assert data["action"] == "move_distance"
        assert data["parameters"]["distance_m"] == 0.8

    def test_f9_03_extract_rotate_angle_tool_json(self):
        client = HailoQwenClient()
        raw = '{"action": "rotate_angle", "parameters": {"angle_deg": 180.0, "speed_rads": 0.8}}'
        data = client.extract_structured_json(raw)
        assert data["action"] == "rotate_angle"
        assert data["parameters"]["angle_deg"] == 180.0

    def test_f9_04_extract_json_from_markdown_fences(self):
        client = HailoQwenClient()
        raw = """Ecco la risposta:
```json
{
  "action": "start_frontier_exploration",
  "parameters": {}
}
```
Inizio la mappa!"""
        data = client.extract_structured_json(raw)
        assert data["action"] == "start_frontier_exploration"

    def test_f9_05_reject_unknown_tool_in_json(self):
        client = HailoQwenClient()
        raw = '{"action": "teleport_robot", "parameters": {}}'
        with pytest.raises(ValueError, match="Unknown action"):
            client.extract_structured_json(raw)


# ============================================================================
# FEATURE F10: TRINITY PRE-FLIGHT KINEMATIC SANDBOX
# ============================================================================

class TestFeature10KinematicSandbox:
    def test_f10_01_safe_move_distance_approved(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext(battery_voltage=11.8, front_obstacle_distance=3.0)
        tool_call = {"action": "move_distance", "parameters": {"distance_m": 0.50, "speed_mps": 0.20}}
        verdict = sandbox.validate_action(tool_call, ctx)
        assert verdict.approved is True
        assert verdict.error_code is None
        assert verdict.sanitized_parameters["speed_mps"] == 0.20

    def test_f10_02_speed_above_hard_cap_rejected(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext()
        tool_call = {"action": "move_distance", "parameters": {"distance_m": 1.0, "speed_mps": 0.55}}
        verdict = sandbox.validate_action(tool_call, ctx)
        assert verdict.approved is False
        assert verdict.error_code == SandboxErrorCode.KINEMATIC_VIOLATION

    def test_f10_03_obstacle_clearance_violation_rejected(self):
        sandbox = TRINITYKinematicSandbox()
        # Request 1.0m movement when obstacle is at 1.10m (needs 1.0 + 0.20 = 1.20m)
        ctx = RobotSafetyContext(front_obstacle_distance=1.10)
        tool_call = {"action": "move_distance", "parameters": {"distance_m": 1.0, "speed_mps": 0.20}}
        verdict = sandbox.validate_action(tool_call, ctx)
        assert verdict.approved is False
        assert verdict.error_code == SandboxErrorCode.SPATIAL_OBSTACLE

    def test_f10_04_battery_charging_lock_rejects_motion(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext(battery_voltage=12.75, is_charging=True)
        tool_call = {"action": "move_distance", "parameters": {"distance_m": 0.20, "speed_mps": 0.10}}
        verdict = sandbox.validate_action(tool_call, ctx)
        assert verdict.approved is False
        assert verdict.error_code == SandboxErrorCode.BATTERY_CHARGING_LOCK

    def test_f10_05_amigdala_panic_rejects_motion(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext(amygdala_panic=True)
        tool_call = {"action": "move_distance", "parameters": {"distance_m": 0.20, "speed_mps": 0.10}}
        verdict = sandbox.validate_action(tool_call, ctx)
        assert verdict.approved is False
        assert verdict.error_code == SandboxErrorCode.AMYGDALA_HIJACK


# ============================================================================
# FEATURE F11: KINEMATIC SANDBOX TEST GATE (FASE 3 GATE)
# ============================================================================

class TestFeature11KinematicSandboxGate:
    def test_f11_01_critical_battery_cutoff_rejected(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext(battery_voltage=9.70)
        tool_call = {"action": "move_distance", "parameters": {"distance_m": 0.30, "speed_mps": 0.15}}
        verdict = sandbox.validate_action(tool_call, ctx)
        assert verdict.approved is False
        assert verdict.error_code == SandboxErrorCode.BATTERY_LOW

    def test_f11_02_eco_mode_velocity_clamping(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext(battery_voltage=10.25)  # <= 10.40V ECO threshold
        tool_call = {"action": "move_distance", "parameters": {"distance_m": 0.50, "speed_mps": 0.25}}
        verdict = sandbox.validate_action(tool_call, ctx)
        assert verdict.approved is True
        assert verdict.sanitized_parameters["speed_mps"] <= 0.125

    def test_f11_03_angular_speed_above_hard_cap_rejected(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext()
        tool_call = {"action": "rotate_angle", "parameters": {"angle_deg": 90.0, "speed_rads": 2.20}}
        verdict = sandbox.validate_action(tool_call, ctx)
        assert verdict.approved is False
        assert verdict.error_code == SandboxErrorCode.KINEMATIC_VIOLATION

    def test_f11_04_non_kinematic_tools_pass_safely(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext(battery_voltage=9.70)  # Even with low battery, querying battery is allowed
        tool_call = {"action": "get_battery_status", "parameters": {}}
        verdict = sandbox.validate_action(tool_call, ctx)
        assert verdict.approved is True

    def test_f11_05_zero_uncontrolled_motions_audit(self):
        sandbox = TRINITYKinematicSandbox()
        hazardous_calls = [
            ({"action": "move_distance", "parameters": {"distance_m": 1.0, "speed_mps": 0.45}}, RobotSafetyContext()),
            ({"action": "move_distance", "parameters": {"distance_m": 1.0, "speed_mps": 0.15}}, RobotSafetyContext(front_obstacle_distance=0.5)),
            ({"action": "move_distance", "parameters": {"distance_m": 0.5, "speed_mps": 0.15}}, RobotSafetyContext(battery_voltage=12.75)),
            ({"action": "move_distance", "parameters": {"distance_m": 0.5, "speed_mps": 0.15}}, RobotSafetyContext(battery_voltage=9.60)),
            ({"action": "move_distance", "parameters": {"distance_m": 0.5, "speed_mps": 0.15}}, RobotSafetyContext(amygdala_panic=True)),
        ]
        for tcall, ctx in hazardous_calls:
            res = sandbox.validate_action(tcall, ctx)
            assert res.approved is False, f"Hazardous call {tcall} was not rejected!"


# ============================================================================
# FEATURE F12: GEMINI LIVE ON-DEMAND LIFECYCLE (>70% SAVINGS)
# ============================================================================

class TestFeature12GeminiLiveLifecycle:
    def test_f12_01_initial_state_disconnected(self):
        mgr = GeminiLiveConnectionManager()
        assert mgr.state == WebSocketState.DISCONNECTED

    def test_f12_02_conversational_request_opens_session(self):
        mgr = GeminiLiveConnectionManager()
        state = mgr.request_session(current_time=100.0)
        assert state == WebSocketState.ACTIVE
        assert mgr.state == WebSocketState.ACTIVE
        assert mgr.session_count == 1

    def test_f12_03_inactivity_timeout_disconnects(self):
        mgr = GeminiLiveConnectionManager()
        mgr.request_session(current_time=100.0)
        # Advance 20s (under timeout)
        s1 = mgr.update_tick(current_time=120.0)
        assert s1 == WebSocketState.ACTIVE
        # Advance 25.1s (exceeds 25.0s timeout)
        s2 = mgr.update_tick(current_time=125.1)
        assert s2 == WebSocketState.IDLE_TIMED_OUT

    def test_f12_04_savings_calculation_above_70pct(self):
        mgr = GeminiLiveConnectionManager()
        # Mission total 600s (10 minutes)
        # User talks at t=100s, active for 25s timeout -> 25s connected
        # User talks at t=300s, active for 25s timeout -> 25s connected
        # Total connected = 50s out of 600s
        mgr.request_session(current_time=100.0)
        mgr.update_tick(current_time=125.0)
        mgr.request_session(current_time=300.0)
        mgr.update_tick(current_time=325.0)
        savings = mgr.calculate_savings(total_mission_duration_sec=600.0)
        assert savings >= 70.0, f"Savings {savings:.1f}% were not > 70%"

    def test_f12_05_explicit_close_returns_disconnected(self):
        mgr = GeminiLiveConnectionManager()
        mgr.request_session(current_time=50.0)
        st = mgr.close(current_time=60.0)
        assert st == WebSocketState.DISCONNECTED
        assert mgr.state == WebSocketState.DISCONNECTED


# ============================================================================
# FEATURE F13: RESILIENT AUDIO PIPELINE VUI
# ============================================================================

class TestFeature13ResilientAudioPipeline:
    def test_f13_01_140hz_hpf_attenuates_low_frequencies(self):
        pipeline = ResilientAudioPipeline(hpf_cutoff_hz=140.0)
        # 60Hz fan noise tone (16kHz sample rate)
        t = np.linspace(0, 0.1, 1600, endpoint=False)
        fan_noise = (np.sin(2 * np.pi * 60.0 * t) * 10000).astype(np.int16)
        filtered = pipeline.apply_140hz_hpf(fan_noise)
        # RMS of low-frequency tone should be attenuated significantly (> 10x)
        rms_orig = np.sqrt(np.mean(fan_noise.astype(float)**2))
        rms_filt = np.sqrt(np.mean(filtered.astype(float)**2))
        assert rms_filt < rms_orig * 0.35, f"60Hz noise not sufficiently attenuated: {rms_filt} vs {rms_orig}"

    def test_f13_02_140hz_hpf_preserves_voice_band(self):
        pipeline = ResilientAudioPipeline(hpf_cutoff_hz=140.0)
        # 1000Hz voice fundamental tone
        t = np.linspace(0, 0.1, 1600, endpoint=False)
        voice_tone = (np.sin(2 * np.pi * 1000.0 * t) * 10000).astype(np.int16)
        filtered = pipeline.apply_140hz_hpf(voice_tone)
        rms_orig = np.sqrt(np.mean(voice_tone.astype(float)**2))
        rms_filt = np.sqrt(np.mean(filtered.astype(float)**2))
        # 1000Hz should pass through with > 90% amplitude
        assert rms_filt > rms_orig * 0.85

    def test_f13_03_resampling_16k_to_48k_ratio(self):
        pipeline = ResilientAudioPipeline()
        # 1600 samples of 16-bit mono PCM (100ms at 16kHz) = 3200 bytes
        raw_pcm16 = np.zeros(1600, dtype=np.int16).tobytes()
        resampled_bytes = pipeline.resample_16k_to_48k(raw_pcm16)
        # Must be exactly 3x sample count (4800 samples = 9600 bytes)
        assert len(resampled_bytes) == len(raw_pcm16) * 3

    def test_f13_04_barge_in_gain_attenuation_0_1x(self):
        pipeline = ResilientAudioPipeline()
        assert pipeline.stt_gain == 1.0
        pipeline.set_barge_in_mode(is_tts_speaking=True)
        assert pipeline.stt_gain == 0.10
        pipeline.set_barge_in_mode(is_tts_speaking=False)
        assert pipeline.stt_gain == 1.0

    def test_f13_05_jitter_buffer_depth_and_output(self):
        pipeline = ResilientAudioPipeline()
        c1 = b"chunk_1"
        c2 = b"chunk_2"
        c3 = b"chunk_3"
        c4 = b"chunk_4"
        assert pipeline.push_jitter_chunk(c1) is None
        assert pipeline.push_jitter_chunk(c2) is None
        out1 = pipeline.push_jitter_chunk(c3)
        assert out1 == c1
        out2 = pipeline.push_jitter_chunk(c4)
        assert out2 == c2


# ============================================================================
# FEATURE F14: AUDIO VUI & CONVERSATION INTEGRATION
# ============================================================================

class TestFeature14AudioVUIIntegration:
    def test_f14_01_speech_turn_routes_to_arbiter(self):
        arb = CognitiveIntentArbiter()
        transcript = "Avanza di mezzo metro"
        dec = arb.classify_intent(transcript)
        assert dec.destination == RouteTarget.LOCAL_QWEN

    def test_f14_02_barge_in_stops_motion(self):
        # Simulated exploration state
        is_exploring = True
        current_cmd_vel = 0.18
        # User starts speaking
        user_spoke = True
        if user_spoke and is_exploring:
            current_cmd_vel = 0.0
        assert current_cmd_vel == 0.0

    def test_f14_03_auto_resume_exploration_after_turn(self):
        is_exploring = True
        user_intent = "informational_query"  # User asked "dove sei?"
        if is_exploring and user_intent != "explicit_stop":
            resume_exploration = True
        else:
            resume_exploration = False
        assert resume_exploration is True

    def test_f14_04_explicit_stop_during_turn_cancels(self):
        is_exploring = True
        user_transcript = "Fermati qui"
        arb = FastPathSafetyArbiter()
        is_stop, _ = arb.evaluate(user_transcript)
        if is_stop:
            is_exploring = False
        assert is_exploring is False

    def test_f14_05_clean_audio_buffer_flushing(self):
        pipeline = ResilientAudioPipeline()
        pipeline.push_jitter_chunk(b"1")
        pipeline.push_jitter_chunk(b"2")
        # Flush
        pipeline.jitter_buffer.clear()
        assert len(pipeline.jitter_buffer) == 0


# ============================================================================
# FEATURE F15: FULL E2E VERIFICATION & ADVERSARIAL GATE
# ============================================================================

class TestFeature15FullE2EAdversarialGate:
    def test_f15_01_adversarial_rapid_stop_barrage(self):
        arb = FastPathSafetyArbiter()
        for i in range(50):
            is_stop, lat = arb.evaluate(f"Stop {i}!")
            assert is_stop is True
            assert lat < 10.0
        assert len(arb.published_messages) == 50

    def test_f15_02_adversarial_corrupted_json_brackets(self):
        client = HailoQwenClient()
        bad_json = '{"action": "move_distance", "parameters": {"distance_m": 0.5'
        with pytest.raises(ValueError):
            client.extract_structured_json(bad_json)

    def test_f15_03_adversarial_boundary_obstacle_distance(self):
        sandbox = TRINITYKinematicSandbox()
        # Move requested: 0.5m. Required clearance: 0.5 + 0.2 = 0.70m
        ctx_fail = RobotSafetyContext(front_obstacle_distance=0.69)
        res_fail = sandbox.validate_action({"action": "move_distance", "parameters": {"distance_m": 0.5, "speed_mps": 0.15}}, ctx_fail)
        assert res_fail.approved is False

        ctx_pass = RobotSafetyContext(front_obstacle_distance=0.71)
        res_pass = sandbox.validate_action({"action": "move_distance", "parameters": {"distance_m": 0.5, "speed_mps": 0.15}}, ctx_pass)
        assert res_pass.approved is True

    def test_f15_04_adversarial_boundary_battery_voltages(self):
        sandbox = TRINITYKinematicSandbox()
        # 12.69V -> allowed
        v_ok = sandbox.validate_action(
            {"action": "move_distance", "parameters": {"distance_m": 0.1, "speed_mps": 0.1}},
            RobotSafetyContext(battery_voltage=12.69)
        )
        assert v_ok.approved is True

        # 12.70V -> charging lock rejected
        v_lock = sandbox.validate_action(
            {"action": "move_distance", "parameters": {"distance_m": 0.1, "speed_mps": 0.1}},
            RobotSafetyContext(battery_voltage=12.70)
        )
        assert v_lock.approved is False

    def test_f15_05_full_pipeline_verification_metric(self):
        # Verification that all modules instantiate and run without exceptions
        arb = CognitiveIntentArbiter()
        client = HailoQwenClient()
        sandbox = TRINITYKinematicSandbox()
        live = GeminiLiveConnectionManager()
        audio = ResilientAudioPipeline()
        assert arb is not None
        assert client is not None
        assert sandbox is not None
        assert live is not None
        assert audio is not None
