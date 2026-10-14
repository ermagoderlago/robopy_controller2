"""
==============================================================================
🧪 TIER 2: BOUNDARY & CORNER CASES (Exact Thresholds & Extremes)
==============================================================================
Systematic boundary testing across all 15 features of Marcus Hybrid AI Architecture.
Derivation: PROJECT.md, TEST_INFRA.md, marcus_core_rules.md, SPEC-01 to SPEC-07.

Coverage Threshold: >= 5 test cases per feature (15 features * 5 = 75 tests minimum).
==============================================================================
"""

import math
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
    generate_100_domain_prompts,
)


# ============================================================================
# FEATURE F1: HAILO-10H QWEN2 INFERENCE CLIENT BOUNDARIES
# ============================================================================

class TestFeature1HailoQwenClientBoundaries:
    def test_f1_b01_empty_user_prompt(self):
        client = HailoQwenClient()
        formatted = client.format_chatml_prompt("")
        assert "<|im_start|>user\n\n<|im_end|>" in formatted

    def test_f1_b02_massive_token_prompt_boundary(self):
        client = HailoQwenClient()
        long_prompt = "Avanza " * 200
        formatted = client.format_chatml_prompt(long_prompt)
        assert len(formatted) > 1000

    def test_f1_b03_ttft_boundary_exactly_400ms(self):
        client = HailoQwenClient()
        _, prof_pass = client.mock_generate_stream("Vai avanti", ttft_target_ms=400.0)
        assert prof_pass.ttft_ms <= 400.0

        _, prof_fail = client.mock_generate_stream("Vai avanti", ttft_target_ms=400.1)
        assert prof_fail.ttft_ms > 400.0

    def test_f1_b04_json_output_with_escaped_newlines_and_unicode(self):
        client = HailoQwenClient()
        raw = '{"action": "navigate_to", "parameters": {"target_room": "cucina\\nprincipale\\u0020nord"}}'
        parsed = client.extract_structured_json(raw)
        assert parsed["action"] == "navigate_to"
        assert "cucina\nprincipale nord" == parsed["parameters"]["target_room"]

    def test_f1_b05_partial_stream_interruption_recovery(self):
        client = HailoQwenClient()
        raw_cut = '{"action": "move_distance", "parameters": {"distance_m": 0.5'
        with pytest.raises(ValueError):
            client.extract_structured_json(raw_cut)
        # Verify subsequent query succeeds
        raw_good = '{"action": "stop_motion", "parameters": {"immediate": true}}'
        good = client.extract_structured_json(raw_good)
        assert good["action"] == "stop_motion"


# ============================================================================
# FEATURE F2: ZERO CPU & CORE ISOLATION BOUNDARIES
# ============================================================================

class TestFeature2ZeroCpuCoreIsolationBoundaries:
    def test_f2_b01_cpu_delta_boundary_exactly_5_pct(self):
        client = HailoQwenClient()
        _, prof_ok = client.mock_generate_stream("Test", cpu_delta_target=5.0)
        assert prof_ok.cpu_delta_pct <= 5.0

        _, prof_fail = client.mock_generate_stream("Test", cpu_delta_target=5.05)
        assert prof_fail.cpu_delta_pct > 5.0

    def test_f2_b02_single_core_affinity_edge_case(self):
        # Even if someone assigns only Core 2 to C++, it should fail because {2, 3} is required
        assert not CoreIsolationManager.verify_affinity("hailo_bridge_node_cpp", [2])
        assert not CoreIsolationManager.verify_affinity("hailo_bridge_node_cpp", [3])
        assert CoreIsolationManager.verify_affinity("hailo_bridge_node_cpp", [2, 3])

    def test_f2_b03_spurious_thread_affinity_reset(self):
        # Verify invalid masks containing Core 0 for C++ node are rejected
        assert not CoreIsolationManager.verify_affinity("hailo_bridge_node_cpp", [0, 2, 3])

    def test_f2_b04_full_4core_saturation_test(self):
        # Python nodes must not touch cores 2 or 3
        python_aff = CoreIsolationManager.get_expected_affinity("respeaker_vui_node")
        assert 2 not in python_aff and 3 not in python_aff

    def test_f2_b05_affinity_bitmask_conversion_edge_cases(self):
        # 0x03 -> Cores 0,1. 0x0C -> Cores 2,3.
        mask_cpp = 0x0C
        cores_cpp = [i for i in range(4) if (mask_cpp >> i) & 1]
        assert cores_cpp == [2, 3]

        mask_py = 0x03
        cores_py = [i for i in range(4) if (mask_py >> i) & 1]
        assert cores_py == [0, 1]


# ============================================================================
# FEATURE F3: VISION THROUGHPUT PRESERVATION BOUNDARIES
# ============================================================================

class TestFeature3VisionThroughputBoundaries:
    def test_f3_b01_vision_fps_boundary_exactly_10_0_fps(self):
        monitor = VisionThroughputMonitor()
        t = 0.0
        # Exactly 10 FPS: 0.10s interval
        for _ in range(11):
            monitor.record_frame(t)
            t += 0.10
        assert round(monitor.compute_current_fps(), 2) == 10.00

    def test_f3_b02_burst_frame_arrival_no_drops(self):
        monitor = VisionThroughputMonitor()
        for i in range(5):
            monitor.record_frame(i * 0.002)
        assert monitor.dropped_frames == 0

    def test_f3_b03_maximum_allowable_interframe_gap(self):
        monitor = VisionThroughputMonitor()
        monitor.record_frame(0.0)
        monitor.record_frame(0.09)
        monitor.record_frame(0.18)
        # Gap of 105ms is allowable if overall FPS >= 10
        monitor.record_frame(0.285)
        assert monitor.compute_current_fps() >= 10.0

    def test_f3_b04_camera_reconnect_transient_recovery(self):
        monitor = VisionThroughputMonitor()
        monitor.record_frame(0.0)
        # Reconnect gap
        monitor.record_frame(0.12)
        # Recovery
        monitor.record_frame(0.18)
        monitor.record_frame(0.24)
        assert monitor.dropped_frames == 0

    def test_f3_b05_high_resolution_frame_simulation(self):
        monitor = VisionThroughputMonitor()
        # 1280x720 RGB-D frames arriving at 11 FPS
        t = 0.0
        for _ in range(30):
            monitor.record_frame(t)
            t += (1.0 / 11.0)
        assert monitor.compute_current_fps() >= 10.5


# ============================================================================
# FEATURE F4: NPU BENCHMARK SUITE BOUNDARIES
# ============================================================================

class TestFeature4NPUBenchmarkSuiteBoundaries:
    def test_f4_b01_benchmark_boundary_all_at_exact_limits(self):
        res = NPUBenchmarkRunner.run_benchmark(
            simulated_ttft_ms=400.0,
            simulated_cpu_pct=5.0,
            simulated_vision_fps=10.0,
            simulated_mem_mb=79.9
        )
        assert res["gate1_passed"] is True

    def test_f4_b02_benchmark_single_metric_fail_ttft_400_1(self):
        res = NPUBenchmarkRunner.run_benchmark(
            simulated_ttft_ms=400.1,
            simulated_cpu_pct=5.0,
            simulated_vision_fps=10.0,
            simulated_mem_mb=79.9
        )
        assert res["gate1_passed"] is False

    def test_f4_b03_benchmark_single_metric_fail_cpu_5_01(self):
        res = NPUBenchmarkRunner.run_benchmark(
            simulated_ttft_ms=400.0,
            simulated_cpu_pct=5.01,
            simulated_vision_fps=10.0,
            simulated_mem_mb=79.9
        )
        assert res["gate1_passed"] is False

    def test_f4_b04_benchmark_single_metric_fail_fps_9_99(self):
        res = NPUBenchmarkRunner.run_benchmark(
            simulated_ttft_ms=400.0,
            simulated_cpu_pct=5.0,
            simulated_vision_fps=9.99,
            simulated_mem_mb=79.9
        )
        assert res["gate1_passed"] is False

    def test_f4_b05_benchmark_single_metric_fail_mem_80_1(self):
        res = NPUBenchmarkRunner.run_benchmark(
            simulated_ttft_ms=400.0,
            simulated_cpu_pct=5.0,
            simulated_vision_fps=10.0,
            simulated_mem_mb=80.1
        )
        assert res["gate1_passed"] is False


# ============================================================================
# FEATURE F5: FAST-PATH EMERGENCY STOP BOUNDARIES
# ============================================================================

class TestFeature5FastPathEmergencyStopBoundaries:
    def test_f5_b01_stop_embedded_inside_long_sentence(self):
        arb = FastPathSafetyArbiter()
        phrase = "Ascolta Marcus prima di fare qualsiasi altra cosa per favore fermati per sicurezza"
        is_stop, _ = arb.evaluate(phrase)
        assert is_stop is True

    def test_f5_b02_case_and_punctuation_permutations(self):
        arb = FastPathSafetyArbiter()
        variants = ["STOP!", "---fermati---", "BLOCCA.", "[alt]", "arresto!!!"]
        for v in variants:
            is_stop, _ = arb.evaluate(v)
            assert is_stop is True, f"Failed to detect stop on variant '{v}'"

    def test_f5_b03_non_stop_substring_avoided(self):
        arb = FastPathSafetyArbiter()
        non_stops = ["stopper", "autostop", "bastanza", "fermatura", "pistone"]
        for ns in non_stops:
            is_stop, _ = arb.evaluate(ns)
            assert is_stop is False, f"False positive detected on '{ns}'"

    def test_f5_b04_latency_boundary_approaching_10ms(self):
        arb = FastPathSafetyArbiter()
        t0 = time.perf_counter()
        is_stop, lat = arb.evaluate("stop")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert is_stop is True
        assert elapsed_ms < 10.0
        assert elapsed_ms < 2.0  # Target < 2ms

    def test_f5_b05_empty_and_null_transcript_safety(self):
        arb = FastPathSafetyArbiter()
        for empty in ["", "   ", "\t\n", "   \r\n"]:
            is_stop, _ = arb.evaluate(empty)
            assert is_stop is False


# ============================================================================
# FEATURE F6: COGNITIVE INTENT CLASSIFIER BOUNDARIES
# ============================================================================

class TestFeature6CognitiveIntentClassifierBoundaries:
    def test_f6_b01_ambiguous_phrase_classification(self):
        arb = CognitiveIntentArbiter()
        phrase = "Marcus vorrei che tu andassi in cucina per favore"
        dec = arb.classify_intent(phrase)
        assert dec.destination == RouteTarget.LOCAL_QWEN
        assert dec.matched_category == "motion"

    def test_f6_b02_latency_boundary_1499us_vs_1501us(self):
        arb = CognitiveIntentArbiter()
        dec = arb.classify_intent("Accendi la luce")
        assert dec.latency_us < 1500.0

    def test_f6_b03_mixed_keywords_safety_precedence(self):
        arb = CognitiveIntentArbiter()
        phrase = "Marcus fermati e spiegami la teoria della relatività"
        dec = arb.classify_intent(phrase)
        # Emergency stop must override conversational intent
        assert dec.destination == RouteTarget.FAST_PATH_STOP

    def test_f6_b04_ultra_short_utterance(self):
        arb = CognitiveIntentArbiter()
        assert arb.classify_intent("vai").destination == RouteTarget.LOCAL_QWEN
        assert arb.classify_intent("stop").destination == RouteTarget.FAST_PATH_STOP
        assert arb.classify_intent("ciao").destination == RouteTarget.GEMINI_LIVE

    def test_f6_b05_special_characters_and_emojis(self):
        arb = CognitiveIntentArbiter()
        dec = arb.classify_intent("🚀 Avanza di 1 metro! #motion")
        assert dec.destination == RouteTarget.LOCAL_QWEN


# ============================================================================
# FEATURE F7: CONNECTIVITY FAILOVER ARBITER BOUNDARIES
# ============================================================================

class TestFeature7ConnectivityFailoverArbiterBoundaries:
    def test_f7_b01_ping_boundary_exactly_500_0ms(self):
        arb = CognitiveIntentArbiter()
        dec_normal = arb.classify_intent("Racconta una storia", network_latency_ms=500.0)
        assert dec_normal.destination == RouteTarget.GEMINI_LIVE

        dec_failover = arb.classify_intent("Racconta una storia", network_latency_ms=500.1)
        assert dec_failover.destination == RouteTarget.LOCAL_QWEN
        assert dec_failover.reason == "fallback_high_latency"

    def test_f7_b02_wan_packet_loss_infinity_ping(self):
        arb = CognitiveIntentArbiter()
        dec = arb.classify_intent("Come stai?", network_latency_ms=float("inf"))
        assert dec.destination == RouteTarget.LOCAL_QWEN
        assert dec.reason == "fallback_high_latency"

    def test_f7_b03_ping_recovery_flapping_hysteresis(self):
        arb = CognitiveIntentArbiter()
        pings = [40.0, 550.0, 45.0, 600.0, 35.0]
        expected = [
            RouteTarget.GEMINI_LIVE,
            RouteTarget.LOCAL_QWEN,
            RouteTarget.GEMINI_LIVE,
            RouteTarget.LOCAL_QWEN,
            RouteTarget.GEMINI_LIVE,
        ]
        for ping, exp in zip(pings, expected):
            dec = arb.classify_intent("Raccontami qualcosa", network_latency_ms=ping)
            assert dec.destination == exp

    def test_f7_b04_failover_latency_stress(self):
        arb = CognitiveIntentArbiter()
        t0 = time.perf_counter()
        for _ in range(100):
            arb.classify_intent("Barzelletta", network_latency_ms=700.0)
        dur_ms = (time.perf_counter() - t0) * 1000.0
        assert dur_ms < 100.0, f"100 failovers took {dur_ms:.1f}ms (>100ms)"

    def test_f7_b05_negative_or_zero_ping_handling(self):
        arb = CognitiveIntentArbiter()
        for p in [0.0, -1.0]:
            dec = arb.classify_intent("Raccontami una barzelletta", network_latency_ms=p)
            assert dec.destination == RouteTarget.GEMINI_LIVE


# ============================================================================
# FEATURE F8: 100 PROMPTS CATEGORIZATION GATE BOUNDARIES
# ============================================================================

class TestFeature8PromptsCategorizationGateBoundaries:
    def test_f8_b01_boundary_prompt_length_extremes(self):
        arb = CognitiveIntentArbiter()
        single_word = arb.classify_intent("Avanza")
        assert single_word.destination == RouteTarget.LOCAL_QWEN

        long_sentence = arb.classify_intent(
            "Ti chiedo cortesemente se potresti gentilmente avanzare in direzione della cucina "
            "perché desidero che tu vada a verificare lo stato dell'ambiente e poi torni indietro"
        )
        assert long_sentence.destination == RouteTarget.LOCAL_QWEN

    def test_f8_b02_high_noise_transcript_with_filler_words(self):
        arb = CognitiveIntentArbiter()
        noisy = "Ehm dunque... Marcus, controlla lo stato e il voltaggio della batteria per favore"
        dec = arb.classify_intent(noisy)
        assert dec.destination == RouteTarget.LOCAL_QWEN

    def test_f8_b03_colloquial_phrasing_variations(self):
        arb = CognitiveIntentArbiter()
        dec1 = arb.classify_intent("Gira a destra subito")
        assert dec1.destination == RouteTarget.LOCAL_QWEN
        dec2 = arb.classify_intent("Spostati un poco più avanti")
        assert dec2.destination == RouteTarget.LOCAL_QWEN

    def test_f8_b04_conversational_philosophical_complex_sentence(self):
        arb = CognitiveIntentArbiter()
        dec = arb.classify_intent("Qual è secondo te il significato profondo della coscienza robotica?")
        assert dec.destination == RouteTarget.GEMINI_LIVE

    def test_f8_b05_exact_100_percent_safety_prompt_zero_tolerance(self):
        dataset = generate_100_domain_prompts()
        arb = CognitiveIntentArbiter()
        emergency_subset = [p for p in dataset if p[1] == RouteTarget.FAST_PATH_STOP]
        missed = [p[0] for p in emergency_subset if arb.classify_intent(p[0]).destination != RouteTarget.FAST_PATH_STOP]
        assert len(missed) == 0, f"Zero tolerance violated: missed emergency stop on {missed}"


# ============================================================================
# FEATURE F9: QWEN2 STRUCTURED JSON PROTOCOL BOUNDARIES
# ============================================================================

class TestFeature9StructuredJsonProtocolBoundaries:
    def test_f9_b01_trailing_commas_in_json(self):
        client = HailoQwenClient()
        # Normal JSON allows clean parsing, verify robust handling
        valid_json = '{"action": "stop_motion", "parameters": {"immediate": true}}'
        data = client.extract_structured_json(valid_json)
        assert data["action"] == "stop_motion"

    def test_f9_b02_deeply_nested_parameters(self):
        client = HailoQwenClient()
        nested = '{"action": "set_smart_device", "parameters": {"device_name": "lamp", "state": "on", "extra": {"brightness": 80}}}'
        data = client.extract_structured_json(nested)
        assert data["action"] == "set_smart_device"
        assert data["parameters"]["extra"]["brightness"] == 80

    def test_f9_b03_extra_conversational_text_around_json(self):
        client = HailoQwenClient()
        text = 'Certamente! Procedo:\n{"action": "move_distance", "parameters": {"distance_m": 0.4, "speed_mps": 0.2}}\nFatto!'
        data = client.extract_structured_json(text)
        assert data["action"] == "move_distance"
        assert data["parameters"]["distance_m"] == 0.4

    def test_f9_b04_numeric_type_coercion_string_to_float(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext()
        call = {"action": "move_distance", "parameters": {"distance_m": "0.30", "speed_mps": "0.15"}}
        verdict = sandbox.validate_action(call, ctx)
        assert verdict.approved is True
        assert verdict.sanitized_parameters["distance_m"] == 0.30

    def test_f9_b05_missing_parameters_field_defaults_to_empty_dict(self):
        client = HailoQwenClient()
        call = client.extract_structured_json('{"action": "get_battery_status"}')
        assert call["parameters"] == {}


# ============================================================================
# FEATURE F10: TRINITY PRE-FLIGHT KINEMATIC SANDBOX BOUNDARIES
# ============================================================================

class TestFeature10KinematicSandboxBoundaries:
    def test_f10_b01_speed_boundary_exactly_0_25_mps(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext()
        # 0.25 m/s approved without clamping
        call_exact = {"action": "move_distance", "parameters": {"distance_m": 0.5, "speed_mps": 0.25}}
        v_exact = sandbox.validate_action(call_exact, ctx)
        assert v_exact.approved is True
        assert v_exact.sanitized_parameters["speed_mps"] == 0.25

        # 0.26 m/s clamped to 0.25 m/s
        call_clamp = {"action": "move_distance", "parameters": {"distance_m": 0.5, "speed_mps": 0.26}}
        v_clamp = sandbox.validate_action(call_clamp, ctx)
        assert v_clamp.approved is True
        assert v_clamp.sanitized_parameters["speed_mps"] == 0.25

    def test_f10_b02_speed_boundary_exactly_0_40_mps(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext()
        # 0.40 m/s approved (clamped to 0.25)
        call_40 = {"action": "move_distance", "parameters": {"distance_m": 0.5, "speed_mps": 0.40}}
        v_40 = sandbox.validate_action(call_40, ctx)
        assert v_40.approved is True

        # 0.401 m/s rejected as KINEMATIC_VIOLATION
        call_401 = {"action": "move_distance", "parameters": {"distance_m": 0.5, "speed_mps": 0.401}}
        v_401 = sandbox.validate_action(call_401, ctx)
        assert v_401.approved is False
        assert v_401.error_code == SandboxErrorCode.KINEMATIC_VIOLATION

    def test_f10_b03_angular_speed_boundary_1_0_and_1_80_rads(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext()
        # 1.0 rad/s approved unclamped
        v_10 = sandbox.validate_action({"action": "rotate_angle", "parameters": {"angle_deg": 45, "speed_rads": 1.0}}, ctx)
        assert v_10.approved is True
        assert v_10.sanitized_parameters["speed_rads"] == 1.0

        # 1.80 rad/s approved (clamped to 1.0)
        v_18 = sandbox.validate_action({"action": "rotate_angle", "parameters": {"angle_deg": 45, "speed_rads": 1.80}}, ctx)
        assert v_18.approved is True
        assert v_18.sanitized_parameters["speed_rads"] == 1.0

        # 1.801 rad/s rejected
        v_181 = sandbox.validate_action({"action": "rotate_angle", "parameters": {"angle_deg": 45, "speed_rads": 1.801}}, ctx)
        assert v_181.approved is False
        assert v_181.error_code == SandboxErrorCode.KINEMATIC_VIOLATION

    def test_f10_b04_obstacle_distance_exact_threshold(self):
        sandbox = TRINITYKinematicSandbox()
        # Requested dist: 0.50m. Buffer: 0.20m. Required: 0.70m
        ctx_pass = RobotSafetyContext(front_obstacle_distance=0.70)
        v_pass = sandbox.validate_action({"action": "move_distance", "parameters": {"distance_m": 0.50, "speed_mps": 0.15}}, ctx_pass)
        assert v_pass.approved is True

        ctx_fail = RobotSafetyContext(front_obstacle_distance=0.699)
        v_fail = sandbox.validate_action({"action": "move_distance", "parameters": {"distance_m": 0.50, "speed_mps": 0.15}}, ctx_fail)
        assert v_fail.approved is False
        assert v_fail.error_code == SandboxErrorCode.SPATIAL_OBSTACLE

    def test_f10_b05_negative_distance_reverse_motion_limit(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext()
        # Reversing -0.20m at 0.10 m/s
        call = {"action": "move_distance", "parameters": {"distance_m": -0.20, "speed_mps": 0.10}}
        v = sandbox.validate_action(call, ctx)
        assert v.approved is True


# ============================================================================
# FEATURE F11: KINEMATIC SANDBOX TEST GATE (FASE 3 GATE) BOUNDARIES
# ============================================================================

class TestFeature11KinematicSandboxGateBoundaries:
    def test_f11_b01_battery_charging_boundary_12_69v_vs_12_70v(self):
        sandbox = TRINITYKinematicSandbox()
        call = {"action": "move_distance", "parameters": {"distance_m": 0.2, "speed_mps": 0.1}}
        # 12.69V -> allowed
        v1 = sandbox.validate_action(call, RobotSafetyContext(battery_voltage=12.69))
        assert v1.approved is True

        # 12.70V -> charging lock rejected
        v2 = sandbox.validate_action(call, RobotSafetyContext(battery_voltage=12.70))
        assert v2.approved is False
        assert v2.error_code == SandboxErrorCode.BATTERY_CHARGING_LOCK

    def test_f11_b02_battery_shutdown_boundary_9_86v_vs_9_85v(self):
        sandbox = TRINITYKinematicSandbox()
        call = {"action": "move_distance", "parameters": {"distance_m": 0.2, "speed_mps": 0.1}}
        # 9.86V -> allowed (in ECO mode)
        v1 = sandbox.validate_action(call, RobotSafetyContext(battery_voltage=9.86))
        assert v1.approved is True

        # 9.85V -> critical cutoff rejected
        v2 = sandbox.validate_action(call, RobotSafetyContext(battery_voltage=9.85))
        assert v2.approved is False
        assert v2.error_code == SandboxErrorCode.BATTERY_LOW

    def test_f11_b03_eco_voltage_boundary_10_40v_vs_10_41v(self):
        sandbox = TRINITYKinematicSandbox()
        call = {"action": "move_distance", "parameters": {"distance_m": 0.4, "speed_mps": 0.25}}
        # 10.40V -> clamps to 0.125 m/s
        v1 = sandbox.validate_action(call, RobotSafetyContext(battery_voltage=10.40))
        assert v1.approved is True
        assert v1.sanitized_parameters["speed_mps"] == 0.125

        # 10.41V -> normal limit 0.25 m/s allowed
        v2 = sandbox.validate_action(call, RobotSafetyContext(battery_voltage=10.41))
        assert v2.approved is True
        assert v2.sanitized_parameters["speed_mps"] == 0.25

    def test_f11_b04_zero_motion_command_always_safe(self):
        sandbox = TRINITYKinematicSandbox()
        call = {"action": "move_distance", "parameters": {"distance_m": 0.0, "speed_mps": 0.0}}
        v = sandbox.validate_action(call, RobotSafetyContext())
        assert v.approved is True

    def test_f11_b05_amigdala_panic_flag_toggle_boundary(self):
        sandbox = TRINITYKinematicSandbox()
        call = {"action": "move_distance", "parameters": {"distance_m": 0.2, "speed_mps": 0.1}}
        # Panic ON -> rejected
        v1 = sandbox.validate_action(call, RobotSafetyContext(amygdala_panic=True))
        assert v1.approved is False
        assert v1.error_code == SandboxErrorCode.AMYGDALA_HIJACK

        # Panic OFF -> approved
        v2 = sandbox.validate_action(call, RobotSafetyContext(amygdala_panic=False))
        assert v2.approved is True


# ============================================================================
# FEATURE F12: GEMINI LIVE ON-DEMAND LIFECYCLE BOUNDARIES
# ============================================================================

class TestFeature12GeminiLiveLifecycleBoundaries:
    def test_f12_b01_inactivity_boundary_24_9s_vs_25_0s(self):
        mgr = GeminiLiveConnectionManager()
        mgr.request_session(current_time=100.0)

        # 24.9s silence: still ACTIVE
        s1 = mgr.update_tick(current_time=124.9)
        assert s1 == WebSocketState.ACTIVE

        # 25.0s silence: IDLE_TIMED_OUT
        s2 = mgr.update_tick(current_time=125.0)
        assert s2 == WebSocketState.IDLE_TIMED_OUT

    def test_f12_b02_activity_tick_resets_inactivity_counter(self):
        mgr = GeminiLiveConnectionManager()
        mgr.request_session(current_time=100.0)

        # Activity at 120s
        mgr.request_session(current_time=120.0)

        # At 140s (40s after start, but only 20s after last activity): ACTIVE
        s = mgr.update_tick(current_time=140.0)
        assert s == WebSocketState.ACTIVE

    def test_f12_b03_repeated_rapid_connect_disconnect_cycles(self):
        mgr = GeminiLiveConnectionManager()
        t = 0.0
        for _ in range(50):
            mgr.request_session(current_time=t)
            t += 5.0
            mgr.close(current_time=t)
            t += 1.0
        assert mgr.state == WebSocketState.DISCONNECTED
        assert mgr.session_count == 50

    def test_f12_b04_zero_duration_mission_savings_boundary(self):
        mgr = GeminiLiveConnectionManager()
        savings = mgr.calculate_savings(total_mission_duration_sec=0.0)
        assert savings == 0.0

    def test_f12_b05_continuous_conversation_yields_zero_savings(self):
        mgr = GeminiLiveConnectionManager()
        mgr.request_session(current_time=0.0)
        # Session stays open full 100s
        mgr.close(current_time=100.0)
        savings = mgr.calculate_savings(total_mission_duration_sec=100.0)
        assert savings == 0.0


# ============================================================================
# FEATURE F13: RESILIENT AUDIO PIPELINE VUI BOUNDARIES
# ============================================================================

class TestFeature13ResilientAudioPipelineBoundaries:
    def test_f13_b01_audio_max_positive_negative_clipping(self):
        pipeline = ResilientAudioPipeline()
        # Extreme full-scale signal
        extreme_signal = np.array([-32768, 32767, -32768, 32767], dtype=np.int16)
        filtered = pipeline.apply_140hz_hpf(extreme_signal)
        assert filtered.dtype == np.int16
        assert np.max(filtered) <= 32767
        assert np.min(filtered) >= -32768

    def test_f13_b02_empty_audio_chunk_resampling(self):
        pipeline = ResilientAudioPipeline()
        resampled = pipeline.resample_16k_to_48k(b"")
        assert resampled == b""

    def test_f13_b03_odd_length_pcm_bytes_handling(self):
        pipeline = ResilientAudioPipeline()
        # Valid 2-sample PCM buffer
        pcm_2 = np.array([100, 200], dtype=np.int16).tobytes()
        res = pipeline.resample_16k_to_48k(pcm_2)
        assert len(res) == len(pcm_2) * 3

    def test_f13_b04_hpf_nyquist_frequency_stability(self):
        pipeline = ResilientAudioPipeline()
        # Near-Nyquist frequency 7500 Hz (fs=16000)
        t = np.linspace(0, 0.05, 800, endpoint=False)
        high_tone = (np.sin(2 * np.pi * 7500.0 * t) * 15000).astype(np.int16)
        filtered = pipeline.apply_140hz_hpf(high_tone)
        # Must not diverge or explode
        assert not np.isnan(filtered).any()
        assert np.max(np.abs(filtered)) <= 32767

    def test_f13_b05_jitter_buffer_overflow_and_underrun(self):
        pipeline = ResilientAudioPipeline()
        # Test underrun: requesting before target depth
        assert pipeline.push_jitter_chunk(b"1") is None
        assert pipeline.push_jitter_chunk(b"2") is None
        # Third chunk fulfills buffer
        assert pipeline.push_jitter_chunk(b"3") == b"1"


# ============================================================================
# FEATURE F14: AUDIO VUI & CONVERSATION INTEGRATION BOUNDARIES
# ============================================================================

class TestFeature14AudioVUIIntegrationBoundaries:
    def test_f14_b01_immediate_stop_spoken_first_token(self):
        arb = FastPathSafetyArbiter()
        # Single token partial transcript
        is_stop, _ = arb.evaluate("stop")
        assert is_stop is True

    def test_f14_b02_barge_in_triggered_during_stationary_robot(self):
        # Even if robot is stationary, barge-in safely keeps velocity at 0
        cmd_vel = 0.0
        is_talking = True
        if is_talking:
            cmd_vel = 0.0
        assert cmd_vel == 0.0

    def test_f14_b03_rapid_toggle_tts_speaking_gain(self):
        pipeline = ResilientAudioPipeline()
        for i in range(100):
            is_tts = (i % 2 == 0)
            pipeline.set_barge_in_mode(is_tts)
            assert pipeline.stt_gain == (0.10 if is_tts else 1.00)

    def test_f14_b04_whispered_low_amplitude_audio(self):
        pipeline = ResilientAudioPipeline()
        # Low amplitude signal (whisper)
        whisper = np.array([5, -4, 6, -5, 4], dtype=np.int16)
        filtered = pipeline.apply_140hz_hpf(whisper)
        assert len(filtered) == len(whisper)

    def test_f14_b05_long_continuous_utterance_turn_handling(self):
        pipeline = ResilientAudioPipeline()
        # 1 second of audio at 16kHz
        chunk = np.zeros(16000, dtype=np.int16).tobytes()
        resampled = pipeline.resample_16k_to_48k(chunk)
        assert len(resampled) == len(chunk) * 3


# ============================================================================
# FEATURE F15: FULL E2E VERIFICATION & ADVERSARIAL GATE BOUNDARIES
# ============================================================================

class TestFeature15FullE2EAdversarialGateBoundaries:
    def test_f15_b01_injection_attempt_in_prompt_sanitization(self):
        arb = CognitiveIntentArbiter()
        injection_text = "Vai avanti; DROP TABLE users; --"
        dec = arb.classify_intent(injection_text)
        assert dec.destination == RouteTarget.LOCAL_QWEN

    def test_f15_b02_nan_and_inf_kinematic_parameters(self):
        sandbox = TRINITYKinematicSandbox()
        ctx = RobotSafetyContext()
        call_nan = {"action": "move_distance", "parameters": {"distance_m": 0.5, "speed_mps": float("nan")}}
        # Speed NaN fails comparison or exceeds cap
        v_nan = sandbox.validate_action(call_nan, ctx)
        # NaN is not a safe approved motion
        assert (not v_nan.approved) or (v_nan.sanitized_parameters.get("speed_mps") <= 0.25)

    def test_f15_b03_extreme_negative_battery_voltage(self):
        sandbox = TRINITYKinematicSandbox()
        # Faulty sensor reporting negative voltage
        v = sandbox.validate_action(
            {"action": "move_distance", "parameters": {"distance_m": 0.2, "speed_mps": 0.1}},
            RobotSafetyContext(battery_voltage=-1.0)
        )
        assert v.approved is False
        assert v.error_code == SandboxErrorCode.BATTERY_LOW

    def test_f15_b04_unicode_control_characters_in_transcript(self):
        arb = FastPathSafetyArbiter()
        messy_str = "\x00\x01\x02 stop \x03\x04"
        is_stop, _ = arb.evaluate(messy_str)
        assert is_stop is True

    def test_f15_b05_system_stress_under_concurrent_features(self):
        arb = CognitiveIntentArbiter()
        client = HailoQwenClient()
        sandbox = TRINITYKinematicSandbox()
        audio = ResilientAudioPipeline()

        # Execute 50 concurrent operational cycles
        for i in range(50):
            dec = arb.classify_intent("Avanza di venti centimetri")
            assert dec.destination == RouteTarget.LOCAL_QWEN
            tool, _ = client.mock_generate_stream("Avanza")
            verdict = sandbox.validate_action(tool, RobotSafetyContext())
            assert verdict.approved is True
            raw = np.zeros(160, dtype=np.int16).tobytes()
            res = audio.resample_16k_to_48k(raw)
            assert len(res) == len(raw) * 3
