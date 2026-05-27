import numpy as np
import cv2

def adaptive_depth_roi(depth, confidence, min_conf=220, min_valid_ratio=0.25):
    """
    Trova ROI minimo contenente depth valida
    
    Returns:
        (depth_cropped, roi_coords) oppure None se troppo pochi pixel
        roi_coords = (x1, y1, x2, y2)
    """
    # Maschera pixel validi
    # Note: depth unit is usually mm. 0 is invalid. >5000 is far/invalid often.
    valid_mask = (confidence > min_conf) & (depth > 0) & (depth < 5000)
    
    valid_count = valid_mask.sum()
    total_count = depth.size
    
    if valid_count < total_count * min_valid_ratio:
        # Troppo pochi pixel validi
        return None, None
    
    # Trova bounding box dei pixel validi
    # np.where returns (row_indices, col_indices)
    rows, cols = np.where(valid_mask)
    
    if len(rows) == 0:
        return None, None
    
    y_min, y_max = rows.min(), rows.max()
    x_min, x_max = cols.min(), cols.max()
    
    # Aggiungi margine 10%
    h, w = depth.shape
    margin_y = int((y_max - y_min) * 0.1)
    margin_x = int((x_max - x_min) * 0.1)
    
    y1 = max(0, y_min - margin_y)
    y2 = min(h, y_max + margin_y)
    x1 = max(0, x_min - margin_x)
    x2 = min(w, x_max + margin_x)
    
    # Crop
    depth_roi = depth[y1:y2, x1:x2]
    roi_coords = (x1, y1, x2, y2)
    
    return depth_roi, roi_coords
