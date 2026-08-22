#!/usr/bin/env python3
"""
Robot AI Services - Face Recognition Service (NPU ArcFace Refactored)
====================================================================
Loads known face ArcFace embeddings (.npy) from person folders and matches
them against embeddings received from the Hailo NPU.
Also coordinates dynamic enrollment via FaceEnrollmentManager.

Version: 02.00.00 (NPU Integration)
"""

import os
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import cv2

from ..utils.logging_utils import get_logger
from ..core.event_bus import EventBus, EventType
from .face_enrollment_manager import FaceEnrollmentManager


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class UserProfile:
    """Profile for a recognized user."""
    user_id: str
    name: str
    tone_preference: str = "informal"
    proactivity_level: float = 0.6
    voice_speed: str = "normal"


@dataclass
class FaceRecognitionResult:
    """Result of a face recognition attempt."""
    recognized: bool = False
    user_id: str = ""
    name: str = ""
    confidence: float = 0.0
    fallback_to_generic: bool = True
    ask_confirmation: bool = False
    likely_user: str = ""
    num_faces_detected: int = 0
    enrollment_complete: bool = False


# Default user profiles — can be extended via config
DEFAULT_USER_PROFILES: Dict[str, UserProfile] = {
    "luca": UserProfile(user_id="user_luca", name="Luca", tone_preference="informal", proactivity_level=0.7),
    "edoardo": UserProfile(user_id="user_edoardo", name="Edoardo", tone_preference="informal", proactivity_level=0.6),
    "filippo": UserProfile(user_id="user_filippo", name="Filippo", tone_preference="informal", proactivity_level=0.6),
    "isabella": UserProfile(user_id="user_isabella", name="Isabella", tone_preference="informal", proactivity_level=0.5),
    "jacopo": UserProfile(user_id="user_jacopo", name="Jacopo", tone_preference="informal", proactivity_level=0.6),
    "luisella": UserProfile(user_id="user_luisella", name="Luisella", tone_preference="formal", proactivity_level=0.4),
    "rosaria": UserProfile(user_id="user_rosaria", name="Rosaria", tone_preference="formal", proactivity_level=0.3),
}

# Guest profile for unrecognized faces
GUEST_PROFILE = UserProfile(
    user_id="guest_temporary",
    name="Amico",
    tone_preference="neutral",
    proactivity_level=0.5,
    voice_speed="normal",
)


class FaceRecognitionService:
    """
    Face recognition service using ArcFace embeddings computed on the Hailo NPU.
    
    Loads known face embeddings (.npy files) from the known_faces directory,
    and performs fast cosine similarity matching against incoming embeddings.
    
    Confidence thresholds:
        >= 0.80: Recognized (high confidence)
        0.60 - 0.80: Uncertain (ask confirmation)
        < 0.60: Unknown / guest -> trigger enrollment if context permits
    """
    
    def __init__(
        self,
        known_faces_dir: str = "",
        tolerance: float = 0.5,
        confidence_high: float = 0.80,
        confidence_low: float = 0.60,
        user_profiles: Optional[Dict[str, UserProfile]] = None,
    ):
        self.logger = get_logger("face_recognition")
        self._lock = threading.Lock()
        
        self.tolerance = tolerance
        self.confidence_high = confidence_high
        self.confidence_low = confidence_low
        self.known_faces_dir = known_faces_dir
        
        # Known face data (name -> normalized embedding array)
        self._known_embeddings: Dict[str, np.ndarray] = {}
        
        # User profiles
        self._user_profiles: Dict[str, UserProfile] = user_profiles or DEFAULT_USER_PROFILES.copy()
        
        # Event Bus singleton
        self.event_bus = EventBus()
        
        # Enrollment manager
        self.enrollment_manager = FaceEnrollmentManager(known_faces_dir)
        
        # Last recognized result (cached)
        self._last_result: FaceRecognitionResult = FaceRecognitionResult()
        self._last_recognition_time: float = 0.0
        
        # Stats
        self._total_recognitions: int = 0
        self._successful_recognitions: int = 0
        
        # Load known faces
        if known_faces_dir and os.path.isdir(known_faces_dir):
            self.load_known_faces()
        else:
            self.logger.warning(f"⚠️ known_faces_dir not found or empty: {known_faces_dir}")
    
    def load_known_faces(self):
        """Loads all .npy embeddings from known_faces_dir."""
        with self._lock:
            self._known_embeddings = {}
            if not self.known_faces_dir or not os.path.isdir(self.known_faces_dir):
                self.logger.warning(f"known_faces_dir not found: {self.known_faces_dir}")
                return
                
            self.logger.info(f"📂 Loading ArcFace embeddings from: {self.known_faces_dir}")
            for root, dirs, files in os.walk(self.known_faces_dir):
                for file in files:
                    if file.endswith(".npy"):
                        name = os.path.splitext(file)[0].lower()
                        npy_path = os.path.join(root, file)
                        try:
                            emb = np.load(npy_path)
                            if emb.shape == (512,):
                                # Ensure L2 normalization
                                norm = np.linalg.norm(emb)
                                if norm > 0:
                                    emb = emb / norm
                                self._known_embeddings[name] = emb
                                self.logger.info(f"  ✅ Loaded embedding for {name} from {file}")
                            else:
                                self.logger.warning(f"  ⚠️ Skipping {file}: expected shape (512,), got {emb.shape}")
                        except Exception as e:
                            self.logger.error(f"  ❌ Failed to load {file}: {e}")
            
            self.logger.info(f"✅ Loaded {len(self._known_embeddings)} known faces.")

    @property
    def is_enrolling(self) -> bool:
        """Check if enrollment is active."""
        return self.enrollment_manager.is_enrolling

    @property
    def enrollment_name(self) -> Optional[str]:
        """Get current enrollment name."""
        return self.enrollment_manager.current_name

    def start_enrollment(self, name: str, num_samples: int = 10) -> bool:
        """Start dynamic face enrollment session."""
        return self.enrollment_manager.start_enrollment(name, num_samples)

    def cancel_enrollment(self) -> None:
        """Cancel dynamic face enrollment session."""
        self.enrollment_manager.cancel_enrollment()

    def process_face_embedding(self, embedding: List[float], crop_image: Optional[np.ndarray] = None) -> FaceRecognitionResult:
        """
        Processes a face embedding vector received from the Hailo NPU.
        If in enrollment mode, accumulates the sample.
        Otherwise, performs cosine similarity matching.
        """
        if embedding is None or len(embedding) == 0:
            return FaceRecognitionResult()
            
        emb_arr = np.array(embedding, dtype=np.float32).flatten()
        if emb_arr.shape != (512,):
            self.logger.warning(f"Received face embedding of invalid shape: {emb_arr.shape}")
            return FaceRecognitionResult()
            
        # Standardize normalization (L2 norm)
        norm = np.linalg.norm(emb_arr)
        if norm > 0:
            emb_arr = emb_arr / norm

        # Enrollment mode
        if self.is_enrolling:
            enroll_name = self.enrollment_name
            complete = self.enrollment_manager.add_sample(emb_arr, crop_image)
            if complete:
                self.logger.info(f"Enrollment completed for {enroll_name}. Reloading database.")
                self.load_known_faces()
                return FaceRecognitionResult(
                    recognized=True,
                    name=enroll_name,
                    confidence=1.0,
                    enrollment_complete=True,
                    fallback_to_generic=False
                )
            else:
                return FaceRecognitionResult(
                    recognized=False,
                    name=enroll_name,
                    confidence=0.0,
                    fallback_to_generic=True
                )

        # Match mode
        best_name = ""
        best_similarity = 0.0
        
        with self._lock:
            for name, known_emb in self._known_embeddings.items():
                similarity = float(np.dot(emb_arr, known_emb))
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_name = name

        self._total_recognitions += 1
        
        result = FaceRecognitionResult(num_faces_detected=1)
        
        if best_similarity >= self.confidence_high:
            profile = self._user_profiles.get(best_name, UserProfile(user_id=f"user_{best_name}", name=best_name.capitalize()))
            result = FaceRecognitionResult(
                recognized=True,
                user_id=profile.user_id,
                name=profile.name,
                confidence=round(best_similarity, 3),
                fallback_to_generic=False,
                ask_confirmation=False,
                num_faces_detected=1
            )
            self._successful_recognitions += 1
            
            # Publish event to EventBus for world model and other subscribers
            self.event_bus.publish(EventType.FACE_RECOGNIZED, {"name": profile.name, "confidence": best_similarity})
            
        elif best_similarity >= self.confidence_low:
            profile = self._user_profiles.get(best_name, UserProfile(user_id=f"user_{best_name}", name=best_name.capitalize()))
            result = FaceRecognitionResult(
                recognized=False,
                user_id="",
                name="",
                confidence=round(best_similarity, 3),
                fallback_to_generic=True,
                ask_confirmation=True,
                likely_user=profile.name,
                num_faces_detected=1
            )
        else:
            # Below low threshold -> unknown guest
            result = FaceRecognitionResult(
                recognized=False,
                user_id="guest_temporary",
                name="Amico",
                confidence=round(best_similarity, 3),
                fallback_to_generic=True,
                ask_confirmation=False,
                num_faces_detected=1
            )

        with self._lock:
            self._last_result = result
            self._last_recognition_time = time.time()
            
        return result

    def recognize(self, frame_b64: str) -> FaceRecognitionResult:
        """
        Deprecated. Use process_face_embedding instead.
        Kept for backward compatibility.
        """
        self.logger.warning("recognize() is deprecated. Please use process_face_embedding() with NPU vectors.")
        return self.last_result
        
    async def process_image_async(self, image_data: bytes):
        """Deprecated. Face recognition is now computed on NPU."""
        pass
    
    @property
    def last_result(self) -> FaceRecognitionResult:
        """Get last recognition result."""
        with self._lock:
            return self._last_result
    
    @property
    def is_available(self) -> bool:
        """Check if face recognition is available and loaded."""
        return len(self._known_embeddings) > 0
    
    def get_user_profile(self, name: str) -> UserProfile:
        """Get user profile by name (lowercase key)."""
        return self._user_profiles.get(name.lower(), GUEST_PROFILE)
    
    def get_profile_for_gemini(self, result: Optional[FaceRecognitionResult] = None) -> Dict:
        """
        Build user profile context for Gemini LLM.
        """
        if result is None:
            result = self.last_result
        
        if result.recognized:
            profile = self._user_profiles.get(result.name.lower(), GUEST_PROFILE)
            return {
                "face_recognition": {
                    "recognized": True,
                    "user_id": profile.user_id,
                    "confidence": result.confidence,
                    "fallback_to_generic": False,
                },
                "user_profile": {
                    "name": profile.name,
                    "tone_preference": profile.tone_preference,
                    "proactivity_level": profile.proactivity_level,
                    "voice_speed": profile.voice_speed,
                },
            }
        elif result.ask_confirmation and result.likely_user:
            return {
                "face_recognition": {
                    "recognized": False,
                    "confidence": result.confidence,
                    "likely_user": result.likely_user,
                    "fallback_to_generic": True,
                    "ask_confirmation": True,
                },
                "user_profile": {
                    "name": "Amico",
                    "tone_preference": "neutral",
                    "proactivity_level": 0.5,
                    "voice_speed": "normal",
                    "note": f"Utente non riconosciuto con certezza. Potrebbe essere {result.likely_user}."
                },
            }
        else:
            return {
                "face_recognition": {
                    "recognized": False,
                    "confidence": 0.0,
                    "fallback_to_generic": True,
                },
                "user_profile": {
                    "name": "Amico",
                    "tone_preference": "neutral",
                    "proactivity_level": 0.5,
                    "voice_speed": "normal",
                    "note": "Utente non riconosciuto, usa profilo generico.",
                },
            }
    
    def get_statistics(self) -> Dict:
        """Get recognition statistics."""
        return {
            "known_people": len(self._known_embeddings),
            "total_recognitions": self._total_recognitions,
            "successful_recognitions": self._successful_recognitions,
            "success_rate": (
                self._successful_recognitions / max(1, self._total_recognitions)
            ),
            "available": self.is_available,
        }
