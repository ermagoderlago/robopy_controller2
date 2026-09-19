"""
Challenger Adversarial Stress & Verification Test Suite - Milestone 3
=====================================================================
Target Modules:
  - robopy_controller.robot_ai.core.mapping_state_machine (MappingStateMachine)
  - robopy_controller.nodes.map_export_optimizer (SLAMOptimizationExporter, MapExportOptimizerNode)

Challenge Dimensions:
  1. State Machine timer boundaries:
     - 119.99s (no reminder) vs 120.00s (reminder emitted, state REMINDER_SENT)
     - 299.99s (no abort) vs 300.00s (abort emitted, state STANDBY_ABORTED, motors locked)
     - Large time jumps (e.g. advance_time(600.0), advance_time(86400.0)) in single call
  2. Concurrent destructive action gate:
     - 29.99s (pending) vs 30.00s (timeout, cancelled)
     - Strict phrase discrimination: 'procedi pure' must NOT confirm; 'sì, confermo' MUST confirm
     - Concurrent operation with active mapping / navigation
  3. SLAM Optimizer RAM Delta boundaries:
     - 79.0 MB / 79.9 MB (must pass)
     - 80.0 MB / 80.1 MB (must trigger alarm/fail)
     - Zero / negative delta (must pass)
  4. Battery Anti-Sag moving average:
     - Transient dip to 9.20V for 1-2 samples (filtered voltage stays > 9.90V -> must pass)
     - Sustained drop to 9.85V for 20 samples (filtered voltage < 9.90V -> must inhibit & trigger docking)
     - Charging voltage >= 12.70V override
  5. Atomic SSD map export:
     - Read-only filesystem error handling (simulated and OSError EROFS)
     - Unauthorized path rejection (e.g. /home/robopy/map, /tmp/map, traversal)
  6. Adversarial Logic & Security Challenge (Empirical Bug Hunter):
     - Negated confirmation phrases ('non confermo') must NOT confirm destructive action (BUG-HRI-01)
     - Path traversal via relative dot-dots ('/mnt/ssd/maps/../../home/robopy/map') must NOT bypass SSD check (VULN-SSD-01)
     - Map name directory traversal ('../../escape') must NOT escape destination folder (VULN-SSD-02)
"""

import errno
import math
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from robopy_controller.robot_ai.core.mapping_state_machine import MappingStateMachine
from robopy_controller.nodes.map_export_optimizer import (
    SLAMOptimizationExporter,
    MapExportOptimizerNode,
)
import robopy_controller.nodes.map_export_optimizer as meo


# ============================================================================
# 1. State Machine Timer Boundaries (119.99s vs 120s, 299.99s vs 300s, jumps)
# ============================================================================
class TestChallengerGroup1TimerBoundaries:
    """Adversarial stress-testing of HRI confirmation and silence timers."""

    def test_exact_timer_boundary_119_99s_vs_120_00s(self):
        """119.99s must NOT emit reminder; exactly 120.00s MUST emit reminder."""
        fsm = MappingStateMachine()
        prompt = fsm.request_mapping("laboratorio")
        assert fsm.state == MappingStateMachine.STATE_AWAITING_CONFIRMATION
        assert fsm.reminder_dispatched is False
        assert fsm.motors_allowed is False

        # Advance to 119.99s
        res_119_99 = fsm.advance_time(119.99)
        assert res_119_99 is None, "Reminder emitted prematurely at 119.99s!"
        assert fsm.state == MappingStateMachine.STATE_AWAITING_CONFIRMATION
        assert fsm.reminder_dispatched is False
        assert fsm.motors_allowed is False

        # Advance remaining 0.01s to reach exactly 120.00s
        res_120_00 = fsm.advance_time(0.01)
        assert res_120_00 is not None, "Reminder failed to fire at exactly 120.00s!"
        assert "Sollecito" in res_120_00
        assert fsm.state == MappingStateMachine.STATE_REMINDER_SENT
        assert fsm.reminder_dispatched is True
        assert fsm.motors_allowed is False

    def test_exact_timer_boundary_299_99s_vs_300_00s(self):
        """299.99s must NOT abort; exactly 300.00s MUST abort to STANDBY_ABORTED with motors locked."""
        fsm = MappingStateMachine()
        fsm.request_mapping("magazzino")

        # Reach 120.0s reminder
        fsm.advance_time(120.0)
        assert fsm.state == MappingStateMachine.STATE_REMINDER_SENT

        # Advance to 299.99s total (120.0 + 179.99 = 299.99)
        res_299_99 = fsm.advance_time(179.99)
        assert res_299_99 is None, "Abort triggered prematurely at 299.99s!"
        assert fsm.state == MappingStateMachine.STATE_REMINDER_SENT
        assert fsm.abort_dispatched is False
        assert fsm.motors_allowed is False

        # Advance remaining 0.01s to reach exactly 300.00s
        res_300_00 = fsm.advance_time(0.01)
        assert res_300_00 is not None, "Abort failed to fire at exactly 300.00s!"
        assert "300 secondi" in res_300_00
        assert "standby" in res_300_00.lower()
        assert fsm.state == MappingStateMachine.STATE_STANDBY_ABORTED
        assert fsm.abort_dispatched is True
        assert fsm.motors_allowed is False

    def test_large_time_jump_600s_single_call(self):
        """A sudden large time jump of 600.0s in a single call must safely transition to STANDBY_ABORTED."""
        fsm = MappingStateMachine()
        fsm.request_mapping("reception")
        assert fsm.state == MappingStateMachine.STATE_AWAITING_CONFIRMATION

        # Single large jump of 600.0s
        res = fsm.advance_time(600.0)
        assert res is not None
        assert fsm.state == MappingStateMachine.STATE_STANDBY_ABORTED
        assert fsm.motors_allowed is False
        assert fsm.reminder_dispatched is True
        assert fsm.abort_dispatched is True
        # Both prompts were logged
        assert any("Sollecito" in p for p in fsm.voice_prompts_emitted)
        assert any("300 secondi" in p for p in fsm.voice_prompts_emitted)

    def test_ultra_large_time_jump_86400s_and_idempotency(self):
        """24-hour jump (86400s) followed by subsequent advances must remain safely in STANDBY_ABORTED."""
        fsm = MappingStateMachine()
        fsm.request_mapping("tetto")
        fsm.advance_time(86400.0)
        assert fsm.state == MappingStateMachine.STATE_STANDBY_ABORTED
        assert fsm.motors_allowed is False

        # Subsequent time advances must not alter state or re-emit prompts
        prompts_before = len(fsm.voice_prompts_emitted)
        res_after = fsm.advance_time(3600.0)
        assert res_after is None
        assert fsm.state == MappingStateMachine.STATE_STANDBY_ABORTED
        assert fsm.motors_allowed is False
        assert len(fsm.voice_prompts_emitted) == prompts_before

    def test_high_frequency_micro_ticks_floating_point_accumulation(self):
        """Advancing in 10ms micro-ticks (100Hz) evaluates floating point accumulation.
        At 12000 ticks (120.0s), reminder fires. At tick 30001 (300.01s), abort fires.
        (Documenting the IEEE 754 1.28e-10 float rounding deficit at 30,000 additions).
        """
        fsm = MappingStateMachine()
        fsm.request_mapping("corridoio_nord")

        tick_dt = 0.01  # 10ms

        # Run up to 119.99s (11999 ticks)
        for _ in range(11999):
            p = fsm.advance_time(tick_dt)
            assert p is None
        assert fsm.state == MappingStateMachine.STATE_AWAITING_CONFIRMATION

        # Tick 12000 -> 120.00s
        p_reminder = fsm.advance_time(tick_dt)
        assert p_reminder is not None
        assert fsm.state == MappingStateMachine.STATE_REMINDER_SENT

        # Run up to 299.99s (17999 additional ticks)
        for _ in range(17999):
            p = fsm.advance_time(tick_dt)
            assert p is None
        assert fsm.state == MappingStateMachine.STATE_REMINDER_SENT

        # Tick 30000: float sum is 299.99999999987216 (< 300.0 by 1.28e-10)
        p_tick_30000 = fsm.advance_time(tick_dt)
        # Tick 30001: float sum reaches 300.00999999987214 (>= 300.0)
        p_tick_30001 = fsm.advance_time(tick_dt)

        abort_fired = (p_tick_30000 is not None) or (p_tick_30001 is not None)
        assert abort_fired is True, "Abort failed to fire within 10ms of 300s window!"
        assert fsm.state == MappingStateMachine.STATE_STANDBY_ABORTED
        assert fsm.motors_allowed is False


# ============================================================================
# 2. Concurrent Destructive Action Gate
# ============================================================================
class TestChallengerGroup2DestructiveActionGate:
    """Stress-testing of destructive confirmation gate boundaries, phrase discrimination, and concurrency."""

    def test_destructive_gate_29_99s_pending_vs_30_00s_cancelled(self):
        """29.99s must keep destructive action pending; 30.00s must cancel it."""
        fsm = MappingStateMachine()
        fsm.request_destructive_action("mappa_produzione")
        assert fsm.destructive_pending is True
        assert fsm.destructive_confirmed is False

        # At 29.99s: still pending
        res_29_99 = fsm.advance_time(29.99)
        assert res_29_99 is None
        assert fsm.destructive_pending is True
        assert fsm.destructive_confirmed is False

        # At 30.00s (+0.01s): cancelled due to timeout
        res_30_00 = fsm.advance_time(0.01)
        assert res_30_00 is not None
        assert "Tempo scaduto" in res_30_00
        assert fsm.destructive_pending is False
        assert fsm.destructive_confirmed is False

    def test_strict_phrase_discrimination_procedi_pure_rejected(self):
        """'procedi pure' must NOT confirm destructive action (must remain pending)."""
        fsm = MappingStateMachine()
        fsm.request_destructive_action("mappa_uffici")
        assert fsm.destructive_pending is True

        # Utterance: 'procedi pure'
        msg, ok = fsm.process_voice_input("procedi pure")
        assert ok is False, "'procedi pure' erroneously confirmed a destructive action!"
        assert fsm.destructive_confirmed is False
        assert fsm.destructive_pending is True

        # Other non-confirming variations
        for invalid_phrase in ["procedi", "sì, procedi", "vai avanti", "avvia i motori", "ok procedi"]:
            msg_inv, ok_inv = fsm.process_voice_input(invalid_phrase)
            assert ok_inv is False, f"Phrase '{invalid_phrase}' erroneously confirmed destructive action!"
            assert fsm.destructive_confirmed is False

    def test_strict_phrase_discrimination_si_confermo_confirmed(self):
        """'sì, confermo' and 'si, confermo' MUST successfully confirm destructive action."""
        # Case A: 'sì, confermo' with accent
        fsm_a = MappingStateMachine()
        fsm_a.request_destructive_action("area_a")
        msg_a, ok_a = fsm_a.process_voice_input("sì, confermo")
        assert ok_a is True
        assert fsm_a.destructive_confirmed is True
        assert fsm_a.destructive_pending is False
        assert "Conferma registrata" in msg_a

        # Case B: 'si, confermo' without accent
        fsm_b = MappingStateMachine()
        fsm_b.request_destructive_action("area_b")
        msg_b, ok_b = fsm_b.process_voice_input("si, confermo")
        assert ok_b is True
        assert fsm_b.destructive_confirmed is True
        assert fsm_b.destructive_pending is False

        # Case C: 'sì, confermo l'eliminazione della mappa' (natural phrasing)
        fsm_c = MappingStateMachine()
        fsm_c.request_destructive_action("area_c")
        msg_c, ok_c = fsm_c.process_voice_input("sì, confermo l'eliminazione della mappa")
        assert ok_c is True
        assert fsm_c.destructive_confirmed is True
        assert fsm_c.destructive_pending is False

    def test_explicit_rejection_phrase_cancels_destructive_gate(self):
        """'no, annulla' or 'annulla' immediately cancels destructive gate before timeout."""
        fsm = MappingStateMachine()
        fsm.request_destructive_action("mappa_critica")
        assert fsm.destructive_pending is True

        msg, ok = fsm.process_voice_input("no, annulla subito")
        assert ok is False
        assert fsm.destructive_confirmed is False
        assert fsm.destructive_pending is False
        assert "annullata" in msg.lower()

    def test_concurrent_operation_with_active_mapping_and_motion(self):
        """Destructive action gate running concurrently while robot is actively mapping.
        Timeout or confirmation of destructive gate must NEVER disturb MAPPING_ACTIVE or lock motors.
        """
        fsm = MappingStateMachine()
        fsm.request_mapping("reparto_assemblaggio")
        fsm.process_voice_input("sì, procedi")
        assert fsm.state == MappingStateMachine.STATE_MAPPING_ACTIVE
        assert fsm.motors_allowed is True

        # Operator triggers destructive action while mapping is in progress
        fsm.request_destructive_action("vecchia_area")
        assert fsm.destructive_pending is True
        assert fsm.state == MappingStateMachine.STATE_MAPPING_ACTIVE
        assert fsm.motors_allowed is True

        # Mapping continues for 29.99s
        fsm.advance_time(29.99)
        assert fsm.destructive_pending is True
        assert fsm.state == MappingStateMachine.STATE_MAPPING_ACTIVE
        assert fsm.motors_allowed is True

        # Destructive times out at 30.00s
        timeout_prompt = fsm.advance_time(0.01)
        assert timeout_prompt is not None
        assert "Tempo scaduto" in timeout_prompt
        assert fsm.destructive_pending is False
        assert fsm.destructive_confirmed is False
        # Motors and mapping state remain strictly active
        assert fsm.state == MappingStateMachine.STATE_MAPPING_ACTIVE
        assert fsm.motors_allowed is True

        # Now test confirmation while mapping
        fsm.request_destructive_action("altra_mappa")
        assert fsm.destructive_pending is True
        msg_conf, ok_conf = fsm.process_voice_input("sì, confermo")
        assert ok_conf is True
        assert fsm.destructive_confirmed is True
        assert fsm.destructive_pending is False
        assert fsm.state == MappingStateMachine.STATE_MAPPING_ACTIVE
        assert fsm.motors_allowed is True


# ============================================================================
# 3. SLAM Optimizer RAM Delta Boundaries
# ============================================================================
class TestChallengerGroup3RAMDeltaBoundaries:
    """Stress-testing RAM delta budget: 79.0/79.9 pass, 80.0/80.1 fail, zero/negative pass."""

    def test_ram_delta_79_0_and_79_9_pass(self):
        """RAM delta 79.0 MB and 79.9 MB are strictly below 80.0 MB threshold and must PASS."""
        exporter = SLAMOptimizationExporter()
        for _ in range(20):
            exporter.update_battery_voltage(11.10)

        # 79.0 MB
        ok_79_0, msg_79_0 = exporter.trigger_global_optimization(simulate_ram_usage_mb=79.0)
        assert ok_79_0 is True, f"RAM delta 79.0 MB erroneously rejected: {msg_79_0}"
        assert "converged" in msg_79_0
        assert exporter.last_ram_delta_mb == pytest.approx(79.0)

        # 79.9 MB
        ok_79_9, msg_79_9 = exporter.trigger_global_optimization(simulate_ram_usage_mb=79.9)
        assert ok_79_9 is True, f"RAM delta 79.9 MB erroneously rejected: {msg_79_9}"
        assert "converged" in msg_79_9
        assert exporter.last_ram_delta_mb == pytest.approx(79.9)

    def test_ram_delta_80_0_and_80_1_fail_with_alarm(self):
        """RAM delta 80.0 MB and 80.1 MB hit or exceed limit and MUST FAIL with alarm."""
        exporter = SLAMOptimizationExporter()
        for _ in range(20):
            exporter.update_battery_voltage(11.10)

        # Exact boundary: 80.0 MB
        ok_80_0, msg_80_0 = exporter.trigger_global_optimization(simulate_ram_usage_mb=80.0)
        assert ok_80_0 is False, "RAM delta 80.0 MB erroneously allowed!"
        assert "ALARM" in msg_80_0
        assert "80.0 MB" in msg_80_0

        # Just above boundary: 80.1 MB
        ok_80_1, msg_80_1 = exporter.trigger_global_optimization(simulate_ram_usage_mb=80.1)
        assert ok_80_1 is False, "RAM delta 80.1 MB erroneously allowed!"
        assert "ALARM" in msg_80_1
        assert "80.1 MB" in msg_80_1

    def test_ram_delta_zero_and_negative_pass(self):
        """Zero RAM delta (0.0 MB) and negative delta (post-GC memory drop) MUST PASS."""
        exporter = SLAMOptimizationExporter()
        for _ in range(20):
            exporter.update_battery_voltage(11.10)

        # Zero delta
        ok_zero, msg_zero = exporter.trigger_global_optimization(simulate_ram_usage_mb=0.0)
        assert ok_zero is True
        assert "converged" in msg_zero

        # Negative delta (e.g. garbage collection freed memory during BA)
        ok_neg, msg_neg = exporter.trigger_global_optimization(simulate_ram_usage_mb=-15.4)
        assert ok_neg is True
        assert "converged" in msg_neg

    def test_ram_delta_boundary_epsilon(self):
        """Epsilon boundary: 79.999 MB passes, 80.0001 MB fails."""
        exporter = SLAMOptimizationExporter()
        for _ in range(20):
            exporter.update_battery_voltage(11.10)

        ok_pass, _ = exporter.trigger_global_optimization(simulate_ram_usage_mb=79.999)
        assert ok_pass is True

        ok_fail, msg_fail = exporter.trigger_global_optimization(simulate_ram_usage_mb=80.0001)
        assert ok_fail is False
        assert "ALARM" in msg_fail


# ============================================================================
# 4. Battery Anti-Sag Moving Average
# ============================================================================
class TestChallengerGroup4BatteryAntiSag:
    """Stress-testing battery moving average filter: transient dips vs sustained drop vs charging override."""

    def test_transient_motor_kick_sag_1_and_2_samples_pass(self):
        """Transient motor kick sag to 9.20V for 1 or 2 samples: filtered voltage stays > 9.90V -> MUST PASS."""
        exporter = SLAMOptimizationExporter()

        # Prime with 19 samples at nominal 11.10V
        for _ in range(19):
            exporter.update_battery_voltage(11.10)

        # 1-sample transient dip to 9.20V (due to high motor torque burst)
        v_filt_1 = exporter.update_battery_voltage(9.20)
        # Expected: (19 * 11.10 + 9.20) / 20 = 220.1 / 20 = 11.005V
        assert v_filt_1 == pytest.approx(11.005, abs=1e-3)
        assert v_filt_1 > 9.90
        ok_1, msg_1 = exporter.check_battery_safety()
        assert ok_1 is True
        assert "adequate" in msg_1

        # 2nd consecutive sample at 9.20V
        v_filt_2 = exporter.update_battery_voltage(9.20)
        # Buffer has eighteen 11.10V and two 9.20V: (18 * 11.10 + 2 * 9.20) / 20 = 218.2 / 20 = 10.91V
        assert v_filt_2 == pytest.approx(10.91, abs=1e-3)
        assert v_filt_2 > 9.90
        ok_2, msg_2 = exporter.check_battery_safety()
        assert ok_2 is True

    def test_sustained_drop_to_9_85v_20_samples_inhibits_and_triggers_docking(self):
        """Sustained drop to 9.85V across all 20 samples: filtered voltage < 9.90V -> INHIBITS & TRIGGERS DOCKING."""
        exporter = SLAMOptimizationExporter()

        # Feed 20 consecutive samples of 9.85V
        for _ in range(20):
            exporter.update_battery_voltage(9.85)

        v_filt = exporter.get_filtered_voltage()
        assert v_filt == pytest.approx(9.85, abs=1e-3)
        assert v_filt < 9.90

        # Safety check MUST inhibit
        ok, msg = exporter.check_battery_safety()
        assert ok is False
        assert "INHIBITED_BATTERY_LOW" in msg
        assert "9.85V" in msg
        assert "9.9" in msg

        # Optimization call MUST also be inhibited
        opt_ok, opt_msg = exporter.trigger_global_optimization()
        assert opt_ok is False
        assert "INHIBITED_BATTERY_LOW" in opt_msg

    def test_charging_voltage_override_12_70v(self):
        """When robot is docked / charging (voltage >= 12.70V), safety check overrides and authorizes optimization."""
        exporter = SLAMOptimizationExporter()

        # Case A: Exact threshold 12.70V
        for _ in range(20):
            exporter.update_battery_voltage(12.70)
        ok_a, msg_a = exporter.check_battery_safety()
        assert ok_a is True
        assert "charging" in msg_a.lower()

        # Case B: High charging voltage 12.85V
        for _ in range(20):
            exporter.update_battery_voltage(12.85)
        ok_b, msg_b = exporter.check_battery_safety()
        assert ok_b is True
        assert "charging" in msg_b.lower()

    def test_node_level_triggers_docking_on_low_battery_service_call(self):
        """At ROS 2 node level, if battery is low (<9.90V), optimize service must trigger docking publisher."""
        # Inject mock Bool in module namespace for execution without native rclpy
        orig_bool = getattr(meo, "Bool", None)
        mock_bool_cls = MagicMock()
        meo.Bool = mock_bool_cls

        try:
            node = object.__new__(MapExportOptimizerNode)
            node.exporter = SLAMOptimizationExporter()
            node.get_logger = MagicMock()
            node.pub_docking = MagicMock()

            # Feed 20 low battery samples
            for _ in range(20):
                node.exporter.update_battery_voltage(9.80)

            req = MagicMock()
            resp = MagicMock()

            result_resp = node._handle_optimize_and_export(req, resp)
            assert result_resp.success is False
            assert "INHIBITED_BATTERY_LOW" in result_resp.message

            # Check that pub_docking.publish was invoked with dock_msg.data = True
            assert node.pub_docking.publish.call_count == 1
        finally:
            if orig_bool is not None:
                meo.Bool = orig_bool

    def test_noise_and_invalid_telemetry_rejection(self):
        """NaN, negative, or near-zero voltages must be discarded by moving average buffer."""
        exporter = SLAMOptimizationExporter()
        for _ in range(20):
            exporter.update_battery_voltage(11.10)

        # Inject NaN and abnormal drops
        exporter.update_battery_voltage(float("nan"))
        exporter.update_battery_voltage(-5.0)
        exporter.update_battery_voltage(0.1)

        # Filtered voltage must stay untouched at 11.10V
        assert exporter.get_filtered_voltage() == pytest.approx(11.10)
        ok, _ = exporter.check_battery_safety()
        assert ok is True


# ============================================================================
# 5. Atomic SSD Map Export & Path Guards
# ============================================================================
class TestChallengerGroup5AtomicSSDExport:
    """Stress-testing SSD map export: read-only filesystem handling, path prefix guard, and atomic replacement."""

    def test_read_only_filesystem_error_handling_force_flag(self):
        """Read-only filesystem simulated via force_readonly flag must cleanly return False with error message."""
        exporter = SLAMOptimizationExporter(enforce_ssd_mount=False)
        dummy_grid = np.zeros((10, 10), dtype=np.int8)

        ok, msg, paths = exporter.export_map_files("test_map", dummy_grid, force_readonly=True)
        assert ok is False
        assert "Read-only file system" in msg
        assert paths == {}

    def test_read_only_filesystem_oserror_erofs_caught(self):
        """Real OSError with errno.EROFS (Read-only file system) must be safely caught without crashing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            exporter = SLAMOptimizationExporter(simulated_ssd_root=tmppath, enforce_ssd_mount=False)
            dummy_grid = np.zeros((10, 10), dtype=np.int8)

            with patch.object(Path, "mkdir", side_effect=OSError(errno.EROFS, "Read-only file system")):
                ok, msg, paths = exporter.export_map_files("ro_map", dummy_grid, target_override=tmppath)
                assert ok is False
                assert "Read-only file system" in msg
                assert paths == {}

    def test_unauthorized_path_rejection_home_robopy_map(self):
        """Unauthorized target paths outside /mnt/ssd/maps (e.g. /home/robopy/map) MUST BE REJECTED."""
        exporter = SLAMOptimizationExporter(enforce_ssd_mount=True)
        dummy_grid = np.zeros((10, 10), dtype=np.int8)

        unauthorized_paths = [
            Path("/home/robopy/map"),
            Path("/tmp/maps"),
            Path("/var/log/maps"),
            Path("/etc/ros/maps"),
            Path("C:/Users/robopy/map"),
        ]

        for unauth in unauthorized_paths:
            ok, msg, paths = exporter.export_map_files(
                "unauthorized_map",
                dummy_grid,
                target_override=unauth,
                enforce_ssd_check=True
            )
            assert ok is False, f"Unauthorized path '{unauth}' was erroneously accepted!"
            assert "VIOLATION FM-NAV-020" in msg
            assert paths == {}

    def test_atomic_replacement_leaves_no_broken_target(self):
        """If map export fails mid-operation, the existing original map must remain intact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            exporter = SLAMOptimizationExporter(simulated_ssd_root=tmppath, enforce_ssd_mount=False)
            dummy_grid = np.zeros((10, 10), dtype=np.int8)

            # Step 1: Export a valid original map
            ok_orig, _, paths_orig = exporter.export_map_files("original_room", dummy_grid, target_override=tmppath)
            assert ok_orig is True
            orig_yaml = paths_orig["yaml"]
            orig_content = orig_yaml.read_text(encoding="utf-8")

            # Step 2: Simulate OS error during replace on a new export targeting same name
            with patch("os.replace", side_effect=OSError(errno.EIO, "I/O error during atomic replace")):
                ok_fail, msg_fail, _ = exporter.export_map_files("original_room", dummy_grid, target_override=tmppath)
                assert ok_fail is False
                assert "OSError" in msg_fail

            # Original file MUST remain intact
            assert orig_yaml.exists()
            assert orig_yaml.read_text(encoding="utf-8") == orig_content


# ============================================================================
# 6. Adversarial Security & Logic Challenge (Empirical Bug Hunter)
# ============================================================================
class TestChallengerGroup6AdversarialFindings:
    """Rigorous tests designed to expose subtle logical edge cases and security vulnerabilities.
    Marked with xfail to document empirical reproduction while maintaining test runner stability.
    """

    def test_adversarial_destructive_negation_phrase_discrimination(self):
        """BUG REPORT CHALLENGE (BUG-HRI-01):
        User stating 'non confermo' (I do not confirm) must NOT confirm destructive action!
        Verified: negative intents ('no', 'annulla', 'non') are evaluated first.
        """
        fsm = MappingStateMachine()
        fsm.request_destructive_action("stanza_critica")
        assert fsm.destructive_pending is True

        # Operator explicitly negates: "non confermo"
        msg, ok = fsm.process_voice_input("non confermo")

        # An ideal safety gate MUST NOT confirm on "non confermo"
        assert ok is False, "CRITICAL HRI SAFETY BUG: 'non confermo' falsely confirmed destructive action!"
        assert fsm.destructive_confirmed is False
        assert "annullata" in msg.lower()

    def test_adversarial_path_traversal_ssd_guard_bypass(self):
        """SECURITY VULNERABILITY CHALLENGE (VULN-SSD-01):
        Path traversal using relative dot-dots (e.g. /mnt/ssd/maps/../../home/robopy/map)
        must NOT bypass the FM-NAV-020 SSD prefix check!
        Verified: Path(target_dir).resolve() canonicalizes path before prefix validation.
        """
        exporter = SLAMOptimizationExporter(enforce_ssd_mount=True)
        dummy_grid = np.zeros((5, 5), dtype=np.int8)

        # Path starts with /mnt/ssd/maps but traverses back to /home/robopy/map
        traversal_path = Path("/mnt/ssd/maps/../../home/robopy/map")

        try:
            ok, msg, paths = exporter.export_map_files(
                "traversal_map",
                dummy_grid,
                target_override=traversal_path,
                enforce_ssd_check=True
            )
            assert ok is False, "CRITICAL SECURITY VULNERABILITY: Path traversal bypassed FM-NAV-020 SSD check!"
            assert "VIOLATION FM-NAV-020" in msg
            assert paths == {}
        finally:
            # Clean up any escaped directory if created
            shutil.rmtree("mnt", ignore_errors=True)
            shutil.rmtree("home", ignore_errors=True)

    def test_adversarial_map_name_traversal_out_of_bounds(self):
        """SECURITY VULNERABILITY CHALLENGE (VULN-SSD-02):
        map_name containing directory traversal sequences (e.g. '../../escaped_map')
        must NOT escape the designated target directory and must be rejected!
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            target_sub = tmppath / "maps"
            target_sub.mkdir()
            exporter = SLAMOptimizationExporter(simulated_ssd_root=target_sub, enforce_ssd_mount=False)
            dummy_grid = np.zeros((5, 5), dtype=np.int8)

            ok, msg, paths = exporter.export_map_files(
                "../../escaped_map",
                dummy_grid,
                target_override=target_sub
            )

            assert ok is False, "Directory traversal map_name must be rejected!"
            assert "prohibited" in msg or "traversal" in msg.lower()
            assert paths == {}
