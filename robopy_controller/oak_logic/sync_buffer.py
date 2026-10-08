from collections import deque
import time

class TemporalSyncBuffer:
    def __init__(self, max_age_ms=50):
        self.depth_buf = deque(maxlen=100) # Increased to handle 1s+ latency
        self.kp_buf = deque(maxlen=100)
        self.yolo_buf = deque(maxlen=100)
        self.max_age = max_age_ms / 1000.0
        
    def add_depth(self, depth_data, roi, valid_ratio, timestamp):
        self.depth_buf.append({
            'data': depth_data,
            'roi': roi,
            'valid_ratio': valid_ratio,
            'ts': timestamp
        })
    
    def add_keypoints(self, kps, descriptors, timestamp):
        self.kp_buf.append({
            'kps': kps,
            'desc': descriptors,
            'ts': timestamp
        })
    
    def add_yolo(self, detections, timestamp):
        self.yolo_buf.append({
            'detections': detections,
            'ts': timestamp
        })
    
    def get_synced_frame(self):
        """
        Ritorna frame sincronizzato o None
        
        Returns:
            dict con tutti i dati + metadata, oppure None
        """
        # Verifica che ci siano dati nel depth buffer
        if not self.depth_buf:
            return None
        
        # Sincronizzazione guidata dal depth stream primario
        depth_match = self.depth_buf[-1]
        ref_ts = depth_match['ts']
        
        # Match opzionali per keypoint e YOLO se presenti nel buffer
        kp_match = min(self.kp_buf, key=lambda x: abs(x['ts'] - ref_ts)) if self.kp_buf else None
        yolo_match = min(self.yolo_buf, key=lambda x: abs(x['ts'] - ref_ts)) if self.yolo_buf else None
        
        kps = kp_match['kps'] if kp_match else None
        desc = kp_match['desc'] if kp_match else None
        detections = yolo_match['detections'] if yolo_match else []
        yolo_age = (ref_ts - yolo_match['ts']) * 1000 if yolo_match else 0.0
        
        synced_frame = {
            'depth': depth_match['data'],
            'depth_roi': depth_match['roi'],
            'depth_valid_ratio': depth_match['valid_ratio'],
            'keypoints': kps,
            'descriptors': desc,
            'detections': detections,
            'timestamp': ref_ts,
            'yolo_age_ms': yolo_age
        }
        return synced_frame
