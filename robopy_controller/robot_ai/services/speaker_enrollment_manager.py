import os
import time
import numpy as np
from typing import Optional, List
from ..utils.logging_utils import get_logger

class SpeakerEnrollmentManager:
    """
    Manages dynamic enrollment of new speakers for Marcus.
    Collects a specified number of 192-dim speaker embeddings,
    averages them for robustness, and saves them as a normalized .npy vector.
    """
    def __init__(self, known_speakers_dir: str):
        self.logger = get_logger("speaker_enrollment")
        self.known_speakers_dir = known_speakers_dir
        
        # State variables for active enrollment
        self._active_name: Optional[str] = None
        self._target_samples: int = 5
        self._collected_embeddings: List[np.ndarray] = []
        self._is_active: bool = False

    @property
    def is_enrolling(self) -> bool:
        """Check if enrollment is currently active."""
        return self._is_active

    @property
    def current_name(self) -> Optional[str]:
        """Get the name of the speaker currently being enrolled."""
        return self._active_name

    def start_enrollment(self, name: str, num_samples: int = 5) -> bool:
        """
        Starts the speaker enrollment session.
        """
        if not name:
            self.logger.error("Cannot start speaker enrollment without a name.")
            return False
            
        self._active_name = name.strip().lower()
        self._target_samples = num_samples
        self._collected_embeddings = []
        self._is_active = True
        
        self.logger.info(f"🎤 Sessione enrollment speaker avviata per: {self._active_name} (soglia: {num_samples} segmenti)")
        return True

    def cancel_enrollment(self) -> None:
        """Cancels the active enrollment session."""
        self._active_name = None
        self._collected_embeddings = []
        self._is_active = False
        self.logger.info("Speaker enrollment session cancelled.")

    def add_sample(self, embedding: np.ndarray) -> bool:
        """
        Adds a single speaker embedding sample to the active session.
        
        Returns:
            True if enrollment is now complete, False if more samples are needed.
        """
        if not self._is_active or self._active_name is None:
            return False
            
        if embedding is None or len(embedding) != 192:
            self.logger.warning(f"Invalid speaker embedding size received (expected 192, got {len(embedding) if embedding is not None else 0}), skipping sample.")
            return False
            
        emb_arr = np.array(embedding, dtype=np.float32).flatten()
        
        self._collected_embeddings.append(emb_arr)
        self.logger.info(f"🎙️ Impronta vocale aggiunta ({len(self._collected_embeddings)}/{self._target_samples}) per {self._active_name}")
        
        if len(self._collected_embeddings) >= self._target_samples:
            return self._finalize_enrollment()
            
        return False

    def _finalize_enrollment(self) -> bool:
        """
        Computes the average speaker embedding, normalizes it, and saves the file.
        """
        try:
            name = self._active_name
            person_dir = os.path.join(self.known_speakers_dir, name)
            os.makedirs(person_dir, exist_ok=True)
            
            # Compute average embedding
            avg_embedding = np.mean(self._collected_embeddings, axis=0)
            
            # Normalize embedding (L2 Norm)
            norm = np.linalg.norm(avg_embedding)
            if norm > 0:
                avg_embedding = avg_embedding / norm
                
            # Save the embedding as .npy
            npy_path = os.path.join(person_dir, f"{name}.npy")
            np.save(npy_path, avg_embedding)
            self.logger.info(f"💾 Salvato embedding Speaker ID in: {npy_path}")
            
            self.logger.info(f"✅ Enrollment speaker completato con successo per: {name}")
            
            # Reset state
            self._is_active = False
            self._active_name = None
            self._collected_embeddings = []
            return True
            
        except Exception as e:
            self.logger.error(f"Errore durante la finalizzazione dell'enrollment speaker: {e}")
            self.cancel_enrollment()
            return False
