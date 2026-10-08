#!/usr/bin/env python3
"""
Unit Tests for Phase F2: Multimodal Identity Tracker & Biometrics
================================================================
Validates:
- Visual track creation, centroid azimuth computation, and aging.
- Temporal re-verification policy across Governor states (ENGAGED 2s, VIGILANT 5s, MOVING 10s).
- Progressive 2-stage speaker biometrics (provisional vs confirmed).
- Spatial coherence gating between ReSpeaker DOA and visual azimuth.
- Multimodal Bayesian / weighted fusion (congruence, conflict, single-modality).
- Context formatting for CAG / TRINITY prompt injection.
"""

import os
import sys
import time
import pytest
import numpy as np

# Ensure robopy_controller is on sys.path for module resolution
_pkg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "robopy_controller"))
if _pkg_path not in sys.path:
    sys.path.insert(0, _pkg_path)

from robopy_controller.robot_ai.services.multimodal_identity_tracker import (
    MultimodalIdentityTracker,
    VisualTrack,
    AudioSpeakerEvent,
    MultimodalPerson,
)
from robopy_controller.robot_ai.trinity.cag_environment import EnvironmentSnapshot


def test_visual_track_creation_and_azimuth():
    tracker = MultimodalIdentityTracker(camera_hfov_deg=73.0)
    
    # Person right at optical center (320, 240) in 640x480 frame
    detections = [{'bbox': [270, 190, 100, 100]}]  # cx = 320, cy = 240
    t0 = 1000.0
    recheck_ids = tracker.update_visual_tracks(detections, timestamp=t0)
    
    assert len(recheck_ids) == 1
    tid = recheck_ids[0]
    track = tracker._tracks[tid]
    assert track.track_id == tid
    assert abs(track.azimuth_deg) < 1.0  # Centered at ~0 degrees
    assert track.needs_recheck is True
    
    # Person on extreme right edge (cx = 640)
    det_right = [{'bbox': [590, 190, 100, 100]}]  # cx = 640
    recheck_ids_2 = tracker.update_visual_tracks(det_right, timestamp=t0 + 0.1)
    trk_right = tracker._tracks[recheck_ids_2[0]]
    assert trk_right.azimuth_deg > 30.0  # Approx +36.5 deg


def test_temporal_recheck_cadence_governor_states():
    tracker = MultimodalIdentityTracker()
    t0 = 1000.0
    
    # 1. Start in VIGILANT (default interval = 5.0s)
    tracker.set_governor_state("VIGILANT")
    assert tracker.get_recheck_interval() == 5.0
    
    recheck_ids = tracker.update_visual_tracks([{'bbox': [100, 100, 50, 50]}], timestamp=t0)
    tid = recheck_ids[0]
    
    # Update face identity from ArcFace
    tracker.update_face_identity(tid, identity="Luca", confidence=0.88, timestamp=t0)
    assert tracker._tracks[tid].needs_recheck is False
    
    # Update track after 2 seconds (< 5s interval)
    rechecks_2s = tracker.update_visual_tracks([{'bbox': [100, 100, 50, 50]}], timestamp=t0 + 2.0)
    assert tid not in rechecks_2s  # Saved NPU inference!
    
    # Update track after 4 seconds (< 5s interval, keeps track alive)
    rechecks_4s = tracker.update_visual_tracks([{'bbox': [100, 100, 50, 50]}], timestamp=t0 + 4.0)
    assert tid not in rechecks_4s  # Saved NPU inference!

    # Update track after 5.5 seconds (>= 5s interval from last recheck at t0)
    rechecks_5s = tracker.update_visual_tracks([{'bbox': [100, 100, 50, 50]}], timestamp=t0 + 5.5)
    assert tid in rechecks_5s  # Recheck triggered!
    
    # 2. Switch to ENGAGED (interval = 2.0s)
    tracker.set_governor_state("ENGAGED")
    assert tracker.get_recheck_interval() == 2.0
    tracker.update_face_identity(tid, identity="Luca", confidence=0.90, timestamp=t0 + 6.0)
    
    # At t0 + 7.5s (1.5s later), no recheck
    assert tid not in tracker.update_visual_tracks([{'bbox': [100, 100, 50, 50]}], timestamp=t0 + 7.5)
    # At t0 + 8.2s (2.2s later), recheck triggered
    assert tid in tracker.update_visual_tracks([{'bbox': [100, 100, 50, 50]}], timestamp=t0 + 8.2)

    # 3. Switch to MOVING (interval = 10.0s)
    tracker.set_governor_state("MOVING")
    assert tracker.get_recheck_interval() == 10.0


def test_spatial_coherence_doa():
    tracker = MultimodalIdentityTracker()
    
    # Visual track centered at 0 degrees
    # DOA right in front (0 deg) -> high coherence
    coh_front = tracker._compute_spatial_coherence(track_azimuth_deg=0.0, audio_doa_deg=0.0)
    assert coh_front >= 0.95
    
    # DOA slightly offset (20 deg) -> still coherent
    coh_offset = tracker._compute_spatial_coherence(track_azimuth_deg=0.0, audio_doa_deg=20.0)
    assert coh_offset > 0.6
    
    # DOA behind robot (180 deg) while person in front -> zero coherence
    coh_behind = tracker._compute_spatial_coherence(track_azimuth_deg=0.0, audio_doa_deg=180.0)
    assert coh_behind == 0.0


def test_multimodal_fusion_congruent():
    tracker = MultimodalIdentityTracker()
    t0 = 100.0
    
    # Create visual track for Luca
    tracker.update_visual_tracks([{'bbox': [270, 190, 100, 100]}], timestamp=t0)
    tracker.update_face_identity(track_id=1, identity="Luca", confidence=0.85, timestamp=t0)
    
    # Voice says Luca with DOA aligned (front)
    tracker.update_voice_identity(
        identity="Luca",
        confidence=0.82,
        doa_deg=0.0,
        is_provisional=False,
        timestamp=t0 + 0.1,
    )
    
    active, all_p = tracker.fuse_and_get_active_identities(timestamp=t0 + 0.2)
    assert active is not None
    assert active.person_id == "Luca"
    assert active.primary_modality == "fused"
    assert active.fused_confidence > 0.90  # Boosted above individual scores
    assert active.conflict_detected is False
    assert active.ask_confirmation is False
    assert "face" in active.modalities_present
    assert "voice_confirmed" in active.modalities_present


def test_multimodal_fusion_conflict():
    tracker = MultimodalIdentityTracker()
    t0 = 100.0
    
    # Visual track recognized as Luca
    tracker.update_visual_tracks([{'bbox': [270, 190, 100, 100]}], timestamp=t0)
    tracker.update_face_identity(track_id=1, identity="Luca", confidence=0.85, timestamp=t0)
    
    # Voice from same direction says Edoardo
    tracker.update_voice_identity(
        identity="Edoardo",
        confidence=0.82,
        doa_deg=0.0,
        is_provisional=False,
        timestamp=t0 + 0.1,
    )
    
    active, all_p = tracker.fuse_and_get_active_identities(timestamp=t0 + 0.2)
    assert active is not None
    assert active.conflict_detected is True
    assert active.ask_confirmation is True


def test_voice_only_off_screen():
    tracker = MultimodalIdentityTracker()
    t0 = 100.0
    
    # Voice from behind (DOA 180 deg), no visual tracks
    tracker.update_voice_identity(
        identity="Luca",
        confidence=0.85,
        doa_deg=180.0,
        is_provisional=True,
        timestamp=t0,
    )
    
    active, all_p = tracker.fuse_and_get_active_identities(timestamp=t0 + 0.1)
    assert active is not None
    assert active.person_id == "Luca"
    assert active.primary_modality == "voice"
    assert active.is_provisional is True
    assert active.visual_track_id is None


def test_cag_environment_integration():
    env = EnvironmentSnapshot()
    env.update_location("salotto", (1.5, 2.0), map_name="casa_piano1", covariance_trace=0.025)
    
    # Without biometrics
    env.update_perception(humans=["Luca", "Filippo"], objects=["divano"])
    text_before = env.to_text()
    assert "Humans: Luca,Filippo" in text_before
    
    # With biometrics
    env.update_biometrics("[BIOMETRICS] Active: Luca (fused 95%, face+voice)")
    text_after = env.to_text()
    assert "Active: Luca (fused 95%, face+voice)" in text_after
