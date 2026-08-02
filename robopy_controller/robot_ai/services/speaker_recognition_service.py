import os
import time
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np

from ..utils.logging_utils import get_logger
from ..core.event_bus import EventBus, EventType
from .speaker_enrollment_manager import SpeakerEnrollmentManager

@dataclass
class SpeakerRecognitionResult:
    """Result of a speaker recognition attempt."""
    recognized: bool = False
    name: str = ""
    confidence: float = 0.0
    enrollment_complete: bool = False
    fallback_to_generic: bool = True

class SpeakerRecognitionService:
    """
    Speaker recognition service using 192-dim voice embeddings.
    Loads known speaker embeddings (.npy files) from the known_speakers directory,
    and performs cosine similarity matching.
    """
    def __init__(
        self,
        known_speakers_dir: str = "",
        confidence_high: float = 0.75,
        confidence_low: float = 0.60,
    ):
        self.logger = get_logger("speaker_recognition")
        self._lock = threading.Lock()
        
        self.confidence_high = confidence_high
        self.confidence_low = confidence_low
        self.known_speakers_dir = known_speakers_dir
        
        # Known speaker data (name -> normalized embedding array)
        self._known_embeddings: Dict[str, np.ndarray] = {}
        
        # Event Bus singleton
        self.event_bus = EventBus()
        
        # Enrollment manager
        self.enrollment_manager = SpeakerEnrollmentManager(known_speakers_dir)
        
        # Load known speakers
        if known_speakers_dir and os.path.isdir(known_speakers_dir):
            self.load_known_speakers()
        else:
            self.logger.warning(f"⚠️ known_speakers_dir not found: {known_speakers_dir}")

    def load_known_speakers(self):
        """Loads all .npy embeddings from known_speakers_dir."""
        with self._lock:
            self._known_embeddings = {}
            if not self.known_speakers_dir or not os.path.isdir(self.known_speakers_dir):
                self.logger.warning(f"known_speakers_dir not found: {self.known_speakers_dir}")
                return
                
            self.logger.info(f"📂 Loading Speaker embeddings from: {self.known_speakers_dir}")
            for root, dirs, files in os.walk(self.known_speakers_dir):
                for file in files:
                    if file.endswith(".npy"):
                        name = os.path.splitext(file)[0].lower()
                        npy_path = os.path.join(root, file)
                        try:
                            emb = np.load(npy_path)
                            if emb.shape == (192,):
                                # Ensure L2 normalization
                                norm = np.linalg.norm(emb)
                                if norm > 0:
                                    emb = emb / norm
                                self._known_embeddings[name] = emb
                                self.logger.info(f"  ✅ Loaded speaker embedding for {name}")
                            else:
                                self.logger.warning(f"  ⚠️ Skipping {file}: expected shape (192,), got {emb.shape}")
                        except Exception as e:
                            self.logger.error(f"  ❌ Failed to load speaker {file}: {e}")
            
            self.logger.info(f"✅ Loaded {len(self._known_embeddings)} known speakers.")

    @property
    def is_enrolling(self) -> bool:
        """Check if enrollment is active."""
        return self.enrollment_manager.is_enrolling

    @property
    def enrollment_name(self) -> Optional[str]:
        """Get current enrollment name."""
        return self.enrollment_manager.current_name

    def start_enrollment(self, name: str, num_samples: int = 5) -> bool:
        """Start dynamic speaker enrollment session."""
        return self.enrollment_manager.start_enrollment(name, num_samples)

    def cancel_enrollment(self) -> None:
        """Cancel dynamic speaker enrollment session."""
        self.enrollment_manager.cancel_enrollment()

    def process_speaker_embedding(self, embedding: List[float]) -> SpeakerRecognitionResult:
        """
        Processes a speaker embedding vector.
        If in enrollment mode, accumulates the sample.
        Otherwise, performs cosine similarity matching.
        """
        if embedding is None or len(embedding) == 0:
            return SpeakerRecognitionResult()
            
        emb_arr = np.array(embedding, dtype=np.float32).flatten()
        if emb_arr.shape != (192,):
            self.logger.warning(f"Received speaker embedding of invalid shape: {emb_arr.shape}")
            return SpeakerRecognitionResult()
            
        # Standardize normalization
        norm = np.linalg.norm(emb_arr)
        if norm > 0:
            emb_arr = emb_arr / norm

        # Enrollment mode
        if self.is_enrolling:
            enroll_name = self.enrollment_name
            complete = self.enrollment_manager.add_sample(emb_arr)
            if complete:
                self.logger.info(f"Enrollment completed for speaker {enroll_name}. Reloading database.")
                self.load_known_speakers()
                return SpeakerRecognitionResult(
                    recognized=True,
                    name=enroll_name,
                    confidence=1.0,
                    enrollment_complete=True,
                    fallback_to_generic=False
                )
            else:
                return SpeakerRecognitionResult(
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

        if best_similarity >= self.confidence_high:
            result = SpeakerRecognitionResult(
                recognized=True,
                name=best_name,
                confidence=round(best_similarity, 3),
                fallback_to_generic=False
            )
            # Publish event to EventBus
            self.event_bus.publish(EventType.USER_SPOKE, {"name": best_name, "speaker_confidence": best_similarity})
        else:
            # 💡 UTENTE REQUEST: Salva l'embedding come unknown_XX per raggruppare le voci
            new_id = self._generate_new_unknown_id()
            self._save_unknown_embedding(new_id, emb_arr)
            
            result = SpeakerRecognitionResult(
                recognized=False,
                name=new_id,
                confidence=0.0,
                fallback_to_generic=True
            )

        return result

    def _generate_new_unknown_id(self) -> str:
        """Trova il prossimo ID libero per gli speaker sconosciuti (es. unknown_01, unknown_02)."""
        max_id = 0
        with self._lock:
            for name in self._known_embeddings.keys():
                if name.startswith("unknown_"):
                    try:
                        num = int(name.split("_")[1])
                        if num > max_id:
                            max_id = num
                    except ValueError:
                        pass
        return f"unknown_{max_id + 1:02d}"

    def _save_unknown_embedding(self, name: str, emb_arr: np.ndarray):
        """Salva il nuovo embedding su disco e in memoria."""
        if not self.known_speakers_dir:
            return
        os.makedirs(self.known_speakers_dir, exist_ok=True)
        file_path = os.path.join(self.known_speakers_dir, f"{name}.npy")
        try:
            np.save(file_path, emb_arr)
            with self._lock:
                self._known_embeddings[name] = emb_arr
            self.logger.info(f"🆕 Nuova voce sconosciuta registrata come {name}")
        except Exception as e:
            self.logger.error(f"❌ Errore nel salvataggio di {name}: {e}")
