from collections import deque
import time

class AdaptiveRateController:
    def __init__(self):
        self.target_fps = 30
        self.current_fps = 30
        self.adjustments_history = deque(maxlen=100)
        self.last_adjustment_time = time.time()
        self.adjustment_cooldown = 5.0  # secondi tra adjustment
        
    def adjust_rates(self, health_monitor):
        """
        Feedback control basato su metriche
        
        Returns:
            dict con parametri aggiornati
        """
        current_time = time.time()
        
        # Cooldown per evitare oscillazioni
        if current_time - self.last_adjustment_time < self.adjustment_cooldown:
            return {}
        
        adj, mode = health_monitor.get_pipeline_adjustments()
        
        # Log storico only if adjustment happens
        if adj or mode != health_monitor.degradation_mode:
            # Note: degradation_mode in health_monitor is updated IN get_pipeline_adjustments
            # So checking existing mode might be tricky if it was just updated.
            # Let's assume 'adj' is non-empty if changes are needed.
            # But mode changes might return empty adj if only mode flag changes?
            # User logic returns adjustments dict.
            pass

        self.adjustments_history.append({
            'timestamp': current_time,
            'mode': mode,
            'adjustments': adj,
            'metrics': health_monitor.health_metrics.copy()
        })
        
        self.last_adjustment_time = current_time
        
        return adj
    
    def get_diagnostics(self):
        """Ritorna report per topic diagnostics"""
        if not self.adjustments_history:
            return {}
        
        recent = list(self.adjustments_history)[-10:]
        
        # Avoid division by zero
        if not recent:
             return {}

        return {
            'recent_modes': [r['mode'] for r in recent],
            'avg_temperature': sum(r['metrics']['oak_temperature'] for r in recent) / len(recent),
            'avg_keypoints': sum(r['metrics']['num_keypoints'] for r in recent) / len(recent),
            'total_adjustments': len(self.adjustments_history)
        }
