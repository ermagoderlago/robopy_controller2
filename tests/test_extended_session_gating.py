import sys
import os
import time
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class MockSessionGatingFSM:
    """Simulates the 3-minute extended conversation gating FSM from respeaker_vui_node & SPEC-04."""
    def __init__(self, timeout_s: float = 180.0):
        self.timeout_s = timeout_s
        self.is_session_active = False
        self.last_activity_time = 0.0
        self.turns_processed = 0
        self.silently_ignored_turns = 0

    def start_session(self, now: float = None):
        t = now if now is not None else time.monotonic()
        self.is_session_active = True
        self.last_activity_time = t

    def on_activity_start(self, now: float = None):
        """ActivityStart Gating: evaluates turn at speech onset."""
        t = now if now is not None else time.monotonic()
        if self.is_session_active:
            if t - self.last_activity_time <= self.timeout_s:
                return True
            else:
                self.is_session_active = False
                return False
        return False

    def on_valid_speech(self, speech_ms: float, now: float = None):
        t = now if now is not None else time.monotonic()
        if speech_ms >= 120.0 and self.is_session_active:
            self.last_activity_time = t
            return True
        return False

    def process_turn_directive(self, text: str):
        if "<IGNORE_TURN>" in text:
            # Directed follow-up: silent suppression without terminating session
            self.silently_ignored_turns += 1
            return False
        self.turns_processed += 1
        return True

    def check_inactivity_timeout(self, now: float = None) -> bool:
        t = now if now is not None else time.monotonic()
        if self.is_session_active and (t - self.last_activity_time > self.timeout_s):
            self.is_session_active = False
            return True
        return False


def test_session_gating_deterministic_timeout():
    fsm = MockSessionGatingFSM(timeout_s=180.0)
    start_t = 1000.0
    fsm.start_session(now=start_t)
    assert fsm.is_session_active is True

    # At 179s: still active
    fsm.check_inactivity_timeout(now=start_t + 179.0)
    assert fsm.is_session_active is True

    # At 181s: session must deterministically close
    closed = fsm.check_inactivity_timeout(now=start_t + 181.0)
    assert closed is True
    assert fsm.is_session_active is False


def test_session_gating_speech_resets_timer():
    fsm = MockSessionGatingFSM(timeout_s=180.0)
    start_t = 1000.0
    fsm.start_session(now=start_t)

    # Valid speech at 150s resets inactivity timer
    admitted = fsm.on_valid_speech(speech_ms=250.0, now=start_t + 150.0)
    assert admitted is True
    assert fsm.last_activity_time == start_t + 150.0

    # At 250s (100s after last speech): still active
    fsm.check_inactivity_timeout(now=start_t + 250.0)
    assert fsm.is_session_active is True

    # Short acoustic noise (<120ms) does NOT reset timer
    fsm.on_valid_speech(speech_ms=50.0, now=start_t + 260.0)
    assert fsm.last_activity_time == start_t + 150.0


def test_ignore_turn_suppression_preserves_session():
    fsm = MockSessionGatingFSM(timeout_s=180.0)
    fsm.start_session()
    
    # User was speaking to someone else in the room
    res = fsm.process_turn_directive("<IGNORE_TURN>")
    assert res is False
    assert fsm.silently_ignored_turns == 1
    assert fsm.is_session_active is True, "Silent turn suppression must NOT close session WebSocket"
