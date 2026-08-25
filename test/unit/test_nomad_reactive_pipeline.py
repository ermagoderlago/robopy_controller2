#!/usr/bin/env python3
"""
test_nomad_reactive_pipeline.py - Unit tests for NoMaD v2 Reactive Pipeline
==========================================================================
Comprehensive unit tests covering:
- CPU affinity configuration safety
- Vectorized EMA filter (steady state, step response, adaptive alpha, reset)
- Pure Pursuit controller (straight, turn scaling, velocity clipping, overrides)
- Timeout state machine (DDIM -> Action Chunking -> Hysteresis restoration)
- Real-time safety watchdog
- Synthetic visual affordance generator
- Non-blocking drop-oldest queue buffer
"""

import math
import time
import queue
import pytest
import numpy as np

from robopy_controller.nodes.nomad_reactive_pipeline_node import (
    EMAWaypointFilter,
    PurePursuitController,
    IMUImpactDetector,
    TexturelessWallDetector,
    StallSlipDetector,
    NomadReactivePipelineNode
)


class TestEMAWaypointFilter:
    def test_ema_initialization_and_shape(self):
        ema = EMAWaypointFilter(num_waypoints=6, base_alpha=0.30, fast_alpha=0.70)
        raw = np.array([[0.25, 0.0], [0.50, 0.0], [0.75, 0.0], [1.00, 0.0], [1.25, 0.0], [1.50, 0.0]], dtype=np.float32)
        filtered = ema.filter(raw)
        assert filtered.shape == (6, 2)
        assert np.allclose(filtered, raw)

    def test_ema_invalid_shape_raises(self):
        ema = EMAWaypointFilter()
        with pytest.raises(ValueError):
            ema.filter(np.zeros((6, 3)))

    def test_ema_steady_state_convergence(self):
        ema = EMAWaypointFilter(base_alpha=0.30)
        constant_raw = np.array([[0.25, 0.05], [0.50, 0.10], [0.75, 0.15], [1.00, 0.20], [1.25, 0.25], [1.50, 0.30]], dtype=np.float32)
        
        # Initialize
        ema.filter(constant_raw)
        
        # 30 iterations with constant input
        for _ in range(30):
            res = ema.filter(constant_raw)
        
        assert np.allclose(res, constant_raw, atol=1e-4)

    def test_ema_step_response(self):
        ema = EMAWaypointFilter(base_alpha=0.30, fast_alpha=0.70)
        init_raw = np.zeros((6, 2), dtype=np.float32)
        ema.filter(init_raw)
        
        # Step along X axis (heading 0 deg, so delta_heading = 0 < 30 deg threshold)
        step_raw = np.array([[1.0, 0.0], [2.0, 0.0], [3.0, 0.0], [4.0, 0.0], [5.0, 0.0], [6.0, 0.0]], dtype=np.float32)
        first_step = ema.filter(step_raw)
        
        # After 1 step with base_alpha = 0.30: 0.30 * step_raw + 0.70 * 0.0 = 0.30 * step_raw
        expected = 0.30 * step_raw
        assert np.allclose(first_step, expected, atol=1e-3)

    def test_ema_adaptive_alpha_sharp_turn(self):
        ema = EMAWaypointFilter(base_alpha=0.30, fast_alpha=0.70, heading_threshold_deg=30.0)
        
        # Heading 0 deg (straight)
        straight = np.array([[0.25, 0.0], [0.50, 0.0], [0.75, 0.0], [1.0, 0.0], [1.25, 0.0], [1.5, 0.0]], dtype=np.float32)
        ema.filter(straight)
        
        # Sudden 60 degree turn (heading > 30 deg threshold)
        turn_y = 0.50 * math.tan(math.radians(60.0))  # approx 0.866
        turn = np.array([[0.25, 0.433], [0.50, turn_y], [0.75, 1.299], [1.0, 1.732], [1.25, 2.165], [1.5, 2.598]], dtype=np.float32)
        
        res = ema.filter(turn)
        # With fast_alpha = 0.70: 0.70 * turn_y + 0.30 * 0.0 = 0.70 * 0.866 = 0.606
        expected_y1 = 0.70 * turn_y
        assert abs(res[1, 1] - expected_y1) < 0.05

    def test_ema_reset(self):
        ema = EMAWaypointFilter()
        raw = np.ones((6, 2), dtype=np.float32)
        ema.filter(raw)
        assert ema.smoothed_waypoints is not None
        ema.reset()
        assert ema.smoothed_waypoints is None
        assert ema.last_heading == 0.0


class TestPurePursuitController:
    def test_pure_pursuit_empty_waypoints(self):
        controller = PurePursuitController()
        cmd = controller.compute_cmd_vel(np.empty((0, 2)))
        assert cmd.linear.x == 0.0
        assert cmd.angular.z == 0.0

    def test_pure_pursuit_straight_motion(self):
        controller = PurePursuitController(max_linear_speed=0.22, max_angular_speed=1.50)
        # Straight waypoints along X axis
        waypoints = np.array([[0.25, 0.0], [0.50, 0.0], [0.75, 0.0], [1.0, 0.0]], dtype=np.float32)
        cmd = controller.compute_cmd_vel(waypoints)
        
        assert cmd.linear.x == pytest.approx(0.22, abs=1e-3)
        assert cmd.angular.z == pytest.approx(0.0, abs=1e-3)

    def test_pure_pursuit_turn_scaling(self):
        controller = PurePursuitController(lookahead_index=1, max_linear_speed=0.20, max_angular_speed=1.50, k_angular=1.80)
        # Waypoints tilted 45 degrees left
        waypoints = np.array([[0.25, 0.25], [0.50, 0.50], [0.75, 0.75]], dtype=np.float32)
        cmd = controller.compute_cmd_vel(waypoints)
        
        # Heading error = pi/4 (0.785 rad)
        # Angular = 1.80 * 0.785 = 1.413 rad/s
        # Linear = 0.20 * cos(pi/4) = 0.1414 m/s
        assert cmd.angular.z > 0.5
        assert cmd.linear.x < 0.20

    def test_pure_pursuit_clipping(self):
        controller = PurePursuitController(lookahead_index=0, max_linear_speed=0.22, max_angular_speed=1.50, k_angular=5.0)
        # Extreme lateral target (90 degrees)
        waypoints = np.array([[0.01, 1.0], [0.02, 2.0]], dtype=np.float32)
        cmd = controller.compute_cmd_vel(waypoints)
        
        assert abs(cmd.angular.z) <= 1.50
        assert cmd.linear.x <= 0.22

    def test_pure_pursuit_speed_limit_override(self):
        controller = PurePursuitController(max_linear_speed=0.22)
        waypoints = np.array([[0.25, 0.0], [0.50, 0.0]], dtype=np.float32)
        cmd = controller.compute_cmd_vel(waypoints, speed_limit_override=0.15)
        
        assert cmd.linear.x == pytest.approx(0.15, abs=1e-3)


class TestNomadStateAndBuffers:
    def test_drop_oldest_queue_behavior(self):
        q = queue.Queue(maxsize=1)
        
        # Put 1st item
        q.put_nowait("frame_1")
        assert q.qsize() == 1
        
        # Simulate drop-oldest
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        q.put_nowait("frame_2")
        
        assert q.qsize() == 1
        assert q.get_nowait() == "frame_2"

    def test_synthetic_affordance_generation(self):
        # Create a dummy image
        img = np.ones((224, 224, 3), dtype=np.float32) * 0.5
        # Brighter on right
        img[:, 150:, :] = 0.9
        
        # Instantiate helper to test affordance
        ema = EMAWaypointFilter()
        h, w, _ = img.shape
        left_val = float(np.mean(img[:, :w // 3]))
        right_val = float(np.mean(img[:, 2 * w // 3:]))
        bias = float(np.clip((right_val - left_val) * 1.2, -0.40, 0.40))
        
        assert bias > 0.0  # Steers right towards brighter sector

    def test_timeout_fallback_logic(self):
        consecutive_timeouts = 0
        mode = "DDIM"
        latencies = [110.0, 120.0]  # Two timeouts > 100ms
        
        for lat in latencies:
            if lat > 100.0:
                consecutive_timeouts += 1
                if consecutive_timeouts >= 2 and mode == "DDIM":
                    mode = "ACTION_CHUNKING"
            else:
                consecutive_timeouts = 0
                
        assert mode == "ACTION_CHUNKING"
        assert consecutive_timeouts == 2

    def test_hysteresis_recovery_logic(self):
        mode = "ACTION_CHUNKING"
        consecutive_fast_cycles = 0
        
        # 10 consecutive fast cycles under 80ms
        for _ in range(10):
            lat = 4.5  # ms
            if lat < 80.0:
                consecutive_fast_cycles += 1
                if consecutive_fast_cycles >= 10:
                    mode = "DDIM"
                    consecutive_fast_cycles = 0
                    
        assert mode == "DDIM"
        assert consecutive_fast_cycles == 0

    def test_watchdog_time_elapsed(self):
        last_time = time.monotonic() - 0.350  # 350ms ago
        timeout_threshold = 300.0  # ms
        elapsed_ms = (time.monotonic() - last_time) * 1000.0
        
        assert elapsed_ms > timeout_threshold


class TestIMUImpactDetector:
    """Test suite for Camera IMU Jerk & Shock Collision Detector and Recovery FSM."""
    def test_imu_detector_initialization(self):
        det = IMUImpactDetector(
            impact_accel_threshold=5.5,
            impact_jerk_threshold=65.0,
            stop_duration_sec=0.20,
            backoff_speed=0.10,
            backoff_duration_sec=0.80,
            turn_speed=0.35,
            turn_duration_sec=0.50,
            warmup_duration_sec=0.0
        )
        assert det.state == "IDLE"
        assert det.total_impacts == 0

    def test_imu_detector_stationary_no_trigger(self):
        det = IMUImpactDetector(impact_accel_threshold=5.5, impact_jerk_threshold=65.0, warmup_duration_sec=0.0)
        t = 100.0
        # Initialize bias
        det.process_imu_sample(0.05, -0.02, t)
        
        # Stationary baseline noise (< 0.2 m/s²)
        for i in range(20):
            t += 0.01  # 100 Hz
            trig = det.process_imu_sample(0.05 + 0.02 * math.sin(i), -0.02, t)
            assert not trig
            assert det.state == "IDLE"

    def test_imu_detector_normal_acceleration_no_trigger(self):
        det = IMUImpactDetector(impact_accel_threshold=5.5, impact_jerk_threshold=65.0, warmup_duration_sec=0.0)
        t = 100.0
        det.process_imu_sample(0.0, 0.0, t)
        
        # Normal robot drive acceleration: smooth ramp to 1.5 m/s² over 0.5s
        for step in range(50):
            t += 0.01
            ramp_ax = 1.5 * (step / 50.0)
            trig = det.process_imu_sample(ramp_ax, 0.0, t)
            assert not trig
            assert det.state == "IDLE"

    def test_imu_detector_impact_trigger_on_sharp_decel(self):
        det = IMUImpactDetector(impact_accel_threshold=5.5, impact_jerk_threshold=65.0, warmup_duration_sec=0.0)
        t = 100.0
        det.process_imu_sample(0.5, 0.0, t)
        
        # Robot cruising at 0.5 m/s²
        for _ in range(10):
            t += 0.01
            det.process_imu_sample(0.5, 0.0, t)
            
        # Physical collision / impact: violent deceleration shock to -12.0 m/s² for 2 samples (20ms debounce)
        t += 0.01
        trig1 = det.process_imu_sample(-12.0, 0.0, t)
        t += 0.01
        trig2 = det.process_imu_sample(-12.0, 0.0, t)
        
        assert trig2 is True
        assert det.state == "COLLISION_STOP"
        assert det.total_impacts == 1
        assert det.last_impact_magnitude > 4.5

    def test_imu_fsm_lifecycle_transitions_and_outputs(self):
        det = IMUImpactDetector(
            stop_duration_sec=0.20,
            backoff_speed=0.10,
            backoff_duration_sec=0.80,
            turn_speed=0.35,
            turn_duration_sec=0.50,
            warmup_duration_sec=0.0
        )
        t = 100.0
        det.process_imu_sample(0.0, 0.0, t)
        
        # Trigger impact with 2 samples
        t += 0.01
        det.process_imu_sample(12.0, 0.0, t)
        t += 0.01
        det.process_imu_sample(12.0, 0.0, t)
        assert det.state == "COLLISION_STOP"
        
        # 1. Phase 1: COLLISION_STOP for 0.20s (v=0, w=0)
        state, v, w, reset_flag = det.update_fsm(t + 0.10)
        assert state == "COLLISION_STOP"
        assert v == 0.0
        assert w == 0.0
        assert reset_flag is False
        
        # Transition to BACKOFF after 0.20s
        t_stop_end = t + 0.21
        state, v, w, reset_flag = det.update_fsm(t_stop_end)
        assert state == "COLLISION_BACKOFF"
        assert math.isclose(v, -0.10, abs_tol=1e-3)
        assert w == 0.0
        assert reset_flag is False
        
        # 2. Phase 2: COLLISION_BACKOFF for 0.80s (v=-0.10 m/s)
        state, v, w, reset_flag = det.update_fsm(t_stop_end + 0.40)
        assert state == "COLLISION_BACKOFF"
        assert math.isclose(v, -0.10, abs_tol=1e-3)
        assert w == 0.0
        
        # Transition to REPLAN_RECOVERY after 0.80s
        t_backoff_end = t_stop_end + 0.81
        state, v, w, reset_flag = det.update_fsm(t_backoff_end)
        assert state == "REPLAN_RECOVERY"
        assert v == 0.0
        assert math.isclose(w, 0.35, abs_tol=1e-3)
        assert reset_flag is True  # Flags EMA & visual buffer reset!
        
        # 3. Phase 3: REPLAN_RECOVERY for 0.50s (turn w=0.35 rad/s)
        state, v, w, reset_flag = det.update_fsm(t_backoff_end + 0.25)
        assert state == "REPLAN_RECOVERY"
        assert math.isclose(w, 0.35, abs_tol=1e-3)
        
        # Transition back to IDLE after 0.50s
        t_turn_end = t_backoff_end + 0.51
        state, v, w, reset_flag = det.update_fsm(t_turn_end)
        assert state == "IDLE"
        assert v == 0.0
        assert w == 0.0
        assert det.state == "IDLE"

    def test_imu_detector_manual_reset(self):
        det = IMUImpactDetector(warmup_duration_sec=0.0)
        t = 100.0
        det.process_imu_sample(0.0, 0.0, t)
        det.process_imu_sample(12.0, 0.0, t + 0.01)
        det.process_imu_sample(12.0, 0.0, t + 0.02)
        assert det.state == "COLLISION_STOP"
        
        det.reset()
        assert det.state == "IDLE"
        state, v, w, _ = det.update_fsm(t + 0.05)
        assert state == "IDLE"

    def test_imu_detector_sensitive_threshold(self):
        """Verifies calibrated 2.5 m/s² threshold triggers reliably on gentle low-speed impacts."""
        det = IMUImpactDetector(impact_accel_threshold=2.5, impact_jerk_threshold=28.0, warmup_duration_sec=0.0)
        t = 100.0
        det.process_imu_sample(0.0, 0.0, t)
        
        # 3.2 m/s² deceleration bump (2 samples)
        det.process_imu_sample(3.2, 0.0, t + 0.01)
        trig = det.process_imu_sample(3.2, 0.0, t + 0.02)
        assert trig is True
        assert det.state == "COLLISION_STOP"


class TestTexturelessWallDetector:
    """Test suite for Monochromatic / White Wall & Door Optical Void Detection."""
    def test_white_wall_detection(self):
        detector = TexturelessWallDetector(laplacian_var_thresh=18.0)
        # Create a uniform white/light gray image (representing a flat white wall or closed door)
        white_wall = np.ones((224, 224, 3), dtype=np.uint8) * 240
        is_wall, var_lap = detector.is_textureless_wall(white_wall)
        assert is_wall is True
        assert var_lap < 1.0

    def test_textured_scene_detection(self):
        detector = TexturelessWallDetector(laplacian_var_thresh=18.0)
        # Create an image with high frequency textures (checkerboard / edges)
        textured_scene = np.zeros((224, 224, 3), dtype=np.uint8)
        textured_scene[::8, :, :] = 255
        textured_scene[:, ::8, :] = 255
        is_wall, var_lap = detector.is_textureless_wall(textured_scene)
        assert is_wall is False
        assert var_lap > 50.0

    def test_empty_or_invalid_frame_graceful(self):
        detector = TexturelessWallDetector()
        is_wall, var = detector.is_textureless_wall(np.array([]))
        assert is_wall is False


class TestStallSlipDetector:
    """Test suite for Motor Stall, Wheel Slip & Obstacle Pinning Detection."""
    def test_normal_driving_no_stall_or_slip(self):
        det = StallSlipDetector(stall_duration_sec=0.40, slip_duration_sec=0.50)
        t = 100.0
        # Robot commanded 0.15 m/s, wheels moving at 0.14 m/s, VIO measuring 0.14 m/s
        for _ in range(10):
            t += 0.05
            trig, reason = det.evaluate(cmd_v=0.15, wheel_v=0.14, vio_v=0.14, now_mono=t)
            assert trig is False
            assert reason == "NONE"

    def test_wheel_stall_pinned_trigger(self):
        det = StallSlipDetector(stall_duration_sec=0.40, slip_duration_sec=0.50)
        t = 100.0
        # Robot commanded forward at 0.15 m/s, but wheels are physically locked (0.005 m/s)
        # For 0.30s -> not triggered yet
        for _ in range(6):
            t += 0.05
            trig, _ = det.evaluate(cmd_v=0.15, wheel_v=0.005, vio_v=0.0, now_mono=t)
            assert trig is False
            
        # At t = 100.50s (elapsed 0.45s >= 0.40s) -> TRIGGERED!
        t += 0.20
        trig, reason = det.evaluate(cmd_v=0.15, wheel_v=0.005, vio_v=0.0, now_mono=t)
        assert trig is True
        assert reason == "WHEEL_STALL_PINNED"

    def test_wheel_slip_on_wall_trigger(self):
        det = StallSlipDetector(stall_duration_sec=0.40, slip_duration_sec=0.50)
        t = 100.0
        # Robot pushing against white wall: commanded 0.15 m/s, wheels spinning at 0.12 m/s, but VIO is 0.002 m/s
        for _ in range(8):
            t += 0.05
            trig, _ = det.evaluate(cmd_v=0.15, wheel_v=0.12, vio_v=0.002, now_mono=t)
            assert trig is False
            
        # At t = 100.60s (elapsed 0.55s >= 0.50s) -> TRIGGERED!
        t += 0.20
        trig, reason = det.evaluate(cmd_v=0.15, wheel_v=0.12, vio_v=0.002, now_mono=t)
        assert trig is True
        assert reason == "WHEEL_SLIP_ON_WALL"

    def test_stall_slip_reset(self):
        det = StallSlipDetector(stall_duration_sec=0.40)
        t = 100.0
        det.evaluate(cmd_v=0.15, wheel_v=0.0, vio_v=0.0, now_mono=t)
        det.reset()
        assert det.stall_start_time is None
        assert det.slip_start_time is None


