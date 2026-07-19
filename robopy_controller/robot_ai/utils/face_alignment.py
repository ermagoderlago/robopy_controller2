import cv2
import numpy as np
from typing import Optional

# Standard reference landmarks for 112x112 face alignment (ArcFace / InsightFace standard)
REFERENCE_LANDMARKS = np.array([
    [38.2946, 51.6963],  # Left Eye
    [73.5318, 51.5014],  # Right Eye
    [56.0252, 71.7366],  # Nose
    [41.5493, 92.3655],  # Left Mouth Corner
    [70.7299, 92.2041]   # Right Mouth Corner
], dtype=np.float32)


def align_face(image: np.ndarray, landmarks: np.ndarray, output_size: int = 112) -> Optional[np.ndarray]:
    """
    Aligns and crops a face image using 5 landmarks to a standard target size (default 112x112).
    
    Args:
        image: Original input image (BGR or RGB)
        landmarks: 5x2 array of [x, y] coordinates of facial landmarks
        output_size: Target size of the aligned face crop
        
    Returns:
        Aligned face crop as np.ndarray (shape: output_size x output_size x C), or None if alignment fails.
    """
    if image is None or landmarks is None or len(landmarks) != 5:
        return None
        
    try:
        # Scale reference landmarks if target size is not 112
        ref_pts = REFERENCE_LANDMARKS.copy()
        if output_size != 112:
            ref_pts = ref_pts * (output_size / 112.0)
            
        # Estimate the similarity transformation (scale, rotation, translation)
        # cv2.estimateAffinePartial2D is more robust than manual implementation
        trans_matrix, inliers = cv2.estimateAffinePartial2D(
            np.array(landmarks, dtype=np.float32), 
            ref_pts,
            method=cv2.LMEDS
        )
        
        if trans_matrix is None:
            return None
            
        # Warp the image to align the face
        aligned = cv2.warpAffine(
            image, 
            trans_matrix, 
            (output_size, output_size),
            flags=cv2.INTER_CUBIC
        )
        return aligned
        
    except Exception:
        return None
