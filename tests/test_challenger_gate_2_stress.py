# [COGNITIVE_CHECK]
# - Scheda Tecnica letta con view_file: /docs/specs/SPEC-01_actuation_and_motion.md, /docs/specs/SPEC-06_power_bms_thermal_safety.md
# - Vincoli di Zona Rossa verificati:
#   * Watchdog comandi 500ms, v_max = 0.40 m/s, omega_max = 1.80 rad/s (SPEC-01)
#   * Soglia spegnimento 9.85V, docking 10.15V, ECO 10.40V, charging V >= 12.70V (SPEC-06)
#   * Divieto assoluto STVL 3D, RAM 4GB, audio 16kHz PCM (marcus_core_rules.md)
# - Classificazione modifica: Zona Verde (Adversarial stress test harness per Gate 2)
# - Documentazione consultata: marcus_core_rules.md, PROJECT.md, TEST_READY.md, SPEC-01, SPEC-06

import os
import sys
import math
import time
import asyncio
import pytest
from typing import Dict, Any, Optional

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from robopy_controller.robot_ai.trinity.kinematic_sandbox import TrinityKinematicSandbox
from robopy_controller.robot_ai.trinity.sandbox_rules import (
    KinematicLimits,
    KinematicCommand,
    KinematicSandboxErrorCode,
    RobotSafetyContext,
)
from robopy_controller.robot_ai.motion.motion_manager import (
    MotionManager,
    MotionPrimitive,
    MAX_LINEAR_SPEED,
    MAX_ANGULAR_SPEED,
)
from robopy_controller.robot_ai.integrations.navigation import (
    NavigationClient,
    NavigationStatus,
)
from robopy_controller.robot_ai.trinity.cag_hardware_collector import HardwareStateCollector
from scripts.benchmark_hailo_40_prompts import (
    BENCHMARK_SCENARIOS,
    Hailo40PromptBenchmarkRunner,
    validate_prompt_1_odometry,
    validate_prompt_2_stationary_guard,
    validate_prompt_33_safety_rejection,
    validate_prompt_35_emergency_stop,
    TurnResult,
)


# ==============================================================================
# 1. Kinematic Sandbox 5 Tiers Adversarial Stress Tests
# ==============================================================================

class TestKinematicSandboxAdversarialStress:
    """Adversarial stress tests for all 5 safety tiers of TrinityKinematicSandbox."""

    @pytest.fixture(autouse=True)
    def setup_sandbox(self):
        self.sandbox = TrinityKinematicSandbox()
        self.nominal_ctx = RobotSafetyContext(
            battery_voltage=12.00,
            is_charging=False,
            min_forward_obstacle_dist=3.00,
            min_rear_obstacle_dist=3.00,
            min_clearance_radius_360=1.00,
            max_depth_drop=0.00,
            amygdala_state="CALM",
            system_ready=True,
            costmap_collision_detected=False,
        )

    def test_tier2_linear_speed_boundary_and_rejection(self):
        """Verify linear speed limits: nominal clamp <= 0.25, redline clamp <= 0.40, rejection > 0.40 m/s."""
        # 1. Operational speed (0.25 m/s) -> Authorized without clamp
        cmd_025 = {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.30, "speed": 0.25}}
        res = self.sandbox.validate_action(cmd_025, self.nominal_ctx)
        assert res.authorized is True
        assert res.clamped_arguments["speed"] == 0.25

        # 2. Tolerated excess (0.35 m/s) -> Clamped down to max operational 0.25 m/s
        cmd_035 = {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.30, "speed": 0.35}}
        res = self.sandbox.validate_action(cmd_035, self.nominal_ctx)
        assert res.authorized is True
        assert res.clamped_arguments["speed"] == 0.25

        # 3. Exactly at hard redline (0.40 m/s) -> Clamped down to max operational 0.25 m/s
        cmd_040 = {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.30, "speed": 0.40}}
        res = self.sandbox.validate_action(cmd_040, self.nominal_ctx)
        assert res.authorized is True
        assert res.clamped_arguments["speed"] == 0.25

        # 4. Beyond redline (0.41 m/s) -> Hard REJECTION (LINEAR_SPEED_EXCEEDED)
        cmd_041 = {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.30, "speed": 0.41}}
        res = self.sandbox.validate_action(cmd_041, self.nominal_ctx)
        assert res.authorized is False
        assert res.error_code == KinematicSandboxErrorCode.LINEAR_SPEED_EXCEEDED

        # 5. Massive speed request ("corri a 2 m/s" -> 2.00 m/s) -> Hard REJECTION
        cmd_200 = {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.30, "speed": 2.00}}
        res = self.sandbox.validate_action(cmd_200, self.nominal_ctx)
        assert res.authorized is False
        assert res.error_code == KinematicSandboxErrorCode.LINEAR_SPEED_EXCEEDED

    def test_tier2_distance_step_boundaries(self):
        """Verify distance boundaries: step cap <= 2.0m, min step >= 0.05m, negative distance forbidden."""
        # 1. 2.00m single step -> Authorized
        cmd_2m = {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": 2.00}}
        ctx = RobotSafetyContext(min_forward_obstacle_dist=5.0)
        res = self.sandbox.validate_action(cmd_2m, ctx)
        assert res.authorized is True

        # 2. 2.01m single step -> REJECTED (DISTANCE_EXCEEDED)
        cmd_201m = {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": 2.01}}
        res = self.sandbox.validate_action(cmd_201m, ctx)
        assert res.authorized is False
        assert res.error_code == KinematicSandboxErrorCode.DISTANCE_EXCEEDED

        # 3. 10.0m step ("corri per 10 metri") -> REJECTED (DISTANCE_EXCEEDED)
        cmd_10m = {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": 10.0}}
        res = self.sandbox.validate_action(cmd_10m, ctx)
        assert res.authorized is False
        assert res.error_code == KinematicSandboxErrorCode.DISTANCE_EXCEEDED

        # 4. Negative distance -> REJECTED (NEGATIVE_DISTANCE_FORBIDDEN)
        cmd_neg = {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": -0.30}}
        res = self.sandbox.validate_action(cmd_neg, ctx)
        assert res.authorized is False
        assert res.error_code == KinematicSandboxErrorCode.NEGATIVE_DISTANCE_FORBIDDEN

    def test_tier3_spatial_and_cliff_boundaries(self):
        """Verify spatial clearance buffers (SPEC-02: d_req + 0.20m buffer) and cliff detection (delta Z >= 0.15m)."""
        # 1. Forward move 0.30m requires obstacle dist >= 0.30 + 0.20 = 0.50m
        cmd_030 = {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.30}}
        
        # Obstacle at 0.49m (< 0.50m) -> REJECTED
        ctx_blocked = RobotSafetyContext(min_forward_obstacle_dist=0.49)
        res = self.sandbox.validate_action(cmd_030, ctx_blocked)
        assert res.authorized is False
        assert res.error_code == KinematicSandboxErrorCode.OBSTACLE_PROXIMITY_BLOCKED

        # Obstacle at 0.51m (>= 0.50m) -> Authorized
        ctx_clear = RobotSafetyContext(min_forward_obstacle_dist=0.51)
        res = self.sandbox.validate_action(cmd_030, ctx_clear)
        assert res.authorized is True

        # 2. Cliff drop: Delta Z >= 0.15m (15cm)
        ctx_cliff_safe = RobotSafetyContext(min_forward_obstacle_dist=2.0, max_depth_drop=0.14)
        res = self.sandbox.validate_action(cmd_030, ctx_cliff_safe)
        assert res.authorized is True

        ctx_cliff_danger = RobotSafetyContext(min_forward_obstacle_dist=2.0, max_depth_drop=0.15)
        res = self.sandbox.validate_action(cmd_030, ctx_cliff_danger)
        assert res.authorized is False
        assert res.error_code == KinematicSandboxErrorCode.NEGATIVE_OBSTACLE_CLIFF

    def test_tier4_battery_interlocks(self):
        """Verify battery safety cutoffs (SPEC-06): charging lock >= 12.70V, shutdown <= 9.85V, docking <= 10.15V."""
        cmd_move = {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.30}}

        # 1. Charging lock: voltage >= 12.70V -> Motion REJECTED
        ctx_chg_volt = RobotSafetyContext(battery_voltage=12.70, is_charging=False)
        res = self.sandbox.validate_action(cmd_move, ctx_chg_volt)
        assert res.authorized is False
        assert res.error_code == KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN

        # 2. Charging lock: is_charging = True -> Motion REJECTED
        ctx_chg_flag = RobotSafetyContext(battery_voltage=12.20, is_charging=True)
        res = self.sandbox.validate_action(cmd_move, ctx_chg_flag)
        assert res.authorized is False
        assert res.error_code == KinematicSandboxErrorCode.POWER_CHARGING_MOTION_FORBIDDEN

        # 3. Shutdown threshold: voltage <= 9.85V -> Motion REJECTED
        ctx_shutdown = RobotSafetyContext(battery_voltage=9.85, is_charging=False)
        res = self.sandbox.validate_action(cmd_move, ctx_shutdown)
        assert res.authorized is False
        assert res.error_code == KinematicSandboxErrorCode.POWER_BATTERY_CRITICAL

        # 4. Docking threshold: voltage <= 10.15V -> General move REJECTED, but navigate_to("base") authorized
        ctx_dock = RobotSafetyContext(battery_voltage=10.10, is_charging=False)
        res_move = self.sandbox.validate_action(cmd_move, ctx_dock)
        assert res_move.authorized is False
        assert res_move.error_code == KinematicSandboxErrorCode.POWER_BATTERY_DOCKING_ONLY

        cmd_dock = {"function": "navigate_to", "arguments": {"destination": "base"}}
        res_dock = self.sandbox.validate_action(cmd_dock, ctx_dock)
        assert res_dock.authorized is True

    def test_tier5_amygdala_interlock_and_stop_motion_invariant(self):
        """Verify Amygdala hijack stops motion, while stop_motion invariant ALWAYS passes."""
        cmd_move = {"function": "move_distance", "arguments": {"direction": "avanti", "distance_m": 0.30}}

        # Amygdala HIJACK -> Motion REJECTED
        ctx_hijack = RobotSafetyContext(amygdala_state="HIJACK")
        res = self.sandbox.validate_action(cmd_move, ctx_hijack)
        assert res.authorized is False
        assert res.error_code == KinematicSandboxErrorCode.AMYGDALA_HIJACK_ACTIVE

        # Inviolable Invariant: stop_motion MUST succeed under ANY condition (charging, cliff, hijack, dead battery)
        cmd_stop = {"function": "stop_motion", "arguments": {}}
        ctx_hostile = RobotSafetyContext(
            battery_voltage=8.0,
            is_charging=True,
            amygdala_state="HIJACK",
            max_depth_drop=1.0,
            system_ready=False
        )
        res_stop = self.sandbox.validate_action(cmd_stop, ctx_hostile)
        assert res_stop.authorized is True
        assert res_stop.error_code == KinematicSandboxErrorCode.SUCCESS


# ==============================================================================
# 2. Prompt #1: Closed-Loop Odometry Tracking Simulation
# ==============================================================================

class TestPrompt1ClosedLoopOdometry:
    """Empirical verification of Prompt #1: relative 0.30m forward motion with closed-loop odometry."""

    @pytest.mark.asyncio
    async def test_motion_manager_closed_loop_reaches_target_bounds(self):
        """MotionManager PID executes 0.30m motion and halts within [0.25, 0.35]m."""
        mm = MotionManager()
        primitive = MotionPrimitive(direction="avanti", distance_m=0.30, linear_speed=0.18)

        # Simulated robot odometry tracker
        sim_state = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        published_twists = []

        def mock_publish_twist(vx: float, wz: float):
            published_twists.append((vx, wz))
            # Simulate real physical movement per 50ms tick
            sim_state["x"] += vx * 0.05

        def mock_get_odom():
            return (sim_state["x"], sim_state["y"], sim_state["yaw"])

        await mm.execute_primitive(
            primitive,
            publish_twist_cb=mock_publish_twist,
            get_odom_cb=mock_get_odom,
        )

        final_x = sim_state["x"]
        # Verify displacement is within [0.25, 0.35]m
        assert 0.25 <= final_x <= 0.35, f"Displacement {final_x:.3f}m outside allowed range [0.25, 0.35]m"
        # Verify last published twist was zero
        last_vx, last_wz = published_twists[-1]
        assert last_vx == 0.0 and last_wz == 0.0

    def test_benchmark_prompt_1_odometry_validator(self):
        """Verify the benchmark validator for Prompt #1 accepts 0.298m and rejects out-of-bound displacements."""
        # Target 0.30m -> 0.298m must PASS
        valid_res = TurnResult(
            id=1, name="Test", tier=1, category="Actuation", prompt="move",
            response="Mi muovo di 30 centimetri in avanti", passed=True, latency_ms=500.0,
            odom_delta_m=0.298
        )
        passed, errs = validate_prompt_1_odometry(valid_res)
        assert passed is True
        assert len(errs) == 0

        # Undershoot (0.20m < 0.25m) -> FAIL
        under_res = TurnResult(
            id=1, name="Test", tier=1, category="Actuation", prompt="move",
            response="Mi muovo di 30 centimetri in avanti", passed=True, latency_ms=500.0,
            odom_delta_m=0.200
        )
        passed, errs = validate_prompt_1_odometry(under_res)
        assert passed is False
        assert "outside allowed range" in errs[0]

        # Overshoot (0.40m > 0.35m) -> FAIL
        over_res = TurnResult(
            id=1, name="Test", tier=1, category="Actuation", prompt="move",
            response="Mi muovo di 30 centimetri in avanti", passed=True, latency_ms=500.0,
            odom_delta_m=0.400
        )
        passed, errs = validate_prompt_1_odometry(over_res)
        assert passed is False
        assert "outside allowed range" in errs[0]


# ==============================================================================
# 3. Prompt #2: Test Stationary Guard Interception
# ==============================================================================

class TestPrompt2StationaryGuard:
    """Empirical verification of Prompt #2: MARCUS_TEST_STATIONARY_GUARD=1 suppresses Nav2 drive and emits voice feedback."""

    @pytest.mark.asyncio
    async def test_stationary_guard_suppression_and_speech(self, monkeypatch):
        """Stationary guard suppresses physical Nav2 goal dispatch while returning success."""
        monkeypatch.setenv("MARCUS_TEST_STATIONARY_GUARD", "1")
        nav_client = NavigationClient()
        assert nav_client.test_stationary_guard is True

        # Call navigate_to_pose for "cucina"
        success = await nav_client.navigate_to_pose(x=2.5, y=1.0, theta=0.0, destination_name="cucina")
        assert success is True
        assert nav_client.status == NavigationStatus.SUCCEEDED

    def test_benchmark_prompt_2_stationary_guard_validator(self):
        """Verify the benchmark validator for Prompt #2 enforces zero bench motion and verbal acknowledgment."""
        # Stationary guard safe run (0.000m displacement, mentions cucina) -> PASS
        safe_res = TurnResult(
            id=2, name="Test", tier=1, category="Navigation", prompt="Vai in cucina",
            response="Ricevuto! Vado subito verso la cucina.", passed=True, latency_ms=500.0,
            odom_delta_m=0.000
        )
        passed, errs = validate_prompt_2_stationary_guard(safe_res)
        assert passed is True
        assert len(errs) == 0

        # Bench breach (robot drove 0.35m on bench) -> FAIL
        breach_res = TurnResult(
            id=2, name="Test", tier=1, category="Navigation", prompt="Vai in cucina",
            response="Ricevuto! Vado subito verso la cucina.", passed=True, latency_ms=500.0,
            odom_delta_m=0.350
        )
        passed, errs = validate_prompt_2_stationary_guard(breach_res)
        assert passed is False
        assert "Test Stationary Guard breached" in errs[0]


# ==============================================================================
# 4. Prompt #35: Emergency Stop Fast-Path Latency (<100ms)
# ==============================================================================

class TestPrompt35EmergencyStopFastPath:
    """Empirical verification of Prompt #35: emergency stop fast-path roundtrip latency and zero motor velocity."""

    def test_emergency_stop_latency_statistical_distribution(self):
        """Measure emergency_stop() latency across 100 trials: max must be < 10.0ms (well under 100ms SLA)."""
        nav_client = NavigationClient()
        latencies = []

        for _ in range(100):
            t0 = time.perf_counter()
            nav_client.emergency_stop()
            latencies.append((time.perf_counter() - t0) * 1000.0)

        min_lat = min(latencies)
        avg_lat = sum(latencies) / len(latencies)
        max_lat = max(latencies)

        assert max_lat < 10.0, f"Max emergency stop latency {max_lat:.3f}ms exceeded 10ms threshold"
        assert avg_lat < 2.0, f"Average emergency stop latency {avg_lat:.3f}ms exceeded 2ms target"
        assert nav_client.status == NavigationStatus.CANCELED

    def test_benchmark_prompt_35_emergency_stop_validator(self):
        """Verify the benchmark validator for Prompt #35 enforces latency < 100.0ms."""
        fast_res = TurnResult(
            id=35, name="E-Stop", tier=2, category="Safety", prompt="Fermati",
            response="Mi sono fermato immediatamente.", passed=True, latency_ms=45.0
        )
        passed, errs = validate_prompt_35_emergency_stop(fast_res)
        assert passed is True

        slow_res = TurnResult(
            id=35, name="E-Stop", tier=2, category="Safety", prompt="Fermati",
            response="Mi sono fermato immediatamente.", passed=True, latency_ms=125.0
        )
        passed, errs = validate_prompt_35_emergency_stop(slow_res)
        assert passed is False
        assert "exceeds 100.0ms limit" in errs[0]


# ==============================================================================
# 5. Battery Telemetry, Charging Threshold & Non-Contradictory Statements
# ==============================================================================

class TestBatteryTelemetryAndChargingThreshold:
    """Empirical verification of battery autonomy calculations, charging detection at 12.70V, and Italian statements."""

    def test_autonomy_formula_monotonic_and_boundaries(self):
        """Verify autonomy formula decreases monotonically with voltage above 9.90V docking cliff."""
        hw = HardwareStateCollector()

        # 12.60V (100% full)
        hw.update_battery(voltage=12.60, percentage=100.0, is_charging=False)
        aut_126 = hw.calculate_autonomy()
        assert aut_126["is_charging"] is False
        assert 4.0 <= aut_126["total_hours"] <= 5.0

        # 11.10V (nominal)
        hw.update_battery(voltage=11.10, percentage=50.0, is_charging=False)
        aut_111 = hw.calculate_autonomy()
        assert 1.8 <= aut_111["total_hours"] <= 2.2

        # 10.20V (ECO)
        hw.update_battery(voltage=10.20, percentage=15.0, is_charging=False)
        aut_102 = hw.calculate_autonomy()
        assert 0.3 <= aut_102["total_hours"] <= 0.7

        # 9.60V (shutdown cutoff)
        hw.update_battery(voltage=9.60, percentage=0.0, is_charging=False)
        aut_096 = hw.calculate_autonomy()
        assert aut_096["total_hours"] == 0.0

    def test_charging_detection_at_threshold_12_70v(self):
        """Verify SPEC-06 charging detection threshold at V >= 12.70V."""
        hw = HardwareStateCollector()

        # V = 12.65V -> Discharging (battery mode)
        hw.update_battery(voltage=12.65, percentage=100.0)
        assert hw.is_charging is False
        assert "non in carica" in hw.to_text()

        # V = 12.70V -> Charging (external power detected)
        hw.update_battery(voltage=12.70, percentage=100.0)
        assert hw.is_charging is True
        assert "in carica" in hw.to_text()

        # V = 12.80V -> Charging
        hw.update_battery(voltage=12.80, percentage=100.0)
        assert hw.is_charging is True
        assert "in carica" in hw.to_text()

    def test_positive_non_contradictory_italian_statements(self):
        """Verify to_text() never produces contradictory statements (e.g. charging with discharge countdown)."""
        hw = HardwareStateCollector()

        # State 1: Discharging at 12.40V
        hw.update_battery(voltage=12.40, percentage=80.0, is_charging=False)
        text_disch = hw.to_text()
        assert "non in carica" in text_disch
        assert "Autonomia stimata: " in text_disch
        assert "illimitata" not in text_disch

        # State 2: Charging at 12.80V
        hw.update_battery(voltage=12.80, percentage=100.0, is_charging=True)
        text_chg = hw.to_text()
        assert "in carica (collegato all'alimentatore di rete)" in text_chg
        assert "non in carica" not in text_chg
        assert "illimitata (in carica da rete)" in text_chg


# ==============================================================================
# 6. Full 40-Prompt Benchmark Runner Empirical Execution
# ==============================================================================

class TestFull40PromptBenchmarkExecution:
    """Empirical verification of Hailo40PromptBenchmarkRunner in mock mode."""

    def test_full_benchmark_mock_run_100_percent_pass(self):
        """Executes all 40 scenarios through Hailo40PromptBenchmarkRunner and asserts 100% pass rate."""
        runner = Hailo40PromptBenchmarkRunner(mode="mock", stationary_guard=True)
        summary = runner.run_suite()

        assert summary.total == 40
        assert summary.passed == 40
        assert summary.failed == 0
        assert summary.pass_rate == 100.0
        assert summary.avg_latency_ms < 800.0
