import time

class SystemHealthMonitor:
    def __init__(self):
        self.health_metrics = {
            'depth_valid_ratio': 1.0,
            'num_keypoints': 0,
            'yolo_age_ms': 0,
            'oak_temperature': 0,
            'frame_loss_rate': 0.0,
            'last_frame_ts': 0
        }
        self.degradation_mode = 'normal'  # normal / degraded / minimal
        self.frame_count = 0
        self.expected_frame_count = 0
        
    def update_metrics(self, synced_frame, oak_device_temp):
        """Aggiorna metriche da frame sincronizzato e temperatura"""
        
        # Depth quality
        if synced_frame['depth'] is not None:
            self.health_metrics['depth_valid_ratio'] = synced_frame['depth_valid_ratio']
        else:
            self.health_metrics['depth_valid_ratio'] = 0.0
        
        # SuperPoint tracking
        self.health_metrics['num_keypoints'] = len(synced_frame['keypoints'])
        
        # YOLO staleness
        self.health_metrics['yolo_age_ms'] = synced_frame['yolo_age_ms']
        
        # Temperature (da device)
        # oak_device_temp is expected to be a float or object with average?
        # User prompt passed: device.getChipTemperature()
        if hasattr(oak_device_temp, 'average'):
             self.health_metrics['oak_temperature'] = oak_device_temp.average
        else:
             self.health_metrics['oak_temperature'] = oak_device_temp
        
        # Frame loss rate
        self.frame_count += 1
        self.expected_frame_count += 1
        current_ts = time.time()
        
        # Simple gap check (should use sequence numbers if available, but timestamp is ok)
        if self.health_metrics['last_frame_ts'] > 0 and current_ts - self.health_metrics['last_frame_ts'] > 0.05:  # >50ms gap implies loss @ 30Hz
            # Frame perso (this is approximate)
             pass
        
        self.health_metrics['last_frame_ts'] = current_ts
        
        # Calcola loss rate su finestra 100 frame
        if self.expected_frame_count % 100 == 0:
            # This is a very rough estimate of loss rate based on expected vs actual? 
            # Actually with just logic above we don't know expected count unless we know target fps.
            # Let's assume target is 30Hz -> 0.033s.
            # Refine logic later if needed. For now assume external loop calls this per frame received.
            self.health_metrics['frame_loss_rate'] = 0.0 # Placeholder
            self.frame_count = 0
            self.expected_frame_count = 0
            
    def get_pipeline_adjustments(self):
        """
        Ritorna aggiustamenti dinamici basati su health
        
        Returns:
            (adjustments_dict, degradation_mode)
        """
        adjustments = {}
        
        # === REGOLA 1: Thermal Throttling ===
        if self.health_metrics['oak_temperature'] > 75:
            # MINIMAL mode
            adjustments['yolo_enabled'] = False
            adjustments['depth_enabled'] = False
            adjustments['superpoint_threads'] = 1
            self.degradation_mode = 'minimal'
            
        elif self.health_metrics['oak_temperature'] > 68:
            # DEGRADED mode
            adjustments['yolo_fps'] = 10  # Dimezza
            adjustments['superpoint_threads'] = 1
            adjustments['depth_fps'] = 20
            self.degradation_mode = 'degraded'
        
        # === REGOLA 2: Depth Quality ===
        if self.health_metrics['depth_valid_ratio'] < 0.25:
            adjustments['depth_fps'] = 20  # Riduci carico
            adjustments['depth_confidence'] = 200  # Abbassa threshold
            if self.degradation_mode == 'normal':
                self.degradation_mode = 'degraded'
        
        # === REGOLA 3: Feature Tracking ===
        num_kp = self.health_metrics['num_keypoints']
        if num_kp < 150:
            # Troppo pochi keypoints
            adjustments['superpoint_threshold'] = 0.003  # Più permissivo
            adjustments['superpoint_max_kp'] = 500
            
        elif num_kp > 450:
            # Troppi keypoints, spreca bandwidth
            adjustments['superpoint_threshold'] = 0.008  # Più selettivo
            adjustments['superpoint_max_kp'] = 350
        
        # === REGOLA 4: YOLO Staleness ===
        if self.health_metrics['yolo_age_ms'] > 500:
            # Detection troppo vecchia
            adjustments['force_yolo_inference'] = True
        
        # === REGOLA 5: Frame Loss (Disabled for now as logic was placeholder) ===
        # if self.health_metrics['frame_loss_rate'] > 0.1:  
        #    adjustments['all_fps'] = 25
        #    if self.degradation_mode == 'normal':
        #        self.degradation_mode = 'degraded'
        
        # Se nessun problema, torna a normal
        if (self.health_metrics['oak_temperature'] < 65 and
            self.health_metrics['depth_valid_ratio'] > 0.4 and
            150 <= num_kp <= 400):
            self.degradation_mode = 'normal'
        
        return adjustments, self.degradation_mode
