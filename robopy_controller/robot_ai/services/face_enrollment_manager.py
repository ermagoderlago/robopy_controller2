import os
import time
import numpy as np
import cv2
from typing import Optional, List, Dict
from ..utils.logging_utils import get_logger

class FaceEnrollmentManager:
    """
    Manages dynamic enrollment of new faces for Marcus.
    Collects a specified number of ArcFace 512-dim embedding samples,
    averages them for robustness, and saves them as a normalized .npy vector.
    Also saves raw crop images for visual reference.
    """
    def __init__(self, known_faces_dir: str):
        self.logger = get_logger("face_enrollment")
        self.known_faces_dir = known_faces_dir
        
        # State variables for active enrollment
        self._active_name: Optional[str] = None
        self._target_samples: int = 10
        self._collected_embeddings: List[np.ndarray] = []
        self._collected_crops: List[np.ndarray] = []
        self._is_active: bool = False

    @property
    def is_enrolling(self) -> bool:
        """Check if enrollment is currently active."""
        return self._is_active

    @property
    def current_name(self) -> Optional[str]:
        """Get the name of the person currently being enrolled."""
        return self._active_name

    def start_enrollment(self, name: str, num_samples: int = 10) -> bool:
        """
        Starts the enrollment session for a person.
        """
        if not name:
            self.logger.error("Cannot start enrollment without a name.")
            return False
            
        self._active_name = name.strip().lower()
        self._target_samples = num_samples
        self._collected_embeddings = []
        self._collected_crops = []
        self._is_active = True
        
        self.logger.info(f"🎤 Sessione enrollment avviata per: {self._active_name} (soglia: {num_samples} frame)")
        return True

    def cancel_enrollment(self) -> None:
        """Cancels the active enrollment session."""
        self._active_name = None
        self._collected_embeddings = []
        self._collected_crops = []
        self._is_active = False
        self.logger.info("Enrollment session cancelled.")

    def add_sample(self, embedding: np.ndarray, crop_image: Optional[np.ndarray] = None) -> bool:
        """
        Adds a single embedding sample (and optional face crop image) to the active session.
        
        Returns:
            True if enrollment is now complete, False if more samples are needed.
        """
        if not self._is_active or self._active_name is None:
            return False
            
        if embedding is None or len(embedding) != 512:
            self.logger.warning("Invalid embedding size received, skipping sample.")
            return False
            
        # Standardize embedding shape to (512,)
        emb_arr = np.array(embedding, dtype=np.float32).flatten()
        
        self._collected_embeddings.append(emb_arr)
        if crop_image is not None:
            self._collected_crops.append(crop_image.copy())
            
        self.logger.info(f"📸 Campione face aggiunto ({len(self._collected_embeddings)}/{self._target_samples}) per {self._active_name}")
        
        if len(self._collected_embeddings) >= self._target_samples:
            return self._finalize_enrollment()
            
        return False

    def _finalize_enrollment(self) -> bool:
        """
        Computes the average embedding, normalizes it, and saves all data.
        """
        try:
            name = self._active_name
            person_dir = os.path.join(self.known_faces_dir, name)
            os.makedirs(person_dir, exist_ok=True)
            
            # Compute average embedding
            avg_embedding = np.mean(self._collected_embeddings, axis=0)
            
            # Normalize embedding (L2 Norm) so cosine similarity is just dot product
            norm = np.linalg.norm(avg_embedding)
            if norm > 0:
                avg_embedding = avg_embedding / norm
                
            # Save the embedding as .npy
            npy_path = os.path.join(person_dir, f"{name}.npy")
            np.save(npy_path, avg_embedding)
            self.logger.info(f"💾 Salvato embedding ArcFace in: {npy_path}")
            
            # Save face crops
            for i, crop in enumerate(self._collected_crops):
                crop_path = os.path.join(person_dir, f"face_{int(time.time())}_{i}.jpg")
                cv2.imwrite(crop_path, crop)
                
            self.logger.info(f"✅ Enrollment completato con successo per: {name} ({len(self._collected_crops)} foto salvate)")
            
            # Reset state
            self._is_active = False
            self._active_name = None
            self._collected_embeddings = []
            self._collected_crops = []
            return True
            
        except Exception as e:
            self.logger.error(f"Errore durante la finalizzazione dell'enrollment: {e}")
            self.cancel_enrollment()
            return False
