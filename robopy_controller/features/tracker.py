import numpy as np

class KeypointTracker:
    """Tracker STABILE con priorità a punti longevi"""
    
    def __init__(self, max_distance=20, max_age=10):  # ⬆️ max_age da 5 a 10
        self.max_distance = max_distance
        self.max_age = max_age
        self.tracked_points = {}
        self.next_id = 0
        self.frame_count = 0
        
        # ✅ NUOVO: Penalità per punti instabili
        self.stability_threshold = 5  # Frame minimi per essere "stabile"
    
    
    def get_active_tracks(self):
        """
        Ritorna keypoints, descrittori e ID delle tracce attive.
        Fallback semplice: ritorna ultimo frame.
        """
        if not hasattr(self, "last_keypoints"):
            return np.array([]), np.array([]), np.array([])

        kpts = self.last_keypoints
        desc = self.last_descriptors
        ids = np.arange(len(kpts), dtype=np.int32)

        return kpts, desc, ids

    def update(self, new_keypoints, new_descriptors):
        """Update con PRIORITÀ ai punti stabili"""
        
        if new_keypoints is None or len(new_keypoints) == 0:
            return self.get_active_tracks()
        
        if new_descriptors is None:
            new_descriptors = np.zeros((len(new_keypoints), 256), dtype=np.float32)
        
        # ✅ NUOVO: Prima processa i punti STABILI (hits > threshold)
        stable_tracks = {tid: track for tid, track in self.tracked_points.items() 
                        if track['hits'] >= self.stability_threshold}
        unstable_tracks = {tid: track for tid, track in self.tracked_points.items() 
                          if track['hits'] < self.stability_threshold}
        
        matched_tracks = set()
        assigned_new = set()
        
        output_kpts = []
        output_desc = []
        output_ids = []
        
        # 1. PRIORITÀ: Matcha prima i punti STABILI
        for track_id, track in stable_tracks.items():
            best_idx = -1
            best_dist = float('inf')
            
            for i, kp in enumerate(new_keypoints):
                if i in assigned_new:
                    continue
                
                dist = np.linalg.norm(kp[:2] - track['pos'][:2])
                if dist < best_dist and dist < self.max_distance:
                    best_dist = dist
                    best_idx = i
            
            if best_idx >= 0:
                # Smoothing più aggressivo per punti stabili
                alpha = 0.2  # ⬇️ Da 0.3 a 0.2
                track['pos'] = alpha * new_keypoints[best_idx] + (1 - alpha) * track['pos']
                track['descriptor'] = new_descriptors[best_idx].copy()
                track['age'] = 0
                track['hits'] += 1
                track['last_updated'] = self.frame_count
                
                matched_tracks.add(track_id)
                assigned_new.add(best_idx)
                
                output_kpts.append(track['pos'])
                output_desc.append(track['descriptor'])
                output_ids.append(track_id)
        
        # 2. Poi matcha i punti INSTABILI
        for track_id, track in unstable_tracks.items():
            best_idx = -1
            best_dist = float('inf')
            
            for i, kp in enumerate(new_keypoints):
                if i in assigned_new:
                    continue
                
                dist = np.linalg.norm(kp[:2] - track['pos'][:2])
                if dist < best_dist and dist < self.max_distance:
                    best_dist = dist
                    best_idx = i
            
            if best_idx >= 0:
                alpha = 0.4  # Smoothing più leggero per punti nuovi
                track['pos'] = alpha * new_keypoints[best_idx] + (1 - alpha) * track['pos']
                track['descriptor'] = new_descriptors[best_idx].copy()
                track['age'] = 0
                track['hits'] += 1
                track['last_updated'] = self.frame_count
                
                matched_tracks.add(track_id)
                assigned_new.add(best_idx)
                
                output_kpts.append(track['pos'])
                output_desc.append(track['descriptor'])
                output_ids.append(track_id)
        
        # 3. Invecchiamento
        to_delete = []
        for track_id, track in self.tracked_points.items():
            if track_id not in matched_tracks:
                track['age'] += 1
                # ✅ NUOVO: Punti stabili possono "sopravvivere" più a lungo
                max_age = self.max_age if track['hits'] >= self.stability_threshold else self.max_age // 2
                if track['age'] > max_age:
                    to_delete.append(track_id)
        
        for tid in to_delete:
            del self.tracked_points[tid]
        
        # 4. Nuovi punti (LIMITATI)
        max_new_points = 50  # ✅ NUOVO: Massimo 50 nuovi punti per frame
        new_count = 0
        
        for i in range(len(new_keypoints)):
            if i not in assigned_new and new_count < max_new_points:
                tid = self.next_id
                self.tracked_points[tid] = {
                    'pos': new_keypoints[i].copy(),
                    'descriptor': new_descriptors[i].copy(),
                    'age': 0,
                    'hits': 1,
                    'last_updated': self.frame_count
                }
                
                output_kpts.append(new_keypoints[i])
                output_desc.append(new_descriptors[i])
                output_ids.append(tid)
                
                self.next_id += 1
                new_count += 1
        
        self.frame_count += 1
        
        if len(output_kpts) > 0:
            kpts_array = np.array(output_kpts, dtype=np.float32)
            desc_array = np.array(output_desc, dtype=np.float32)
            return kpts_array, desc_array, output_ids
        
        return self.get_active_tracks()
