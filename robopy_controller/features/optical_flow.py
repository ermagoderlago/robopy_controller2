
import cv2
import numpy as np

class KLTTracker:
    """
    Lucas-Kanade Optical Flow Tracker for hybrid tracking.
    Designed to work with SuperPoint keyframes on Raspberry Pi.
    """
    
    def __init__(self, logger=None):
        self.logger = logger
        
        # LK Parameters
        self.lk_params = dict(
            winSize=(21, 21),
            maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)
        )
        
        # State
        self.prev_gray = None
        self.prev_pts = None
        self.track_ids = None
        self.prev_descriptors = None
        
    def init_tracks(self, frame_gray, keypoints, descriptors, track_ids=None):
        """
        Initialize tracks from a Keyframe (e.g. SuperPoint features).
        
        Args:
            frame_gray: Current grayscale frame (uint8)
            keypoints: (N, 2) float32 array of points
            descriptors: (N, D) descriptors associated with keypoints
            track_ids: Optional list/array of IDs for the keypoints
        """
        self.prev_gray = frame_gray.copy()
        
        # Ensure correct shape/type for OpenCV
        if keypoints is not None and len(keypoints) > 0:
            self.prev_pts = np.float32(keypoints).reshape(-1, 1, 2)
            self.prev_descriptors = descriptors
            
            if track_ids is not None:
                self.track_ids = np.array(track_ids)
            else:
                self.track_ids = np.arange(len(keypoints))
        else:
            self.prev_pts = None
            self.prev_descriptors = None
            self.track_ids = None
            
    def track(self, frame_gray):
        """
        Track existing points in new frame using Optical Flow.
        
        Returns:
            good_new (N, 2): New point positions
            good_ids (N,): IDs of the tracked points
            good_desc (N, 256): Descriptors carried over (no re-compute)
            status: Tracking status array
        """
        if self.prev_gray is None or self.prev_pts is None or len(self.prev_pts) < 1:
            return None, None, None, False
            
        # 1. Calculate Optical Flow
        p1, st, err = cv2.calcOpticalFlowPyrLK(
            self.prev_gray, frame_gray, self.prev_pts, None, **self.lk_params
        )
        
        # 2. Select good points
        if p1 is not None:
            good_point_mask = (st == 1).reshape(-1)
            
            # --- Check boundaries ---
            h, w = frame_gray.shape
            p1_sq = p1.reshape(-1, 2)
            
            valid_bounds = (p1_sq[:, 0] >= 0) & (p1_sq[:, 0] < w) & \
                           (p1_sq[:, 1] >= 0) & (p1_sq[:, 1] < h)
                           
            total_mask = good_point_mask & valid_bounds
            
            good_new = p1_sq[total_mask]
            
            # Retrieve associated data
            if self.track_ids is not None:
                good_ids = self.track_ids[total_mask]
            else:
                good_ids = np.array([])
                
            if self.prev_descriptors is not None:
                good_desc = self.prev_descriptors[total_mask]
            else:
                good_desc = None
                
            # Update state for next step
            self.prev_gray = frame_gray.copy()
            self.prev_pts = good_new.reshape(-1, 1, 2)
            self.track_ids = good_ids
            self.prev_descriptors = good_desc
            
            return good_new, good_ids, good_desc, True
            
        return None, None, None, False
