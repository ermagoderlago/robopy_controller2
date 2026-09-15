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
        # Verifica che ci siano dati in tutti i buffer
        # In degraded mode some might be missing? Need to handle that logic outside or be tolerant?
        # For now strict sync as per initial design.
        if not all([self.depth_buf, self.kp_buf, self.yolo_buf]):
            return None
        
        # Strategy Update:
        # Depth is fast, KP (NN) might be slow.
        # Use min(depth, kp) to ensuring we match against what is available for BOTH.
        # YOLO is auxiliary, so we don't let it hold back the VO stream (handled later).
        
        t_depth = self.depth_buf[-1]['ts']
        t_kp = self.kp_buf[-1]['ts']
        
        # We synchronize to the SLOWEST of the critical paths (Depth + KP)
        # to ensure we have data for both.
        ref_ts = min(t_depth, t_kp)
        
        # Trova match più vicini in ogni buffer
        depth_match = min(self.depth_buf, key=lambda x: abs(x['ts'] - ref_ts))
        kp_match = min(self.kp_buf, key=lambda x: abs(x['ts'] - ref_ts))
        yolo_match = min(self.yolo_buf, key=lambda x: abs(x['ts'] - ref_ts))
        
        # Verifica che siano entro max_age
        # Note: YOLO might be OLD (stale), so we might relax check for YOLO?
        # User prompt says "detections può essere stale 500ms".
        # So we check staleness differently for YOLO.
        
        if not abs(depth_match['ts'] - ref_ts) < self.max_age:
             return None
        if not abs(kp_match['ts'] - ref_ts) < self.max_age:
             return None
             
        # For YOLO, just attach the latest available match (even if old) and let health monitor flag it?
        # Or check absolute age?
        # Let's attach and provide age.
        
        # Costruisci frame sincronizzato
        synced_frame = {
            'depth': depth_match['data'],
            'depth_roi': depth_match['roi'],
            'depth_valid_ratio': depth_match['valid_ratio'],
            'keypoints': kp_match['kps'],
            'descriptors': kp_match['desc'],
            'detections': yolo_match['detections'],
            'timestamp': ref_ts,
            'yolo_age_ms': (ref_ts - yolo_match['ts']) * 1000  # Per health
        }
        
        return synced_frame
