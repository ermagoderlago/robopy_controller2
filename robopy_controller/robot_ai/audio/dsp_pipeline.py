import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

class DSPPipeline:
    def __init__(self, sample_rate=16000, hpf_cutoff=140.0, target_rms=1400.0, max_gain=2.0):
        self.sample_rate = sample_rate
        self.target_rms = target_rms
        self.max_gain = max_gain
        self.current_gain = 1.0
        
        # High Pass Filter
        self.hpf_sos = butter(2, hpf_cutoff, btype='highpass', fs=sample_rate, output='sos')
        self.hpf_zi = sosfilt_zi(self.hpf_sos) * 0.0
        
        # Attack/Release parameters for AGC
        # Fast attack (50ms) per 60ms chunk
        self.alpha_attack = float(np.exp(-0.060 / 0.050))
        # Slow release (800ms) per 60ms chunk
        self.alpha_release = float(np.exp(-0.060 / 0.800))
        
        # Envelope state
        self.env = 0.0
        self.nominal_gain = 1.0

    def process(self, chunk_int16: np.ndarray) -> np.ndarray:
        # Convert to float32 for processing
        x = chunk_int16.astype(np.float32)
        
        # Apply HPF
        x, self.hpf_zi = sosfilt(self.hpf_sos, x, zi=self.hpf_zi)
        
        # Calculate chunk RMS
        rms = float(np.sqrt(np.mean(np.square(x)) + 1e-6))
        
        # Se il segnale è sotto la soglia di silenzio o rumore TV lontano (< 130 RMS),
        # mantieni guadagno unitario 1.0x per non ingannare il VAD con rumori amplificati.
        if rms < 130.0:
            target_gain = self.nominal_gain
        else:
            target_gain = float(self.target_rms / (rms + 1e-6))
            target_gain = float(np.clip(target_gain, 1.0, self.max_gain))
        
        # Smooth gain transition con filtri IIR per chunk
        if target_gain < self.current_gain:
            alpha = self.alpha_attack
        else:
            alpha = self.alpha_release
            
        self.current_gain = alpha * self.current_gain + (1.0 - alpha) * target_gain
        
        # Apply gain
        x = x * self.current_gain
        
        # Hard clip to int16 range to prevent overflow
        x = np.clip(x, -32768, 32767)
        
        return x.astype(np.int16)
