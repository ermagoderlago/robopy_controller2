#!/usr/bin/env python3
"""
Robot AI Services - Face Recognition Service
=============================================
Face recognition using the face_recognition library (dlib-based).
Loads known face encodings from a directory of person folders and matches
them against live camera frames.

Directory structure expected:
    known_faces/
        luca/
            luca_1.jpg
            face_xxx.jpg
        edoardo/
            edoardo_1.jpg
            ...
"""

import os
import time
import base64
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

from ..utils.logging_utils import get_logger


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class UserProfile:
    """Profile for a recognized user (from marcus_AI.md section 6.2)."""
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
    Face recognition service using face_recognition (dlib).
    
    Loads known face encodings from a directory tree at startup,
    then provides a recognize() method to match against camera frames.
    
    Confidence thresholds (from marcus_AI.md section 6.1):
        >= 0.80: Recognized (high confidence)
        0.60 - 0.80: Uncertain (ask confirmation)
        < 0.60: Unknown / guest
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
        
        # Known face data
        self._known_encodings: List[np.ndarray] = []
        self._known_names: List[str] = []
        
        # User profiles
        self._user_profiles: Dict[str, UserProfile] = user_profiles or DEFAULT_USER_PROFILES.copy()
        
        # Last recognized result (cached)
        self._last_result: FaceRecognitionResult = FaceRecognitionResult()
        self._last_recognition_time: float = 0.0
        
        # Stats
        self._total_recognitions: int = 0
        self._successful_recognitions: int = 0
        
        if not FACE_RECOGNITION_AVAILABLE:
            self.logger.error("❌ face_recognition library not available! pip install face_recognition")
            return
        
        if not CV2_AVAILABLE:
            self.logger.error("❌ cv2 (OpenCV) not available!")
            return
        
        # Load known faces
        if known_faces_dir and os.path.isdir(known_faces_dir):
            self._load_known_faces(known_faces_dir)
        else:
            self.logger.warning(f"⚠️ known_faces_dir not found or empty: {known_faces_dir}")
    
    def _load_known_faces(self, base_dir: str):
        """
        Load face encodings from directory structure:
            base_dir/
                person_name/
                    image1.jpg
                    image2.jpg
                    ...
        """
        total_loaded = 0
        total_failed = 0
        
        self.logger.info(f"📂 Loading known faces from: {base_dir}")
        
        for person_name in sorted(os.listdir(base_dir)):
            person_dir = os.path.join(base_dir, person_name)
            if not os.path.isdir(person_dir):
                continue
            
            person_encodings = []
            
            for img_file in sorted(os.listdir(person_dir)):
                img_path = os.path.join(person_dir, img_file)
                if not img_file.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp')):
                    continue
                
                try:
                    image = face_recognition.load_image_file(img_path)
                    encodings = face_recognition.face_encodings(image)
                    
                    if encodings:
                        # Use the first face found in the image
                        person_encodings.append(encodings[0])
                        total_loaded += 1
                    else:
                        self.logger.warning(f"  ⚠️ No face found in: {img_file}")
                        total_failed += 1
                        
                except Exception as e:
                    self.logger.warning(f"  ❌ Failed to load {img_file}: {e}")
                    total_failed += 1
            
            if person_encodings:
                # Store all encodings for this person
                for enc in person_encodings:
                    self._known_encodings.append(enc)
                    self._known_names.append(person_name)
                
                self.logger.info(f"  ✅ {person_name}: {len(person_encodings)} encoding(s) loaded")
            else:
                self.logger.warning(f"  ⚠️ {person_name}: no valid encodings loaded")
        
        self.logger.info(
            f"✅ Face DB ready: {total_loaded} encodings for "
            f"{len(set(self._known_names))} people "
            f"({total_failed} failed)"
        )
    
    def recognize(self, frame_b64: str) -> FaceRecognitionResult:
        """
        Recognize faces in a base64-encoded JPEG/PNG frame.
        
        Args:
            frame_b64: Base64-encoded image bytes
            
        Returns:
            FaceRecognitionResult with recognition details
        """
        if not FACE_RECOGNITION_AVAILABLE or not CV2_AVAILABLE:
            return FaceRecognitionResult()
        
        if not self._known_encodings:
            return FaceRecognitionResult()
        
        try:
            # Decode base64 → numpy image
            img_bytes = base64.b64decode(frame_b64)
            np_arr = np.frombuffer(img_bytes, dtype=np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            
            if frame is None:
                return FaceRecognitionResult()
            
            # Downscale for speed (face_recognition works fine at lower res)
            scale = 0.5
            small_frame = cv2.resize(frame, (0, 0), fx=scale, fy=scale)
            
            # Convert BGR → RGB (face_recognition uses RGB)
            rgb_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
            
            # Detect face locations and encodings
            face_locations = face_recognition.face_locations(rgb_frame, model="hog")
            
            if not face_locations:
                return FaceRecognitionResult(num_faces_detected=0)
            
            face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
            
            self._total_recognitions += 1
            
            # Find best match among all detected faces
            best_result = FaceRecognitionResult(num_faces_detected=len(face_locations))
            best_confidence = 0.0
            
            for face_encoding in face_encodings:
                # Compare against all known encodings
                distances = face_recognition.face_distance(
                    self._known_encodings, face_encoding
                )
                
                if len(distances) == 0:
                    continue
                
                # Find best match
                best_idx = int(np.argmin(distances))
                best_distance = distances[best_idx]
                
                # Convert distance to confidence (0-1 scale)
                # face_recognition distance: 0 = perfect match, ~0.6 = threshold
                # Confidence: 1 - distance (clamped)
                confidence = max(0.0, min(1.0, 1.0 - best_distance))
                
                if confidence > best_confidence:
                    best_confidence = confidence
                    matched_name = self._known_names[best_idx]
                    
                    if confidence >= self.confidence_high:
                        # HIGH CONFIDENCE — Recognized!
                        profile = self._user_profiles.get(matched_name, GUEST_PROFILE)
                        best_result = FaceRecognitionResult(
                            recognized=True,
                            user_id=profile.user_id,
                            name=profile.name,
                            confidence=round(confidence, 3),
                            fallback_to_generic=False,
                            ask_confirmation=False,
                            num_faces_detected=len(face_locations),
                        )
                        self._successful_recognitions += 1
                        
                    elif confidence >= self.confidence_low:
                        # UNCERTAIN — Ask confirmation
                        profile = self._user_profiles.get(matched_name, GUEST_PROFILE)
                        best_result = FaceRecognitionResult(
                            recognized=False,
                            user_id="",
                            name="",
                            confidence=round(confidence, 3),
                            fallback_to_generic=True,
                            ask_confirmation=True,
                            likely_user=profile.name,
                            num_faces_detected=len(face_locations),
                        )
                    # else: below threshold, stays as generic/unknown
            
            with self._lock:
                self._last_result = best_result
                self._last_recognition_time = time.time()
            
            return best_result
            
        except Exception as e:
            self.logger.error(f"Face recognition error: {e}")
            return FaceRecognitionResult()
    
    @property
    def last_result(self) -> FaceRecognitionResult:
        """Get last recognition result."""
        with self._lock:
            return self._last_result
    
    @property
    def is_available(self) -> bool:
        """Check if face recognition is available and loaded."""
        return FACE_RECOGNITION_AVAILABLE and CV2_AVAILABLE and len(self._known_encodings) > 0
    
    def get_user_profile(self, name: str) -> UserProfile:
        """Get user profile by name (lowercase key)."""
        return self._user_profiles.get(name.lower(), GUEST_PROFILE)
    
    def get_profile_for_gemini(self, result: Optional[FaceRecognitionResult] = None) -> Dict:
        """
        Build user profile context for Gemini LLM.
        Follows marcus_AI.md section 6 format — no face hashes, anonymous user_id.
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
            "known_people": len(set(self._known_names)),
            "known_encodings": len(self._known_encodings),
            "total_recognitions": self._total_recognitions,
            "successful_recognitions": self._successful_recognitions,
            "success_rate": (
                self._successful_recognitions / max(1, self._total_recognitions)
            ),
            "available": self.is_available,
        }
