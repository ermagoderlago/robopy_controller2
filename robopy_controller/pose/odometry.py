import cv2
import numpy as np

# ============================================================================
# CLASSE 2: HybridOdometrySystem
# ============================================================================
class HybridOdometrySystem:
    """Sistema ibrido per odometria visiva robusta"""
    
    def __init__(self, config, camera_matrix, dist_coeffs, logger):
        self.config = config
        self.camera_matrix = camera_matrix
        self.dist_coeffs = dist_coeffs
        self.logger = logger
        
        # Inizializza matchers
        self.flann = self._init_flann()
        self.bf = self._init_bf()
        
        # Stato odometria
        self.transform_accumulated = np.eye(4)
        self.last_good_transform = np.eye(4)
        self.consecutive_failures = 0
        self.max_consecutive_failures = 5
        
        # Buffer per smoothing
        self.transform_buffer = []
        self.buffer_size = 5
        
        # Statistiche
        self.stats = {
            'total_frames': 0,
            'successful_odometry': 0,
            'failed_odometry': 0,
            'avg_matches': 0,
            'avg_inlier_ratio': 0
        }
    
    def _init_flann(self):
        """Inizializza FLANN matcher"""
        FLANN_INDEX_KDTREE = 1
        index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=4)
        search_params = dict(checks=30)
        return cv2.FlannBasedMatcher(index_params, search_params)
    
    def _init_bf(self):
        """Inizializza Brute Force matcher"""
        return cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
    
    def match_features_hybrid(self, desc1, desc2, kp1=None, kp2=None):
        """Matching ibrido: prova FLANN, se fallisce usa BF"""
        if desc1 is None or desc2 is None or len(desc1) < 2 or len(desc2) < 2:
            self.logger.warn("Descrittori insufficienti per matching")
            return []
        
        # 1. Controlla se i descrittori sono tutti zero
        if np.all(desc1 == 0) or np.all(desc2 == 0):
            self.logger.warn("Descrittori tutti zero - skip matching")
            return []
        
        # 2. Converti a float32
        d1 = desc1.astype(np.float32)
        d2 = desc2.astype(np.float32)
        
        # 3. Normalizzazione L2 robusta
        try:
            # Controlla se i descrittori sono già normalizzati
            norm1 = np.linalg.norm(d1, axis=1, keepdims=True)
            norm2 = np.linalg.norm(d2, axis=1, keepdims=True)
            
            # Se le norme sono troppo piccole (< 0.1), i descrittori sono probabilmente errati
            if np.mean(norm1) < 0.1 or np.mean(norm2) < 0.1:
                # Prova a normalizzare comunque, ma filtra quelli con norma 0
                mask1 = norm1.flatten() > 1e-6
                mask2 = norm2.flatten() > 1e-6
                
                if np.sum(mask1) == 0 or np.sum(mask2) == 0:
                    return []
                
                d1 = d1[mask1]
                d2 = d2[mask2]
                
                # Ricalcola norme sui filtrati
                norm1 = np.linalg.norm(d1, axis=1, keepdims=True)
                norm2 = np.linalg.norm(d2, axis=1, keepdims=True)
            
            # Normalizza
            d1 = d1 / (norm1 + 1e-8)
            d2 = d2 / (norm2 + 1e-8)
            
        except Exception as e:
            self.logger.error(f"Errore normalizzazione: {e}")
            return []
        
        matches = []
        matcher_type = "NONE"
        min_matches = self.config.get('min_matches', 10)
        
        # 4. TENTATIVO FLANN
        try:
            if len(d1) >= 2 and len(d2) >= 2:
                FLANN_INDEX_KDTREE = 1
                index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=4)
                search_params = dict(checks=50)
                
                flann_matcher = cv2.FlannBasedMatcher(index_params, search_params)
                raw_matches = flann_matcher.knnMatch(d1, d2, k=2)
                
                good_matches = []
                ratio_thresh = 0.75
                
                for m, n in raw_matches:
                    if m.distance < ratio_thresh * n.distance:
                        if m.distance < 0.8:
                            good_matches.append(m)
                
                if len(good_matches) >= min_matches:
                    matches = good_matches
                    matcher_type = "FLANN"
                else:
                    pass
                    
        except Exception as e:
            self.logger.warn(f"FLANN matching fallito: {e}")
        
        # 5. TENTATIVO Brute-Force con Cross-Check
        if len(matches) < min_matches:
            try:
                bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=True)
                bf_matches = bf.match(d1, d2)
                
                if len(bf_matches) > 0:
                    bf_matches = sorted(bf_matches, key=lambda x: x.distance)
                    
                    # Filtra per distanza massima
                    max_distance = 0.7
                    bf_matches = [m for m in bf_matches if m.distance < max_distance]
                    
                    if len(bf_matches) >= min_matches:
                        max_matches = min(100, len(bf_matches))
                        matches = bf_matches[:max_matches]
                        matcher_type = "BF_CROSSCHECK"
                        
            except Exception as e:
                self.logger.warn(f"BF cross-check fallito: {e}")
        
        # 6. TENTATIVO Brute-Force con Ratio Test
        if len(matches) < min_matches:
            try:
                bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
                bf_knn_matches = bf.knnMatch(d1, d2, k=2)
                
                good_bf_knn = []
                ratio_thresh = 0.8
                
                for match_pair in bf_knn_matches:
                    if len(match_pair) < 2:
                        continue
                    m, n = match_pair
                    if m.distance < ratio_thresh * n.distance:
                        good_bf_knn.append(m)
                
                if len(good_bf_knn) >= min_matches:
                    matches = good_bf_knn
                    matcher_type = "BF_KNN"
                    
            except Exception as e:
                self.logger.error(f"BF KNN fallito: {e}")
        
        if len(matches) > 0:
            pass
        else:
            pass
        
        return matches
    
    def _prepare_pnp_points(self, prev_kpts, curr_kpts, depth_frame, matches):
        """Prepara punti 3D-2D per PnP"""
        object_points = []
        image_points = []
        
        # Pre-calcola costanti
        fx, fy = self.camera_matrix[0, 0], self.camera_matrix[1, 1]
        cx, cy = self.camera_matrix[0, 2], self.camera_matrix[1, 2]
        h_depth, w_depth = depth_frame.shape

        for match in matches:
            u_prev, v_prev = prev_kpts[match.queryIdx]
            u_curr, v_curr = curr_kpts[match.trainIdx]
            
            x_d, y_d = int(round(u_prev)), int(round(v_prev))
            
            # Margine per ROI 3x3
            if not (1 <= x_d < w_depth-1 and 1 <= y_d < h_depth-1):
                continue
            
            # --- FIX ROBUSTEZZA: Mediana locale 3x3 ---
            roi = depth_frame[y_d-1:y_d+2, x_d-1:x_d+2]
            
            # Filtriamo gli zeri (punti senza profondità) prima di calcolare la mediana
            valid_depths = roi[roi > 0]
            
            if len(valid_depths) < 5: # Richiediamo almeno metà ROI valida
                continue
                
            depth_mm = np.median(valid_depths)
            
            # Filtro range operativo (OAK-D Lite è affidabile tra 20cm e 7m)
            if depth_mm < 200 or depth_mm > 7000: 
                continue
            
            # Trasformazione in coordinate 3D (Camera Frame)
            z = depth_mm / 1000.0
            x = (u_prev - cx) * z / fx
            y = (v_prev - cy) * z / fy
            
            object_points.append([x, y, z])
            image_points.append([u_curr, v_curr])
        
        return np.array(object_points), np.array(image_points)


    def _validate_transformation(self, rvec, tvec, inlier_ratio):
        t_norm = np.linalg.norm(tvec)
        r_norm = np.linalg.norm(rvec)
        
        # Dead-zone per robot fermo
        if t_norm < 0.002 and r_norm < np.deg2rad(0.1):
            return False
            
        # Soglia inlier
        if inlier_ratio < 0.15:
            return False
            
        # Limiti dinamici
        if t_norm > 0.15:
            return False
            
        return True


    def _smooth_transform(self, transform):
        """Applica smoothing alla trasformazione"""
        self.transform_buffer.append(transform)
        
        if len(self.transform_buffer) > self.buffer_size:
            self.transform_buffer.pop(0)
        
        if len(self.transform_buffer) > 1:
            weights = np.linspace(0.5, 1.0, len(self.transform_buffer))
            weights = weights / weights.sum()
            
            t_smoothed = np.zeros(3)
            for i, T in enumerate(self.transform_buffer):
                t_smoothed += weights[i] * T[:3, 3]
            
            transform_smoothed = transform.copy()
            transform_smoothed[:3, 3] = t_smoothed
            
            return transform_smoothed
        
        return transform
    
    def estimate_pose_robust(self, prev_kpts, curr_kpts, depth_frame, matches):
            """
            Versione corretta che accetta matches pre-calcolati.
            """
            if matches is None or len(matches) < self.config.get('min_matches', 6):
                return None, 0.0

            # 2. Preparazione punti per PnP
            # Implementazione diretta dell'estrazione punti (più sicura qui):
            object_points = []
            image_points = []
            
            h, w = depth_frame.shape
            
            for m in matches:
                idx_prev = m.queryIdx
                idx_curr = m.trainIdx
                
                if idx_prev >= len(prev_kpts) or idx_curr >= len(curr_kpts):
                    continue
                    
                p_prev = prev_kpts[idx_prev] # (u, v)
                p_curr = curr_kpts[idx_curr]
                
                u, v = int(p_prev[0]), int(p_prev[1])
                
                # Bounds check
                if 0 <= u < w and 0 <= v < h:
                    z = depth_frame[v, u] * 0.001 # Converti mm in metri
                    
                    if 0.1 < z < 10.0:
                        fx = self.camera_matrix[0, 0]
                        fy = self.camera_matrix[1, 1]
                        cx = self.camera_matrix[0, 2]
                        cy = self.camera_matrix[1, 2]
                        
                        x = (u - cx) * z / fx
                        y = (v - cy) * z / fy
                        
                        object_points.append([x, y, z])
                        image_points.append(p_curr)

            object_points = np.array(object_points, dtype=np.float32)
            image_points = np.array(image_points, dtype=np.float32)

            if len(object_points) < 6:
                return None, 0.0

            # 3. Esegui PnP RANSAC
            try:
                success, rvec, tvec, inliers = cv2.solvePnPRansac(
                    object_points,
                    image_points,
                    self.camera_matrix,
                    self.dist_coeffs,
                    flags=cv2.SOLVEPNP_EPNP,
                    iterationsCount=100,
                    reprojectionError=2.0,
                    confidence=0.99
                )
                
                if not success or inliers is None:
                    self.logger.warn(f"PnP RANSAC fallito. Punti: {len(object_points)}")
                    return None, 0.0

                inlier_ratio = len(inliers) / len(matches)
                
                R, _ = cv2.Rodrigues(rvec)
                T = np.eye(4)
                T[:3, :3] = R
                T[:3, 3] = tvec.flatten()
                
                T_inv = np.linalg.inv(T)

                trans_mag = np.linalg.norm(T_inv[:3, 3])
                rot_mag = np.linalg.norm(cv2.Rodrigues(T_inv[:3, :3])[0])
                
                if trans_mag < 0.002 and rot_mag < np.deg2rad(0.1):
                    return np.eye(4), inlier_ratio
                
                return T_inv, inlier_ratio

            except Exception as e:
                self.logger.error(f"Errore PnP o Calcolo: {e}")
                return None, 0.0
                
            except Exception as e:
                self.logger.error(f"Errore PnP calcolo: {e}")
                return None, 0.0
            
            
    def update_odometry(self, transform, inlier_ratio):
        """Aggiorna odometria accumulata"""
        self.stats['total_frames'] += 1
    
        if transform is not None and inlier_ratio > 0.20:
            try:
                rel_pose = np.linalg.inv(transform) 
                self.transform_accumulated = self.transform_accumulated @ rel_pose
                self.last_good_transform = rel_pose
                self.consecutive_failures = 0
                self.stats['successful_odometry'] += 1
                return True
            except np.linalg.LinAlgError:
                return False
        else:
            self.consecutive_failures += 1
            # Dead Reckoning limitato
            if self.consecutive_failures <= 2 and self.last_good_transform is not None:
                self.transform_accumulated = self.transform_accumulated @ self.last_good_transform
                return True
            return False
    
    def get_current_pose(self):
        """Restituisce la posa corrente"""
        return self.transform_accumulated.copy()
    
    def get_stats(self):
        """Restituisce statistiche"""
        return self.stats
