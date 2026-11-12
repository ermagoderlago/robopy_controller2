"""In-process Single Turn & Voice Floor Arbiter for Marcus (Phase 2).

Enforces Option A hierarchical brain authority:
- ``ONLINE`` + Live ready: Gemini Live owns the spoken voice floor; local Qwen
  may not emit parallel voice replies or steal mic/TTS ownership.
- ``OFFLINE`` or ``DEGRADED`` (or Live transport down): Qwen 2.5 + Piper TTS
  owns local voice interaction.
- Fast-Path emergency stop commands resolve locally in <10 ms regardless of cloud state.

Also arbitrates the conversational floor FSM, centralized ``/ai/input/mic_mute``
locks, social context gating (``/engagement/status``, ``/marcus/social_context/tom_state``),
and a bounded proactive speech queue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .dialogue_trace import DialogueTraceLogger, get_dialogue_trace


class FloorState(str, Enum):
    IDLE = "IDLE"
    USER_SPEAKING = "USER_SPEAKING"
    LIVE_ANSWERING = "LIVE_ANSWERING"
    LOCAL_ANSWERING = "LOCAL_ANSWERING"
    PROACTIVE_SPEAKING = "PROACTIVE_SPEAKING"
    EMERGENCY = "EMERGENCY"


class ConnectivityMode(str, Enum):
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


class SpeakerSource(str, Enum):
    GEMINI_LIVE = "GEMINI_LIVE"
    LOCAL_QWEN = "LOCAL_QWEN"
    SYSTEM_PROACTIVE = "SYSTEM_PROACTIVE"
    FAST_PATH = "FAST_PATH"
    SKILL_OUTPUT = "SKILL_OUTPUT"
    BACKCHANNEL = "BACKCHANNEL"
    EMERGENCY = "EMERGENCY"


class MicMuteOwner(str, Enum):
    EMERGENCY = "EMERGENCY"
    TTS_ECHO = "TTS_ECHO"
    MOVEMENT = "MOVEMENT"
    USER = "USER"
    SYSTEM = "SYSTEM"


_SOURCE_TO_FLOOR: Dict[SpeakerSource, FloorState] = {
    SpeakerSource.GEMINI_LIVE: FloorState.LIVE_ANSWERING,
    SpeakerSource.LOCAL_QWEN: FloorState.LOCAL_ANSWERING,
    SpeakerSource.FAST_PATH: FloorState.LOCAL_ANSWERING,
    SpeakerSource.SKILL_OUTPUT: FloorState.LOCAL_ANSWERING,
    SpeakerSource.BACKCHANNEL: FloorState.LOCAL_ANSWERING,
    SpeakerSource.SYSTEM_PROACTIVE: FloorState.PROACTIVE_SPEAKING,
    SpeakerSource.EMERGENCY: FloorState.EMERGENCY,
}

_FAST_PATH_STOP_RE = re.compile(
    r"\b(fermati|stop|alt|basta\s+cos[iì]|blocca|arresto|taci|zitto|silenzio)\b",
    re.IGNORECASE,
)

_BACKCHANNEL_PHRASES: Dict[str, str] = {
    "vision": "Un attimo, guardo subito.",
    "memory": "Controllo nei miei ricordi...",
    "home": "Verifico subito la casa.",
    "default": "Un attimo, controllo...",
}


@dataclass
class FloorDecision:
    """Result of requesting spoken floor ownership for a turn."""

    allowed: bool
    reason: str
    turn_id: str
    owner: Optional[str] = None
    floor_state: str = FloorState.IDLE.value


@dataclass
class SocialContextSnapshot:
    """Latest fused social perception signals from Vision/Engagement/ToM nodes."""

    engagement_state: str = "UNKNOWN"
    is_person_present: bool = False
    active_person: str = ""
    tom_state: str = "NORMAL"
    do_not_disturb: bool = False
    engagement_updated_mono: float = 0.0
    tom_updated_mono: float = 0.0


@dataclass
class ProactiveItem:
    """Queued background utterance awaiting free conversational floor."""

    kind: str
    text: str
    priority: int = 5
    requires_presence: bool = True
    force_urgent: bool = False
    created_mono: float = field(default_factory=time.monotonic)
    ttl_sec: float = 300.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class DialogueArbiter:
    """Single in-process coordinator for turn authority, voice floor, and social gating."""

    def __init__(
        self,
        trace_logger: Optional[DialogueTraceLogger] = None,
        proactive_cooldown_sec: float = 1.5,
        followup_window_sec: float = 8.0,
        max_proactive_queue: int = 16,
        floor_lease_timeout_sec: float = 30.0,
    ):
        self.trace = trace_logger or get_dialogue_trace()
        self.proactive_cooldown_sec = proactive_cooldown_sec
        self.followup_window_sec = followup_window_sec
        self.max_proactive_queue = max_proactive_queue
        self.floor_lease_timeout_sec = floor_lease_timeout_sec

        self._lock = threading.RLock()
        self._floor: FloorState = FloorState.IDLE
        self._floor_owner: Optional[str] = None
        self._floor_turn_id: Optional[str] = None
        self._floor_acquired_mono: float = 0.0
        self._last_floor_released_mono: float = 0.0
        self._last_turn_id: Optional[str] = None

        self._connectivity: ConnectivityMode = ConnectivityMode.ONLINE
        self._live_transport_ready: bool = False
        self._live_session_open: bool = False

        self._mic_mute_locks: Dict[str, bool] = {
            MicMuteOwner.EMERGENCY.value: False,
            MicMuteOwner.TTS_ECHO.value: False,
            MicMuteOwner.MOVEMENT.value: False,
            MicMuteOwner.USER.value: False,
            MicMuteOwner.SYSTEM.value: False,
        }
        self._effective_mic_muted: bool = False

        self.social = SocialContextSnapshot()
        self._proactive_queue: List[ProactiveItem] = []
        self._recent_proactive_fingerprints: Dict[str, float] = {}
        self._last_backchannel_mono: float = 0.0

    # ------------------------------------------------------------------
    # Fast-path emergency & connectivity hierarchy (Option A)
    # ------------------------------------------------------------------

    @staticmethod
    def is_fast_path_stop(text: str) -> bool:
        """Returns True if text contains an immediate local safety/halt command."""
        if not text:
            return False
        return bool(_FAST_PATH_STOP_RE.search(text))

    @property
    def connectivity(self) -> ConnectivityMode:
        with self._lock:
            return self._connectivity

    @property
    def floor_state(self) -> FloorState:
        with self._lock:
            self._expire_stale_floor_locked()
            return self._floor

    @property
    def active_voice_brain(self) -> str:
        """Returns which engine holds voice synthesis authority right now."""
        with self._lock:
            if self._connectivity == ConnectivityMode.ONLINE and self._live_transport_ready:
                return SpeakerSource.GEMINI_LIVE.value
            return SpeakerSource.LOCAL_QWEN.value

    def is_live_voice_active(self) -> bool:
        """True when Option A assigns spoken dialogue exclusively to Gemini Live."""
        with self._lock:
            return (
                self._connectivity == ConnectivityMode.ONLINE
                and self._live_transport_ready
            )

    def should_vui_forward_to_local_pipeline(self) -> bool:
        """Whether ``respeaker_vui_node`` should publish ``/ai/input/voice_command`` to Qwen.

        When Gemini Live owns the active online audio stream, publishing duplicate
        Vosk finals to ``/ai/input/voice_command`` causes simultaneous dual responses.
        """
        with self._lock:
            return not (
                self._connectivity == ConnectivityMode.ONLINE
                and self._live_transport_ready
            )

    def set_connectivity(self, mode: ConnectivityMode | str) -> ConnectivityMode:
        """Updates cloud connectivity state (``ONLINE`` / ``DEGRADED`` / ``OFFLINE``)."""
        if isinstance(mode, str):
            normalized = mode.strip().upper()
            try:
                parsed = ConnectivityMode(normalized)
            except ValueError:
                parsed = ConnectivityMode.DEGRADED if "DEG" in normalized else ConnectivityMode.OFFLINE
        else:
            parsed = mode

        with self._lock:
            prev = self._connectivity
            self._connectivity = parsed
            brain = self.active_voice_brain
            floor_val = self._floor.value
            turn_id = self._last_turn_id

        if prev != parsed:
            self.trace.log_event(
                "connectivity_switch",
                turn_id=turn_id,
                source="arbiter",
                floor=floor_val,
                connectivity=parsed.value,
                detail={"prev": prev.value, "new": parsed.value, "voice_brain": brain},
            )
        return parsed

    def set_live_transport_ready(self, ready: bool, session_open: Optional[bool] = None) -> None:
        """Updates whether Gemini Live WebSocket session is healthy and owning audio."""
        with self._lock:
            prev = self._live_transport_ready
            self._live_transport_ready = bool(ready)
            if session_open is not None:
                self._live_session_open = bool(session_open)
            elif not ready:
                self._live_session_open = False
            conn = self._connectivity.value
            floor_val = self._floor.value
            turn_id = self._last_turn_id
            brain = self.active_voice_brain

        if prev != bool(ready):
            self.trace.log_event(
                "live_transport_state",
                turn_id=turn_id,
                source="gemini_live",
                floor=floor_val,
                connectivity=conn,
                detail={"live_ready": bool(ready), "voice_brain": brain},
            )

    # ------------------------------------------------------------------
    # Turn correlation & Floor FSM
    # ------------------------------------------------------------------

    def begin_turn(
        self,
        source: str,
        text_preview: str = "",
        turn_id: Optional[str] = None,
    ) -> str:
        """Starts a correlated dialogue turn and logs a ``turn_start`` trace entry."""
        tid = turn_id or self.trace.new_turn_id()
        with self._lock:
            self._last_turn_id = tid
            floor_val = self._floor.value
            conn = self._connectivity.value

        self.trace.log_event(
            "turn_start",
            turn_id=tid,
            source=source,
            floor=floor_val,
            connectivity=conn,
            detail={"text": text_preview},
        )
        return tid

    def notify_user_speaking(self, active: bool, turn_id: Optional[str] = None) -> None:
        """Transitions IDLE <-> USER_SPEAKING safely without clobbering active AI speech."""
        with self._lock:
            if active and self._floor == FloorState.IDLE:
                self._floor = FloorState.USER_SPEAKING
                self._floor_acquired_mono = time.monotonic()
            elif not active and self._floor == FloorState.USER_SPEAKING:
                self._floor = FloorState.IDLE
                self._last_floor_released_mono = time.monotonic()
            floor_val = self._floor.value
            conn = self._connectivity.value
            tid = turn_id or self._last_turn_id

        self.trace.log_event(
            "user_vad_state",
            turn_id=tid,
            source="vui",
            floor=floor_val,
            connectivity=conn,
            detail={"speaking": bool(active)},
        )

    def acquire_floor(
        self,
        source: SpeakerSource | str,
        *,
        turn_id: Optional[str] = None,
        emergency: bool = False,
        allow_same_turn_upgrade: bool = True,
    ) -> FloorDecision:
        """Attempts to reserve exclusive voice output floor for ``source``.

        Prevents Option A double responses (Qwen speaking while Live owns voice)
        and ensures only one speaker uses TTS at a time unless preempted by an
        emergency stop.
        """
        src = SpeakerSource(source) if isinstance(source, str) else source
        tid = turn_id or self._last_turn_id or self.trace.new_turn_id()

        with self._lock:
            self._expire_stale_floor_locked()
            conn = self._connectivity
            live_active = conn == ConnectivityMode.ONLINE and self._live_transport_ready

            # Option A invariant: Qwen never voices replies while Live owns online voice.
            if src == SpeakerSource.LOCAL_QWEN and live_active and not emergency:
                current_floor = self._floor.value
                current_owner = self._floor_owner
                self._lock_log_denied(
                    tid, src.value, current_floor, conn.value,
                    "option_a_live_owns_voice", current_owner,
                )
                return FloorDecision(
                    allowed=False,
                    reason="option_a_live_owns_voice",
                    turn_id=tid,
                    owner=current_owner,
                    floor_state=current_floor,
                )

            if self._floor in (FloorState.IDLE, FloorState.USER_SPEAKING):
                target_floor = _SOURCE_TO_FLOOR[src]
                self._floor = target_floor
                self._floor_owner = src.value
                self._floor_turn_id = tid
                self._floor_acquired_mono = time.monotonic()
                self._last_turn_id = tid
                self.trace.log_event(
                    "floor_acquired",
                    turn_id=tid,
                    source=src.value,
                    floor=target_floor.value,
                    connectivity=conn.value,
                )
                return FloorDecision(
                    allowed=True,
                    reason="acquired",
                    turn_id=tid,
                    owner=src.value,
                    floor_state=target_floor.value,
                )

            # Re-entrant acquisition for the exact same turn/owner (e.g., backchannel -> answer)
            if (
                allow_same_turn_upgrade
                and self._floor_turn_id == tid
                and self._floor_owner in (src.value, SpeakerSource.BACKCHANNEL.value)
            ):
                target_floor = _SOURCE_TO_FLOOR[src]
                self._floor = target_floor
                self._floor_owner = src.value
                self._floor_acquired_mono = time.monotonic()
                return FloorDecision(
                    allowed=True,
                    reason="upgraded_same_turn",
                    turn_id=tid,
                    owner=src.value,
                    floor_state=target_floor.value,
                )

            # Emergency preemption
            if emergency or src == SpeakerSource.EMERGENCY:
                prev_owner = self._floor_owner
                self._floor = FloorState.EMERGENCY
                self._floor_owner = src.value
                self._floor_turn_id = tid
                self._floor_acquired_mono = time.monotonic()
                self.trace.log_event(
                    "floor_preempted",
                    turn_id=tid,
                    source=src.value,
                    floor=self._floor.value,
                    connectivity=conn.value,
                    detail={"preempted_owner": prev_owner},
                )
                return FloorDecision(
                    allowed=True,
                    reason="emergency_preempt",
                    turn_id=tid,
                    owner=src.value,
                    floor_state=self._floor.value,
                )

            # Busy floor held by another source
            reason = f"floor_busy:{self._floor.value}:{self._floor_owner}"
            current_floor = self._floor.value
            current_owner = self._floor_owner
            self._lock_log_denied(
                tid, src.value, current_floor, conn.value, reason, current_owner
            )
            return FloorDecision(
                allowed=False,
                reason=reason,
                turn_id=tid,
                owner=current_owner,
                floor_state=current_floor,
            )

    def _lock_log_denied(
        self,
        turn_id: str,
        source: str,
        floor: str,
        connectivity: str,
        reason: str,
        owner: Optional[str],
    ) -> None:
        """Logs a floor denial and tracks collision metrics (caller holds lock)."""
        self.trace.log_event(
            "floor_denied",
            turn_id=turn_id,
            source=source,
            floor=floor,
            connectivity=connectivity,
            detail={"reason": reason, "current_owner": owner},
        )
        if owner in (SpeakerSource.GEMINI_LIVE.value, SpeakerSource.LOCAL_QWEN.value) and owner != source:
            self.trace.counters.inc("double_response_count", 0)  # blocked cleanly, 0 emitted

    def release_floor(
        self,
        *,
        turn_id: Optional[str] = None,
        source: Optional[str] = None,
        force: bool = False,
    ) -> bool:
        """Releases the conversational floor back to ``IDLE``."""
        with self._lock:
            if self._floor == FloorState.IDLE:
                return False
            if not force:
                if turn_id and self._floor_turn_id and turn_id != self._floor_turn_id:
                    return False
                if source and self._floor_owner and source != self._floor_owner:
                    # Allow EMERGENCY / SYSTEM release cleanly only via force
                    return False
            prev_floor = self._floor.value
            prev_owner = self._floor_owner
            tid = turn_id or self._floor_turn_id
            conn = self._connectivity.value
            self._floor = FloorState.IDLE
            self._floor_owner = None
            self._floor_turn_id = None
            self._last_floor_released_mono = time.monotonic()

        self.trace.log_event(
            "floor_released",
            turn_id=tid,
            source=prev_owner or (source or "arbiter"),
            floor=FloorState.IDLE.value,
            connectivity=conn,
            detail={"prev_floor": prev_floor},
        )
        return True

    def _expire_stale_floor_locked(self) -> None:
        """Self-heals if an owner crashed mid-turn without releasing the floor."""
        if self._floor in (FloorState.IDLE, FloorState.USER_SPEAKING):
            return
        if self._floor_acquired_mono > 0 and (
            time.monotonic() - self._floor_acquired_mono > self.floor_lease_timeout_sec
        ):
            tid = self._floor_turn_id
            prev = self._floor.value
            self._floor = FloorState.IDLE
            self._floor_owner = None
            self._floor_turn_id = None
            self._last_floor_released_mono = time.monotonic()
            self.trace.log_event(
                "floor_lease_expired",
                turn_id=tid,
                source="arbiter",
                floor=FloorState.IDLE.value,
                connectivity=self._connectivity.value,
                detail={"prev_floor": prev},
            )

    # ------------------------------------------------------------------
    # Social Context & Follow-up Window Gating (Phase 3)
    # ------------------------------------------------------------------

    def update_engagement(
        self,
        engagement_state: str,
        *,
        is_person_present: Optional[bool] = None,
        active_person: Optional[str] = None,
    ) -> None:
        """Updates visual/social engagement state from ``/engagement/status`` & ``/identity/active_person``."""
        norm = (engagement_state or "UNKNOWN").strip().upper()
        with self._lock:
            self.social.engagement_state = norm
            if is_person_present is not None:
                self.social.is_person_present = bool(is_person_present)
            elif norm in ("ENGAGED", "ATTENTIVE", "PASSIVE", "PRESENT"):
                self.social.is_person_present = True
            elif norm in ("DISENGAGED", "NONE", "ABSENT", "IDLE"):
                self.social.is_person_present = False
            if active_person is not None:
                self.social.active_person = active_person.strip()
            self.social.engagement_updated_mono = time.monotonic()

    def update_tom_state(self, tom_state: str, *, do_not_disturb: Optional[bool] = None) -> None:
        """Updates Theory-of-Mind state from ``/marcus/social_context/tom_state``."""
        norm = (tom_state or "NORMAL").strip().upper()
        with self._lock:
            self.social.tom_state = norm
            if do_not_disturb is not None:
                self.social.do_not_disturb = bool(do_not_disturb)
            else:
                self.social.do_not_disturb = any(
                    k in norm for k in ("DO_NOT_DISTURB", "DND", "BUSY_DO_NOT_INTERRUPT", "SLEEP")
                )
            self.social.tom_updated_mono = time.monotonic()

    def can_continue_without_wakeword(self, within_timer: bool) -> Tuple[bool, str]:
        """Determines whether a follow-up utterance is valid without explicit wakeword.

        Requires BOTH an active bounded follow-up window AND (when social
        telemetry is live) either ``ENGAGED`` status or confirmed person presence.
        """
        if not within_timer:
            return False, "outside_followup_window"

        with self._lock:
            if self.social.tom_updated_mono > 0 and self.social.do_not_disturb:
                return False, "tom_do_not_disturb"
            if self.social.engagement_updated_mono > 0:
                if self.social.engagement_state in ("ENGAGED", "ATTENTIVE") or self.social.is_person_present:
                    return True, "engaged_followup"
                return False, f"disengaged:{self.social.engagement_state}"

        return True, "timer_active_no_social_telemetry"

    def recommended_tts_volume_scale(self) -> float:
        """Returns 0.65 during DO_NOT_DISTURB/RESTING states, else 1.0."""
        with self._lock:
            if self.social.do_not_disturb or "REST" in self.social.tom_state:
                return 0.65
            return 1.0

    # ------------------------------------------------------------------
    # Proactive Announcement Queue & Gate (Phase 2.4 + Phase 4.3)
    # ------------------------------------------------------------------

    def enqueue_proactive(
        self,
        kind: str,
        text: str,
        *,
        priority: int = 5,
        requires_presence: bool = True,
        force_urgent: bool = False,
        ttl_sec: float = 300.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Queues a proactive announcement instead of interrupting or ignoring active floor.

        Suppresses non-urgent proactivity immediately if ToM is ``DO_NOT_DISTURB``.
        """
        clean = (text or "").strip()
        if not clean:
            return False

        now = time.monotonic()
        fp = f"{kind}:{clean[:80]}"

        with self._lock:
            if self.social.do_not_disturb and not force_urgent:
                self.trace.log_event(
                    "proactive_suppressed",
                    turn_id=self._last_turn_id,
                    source="arbiter",
                    floor=self._floor.value,
                    connectivity=self._connectivity.value,
                    detail={"kind": kind, "reason": "do_not_disturb"},
                )
                return False

            last_fp = self._recent_proactive_fingerprints.get(fp, 0.0)
            if now - last_fp < 30.0 and not force_urgent:
                return False
            self._recent_proactive_fingerprints[fp] = now

            item = ProactiveItem(
                kind=kind,
                text=clean,
                priority=int(priority),
                requires_presence=bool(requires_presence),
                force_urgent=bool(force_urgent),
                created_mono=now,
                ttl_sec=float(ttl_sec),
                metadata=dict(metadata or {}),
            )
            self._proactive_queue.append(item)
            self._proactive_queue.sort(key=lambda x: (-x.priority, x.created_mono))
            if len(self._proactive_queue) > self.max_proactive_queue:
                dropped = self._proactive_queue.pop()
                self.trace.log_event(
                    "proactive_suppressed",
                    turn_id=self._last_turn_id,
                    source="arbiter",
                    floor=self._floor.value,
                    connectivity=self._connectivity.value,
                    detail={"kind": dropped.kind, "reason": "queue_overflow"},
                )

        self.trace.log_event(
            "proactive_queued",
            turn_id=self._last_turn_id,
            source="arbiter",
            floor=self._floor.value,
            connectivity=self._connectivity.value,
            detail={"kind": kind, "priority": priority, "queue_len": len(self._proactive_queue)},
        )
        return True

    def pop_ready_proactive(self) -> Optional[ProactiveItem]:
        """Pops the highest-priority proactive utterance if floor and social state allow it."""
        now = time.monotonic()
        with self._lock:
            self._expire_stale_floor_locked()

            # Drop expired entries
            self._proactive_queue = [
                it for it in self._proactive_queue if (now - it.created_mono) <= it.ttl_sec
            ]
            if not self._proactive_queue:
                return None

            if self._floor != FloorState.IDLE:
                return None

            if (now - self._last_floor_released_mono) < self.proactive_cooldown_sec:
                return None

            for idx, item in enumerate(self._proactive_queue):
                if not item.force_urgent:
                    if self.social.do_not_disturb:
                        continue
                    if (
                        item.requires_presence
                        and self.social.engagement_updated_mono > 0
                        and not self.social.is_person_present
                        and self.social.engagement_state not in ("ENGAGED", "ATTENTIVE")
                    ):
                        continue
                return self._proactive_queue.pop(idx)

        return None

    # ------------------------------------------------------------------
    # Centralized /ai/input/mic_mute Arbitration (Phase 2.5)
    # ------------------------------------------------------------------

    def set_mic_mute_lock(
        self,
        owner: MicMuteOwner | str,
        muted: bool,
        *,
        reason: str = "",
    ) -> Optional[bool]:
        """Sets or clears one owner's lock on ``/ai/input/mic_mute``.

        Returns the new effective boolean mute state ONLY if the aggregate state
        changed, preventing competing publishers from flapping the mic mute topic.
        """
        owner_key = owner.value if isinstance(owner, MicMuteOwner) else str(owner).upper()
        with self._lock:
            self._mic_mute_locks[owner_key] = bool(muted)
            new_effective = any(self._mic_mute_locks.values())
            changed = new_effective != self._effective_mic_muted
            self._effective_mic_muted = new_effective
            floor_val = self._floor.value
            conn = self._connectivity.value
            tid = self._last_turn_id

        if changed:
            self.trace.log_event(
                "mic_mute_changed",
                turn_id=tid,
                source=owner_key,
                floor=floor_val,
                connectivity=conn,
                detail={"effective_muted": new_effective, "owner_muted": bool(muted), "reason": reason},
            )
            return new_effective
        return None

    @property
    def is_mic_muted(self) -> bool:
        with self._lock:
            return self._effective_mic_muted

    # ------------------------------------------------------------------
    # Backchannel helper (<300ms reaction for slow tools - Phase 4.1)
    # ------------------------------------------------------------------

    def request_backchannel_cue(
        self,
        category: str = "default",
        min_interval_sec: float = 4.0,
    ) -> Optional[str]:
        """Returns a short Italian backchannel phrase if throttled cooldown allows."""
        now = time.monotonic()
        with self._lock:
            if (now - self._last_backchannel_mono) < min_interval_sec:
                return None
            self._last_backchannel_mono = now
            floor_val = self._floor.value
            conn = self._connectivity.value
            tid = self._last_turn_id

        phrase = _BACKCHANNEL_PHRASES.get(category, _BACKCHANNEL_PHRASES["default"])
        self.trace.log_event(
            "backchannel",
            turn_id=tid,
            source="arbiter",
            floor=floor_val,
            connectivity=conn,
            detail={"category": category, "phrase": phrase},
        )
        return phrase
