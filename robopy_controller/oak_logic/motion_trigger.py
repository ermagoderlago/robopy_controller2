import cv2
import numpy as np

class MotionTriggeredYOLO:
    def __init__(self, motion_threshold=0.03, force_interval=30):
        self.prev_frame = None
        self.frame_count = 0
        self.motion_threshold = motion_threshold
        self.force_interval = force_interval  # Forza ogni N frame
        
    def should_run_yolo(self, frame_rgb):
        """
        Ritorna True se c'è abbastanza motion o è passato troppo tempo
        
        Args:
            frame_rgb: frame RGB 320x320 (o altra risoluzione preview)
        
        Returns:
            bool
        """
        self.frame_count += 1
        
        # Converti a grayscale per diff
        frame_gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
        
        if self.prev_frame is None:
            self.prev_frame = frame_gray
            return True  # Primo frame
        
        # Calcola diff
        diff = cv2.absdiff(frame_gray, self.prev_frame)
        
        # Binary threshold 
        _, diff_thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
        
        motion_ratio = (diff_thresh > 0).sum() / diff.size
        
        self.prev_frame = frame_gray
        
        # Forza inferenza ogni N frame (1 Hz @ 30 fps)
        if self.frame_count % self.force_interval == 0:
            return True
        
        # Altrimenti solo se c'è motion
        return motion_ratio > self.motion_threshold
