#!/usr/bin/env python3
"""
Multimodal Identity Tracker
===========================
Fuses facial biometric embeddings (ArcFace / SCRFD) with voice biometric
embeddings (ECAPA-TDNN / speaker recognition) and spatial localization (audio DOA).

Features:
- Track registry with spatial bounding box, centroid, and camera azimuth.
- Temporal re-verification policy per track to reduce NPU load by 70-80%
  (e.g., 2s in ENGAGED, 5s in VIGILANT, 10s in MOVING).
- Progressive 2-stage voice identification (Provisional <300ms, Confirmed <500ms).
- Spatial coherence gating comparing microphone Direction of Arrival (DOA)
  with visual person azimuth angle.
- Bayesian/weighted confidence fusion with conflict detection and confirmation flags.
- Real-time serialization for TRINITY / CAG Aggregator and LLM prompting.

Version: 02.00.00 (Fase F2)
"""

import time
import math
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import numpy as np


@dataclass
class VisualTrack:
    """Visual person track maintaining bounding box and biometric recheck state."""
    track_id: int
    bbox: Tuple[float, float, float, float]  # (x, y, w, h) in pixels or normalized
    centroid: Tuple[float, float]            # (cx, cy)
    azimuth_deg: float                       # Estimated horizontal angle in camera frame (-fov/2 to +fov/2)
    face_identity: str = "unknown"
    face_confidence: float = 0.0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    last_recheck_time: float = 0.0
    hits: int = 1
    needs_recheck: bool = True


@dataclass
class AudioSpeakerEvent:
    """Voice identification event (provisional or confirmed)."""
    identity: str
    confidence: float
    timestamp: float
    doa_deg: Optional[float] = None
    is_provisional: bool = False


@dataclass
class MultimodalPerson:
    """Consolidated multimodal identity for TRINITY and LLM context."""
    person_id: str
    fused_confidence: float
    primary_modality: str                      # 'face', 'voice', 'fused', 'unknown'
    modalities_present: List[str]              # e.g. ['face', 'voice_provisional']
    visual_track_id: Optional[int] = None
    face_confidence: float = 0.0
    voice_confidence: float = 0.0
    spatial_coherence: float = 1.0             # 0.0 (conflicting angles) to 1.0 (perfect alignment)
    is_provisional: bool = False
    conflict_detected: bool = False
    ask_confirmation: bool = False
    last_updated: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "person_id": self.person_id,
            "fused_confidence": round(self.fused_confidence, 3),
            "primary_modality": self.primary_modality,
            "modalities_present": self.modalities_present,
            "visual_track_id": self.visual_track_id,
            "face_confidence": round(self.face_confidence, 3),
            "voice_confidence": round(self.voice_confidence, 3),
            "spatial_coherence": round(self.spatial_coherence, 2),
            "is_provisional": self.is_provisional,
            "conflict_detected": self.conflict_detected,
            "ask_confirmation": self.ask_confirmation,
            "last_updated": round(self.last_updated, 3),
        }


class MultimodalIdentityTracker:
    """
    Multimodal Identity Tracker for Marcus.
    Coordinates vision-based facial recognition and audio-based speaker identification.
    """

    # Default cadence intervals for ArcFace re-verification per track (in seconds)
    RECHECK_INTERVALS = {
        "ENGAGED": 2.0,
        "VIGILANT": 5.0,
        "MOVING": 10.0,
        "STATIONARY": 5.0,
        "LOCAL_VLM": 10.0,
        "DOCKED_SLEEP": 30.0,
    }

    def __init__(
        self,
        camera_hfov_deg: float = 73.0,
        max_track_staleness_sec: float = 3.0,
        face_confidence_threshold: float = 0.72,
        voice_confidence_threshold: float = 0.70,
        spatial_match_angle_tol_deg: float = 35.0,
    ):
        self.camera_hfov_deg = camera_hfov_deg
        self.max_track_staleness_sec = max_track_staleness_sec
        self.face_thresh = face_confidence_threshold
        self.voice_thresh = voice_confidence_threshold
        self.spatial_angle_tol = spatial_match_angle_tol_deg

        self._lock = threading.RLock()
        self._tracks: Dict[int, VisualTrack] = {}
        self._next_track_id: int = 1
        self._recent_voice_event: Optional[AudioSpeakerEvent] = None
        self._active_person: Optional[MultimodalPerson] = None
        self._governor_state: str = "VIGILANT"

    def set_governor_state(self, state: str) -> None:
        """Sets the cognitive/kinematic state from the Resource Governor."""
        with self._lock:
            self._governor_state = state.upper()

    def get_recheck_interval(self) -> float:
        """Returns the current temporal re-verification interval."""
        return self.RECHECK_INTERVALS.get(self._governor_state, 5.0)

    def _calculate_azimuth(self, cx: float, img_w: float = 640.0) -> float:
        """
        Estimates horizontal azimuth angle in degrees relative to camera optical axis.
        cx in [0, img_w], where img_w/2 is 0 deg.
        """
        if img_w <= 0:
            return 0.0
        normalized_x = (cx - (img_w / 2.0)) / (img_w / 2.0)  # [-1.0, 1.0]
        azimuth = normalized_x * (self.camera_hfov_deg / 2.0)
        return float(np.clip(azimuth, -self.camera_hfov_deg / 2.0, self.camera_hfov_deg / 2.0))

    def update_visual_tracks(
        self,
        detections: List[Dict[str, Any]],
        timestamp: Optional[float] = None,
        img_w: float = 640.0,
        img_h: float = 480.0,
    ) -> List[int]:
        """
        Updates visual person tracks with new bounding boxes.
        Returns list of track IDs that require ArcFace re-verification.

        Each detection dict is expected to contain:
        - 'bbox': [x, y, w, h] or [x1, y1, x2, y2]
        - optionally 'confidence': float
        """
        if timestamp is None:
            timestamp = time.time()

        tracks_needing_recheck: List[int] = []
        recheck_interval = self.get_recheck_interval()

        with self._lock:
            # 1. Purge stale tracks
            dead_tracks = [
                tid for tid, trk in self._tracks.items()
                if (timestamp - trk.last_seen) > self.max_track_staleness_sec
            ]
            for tid in dead_tracks:
                del self._tracks[tid]

            # 2. Match detections to existing tracks
            unmatched_detections = []
            for det in detections:
                bbox_raw = det.get('bbox', [0, 0, 0, 0])
                if len(bbox_raw) == 4:
                    # Normalize to (x, y, w, h)
                    x, y, w, h = bbox_raw[0], bbox_raw[1], bbox_raw[2], bbox_raw[3]
                    # If format is [x1, y1, x2, y2]
                    if w > x and h > y and (w - x < img_w) and (h - y < img_h):
                        w = w - x
                        h = h - y
                else:
                    continue

                cx = x + w / 2.0
                cy = y + h / 2.0
                azimuth = self._calculate_azimuth(cx, img_w)

                # Find best matching existing track by Euclidean distance of centroids
                best_tid = None
                best_dist = float('inf')
                match_threshold = max(w, h, 60.0)

                for tid, trk in self._tracks.items():
                    dist = math.hypot(cx - trk.centroid[0], cy - trk.centroid[1])
                    if dist < match_threshold and dist < best_dist:
                        best_dist = dist
                        best_tid = tid

                if best_tid is not None:
                    # Update existing track
                    trk = self._tracks[best_tid]
                    trk.bbox = (x, y, w, h)
                    trk.centroid = (cx, cy)
                    trk.azimuth_deg = azimuth
                    trk.last_seen = timestamp
                    trk.hits += 1

                    # Check temporal re-verification policy
                    if trk.face_identity == "unknown":
                        trk.needs_recheck = True
                    elif (timestamp - trk.last_recheck_time) >= recheck_interval:
                        trk.needs_recheck = True

                    if trk.needs_recheck:
                        tracks_needing_recheck.append(best_tid)
                else:
                    unmatched_detections.append((x, y, w, h, cx, cy, azimuth))

            # 3. Create new tracks for unmatched detections
            for x, y, w, h, cx, cy, azimuth in unmatched_detections:
                new_tid = self._next_track_id
                self._next_track_id += 1
                new_track = VisualTrack(
                    track_id=new_tid,
                    bbox=(x, y, w, h),
                    centroid=(cx, cy),
                    azimuth_deg=azimuth,
                    first_seen=timestamp,
                    last_seen=timestamp,
                    last_recheck_time=0.0,
                    needs_recheck=True,
                )
                self._tracks[new_tid] = new_track
                tracks_needing_recheck.append(new_tid)

        return tracks_needing_recheck

    def update_face_identity(
        self,
        track_id: int,
        identity: str,
        confidence: float,
        timestamp: Optional[float] = None,
    ) -> None:
        """Updates the face identity for a given track after ArcFace inference."""
        if timestamp is None:
            timestamp = time.time()

        with self._lock:
            if track_id in self._tracks:
                trk = self._tracks[track_id]
                trk.face_identity = identity
                trk.face_confidence = confidence
                trk.last_recheck_time = timestamp
                trk.needs_recheck = False

    def update_voice_identity(
        self,
        identity: str,
        confidence: float,
        doa_deg: Optional[float] = None,
        is_provisional: bool = False,
        timestamp: Optional[float] = None,
    ) -> None:
        """
        Updates speaker recognition event from ECAPA-TDNN.
        Supports provisional (KWS buffer, <300ms) and confirmed (EOS, <500ms).
        """
        if timestamp is None:
            timestamp = time.time()

        with self._lock:
            self._recent_voice_event = AudioSpeakerEvent(
                identity=identity,
                confidence=confidence,
                timestamp=timestamp,
                doa_deg=doa_deg,
                is_provisional=is_provisional,
            )

    def _compute_spatial_coherence(
        self,
        track_azimuth_deg: float,
        audio_doa_deg: Optional[float],
    ) -> float:
        """
        Computes spatial coherence score in [0.0, 1.0] between visual azimuth
        and ReSpeaker DOA.
        """
        if audio_doa_deg is None:
            return 1.0  # In uncalibrated mode, don't penalize

        # Normalize DOA into [-180, 180] relative to robot front
        norm_doa = audio_doa_deg
        if norm_doa > 180.0:
            norm_doa -= 360.0

        diff = abs(track_azimuth_deg - norm_doa)
        if diff > 180.0:
            diff = 360.0 - diff

        if diff <= self.spatial_angle_tol:
            # High coherence
            return float(1.0 - (diff / (self.spatial_angle_tol * 2.0)))
        elif diff <= 90.0:
            return float(max(0.1, 0.5 - ((diff - self.spatial_angle_tol) / 120.0)))
        else:
            # Voice came from behind/side, face is in front: spatial conflict
            return 0.0

    def fuse_and_get_active_identities(
        self,
        timestamp: Optional[float] = None,
    ) -> Tuple[Optional[MultimodalPerson], List[MultimodalPerson]]:
        """
        Fuses current visual tracks and recent voice events into unified multimodal persons.
        Returns:
            (primary_active_person, all_detected_persons)
        """
        if timestamp is None:
            timestamp = time.time()

        all_persons: List[MultimodalPerson] = []
        voice_event = None

        with self._lock:
            # Check if voice event is fresh (< 4.0 seconds old)
            if self._recent_voice_event and (timestamp - self._recent_voice_event.timestamp) <= 4.0:
                voice_event = self._recent_voice_event

            # If we have visual tracks, attempt cross-modal association
            matched_voice = False

            for tid, trk in self._tracks.items():
                face_id = trk.face_identity
                face_conf = trk.face_confidence

                spatial_coherence = 1.0
                voice_id = None
                voice_conf = 0.0
                is_prov = False

                if voice_event and voice_event.confidence >= (self.voice_thresh - 0.15):
                    spatial_coherence = self._compute_spatial_coherence(
                        trk.azimuth_deg, voice_event.doa_deg
                    )
                    # Associate voice to this track if spatially plausible
                    if spatial_coherence > 0.2:
                        voice_id = voice_event.identity
                        voice_conf = voice_event.confidence
                        is_prov = voice_event.is_provisional
                        matched_voice = True

                # Determine fusion
                modalities = []
                conflict = False
                ask_conf = False

                if face_id != "unknown" and face_conf >= self.face_thresh:
                    modalities.append("face")

                if voice_id is not None:
                    mod_tag = "voice_provisional" if is_prov else "voice_confirmed"
                    modalities.append(mod_tag)

                # Fusion Logic
                if "face" in modalities and any("voice" in m for m in modalities):
                    if face_id.lower() == voice_id.lower():
                        # Congruent identity: Boost confidence!
                        # Independent probability fusion: 1 - (1 - Cf)(1 - Cv)
                        fused_score = 1.0 - (1.0 - face_conf) * (1.0 - voice_conf * spatial_coherence)
                        fused_score = min(0.99, max(face_conf, fused_score))
                        person_id = face_id
                        primary_mod = "fused"
                    else:
                        # Conflict! Visual says Person A, Voice says Person B
                        conflict = True
                        ask_conf = True
                        # Weighted towards face if visually centered and high confidence
                        if face_conf >= voice_conf:
                            person_id = face_id
                            fused_score = face_conf * 0.8
                            primary_mod = "face"
                        else:
                            person_id = voice_id
                            fused_score = voice_conf * 0.8
                            primary_mod = "voice"
                elif "face" in modalities:
                    person_id = face_id
                    fused_score = face_conf
                    primary_mod = "face"
                elif any("voice" in m for m in modalities):
                    person_id = voice_id
                    fused_score = voice_conf * spatial_coherence
                    primary_mod = "voice"
                else:
                    person_id = f"unknown_guest_{tid}"
                    fused_score = max(face_conf, 0.0)
                    primary_mod = "unknown"

                mm_person = MultimodalPerson(
                    person_id=person_id,
                    fused_confidence=fused_score,
                    primary_modality=primary_mod,
                    modalities_present=modalities,
                    visual_track_id=tid,
                    face_confidence=face_conf,
                    voice_confidence=voice_conf,
                    spatial_coherence=spatial_coherence,
                    is_provisional=is_prov,
                    conflict_detected=conflict,
                    ask_confirmation=ask_conf,
                    last_updated=timestamp,
                )
                all_persons.append(mm_person)

            # If voice was NOT associated with any visual track (speaker outside camera FOV / behind)
            if voice_event and not matched_voice and voice_event.confidence >= self.voice_thresh:
                mm_voice_only = MultimodalPerson(
                    person_id=voice_event.identity,
                    fused_confidence=voice_event.confidence,
                    primary_modality="voice",
                    modalities_present=["voice_provisional" if voice_event.is_provisional else "voice_confirmed"],
                    visual_track_id=None,
                    face_confidence=0.0,
                    voice_confidence=voice_event.confidence,
                    spatial_coherence=0.0,
                    is_provisional=voice_event.is_provisional,
                    conflict_detected=False,
                    ask_confirmation=False,
                    last_updated=timestamp,
                )
                all_persons.append(mm_voice_only)

            # Select primary interlocutor
            if all_persons:
                # Prioritize: non-unknown, highest fused confidence, then active voice
                all_persons.sort(
                    key=lambda p: (
                        not p.person_id.startswith("unknown"),
                        p.fused_confidence,
                        "fused" in p.primary_modality or "voice" in p.primary_modality,
                    ),
                    reverse=True,
                )
                self._active_person = all_persons[0]
            else:
                self._active_person = None

            return self._active_person, all_persons

    @property
    def active_person(self) -> Optional[MultimodalPerson]:
        with self._lock:
            return self._active_person

    def get_cag_context_string(self) -> str:
        """
        Returns a concise context string for CAG / TRINITY.
        Example: [BIOMETRICS] Active: Luca (fused 95%, face+voice) | Tracked: Luca, Filippo
        """
        with self._lock:
            if not self._active_person:
                return "[BIOMETRICS] No active person identified."

            p = self._active_person
            mods = "+".join(p.modalities_present) if p.modalities_present else "none"
            flags = []
            if p.is_provisional:
                flags.append("provisional")
            if p.conflict_detected:
                flags.append("CONFLICT")
            if p.ask_confirmation:
                flags.append("needs_confirmation")

            flag_str = f" [{', '.join(flags)}]" if flags else ""
            summary = f"[BIOMETRICS] Active: {p.person_id} ({p.primary_modality} {int(p.fused_confidence * 100)}%, {mods}){flag_str}"
            return summary
